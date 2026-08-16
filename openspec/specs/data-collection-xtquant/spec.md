# Capability: data-collection-xtquant

## Purpose

Lets the project pull market data from a local MiniQMT `xtquant` client on a recurring basis and persist it into the data layer's K-line repositories, while tracking per-interval watermarks so each run resumes from the last successful trading day instead of re-pulling everything from scratch. The capability exposes a `DataSource` implementation, a reusable collector service, and a `xtrade data` CLI subcommand group that operators can wire into a scheduler (cron / Task Scheduler / systemd timer) once per trading day.
## Requirements
### Requirement: `XtQuantDataSource` registered under the name `"xtquant"`

The `SourceRegistry` SHALL default-register a `DataSource` named `"xtquant"` alongside the existing `"mock"` source. The registration SHALL be **lazy**: it SHALL attempt `import xtquant.xtdata` and SHALL silently skip the registration (without raising) when the import fails. After a successful lazy import, the registered source SHALL be available via `SourceRegistry().get("xtquant")`.

The `XtQuantDataSource` class SHALL implement the four `DataSource` methods. For each method:
- `fetch_instruments() -> list[Instrument]` SHALL return `[]` (xtquant has no batched instrument listing; the collector reads `instrument` from Postgres directly).
- `fetch_bars(symbol, start, end, interval) -> pd.DataFrame` SHALL call `xtdata.download_history_data2` with the formatted date strings (see below), then `xtdata.get_local_data(..., dividend_type="none")`, and SHALL return the per-symbol DataFrame normalised to the schema the repositories expect (ms-UTC → Asia/Shanghai tz, then `date` for `1d` / tz-aware datetime for `1m`; `pre_close` dropped).
- `fetch_adjust_factors(symbol, start, end) -> pd.DataFrame` and `fetch_trade_calendar(start, end) -> pd.DataFrame` SHALL return empty DataFrames (out of scope for this change; the corresponding Postgres tables are populated by separate paths).

The `interval` argument to `fetch_bars` SHALL be restricted to `{"1d", "1m"}`; any other value SHALL raise `ValueError` before any xtquant call.

#### Scenario: `xtquant` registered when the package is importable

- **WHEN** a developer runs `from xtrade.data.sources import SourceRegistry` on a machine where `import xtquant` succeeds
- **THEN** `SourceRegistry().get("xtquant")` returns an `XtQuantDataSource` instance and `SourceRegistry().names()` includes `"xtquant"`

#### Scenario: `xtquant` not registered when the package is missing

- **WHEN** a developer runs `from xtrade.data.sources import SourceRegistry` on a machine where `import xtquant` raises `ModuleNotFoundError`
- **THEN** `SourceRegistry().get("xtquant")` raises `KeyError` listing known sources (which include `"mock"` but exclude `"xtquant"`); no `ModuleNotFoundError` leaks out of `SourceRegistry` itself

#### Scenario: `fetch_bars` returns a normalised per-symbol DataFrame

- **WHEN** `xtquant_source.fetch_bars("000001.SZ", date(2024, 1, 1), date(2024, 1, 5), "1d")` is called and MiniQMT has cached data for `000001.SZ` in that window
- **THEN** the returned `pd.DataFrame` has columns `[symbol, time, open, high, low, close, volume, amount]` and a `time` column whose dtype is `datetime64[ns]` of `datetime.date` values (no timezone for `1d`); the `pre_close` column is absent

#### Scenario: `fetch_bars` rejects unsupported intervals before any xtquant call

- **WHEN** `xtquant_source.fetch_bars("000001.SZ", date(2024, 1, 1), date(2024, 1, 5), "5m")` is called
- **THEN** the method raises `ValueError("unsupported interval '5m'")` and `xtdata.download_history_data2` is never called

#### Scenario: Date strings are formatted per `interval`

- **WHEN** `xtquant_source.fetch_bars("X", date(2024, 1, 1), date(2024, 1, 31), "1m")` is called
- **THEN** the underlying `xtdata.download_history_data2` is invoked with `start_time="20240101000000"` and `end_time="20240131235959"`

