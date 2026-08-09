## ADDED Requirements

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