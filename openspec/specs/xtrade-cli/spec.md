# Capability: xtrade-cli

## Purpose

Provides the `xtrade` console entry point and the `xtrade config` subcommand group so users can read and edit runtime configuration from the shell without editing JSON by hand.

## Requirements

### Requirement: `xtrade` console entry point

The project SHALL install a console script named `xtrade` that, when invoked, dispatches to the Click command group exposed by `xtrade.cli.xtrade:cli`. Invoking `xtrade --help` SHALL list the available subcommands and SHALL include a top-level help text describing the project.

#### Scenario: `xtrade --help` exits zero

- **WHEN** a user runs `xtrade --help` in a shell where the project is installed
- **THEN** the command exits with status code 0 and stdout contains both `Usage:` and a reference to the `config` subcommand

#### Scenario: `xtrade` is invokable as a module

- **WHEN** a user runs `python -m xtrade.cli.xtrade --help`
- **THEN** the same help text is printed and exit status is 0

### Requirement: `xtrade config list` subcommand

The CLI SHALL expose a `xtrade config list` subcommand that prints every configuration item, the resolved config file path, and whether that file currently exists on disk. Default behavior lists the main config type (`main`); an optional `--type` flag selects other registered types (none registered yet beyond `main`, so `--type main` is the only valid value in this change).

#### Scenario: List prints defaults when no file exists

- **WHEN** a user runs `xtrade config list` and `~/.xtrade/config.json` does not exist
- **THEN** exit status is 0, stdout includes the resolved config file path, indicates that the file does not exist, and lists the full set of default config items including the `postgres` section

#### Scenario: List with unknown type fails

- **WHEN** a user runs `xtrade config list --type does-not-exist`
- **THEN** the command exits non-zero with an error message naming the unknown type

### Requirement: `xtrade config get <key>` subcommand

The CLI SHALL expose a `xtrade config get <key>` subcommand that accepts a dotted key (e.g. `postgres.port`) and prints the resolved value as `type.key = value`. If the key does not resolve to a value, the command SHALL exit zero and print `<type>.<key> 不存在` (or an equivalent message identifying the missing key).

#### Scenario: Get returns existing key

- **WHEN** a user runs `xtrade config get postgres.host`
- **THEN** the command exits 0 and stdout contains `postgres.host = <value>` where `<value>` matches the current resolved setting (default `localhost` when no file is present)

#### Scenario: Get returns missing-key message

- **WHEN** a user runs `xtrade config get nope.missing`
- **THEN** the command exits 0 and stdout identifies that `nope.missing` is not present

### Requirement: `xtrade config set <key> <value>` subcommand

The CLI SHALL expose a `xtrade config set <key> <value>` subcommand that updates a single dotted key in the active config and writes the resulting JSON back to the same file. The CLI SHALL coerce string literals (`true`, `false`, integer strings, float strings, JSON arrays / objects, raw strings prefixed with `~`) into appropriate Python scalars; nested dict updates SHALL be deep-merged so untouched sections are preserved.

#### Scenario: Set persists across reload

- **WHEN** a user runs `xtrade config set postgres.port 5433` and then runs `xtrade config get postgres.port`
- **THEN** the second command reports `postgres.port = 5433`

#### Scenario: Set with invalid type is rejected

- **WHEN** a user runs `xtrade config set postgres.port not-an-int`
- **THEN** the command exits non-zero, no config file write occurs, and an error message identifies that the value failed validation

#### Scenario: Nested set only touches that leaf

- **WHEN** a user sets `postgres.port` to a new value while the existing file has a custom `postgres.host`
- **THEN** after the write the file still contains the custom `postgres.host` and the new port

### Requirement: `xtrade config types` subcommand

The CLI SHALL expose a `xtrade config types` subcommand that lists the available configuration types. In this change only `main` is registered.

#### Scenario: Types lists main

- **WHEN** a user runs `xtrade config types`
- **THEN** stdout contains a line identifying `main` as the main configuration type

### Requirement: `xtrade data` subcommand group

The CLI SHALL expose a new subcommand group `xtrade data` registered under the existing `xtrade` Click group. The group SHALL contain three subcommands:
- `xtrade data sync --interval {1d,1m} [--batch-size N] [--batch-size-max N] [--lookback-days N] [--dry-run] [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] [--verbose]`
- `xtrade data status`
- `xtrade data reset --interval {1d,1m}`

