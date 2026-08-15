"""One-shot script: fetch historical K-line data (1d or 1m) from the local
``xtquant`` MiniQMT client and write it into the configured Postgres database.

Run::

    uv run python scripts/fetch_historical_bars_xtquant.py --interval 1d \\
        --start 2024-01-01 --end 2024-12-31
    uv run python scripts/fetch_historical_bars_xtquant.py --interval 1m \\
        --start 2024-01-01 --end 2024-01-31 --limit 50
    uv run python scripts/fetch_historical_bars_xtquant.py --interval 1d \\
        --start 2024-01-01 --end 2024-12-31 --dry-run

The script is idempotent — re-running it rewrites only the overlapping
``(symbol, time_col)`` rows. ``pre_close`` is dropped before insert (the
new schema does not store it; recover from ``adjustment_factor`` on the
read path).

Prerequisites:

- The ``xtquant`` package must be installed locally (typically copied
  from the MiniQMT distribution, not on PyPI). Install with
  ``uv pip install xtquant`` (or whichever local index you use).
- MiniQMT must be running locally with the requested data cached.
- The ``instrument`` table must contain the symbols you want to fetch.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from datetime import time as dtime

import pandas as pd
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BATCH_SIZE: int = 50
DEFAULT_LIMIT: int = 0  # 0 = unlimited

SUPPORTED_INTERVALS: frozenset[str] = frozenset({"1d", "1m"})
BEIJING_TZ: str = "Asia/Shanghai"

logger = logging.getLogger("fetch_historical_bars_xtquant")


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without xtquant)
# ---------------------------------------------------------------------------


def merge_xtquant_bars(ret: dict[str, pd.DataFrame | None], interval: str) -> pd.DataFrame:
    """Merge ``xtdata.get_local_data``'s per-symbol output into one DataFrame.

    - Drops any ``pre_close`` column.
    - Renames ``preClose`` → ``pre_close`` defensively (then drops it).
    - Converts ``time`` (ms-UTC int64) to Asia/Shanghai ``Timestamp`` and then
      either ``.dt.date`` (for ``1d``) or kept tz-aware (for ``1m``).
    - Adds the ``interval`` column.
    - Sorts by ``(symbol, time_col)``.

    Skips ``None`` / empty frames silently. Returns an empty DataFrame if
    every frame is empty.
    """
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"unsupported interval {interval!r}")

    time_col = "date" if interval == "1d" else "time"
    frames: list[pd.DataFrame] = []

    for symbol, df in ret.items():
        if df is None or df.empty:
            continue
        df = df.copy()

        # xtdata returns ``preClose`` (camelCase). Normalise defensively then
        # drop it; the new schema does not store ``pre_close``.
        if "preClose" in df.columns and "pre_close" not in df.columns:
            df = df.rename(columns={"preClose": "pre_close"})
        if "pre_close" in df.columns:
            df = df.drop(columns=["pre_close"])

        # xtdata returns ``time`` as int64 ms since UTC epoch, representing
        # Beijing 00:00 of the requested date. Parse as UTC then convert to
        # Asia/Shanghai to avoid a one-day offset.
        if interval == "1d":
            df[time_col] = (
                pd.to_datetime(df["time"], unit="ms", utc=True).dt.tz_convert(BEIJING_TZ).dt.date
            )
        else:
            df[time_col] = pd.to_datetime(df["time"], unit="ms", utc=True).dt.tz_convert(BEIJING_TZ)

        df["symbol"] = symbol
        df["interval"] = interval

        df = df[
            ["symbol", time_col, "interval", "open", "high", "low", "close", "volume", "amount"]
        ].rename(columns={time_col: "time"})
        frames.append(df)

    if not frames:
        return pd.DataFrame(
            columns=[
                "symbol",
                "time",
                "interval",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
            ]
        )

    result = pd.concat(frames, ignore_index=True)
    return result.sort_values(["symbol", "time"]).reset_index(drop=True)


def list_instrument_symbols(limit: int | None = None) -> list[str]:
    """Read symbols from the ``instrument`` table (no date filtering).

    Returns ``[]`` when the table is empty.
    """
    # Imported lazily so that callers using ``merge_xtquant_bars`` in unit
    # tests do not need a configured engine.
    from xtrade.data import get_engine

    query = "SELECT symbol FROM instrument ORDER BY symbol"
    if limit is not None and limit > 0:
        # SQLAlchemy text() doesn't support DBAPI param expansion in `text()`;
        # ``LIMIT`` is a constant, not user data, so it's safe to inline.
        query += f" LIMIT {int(limit)}"

    with get_engine().connect() as conn:
        rows = conn.execute(text(query)).fetchall()
    return [row[0] for row in rows]


def format_xtquant_time(d: date, interval: str, *, end: bool = False) -> str:
    """Format a ``date`` for ``xtdata.download_history_data2``.

    - 1d: ``YYYYMMDD``.
    - 1m: ``YYYYMMDDHHmmss`` (``time.min`` for start, ``time.max`` for end).
    """
    if interval == "1d":
        return d.strftime("%Y%m%d")
    base = datetime.combine(d, dtime.max if end else dtime.min)
    return base.strftime("%Y%m%d%H%M%S")


# ---------------------------------------------------------------------------
# Per-batch download + write (xtquant-touching; requires MiniQMT running)
# ---------------------------------------------------------------------------


def fetch_and_write_batch(
    symbols: list[str],
    start: date,
    end: date,
    interval: str,
    batch_size: int,
    dry_run: bool,
) -> tuple[int, list[tuple[str, str]]]:
    """Fetch one batch via xtquant, merge, and upsert via the existing repository.

    Returns ``(rows_written, skipped_symbols)``. Per-symbol errors do NOT
    abort the batch; they are returned as ``(symbol, error)`` records.
    """
    if dry_run:
        return (0, [])

    # Lazy import: xtquant is not on PyPI; we surface a clear ModuleNotFoundError
    # rather than failing at script import.
    from xtquant import xtdata

    start_str = format_xtquant_time(start, interval, end=False)
    end_str = format_xtquant_time(end, interval, end=True)

    skipped: list[tuple[str, str]] = []

    try:
        xtdata.download_history_data2(
            stock_list=symbols,
            period=interval,
            start_time=start_str,
            end_time=end_str,
        )
    except Exception as exc:
        # Fail the whole batch but don't crash the script.
        for s in symbols:
            skipped.append((s, f"download_history_data2 failed: {exc!r}"))
        return (0, skipped)

    try:
        ret = xtdata.get_local_data(
            stock_list=symbols,
            period=interval,
            start_time=start_str,
            end_time=end_str,
            dividend_type="none",
        )
    except Exception as exc:
        for s in symbols:
            skipped.append((s, f"get_local_data failed: {exc!r}"))
        return (0, skipped)

    if not ret:
        for s in symbols:
            skipped.append((s, "no data returned"))
        return (0, skipped)

    merged = merge_xtquant_bars(ret, interval)
    if merged.empty:
        for s in symbols:
            skipped.append((s, "empty after merge"))
        return (0, skipped)

    # Lazy import: the repository is part of the data layer.
    from xtrade.data.market_data import PostgresKLineRepository

    PostgresKLineRepository(batch_size=batch_size).upsert_bars(merged)
    return (len(merged), [])


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


@dataclass
class Report:
    symbols_total: int = 0
    symbols_processed: int = 0
    rows_written: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def rows_per_sec(self) -> float:
        return self.rows_written / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--interval",
        required=True,
        choices=sorted(SUPPORTED_INTERVALS),
        help="K-line frequency (1d or 1m).",
    )
    p.add_argument(
        "--start",
        required=True,
        help="Inclusive start date (YYYY-MM-DD).",
    )
    p.add_argument(
        "--end",
        required=True,
        help="Inclusive end date (YYYY-MM-DD).",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Symbols per xtquant batch (default: {DEFAULT_BATCH_SIZE}).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Process at most N symbols (default: {DEFAULT_LIMIT}, unlimited).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover symbols and print the planned batches; do not write.",
    )
    return p.parse_args(argv)


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def run(args: argparse.Namespace) -> Report:
    start = parse_date(args.start)
    end = parse_date(args.end)
    if start > end:
        raise SystemExit(f"--start must be <= --end (got {start} > {end})")

    interval = args.interval
    batch_size = max(int(args.batch_size), 1)
    limit = int(args.limit) if args.limit and args.limit > 0 else None

    symbols = list_instrument_symbols(limit=limit)
    report = Report(symbols_total=len(symbols))

    if not symbols:
        return report

    # Dry-run: print plan and exit.
    if args.dry_run:
        batches = [symbols[i : i + batch_size] for i in range(0, len(symbols), batch_size)]
        print(
            f"[dry-run] would process {len(symbols)} symbols in {len(batches)} batch(es) of "
            f"{batch_size}, interval={interval}, window=[{start}, {end}]"
        )
        return report

    started = time.monotonic()
    last_log_at = started

    def tick() -> None:
        nonlocal last_log_at
        now = time.monotonic()
        if now - last_log_at >= 1.0:
            report.elapsed_seconds = now - started
            print(
                f"[fetch-xtquant] "
                f"{report.symbols_processed}/{report.symbols_total} symbols, "
                f"{report.rows_written} rows in {report.elapsed_seconds:.1f}s "
                f"({len(report.skipped)} skipped)",
                flush=True,
            )
            last_log_at = now

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]
        rows, skipped = fetch_and_write_batch(
            batch, start, end, interval, batch_size, dry_run=False
        )
        report.symbols_processed += len(batch) - len(skipped)
        report.rows_written += rows
        report.skipped.extend(skipped)
        tick()

    report.elapsed_seconds = time.monotonic() - started
    return report


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)

    print(
        f"interval={args.interval} window=[{args.start}, {args.end}] "
        f"batch-size={args.batch_size} limit={args.limit} dry-run={args.dry_run}"
    )

    report = run(args)

    print(
        f"\n=== fetch complete ===\n"
        f"  symbols_processed: {report.symbols_processed}/{report.symbols_total}\n"
        f"  rows_written:      {report.rows_written}\n"
        f"  elapsed_seconds:   {report.elapsed_seconds:.2f}\n"
        f"  rows_per_sec:      {report.rows_per_sec:.1f}\n"
        f"  skipped:           {len(report.skipped)}"
    )
    if report.skipped:
        print("\nskipped symbols:")
        for symbol, err in report.skipped[:20]:
            print(f"  - {symbol}: {err}")
        if len(report.skipped) > 20:
            print(f"  ... and {len(report.skipped) - 20} more")

    return 0 if (args.dry_run or report.symbols_processed > 0 or report.symbols_total == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
