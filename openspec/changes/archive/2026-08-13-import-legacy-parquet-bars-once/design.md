## Context

The legacy `LocalDataSystem` writes K-line data to a flat parquet layout under `F:\Quant\data\bars`. The new `xtrade` system replaced that with two Postgres tables (`kline_1d`, `kline_1m`) created from `0001_initial.py`. After the previous change, the database, the schema, and the `KLineRepository.upsert_bars` Protocol exist; what is missing is a one-shot importer that moves the legacy corpus into Postgres. The current source snapshot has:

- `hot/1d.parquet` — one file, 947 782 rows × 7 073 symbols, 22 MB, columns `[date, symbol, open, high, low, close, pre_close, volume, amount]`.
- `hot/1m/{symbol}/2026.parquet` — 688 files (~31k rows each, ~800 KB), ~550 MB total, columns `[time, symbol, open, high, low, close, pre_close, volume, amount]`.
- `cold/` — empty in this snapshot (the legacy `LocalDataSystem.archive_to_cold` was never run).
- `hot/adjust_factors.parquet` — not present in this snapshot (the legacy `save_adjust_factors` was never called).

The importer is a **single-use CLI command**, not a recurring ingestion pipeline. The Postgres configured by `Config.data.database.url` is `postgresql+psycopg://postgres:br13jhhrhl@192.168.5.62:5432/xtrade`; the database itself does not exist yet (verified by `select current_database()` probe returning a Chinese-localised "database xtrade does not exist" error), so the importer must create it.

The importer does not own the schema; it only triggers `alembic upgrade head`. It does not own the repository; it only calls `KLineRepository.upsert_bars(df)`.

## Goals / Non-Goals

**Goals:**
- Provide a one-shot CLI command that walks the legacy layout, normalises columns, and bulk-loads into `kline_1d` / `kline_1m`.
- Auto-create the target database (if missing) and run `alembic upgrade head` so the import is self-contained.
- Be idempotent: re-running the command only rewrites overlapping rows.
- Use multi-process parallelism to keep wall time short on the 1m corpus (688 files).

**Non-Goals:**
- Replace `pump` / `DataSource` / recurring ingestion. This is a one-shot loader, not a pipeline.
- Ingest `adjust_factors` (the corresponding legacy file does not exist in the current snapshot). If the user later wants to ingest it, open a follow-up change.
- Ingest the "cold" layout. The current snapshot has no cold files; the discoverer SHALL still walk it for completeness, but tests cover only the hot layout.
- Truncate the destination tables. The importer never `TRUNCATE`s; users must do that manually if they want to start over.
- Stream the rows back to the DB. The bottleneck is parquet read + COPY; reading per-file into one DataFrame and dispatching to `upsert_bars` is sufficient.

## Decisions

### Decision 1: Multi-process pool with a shared counter, NOT one COPY per worker

`ProcessPoolExecutor` with `max_workers = min(8, os.cpu_count())`. Each worker:

1. Picks a path from a `multiprocessing.JoinableQueue`.
2. Reads the parquet into a `pandas.DataFrame`.
3. Normalises columns (rename `date` → `time` for 1d, drop `pre_close`, add `interval`).
4. Calls `KLineRepository.upsert_bars(df)` (which uses `COPY` + `ON CONFLICT`), in batches of `Config.data.batch_size`.
5. Updates a shared `multiprocessing.Value` counter (atomic int) and reports every N files.

**Why not one PostgreSQL session per worker?** `SqlAlchemy` `Engine` per worker is fine; each worker process creates its own connection pool lazily. The connection count will be `workers × pool_size` (`pool_size` default 5). 8 workers × 5 = 40 connections, well within typical Postgres `max_connections=100`. To stay safe we reduce pool size per worker to 2 via `pool_size` arg in `create_engine` (or accept the default since we override).

**Why not threads?** Parquet read with `pyarrow` releases the GIL during deserialisation, but pandas' CSV-writer / COPY side does not free the GIL — so threads bottleneck on the COPY writer. Multi-process wins.

**Why not a single COPY per file?** That would require re-streaming the parquet via `pyarrow.RecordBatch.iter_batches`, which is more code for no measurable throughput gain; the current `KLineRepository.upsert_bars` already batches internally.