#### Scenario: Lazy timestamp conversion avoids off-by-one day

- **WHEN** xtquant returns a row with `time = 1704038400000` (UTC midnight for the requested 2024-01-01 trading day in Beijing)
- **THEN** the row's `time` column value in the returned DataFrame is `date(2024, 1, 1)`, not `date(2023, 12, 31)`

### Requirement: `data_sync_state` table for per-interval watermarks

The data layer SHALL persist a `data_sync_state` table managed by SQLAlchemy 2.x ORM, with the following columns:
- `source` (VARCHAR, NOT NULL): the registered `DataSource` name (currently always `"xtquant"`).
- `interval` (VARCHAR, NOT NULL): one of `"1d"`, `"1m"`.
- `last_trade_date` (DATE, nullable): the latest trading date whose bars have been written for this `(source, interval)` pair.
- `last_run_at` (TIMESTAMPTZ, NOT NULL): the wall-clock timestamp of the most recent completed run (success or failure).
- `rows_written` (BIGINT, NOT NULL, default `0`): total rows persisted by the most recent run.
- `status` (VARCHAR, NOT NULL): one of `"ok"`, `"failed"`, `"in_progress"`.
- `error` (TEXT, nullable): the error message when `status="failed"`, otherwise `NULL`.

The primary key SHALL be `(source, interval)`. There SHALL be exactly one row per `(source, interval)` pair.

#### Scenario: A successful run advances the watermark

- **WHEN** `DailyXtQuantCollector.run(interval="1d")` completes successfully and the latest bar returned has `trade_date = 2024-01-05`
- **THEN** the `(source="xtquant", interval="1d")` row in `data_sync_state` has `last_trade_date = 2024-01-05`, `last_run_at = <now>`, `rows_written = <count>`, `status = "ok"`, `error = NULL`

#### Scenario: A failed run records the error without advancing the watermark

- **WHEN** `DailyXtQuantCollector.run(interval="1d")` raises before any rows are written
- **THEN** the `(source="xtquant", interval="1d")` row in `data_sync_state` has `status = "failed"`, `error = <message>`, `last_trade_date` unchanged, and `rows_written = 0`

#### Scenario: First-ever run creates the watermark row

- **WHEN** `DailyXtQuantCollector.run(interval="1d")` is called and no row exists for `(source="xtquant", interval="1d")`
- **THEN** the collector inserts a new row with the run's outcome; the table contains exactly one row for that key after the call returns

### Requirement: `DailyXtQuantCollector` service

The data layer SHALL expose a `DailyXtQuantCollector` class in `xtrade.data.collection.xtquant`. Its constructor SHALL accept:
- `source: DataSource` — the producer (typically `SourceRegistry().get("xtquant")`).
- `instrument_repo: InstrumentRepository`.
- `kline_repo: KLineRepository`.
- `sync_state_repo: DataSyncStateRepository` — a new protocol + ORM-backed implementation for `data_sync_state`.
- `trade_calendar` — used to skip non-trading days.
- `clock: Callable[[], datetime]` — injected so tests can freeze "now" (default `datetime.now`).

Its `run(interval, *, batch_size=50, lookback_days=5, dry_run=False, start_date=None, end_date=None) -> SyncReport` method SHALL:
1. Reject `interval` not in `{"1d", "1m"}` with `ValueError` before any IO.
2. Reject `start_date > end_date` (when both are non-`None`) with `ValueError` before any IO.
3. Resolve the target window using the following precedence:
   - If **both** `start_date` and `end_date` are supplied (non-`None`): window is `(start_date, end_date)`.
   - Else if only `start_date` is supplied: window is `(start_date, today)`.
   - Else if only `end_date` is supplied: window is `(watermark.last_trade_date - lookback_days if watermark else today - lookback_days, end_date)`, clamped forward to the next trading day for the start.
   - Else (no date overrides): window is `(watermark.last_trade_date - lookback_days if watermark else today - lookback_days, today)`, clamped forward to the next trading day for the start.
