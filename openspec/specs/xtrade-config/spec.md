# Capability: xtrade-config

## Purpose

Provides a JSON-backed application configuration model for `xtrade`, including a generic pydantic-settings base, a `Config` containing the `postgres` and `data` sections, and the file-location contract (`~/.xtrade/config.json`, overridable via `XTRADE_CONFIG`).
## Requirements
### Requirement: Generic `BaseConfig` settings base

The configuration package SHALL provide a `BaseConfig` subclass of `pydantic_settings.BaseSettings` that exposes four class/instance operations: `load`, `save`, `update`, and `get`. Subclasses SHALL declare their JSON file location via a `ClassVar[Path]` named `config_file_path` and SHALL inherit the four operations without re-implementing them.

#### Scenario: Subclass reads its own file

- **WHEN** a developer subclasses `BaseConfig`, points `config_file_path` at a temp JSON file containing overrides, and calls `cls.load()`
- **THEN** the returned instance reflects the overrides for fields it declares and defaults for fields it does not

#### Scenario: Subclass save round-trips

- **WHEN** a developer loads a `BaseConfig` subclass, mutates a field, and calls `.save()`
- **THEN** the JSON file at `config_file_path` is rewritten, contains the mutated value, and a subsequent `cls.load()` reads the new value back

### Requirement: Atomic file save

`BaseConfig.save` SHALL write the JSON to a sibling `.tmp` file, flush and fsync it, then atomically rename it over the target. On failure the temp file SHALL be removed and the existing target file SHALL be left untouched.

#### Scenario: Successful save leaves no temp file

- **WHEN** a developer calls `BaseConfig.save()` on a successful path
- **THEN** the target JSON exists and no `<target>.tmp` file remains

#### Scenario: Failed save preserves prior content

- **WHEN** a developer calls `BaseConfig.save()` and the temp-file write raises mid-write
- **THEN** the original target JSON is unchanged on disk and no `<target>.tmp` remains

### Requirement: Deep-merge `update` semantics

`BaseConfig.update(**overrides)` SHALL return a new instance where nested dict overrides are recursively merged with the current values; non-dict collisions overwrite. The original instance SHALL remain unchanged. Values supplied via `update` SHALL be coerced by pydantic at validation time (e.g. `"true"` → `True`, `"5433"` → `5433`).

#### Scenario: Update merges nested dicts

- **WHEN** a developer calls `base.update(postgres={"port": 6543})` on a config whose `postgres.host` was previously customized
- **THEN** the returned instance has the new port AND the customized host AND unchanged fields on other sections

#### Scenario: Update coerces string values

- **WHEN** a developer calls `base.update(debug="true")`
- **THEN** the returned instance has `debug is True` (a `bool`, not the string `"true"`)

### Requirement: `get(*keys)` nested accessor

`BaseConfig.get(*keys, default=None)` SHALL traverse the dumped config by key sequence and return the resolved value or the supplied default. A non-dict encountered mid-traversal SHALL return the default.

#### Scenario: Get returns nested value

- **WHEN** a developer calls `cfg.get("postgres", "port")`
- **THEN** it returns the `postgres.port` value as an `int`

#### Scenario: Get returns default on missing path

- **WHEN** a developer calls `cfg.get("postgres", "missing", default="x")`
- **THEN** it returns the string `"x"`

### Requirement: `Config` model with a `postgres` section

The configuration package SHALL expose a `Config` (subclass of `BaseConfig`) declaring exactly one nested section, `PostgresConfig`, with fields `host` (default `"localhost"`), `port` (default `5432`), `user` (default `"postgres"`), `password` (default `""`), and `database` (default `"xtrade"`).

#### Scenario: Defaults when no file present

- **WHEN** a developer loads `Config` and no `config.json` exists at the resolved path
- **THEN** `cfg.postgres.host == "localhost"`, `cfg.postgres.port == 5432`, `cfg.postgres.user == "postgres"`, `cfg.postgres.password == ""`, `cfg.postgres.database == "xtrade"`

#### Scenario: Postgres section survives round-trip

- **WHEN** a developer sets `cfg.postgres.port = 6543`, calls `cfg.save()`, and reloads `Config`
- **THEN** the reloaded `postgres.port` is `6543` and the other `postgres` fields keep their previous values

### Requirement: Default config path under `~/.xtrade/`

The configuration package SHALL resolve its default config path to `~/.xtrade/config.json` (`Path.home() / ".xtrade" / "config.json"`). The default home and path SHALL be exposed as module-level constants.

#### Scenario: Constants resolve under the user home

