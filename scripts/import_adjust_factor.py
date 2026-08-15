"""One-shot script: import the legacy ``mos`` adjustment-factor DuckDB
table into Postgres ``adjustment_factor``.

The legacy layout lives at ``C:\\Users\\tkorays\\.mos\\data\\adjust_factor.db``
and contains a single table ``adjust_factor`` with columns
``(symbol VARCHAR NOT NULL, date DATE NOT NULL, factor DOUBLE NULLABLE)``.
The Postgres target table ``adjustment_factor`` has columns
``(symbol VARCHAR(64) NOT NULL, ex_date DATE NOT NULL,
factor NUMERIC(20, 8) NOT NULL)`` with a UNIQUE constraint on
``(symbol, ex_date)``. Row counts in the source dump (~57k) are
bounded, so no parallel workers are needed.

Schema mapping:

- DuckDB ``symbol`` -> Postgres ``symbol`` (string)
- DuckDB ``date``   -> Postgres ``ex_date`` (rename)
- DuckDB ``factor`` -> Postgres ``factor`` (DOUBLE -> NUMERIC(20, 8))

The script is idempotent: ``ON CONFLICT (symbol, ex_date) DO UPDATE``
overwrites any pre-existing factor for the same ``(symbol, ex_date)``
pair. It does NOT ``TRUNCATE``; users wanting a fresh start must do
that manually.

Run::

    uv run python scripts/import_adjust_factor.py
    uv run python scripts/import_adjust_factor.py --dry-run
    uv run python scripts/import_adjust_factor.py --batch-size 5000
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
import psycopg
from sqlalchemy.engine import make_url

# ---------------------------------------------------------------------------
# Paths — adjust DEFAULT_SOURCE if your DuckDB lives elsewhere.
# ---------------------------------------------------------------------------

DEFAULT_SOURCE: Path = Path(r"C:\Users\tkorays\.mos\data\adjust_factor.db")
DEFAULT_SOURCE_TABLE: str = "adjust_factor"
DEFAULT_BATCH_SIZE: int = 5_000

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


def fetch_records(
    con: duckdb.DuckDBPyConnection,
    table: str,
) -> list[tuple[str, object, float]]:
    """Read all rows from ``table`` and project to the target schema.

    Returns a list of ``(symbol, ex_date, factor)`` tuples matching the
    columns the Postgres repository upserts. Rows missing ``symbol`` /
    ``ex_date`` / ``factor`` are skipped (logged) — the Postgres schema
    enforces NOT NULL on all three. The DuckDB column ``date`` is renamed
    to ``ex_date`` in the SELECT so the tuple order matches the target.
    """
    # DuckDB accepts the ``date AS ex_date`` alias directly; the resulting
    # rows are projected as ``(symbol, ex_date, factor)``.
    query = f"SELECT symbol, date AS ex_date, factor FROM {table} ORDER BY symbol, ex_date"
    raw = con.execute(query).fetchall()
    records: list[tuple[str, object, float]] = []
    for row in raw:
        symbol, ex_date, factor = row
        if symbol is None or ex_date is None or factor is None:
            logger.warning(
                "skipping row with NULL required field: symbol=%r ex_date=%r factor=%r",
                symbol,
                ex_date,
                factor,
            )
            continue
        records.append((str(symbol), ex_date, float(factor)))
    return records


# ---------------------------------------------------------------------------
# Per-batch upsert (worker function; module-level so it can be pickled).
# ---------------------------------------------------------------------------


def _upsert_batch(db_url: str, batch: list[tuple[str, object, float]]) -> int:
    """Upsert one batch via PostgresAdjustmentFactorRepository."""
    # Lazy import — keeps this script importable without DB config for unit
    # tests of the pure helpers above.
    from xtrade.data.engine import reset_engine
    from xtrade.data.market_data.adj_factor import (
        PostgresAdjustmentFactorRepository,
    )

    # Each batch uses a fresh repository instance; clear the SQLAlchemy pool
    # before we touch the engine to be safe across batches.
    reset_engine()
    repo = PostgresAdjustmentFactorRepository()
    # Build the DataFrame the repository expects.
    import pandas as pd

    df = pd.DataFrame(batch, columns=["symbol", "ex_date", "factor"])
    # ``upsert`` returns int but ``INSERT ... ON CONFLICT`` rowcount is
    # unreliable under psycopg3; report the batch length so the user sees
    # what was imported.
    repo.upsert(df)
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
    records: list[tuple[str, object, float]],
    *,
    db_url: str,
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
            written = _upsert_batch(db_url, batch)
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
        f"[import-adjust-factor{suffix}] "
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

    report = run(records, db_url=db_url, batch_size=args.batch_size, dry_run=args.dry_run)

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
