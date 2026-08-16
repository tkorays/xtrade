## MODIFIED Requirements

### Requirement: `xtrade data` subcommand group

The CLI SHALL expose a new subcommand group `xtrade data` registered under the existing `xtrade` Click group. The group SHALL contain three subcommands:
- `xtrade data sync --interval {1d,1m} [--batch-size N] [--lookback-days N] [--dry-run]`
- `xtrade data status`
- `xtrade data reset --interval {1d,1m}`

Invoking `xtrade data --help` SHALL list all three subcommands. Invoking `xtrade --help` SHALL include `data` in its subcommand list alongside `config` and `backtest`.

#### Scenario: `xtrade --help` lists `data`

- **WHEN** a user runs `xtrade --help`
- **THEN** stdout lists `data` alongside `config` (and any other existing subcommands)

#### Scenario: `xtrade data --help` lists `sync`, `status`, `reset`

- **WHEN** a user runs `xtrade data --help`
- **THEN** stdout lists the three subcommands and explains each one's flags

#### Scenario: `xtrade data sync --interval 5m` is rejected

- **WHEN** a user runs `xtrade data sync --interval 5m`
- **THEN** the command exits non-zero with `--interval must be one of {1d, 1m}` and no xtquant / DB call is made

#### Scenario: `xtrade data reset --interval 5m` is rejected

- **WHEN** a user runs `xtrade data reset --interval 5m`
- **THEN** the command exits non-zero with `--interval must be one of {1d, 1m}` and no DB call is made