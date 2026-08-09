## Why

Today `xtrade` is a Python package with no entry point: nothing to run from the shell, and every future module (data, strategy, execution, risk) will need a place to read runtime parameters such as the PostgreSQL connection. Without a CLI and a persisted config, the first backtest or live-trading change will end up reinventing them.

## What Changes

- Add a Click-based console entry point `xtrade` exposing only a `config` subcommand group (no init / plugin / MCP / task / streamlit surface — those are out of scope here).
  - `xtrade config list` prints every config item (with file path + presence).
  - `xtrade config get <key>` reads a single dotted key (e.g. `postgres.port`).
  - `xtrade config set <key> <value>` writes a single dotted key with CLI-side type coercion (true/false/int/float/JSON/str) and pydantic-side validation.
  - `xtrade config types` lists the available config "types" — for now only `main`.
- Add a `xtrade.core.config` module implementing a JSON-backed pydantic-settings base (`BaseConfig`) with `load` / `save` / `update` / `get`, mirroring the pattern used in `mos.core.baseconfig` but re-implemented locally (no import of mos).
- Add a single top-level config model `xtrade.core.config.Config` with one nested section `PostgresConfig` (host, port, user, password, database). No broker / data-source / risk fields in this change — those land with their own capability changes.
- Config file lives at `~/.xtrade/config.json` by default; path is overridable per-process via the `XTRADE_CONFIG` environment variable (env var takes precedence over the default).
- Environment variables override file values using the `XTRADE_` prefix and `__` as the nested delimiter (e.g. `XTRADE_POSTGRES__PORT=5433`).
- Register `xtrade` as a console script via `[project.scripts]` in `pyproject.toml`.
- Add runtime deps: `click`, `pydantic`, `pydantic-settings`.
- Tests covering: defaults on missing file, file round-trip, atomic save, deep-merge update, env var override, per-call path override, dotted-key get, set with type coercion, and CLI invocation smoke tests (`xtrade --help`, `xtrade config list`, `xtrade config set ... get ...`).
- `README.md` quickstart gains a short "CLI & config" section (no full rewrite).

## Capabilities

### New Capabilities

- `xtrade-cli`: console entry point (`xtrade`) and the `xtrade config` subcommand group (`list`, `get`, `set`, `types`).
- `xtrade-config`: JSON-backed application config model (single `Config` with a `postgres` section), `BaseConfig` generic base, and the `~/.xtrade/config.json` default location with `XTRADE_CONFIG` override.

### Modified Capabilities

None. `project-skeleton` requires no behavioral change; new deps and the console script are additive.

## Impact

- `pyproject.toml`: add deps `click`, `pydantic`, `pydantic-settings`; add `[project.scripts] xtrade = "xtrade.cli.xtrade:cli"`; lockfile regenerated.
- New modules: `src/xtrade/cli/__init__.py`, `src/xtrade/cli/xtrade.py`, `src/xtrade/cli/config.py`, `src/xtrade/core/__init__.py`, `src/xtrade/core/baseconfig.py`, `src/xtrade/core/config.py`, `src/xtrade/core/logging.py` (minimal stub for parity with mos; `setup_logging` is a no-op so the CLI can call it safely).
- New tests: `tests/test_baseconfig.py`, `tests/test_config.py`, `tests/test_cli_config.py`, plus extending `tests/test_smoke.py` with a CLI import smoke test.
- `README.md`: small "CLI & config" section under Quickstart.
- New env var: `XTRADE_CONFIG` (optional, defaults to `~/.xtrade/config.json`).
- No new top-level directory under `~/.xtrade/` is created by this change — the first run only writes the JSON file. Other dirs (logs, cache, plugins) are deferred to their respective capability changes.