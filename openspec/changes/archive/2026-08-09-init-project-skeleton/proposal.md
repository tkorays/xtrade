## Why

The `xtrade` repo currently contains only an `openspec/` directory with project metadata. Before any quantitative trading capability (backtest + live) can be built, we need a runnable Python package skeleton: a `pyproject.toml`, a `src/` layout, separated module folders for data / strategy / execution / risk, a test harness, and quality tooling (ruff / mypy / pre-commit). This change lays that foundation; subsequent changes will fill in concrete behavior.

## What Changes

- Add `pyproject.toml` managed by `uv`, declaring Python 3.13+, runtime deps (`pandas`, `numpy`), dev deps (`pytest`, `ruff`, `mypy`, `pre-commit`).
- Add `src/xtrade/` package with empty `__init__.py` and the four module subpackages (no business code yet):
  - `xtrade.data/`
  - `xtrade.strategy/`
  - `xtrade.execution/`
  - `xtrade.risk/`
- Add `tests/` directory with `conftest.py` and one smoke test that imports `xtrade`.
- Add root-level files: `README.md` (project intro + quickstart), `.gitignore`, `.pre-commit-config.yaml`, `pyproject.toml`, `uv.lock` (generated).
- Configure `ruff` (lint + format), `mypy --strict`, `pytest` defaults via `pyproject.toml`.
- Add `examples/` folder with a placeholder notebook-free readme pointer.

## Capabilities

### New Capabilities

- `project-skeleton`: Bootstraps the `xtrade` Python package with the layout, tooling, and quality gates required for future backtest and live-trading capability work.

### Modified Capabilities

_None._

## Impact

- Repo root: new top-level files (`pyproject.toml`, `README.md`, `.gitignore`, `.pre-commit-config.yaml`, `uv.lock`).
- New source tree: `src/xtrade/` and the four subpackages above.
- New test tree: `tests/`.
- No runtime behavior is added in this change; it is purely structural.
- Tooling install path: `uv sync` → `uv run pytest` → `uv run ruff check .` → `uv run mypy src`.