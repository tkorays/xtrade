## Context

`xtrade` currently has only the project skeleton (`src/xtrade/{data,strategy,execution,risk}` packages, `tests/test_smoke.py`, no entry points). The first capability that needs runtime parameters is the PostgreSQL connection — without a config + CLI every follow-up change will reinvent them. The reference project `mos` already solved this with a `BaseConfig` JSON-backed settings layer and a `mos config {list,get,set,types}` Click group; we want the same shape but re-implemented locally, not vendored. See [proposal.md](proposal.md) for motivation and [specs/xtrade-cli/spec.md](specs/xtrade-cli/spec.md) / [specs/xtrade-config/spec.md](specs/xtrade-config/spec.md) for the behaviour contract.

## Goals / Non-Goals

**Goals:**
- Standalone `BaseConfig` (pydantic-settings) generic enough that future per-domain configs (broker, data-source, backtest) can subclass it without touching `Config`.
- A single `Config` model exposing exactly one section, `PostgresConfig`.
- A `xtrade` console script + `xtrade config` subcommand group (only `list`, `get`, `set`, `types`).
- File default `~/.xtrade/config.json`, overridable via `XTRADE_CONFIG`; env-var override via `XTRADE_` / `__` with higher precedence than the file.
- Quality-gated: full coverage of `BaseConfig` behaviours via tests, strict typing, no `mos.*` import in either package.

**Non-Goals:**
- No `init`, `plugin`, `mcp`, `task`, `streamlit`, or any other mos subcommand.
- No broker / data-source / risk / backtest config sections — those land in their own changes.
- No PostgreSQL driver install (`psycopg`, `asyncpg`, etc.) — config model only.
- No automatic creation of `~/.xtrade/` subdirectories beyond the JSON file itself.
- No global plugin registry / entry-point loader — `--type` only accepts `main` for now.

## Decisions

### Decision 1: Re-implement `BaseConfig` locally instead of depending on `mos`
- **Choice**: copy the `BaseConfig` pattern (`pydantic_settings.BaseSettings` + `JsonConfigSettingsSource`, `ClassVar[Path] config_file_path`, atomic `save` via tmp+fsync+`os.replace`, deep-merge `update`, dotted `get`) into `xtrade.core.baseconfig`.
- **Why**: the user's explicit instruction is "不要去依赖 mos". Re-using `mos.core.baseconfig` would create a runtime dependency on the `mos-core` package, which is undesirable and could version-drift. The implementation is small (~150 LOC) and well-understood, so the duplication cost is bounded.
- **Alternatives considered**:
  - Vendor the file under `xtrade/_vendor/mos_baseconfig.py` — works but pollutes the namespace with a `_vendor` module; rejected.
  - Extract to a shared internal package later (e.g. `xtrade-foundation`) — premature; rejected for now.

### Decision 2: `pydantic-settings` JSON source via `settings_customise_sources`
- **Choice**: override `settings_customise_sources` on `BaseConfig` to inject a `JsonConfigSettingsSource` whose file path is `cls.config_file_path` (resolved per-call, so subclasses can override the `ClassVar` without baking it into `model_config`).
- **Why**: same pattern as `mos.core.baseconfig`; lets pydantic do the parsing, validation, and env-var merging while we own the file path. `model_config` is captured at class-definition time, so a base `ClassVar` is the only way to give every subclass its own JSON file without subclassing per call site.
- **Alternatives considered**:
  - Manually parse JSON and pass to `__init__` — loses pydantic-settings env merging; rejected.
  - Use `BaseModel` + custom loader — duplicates a lot of pydantic-settings behaviour; rejected.

### Decision 3: Env vars use `XTRADE_` prefix + `__` nested delimiter
- **Choice**: `SettingsConfigDict(env_prefix="XTRADE_", env_nested_delimiter="__", extra="allow")` on `BaseConfig`.
- **Why**: matches the mos convention (`MOS_` / `__`); consistent with the project's existing `XTRADE_` env var names in `.env.example`; pydantic-settings natively understands the `__` delimiter for nested models.
- **Alternatives considered**:
  - Single flat namespace (e.g. `XTRADE_POSTGRES_PORT`) — pydantic-settings doesn't auto-traverse; rejected.

### Decision 4: CLI built on Click, no plugin loader
- **Choice**: `xtrade.cli.xtrade:cli` is a Click group that hard-registers `xtrade.cli.config:config` and nothing else. No `entry_points` discovery.
- **Why**: keeps the CLI surface tiny, makes `xtrade --help` deterministic, and avoids pulling `importlib.metadata` work into the critical path. Plugin support can be added later as its own capability change.
- **Alternatives considered**:
  - Plugin registry mirroring `mos.core.plugin` — adds 200+ LOC for no immediate gain; rejected.

