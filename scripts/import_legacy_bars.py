"""One-shot script: import the legacy ``mos_quant`` K-line parquet layout
into Postgres.

The legacy layout under ``F:\\Quant\\data\\bars`` contains:

- ``hot/1d.parquet`` — one global file, multi-symbol rows.
- ``hot/1m/{symbol}/{year}.parquet`` — per-symbol, per-year.
- ``cold/1d/{symbol}.parquet`` — per-symbol cold archive (optional).
- ``cold/1m/{symbol}/{year}.parquet`` — per-symbol cold archive (optional).

This script:

1. Connects to the configured Postgres; creates the ``xtrade`` database
   if it doesn't exist (requires the configured user to have ``CREATEDB``).
2. Runs ``alembic upgrade head`` to create ``kline_1d`` / ``kline_1m``.
3. Walks the source directory, normalises each parquet, and upserts
   rows into Postgres. ``pre_close`` is dropped (the new schema does
   not store it; recover from ``adj_factor`` on the read path).

Run::

    uv run python scripts/import_legacy_bars.py
    uv run python scripts/import_legacy_bars.py --source D:\\data\\bars --workers 4
    uv run python scripts/import_legacy_bars.py --dry-run

The script is idempotent — re-running it only rewrites overlapping
``(symbol, time_col)`` rows. It does NOT ``TRUNCATE``; users wanting
a fresh start must do that manually.
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import psycopg
from sqlalchemy.engine import make_url

# ---------------------------------------------------------------------------
# Paths — adjust DEFAULT_SOURCE if your data lives elsewhere.
# ---------------------------------------------------------------------------

DEFAULT_SOURCE: Path = Path(r"F:\Quant\data\bars")
DEFAULT_BATCH_SIZE: int = 10_000
DEFAULT_WORKERS: int = 1  # sequential; use 4-8 on a beefy box.


# ---------------------------------------------------------------------------
# Bootstrap: create the database if missing.
# ---------------------------------------------------------------------------


def ensure_database(url: str) -> None:
    """Create the target database if missing. Idempotent."""
    parsed = make_url(url)
    target_db = parsed.database
    if not target_db:
        raise ValueError(f"ensure_database: DSN has no database: {url}")

    psycopg_dsn = parsed.render_as_string(hide_password=False).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    admin_dsn = (
        parsed.set(database="postgres")
        .render_as_string(hide_password=False)
        .replace("postgresql+psycopg://", "postgresql://", 1)
    )

    try:
        with psycopg.connect(psycopg_dsn, autocommit=True) as conn:
            conn.execute("SELECT 1")
        return
    except psycopg.OperationalError as exc:
        # DB does not exist (server returned a "database does not exist" error).
        msg = str(exc).lower()
        if "does not exist" not in msg and "数据库" not in msg and "3d000" not in msg:
            raise

    try:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{target_db}"')
    except psycopg.errors.InsufficientPrivilege as exc:
        raise RuntimeError(
            f"Postgres user cannot CREATE DATABASE ({exc}). "
            f"Either grant CREATEDB to the user, or create database "
            f"{target_db!r} manually before running this script."
        ) from exc
    except psycopg.errors.DuplicateDatabase:
        pass  # race condition; another process created it.


def run_alembic_upgrade() -> None:
    """Run ``alembic upgrade head`` via subprocess (uses the same env vars)."""
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)


# ---------------------------------------------------------------------------
# Discovery.
# ---------------------------------------------------------------------------


def discover_files(root: Path) -> list[tuple[str, Path]]:
    """Walk ``root`` and return every legacy K-line parquet as ``(interval, path)``."""
    if not root.exists():
        return []
    found: list[tuple[str, Path]] = []

    hot_1d = root / "hot" / "1d.parquet"
    if hot_1d.is_file():
        found.append(("1d", hot_1d))
    cold_1d = root / "cold" / "1d"
    if cold_1d.is_dir():
        for p in sorted(cold_1d.glob("*.parquet")):
            if p.is_file():
                found.append(("1d", p))

    for sub_root in (root / "hot" / "1m", root / "cold" / "1m"):
        if sub_root.is_dir():
            for sym_dir in sorted(p for p in sub_root.iterdir() if p.is_dir()):
                for p in sorted(sym_dir.glob("*.parquet")):
                    if p.is_file():
                        found.append(("1m", p))

    return found


# ---------------------------------------------------------------------------
# Normalisation.
# ---------------------------------------------------------------------------


def normalize_frame(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Normalise a legacy parquet DataFrame to the repository contract.

    - 1d: rename ``date`` -> ``time`` for parity with 1m.
    - Drop ``pre_close`` (the new schema does not store it).
    - Drop NaN rows (the schema enforces NOT NULL on all numeric columns).
    - Add the ``interval`` column so the repository can route the frame.
    """
    if interval not in ("1d", "1m"):
        raise ValueError(f"unsupported interval {interval!r}")

    out = df.copy()
    if interval == "1d" and "date" in out.columns and "time" not in out.columns:
        out = out.rename(columns={"date": "time"})
    if "pre_close" in out.columns:
        out = out.drop(columns=["pre_close"])
    out["interval"] = interval
    out["time"] = pd.to_datetime(out["time"], utc=True)

    numeric_cols = ("open", "high", "low", "close", "volume", "amount")
    for c in numeric_cols:
        if c not in out.columns:
            raise ValueError(f"normalize_frame: missing column {c!r}")
    out = out.dropna(subset=numeric_cols)

    return out.loc[
        :, ("symbol", "time", "interval", "open", "high", "low", "close", "volume", "amount")
    ]


