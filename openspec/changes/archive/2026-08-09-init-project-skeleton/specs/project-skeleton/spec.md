## Purpose

Establishes the foundational Python package layout, build/quality tooling, and test harness that every later backtest and live-trading capability of the `xtrade` quantitative trading system will build on.

## ADDED Requirements

### Requirement: Python package layout

The repository SHALL contain a `src/xtrade/` package with an `__init__.py` and four subpackages: `xtrade.data`, `xtrade.strategy`, `xtrade.execution`, `xtrade.risk`. Each subpackage SHALL contain an `__init__.py`.

#### Scenario: Package import succeeds
- **WHEN** a developer installs the project and runs `python -c "import xtrade"`
- **THEN** the import resolves without error and the four submodules (`xtrade.data`, `xtrade.strategy`, `xtrade.execution`, `xtrade.risk`) are importable

### Requirement: Dependency management via uv

The repository SHALL provide a `pyproject.toml` declaring the project metadata and dependencies, and SHALL be installable with `uv sync`. The lockfile `uv.lock` SHALL be committed.

#### Scenario: Fresh install
- **WHEN** a developer runs `uv sync` in a clean checkout
- **THEN** a virtual environment is created at `.venv` and all declared dependencies are installed

### Requirement: Quality gates configured

The repository SHALL configure `ruff` (lint + format), `mypy --strict` for the `src/` tree, and `pytest` as the default test runner via `pyproject.toml`. A `.pre-commit-config.yaml` SHALL run these tools on every commit.

#### Scenario: Lint and type-check pass
- **WHEN** a developer runs `uv run ruff check src tests` and `uv run mypy src`
- **THEN** both commands exit zero on a freshly scaffolded tree

### Requirement: Test harness in place

The repository SHALL contain a `tests/` directory with `conftest.py` and at least one passing smoke test that imports the `xtrade` package.

#### Scenario: Smoke test runs
- **WHEN** a developer runs `uv run pytest`
- **THEN** the test suite discovers and passes with at least one green test

### Requirement: Repository hygiene files

The repository SHALL include a `.gitignore` covering Python, `uv`, IDE, and OS artifacts, and a `README.md` introducing the project with a quickstart section.

#### Scenario: Ignored artifacts are not tracked
- **WHEN** a developer inspects `git status` after generating `.venv/` and `__pycache__/`
- **THEN** those paths are not reported as untracked

### Requirement: No hardcoded secrets

The repository SHALL NOT contain any hardcoded API keys, account IDs, broker credentials, or live-trading endpoints. Configuration for live-trading MUST be loaded from environment variables or a gitignored config file.

#### Scenario: Secret scan finds nothing
- **WHEN** a developer greps the repo for patterns resembling API keys (e.g. `AK[A-Z0-9]{16,}`, `sk-...`, `xtrade_live_*`)
- **THEN** no matches are returned outside of `.env.example`