4. Iterate `instrument_repo.list_symbols()` in chunks of `batch_size`.
5. For each chunk, call `source.fetch_bars(symbol, start, end, interval)` for every symbol, merge the results into one DataFrame, and call `kline_repo.upsert_bars(df)`.
6. After the loop, **conditionally** update the watermark row:
   - **Routine run** (no `start_date` supplied): advance `last_trade_date` to the latest bar actually written on success; record `status="failed"` and the exception message on fatal failure. This is the existing behaviour.
   - **Ad-hoc backfill** (`start_date` supplied): the `(source, interval)` row in `data_sync_state` SHALL NOT be mutated by this run. The `status`, `error`, `last_run_at`, `rows_written`, and `last_trade_date` columns SHALL be left exactly as they were before the call (or remain absent if no row existed). The `SyncReport.rows_written` and `SyncReport.last_trade_date` SHALL still be populated so the CLI can report what happened; they are simply not persisted.

Per-symbol errors SHALL be accumulated into the returned `SyncReport.symbols_skipped` list (a `list[tuple[str, str]]` of `(symbol, error_message)`). A non-empty skipped list SHALL NOT cause the run to be marked `"failed"`; the run is `"failed"` only when no rows were written at all. Ad-hoc backfills MAY also be `"failed"` for the same reason (no rows written).

#### Scenario: Run with no watermark uses `lookback_days` window

- **WHEN** `DailyXtQuantCollector.run(interval="1d")` is called and `data_sync_state` has no row for `(xtquant, 1d)`
- **THEN** the collector uses `start = today - lookback_days` and processes all symbols in that window

#### Scenario: Run with existing watermark resumes from `last_trade_date - lookback_days`

- **WHEN** `data_sync_state` has `(xtquant, 1d)` with `last_trade_date = 2024-01-05` and `lookback_days = 5`
- **THEN** the collector uses `start = 2024-01-01` (or the earliest trading day on or after that, resolved via `trade_calendar`) and processes only the trailing window

#### Scenario: Per-symbol errors do not abort the run

- **WHEN** `source.fetch_bars("BAD", ...)` raises for one symbol in a batch but succeeds for the others
- **THEN** the run continues, the successful symbols' rows are persisted, and `SyncReport.symbols_skipped` contains `("BAD", "<error>")`; the watermark row's `status` is `"ok"` and `last_trade_date` advances to the latest bar actually written

#### Scenario: `dry_run=True` performs no writes and does not touch the watermark

- **WHEN** `DailyXtQuantCollector.run(interval="1d", dry_run=True)` is called
- **THEN** the collector prints the planned window and batch count to stdout, `data_sync_state` is unchanged, and `kline_repo.upsert_bars` is not called

#### Scenario: Watermark is reset by the CLI

- **WHEN** the operator runs `xtrade data reset --interval 1d`
- **THEN** the `(xtquant, 1d)` row in `data_sync_state` is deleted; the next `sync` re-pulls from `today - lookback_days`

#### Scenario: `start_date` and `end_date` together override the window

- **WHEN** `DailyXtQuantCollector.run(interval="1d", start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))` is called
- **THEN** `source.fetch_bars` is invoked with `start=date(2024, 1, 1)` and `end=date(2024, 1, 31)` for every symbol, regardless of any existing watermark

#### Scenario: Only `end_date` overrides the trailing edge

- **WHEN** `DailyXtQuantCollector.run(interval="1d", end_date=date(2024, 1, 31))` is called and `watermark.last_trade_date = 2024-01-20` with `lookback_days = 5`
- **THEN** `source.fetch_bars` is invoked with `start=2024-01-15` (clamped to next trading day) and `end=date(2024, 1, 31)`

#### Scenario: Only `start_date` overrides the leading edge

- **WHEN** `DailyXtQuantCollector.run(interval="1d", start_date=date(2024, 1, 1))` is called
- **THEN** `source.fetch_bars` is invoked with `start=date(2024, 1, 1)` and `end=today`; the existing watermark is irrelevant

#### Scenario: `start_date > end_date` is rejected before any IO

- **WHEN** `DailyXtQuantCollector.run(interval="1d", start_date=date(2024, 2, 1), end_date=date(2024, 1, 1))` is called
- **THEN** the method raises `ValueError("start_date (2024-02-01) must be <= end_date (2024-01-01)")` and `source.fetch_bars`, `kline_repo.upsert_bars`, and `sync_state_repo.get/upsert` are never called