### Decision 5: One `Config` class, one section (`postgres`)
- **Choice**: `Config(BaseConfig)` with `config_file_path: ClassVar[Path] = DEFAULT_CONFIG_PATH` and a single `postgres: PostgresConfig = Field(default_factory=PostgresConfig)` field.
- **Why**: future capability changes will add sections incrementally (`broker`, `data_sources`, `risk`, ...) — that's a normal evolution and easier to review than anticipating them now. `BaseConfig` is the reuse point for them.
- **Alternatives considered**:
  - Pre-declare empty placeholder sections — would need to add and remove fields later, churning the JSON shape; rejected.

### Decision 6: Per-call `Config.load(path=...)` for tests
- **Choice**: `BaseConfig.load(*, path=None)` builds a one-shot subclass with `config_file_path` redirected, so tests can isolate from the real `~/.xtrade/config.json`. Production code uses `Config.load()` with no args.
- **Why**: prevents test bleed; same approach as `mos.core.baseconfig`.
- **Alternatives considered**:
  - `monkeypatch.setattr(Config, "config_file_path", tmp)` in every test — works but adds boilerplate; kept available as an alternative for tests that prefer to mutate the class.

### Decision 7: Minimal `core.logging` stub
- **Choice**: add `xtrade.core.logging` exposing `setup_logging()` (no-op) and `get_logger(name)` (returns stdlib `logging.getLogger(name)`). CLI calls `setup_logging()` from its group callback.
- **Why**: keeps a future loguru-style logging layer swappable without changing the CLI signature; the no-op stub is enough for this change.
- **Alternatives considered**:
  - No logging module at all — would force every later change to introduce it, growing their scope; rejected.
  - Bring in `loguru` now — adds a dep with no consumer yet; rejected.

### Decision 8: `XTRADE_CONFIG` env var wired by overriding `config_file_path` on first load
- **Choice**: introduce `Config.config_file_path` as a `ClassVar` whose default reads `os.environ.get("XTRADE_CONFIG")` lazily through a `@classmethod` that returns the resolved path. Implement via `settings_customise_sources` reading the env var fresh per call (not cached at import).
- **Why**: lets `XTRADE_CONFIG=/tmp/x.json` redirect both reads and writes without restarting the process. Matches mos's `~/.mos/config.json` override behaviour (mos uses `path=` constructor argument, here we read the env directly).
- **Alternatives considered**:
  - Resolve at import time — wrong because env var changes wouldn't take effect; rejected.

### Decision 9: `pyproject.toml` script entry point via `[project.scripts]`
- **Choice**: `[project.scripts]\nxtrade = "xtrade.cli.xtrade:cli"` in `pyproject.toml`. `uv sync` regenerates the console script entry in `.venv/bin/xtrade`.
- **Why**: standard PEP 621 mechanism, picked up by `uv` automatically; works both for `xtrade` invocation and `python -m xtrade.cli.xtrade`.
- **Alternatives considered**:
  - `[tool.poetry.scripts]` / setuptools `entry_points` — we use hatchling; `[project.scripts]` is the modern equivalent; rejected.

## Risks / Trade-offs

- **Risk**: Re-implementing `BaseConfig` instead of depending on `mos` duplicates maintenance. → **Mitigation**: keep `xtrade.core.baseconfig` under ~200 LOC, add tests covering atomic save / deep-merge / env precedence so we can refactor confidently if patterns diverge later.
- **Risk**: `Config.config_file_path` reading `XTRADE_CONFIG` lazily could confuse users who set the env var after import. → **Mitigation**: document in README; tests assert the env var takes effect on `Config.load()`.
- **Risk**: `pydantic-settings` env precedence over file values could mask an explicit JSON choice. → **Mitigation**: surface this in the CLI's `list` output (print the resolved file path) and in README; env-var docs in `.env.example`.
- **Risk**: A single `Config` class with one section means future changes that add a section will modify `Config`. → **Mitigation**: declare the field as `Field(default_factory=...)` so existing user files don't lose new sections; document the additive-only contract in design.
- **Risk**: Click CLI group + plugin loader-free means adding a new subcommand requires a code edit to `xtrade.cli.xtrade`. → **Mitigation**: that's the explicit non-goal for this change; future plugin support can be added as a discrete capability change.

## Migration Plan

This change is purely additive — no existing `xtrade` files change semantics. After apply:
1. `uv sync` resolves new deps (`click`, `pydantic`, `pydantic-settings`).
2. `uv run xtrade --help` lists the `config` subcommand.
3. `uv run xtrade config list` reads defaults when `~/.xtrade/config.json` is absent.
4. `uv run xtrade config set postgres.port 5433 && uv run xtrade config get postgres.port` round-trips.
5. `XTRADE_CONFIG=/tmp/x.json uv run xtrade config list` redirects to the temp file.
6. `uv run pytest` runs all tests including the new ones; `openspec validate --strict` passes.

No rollback complexity: removing the new modules and the `[project.scripts]` entry restores the previous skeleton-only state. `pyproject.toml` dep removals are a separate decision.

## Open Questions

None. All decisions are settled (path layout, dep set, env precedence, CLI surface, `BaseConfig` design, default location). Future capability changes (broker config, data-source config, risk limits, init command, plugin loader) will be addressed by their own dedicated changes.