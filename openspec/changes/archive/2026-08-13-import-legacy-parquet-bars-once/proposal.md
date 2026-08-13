## Why

The legacy `mos_quant` plugin stores K-line data as parquet files under `F:\Quant\data\bars` (cold + hot layout, ~7k symbols × 1d + 688 × 1m, ~600 MB on disk, ~23 M rows). The new `xtrade` system stores K-lines in two Postgres tables (`kline_1d`, `kline_1m`). We need a one-shot, idempotent loader that moves the existing local corpus into Postgres without rebuilding the data via Tushare / AKShare etc. This change creates the loader. It is intentionally scoped to a single script that runs once.

## What Changes

- Add a new standalone script `scripts/import_legacy_bars.py` (entry point: `uv run python scripts/import_legacy_bars.py`) that:
  - Connects to the configured Postgres (via `Config.data.database.url`).
  - Ensures the target database (`xtrade`) exists; if not, connects to the `postgres` default DB and `CREATE DATABASE xtrade` (requires the configured user to have `CREATEDB` privilege).
  - Runs `alembic upgrade head` against the resolved DB to create `kline_1d`, `kline_1m`, and every other `0001_initial` table.
  - Walks `F:\Quant\data\bars` (configurable via `--source`), reads each parquet file, normalises columns via `xtrade.data.import_legacy.normalize_frame` (which drops `pre_close`), and calls `PostgresKLineRepository.upsert_bars(...)`.
  - Drops `pre_close` before insert (the new `kline_*` tables no longer carry `pre_close`; the legacy column is intentionally discarded — recoverable from `adj_factor` on the read path).
  - Is **idempotent**: re-running rewrites only the overlapping `(symbol, time_col)` rows; pre-existing rows in `kline_1d` / `kline_1m` that are outside the source are preserved.
  - Prints a progress report (files processed, rows written, elapsed time, throughput).
- Add a small `xtrade.data.import_legacy` package exposing the `discover_files` and `normalize_frame` helpers used by the script. The package is **not** a recurring ingestion pipeline; that responsibility stays with `xtrade.data.sources.pump`.
- Add a tests suite that exercises the helpers against a `tmp_path` fixture (no real DB needed for the unit tests; integration tests are skipped without `XTRADE_TEST_DB_URL`).
- **BREAKING** for: anyone who already has data in `kline_1d` / `kline_1m` and wants to "start over" — they must manually `TRUNCATE` first; the script does not truncate by default.
- **BREAKING** for: the postgres user must have `CREATEDB` privilege. If not, the script aborts with a clear error message; no fallback to manual `CREATE DATABASE`.

## Capabilities

### New Capabilities

- `scripts/import_legacy_bars.py`: a one-shot loader script under `scripts/`. Lives outside the package; package-internal helpers (`discover_files`, `normalize_frame`) live in `xtrade.data.import_legacy`.

### New Capabilities (spec-driven delta)

- `tools/one-shot-import`: a new capability folder for "one-shot scripts that read legacy data and write to the configured database". The new requirement is `scripts/import_legacy_bars.py` ships as a one-shot loader with the contract described in `specs/tools-one-shot-import/spec.md`.

### Modified Capabilities

- `data-market`: no requirement change. The script uses the existing `PostgresKLineRepository.upsert_bars` contract unchanged.
- `data-migrations`: no requirement change. The script invokes the existing `alembic upgrade head` flow.
- `xtrade-config`: no requirement change. The script reads `Config.data.database.url` and `Config.data.batch_size` like every other data layer code.
- `xtrade-cli`: the previously-added `xtrade data import-legacy` subcommand is REMOVED in this change. The script lives under `scripts/` instead.

## Impact

- Affected code:
  - `scripts/import_legacy_bars.py` — new one-shot script (single file, ~290 lines).
  - `src/xtrade/data/import_legacy/` — small package containing:
    - `__init__.py`
    - `discovery.py` — file walker producing `{interval, path}` records.
    - `transform.py` — parquet → DataFrame normaliser (column renaming, type coercion, drop `pre_close`, drop NaN rows).
  - `tests/data/import_legacy/` — unit tests for the helpers.
  - `pyproject.toml` — `scripts/` added to `ruff.src` for linting.
- Affected APIs: no API changes; one new standalone script.
- Affected dependencies: `psycopg` (already a runtime dependency), `pyarrow` (already a runtime dependency).
- Affected systems: requires the configured Postgres user to hold `CREATEDB` privilege; documented as a prerequisite.
- Out of scope: ingesting `adjust_factors` parquet (the legacy `LocalDataSystem.save_adjust_factors` produces a hot-side file that may not be present in the current `F:\Quant\data\bars` snapshot — confirm during design).
