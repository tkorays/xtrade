# Capability: engine

## Purpose

`xtrade.engine` provides the application-layer driver that glues together market data, strategies, risk checks, and broker execution. It exposes a `BacktestEngine` (deterministic, explicit-step) and a `LiveEngine` (event-driven, MockSource-backed) that share the same observable per-step semantics. The engine is the sole owner of the system clock during a run; both engines present a uniform `advance(time, prices)` semantics against a `Broker` Protocol.

## Requirements

### Requirement: Engines own the per-step pipeline

The engine SHALL own the per-step pipeline `bar → strategy → risk → broker`. For each step, the engine SHALL:

1. Read the next bar from the configured source.
2. Call `Strategy.on_bar(bar, ctx)` with a fresh `Context` reflecting the current `now`, `bar`, `account`, `positions`, and bound `broker` / `risk` references.
3. Translate every returned `Signal` into an `OrderRequest` and run it through `RiskCheck.check(intent, ctx)` before `broker.submit_order(req)`.
4. Call `broker.advance(now, prices)` to advance time and settle fills.

A `RiskViolationError` raised by any rule SHALL cause the offending signal to be dropped (not submitted) and recorded in the engine's drop log; subsequent signals and subsequent steps SHALL continue normally.

#### Scenario: One bar produces one signal that is risk-blocked then dropped

- **WHEN** a strategy returns one BUY signal and the configured `RiskCheck` rejects it with `RiskViolationError`
- **THEN** the engine does not call `broker.submit_order`, the engine's drop log contains the offending signal, and the engine proceeds to `advance` for the time step

#### Scenario: One bar produces a fillable signal

- **WHEN** a strategy returns a BUY MARKET signal and the price is supplied to `advance`
- **THEN** the engine submits the order, the order fills, and `broker.get_position(symbol)` reflects the new quantity after `advance` returns

### Requirement: `BacktestEngine` is deterministic and explicit-step

`BacktestEngine.run(start, end, market_data)` SHALL iterate every bar in `[start, end]` in chronological order, calling the per-step pipeline once per bar. The iteration order over `(symbol, time)` pairs SHALL be deterministic given the input. The engine SHALL NOT make any network call, file IO, or implicit sleep. Two runs with the same `(strategy, broker, market_data, start, end)` SHALL produce identical `account` / `positions` / `orders` / `trades` final state.

#### Scenario: Same inputs produce same final account

- **WHEN** the caller runs `BacktestEngine.run(...)` twice with identical inputs
- **THEN** the final `broker.get_account()` and `broker.list_positions()` are equal

#### Scenario: Bar iteration order is chronological

- **WHEN** the supplied `market_data` returns bars for two symbols at the same `time`
- **THEN** the engine processes the bar with the lower `symbol` (lexicographic) first, then the higher one

### Requirement: `LiveEngine` is event-driven with MockSource

`LiveEngine.start()` SHALL start an internal `asyncio` event loop that polls `data.sources.mock_source.MockSource.next_price()` and, for each next price, calls the per-step pipeline using `datetime.now()` as `time`. `LiveEngine.stop()` SHALL stop the loop and join the task. The engine SHALL NOT swallow exceptions thrown by the strategy or the broker; such exceptions SHALL propagate out of the loop and stop the engine.

#### Scenario: Live engine consumes MockSource prices

- **WHEN** the caller invokes `LiveEngine.start()` and the MockSource has 3 prices queued
- **THEN** the engine processes 3 steps and stops gracefully when `LiveEngine.stop()` is called

#### Scenario: Live engine propagates broker exceptions

- **WHEN** a step raises an exception from `broker.submit_order`
- **THEN** the engine stops, the loop is cancelled, and the exception is re-raised to the caller of `start()`

### Requirement: `Strategy` is initialized once before the first step

`BacktestEngine.run` and `LiveEngine.start` SHALL, before the first step, call `strategy.on_init(ctx)` exactly once with a context that exposes `ctx.now`, `ctx.broker`, `ctx.risk`, and `ctx.account`. The strategy SHALL NOT call `broker.submit_order` from `on_init`; if it does, the engine SHALL raise `EngineUsageError` and abort the run.

#### Scenario: on_init is called once

- **WHEN** an engine starts a run
- **THEN** `strategy.on_init` is invoked exactly once before the first bar is processed

#### Scenario: on_init submitting orders aborts the run

- **WHEN** `strategy.on_init` calls `ctx.broker.submit_order(...)`
- **THEN** the engine raises `EngineUsageError` and the run aborts

### Requirement: A summary of the run is returned

`BacktestEngine.run` SHALL return a `RunSummary` containing `initial_account`, `final_account`, `n_orders`, `n_fills`, `n_dropped_signals`, and `start` / `end` times. The summary SHALL be the only public way the caller reads the run's terminal state.

#### Scenario: RunSummary reflects final state

- **WHEN** a backtest run completes
- **THEN** the returned `RunSummary.n_orders` equals the number of orders submitted and `n_fills` equals the number of trades produced by `advance`

### Requirement: CLI subcommand `xtrade backtest run`

The CLI SHALL expose a `backtest run` subcommand under the top-level `xtrade` group. The subcommand SHALL accept `--strategy` (format `module:Class`), `--start` (YYYY-MM-DD), `--end` (YYYY-MM-DD), `--symbols` (comma-separated), `--broker` (default `in-memory`; allowed: `in-memory`, `postgres`), `--initial-cash` (decimal; default `1_000_000`), and `--config` (optional path to a config file). The CLI SHALL load the strategy class via `importlib.import_module` and reflect a clear error if the strategy class is missing or does not satisfy the `Strategy` Protocol.

#### Scenario: backtest run with a valid strategy prints a summary

- **WHEN** the caller invokes `xtrade backtest run --strategy module:Cls --start 2024-01-01 --end 2024-01-02 --symbols A`
- **THEN** the CLI exits 0 and prints the `RunSummary` to stdout

#### Scenario: backtest run with an unknown strategy errors out

- **WHEN** the caller invokes `xtrade backtest run --strategy does_not_exist:Cls ...`
- **THEN** the CLI exits non-zero and prints a message stating the strategy could not be loaded

### Requirement: Engines do not mutate the `Broker` Protocol

Engines SHALL interact with a broker only through the `Broker` Protocol defined in `xtrade.execution.broker`. Engines SHALL NOT import `InMemoryBroker` or `PostgresBroker` directly; the caller chooses the implementation and passes it into the engine constructor.

#### Scenario: Engine accepts any Broker implementation

- **WHEN** the caller passes an `InMemoryBroker` instance to `BacktestEngine`
- **THEN** the engine runs the backtest and produces a `RunSummary` without importing `PostgresBroker` (or vice versa)
