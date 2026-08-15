"""One-shot script: import the legacy ``mos`` trade-calendar DuckDB
table into Postgres ``trade_calendar``.

The legacy layout lives at
``C:\\Users\\tkorays\\.mos\\data\\trade_date.db`` and contains a single
table ``trade_date`` with columns
``(exchange VARCHAR NOT NULL, date DATE NOT NULL, is_trading BOOLEAN NULLABLE)``.
The Postgres target table ``trade_calendar`` has columns
``(exchange VARCHAR(16) NOT NULL, date DATE NOT NULL,
is_trading BOOLEAN NOT NULL)`` with a composite primary key on
``(exchange, date)``.

Schema mapping:

- DuckDB ``exchange`` -> Postgres ``exchange`` (string; the leading
  ``Exchange.`` prefix is stripped so only the canonical three-letter
  codes ``SH`` / ``SZ`` / ``BJ`` land in Postgres)
- DuckDB ``date``     -> Postgres ``date``
- DuckDB ``is_trading`` -> Postgres ``is_trading``

The script is idempotent: ``ON CONFLICT (exchange, date) DO UPDATE``
overwrites any pre-existing row for the same pair. It does NOT
``TRUNCATE``; users wanting a fresh start must do that manually.

Run::

    uv run python scripts/import_trade_calendar.py
    uv run python scripts/import_trade_calendar.py --dry-run
    uv run python scripts/import_trade_calendar.py --batch-size 500
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd
import psycopg
from sqlalchemy.engine import make_url

# ---------------------------------------------------------------------------
# Paths — adjust DEFAULT_SOURCE if your DuckDB lives elsewhere.
# ---------------------------------------------------------------------------

DEFAULT_SOURCE: Path = Path(r"C:\Users\tkorays\.mos\data\trade_date.db")
DEFAULT_SOURCE_TABLE: str = "trade_date"
DEFAULT_BATCH_SIZE: int = 1_000


logger = logging.getLogger(__name__)


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
        pass


def run_alembic_upgrade() -> None:
    """Run ``alembic upgrade head`` via subprocess (uses the same env vars)."""
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)


# ---------------------------------------------------------------------------
# Discovery + projection.
# ---------------------------------------------------------------------------


def _normalise_exchange(value: str) -> str:
    """Strip the leading ``Exchange.`` prefix used by the legacy dump.

    ``Exchange.SH`` -> ``SH``; ``SH`` stays ``SH``. Unknown values pass
    through untouched (the schema is just VARCHAR(16) so this is safe).
    """
    prefix = "Exchange."
    if value.startswith(prefix):
        return value[len(prefix) :]
    return value


def fetch_records(
    con: duckdb.DuckDBPyConnection,
    table: str,
) -> list[tuple[str, object, bool]]:
    """Read all rows from ``table`` and project to the target schema.

    Returns a list of ``(exchange, date, is_trading)`` tuples matching
    the columns the Postgres repository upserts. The ``exchange``
    column is normalised (leading ``Exchange.`` prefix stripped) so
    only canonical three-letter codes land in Postgres. Rows missing
    ``exchange`` / ``date`` / ``is_trading`` are skipped (logged).
    """
    query = f"SELECT exchange, date, is_trading FROM {table} ORDER BY exchange, date"
    raw = con.execute(query).fetchall()
    records: list[tuple[str, object, bool]] = []
    for row in raw:
        exchange, ex_date, is_trading = row
        if exchange is None or ex_date is None or is_trading is None:
            logger.warning(
                "skipping row with NULL required field: exchange=%r date=%r is_trading=%r",
                exchange,
                ex_date,
                is_trading,
            )
            continue
        records.append((_normalise_exchange(str(exchange)), ex_date, bool(is_trading)))
    return records


# ---------------------------------------------------------------------------
# Per-batch upsert (worker function; module-level so it can be pickled).
# ---------------------------------------------------------------------------


def _upsert_batch(batch: list[tuple[str, object, bool]]) -> int:
    """Upsert one batch via PostgresTradeCalendarRepository; return rows written."""
    # Lazy import — keeps this script importable without DB config for unit
    # tests of the pure helpers above.
    from xtrade.data.engine import reset_engine
    from xtrade.data.market_data.trade_calendar import (
        PostgresTradeCalendarRepository,
    )

    # Each batch uses a fresh repository instance; clear the SQLAlchemy pool
    # before we touch the engine to be safe across batches.
    reset_engine()
    repo = PostgresTradeCalendarRepository()
    df = pd.DataFrame(batch, columns=["exchange", "date", "is_trading"])
    repo.upsert_days(df)
    return len(batch)


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


@dataclass
class Report:
    rows_total: int = 0
    rows_written: int = 0
    skipped: int = 0
    elapsed_seconds: float = 0.0

    @property
    def rows_per_sec(self) -> float:
        return self.rows_written / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0


def run(
    records: list[tuple[str, object, bool]],
    *,
    batch_size: int,
    dry_run: bool,
) -> Report:
    """Upsert ``records`` in chunks of ``batch_size``."""
    report = Report(rows_total=len(records))
    if not records:
        return report
    if dry_run:
        report.rows_written = len(records)
        return report

    started = time.monotonic()
    last_log_at = started

    def tick() -> None:
        nonlocal last_log_at
        now = time.monotonic()
        if now - last_log_at >= 1.0:
            report.elapsed_seconds = now - started
            _log(report, dry_run)
            last_log_at = now

    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        try:
            written = _upsert_batch(batch)
            report.rows_written += written
        except Exception as exc:
            report.skipped += len(batch)
            logger.error("batch upsert failed (%d rows skipped): %r", len(batch), exc)
        tick()

    report.elapsed_seconds = time.monotonic() - started
    _log(report, dry_run)
    return report


def _log(report: Report, dry_run: bool) -> None:
    suffix = " (dry-run)" if dry_run else ""
    pct = (report.rows_written / report.rows_total * 100.0) if report.rows_total else 0.0
    print(
        f"[import-trade-calendar{suffix}] "
        f"{report.rows_written}/{report.rows_total} rows ({pct:.1f}%), "
        f"{report.skipped} skipped, "
        f"{report.elapsed_seconds:.1f}s",
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
        help=f"DuckDB file (default: {DEFAULT_SOURCE}).",
    )
    p.add_argument(
        "--source-table",
        type=str,
        default=DEFAULT_SOURCE_TABLE,
        help=f"DuckDB table to read from (default: {DEFAULT_SOURCE_TABLE!r}).",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Row chunk size for upsert (default: {DEFAULT_BATCH_SIZE}).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Read the DuckDB and report the planned work; do not write to Postgres.",
    )
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Load the configured DB URL.
    from xtrade.core.config import get_config

    config = get_config()
    db_url = config.data.database.url
    print(f"source: {args.source}")
    print(f"dry-run: {args.dry_run}")
    print(f"batch-size: {args.batch_size}")
    print(f"source table: {args.source_table}")
    print(f"db: {db_url}")

    if not args.source.exists():
        print(f"ERROR: source DuckDB not found: {args.source}", file=sys.stderr)
        return 1

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

    con = duckdb.connect(str(args.source), read_only=True)
    try:
        records = fetch_records(con, args.source_table)
    finally:
        con.close()
    print(f"discovered {len(records)} record(s)")

    if not records:
        print("nothing to do")
        return 0

    report = run(records, batch_size=args.batch_size, dry_run=args.dry_run)

    print(
        f"\n=== import complete ===\n"
        f"  rows_total:     {report.rows_total}\n"
        f"  rows_written:   {report.rows_written}\n"
        f"  skipped:        {report.skipped}\n"
        f"  elapsed_seconds:{report.elapsed_seconds:.2f}\n"
        f"  rows_per_sec:   {report.rows_per_sec:.1f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
