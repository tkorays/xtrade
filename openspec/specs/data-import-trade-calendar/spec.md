# Capability: data-import-trade-calendar

## Purpose

One-shot importer that moves the legacy ``mos`` trade-calendar DuckDB dump
into the Postgres ``trade_calendar`` table so business code can rely on
it without re-implementing the conversion.

## Requirements

### Requirement: One-shot import script for `trade_date.db`

The repository SHALL ship a script
``scripts/import_trade_calendar.py`` that reads the legacy DuckDB at
``C:\Users\tkorays\.mos\data\trade_date.db`` (configurable via
``--source``) and upserts every row into the Postgres
``trade_calendar`` table.

The script SHALL:

- Connect to the configured Postgres database; create the database if
  it does not exist (requires ``CREATEDB``); run ``alembic upgrade head``
  before importing so the schema is up-to-date.
- Read the DuckDB table named by ``--source-table`` (default
  ``trade_date``) and project the columns ``exchange``, ``date``,
  ``is_trading``.
- Normalise the leading ``Exchange.`` prefix on the ``exchange``
  column (for example ``Exchange.SH`` → ``SH``) so only the canonical
  three-letter codes land in Postgres.
- Skip rows whose ``exchange`` / ``date`` / ``is_trading`` is NULL and
  log a warning for each skipped row.
- Upsert in batches of ``--batch-size`` (default 1000) via
  ``PostgresTradeCalendarRepository.upsert_days``.
- Support ``--dry-run`` which discovers rows and reports the planned
  work without writing.
- Print a final report including rows_total, rows_written, skipped,
  and elapsed_seconds.

#### Scenario: Successful import

- **WHEN** a user runs `uv run python scripts/import_trade_calendar.py` against the configured Postgres
- **THEN** the script upserts every (normalised) row from the DuckDB into Postgres `trade_calendar`, prints the row counts, and exits with status 0
- **AND** re-running the script is idempotent: existing `(exchange, date)` pairs are overwritten with the same `is_trading` value (no duplicates created)

#### Scenario: Dry run does not write

- **WHEN** a user runs `uv run python scripts/import_trade_calendar.py --dry-run`
- **THEN** the script reads the DuckDB, normalises rows, and prints the discovered count and per-exchange breakdown
- **AND** the Postgres `trade_calendar` row count is unchanged

#### Scenario: `Exchange.` prefix is normalised

- **WHEN** the source DuckDB contains both `'SH'` and `'Exchange.SH'` rows for the same date with `is_trading=TRUE`
- **THEN** the script upserts only one row per `(exchange, date)` pair into Postgres with `exchange='SH'`, deduplicated by the normalisation step
- **AND** `PostgresTradeCalendarRepository.get_trading_days(start, end)` SHALL reflect the normalised data without the `Exchange.` prefix