#### Scenario: Ad-hoc backfill does not advance the watermark

- **WHEN** `DailyXtQuantCollector.run(interval="1d", start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))` is called and `data_sync_state` already contains `(xtquant, 1d)` with `last_trade_date = 2023-12-29` and `status = "ok"`
- **THEN** the run writes the requested bars, returns `SyncReport(rows_written=N, last_trade_date=date(2024, 1, 31))`, but the `data_sync_state` row is **unchanged** after the call: `last_trade_date` is still `2023-12-29`, `status` is still `"ok"`, `last_run_at` is unchanged, and `rows_written` is unchanged

#### Scenario: Ad-hoc backfill creates no watermark row when none exists

- **WHEN** `DailyXtQuantCollector.run(interval="1d", start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))` is called and `data_sync_state` has no row for `(xtquant, 1d)`
- **THEN** the run writes the requested bars, returns `SyncReport(rows_written=N, last_trade_date=date(2024, 1, 31))`, and `data_sync_state` still has no row for `(xtquant, 1d)` after the call

### Requirement: `xtrade data sync` CLI subcommand

The CLI SHALL expose a `xtrade data` subcommand group with three subcommands:
- `xtrade data sync --interval {1d,1m} [--batch-size N] [--lookback-days N] [--dry-run] [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD]` — runs `DailyXtQuantCollector.run(interval, ...)` once. When `--start-date` is supplied the run is an **ad-hoc backfill**: the window is `(start_date, end_date or today)`, and the watermark row in `data_sync_state` is left untouched. Otherwise the existing watermark-driven behaviour applies. Prints the final `SyncReport` (`rows_written`, `symbols_skipped` count, `elapsed_seconds`, watermark `last_trade_date`) to stdout. Exit codes: `0` on full or partial success (at least one row written), `1` on unrecoverable error (no rows written, invalid args, missing xtquant, missing MiniQMT).
- `xtrade data status` — prints one line per `(source, interval)` row in `data_sync_state`, formatted `xtquant.1d  last_trade_date=YYYY-MM-DD  rows=N  status=ok  last_run_at=YYYY-MM-DDTHH:MM:SSZ`.
- `xtrade data reset --interval {1d,1m}` — deletes the watermark row for `(xtquant, --interval)` and prints a confirmation. Exit codes: `0` on success, `1` if the row did not exist.

The `xtrade data sync` command SHALL lazy-import `xtquant` after argument validation. If `xtquant` is not installed, the command SHALL exit non-zero with `ModuleNotFoundError: No module named 'xtquant'`. If `SourceRegistry().get("xtquant")` raises `KeyError`, the command SHALL exit non-zero with a message naming the missing source.

Argument validation on `xtrade data sync` SHALL also reject `start_date > end_date` (when both are given) before any IO with the same error message produced by the collector.

#### Scenario: `xtrade data sync --interval 1d` runs once and exits zero

- **WHEN** a user runs `xtrade data sync --interval 1d` on a machine where MiniQMT is running and `xtquant` is installed
- **THEN** the command exits `0` after completing one cycle, prints a `SyncReport` summary, and the `data_sync_state` table contains an updated `(xtquant, 1d)` row

#### Scenario: `xtrade data status` prints the current watermark

- **WHEN** a user runs `xtrade data status` after one or more `sync` runs
- **THEN** stdout contains one line per stored `(source, interval)` pair summarising `last_trade_date`, `rows_written`, `status`, and `last_run_at`

#### Scenario: `xtrade data reset --interval 1d` deletes the watermark

- **WHEN** a user runs `xtrade data reset --interval 1d`
- **THEN** the `(xtquant, 1d)` row is deleted from `data_sync_state` and `xtrade data status` no longer prints a line for it

#### Scenario: `--dry-run` performs no writes

- **WHEN** a user runs `xtrade data sync --interval 1d --dry-run`
- **THEN** the command prints the planned window and batch count, exits `0`, and `data_sync_state` and `kline_1d` are unchanged

