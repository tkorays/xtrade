## Context

The data layer currently persists 1-minute K-lines in a regular Postgres table (`kline_1m`). Row counts grow linearly with symbols × bars/day (688 × ~240 ≈ 165 k rows / day). The previous "split kline by interval" change exposed the table through the `KLineRepository` Protocol and routed by `interval`, but did not optimise storage. TimescaleDB 2.x is already available on the target server (verified: `shared_preload_libraries` includes `timescaledb`, extension 2.28.0). The current `xtrade` database on `192.168.5.62:5432` has `kline_1d` populated (~12 M rows) and **does not yet have `kline_1m`** (verified via `information_schema.tables`).

The migration system is Alembic. `0001_initial.py` is the **only** migration in the project (single head). It is **not yet deployed** in a way that requires backwards compatibility for `kline_1m` — the production database doesn't have the table yet. We edit `0001_initial` directly (no separate "0002" needed).

See `proposal.md` for motivation.

## Goals / Non-Goals

**Goals:**
- `kline_1m` becomes a TimescaleDB hypertable with `chunk_time_interval = 1 day`.
- A compression policy compresses chunks older than 7 days, segmentby `symbol`, orderedby `ts`.
- No retention policy.
- `kline_1d` is **untouched** — same DDL, same primary key, same data.
- The application layer (PostgresKLineRepository, KLineRepository Protocol, KLine1mORM) is unchanged.
- The migration is reversible: `alembic downgrade base` removes the hypertable and its chunks.

**Non-Goals:**
- Continuous aggregates (e.g. materialised 5m / 15m / 1h rollups).
- Retention policy.
- Migrating an existing `kline_1m` from a regular table (no data exists yet).
- `kline_1d` hypertable conversion.
- Connection-pool / driver / DSN changes.
- Per-symbol chunk pruning or time-bucket column for queries (the application reads via `ts` range scans; no per-symbol chunking is required).

## Decisions

### Decision 1: Edit `0001_initial.py` in place, no new migration

The current head is `0001_initial`. The production DB has no `kline_1m` table yet. Editing the existing migration is safe because:

- The migration is **not yet applied to a database that contains `kline_1m`** — every existing production DB will run the migration fresh (which creates the hypertable directly), and every dev DB starts from scratch.
- The dev team has confirmed "现在是开发阶段，不需要考虑1min的数据兼容问题" — there is no concern about overwriting data.

If a future change ever ships `0001_initial` to a server that has the old `kline_1m`, a follow-up `0002_*` migration would be required. That follow-up would call `CREATE EXTENSION`, `SELECT create_hypertable('kline_1m', 'ts', chunk_time_interval => INTERVAL '1 day', migrate_data => true)`, and `ALTER TABLE kline_1m SET (timescaledb.compress, timescaledb.compress_segmentby = 'symbol', timescaledb.compress_orderby = 'ts')`. Out of scope today.

### Decision 2: Hyperparameters

- `chunk_time_interval = INTERVAL '1 day'`. Smaller chunks (e.g. 1 hour) cost more in chunk-metadata overhead; larger (e.g. 1 week) reduce query granularity. 1 day is a balanced default for 1-minute bars.
- `compress_after = INTERVAL '7 days'`. Recent chunks are uncompressed for fast inserts; older chunks compress 70–90%.
- `compress_segmentby = 'symbol'`. Compression groups rows by symbol within a chunk; the most common query pattern (`WHERE symbol = ... AND ts BETWEEN ...`) reads fewer compressed blocks.
- `compress_orderby = 'ts DESC'`. Most recent rows in a segment are queried first; the engine can short-circuit.
- No retention policy. Explicit user decision; reversing a retention policy requires `add_retention_policy(...)` followed by data re-import.

### Decision 3: Extension creation

