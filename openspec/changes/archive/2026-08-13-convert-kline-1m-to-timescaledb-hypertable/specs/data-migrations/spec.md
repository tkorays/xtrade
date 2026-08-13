## MODIFIED Requirements

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