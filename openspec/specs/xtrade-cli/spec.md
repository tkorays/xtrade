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

### Requirement: CLI does not import any `mos.*` module

The CLI and config implementations SHALL NOT import from `mos.*`; they SHALL depend only on the project's own modules and approved runtime dependencies (`click`, `pydantic`, `pydantic-settings`).

#### Scenario: No mos dependency in CLI

- **WHEN** the CLI is exercised (`xtrade --help`, `xtrade config list`, etc.)
- **THEN** `sys.modules` contains no `mos.*` entries (verifiable in unit tests by monkeypatching `sys.modules` and inspecting imports)