`CREATE EXTENSION IF NOT EXISTS timescaledb` runs **before** `op.create_table("kline_1m", ...)`. The `IF NOT EXISTS` makes the migration idempotent (re-running `alembic upgrade head` doesn't fail). On a server without the extension, Postgres raises `extension "timescaledb" is not available` (sqlstate `0A000`); Alembic propagates the error and exits non-zero.

`CREATE EXTENSION` requires superuser (or `pg_read_server_files` + appropriate privileges). On the dev server (`postgres` user) this is fine. In production this should be a one-time operation performed by a DBA; we document it in the design.

### Decision 4: Hypertable creation

`SELECT create_hypertable('kline_1m', 'ts', chunk_time_interval => INTERVAL '1 day')` is called via `op.execute(...)` immediately after `op.create_table(...)`. We do NOT use SQLAlchemy declarative `TimescaleDBHypertable` annotations because they require the optional `sqlalchemy-timescaledb` driver, which we don't want to add as a dependency.

The `create_hypertable` call returns a row with `true` / `false` for `ok`. We don't assert on the return value — failure (e.g. the extension is missing) propagates as an `UntranslatableKeywords` error from psycopg.

### Decision 5: Compression policy

Compression is configured in two steps:

```sql
ALTER TABLE kline_1m SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol',
    timescaledb.compress_orderby = 'ts'
);

SELECT add_compression_policy('kline_1m', INTERVAL '7 days');
```

The `ALTER TABLE SET (...)` declares the column ordering for compression. `add_compression_policy` schedules a background job that runs every chunk older than 7 days; chunks are compressed in-place without blocking writes to other chunks.

The default schedule (every other hour) is fine — no need to override.

### Decision 6: `KLine1mORM` stays as-is

The ORM's `__table_args__` already declares `UniqueConstraint("symbol", "ts", ...)` and `Index("ix_kline_1m_symbol_ts", "symbol", "ts")`. TimescaleDB hypertables require a primary key **or** a unique index on the time column; the existing `UniqueConstraint("symbol", "ts")` satisfies that. We add a comment to the ORM that the table is a hypertable, but no schema change.

Note: the existing `Index("ix_kline_1m_symbol_ts", "symbol", "ts")` is **redundant** for hypertables — TimescaleDB automatically creates an index on `(symbol, ts DESC)` for the chunk metadata. The Alembic migration drops this redundant index (only when we're creating the table fresh). For simplicity in this change, we keep the index in the ORM metadata; the migration simply doesn't recreate it (we omit `op.create_index("ix_kline_1m_symbol_ts", ...)` for `kline_1m`).

### Decision 7: Repository code is unchanged

`PostgresKLineRepository.upsert_bars` does `COPY` into a temp staging table + `INSERT ... ON CONFLICT (symbol, ts) DO UPDATE` against `kline_1m`. Hypertable is a transparent storage layer — `INSERT ... ON CONFLICT` works the same way, and `cursor.copy` works the same way. We do not change the repository code.

`get_bars` issues `SELECT ... WHERE symbol = ANY(...) AND ts BETWEEN ... AND ...`. TimescaleDB's chunk pruning is automatic and transparent to the application.

`count` issues `SELECT COUNT(*) FROM kline_1m ...` which is also transparent.

### Decision 8: Downgrade

`alembic downgrade base` drops the hypertable by calling `op.drop_table("kline_1m")`. TimescaleDB automatically removes the hypertable's chunks and policies when the underlying table is dropped. The compression policy is part of the table metadata and is removed as part of `DROP TABLE`. No explicit `remove_compression_policy(...)` is required.

## Risks / Trade-offs

- **[Risk] `CREATE EXTENSION timescaledb` requires superuser** → Documented; on the dev server `postgres` is superuser (verified). In production, the DBA runs this once before the first deployment.
- **[Risk] Application tests running against a Postgres without TimescaleDB** → All existing integration tests in `tests/data/test_market_data.py` are skipped unless `XTRADE_TEST_DB_URL` is set. CI environments that don't set it never exercise the migration. We add a unit test that verifies the migration SQL contains `CREATE EXTENSION timescaledb` and `create_hypertable('kline_1m', 'ts', ...)` via `alembic upgrade head --sql`.
- **[Risk] Compression overhead on write path** → Compression runs in the background; foreground `INSERT ... ON CONFLICT` is not affected. Only chunks older than 7 days are compressed; recent writes always go to an uncompressed chunk.
- **[Risk] Chunk pruning doesn't accelerate `get_bars` for very long time windows** → Acceptable; the typical query is `ts BETWEEN start_of_day AND end_of_day`, which prunes to a single chunk.
- **[Risk] `Index("ix_kline_1m_symbol_ts", ...)` declared in the ORM but not created in the migration** → Low; TimescaleDB creates an equivalent index for chunk metadata. If a future contributor adds the index manually, they must drop the redundant ORM index too. We add a comment to the migration explaining this.
- **[Risk] TimescaleDB extension not in `shared_preload_libraries`** → The dev server has it (verified). Production deployments must verify before running the migration. The migration fails with a clear Postgres error if the extension is missing.

## Migration Plan

Steps to apply this change:

1. **Dev (already in flight)** — `uv run alembic upgrade head` against the dev DB `192.168.5.62:5432/xtrade`. Idempotent. The `kline_1d` table is preserved as-is (existing data: ~12 M rows). `kline_1m` is created as a hypertable.
2. **Import** — re-run `scripts/import_legacy_bars.py` to populate `kline_1m` from the legacy parquet layout.
3. **Verify** — `SELECT count(*) FROM kline_1m` should match the row count of the source parquet (subject to dropna in `normalize_frame`). Compression kicks in after 7 days.
4. **CI** — the existing test suite (no DB) continues to pass; the offline SQL test (`alembic upgrade head --sql`) is updated to expect `CREATE EXTENSION timescaledb` and `create_hypertable(...)` in the generated SQL.

Rollback: `alembic downgrade base` (only safe when no `kline_1m` data exists; in our case the table is empty so it's a no-op). If `kline_1m` data has been imported, export it first before downgrading.

## Open Questions

None. All material decisions are resolved with the user (chunk interval, compression window, retention policy, migration strategy).