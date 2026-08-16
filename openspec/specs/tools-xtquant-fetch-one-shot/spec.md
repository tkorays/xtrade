# Capability: tools-xtquant-fetch-one-shot

## Purpose

Lets operators pull historical K-line data (1d or 1m) from the local `xtquant` MiniQMT client and write it into the configured Postgres database in bulk. The script is a one-shot loader; it is not part of the recurring ingestion pipeline.

## Requirements

### Requirement: `scripts/fetch_historical_bars_xtquant.py` one-shot loader

The project MUST ship a standalone script at `scripts/fetch_historical_bars_xtquant.py` that pulls historical K-lines (1d or 1m) from the local `xtquant` MiniQMT client and writes them into the configured Postgres database. The script MUST be idempotent: re-running it rewrites only the overlapping `(symbol, time_col)` rows; pre-existing rows that are outside the requested window are preserved.

The script MUST accept the following command-line flags:

- `--interval {1d,1m}` — K-line frequency (required).
- `--start YYYY-MM-DD` — inclusive start date (required).
- `--end YYYY-MM-DD` — inclusive end date (required).
- `--batch-size N` — number of symbols per `xtquant` download call (default: `50`).
- `--limit N` — process at most N symbols (default: unlimited). When set, the script takes the first N rows from the `instrument` table in primary-key order.
- `--dry-run` — discover symbols and print the planned batches; do not write to Postgres.

When invoked, the script MUST:

1. Load `Config` and resolve the target database URL via `Config.data.database.url`.
2. Read the list of instruments from the `instrument` table (no filtering by `list_date` / `delist_date`).
3. For each batch of `--batch-size` instruments:
   1. Call `xtdata.download_history_data2(stock_list=batch, period=interval, start_time=..., end_time=...)` to bring data into MiniQMT's local cache.
   2. Call `xtdata.get_local_data(stock_list=batch, period=interval, start_time=..., end_time=..., dividend_type="none")` and receive `dict[symbol, DataFrame]`.
   3. Merge per-symbol frames into a single DataFrame, dropping any `pre_close` column, normalising timestamps (xtdata returns millisecond UTC; convert to Asia/Shanghai tz, then to date for `1d` / tz-aware datetime for `1m`), adding the `interval` column.
   4. Call `PostgresKLineRepository.upsert_bars(df)` with the merged frame.
4. After all batches complete, print a final progress report (`symbols_processed / symbols_total`, `rows_written`, `elapsed_seconds`, `rows_per_sec`, `symbols_skipped`).

The script MUST exit non-zero if the `--start` / `--end` pair is invalid (`--start > --end`), if the `--interval` is not in `{"1d", "1m"}`, or if no rows were written but instruments were processed. Per-symbol errors (xtquant returns empty data, malformed frame) MUST be reported as `(symbol, error)` in the `symbols_skipped` list and MUST NOT abort the run.

The script MUST NOT import any `mos.*` module. It MUST call `xtrade.data.market_data.PostgresKLineRepository` and `xtrade.data.engine.get_engine` directly.

#### Scenario: First run on a fresh script execution

- **WHEN** a user runs `uv run python scripts/fetch_historical_bars_xtquant.py --interval 1d --start 2024-01-01 --end 2024-12-31` against a MiniQMT client that has data for the requested symbols
- **THEN** the script downloads 1d K-lines for every symbol in the `instrument` table, writes them to `kline_1d`, and prints a final report listing total symbols processed, total rows written, and elapsed time

#### Scenario: Re-run is idempotent

- **WHEN** a user runs the script a second time with the same `--start` / `--end` against the same data
- **THEN** the row count in `kline_1d` / `kline_1m` does not double, and the second run's `rows_written` equals the first run's `rows_written`

#### Scenario: `--dry-run` performs no writes

- **WHEN** a user runs the script with `--dry-run`
- **THEN** the script prints the list of symbols and the planned batch sizes, but does not call `xtdata.download_history_data2`, `xtdata.get_local_data`, or `PostgresKLineRepository.upsert_bars`

#### Scenario: `--limit N` truncates the work

- **WHEN** a user runs the script with `--limit 5`
- **THEN** the script processes at most 5 symbols and reports the truncated count in the final report

#### Scenario: Missing xtquant dependency aborts with a clear error

- **WHEN** a user runs the script on a machine that does not have the `xtquant` package installed
- **THEN** the script exits non-zero with `ModuleNotFoundError: No module named 'xtquant'` (Python's standard error message); no rows are written

#### Scenario: MiniQMT not running aborts with a clear error

- **WHEN** a user runs the script and MiniQMT is not running locally
- **THEN** `xtdata.connect()` raises and the script exits non-zero, naming the xtquant connection failure

#### Scenario: Per-symbol errors are reported, not fatal

- **WHEN** `xtdata.get_local_data` returns no data for a specific symbol in a batch (e.g. the symbol has no data in the requested window)
- **THEN** the script logs the error, increments a `symbols_skipped` counter, and continues with the remaining symbols; the final report lists the skipped count

#### Scenario: `pre_close` is dropped before insert

- **WHEN** the xtdata-returned DataFrame contains a `pre_close` column
- **THEN** the column is dropped by the merge helper before insert and never appears in `kline_1d` / `kline_1m`

#### Scenario: 1d timestamps are normalised to `trade_date` (no timezone)

- **WHEN** the script processes `--interval 1d`
- **THEN** the merge helper converts the xtdata millisecond UTC timestamp to Asia/Shanghai tz, then strips to a `date`, and routes to `kline_1d.trade_date`

#### Scenario: 1m timestamps are normalised to tz-aware datetime

- **WHEN** the script processes `--interval 1m`
- **THEN** the merge helper converts the xtdata millisecond UTC timestamp to Asia/Shanghai tz-aware datetime and routes to `kline_1m.ts`

#### Scenario: Invalid date range is rejected before any DB access

- **WHEN** a user runs `uv run python scripts/fetch_historical_bars_xtquant.py --start 2025-01-01 --end 2024-12-31` (start after end)
- **THEN** the script exits non-zero with a clear `--start must be <= --end` error message, before calling `xtdata` or `get_engine`

#### Scenario: Invalid interval is rejected before any DB access

- **WHEN** a user runs `uv run python scripts/fetch_historical_bars_xtquant.py --interval 5m` (unsupported interval)
- **THEN** the script exits non-zero with a clear `--interval must be one of {1d, 1m}` error message, before calling `xtdata` or `get_engine`