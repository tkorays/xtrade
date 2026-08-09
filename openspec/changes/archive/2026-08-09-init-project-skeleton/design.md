## Context

The repo is currently empty save for `openspec/` metadata. We need a runnable Python package that future changes (data ingestion, backtest engine, broker adapters, live runner) can extend. The reference layout in `mos_quant/` already separates `data/`, `strategy/`, `trade/`, `tracker/`, plus a CLI/web shell; we will adopt the same module separation but keep `src/` layout (PEP 660 / uv best practice) and avoid bringing in any of its business code in this change. See [proposal.md](proposal.md) for motivation.

## Goals / Non-Goals

**Goals:**
- Provide a minimal, `uv`-managed Python project that installs cleanly and lints/types/tests green out of the box.
- Reserve four subpackages (`data`, `strategy`, `execution`, `risk`) with empty `__init__.py` so future changes have well-defined homes.
- Establish quality gates (`ruff`, `mypy --strict`, `pytest`, `pre-commit`) as CI-ready defaults.

**Non-Goals:**
- No broker integration, no data sources, no strategy implementations, no execution engine, no risk engine code in this change.
- No Streamlit/web UI, no CLI entry points, no Docker, no CI workflow files.
- No third-party trading SDKs (`xtquant`, `tushare`, `akshare`, etc.) are added even as dev deps; later changes will pull them in only when actually needed.

## Decisions

### Decision 1: `src/` layout with `uv`
- **Choice**: `src/xtrade/__init__.py` plus tooling rooted at `pyproject.toml`; `uv` is the canonical installer/lockfile manager.
- **Why**: `src/` layout prevents accidental in-tree imports during testing, which `uv`/`pytest` resolve cleanly. `uv` keeps lockfile and resolver in a single fast tool, matching the team's existing workflow.
- **Alternatives considered**:
  - Flat layout (`xtrade/` at repo root) — simpler, but easy to import wrong copy during testing; rejected.
  - Poetry — slower resolver, no benefit here; rejected.

### Decision 2: Four fixed subpackages for future domains
- **Choice**: `xtrade.data`, `xtrade.strategy`, `xtrade.execution`, `xtrade.risk` (each only `__init__.py` for now).
- **Why**: Mirrors `mos_quant`'s `data / strategy / trade` split and adds an explicit `risk` module so risk checks are a first-class concern. `trade` in `mos_quant` mixes backtest + live; we separate that into `execution` to keep backtest and live broker adapters independent.
- **Alternatives considered**:
  - Single `xtrade.core` — too coarse; will force messy refactors later; rejected.
  - Six subpackages (adding `analytics`, `infra`) — premature; rejected.

### Decision 3: Strict typing + ruff + pre-commit, no CI file
- **Choice**: `mypy --strict` on `src/`, `ruff check` + `ruff format`, `pre-commit-config.yaml` with those hooks; no GitHub Actions / workflow files in this change.
- **Why**: Strict typing is a stated convention in `openspec/config.yaml` `context`. Pre-commit catches issues locally before push. CI is environment-specific (GitHub vs internal Gitea) and deferred to a follow-up change.
- **Alternatives considered**:
  - `mypy --strict` repo-wide — would force stubs for `tests/`; rejected for now, can tighten later.
  - Add a CI workflow — out of scope; deferred.

### Decision 4: `tests/` at repo root, not under `src/`
- **Choice**: `tests/conftest.py` plus `tests/test_smoke.py`; `pyproject.toml` configures `pytest` with `src/xtrade` on `pythonpath` via editable install.
- **Why**: Standard pytest convention; keeps test code clearly separated from shipped code.
- **Alternatives considered**: Co-located `tests/` under each package — overhead, not needed yet; rejected.

### Decision 5: `pyproject.toml` declares `pandas` and `numpy` even though unused yet
- **Choice**: Pin `pandas` and `numpy` in `[project].dependencies` (no version upper bound, just lower bounds).
- **Why**: They are the de facto data primitives for any quant workflow and the team already standardizes on them. Deferring would only add a follow-up change later.
- **Alternatives considered**: Empty `[project].dependencies` — would force every follow-up change to bump the manifest; rejected.

## Risks / Trade-offs

- **Risk**: Adopting `mypy --strict` from day one may slow down later contributors unfamiliar with strict typing. → **Mitigation**: include a `mypy` section in the README quickstart; future overrides for tests / third-party stubs will be added only when needed.
- **Risk**: `src/` layout requires `uv sync` (or `pip install -e .`) before tests can run; contributors who skip install will get `ModuleNotFoundError`. → **Mitigation**: README quickstart makes `uv sync` the first step; smoke test catches missing install in CI.
- **Risk**: Pinning `pandas`/`numpy` with only lower bounds can cause surprise breakage from upstream releases. → **Mitigation**: rely on `uv.lock` to lock the resolved versions; bump intentionally.
- **Risk**: No CI workflow means quality gates rely on contributors running them locally. → **Mitigation**: `pre-commit` runs hooks on commit; a follow-up change will add CI.

## Migration Plan

This is a greenfield initialization, so there is nothing to migrate. After apply:
1. `uv sync` resolves and installs all declared dependencies.
2. `uv run pre-commit install` enables the git hooks.
3. `uv run pytest`, `uv run ruff check .`, and `uv run mypy src` all pass.
4. Subsequent changes add concrete code under the four subpackages.

## Open Questions

None. All material decisions (layout, modules, tooling set, dep list) are settled in this design; any future addition (CLI, web UI, CI provider, broker SDKs) will be addressed by its own change.