- **WHEN** a developer imports the configuration module and inspects the constants
- **THEN** `DEFAULT_XTRADE_HOME` is absolute and equal to `Path.home() / ".xtrade"`, and `DEFAULT_CONFIG_PATH` equals `DEFAULT_XTRADE_HOME / "config.json"`

### Requirement: `XTRADE_CONFIG` env var overrides default path

When the `XTRADE_CONFIG` environment variable is set, `Config` SHALL read and write JSON at that path instead of the default. When unset, the default `~/.xtrade/config.json` SHALL apply.

#### Scenario: Env var redirects reads

- **WHEN** `XTRADE_CONFIG=/tmp/x.json` is set and `/tmp/x.json` contains a custom `postgres.port`
- **THEN** `Config.load()` returns a config whose `postgres.port` matches the file

#### Scenario: Env var redirects saves

- **WHEN** `XTRADE_CONFIG=/tmp/x.json` is set and a developer calls `Config.save()`
- **THEN** the JSON is written to `/tmp/x.json` and the default path is untouched

### Requirement: `XTRADE_`-prefixed env vars override file values

The configuration package SHALL honour environment variables prefixed with `XTRADE_` and using `__` as the nested delimiter, taking precedence over the JSON file but still subject to pydantic validation.

#### Scenario: Env var overrides file value

- **WHEN** a JSON file sets `postgres.port = 5432` and `XTRADE_POSTGRES__PORT=6543` is set in the environment
- **THEN** `Config.load()` returns a config with `postgres.port == 6543`

### Requirement: `Config` does not import any `mos.*` module

The configuration package SHALL NOT import from `mos.*`. Implementations SHALL depend only on the project's own modules, pydantic, and pydantic-settings.

#### Scenario: mos is not a runtime dependency

- **WHEN** a developer inspects `pyproject.toml`'s `dependencies`
- **THEN** no `mos*` package is listed

### Requirement: Corrupt JSON raises a clear error

`Config.load()` SHALL raise `json.JSONDecodeError` (propagated from pydantic-settings) when the config file exists but contains invalid JSON. The CLI SHALL translate this into a non-zero exit code with a user-friendly message.

#### Scenario: Corrupt file is reported

- **WHEN** a developer writes `{ not json` to the config file and runs `xtrade config list`
- **THEN** the command exits non-zero and stdout / stderr names the JSON parse failure

### Requirement: `Config` model with a `data` section

The configuration package SHALL expose a `DataConfig` pydantic model on `Config.data` with two fields: `database: DataDatabaseConfig` (containing a single `url: str` field, default `postgresql+psycopg://postgres:postgres@localhost:5432/xtrade`) and `batch_size: int` (default `10_000`). The new section SHALL be additive: existing user config files without a `data` block SHALL load with the documented defaults.

#### Scenario: Defaults when no file present

- **WHEN** a developer loads `Config` and no `config.json` exists
- **THEN** `cfg.data.database.url == "postgresql+psycopg://postgres:postgres@localhost:5432/xtrade"` and `cfg.data.batch_size == 10_000`

#### Scenario: Existing user file without `data` block still loads

- **WHEN** a developer has a pre-existing `~/.xtrade/config.json` containing only `{"postgres": {...}}`
- **THEN** `Config.load()` returns a config whose `data` section has the documented defaults, and the `postgres` section is unchanged

#### Scenario: User-supplied `data` block round-trips

- **WHEN** a developer sets `cfg.data.database.url = "postgresql+psycopg://u:p@h:5432/d"`, calls `cfg.save()`, and reloads
- **THEN** the reloaded `cfg.data.database.url` matches the saved value, and `cfg.data.batch_size` keeps its previous value (or default if unset)

### Requirement: `XTRADE_DATA__*` env vars override file values

The configuration package SHALL honour environment variables prefixed with `XTRADE_DATA__` taking precedence over the JSON file but still subject to pydantic validation.

#### Scenario: Env var overrides file value

- **WHEN** a JSON file sets `data.batch_size = 5000` and `XTRADE_DATA__BATCH_SIZE=20000` is set in the environment
- **THEN** `Config.load()` returns a config with `data.batch_size == 20000`

#### Scenario: Nested env override works for `database.url`

- **WHEN** `XTRADE_DATA__DATABASE__URL=postgresql+psycopg://other:5432/d` is set
- **THEN** `cfg.data.database.url` reflects that value

### Requirement: DSN carries a SQLAlchemy driver prefix

