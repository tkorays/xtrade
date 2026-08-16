## ADDED Requirements

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
| `default_dashboard_uid` | `str` | `""` | Optional default dashboard UID for SDK helpers |
| `timeout` | `float` | `10.0` | HTTP request timeout in seconds |
| `verify_ssl` | `bool` | `True` | TLS verification for `https` |

The new section SHALL be additive: existing user config files without a `grafana` block SHALL load with the documented defaults. `GrafanaConfig.base_url` SHALL return `f"{scheme}://{host}:{port}{path_prefix.rstrip('/')}"`.

#### Scenario: Defaults when no file present

- **WHEN** a developer loads `Config` and no `config.json` exists
- **THEN** `cfg.grafana.host == "localhost"`, `cfg.grafana.port == 3000`, `cfg.grafana.scheme == "http"`, `cfg.grafana.api_key == ""`, `cfg.grafana.password == ""`, `cfg.grafana.org_slug == "main"`, `cfg.grafana.timeout == 10.0`, `cfg.grafana.verify_ssl is True`, and `cfg.grafana.base_url == "http://localhost:3000"`

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