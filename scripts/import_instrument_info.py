"""One-shot script: import the legacy ``mos`` instrument reference DuckDB
into Postgres.

Source: ``C:\\Users\\tkorays\\.mos\\data\\instrument_info.db`` (DuckDB),
table ``instrument_info`` with columns::

    symbol        VARCHAR  (PK)
    exchange      VARCHAR  ('SH' | 'SZ')
    type          VARCHAR
    name          VARCHAR
    list_date     DATE
    status        VARCHAR  ('L' = listed, 'D' = delisted)
    price_tick    BIGINT   -- DROPPED (uniformly 0 in source)
    delist_date   DATE
    list_board    VARCHAR
    industry      VARCHAR
    area          VARCHAR
    is_t0         BOOLEAN

Target (Postgres): the ``instrument`` table defined by
:class:`xtrade.data.orm.market.InstrumentORM` — every column except
``price_tick`` is loaded. ``status`` round-trips verbatim (``'L'`` /
``'D'``); the script does NOT translate.

Run::

    uv run python scripts/import_instrument_info.py
    uv run python scripts/import_instrument_info.py --dry-run
    uv run python scripts/import_instrument_info.py --source D:\\mos\\data\\instrument_info.db
    uv run python scripts/import_instrument_info.py --batch-size 500

The script is idempotent — re-running it only rewrites overlapping
``symbol`` rows (the existing :class:`PostgresInstrumentRepository`
does an upsert keyed on the PK).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb
from sqlalchemy.engine import make_url

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SOURCE: Path = Path(r"C:\Users\tkorays\.mos\data\instrument_info.db")
DEFAULT_BATCH_SIZE: int = 1_000

logger = logging.getLogger("import_instrument_info")


# ---------------------------------------------------------------------------
# Bootstrap: ensure target database exists (mirrors import_legacy_bars.py)
# ---------------------------------------------------------------------------


def ensure_database(url: str) -> None:
    """Create the target database if missing. Idempotent."""
    import psycopg

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
        pass  # race; another process created it.


def run_alembic_upgrade() -> None:
    """Run ``alembic upgrade head`` so the ``instrument`` table exists."""
    import subprocess

    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)


# ---------------------------------------------------------------------------
# Discovery + normalisation
# ---------------------------------------------------------------------------


def discover_table_name(con: duckdb.DuckDBPyConnection) -> str:
    """Return the first user table name. We expect ``instrument_info`` but
    fall back to whatever single table the DB contains."""
    rows = con.execute("SHOW TABLES").fetchall()
    if not rows:
        raise RuntimeError("source DuckDB contains no tables")
    return rows[0][0]


# Columns we project from DuckDB, in the order they are persisted to Postgres.
# Mirrors :class:`xtrade.data.market_data.instrument.Instrument`'s keyword set;
# ``price_tick`` is intentionally dropped (uniformly 0 in the source dump).
_INSTRUMENT_COLUMNS: tuple[str, ...] = (
    "symbol",
    "name",
    "exchange",
    "type",
    "list_date",
    "delist_date",
    "status",
    "list_board",
    "industry",
    "area",
    "is_t0",
)

# Columns enforced NOT NULL by the Postgres ``instrument`` schema. Rows with
# NULL in any of these are skipped (logged) rather than aborted, so a single
# bad row doesn't kill a 7k-row import.
_INSTRUMENT_REQUIRED: frozenset[str] = frozenset(
    {"symbol", "name", "exchange", "type", "list_date", "status"}
)


def fetch_records(
    con: duckdb.DuckDBPyConnection,
    table: str,
) -> list[dict[str, object]]:
    """Read all rows from ``table`` and project to the target schema.

    Returns one dict per row, keyed by :data:`_INSTRUMENT_COLUMNS`. The
    dict shape matches :class:`Instrument`'s keyword arguments (caller
    passes ``Instrument(**row)``); ``price_tick`` is dropped and ``status``
    is not remapped — the legacy single-letter codes round-trip verbatim.
    """
    column_list = ", ".join(_INSTRUMENT_COLUMNS)
    query = f"SELECT {column_list} FROM {table} ORDER BY symbol"
    raw = con.execute(query).fetchall()
    records: list[dict[str, object]] = []
    for row in raw:
        record: dict[str, object] = dict(zip(_INSTRUMENT_COLUMNS, row, strict=True))
        missing = [c for c in _INSTRUMENT_REQUIRED if record.get(c) is None]
        if missing:
            logger.warning(
                "skipping row with NULL required field(s) %s: %r",
                missing,
                record,
            )
            continue
        # Normalise: stringify VARCHAR columns, coerce is_t0 to bool
        # (defensive against NULL even though the schema check above
        # already rejects it).
        record["symbol"] = str(record["symbol"])
        record["name"] = str(record["name"])
        record["exchange"] = str(record["exchange"])
        record["type"] = str(record["type"])
        record["status"] = str(record["status"])
        record["is_t0"] = bool(record["is_t0"])
        for col in ("list_board", "industry", "area"):
            value = record[col]
            record[col] = None if value is None else str(value)
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# Per-batch upsert
# ---------------------------------------------------------------------------


def _upsert_batch(
    batch: list[dict[str, object]],
) -> int:
    """Upsert one batch via PostgresInstrumentRepository; return rows written."""
    # Lazy import — keeps this script importable without DB config for unit
    # tests of the pure helpers above.
    from xtrade.data.engine import reset_engine
    from xtrade.data.market_data.instrument import (
        Instrument,
        PostgresInstrumentRepository,
    )

    # Each batch uses a fresh repository instance; clear the SQLAlchemy pool
    # before we touch the engine to be safe across batches.
    reset_engine()
    repo = PostgresInstrumentRepository()
    for record in batch:
        repo.upsert(Instrument(**record))  # type: ignore[arg-type]
    # ``upsert`` returns None; report the batch length so the user sees what
    # was imported (consistent with import_legacy_bars.py).
    return len(batch)


# ---------------------------------------------------------------------------
# Driver
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
    records: list[dict[str, object]],
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
        f"[import-instrument-info{suffix}] "
        f"{report.rows_written}/{report.rows_total} rows ({pct:.1f}%), "
        f"{report.skipped} skipped, "
        f"{report.elapsed_seconds:.1f}s",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"Source DuckDB file (default: {DEFAULT_SOURCE}).",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Rows per upsert batch (default: {DEFAULT_BATCH_SIZE}).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Read the source DuckDB and report the planned work; do not write to Postgres.",
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
    print(f"db: {db_url}")

    if not args.source.is_file():
        print(f"ERROR: source DuckDB not found: {args.source}", file=sys.stderr)
        return 2

    # Discover + read in one connection (read-only).
    con = duckdb.connect(str(args.source), read_only=True)
    try:
        table = discover_table_name(con)
        print(f"source table: {table}")
        records = fetch_records(con, table)
    finally:
        con.close()

    print(f"discovered {len(records)} record(s)")
    if not records:
        print("no records; nothing to do")
        return 0

    if not args.dry_run:
        print("\n[bootstrap] ensuring target database exists...")
        try:
            ensure_database(db_url)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 3
        print("[bootstrap] running alembic upgrade head...")
        try:
            run_alembic_upgrade()
        except Exception as exc:
            print(f"ERROR: alembic upgrade failed: {exc}", file=sys.stderr)
            return 4

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
    sys.exit(main())
