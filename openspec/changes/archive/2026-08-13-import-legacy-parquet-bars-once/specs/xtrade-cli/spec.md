## REMOVED Requirements

~~The CLI SHALL expose a `xtrade data import-legacy` subcommand that imports K-line parquet files from the legacy `mos_quant` local data layout...~~

**Reason for removal:** this loader is a one-shot script, not a recurring operation. The user's review confirmed it should live as a standalone script under `scripts/import_legacy_bars.py` rather than as a CLI subcommand. The capability move to a script-based form is reflected in the new `import-legacy-one-shot-script` requirement below.

## ADDED Requirements

### Requirement: `scripts/import_legacy_bars.py` one-shot loader

The project SHALL ship a standalone script at `scripts/import_legacy_bars.py` that imports K-line parquet files from the legacy `mos_quant` local data layout (`<root>/hot/1d.parquet` and `<root>/hot/1m/{symbol}/{year}.parquet`, with `cold/` paths walked defensively when present) into the configured Postgres database. The script SHALL be idempotent: re-running it rewrites only the overlapping `(symbol, time_col)` rows; pre-existing rows in `kline_1d` / `kline_1m` that are outside the source are preserved. The script SHALL drop `pre_close` from the source before insert (the new schema does not store `pre_close`).

The script SHALL accept the following command-line flags:

- `--source PATH` — root directory containing the legacy layout (default: `F:\Quant\data\bars`).
- `--dry-run` — discover files and report the planned work, but do not write any rows; do not auto-create the database; do not run `alembic upgrade head`.
- `--workers N` — number of worker processes for the parallel upsert loop (default: `1`, sequential).
- `--batch-size N` — row chunk size passed to `PostgresKLineRepository.upsert_bars` (default: `10000`).
- `--limit N` — process at most N files (useful for smoke tests; default: unlimited).

On startup the script SHALL:

1. Load `Config` and resolve the target database URL via `Config.data.database.url`.
2. Connect to the configured Postgres. If the target database does not exist, connect to the `postgres` default database via `psycopg.connect(..., autocommit=True)` and run `CREATE DATABASE xtrade` (or the configured DB name). If `CREATE DATABASE` fails because the configured user lacks `CREATEDB`, exit non-zero with a clear error message naming the missing privilege.
3. Run `alembic upgrade head` (via `subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"])`) against the resolved DB to create `kline_1d`, `kline_1m`, and every other `0001_initial` table. If `alembic` fails, exit non-zero with the underlying error.

For each parquet file discovered, the script SHALL:

1. Read the parquet via `pandas.read_parquet(path)`.
2. Normalise columns via `xtrade.data.import_legacy.normalize_frame(df, interval)` (which renames `date` → `time` for 1d, drops `pre_close`, drops NaN rows, and adds the `interval` column).
3. Call `PostgresKLineRepository.upsert_bars(df)` with the normalised frame.

The script SHALL distribute the per-file work across a process pool (`multiprocessing.Pool`) when `--workers > 1`. When `--workers == 1` (default), the script processes files sequentially. The script SHALL print a final report: total files processed, total rows written, total elapsed time, and aggregate rows/sec.

The script SHALL exit non-zero only if zero files were successfully processed. Per-file errors SHALL be reported as a list of `(path, error)` records in the final report.

#### Scenario: First run on a fresh database

- **WHEN** a user runs `uv run python scripts/import_legacy_bars.py` against a Postgres that has no `xtrade` database
- **THEN** the script creates the `xtrade` database, runs `alembic upgrade head`, walks the source root, and writes rows into `kline_1d` and `kline_1m`; the final report lists total files processed, total rows written, and elapsed time

#### Scenario: Re-run is idempotent

- **WHEN** a user runs the script against the same source a second time
- **THEN** the row count in `kline_1d` and `kline_1m` does not double, and the second run's "total rows written" equals the first run's

#### Scenario: `--dry-run` performs no writes

- **WHEN** a user runs `uv run python scripts/import_legacy_bars.py --dry-run`
- **THEN** the script prints the list of files it would process and the total planned row count, but writes no rows and does not run `alembic upgrade head`

#### Scenario: `--limit N` truncates the work

- **WHEN** a user runs `uv run python scripts/import_legacy_bars.py --limit 5`
- **THEN** the script processes at most 5 files and reports the truncated count in the final report

#### Scenario: Insufficient privileges abort with a clear error

- **WHEN** a user runs the script and the configured Postgres user lacks `CREATEDB` privilege
- **THEN** the script exits non-zero before any rows are written, and stderr explains that the user needs `CREATEDB` privilege (or asks the operator to create the database manually)

#### Scenario: `pre_close` is dropped before insert

- **WHEN** a parquet file contains a `pre_close` column
- **THEN** the column is dropped by `normalize_frame` before insert and never appears in `kline_1d` / `kline_1m`

#### Scenario: Cold root or empty source is not an error

- **WHEN** a user runs the script with `--source /does/not/exist`
- **THEN** the script exits zero, prints `no files found` and an empty final report (no DB writes, no alembic run)

#### Scenario: Per-file errors are reported, not fatal

- **WHEN** a parquet file is corrupt and `pandas.read_parquet` raises
- **THEN** the script logs the error, increments a "skipped files" counter, and continues with the remaining files; the final report lists the skipped count

### Requirement: One-shot script does not import from `mos.*`

The `scripts/import_legacy_bars.py` script SHALL NOT import any `mos.*` module. It SHALL call `xtrade.data.market_data.PostgresKLineRepository` and the `xtrade.data.import_legacy` helpers directly.

#### Scenario: No `mos.*` import triggered by the script

- **WHEN** a user runs `uv run python scripts/import_legacy_bars.py --dry-run`
- **THEN** `sys.modules` contains no `mos.*` entries (verifiable by unit test that monkeypatches `sys.modules` and runs the discover step)
