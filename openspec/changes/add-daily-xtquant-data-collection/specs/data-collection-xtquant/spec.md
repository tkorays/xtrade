## Purpose

Lets the project pull market data from a local MiniQMT `xtquant` client on a recurring basis and persist it into the data layer's K-line repositories, while tracking per-interval watermarks so each run resumes from the last successful trading day instead of re-pulling everything from scratch. The capability exposes a `DataSource` implementation, a reusable collector service, and a `xtrade data` CLI subcommand group that operators can wire into a scheduler (cron / Task Scheduler / systemd timer) once per trading day.

## ADDED Requirements

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

Its `run(interval, *, batch_size=50, lookback_days=5, dry_run=False) -> SyncReport` method SHALL:
1. Reject `interval` not in `{"1d", "1m"}` with `ValueError` before any IO.
2. Resolve the target window:
   - If a watermark exists, the start date SHALL be `max(watermark.last_trade_date - lookback_days, earliest_unfilled_trade_date_in_window)`.
   - If no watermark exists, the start date SHALL be `today - lookback_days`.
   - The end date SHALL be `today`.
3. Iterate `instrument_repo.list_symbols()` in chunks of `batch_size`.
4. For each chunk, call `source.fetch_bars(symbol, start, end, interval)` for every symbol, merge the results into one DataFrame, and call `kline_repo.upsert_bars(df)`.
5. After the loop, update the watermark row: on full success advance `last_trade_date` to the latest bar actually written; on partial failure (some symbols skipped, some written) advance `last_trade_date` to the latest bar actually written **and** record `status="ok"` with a non-fatal summary; on fatal failure (no rows written or unrecoverable error) record `status="failed"` and the exception message.

Per-symbol errors SHALL be accumulated into the returned `SyncReport.symbols_skipped` list (a `list[tuple[str, str]]` of `(symbol, error_message)`). A non-empty skipped list SHALL NOT cause the run to be marked `"failed"`; the run is `"failed"` only when no rows were written at all.

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

### Requirement: `xtrade data sync` CLI subcommand

The CLI SHALL expose a `xtrade data` subcommand group with three subcommands:
- `xtrade data sync --interval {1d,1m} [--batch-size N] [--lookback-days N] [--dry-run]` — runs `DailyXtQuantCollector.run(interval, ...)` once. Prints the final `SyncReport` (`rows_written`, `symbols_skipped` count, `elapsed_seconds`, watermark `last_trade_date`) to stdout. Exit codes: `0` on full or partial success (at least one row written), `1` on unrecoverable error (no rows written, invalid args, missing xtquant, missing MiniQMT).
- `xtrade data status` — prints one line per `(source, interval)` row in `data_sync_state`, formatted `xtquant.1d  last_trade_date=YYYY-MM-DD  rows=N  status=ok  last_run_at=YYYY-MM-DDTHH:MM:SSZ`.
- `xtrade data reset --interval {1d,1m}` — deletes the watermark row for `(xtquant, --interval)` and prints a confirmation. Exit codes: `0` on success, `1` if the row did not exist.

The `xtrade data sync` command SHALL lazy-import `xtquant` after argument validation. If `xtquant` is not installed, the command SHALL exit non-zero with `ModuleNotFoundError: No module named 'xtquant'`. If `SourceRegistry().get("xtquant")` raises `KeyError`, the command SHALL exit non-zero with a message naming the missing source.

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