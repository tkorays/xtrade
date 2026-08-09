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

The repository SHALL include one migration file `0001_initial.py` that creates every table declared in `data-market` and `data-broker` (K-line, adjustment factor, trade calendar, instrument, order, trade, position, account) with all primary keys, unique constraints, and indexes declared in the ORM models. The migration SHALL be reversible: `alembic downgrade base` removes every table.

#### Scenario: Upgrade from empty database creates all tables

- **WHEN** a developer runs `alembic upgrade head` against an empty database
- **THEN** all expected tables exist (verified by querying `information_schema.tables`)

#### Scenario: Downgrade removes all tables

- **WHEN** a developer runs `alembic downgrade base` after a successful upgrade
- **THEN** no project table remains in the database

#### Scenario: Re-running upgrade is idempotent

- **WHEN** a developer runs `alembic upgrade head` twice in a row
- **THEN** both runs succeed without error (no duplicate-table / duplicate-index errors)

### Requirement: Migration is offline-runnable for CI

`env.py` SHALL support `alembic upgrade head --sql <file.sql>` to produce a SQL script without a live connection. The script SHALL be checked into the repo or generated on demand, so CI can verify the SQL without provisioning a Postgres.

#### Scenario: Offline SQL generation works

- **WHEN** a developer runs `alembic upgrade head --sql /tmp/out.sql`
- **THEN** `/tmp/out.sql` contains `CREATE TABLE` statements for every project table

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