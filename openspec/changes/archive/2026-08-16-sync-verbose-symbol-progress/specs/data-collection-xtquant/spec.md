## ADDED Requirements

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

## MODIFIED Requirements

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