#### Scenario: Missing xtquant is a clear CLI error

- **WHEN** a user runs `xtrade data sync --interval 1d` on a machine where `xtquant` is not installed
- **THEN** the command exits non-zero with `ModuleNotFoundError: No module named 'xtquant'`, and the message is printed to stderr

#### Scenario: `xtrade data sync --interval 1d --start-date 2024-01-01 --end-date 2024-01-31` performs an ad-hoc backfill

- **WHEN** a user runs `xtrade data sync --interval 1d --start-date 2024-01-01 --end-date 2024-01-31` and `data_sync_state` already contains a `(xtquant, 1d)` row with `last_trade_date = 2023-12-29`
- **THEN** the command pulls bars in `[2024-01-01, 2024-01-31]`, prints a `SyncReport` summary, exits `0`, and the existing `(xtquant, 1d)` watermark row is **unchanged** afterwards (still `last_trade_date = 2023-12-29`)

#### Scenario: `xtrade data sync --interval 1d --start-date 2024-02-01 --end-date 2024-01-01` is rejected

- **WHEN** a user runs `xtrade data sync --interval 1d --start-date 2024-02-01 --end-date 2024-01-01`
- **THEN** the command exits non-zero with `start_date (2024-02-01) must be <= end_date (2024-01-01)` and no xtquant / DB call is made

#### Scenario: `xtrade data sync --interval 1d --end-date 2024-01-31` is a backfill up to a specific date

- **WHEN** a user runs `xtrade data sync --interval 1d --end-date 2024-01-31` and `data_sync_state` has a `(xtquant, 1d)` row with `last_trade_date = 2024-01-20`
- **THEN** the command pulls bars from `2024-01-15` (clamped to next trading day) to `2024-01-31`, prints a `SyncReport`, exits `0`, and the existing `(xtquant, 1d)` watermark row is **unchanged** afterwards (still `last_trade_date = 2024-01-20`)

### Requirement: Default `lookback_days` is bounded

The CLI's `xtrade data sync --lookback-days` SHALL default to `5`. Any value greater than `30` SHALL be rejected with a clear error before any IO (`--lookback-days must be <= 30`). This bounds the per-run cost on first-time backfill and prevents accidental full-history pulls.

#### Scenario: Excessive lookback is rejected

- **WHEN** a user runs `xtrade data sync --interval 1d --lookback-days 365`
- **THEN** the command exits non-zero with `--lookback-days must be <= 30` and no xtquant / DB call is made

### Requirement: No business logic in `data-collection-xtquant`

The `XtQuantDataSource` and `DailyXtQuantCollector` SHALL NOT import from `xtrade.core`, `xtrade.strategy`, `xtrade.execution`, or `xtrade.engine`. They SHALL depend only on `xtrade.data` (repositories, source registry, engine) and the Python standard library + `pandas`. This keeps the data-collection capability layered below the business modules per the project's separation-of-concerns rule.

#### Scenario: No business-layer imports

- **WHEN** a developer inspects the imports of `xtrade.data.sources.xtquant` and `xtrade.data.collection.xtquant`
- **THEN** no module from `xtrade.core`, `xtrade.strategy`, `xtrade.execution`, or `xtrade.engine` is imported

### Requirement: Progress and slow-call logging

The `DailyXtQuantCollector` SHALL emit `logging` records at `INFO` level for every observable phase transition so operators can confirm the run is alive and locate the slow stage. The collector's `__init__` SHALL accept two new keyword-only parameters:
- `slow_fetch_seconds: float` — threshold above which a single `source.fetch_bars` call is reported as slow. Default `30.0`.
- `slow_upsert_seconds: float` — threshold above which a single `kline_repo.upsert_bars` call is reported as slow. Default `60.0`.

#### Scenario: Run-start INFO line

- **WHEN** `DailyXtQuantCollector.run(interval="1d")` is called and proceeds past input validation
- **THEN** a single `INFO` record is emitted containing: `interval`, `mode` (`"ad-hoc"` if `start_date is not None`, else `"routine"`), window `[start, end]`, `symbols_total`, `batch_size`, and `batches_total`

