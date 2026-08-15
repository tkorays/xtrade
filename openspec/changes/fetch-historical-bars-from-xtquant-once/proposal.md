## Why

The new `xtrade` Postgres database holds ~12 M daily K-line rows (already populated via `import-legacy-parquet-bars-once`) but **no live ingestion path** — when a user wants fresh 1d / 1m data, they currently have no way to pull it from `xtquant` (the MiniQMT data client). The legacy `mos_quant.data.collector_bar_xtdata.DataCollectorXTData` saves to local parquet, not Postgres. We need a one-shot script that pulls historical K-lines from `xtquant` and writes them straight into `kline_1d` / `kline_1m`, using the existing `PostgresKLineRepository.upsert_bars` so the COPY + INSERT … ON CONFLICT path is reused. The script is intentionally one-shot — recurring ingestion is owned by `xtrade.data.sources.pump`, which this change does not modify.

## What Changes

- Add a new standalone script `scripts/fetch_historical_bars_xtquant.py` (entry: `uv run python scripts/fetch_historical_bars_xtquant.py`) that:
  - Loads `Config` and connects to `Config.data.database.url`.
  - Reads the configured Postgres URL, defaults to the `instrument` table for symbols (no other filtering is applied per user decision).
  - Calls `xtdata.download_history_data2(stock_list=batch, period=interval, start_time=start_str, end_time=end_str)` to bring data into MiniQMT's local cache, then `xtdata.get_local_data(...)` to read it back as a `dict[symbol, df]`.
  - Merges per-symbol frames into one DataFrame, dropping `pre_close`, converting the xtdata millisecond timestamp via `pd.to_datetime(unit="ms", utc=True).dt.tz_convert("Asia/Shanghai")` (1d → date, 1m → tz-aware datetime), adding the `interval` column.
  - Calls `PostgresKLineRepository(batch_size=...).upsert_bars(df)` per batch.
  - Accepts `--interval {1d,1m}` (required), `--start YYYY-MM-DD` (required), `--end YYYY-MM-DD` (required), `--batch-size N` (default 50 symbols), `--limit N` (default: unlimited).
  - Prints a final progress report (files / batches processed, rows written, elapsed, throughput, skipped symbols).
- Add `xtquant` to the project's optional `[xtquant]` extras (or as a documented install step). xtquant is **not on PyPI**; the user installs it from their local QMT distribution. The script will not load without xtquant installed.
- Add unit tests for the merge / transform helper using a small in-memory `dict[symbol, df]` fixture, mocked `xtdata` calls.
- **BREAKING**: anyone running this script must have MiniQMT running locally with the `xtquant` Python package installed (from `F:\QMT\python\Lib\site-packages` or similar). Without xtquant, `from xtquant import xtdata` raises `ModuleNotFoundError` at script start.

## Capabilities

### New Capabilities

- `tools/xtquant-fetch-one-shot`: a new capability folder for "one-shot scripts that pull data from external sources and write to the configured database". The new requirement is `scripts/fetch_historical_bars_xtquant.py` ships as a one-shot loader with the contract described in `specs/tools-xtquant-fetch-one-shot/spec.md`.

### Modified Capabilities

- `data-market`: no requirement change. The script uses the existing `PostgresKLineRepository.upsert_bars` contract unchanged.
- `data-sources`: no requirement change. The change does not introduce a recurring `DataSource`; the script is a standalone operator, not part of the producer-side pipeline.
- `xtrade-cli`: no change. The script lives under `scripts/` and is **not** wired into the `xtrade` CLI group.

## Impact

- Affected code:
  - `scripts/fetch_historical_bars_xtquant.py` — new standalone script (~250 lines).
  - `pyproject.toml` — add `xtquant` to `[project.optional-dependencies.xtquant]` (or as a documented `uv add xtquant` step in the README — TBD).
  - `tests/scripts/test_fetch_historical_bars_xtquant.py` — unit tests for the merge / transform helper.
- Affected APIs: none. The `KLineRepository` Protocol is unchanged.
- Affected dependencies: `xtquant` is a runtime dependency for the script. The project does not import it from any other module — the import is local to the script.
- Affected systems: the user's local machine must have MiniQMT running. Without it, the script aborts at `xtdata.connect()`. This is documented and acceptable for a one-shot script.
- Out of scope:
  - Adjusting (复权). xtquant supports `dividend_type="none"` / `front` / `back`; this change passes `none` and stores raw prices (matching the legacy parquet layout).
  - Pulling adjustment factors (`adjustment_factor` table). Separate one-shot script if needed.
  - Continuous (实时) ingestion. The script is purely historical.
  - Pulling from non-equity asset classes (期货 / 期权 / ETF). The script reads `symbol` strings as-is from the `instrument` table; xtquant treats them generically.
  - Resuming a partial run. The script does not checkpoint; re-running it re-fetches everything (the underlying `upsert_bars` is idempotent).