### Decision 2: Database creation via psycopg autocommit, NOT SQLAlchemy

`SQLAlchemy` `CREATE DATABASE` requires raw `psycopg` because `CREATE DATABASE` cannot run inside a transaction:

```python
import psycopg
admin_dsn = url.set(database="postgres")
with psycopg.connect(admin_dsn, autocommit=True) as conn:
    with conn.cursor() as cur:
        cur.execute("CREATE DATABASE xtrade")
```

If the configured user lacks `CREATEDB`, psycopg raises `InsufficientPrivilege`; we catch and re-raise with a clear message.

**Why not `sqlalchemy_utils.create_database`?** It would add a new dependency. `psycopg` is already a runtime dep.

### Decision 3: `alembic upgrade head` invoked via subprocess, NOT in-process

```python
subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True, env=env)
```

**Why subprocess?** The project's `alembic` is configured to read the same `Config` via env vars; running in-process would require importing `alembic.config.Config` and juggling two configurations. Subprocess keeps the migration path identical to how a developer would run it manually.

### Decision 4: Discoverer walks `<root>/hot/1d.parquet` and `<root>/hot/1m/{symbol}/{year}.parquet` only

The discoverer does NOT walk `cold/` because the current snapshot has no cold files. If the user later has cold data, they can re-run with a different source root; the discoverer is a function that returns a list of `(interval, path)` records, and the executor iterates over them.

The discoverer assumes:
- `hot/1d.parquet` exists → one `("1d", path)` record.
- `hot/1m/{symbol}/{year}.parquet` files exist → one `("1m", path)` record per file.
- `cold/1d/{symbol}.parquet` and `cold/1m/{symbol}/{year}.parquet` are also walked if present (defensive — does not break the current snapshot).

This matches the existing `LocalDataSystem.get_data_path` semantics: 1d is a single file at `hot/1d.parquet` containing multi-symbol rows, 1m is partitioned by `(symbol, year)`.

### Decision 5: Normaliser drops `pre_close` and renames `date` → `time` for 1d

```python
def normalize(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    df = df.copy()
    if interval == "1d":
        df = df.rename(columns={"date": "time"})
    if "pre_close" in df.columns:
        df = df.drop(columns=["pre_close"])
    df["interval"] = interval
    df["time"] = pd.to_datetime(df["time"], utc=True)
    for col in ("open", "high", "low", "close", "amount"):
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype("int64")
    return df
```

Returns columns `[symbol, time, interval, open, high, low, close, volume, amount]` in the order required by `KLINE_REQUIRED_COLUMNS`.

**Why rename `date` → `time` for 1d?** The current `KLineRepository.upsert_bars` expects a `time` column named generically so it can route to `trade_date` (1d) or `ts` (1m) internally. Keeping the legacy `date` column would force the repository to special-case 1d.

### Decision 6: No batching inside the worker — `upsert_bars` already does it

`KLineRepository.upsert_bars(df)` already chunks by `Config.data.batch_size` and writes via `COPY` + `ON CONFLICT`. The worker passes the full per-file DataFrame through. Empirically, a 31k-row 1m file fits in ~1 MB of memory; even a 1d file (947k rows) is only ~80 MB. No streaming is needed.

### Decision 7: Progress reporting via shared counter + 1-second logging

`multiprocessing.Value("i", 0)` is updated by each worker. The main process polls every 1 second and prints `[N/total] files processed, M rows written, R rows/sec`. At completion, the final report is printed.

This is enough for a one-shot command; a real-time progress bar (`tqdm`) is excluded because it adds a dependency and the importer is not run interactively.

### Decision 8: Skipped-file tolerance

If `pandas.read_parquet` raises on a single file, the worker logs the path and reason, increments a "skipped" counter, and continues. The final report includes the skipped count. The command exits non-zero only if **no** files were successfully processed (i.e. the source is unreadable as a whole).

### Decision 9: Database URL parsing

`Config.data.database.url` is a `pydantic.PostgresDsn` (e.g. `postgresql+psycopg://postgres:br13jhhrhl@192.168.5.62:5432/xtrade`). To get the underlying psycopg-compatible DSN and the database name:

