# Capability: strategy

## Purpose

`xtrade.strategy` defines the `Strategy` Protocol and the data types (`Bar`, `Signal`, `Context`) that strategies consume and produce. A strategy is a stateless-from-the-engine's-perspective function object: it receives a `Context` per bar and returns zero or more `Signal`s. The strategy SHALL NOT interact directly with the broker or the risk layer; those are accessible via the injected `Context` references.

## Requirements

### Requirement: `Strategy` Protocol

`xtrade.strategy.base` SHALL expose a `Strategy` Protocol with two methods:

- `on_init(ctx: Context) -> None` — called once before the first bar.
- `on_bar(bar: Bar, ctx: Context) -> list[Signal]` — called once per bar.

The Protocol SHALL be `@runtime_checkable` so that `isinstance(obj, Strategy)` works for any class that implements both methods with the right signatures.

#### Scenario: A class with `on_init` and `on_bar` satisfies the Protocol

- **WHEN** a developer defines `class MyStrategy: def on_init(self, ctx): ...; def on_bar(self, bar, ctx): ...`
- **THEN** `isinstance(MyStrategy(), Strategy)` returns `True`

#### Scenario: A class missing `on_bar` does not satisfy the Protocol

- **WHEN** a developer defines a class that only implements `on_init`
- **THEN** `isinstance(obj, Strategy)` returns `False`

### Requirement: `Bar` is a single OHLCV+timestamp row

`Bar` SHALL be a frozen dataclass with fields: `symbol: str`, `time: datetime`, `open: Decimal`, `high: Decimal`, `low: Decimal`, `close: Decimal`, `volume: Decimal`, `interval: str`. `Bar` SHALL NOT depend on `xtrade.data.market_data`; it is an application-layer input type.

#### Scenario: Bar carries OHLCV

- **WHEN** a caller constructs `Bar(symbol="A", time=..., open=1, high=2, low=1, close=2, volume=10, interval="1d")`
- **THEN** all fields are accessible and the bar is hashable

### Requirement: `Signal` is a stateless trade intent

`Signal` SHALL be a frozen dataclass with fields: `symbol: str`, `side: OrderSide` (re-exported from `xtrade.execution.broker`), `quantity: Decimal`, `order_type: OrderType` (re-exported from `xtrade.execution.broker`), `price: Decimal | None`. `Signal` MAY carry an optional `client_order_id: str` for caller correlation. The engine SHALL translate a `Signal` into an `OrderRequest` whose `run_id` is the engine's current run id and whose `client_order_id` is the signal's id (or an auto-generated one if absent).

#### Scenario: Signal translates to OrderRequest

- **WHEN** a strategy returns a `Signal(side=OrderSide.BUY, quantity=10, order_type=OrderType.MARKET)`
- **THEN** the engine constructs an `OrderRequest` with the same `symbol`, `side`, `quantity`, `order_type`, `price=None`, and `run_id` from the engine

#### Scenario: Limit signal carries price

- **WHEN** a strategy returns a `Signal(order_type=OrderType.LIMIT, price=Decimal("9.99"))`
- **THEN** the engine constructs an `OrderRequest` with `order_type=LIMIT` and `price=Decimal("9.99")`

### Requirement: `Context` exposes the dependencies strategies need

`Context` SHALL be a frozen dataclass with fields: `now: datetime`, `bar: Bar | None`, `broker: Broker` (Protocol), `risk: RiskCheck` (Protocol), `account: Account`, `positions: dict[str, Position]`, `params: Mapping[str, Any]`. The strategy SHALL access state via `ctx.account`, `ctx.positions`, `ctx.broker.get_account()` if needed, etc. `Context` SHALL be immutable; a strategy that mutates `ctx` SHALL have no effect on subsequent steps (the engine rebuilds it each step).

#### Scenario: ctx.positions reflects current broker state

- **WHEN** the engine builds a `Context` for step N
- **THEN** `ctx.positions` is a `dict[str, Position]` reflecting the broker state after step N-1's `advance` returned

#### Scenario: Mutating ctx has no effect

- **WHEN** a strategy assigns `ctx.positions["A"] = Position(...)`
- **THEN** the next step's `ctx.positions` does not include the assignment

### Requirement: `params` allows caller-supplied configuration

`Context.params` SHALL be a `Mapping[str, Any]` populated by the engine from the strategy's `__init__` arguments (if the strategy class is instantiated by the engine with kwargs) or from the CLI's `--strategy-params` flag. The strategy reads its configuration from `ctx.params`, not from environment variables.

#### Scenario: CLI passes params via Context

- **WHEN** the CLI is invoked with `--strategy-params '{"lookback": 20}'`
- **THEN** `ctx.params` is `{"lookback": 20}` and the strategy reads `lookback` from `ctx.params["lookback"]`

### Requirement: Strategies are independent of the broker's implementation

`xtrade.strategy.base` SHALL NOT import `InMemoryBroker`, `PostgresBroker`, or any class from `xtrade.data.*`. The Protocol and types SHALL depend only on `xtrade.execution.broker` (re-exports of `OrderSide`, `OrderType`) and `xtrade.data.broker_data` (re-exports of `Account`, `Position`).

#### Scenario: importing strategy.base does not import data classes

- **WHEN** a developer writes `from xtrade.strategy.base import Strategy`
- **THEN** the import succeeds without importing `xtrade.data.engine` or `xtrade.data.sources.*`
