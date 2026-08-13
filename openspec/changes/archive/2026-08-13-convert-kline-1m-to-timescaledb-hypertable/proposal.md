## Why

The current `kline_1m` table is a regular Postgres table. With ~688 symbols × ~240 bars/day × N years of data, row counts grow quickly and indexes / VACUUM / storage become expensive. TimescaleDB hypertables split the table into per-day chunks (smaller indexes, faster range scans), enable native compression after a warm-up window, and keep `INSERT ... ON CONFLICT` semantics intact for upsert. We convert `kline_1m` only — `kline_1d` already holds ~12 M rows in production and is explicitly out of scope for this change.

## What Changes

- Modify the existing `0001_initial` migration so that `kline_1m` is created as a TimescaleDB **hypertable** with `chunk_time_interval = 1 day` instead of a regular Postgres table. `kline_1d` DDL is unchanged.
- Run `CREATE EXTENSION IF NOT EXISTS timescaledb` at the top of `0001_initial.upgrade()`.
- Add a TimescaleDB **compression policy** that compresses chunks older than 7 days, segmentby `symbol`, orderedby `ts`. Compression reduces on-disk size 70–90% for a 1-minute bar table.
- No **retention policy** (1-minute data is kept indefinitely; explicit user decision).
- Add the `timescaledb` Python driver note to README (optional — `psycopg[binary]` already suffices; no new dependency).
- **No code changes** in `xtrade.data.market_data.kline.PostgresKLineRepository` or the `KLineRepository` Protocol: hypertables are transparent for the existing COPY + `INSERT ... ON CONFLICT` write path and `SELECT` read path. The repository contract is unchanged.
- **No code changes** in `xtrade.data.orm.market.KLine1mORM`: hypertables are regular Postgres tables with extra metadata; the SQLAlchemy declarative mapping still works. We may want to add `__table_args__` metadata for documentation, but it's not required.
- **BREAKING**: any environment without TimescaleDB 2.x (the extension is already in `shared_preload_libraries` on this server) cannot run `alembic upgrade head` after this change. The migration aborts cleanly with `extension "timescaledb" is not available`.

## Capabilities

### New Capabilities

- `data-market-timescale`: extends `data-market` with TimescaleDB hypertable semantics for `kline_1m`. See `specs/data-market-timescale/spec.md`.

### Modified Capabilities

- `data-market`: `kline_1m` is now a hypertable with `chunk_time_interval = 1 day` and a 7-day compression policy. No protocol changes.
- `data-migrations`: `0001_initial` now requires TimescaleDB extension and creates `kline_1m` as a hypertable. `kline_1d` and other tables unchanged.

## Impact

- Affected code:
  - `src/xtrade/data/migrations/versions/0001_initial.py` — add extension + hypertable + compression policy; downgrade drops the hypertable (which removes its chunks and policies).
  - `src/xtrade/data/migrations/env.py` — no change expected (still reads `Config.data.database.url`).
  - `src/xtrade/data/orm/market.py` — `KLine1mORM` may gain a `__table_args__` comment indicating it backs a hypertable. The columns and types are unchanged.
  - `tests/data/test_market_data.py` — no behavioural change; existing `kline_1m` integration tests continue to work (writes via `INSERT ... ON CONFLICT` against a hypertable are identical to a regular table from the application's point of view).
- Affected APIs: none. The `KLineRepository` Protocol is unchanged.
- Affected dependencies: no new Python dependencies. TimescaleDB is server-side; the operator must have `timescaledb` in `shared_preload_libraries` and the `CREATE EXTENSION` privilege (default for `postgres` superuser).
- Affected systems: Postgres server must have TimescaleDB 2.x installed. The dev server (`192.168.5.62:5432`) already has `timescaledb` in `shared_preload_libraries` (verified) and 2.28.0 installed.
- Out of scope:
  - Continuous aggregates (e.g. materialised 5-minute / 15-minute / 1-hour rollups). Open a follow-up change if needed.
  - Retention policy. The user explicitly opted out.
  - Migrating any existing `kline_1m` rows from a regular table — the production `kline_1m` is empty so this is a no-op.
  - `kline_1d` hypertable conversion — explicit user decision to keep it as a regular table.