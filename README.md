# xtrade

A quantitative trading system written in Python. Supports both historical
backtesting and live (paper / small-size real) trading. This repository
currently only contains the project skeleton — concrete data sources,
strategies, and execution adapters will be added in follow-up changes.

## Layout

```
xtrade/
├── openspec/                  # OpenSpec specs and active changes
├── src/xtrade/                # Package source (src layout)
│   ├── data/                  # Market data ingestion, storage, feed
│   ├── strategy/              # Signal generation, alpha models
│   ├── execution/             # Backtest + live broker adapters
│   └── risk/                  # Pre-trade risk checks and limits
├── tests/                     # pytest test suite
├── pyproject.toml             # Project metadata + tooling config
├── uv.lock                    # Pinned dependency lockfile
├── .pre-commit-config.yaml    # Local git hooks (ruff, mypy)
├── .env.example               # Required env var placeholders
└── README.md
```

## Quickstart

Requirements: Python 3.13+, [uv](https://docs.astral.sh/uv/).

```bash
# 1. Install dependencies and create the venv
uv sync

# 2. Install pre-commit hooks
uv run pre-commit install

# 3. Run the test suite
uv run pytest

# 4. Lint and type-check
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

## Trading rules (project conventions)

- Always paper-trade a new strategy before going live.
- Live trading starts at small size; scale up only after metrics hold up
  on out-of-sample data.
- All broker credentials and live endpoints come from environment
  variables (see `.env.example`). Nothing is ever hardcoded.
- Risk limits are enforced in `xtrade.risk` and are a hard prerequisite
  for any live execution path.

## Status

Skeleton only. See `openspec/changes/` for in-flight changes and
`openspec/specs/` for the canonical capability requirements.
