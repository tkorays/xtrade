## 1. Dependencies and manifest

- [x] 1.1 Add runtime deps to `pyproject.toml`: `click`, `pydantic`, `pydantic-settings`.
- [x] 1.2 Add `[project.scripts] xtrade = "xtrade.cli.xtrade:cli"` to `pyproject.toml`.
- [x] 1.3 Run `uv lock` and `uv sync` to regenerate `uv.lock` / `.venv`.
- [x] 1.4 Update `.env.example` with new env vars: `XTRADE_CONFIG` (optional override) and a sample `XTRADE_POSTGRES__HOST` / `XTRADE_POSTGRES__PORT` block (commented placeholders only).

## 2. Core config layer

- [x] 2.1 Create `src/xtrade/core/__init__.py` (empty package marker).
- [x] 2.2 Create `src/xtrade/core/logging.py` with `setup_logging()` (no-op) and `get_logger(name)` returning stdlib `logging.getLogger(name)`. Include a docstring explaining this is a stub.
- [x] 2.3 Create `src/xtrade/core/baseconfig.py` implementing `BaseConfig`:
  - `config_file_path: ClassVar[Path]` (abstract — declared with no default; subclasses must override).
  - `model_config = SettingsConfigDict(env_prefix="XTRADE_", env_nested_delimiter="__", extra="allow", json_file_encoding="utf-8")`.
  - `settings_customise_sources` returning init / env / dotenv / file-secrets + `JsonConfigSettingsSource(settings_cls, json_file=str(cls.config_file_path))`.
  - `load(cls, *, path=None)` — defaults to `cls()`, redirects via dynamic subclass when `path` is given.
  - `save(self, path=None)` — atomic write via `.tmp` + `fsync` + `os.replace`; cleans up tmp on failure.
  - `update(self, **kwargs)` — deep-merge nested dicts; return new validated instance.
  - `get(self, *keys, default=None)` — nested traversal returning default on missing path.
  - Module-private `_deep_merge(base, override)` helper.
- [x] 2.4 Create `src/xtrade/core/config.py` with:
  - Constants `DEFAULT_XTRADE_HOME = Path.home() / ".xtrade"` and `DEFAULT_CONFIG_PATH = DEFAULT_XTRADE_HOME / "config.json"`.
  - `PostgresConfig(BaseModel)` with `host` / `port` / `user` / `password` / `database` fields and the documented defaults.
  - `Config(BaseConfig)` with `config_file_path: ClassVar[Path] = _resolve_config_path()` (private helper that reads `XTRADE_CONFIG` env var with fallback to `DEFAULT_CONFIG_PATH`).
  - Module-private `_config: Config | None` and `get_config(reload: bool = False) -> Config`.

## 3. CLI

- [x] 3.1 Create `src/xtrade/cli/__init__.py` (empty package marker).
- [x] 3.2 Create `src/xtrade/cli/xtrade.py` exposing `cli: click.Group` with a `--version` option (from `xtrade.__version__` or `importlib.metadata.version("xtrade")`) and `cli.add_command(config)` from `xtrade.cli.config`. The group's callback calls `setup_logging()`.
- [x] 3.3 Create `src/xtrade/cli/config.py` with:
  - `config: click.Group` named `config` with docstring "配置管理命令".
  - `_print_config_tree(data, prefix="")` recursive pretty-printer.
  - `_coerce_cli_value(value: str) -> Any` handling `~`-prefixed strings, `true`/`false`, int / float literals, JSON arrays / objects, otherwise string.
  - `list`: Click command reading from the main config; prints file path, existence, full tree, and a hint.
  - `get <key>`: Click command returning the dotted-key value or a "key 不存在" message.
  - `set <key> <value>`: Click command coercing the value, calling `current.update(...)`, saving, then reloading the global config.
  - `types`: Click command listing `main` as the only available type for now.
- [x] 3.4 Verify `uv run xtrade --help` exits 0 and lists `config`. Verify `uv run python -m xtrade.cli.xtrade --help` does the same.

## 4. Tests

- [x] 4.1 Add `tests/test_baseconfig.py` covering: defaults on missing file, JSON file round-trip, atomic save (no `.tmp` on success), atomic save (original untouched on failure), deep-merge update preserving nested fields, type coercion via `update`, dotted `get`, `load(path=...)` override, `load(path=...)` not polluting base class, string path expansion.
- [x] 4.2 Add `tests/test_config.py` covering: defaults for `Config.postgres`, save+reload round-trip preserves `postgres` fields, `XTRADE_CONFIG` env var redirects both reads and writes, `XTRADE_POSTGRES__PORT` overrides file value, corrupt JSON raises `JSONDecodeError`, default constants resolve under `Path.home()`, no `mos.*` import appears in `sys.modules` after importing `xtrade.core.config`.
- [x] 4.3 Add `tests/test_cli_config.py` covering: `xtrade --help` exits 0; `xtrade config list` exits 0 and prints `postgres.host: localhost`; `xtrade config get postgres.host` prints `postgres.host = localhost`; `xtrade config set postgres.port 5433` then `xtrade config get postgres.port` reports `5433`; `xtrade config set postgres.port not-an-int` exits non-zero and the file is unchanged; `xtrade config list --type nope` exits non-zero.
- [x] 4.4 Extend `tests/test_smoke.py` with a test asserting the `xtrade.cli.xtrade:cli` Click object is importable and exposes `config` as a subcommand.

## 5. Docs

- [x] 5.1 Add a "CLI & config" subsection under the Quickstart in `README.md`:
  - `uv run xtrade --help` example.
  - `uv run xtrade config list`, `get postgres.port`, `set postgres.port 5433` examples.
  - One-line note that the file lives at `~/.xtrade/config.json` and can be redirected with `XTRADE_CONFIG`.

## 6. Final validation

- [x] 6.1 `uv run pytest` passes with all new tests green.
- [x] 6.2 `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src` pass.
- [x] 6.3 `uv run xtrade --help` and `uv run xtrade config list` both exit 0.
- [x] 6.4 `openspec validate add-cli-and-config --type change --strict` passes.