#### Scenario: Per-batch progress INFO line

- **WHEN** `DailyXtQuantCollector.run` finishes processing a batch (one iteration of the `for i in range(0, len(symbols), batch_size)` loop)
- **THEN** one `INFO` record is emitted containing: `batch_index` (1-based), `batches_total`, `symbols_done`, `symbols_total`, `rows_written` (cumulative), `symbols_skipped` (cumulative), `batch_fetch_seconds`, `batch_upsert_seconds` (only when the batch wrote at least one row), `elapsed_seconds`, and a formatted `eta_seconds` derived from `elapsed / symbols_done * (symbols_total - symbols_done)` (or `"--"` when no symbols are done yet)

#### Scenario: Slow `fetch_bars` emits a WARNING

- **WHEN** a single `source.fetch_bars(symbol, ...)` call takes longer than `slow_fetch_seconds`
- **THEN** one `WARNING` record is emitted containing the offending `symbol` and the measured `elapsed_seconds`; the run is NOT aborted

#### Scenario: Slow `upsert_bars` emits a WARNING

- **WHEN** a single `kline_repo.upsert_bars(df)` call takes longer than `slow_upsert_seconds` and `df` is non-empty
- **THEN** one `WARNING` record is emitted containing the batch index and the measured `elapsed_seconds`; the run is NOT aborted

#### Scenario: Run-end INFO line (one record)

- **WHEN** `DailyXtQuantCollector.run` returns (success, partial, or failed)
- **THEN** one `INFO` record is emitted with `status`, `rows_written`, `symbols_skipped`, `last_trade_date`, `elapsed_seconds`, and `mode` (`"ad-hoc"` or `"routine"`)

#### Scenario: Dry-run path still logs only the planned-window line

- **WHEN** `DailyXtQuantCollector.run(interval="1d", dry_run=True)` is called
- **THEN** the existing dry-run `INFO` record is emitted and NO per-batch / run-end records are emitted (because no batches run)

#### Scenario: Threshold parameters are injectable for tests

- **WHEN** the caller constructs the collector with `slow_fetch_seconds=0.0` and `slow_upsert_seconds=0.0`
- **THEN** every `fetch_bars` and `upsert_bars` call emits a `WARNING` (because every non-zero duration exceeds the threshold), making the slow-call path testable without `time.sleep`

### Requirement: Per-symbol DEBUG progress line

The `DailyXtQuantCollector._fetch_and_write_batch` SHALL emit a `logger.debug` line immediately before each `source.fetch_bars(symbol, ...)` call. The line SHALL contain the symbol's 1-based position within the run's full symbol list (`symbol_index/symbols_total`) and the symbol name. The format string SHALL be the module-level constant `VERBOSE_SYMBOL_PROGRESS_FORMAT` so callers (CLI, tests) can inspect it.

The DEBUG line SHALL fire even when the underlying `fetch_bars` call is slow or blocks, so operators running with `--verbose` see continuous activity.

#### Scenario: DEBUG line emitted before each `fetch_bars`

- **WHEN** `_fetch_and_write_batch` is invoked with a non-empty `batch` and the collector's logger accepts `DEBUG` records
- **THEN** one `DEBUG` record is emitted per symbol in the batch, with the format `<symbol_index>/<symbols_total>  sym=<symbol>  interval=<interval>`

#### Scenario: DEBUG line suppressed at INFO level

- **WHEN** `_fetch_and_write_batch` runs while the collector's logger level is `INFO` or higher (the default)
- **THEN** no per-symbol DEBUG records appear in the captured log stream; per-batch INFO records still fire

#### Scenario: `VERBOSE_SYMBOL_PROGRESS_FORMAT` is a module constant

- **WHEN** a test inspects `xtrade.data.collection.xtquant.VERBOSE_SYMBOL_PROGRESS_FORMAT`
- **THEN** the value is a `str` containing the substrings `"symbol"`, `"sym"`, and `"interval"` so an `str.format`-style render is possible

### Requirement: `xtrade data` subcommand group

