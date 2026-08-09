## 1. Repo hygiene files

- [x] 1.1 Create `.gitignore` covering Python (`__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`), `uv` (`.venv/`), IDE (`.idea/`, `.vscode/`), OS (`.DS_Store`, `Thumbs.db`), and env files (`.env`, `.env.*` but allow `.env.example`).
- [x] 1.2 Create root `README.md` with project intro, layout diagram, and quickstart (`uv sync` → `uv run pre-commit install` → `uv run pytest`).

## 2. Project manifest and lockfile

- [x] 2.1 Create `pyproject.toml` with `[project]` metadata (name `xtrade`, Python `>=3.13`), runtime deps `pandas` and `numpy`, dev deps `pytest`, `ruff`, `mypy`, `pre-commit`; configure `[tool.ruff]`, `[tool.mypy]` (`strict = true`, target `src/`), `[tool.pytest.ini_options]` (testpaths = `tests`), and `[tool.uv]`.
- [x] 2.2 Run `uv sync` to generate `uv.lock` and `.venv/`.

## 3. Package skeleton

- [x] 3.1 Create `src/xtrade/__init__.py` exporting `__version__ = "0.0.0"`.
- [x] 3.2 Create `src/xtrade/data/__init__.py` (empty package marker only).
- [x] 3.3 Create `src/xtrade/strategy/__init__.py` (empty package marker only).
- [x] 3.4 Create `src/xtrade/execution/__init__.py` (empty package marker only).
- [x] 3.5 Create `src/xtrade/risk/__init__.py` (empty package marker only).

## 4. Test harness

- [x] 4.1 Create `tests/__init__.py` (empty) and `tests/conftest.py` (empty, reserved for shared fixtures later).
- [x] 4.2 Create `tests/test_smoke.py` containing a single test that asserts `import xtrade` succeeds and `xtrade.__version__` is a non-empty string.
- [x] 4.3 Verify `uv run pytest` passes.

## 5. Quality gates

- [x] 5.1 Create `.pre-commit-config.yaml` with hooks: `ruff check --fix`, `ruff format`, `mypy src`, and a trailing-whitespace/EOL fixer.
- [x] 5.2 Verify `uv run ruff check src tests` and `uv run ruff format --check src tests` pass.
- [x] 5.3 Verify `uv run mypy src` passes against the strict config.
- [x] 5.4 Create `.env.example` documenting required env vars (placeholder values only — no real secrets).

## 6. Final validation

- [x] 6.1 From a clean checkout, run `uv sync && uv run pre-commit run --all-files && uv run pytest` and confirm all green.
- [x] 6.2 Confirm `openspec validate --change init-project-skeleton --strict` passes.