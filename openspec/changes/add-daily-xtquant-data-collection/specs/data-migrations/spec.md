## MODIFIED Requirements

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