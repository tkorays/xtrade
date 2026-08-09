## 1. Build `xtrade.strategy.base` (Protocol + Bar / Signal / Context)

- [x] 1.1 Create `src/xtrade/strategy/base.py` with `Bar` (frozen dataclass), `Signal` (frozen dataclass), `Context` (frozen dataclass), and `Strategy` Protocol (`@runtime_checkable`, `on_init` + `on_bar`). Re-export `OrderSide`, `OrderType` from `xtrade.execution.broker` and `Account`, `Position` from `xtrade.data.broker_data`.
- [x] 1.2 Update `src/xtrade/strategy/__init__.py` to expose `Strategy`, `Bar`, `Signal`, `Context` via `__all__`.
- [x] 1.3 Add `tests/strategy/__init__.py` and `tests/strategy/test_base.py` covering: `isinstance` positive / negative cases, `Bar` hashability, `Signal` → `OrderRequest` translation via a tiny helper in `strategy.base`, `Context` immutability, `params` plumb-through.

## 2. Build `xtrade.risk` (Protocol + checks + combiner)

- [x] 2.1 Create `src/xtrade/risk/base.py` with `RiskCheck` Protocol (`@runtime_checkable`, `check(intent, ctx) -> None`), `RiskViolationError` (subclass of `RuntimeError`, fields `rule_name` and `message`), `OrderIntent` (frozen dataclass), and `RiskContext` (frozen dataclass with `account`, `positions`, `now`; no `broker` attribute).
- [x] 2.2 Create `src/xtrade/risk/checks.py` with `OrderSizeLimit(max_notional)`, `PositionLimit(max_qty)`, `KillSwitch(trigger_on_daily_loss)`, and `CompositeRiskCheck(checks)`. `KillSwitch` exposes `engaged: bool` and `reset()`.
- [x] 2.3 Update `src/xtrade/risk/__init__.py` to expose the public API.
- [x] 2.4 Add `tests/risk/__init__.py` and `tests/risk/test_checks.py` covering: each rule's accept / reject path, `CompositeRiskCheck` short-circuit ordering, `KillSwitch.engaged` / `reset()` lifecycle, `RiskViolationError.rule_name` contents, `RiskContext` lacks `broker`.

## 3. Build `xtrade.engine` (Backtest + Live + CLI)

- [x] 3.1 Create `src/xtrade/engine/clock.py` with `EngineUsageError` (raised when `on_init` calls `broker.submit_order`) and `RunSummary` (frozen dataclass with `initial_account`, `final_account`, `n_orders`, `n_fills`, `n_dropped_signals`, `start`, `end`).
- [x] 3.2 Create `src/xtrade/engine/backtest.py` with `BacktestEngine(strategy, broker, risk, market_data)` and `run(start, end) -> RunSummary`. The pipeline SHALL be: per-bar `bar → strategy.on_bar(bar, ctx) → for each signal: build OrderIntent, risk.check, on pass -> broker.submit_order, on violation -> drop log; broker.advance(bar.time, prices)`. Deterministic symbol ordering when timestamps collide.
- [x] 3.3 Create `src/xtrade/engine/live.py` with `LiveEngine(strategy, broker, risk, source=MockSource())` exposing `start()` and `stop()`. Use `asyncio` event loop; pump MockSource's `next_price()`; propagate exceptions from strategy / broker.
- [x] 3.4 Create `src/xtrade/engine/runner.py` with `load_strategy(spec: str, params: Mapping[str, Any]) -> Strategy` (parses `module:Class`, imports, instantiates if a non-default `__init__` is detected, otherwise returns the class instance). Helper `_build_run_market_data(broker_kind, symbols, start, end)` builds an in-memory `market_data` callable for tests / live.
- [x] 3.5 Create `src/xtrade/engine/__init__.py` exporting `BacktestEngine`, `LiveEngine`, `RunSummary`, `EngineUsageError`.
- [x] 3.6 Add `tests/engine/__init__.py` and `tests/engine/test_backtest.py` covering: deterministic two-symbol ordering, signal-blocked-then-dropped-and-counted, summary counts, `on_init` abuse raises `EngineUsageError`, single-symbol happy-path producing a `RunSummary`. Add `tests/engine/test_live.py` covering: MockSource-driven loop, `stop()` joins the task, an exception in a step propagates.

## 4. CLI: `xtrade backtest run`

- [x] 4.1 Create `src/xtrade/cli/backtest.py` with Click group `backtest` and subcommand `run`. Accept `--strategy`, `--start`, `--end`, `--symbols`, `--broker` (default `in-memory`), `--initial-cash` (default `1_000_000`), `--config`, `--strategy-params`. On `--strategy`, use `xtrade.engine.runner.load_strategy`. Default `--broker in-memory` constructs `InMemoryBroker`. Print the `RunSummary` JSON to stdout on success; exit non-zero with a clear message on load failures.
- [x] 4.2 Register `backtest` on the top-level `cli` group in `src/xtrade/cli/xtrade.py`.
- [x] 4.3 Add `tests/cli/test_backtest_cli.py` using `click.testing.CliRunner`: a happy-path that registers a fake strategy module in `sys.modules` and asserts a `RunSummary` JSON appears in `result.stdout`; a failure path that points at a non-existent module and asserts `result.exit_code != 0`.

## 5. Quality gates

- [x] 5.1 `uv run pytest` — all new tests pass; existing tests remain green.
- [x] 5.2 `uv run ruff check src tests && uv run ruff format --check src tests` — clean.
- [x] 5.3 `uv run mypy src` — strict, no errors.
- [x] 5.4 `openspec validate --all --strict` — all specs pass.