```python
from sqlalchemy.engine import make_url
url = make_url(str(cfg.data.database.url))
db_name = url.database  # "xtrade"
admin_dsn = url.set(database="postgres").render_as_string(hide_password=False)
psycopg_dsn = url.render_as_string(hide_password=False).replace("postgresql+psycopg://", "postgresql://")
```

## Risks / Trade-offs

- **[Risk] Configured Postgres user lacks `CREATEDB`** → Abort with a clear error message; document the alternative (manual `CREATE DATABASE xtrade`).
- **[Risk] `alembic upgrade head` fails mid-run** → The importer aborts before any rows are written; the user can fix the migration and re-run (idempotent — re-running is safe).
- **[Risk] Multi-process pool duplicates connections** → Each worker spins up its own `Engine` from `xtrade.data.engine.create_engine()`. To cap total connections, override `pool_size=2` per worker (default 5). 8 workers × 2 = 16 connections, well within Postgres defaults.
- **[Risk] Worker crashes wholesale** → ProcessPoolExecutor's `BrokenPoolError` is caught; the command exits non-zero and reports the crash. The `(symbol, time_col)` upsert is idempotent, so re-running picks up where it left off.
- **[Risk] Field `pre_close` contains data we need** → Verified with the user: `pre_close` is intentionally dropped (recoverable from `adj_factor` table on the read path; the `xtrade` schema does not store `pre_close`).
- **[Risk] Memory bloat for 1d.parquet (947k rows)** → ~80 MB peak; acceptable for a one-shot command on a developer machine. Workers read one file at a time; the main process holds no rows.
- **[Risk] Cold layout has files** → The discoverer walks cold paths defensively; the current snapshot has none, but the importer does not break.
- **[Risk] `adjust_factors` data loss** → The current snapshot has no `adjust_factors.parquet`. If the user does have one, they must open a follow-up change; out of scope here.
- **[Risk] psycopg connection error on Windows GBK encoding** → `psycopg.connect` can raise `UnicodeDecodeError` on the GBK error path; catching this with a clear "DB not found / not reachable" message is required during bootstrap.

### Decision 10 (apply-time): `cursor.copy()` is a context manager (psycopg3)

The original (pre-existing) `_copy_into_staging` used `cursor.copy_expert` (psycopg2 API). Under psycopg3 the equivalent is `cursor.copy()` which returns a `Copy` context manager — you write CSV bytes via `copy.write(...)` and the context exit finalises the COPY. The previous code also missed the fact that the chunk carried an extra `interval` column that overflowed the staging table's column count, producing a `BadCopyFileFormat` error on the real Postgres. The fix is two-fold:

1. Use `with cursor.copy(...) as copy: copy.write(buf)` instead of `copy_expert`.
2. Subset the chunk to `data_cols` before serialising to CSV.

These are independent bugs and both fixed in this change.

### Decision 11 (apply-time): `pre_close` is dropped by the normaliser, but the chunk may still have NaN numeric columns

The legacy `LocalDataSystem` occasionally persisted rows with NaN in `open` / `high` / `low` / `close` / `volume` / `amount`. The new schema enforces `NOT NULL` on every numeric column, so the COPY into staging fails with `NotNullViolation`. The normaliser drops rows with any NaN numeric column before serialising, logging the dropped count.

## Migration Plan

This change is a one-shot loader; the migration plan is the runbook itself:

1. Verify the configured Postgres user has `CREATEDB` privilege. If not, escalate.
2. Run `xtrade data import-legacy --dry-run` to confirm the discoverer walks the expected file count.
3. Run `xtrade data import-legacy`. Expect:
   - DB created (if missing), `alembic upgrade head` outputs migration steps.
   - First 1d file processed in ~30s, ~30k rows/sec.
   - 1m files processed in parallel at ~8 × 30k rows/sec = ~240k rows/sec.
   - Total wall time ~20 minutes for the current snapshot.
4. Run `xtrade data import-legacy` again to verify idempotency: row counts unchanged.
5. Spot-check `kline_1d` row count matches `len(df)` after the parquet reads; spot-check `kline_1m` row count matches the sum of per-file row counts.

**Rollback**: `TRUNCATE kline_1d, kline_1m`, then drop the database if desired. The importer does not delete source files; the legacy parquet remains untouched.

## Open Questions

None. All material decisions (DB creation, `pre_close` drop, idempotency, parallelism) are resolved with the user.