The CLI SHALL expose a new subcommand group `xtrade data` registered under the existing `xtrade` Click group. The group SHALL contain three subcommands:
- `xtrade data sync --interval {1d,1m} [--batch-size N] [--lookback-days N] [--dry-run] [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] [--verbose]`
- `xtrade data status`
- `xtrade data reset --interval {1d,1m}`

The `--start-date` and `--end-date` flags, when supplied to `xtrade data sync`, override the watermark-driven window (see the `data-collection-xtquant` capability for the exact resolution rules). When **either** is supplied, the run is treated as an ad-hoc backfill: the `(xtquant, --interval)` row in `data_sync_state` is NOT mutated. When neither is supplied, the existing watermark-driven behaviour applies.

The `--verbose` flag, when supplied to `xtrade data sync`, lowers the logger level for `xtrade.data.collection.xtquant` (and its descendants) to `DEBUG` for the duration of the run. The per-symbol DEBUG progress lines become visible on stderr. The level is restored to its prior value when the command returns.

Invoking `xtrade data --help` SHALL list all three subcommands. Invoking `xtrade --help` SHALL include `data` in its subcommand list alongside `config` and `backtest`.

#### Scenario: `xtrade --help` lists `data`

- **WHEN** a user runs `xtrade --help`
- **THEN** stdout lists `data` alongside `config` (and any other existing subcommands)

#### Scenario: `xtrade data --help` lists `sync`, `status`, `reset`

- **WHEN** a user runs `xtrade data --help`
- **THEN** stdout lists the three subcommands and explains each one's flags, including `--start-date`, `--end-date`, and `--verbose` on `sync`

#### Scenario: `xtrade data sync --interval 5m` is rejected

- **WHEN** a user runs `xtrade data sync --interval 5m`
- **THEN** the command exits non-zero with `--interval must be one of {1d, 1m}` and no xtquant / DB call is made

#### Scenario: `xtrade data sync --start-date ... --end-date ...` is rejected when start > end

- **WHEN** a user runs `xtrade data sync --interval 1d --start-date 2024-02-01 --end-date 2024-01-01`
- **THEN** the command exits non-zero with `start_date (2024-02-01) must be <= end_date (2024-01-01)` and no xtquant / DB call is made

#### Scenario: `xtrade data sync --start-date 2024-01-01 --end-date 2024-01-31` performs an ad-hoc backfill and does not touch the watermark

- **WHEN** a user runs `xtrade data sync --interval 1d --start-date 2024-01-01 --end-date 2024-01-31` and `data_sync_state` already contains a `(xtquant, 1d)` row
- **THEN** the command pulls bars in `[2024-01-01, 2024-01-31]`, prints a `SyncReport`, exits `0`, and the existing `(xtquant, 1d)` watermark row is unchanged afterwards (still the same `last_trade_date`, `status`, `last_run_at`)

#### Scenario: `xtrade data sync --end-date 2024-01-31` is a backfill up to a specific date

- **WHEN** a user runs `xtrade data sync --interval 1d --end-date 2024-01-31` and `data_sync_state` has a `(xtquant, 1d)` row with `last_trade_date = 2024-01-20`
- **THEN** the command pulls bars from `2024-01-15` (clamped to next trading day) to `2024-01-31`, prints a `SyncReport`, exits `0`, and the existing `(xtquant, 1d)` watermark row is unchanged afterwards (still `last_trade_date = 2024-01-20`)

#### Scenario: `xtrade data sync --verbose` lowers the collector logger to DEBUG

- **WHEN** a user runs `xtrade data sync --interval 1d --verbose`
- **THEN** per-symbol DEBUG lines (`symbol=N/Total  sym=...`) appear on stderr; when the command returns, the prior logger level for `xtrade.data.collection.xtquant` is restored

#### Scenario: `xtrade data reset --interval 5m` is rejected

- **WHEN** a user runs `xtrade data reset --interval 5m`
- **THEN** the command exits non-zero with `--interval must be one of {1d, 1m}` and no DB call is made

### Requirement: `DailyXtQuantCollector.run` per-batch fetch and write

