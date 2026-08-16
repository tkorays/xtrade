## MODIFIED Requirements

### Requirement: `xtrade data` subcommand group

The CLI SHALL expose a new subcommand group `xtrade data` registered under the existing `xtrade` Click group. The group SHALL contain three subcommands:
- `xtrade data sync --interval {1d,1m} [--batch-size N] [--lookback-days N] [--dry-run] [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD]`
- `xtrade data status`
- `xtrade data reset --interval {1d,1m}`

The `--start-date` and `--end-date` flags, when supplied to `xtrade data sync`, override the watermark-driven window (see the `data-collection-xtquant` capability for the exact resolution rules). When **either** is supplied, the run is treated as an ad-hoc backfill: the `(xtquant, --interval)` row in `data_sync_state` is NOT mutated. When neither is supplied, the existing watermark-driven behaviour applies.

Invoking `xtrade data --help` SHALL list all three subcommands. Invoking `xtrade --help` SHALL include `data` in its subcommand list alongside `config` and `backtest`.

#### Scenario: `xtrade --help` lists `data`

- **WHEN** a user runs `xtrade --help`
- **THEN** stdout lists `data` alongside `config` (and any other existing subcommands)

#### Scenario: `xtrade data --help` lists `sync`, `status`, `reset`

- **WHEN** a user runs `xtrade data --help`
- **THEN** stdout lists the three subcommands and explains each one's flags, including `--start-date` and `--end-date` on `sync`

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

#### Scenario: `xtrade data reset --interval 5m` is rejected

- **WHEN** a user runs `xtrade data reset --interval 5m`
- **THEN** the command exits non-zero with `--interval must be one of {1d, 1m}` and no DB call is made