`xtrade data sync` SHALL accept:
- `--interval {1d, 1m}` (required)
- `--batch-size N` (default varies by `--interval`: `1d` → `len(instruments)`, `1m` → `50`)
- `--batch-size-max N` (default `500`; rejects values exceeding it)
- `--lookback-days N` (default `5`, clamped to `[1, 30]`)
- `--dry-run`, `--start-date`, `--end-date`, `--verbose` (unchanged)

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

#### Scenario: Default `1d` batch_size is the whole market

- **WHEN** the operator runs `xtrade data sync --interval 1d` (no `--batch-size`)
- **THEN** the CLI defaults `--batch-size` to `len(instruments)` so a single bulk call covers all symbols; the value is logged in the run-start INFO line as `batch_size=<N>`

#### Scenario: Default `1m` batch_size is 50

- **WHEN** the operator runs `xtrade data sync --interval 1m` (no `--batch-size`)
- **THEN** the CLI defaults `--batch-size` to `50`

#### Scenario: `--batch-size-max` rejects too-large values

- **WHEN** the operator runs `xtrade data sync --interval 1d --batch-size 1000 --batch-size-max 500`
- **THEN** the CLI exits non-zero with `--batch-size must be <= 500; got 1000`

### Requirement: `xtrade data sync --batch-size-max` defaults

`xtrade data sync --batch-size-max N` SHALL default to a value that depends on `--interval`:
- `--interval 1d` → no upper bound (the max check is bypassed); the operator's `--batch-size` is accepted up to `len(instruments)`.
- `--interval 1m` → `500`.

The `--batch-size-max` flag itself stays; the operator MAY override the resolved default with an explicit value (including a large value for `1m` runs, at their own risk). The resolved max is shown in the run-start INFO line as `batch_size_max=<value or "(unbounded)">`.

#### Scenario: `1d` default accepts the whole market

- **WHEN** the operator runs `xtrade data sync --interval 1d` against 7000 instruments
- **THEN** the CLI resolves `--batch-size` to `len(instruments) = 7000`; no max-rejection error fires; the run proceeds with one bulk fetch covering all 7000 symbols

#### Scenario: `1d` accepts an explicit batch-size above 500

- **WHEN** the operator runs `xtrade data sync --interval 1d --batch-size 6000`
- **THEN** the CLI accepts it (no max-rejection error); one bulk fetch covers the 6000 symbols

#### Scenario: `1m` default still caps at 500

- **WHEN** the operator runs `xtrade data sync --interval 1m --batch-size 1000` (no `--batch-size-max`)
- **THEN** the CLI rejects with `--batch-size must be <= 500 (--batch-size-max); got 1000`

#### Scenario: `1m` accepts an explicit `--batch-size-max` raise

- **WHEN** the operator runs `xtrade data sync --interval 1m --batch-size 800 --batch-size-max 1000`
- **THEN** the CLI accepts it; one bulk fetch is issued for the 800 symbols

#### Scenario: `1d` with an explicit `--batch-size-max` is honoured

- **WHEN** the operator runs `xtrade data sync --interval 1d --batch-size 600 --batch-size-max 500`
- **THEN** the CLI rejects with `--batch-size must be <= 500 (--batch-size-max); got 600` (the explicit max still wins for `1d`)

#### Scenario: `xtrade data reset --interval 5m` is rejected

- **WHEN** a user runs `xtrade data reset --interval 5m`
- **THEN** the command exits non-zero with `--interval must be one of {1d, 1m}` and no DB call is made

### Requirement: CLI does not import any `mos.*` module

The CLI and config implementations SHALL NOT import from `mos.*`; they SHALL depend only on the project's own modules and approved runtime dependencies (`click`, `pydantic`, `pydantic-settings`).

#### Scenario: No mos dependency in CLI

- **WHEN** the CLI is exercised (`xtrade --help`, `xtrade config list`, etc.)
- **THEN** `sys.modules` contains no `mos.*` entries (verifiable in unit tests by monkeypatching `sys.modules` and inspecting imports)