The collector's run loop SHALL process the symbol universe in batches of `batch_size`. For each batch the collector SHALL call `source.fetch_bars_bulk(batch, start, end, interval)` **once**, then `kline_repo.upsert_bars(merged)` **once** with the merged long-format frame returned by the source.

`batch_size` defaults SHALL be:
- `--interval 1d`: `batch_size = len(instruments)` (the operator can override with `--batch-size`).
- `--interval 1m`: `batch_size = 50` (the operator can override with `--batch-size`).
- `--batch-size-max` defaults to `500`; any value above it SHALL be rejected by the CLI before any IO.

When `source.fetch_bars_bulk` raises (network error, MiniQMT rejection, OOM), the collector SHALL retry the same call **once**. If the retry also raises, the entire batch SHALL be recorded as `symbols_skipped` with the exception class + message; the run SHALL continue with the next batch.

When the bulk fetch succeeds but returns a wide frame containing some symbols with all-NaN rows for required fields, those symbols SHALL be recorded as `symbols_skipped` with `xtquant returned empty frame for this symbol`; the rest of the batch SHALL still be upserted.

#### Scenario: Routine run with no watermark, default 1d batch_size

- **WHEN** the operator runs `xtrade data sync --interval 1d` and `len(instruments) = 7000`
- **THEN** the collector invokes `fetch_bars_bulk(<all 7000>, ...)` exactly once, then `upsert_bars(<merged>)` once; the run produces one INFO progress line per batch (a single batch in this scenario), one `sync done` line, and exits `0`

#### Scenario: 1m run with default batch_size

- **WHEN** the operator runs `xtrade data sync --interval 1m` and `len(instruments) = 7000`
- **THEN** the collector splits the symbols into `ceil(7000 / 50) = 140` batches, invokes `fetch_bars_bulk(50 syms, ...)` once per batch, and `upsert_bars(<50-symbol merged frame>)` once per batch

#### Scenario: Bulk fetch raises; retry succeeds

- **WHEN** the first call to `source.fetch_bars_bulk(batch, ...)` raises `ConnectionError` and the second call returns a valid frame
- **THEN** the run proceeds normally: rows from the second call are upserted, `symbols_skipped` is empty for this batch, the slow-fetch WARN (if any) reports the cumulative fetch time

#### Scenario: Bulk fetch raises twice; entire batch skipped

- **WHEN** both calls to `source.fetch_bars_bulk(batch, ...)` raise the same exception class
- **THEN** every symbol in `batch` is appended to `symbols_skipped` with the exception class + message; the run continues to the next batch

#### Scenario: Bulk fetch returns frame with all-NaN rows for some symbols

- **WHEN** the bulk fetch returns a DataFrame containing `(symbol, time, open, ..., volume)` rows for symbols A and B, but A's rows have NaN for `open`/`high`/`low`/`close`
- **THEN** `A` is appended to `symbols_skipped` with `xtquant returned empty frame for this symbol`; `B`'s rows are upserted

#### Scenario: `--batch-size > --batch-size-max` is rejected

- **WHEN** the operator runs `xtrade data sync --interval 1d --batch-size 1000` and `--batch-size-max` defaults to `500`
- **THEN** the CLI exits non-zero with `--batch-size must be <= 500; got 1000` and no xtquant / DB call is made

### Requirement: Progress logging cadence

The collector's progress lines (the INFO line per batch and the per-batch DEBUG line under `--verbose`) SHALL be emitted exactly once per `fetch_bars_bulk + upsert_bars` pair. The DEBUG line under `--verbose` SHALL report the symbol list as `bulk-fetch: syms=<count> interval=<interval> first=<symbol> last=<symbol>`. Operators SHALL use `--verbose` to confirm which batch is in flight when wall-clock time is dominated by `fetch_bars_bulk`.

#### Scenario: `--verbose` on a 7000-symbol 1d run

- **WHEN** the operator runs `xtrade data sync --interval 1d --verbose` and `len(instruments) = 7000`
- **THEN** one DEBUG line `bulk-fetch: syms=7000 interval=1d first=000001.SZ last=603999.SH` is emitted, followed by the existing batch INFO line and the run-end INFO line; no per-symbol DEBUG lines fire (because the fetch is now batch-scoped)

