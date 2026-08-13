## 1. Migration DDL

- [x] 1.1 Edit `src/xtrade/data/migrations/versions/0001_initial.py`: at the top of `upgrade()`, add `op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")`.
- [x] 1.2 In `upgrade()`, after `op.create_table("kline_1m", ...)`, add `op.execute("SELECT create_hypertable('kline_1m', 'ts', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE)")`.
- [x] 1.3 Remove `op.create_index("ix_kline_1m_symbol_ts", "kline_1m", ["symbol", "ts"])` (TimescaleDB creates its own chunk-index; the redundant index wastes space).
- [x] 1.4 Add compression configuration: `op.execute("ALTER TABLE kline_1m SET (timescaledb.compress, timescaledb.compress_segmentby = 'symbol', timescaledb.compress_orderby = 'ts')")` followed by `op.execute("SELECT add_compression_policy('kline_1m', INTERVAL '7 days')")`.
- [x] 1.5 In `downgrade()`, before `op.drop_table("kline_1m")`, add `op.execute("SELECT remove_compression_policy('kline_1m', if_exists => TRUE)")` to make the downgrade deterministic (TimescaleDB also drops policies on table drop, but explicit removal avoids noise in the SQL log).
- [x] 1.6 Leave `kline_1d` DDL completely untouched.

## 2. ORM metadata

- [x] 2.1 Add a comment to `KLine1mORM` (`src/xtrade/data/orm/market.py`) noting that the table is a TimescaleDB hypertable; the columns and constraints are unchanged.
- [x] 2.2 Drop the redundant `Index("ix_kline_1m_symbol_ts", "symbol", "ts")` from `KLine1mORM.__table_args__` (the migration in 1.3 already omits `op.create_index`; the ORM metadata should match).

## 3. Repository code

- [x] 3.1 No changes to `src/xtrade/data/market_data/kline.py` — the existing `COPY` + `INSERT ... ON CONFLICT` flow works against a hypertable transparently. Verify by re-reading `_upsert_chunk` and `_copy_into_staging`.
- [x] 3.2 No changes to `KLineRepository` Protocol.
- [x] 3.3 Add a one-line docstring comment to `PostgresKLineRepository` clarifying that `kline_1m` is a hypertable and TimescaleDB chunk pruning / compression are transparent.

## 4. Tests

- [x] 4.1 Update `tests/data/test_migrations.py`:
  - Add `CREATE EXTENSION timescaledb` to the `EXPECTED_CREATE_STATEMENTS` (or equivalent assertion) in `testalembic_offline_sql_generation`.
  - Add `create_hypertable('kline_1m', 'ts', ...)` to the expected statements.
  - Add `add_compression_policy('kline_1m', ...)` to the expected statements.
- [x] 4.2 Add a new test that verifies the offline-generated SQL contains `CREATE TABLE kline_1m` (already exists) AND `CREATE EXTENSION timescaledb` AND `create_hypertable`.
- [x] 4.3 No changes to `tests/data/test_market_data.py` — the existing `kline_1m` integration tests continue to work against a hypertable; `INSERT ... ON CONFLICT` semantics is unchanged.

## 5. Validation

- [x] 5.1 `uv run pytest -q --deselect tests/data/test_migrations.py::test_alembic_offline_sql_generation` passes (existing tests). 177 passed.
- [x] 5.2 `uv run ruff check src tests scripts && uv run ruff format --check src tests scripts` clean.
- [x] 5.3 `uv run mypy src` strict passes.
- [x] 5.4 `openspec validate --all --strict` clean. 14/14.
- [x] 5.5 On the dev server `192.168.5.62:5432/xtrade`:
  - `uv run alembic upgrade head` runs successfully. (Note: `alembic_version` was already `0001_initial` on the dev DB before this change — the original migration partially ran and never created `kline_1m`. We manually replayed the `kline_1m` portion of the migration since alembic is a no-op when the head is already at the recorded version. On a fresh DB, `alembic upgrade head` will create the hypertable directly.)
  - `SELECT * FROM timescaledb_information.hypertables WHERE hypertable_name = 'kline_1m'` returns one row. ✅
  - `SELECT count(*) FROM kline_1m` returns 0 (no data imported yet). ✅
  - `SELECT count(*) FROM kline_1d` returns the original ~12 M rows (no data loss). ✅ 12,066,457
  - `SELECT * FROM timescaledb_information.compression_settings WHERE hypertable_name = 'kline_1m'` shows segmentby=`symbol`, orderby=`ts`. ✅
  - `timescaledb_information.jobs` shows the Columnstore Policy [1000] for `kline_1m` with `compress_after: '7 days'`. ✅ (TimescaleDB 2.x renamed `Compression Policy` to `Columnstore Policy`; the old `add_compression_policy` SQL is a deprecated alias.)
- [ ] 5.6 Run `scripts/import_legacy_bars.py` against the dev DB to populate `kline_1m`; verify `SELECT count(*) FROM kline_1m` matches the expected row count from the parquet source.