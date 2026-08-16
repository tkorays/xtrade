# Capability: data-migrations

## Purpose

Provides schema management for the data layer using Alembic, with one initial migration that creates every market-data and broker-data table plus required indexes. The migration system SHALL be runnable both as part of project setup and in CI smoke tests.
## Requirements
### Requirement: Alembic is wired to the data layer

The project SHALL include a configured `alembic` environment under `src/xtrade/data/migrations/` with `env.py`, `script.py.mako`, and `versions/`. The `env.py` SHALL read the database URL from `Config.data.database.url` and use the same SQLAlchemy `Base` declared by `xtrade.data.orm_base.Base`.

#### Scenario: `alembic` CLI works against a configured URL

- **WHEN** a developer sets `XTRADE_DATA__DATABASE__URL=postgresql+psycopg://...` and runs `uv run alembic upgrade head` from the repo root
- **THEN** the migration applies against the configured database without any other CLI flags

#### Scenario: `env.py` does not hardcode a DSN

- **WHEN** a developer inspects `src/xtrade/data/migrations/env.py`
- **THEN** no `postgresql://` literal appears in the file; the DSN is read via `Config` or `os.environ`

### Requirement: Initial migration creates all tables

The repository SHALL include one migration file `0001_initial.py` that creates every table declared in `data-market` and `data-broker` (K-line daily, K-line 1-minute, adjustment factor, trade calendar, instrument, order, trade, position, account) with all primary keys, unique constraints, and indexes declared in the ORM models. The migration SHALL be reversible: `alembic downgrade base` removes every table.

The K-line tables SHALL be `kline_1d` (regular Postgres table with primary key `(symbol, trade_date)`) and `kline_1m` (TimescaleDB **hypertable** with primary key `(symbol, ts)`); neither table SHALL contain a `pre_close` column. The migration SHALL run `CREATE EXTENSION IF NOT EXISTS timescaledb` before creating `kline_1m` and SHALL call `SELECT create_hypertable('kline_1m', 'ts', chunk_time_interval => INTERVAL '1 day')` immediately after the table is created. The migration SHALL add a compression policy on `kline_1m` that compresses chunks older than 7 days, segmentby `symbol`, orderedby `ts`.

#### Scenario: Upgrade from empty database creates all tables

- **WHEN** a developer runs `alembic upgrade head` against an empty database where the TimescaleDB extension is available
- **THEN** all expected tables exist, including `kline_1d` (regular) and `kline_1m` (hypertable, verified by `SELECT * FROM timescaledb_information.hypertables WHERE hypertable_name = 'kline_1m'` returning one row)

#### Scenario: Downgrade removes all tables

- **WHEN** a developer runs `alembic downgrade base` after a successful upgrade
- **THEN** no project table remains in the database, including `kline_1d` and `kline_1m` (the downgrade drops the hypertable and its chunks)

#### Scenario: Re-running upgrade is idempotent

- **WHEN** a developer runs `alembic upgrade head` twice in a row
- **THEN** both runs succeed without error (no duplicate-table / duplicate-index errors; the extension creation is `IF NOT EXISTS`)

#### Scenario: Migration aborts cleanly when the extension is not available

- **WHEN** a developer runs `alembic upgrade head` against a Postgres server without the `timescaledb` extension (e.g. `shared_preload_libraries` does not include `timescaledb`)
- **THEN** the migration raises `extension "timescaledb" is not available` from Postgres and Alembic exits non-zero before any table is created

### Requirement: Migration is offline-runnable for CI

`env.py` SHALL support `alembic upgrade head --sql <file.sql>` to produce a SQL script without a live connection. The script SHALL be checked into the repo or generated on demand, so CI can verify the SQL without provisioning a Postgres.

#### Scenario: Offline SQL generation works

- **WHEN** a developer runs `alembic upgrade head --sql /tmp/out.sql`
- **THEN** `/tmp/out.sql` contains `CREATE TABLE kline_1d`, `CREATE TABLE kline_1m`, `CREATE EXTENSION timescaledb`, and `SELECT create_hypertable('kline_1m', 'ts', ...)` statements

### Requirement: Second migration creates `data_sync_state`

The repository SHALL include a second Alembic migration file `0002_data_sync_state.py` that creates the `data_sync_state` table with columns `(source VARCHAR NOT NULL, interval VARCHAR NOT NULL, last_trade_date DATE NULL, last_run_at TIMESTAMPTZ NOT NULL, rows_written BIGINT NOT NULL DEFAULT 0, status VARCHAR NOT NULL, error TEXT NULL, PRIMARY KEY (source, interval))`. The migration SHALL be reversible: `alembic downgrade base` (after both upgrades) SHALL drop the `data_sync_state` table alongside all `0001_initial.py` tables.

The migration SHALL NOT insert any rows (seeding is performed at runtime by the `DailyXtQuantumCollector` on first run).

#### Scenario: Upgrade from `0001_initial` adds `data_sync_state`

- **WHEN** a developer runs `alembic upgrade head` against a database that already has `0001_initial` applied
- **THEN** `\d data_sync_state` in psql reports the columns above with the primary key `(source, interval)` and the table is empty

#### Scenario: Downgrade removes `data_sync_state`

- **WHEN** a developer runs `alembic downgrade base` after `0002_data_sync_state` is applied
- **THEN** the `data_sync_state` table is dropped; no project table remains in the database

#### Scenario: Re-running upgrade is idempotent

- **WHEN** a developer runs `alembic upgrade head` twice in a row
- **THEN** both runs succeed without error; the second run is a no-op for `data_sync_state`

#### Scenario: `data_sync_state` starts empty

- **WHEN** a developer inspects `versions/0002_data_sync_state.py`
- **THEN** no `op.execute("INSERT ...")` / `op.bulk_insert(...)` calls appear in the file

### Requirement: Migration does not seed data

The initial migration SHALL NOT insert any rows. Seeding (trade calendars, instrument lists) is performed by the `data-sources` capability through the `pump` helper, not by migrations.

#### Scenario: No INSERT statements in the initial migration

- **WHEN** a developer inspects `versions/0001_initial.py`
- **THEN** no `op.execute("INSERT ...")` / `op.bulk_insert(...)` calls appear in the file

### Requirement: Migrations live under the data package

The `src/xtrade/data/migrations/` directory SHALL be importable / discoverable as Python so that `alembic` can find it via the configured `script_location`. No `migrations` directory SHALL exist at the repo root.

#### Scenario: `script_location` points under src

- **WHEN** a developer inspects `pyproject.toml`'s `[tool.alembic]` section (or the `alembic.ini` if used)
- **THEN** the script location is `src/xtrade/data/migrations`