The DSN in `data.database.url` SHALL include a SQLAlchemy driver segment (`postgresql+psycopg` for psycopg v3) so that the data layer can construct the correct dialect. Bare `postgresql://` URIs SHALL be accepted but the data layer SHALL normalise to `postgresql+psycopg`.

#### Scenario: Bare DSN is normalised

- **WHEN** a developer writes `data.database.url = "postgresql://u@h:5432/d"`
- **THEN** `data.engine.create_engine(cfg)` returns an engine whose dialect is `postgresql+psycopg`

#### Scenario: Explicit driver prefix is preserved

- **WHEN** a developer writes `data.database.url = "postgresql+psycopg://u@h:5432/d"`
- **THEN** the engine uses that exact dialect

### Requirement: `Config` model with a `grafana` section

The configuration package SHALL expose a `GrafanaConfig` pydantic model on `Config.grafana` with the following fields and defaults:

| Field | Type | Default | Notes |
|---|---|---|---|
| `host` | `str` | `"localhost"` | Grafana host |
| `port` | `int` | `3000` | Grafana HTTP port |
| `scheme` | `str` | `"http"` | `"http"` or `"https"` |
| `path_prefix` | `str` | `""` | Optional sub-path (e.g. `"/grafana"`) |
| `user` | `str` | `"admin"` | Basic-auth user |
| `password` | `str` | `""` | Basic-auth password (no hardcoded default) |
| `api_key` | `str` | `""` | Grafana Service Account token; when non-empty it overrides basic auth in the HTTP layer |
| `org_slug` | `str` | `"main"` | Default Grafana org slug |
| `namespace` | `str` | `"default"` | Grafana unified-API namespace (overridable via `XTRADE_GRAFANA__NAMESPACE`) |
| `default_dashboard_uid` | `str` | `""` | Optional default dashboard UID for SDK helpers |
| `timeout` | `float` | `10.0` | HTTP request timeout in seconds |
| `verify_ssl` | `bool` | `True` | TLS verification for `https` |

The new section SHALL be additive: existing user config files without a `grafana` block SHALL load with the documented defaults. `GrafanaConfig.base_url` SHALL return `f"{scheme}://{host}:{port}{path_prefix.rstrip('/')}"`.

#### Scenario: Defaults when no file present

- **WHEN** a developer loads `Config` and no `config.json` exists
- **THEN** `cfg.grafana.host == "localhost"`, `cfg.grafana.port == 3000`, `cfg.grafana.scheme == "http"`, `cfg.grafana.api_key == ""`, `cfg.grafana.password == ""`, `cfg.grafana.org_slug == "main"`, `cfg.grafana.namespace == "default"`, `cfg.grafana.timeout == 10.0`, `cfg.grafana.verify_ssl is True`, and `cfg.grafana.base_url == "http://localhost:3000"`

#### Scenario: Existing user file without `grafana` block still loads

- **WHEN** a developer has a pre-existing `~/.xtrade/config.json` containing only `{"postgres": {...}}`
- **THEN** `Config.load()` returns a config whose `grafana` section has the documented defaults, and the `postgres` section is unchanged

#### Scenario: User-supplied `grafana` block round-trips

- **WHEN** a developer sets `cfg.grafana.host = "grafana.internal"`, `cfg.grafana.api_key = "glsa_xxx"`, calls `cfg.save()`, and reloads
- **THEN** the reloaded `cfg.grafana.host == "grafana.internal"` and `cfg.grafana.api_key == "glsa_xxx"`, and untouched fields keep their previous (default) values

#### Scenario: `base_url` strips trailing slashes

- **WHEN** `path_prefix = "/grafana/"`
- **THEN** `cfg.grafana.base_url == "http://localhost:3000/grafana"` (no double slash)

### Requirement: `XTRADE_GRAFANA__*` env vars override file values

The configuration package SHALL honour environment variables prefixed with `XTRADE_GRAFANA__` taking precedence over the JSON file but still subject to pydantic validation.

#### Scenario: Env var overrides file value

- **WHEN** a JSON file sets `grafana.host = "file-host"` and `XTRADE_GRAFANA__HOST=env-host` is set in the environment
- **THEN** `Config.load()` returns a config with `grafana.host == "env-host"`

#### Scenario: Env var sets the API key

- **WHEN** `XTRADE_GRAFANA__API_KEY=glsa_xxx` is set
- **THEN** `cfg.grafana.api_key == "glsa_xxx"` regardless of any value in the JSON file

#### Scenario: Env var numeric coercion

- **WHEN** `XTRADE_GRAFANA__PORT=9090` is set
- **THEN** `cfg.grafana.port == 9090` (an `int`, not the string `"9090"`)

