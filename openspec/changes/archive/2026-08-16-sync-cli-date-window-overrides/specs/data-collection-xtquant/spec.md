## MODIFIED Requirements

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