# ---------------------------------------------------------------------------
# Per-file upsert (worker function; module-level so it can be pickled).
# ---------------------------------------------------------------------------


def _process_one_file(args: tuple[str, str, int, bool]) -> tuple[str, int, str | None]:
    """Read one parquet, normalise, upsert. Returns (path, rows, error)."""
    interval, path_str, batch_size, dry_run = args
    path = Path(path_str)
    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        return (path_str, 0, f"read_parquet failed: {exc!r}")
    if df.empty:
        return (path_str, 0, None)

    try:
        norm = normalize_frame(df, interval)
    except Exception as exc:
        return (path_str, 0, f"normalize failed: {exc!r}")
    if norm.empty:
        return (path_str, 0, None)

    if dry_run:
        return (path_str, len(norm), None)

    try:
        from xtrade.data.market_data import PostgresKLineRepository

        repo = PostgresKLineRepository(batch_size=batch_size)
        repo.upsert_bars(norm)
        # ``repo.upsert_bars`` returns int(rowcount) which is unreliable
        # under psycopg3 + INSERT ... ON CONFLICT DO UPDATE; report the
        # frame length instead so the user sees what was imported.
        return (path_str, len(norm), None)
    except Exception as exc:
        return (path_str, 0, f"upsert failed: {exc!r}")


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


@dataclass
class Report:
    files_total: int = 0
    files_processed: int = 0
    rows_written: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def rows_per_sec(self) -> float:
        return self.rows_written / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0


def run(files: list[tuple[str, Path]], *, workers: int, batch_size: int, dry_run: bool) -> Report:
    """Process ``files`` in parallel (or sequentially)."""
    report = Report(files_total=len(files))
    if not files:
        return report

    args_list = [(iv, str(p), batch_size, dry_run) for iv, p in files]

    # ProcessPoolExecutor workers on Windows must not inherit the parent's
    # SQLAlchemy connection pool; reset before forking.
    try:
        from xtrade.data import reset_engine

        reset_engine()
    except Exception:
        pass

    started = time.monotonic()
    last_log_at = started

    def tick() -> None:
        nonlocal last_log_at
        now = time.monotonic()
        if now - last_log_at >= 1.0:
            report.elapsed_seconds = now - started
            _log(report, dry_run)
            last_log_at = now

    if workers <= 1:
        for args in args_list:
            _consume(args, report)
            tick()
    else:
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(processes=workers) as pool:
            for result in pool.imap_unordered(_process_one_file, args_list):
                _consume_result(result, report)
                tick()
    report.elapsed_seconds = time.monotonic() - started
    _log(report, dry_run)
    return report


def _consume(args: tuple[str, str, int, bool], report: Report) -> None:
    _consume_result(_process_one_file(args), report)


def _consume_result(result: tuple[str, int, str | None], report: Report) -> None:
    path_str, rows, err = result
    if err is None:
        report.files_processed += 1
        report.rows_written += rows
    else:
        report.skipped.append((path_str, err))


def _log(report: Report, dry_run: bool) -> None:
    suffix = " (dry-run)" if dry_run else ""
    pct = (report.files_processed / report.files_total * 100.0) if report.files_total else 0.0
    print(
        f"[import-legacy{suffix}] "
        f"{report.files_processed}/{report.files_total} files ({pct:.1f}%), "
        f"{report.rows_written} rows in {report.elapsed_seconds:.1f}s "
        f"({len(report.skipped)} skipped)",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Entrypoint.
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"Root directory of the legacy parquet layout (default: {DEFAULT_SOURCE}).",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Number of worker processes (default: {DEFAULT_WORKERS}, sequential).",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Row chunk size for PostgresKLineRepository.upsert_bars (default: {DEFAULT_BATCH_SIZE}).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N files (default: 0, unlimited).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover files and report the planned work; do not write to Postgres.",
    )
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Load the configured DB URL.
    from xtrade.core.config import get_config

    config = get_config()
    db_url = config.data.database.url
    print(f"source: {args.source}")
    print(f"dry-run: {args.dry_run}")
    print(f"workers: {args.workers if args.workers > 1 else '1 (sequential)'}")
    print(f"batch-size: {args.batch_size}")
    print(f"limit: {args.limit if args.limit > 0 else 'unlimited'}")
    print(f"db: {db_url}")

    if not args.dry_run:
        print("\n[bootstrap] ensuring target database exists...")
        try:
            ensure_database(db_url)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print("[bootstrap] running alembic upgrade head...")
        try:
            run_alembic_upgrade()
        except subprocess.CalledProcessError as exc:
            print(f"ERROR: alembic upgrade failed: {exc}", file=sys.stderr)
            return 3

    files = discover_files(args.source)
    if args.limit > 0:
        files = files[: args.limit]
    if not files:
        print("no files found; nothing to do")
        return 0

    print(f"discovered {len(files)} file(s); starting import")
    report = run(
        files,
        workers=args.workers,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )

    print(
        f"\n=== import complete ===\n"
        f"  files_processed: {report.files_processed}/{report.files_total}\n"
        f"  rows_written:    {report.rows_written}\n"
        f"  skipped:         {len(report.skipped)}\n"
        f"  elapsed_seconds: {report.elapsed_seconds:.2f}\n"
        f"  rows_per_sec:    {report.rows_per_sec:.1f}"
    )
    if report.skipped:
        print("\nskipped files:")
        for path_str, err in report.skipped[:20]:
            print(f"  - {path_str}: {err}")
        if len(report.skipped) > 20:
            print(f"  ... and {len(report.skipped) - 20} more")

    return 0 if report.files_processed > 0 or report.files_total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
