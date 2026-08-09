## Purpose

Provides a `Broker` abstraction in `xtrade.execution.broker` that business-layer code (strategy, backtest, paper / live trading) uses to submit orders, observe fills, and query positions / account state. Two implementations are mandated behind the same Protocol: `InMemoryBroker` (state lives in process) for backtests and unit tests, and `PostgresBroker` (state persisted via `xtrade.data.broker_data`) for paper / live runs. Existing `data-broker` Repository contracts are not modified.

## ADDED Requirements

### Requirement: `Broker` Protocol with a single command surface

The `xtrade.execution.broker` module SHALL expose a `Broker` Protocol that includes the following methods: `submit_order(req: OrderRequest) -> Order`, `cancel_order(order_id: int) -> None`, `get_order(order_id: int) -> Order | None`, `list_orders() -> list[Order]`, `get_position(symbol: str) -> Position | None`, `list_positions() -> list[Position]`, `get_account() -> Account`, `advance(time: datetime, prices: dict[str, Decimal]) -> list[Trade]`, and `register_callback(event: str, fn: Callable[..., None]) -> None`. Business-layer code SHALL depend only on the Protocol; concrete implementations are selected at the composition root.

#### Scenario: Caller submits a market order

- **WHEN** a caller invokes `broker.submit_order(OrderRequest(symbol="A", side=OrderSide.BUY, quantity=10, order_type=OrderType.MARKET, ...))`
- **THEN** the broker returns an `Order` whose `status` is `pending` (or `submitted` if the broker auto-submits) and the order receives a database- or memory-assigned `id`

#### Scenario: Caller looks up an order

- **WHEN** a caller invokes `broker.get_order(order_id)`
- **THEN** the broker returns the `Order` (or `None` if no such order exists)

#### Scenario: Caller lists positions

- **WHEN** a caller invokes `broker.list_positions()`
- **THEN** the broker returns the latest `Position` per `symbol` (or `[]` if no positions)

### Requirement: Two implementations behind the Protocol

The `xtrade.execution.broker` module SHALL provide two implementations of the `Broker` Protocol: `InMemoryBroker` (process-local dict / list) and `PostgresBroker` (delegates to `xtrade.data.broker_data` repositories). Both SHALL exhibit identical observable behavior for the order lifecycle, position / account updates, callbacks, and `advance` semantics, as expressed in the other requirements of this capability. A test SHALL be able to run the same scenario against both implementations without modification.

#### Scenario: InMemory and Postgres pass the same contract test

- **WHEN** a parameterized pytest runs the same test that submits an order, advances the clock, and asserts the resulting position / account for both `InMemoryBroker` and `PostgresBroker`
- **THEN** both implementations satisfy the assertion

### Requirement: Synchronous callbacks in registration order

The broker SHALL accept callback registration via `register_callback(event, fn)` where `event` is one of `on_fill`, `on_order_update`, `on_account_update`. Callbacks SHALL be invoked synchronously at the corresponding event point, in the order they were registered. A callback that raises SHALL NOT prevent subsequent callbacks from running and SHALL NOT roll back the broker state change that triggered the event (the exception is recorded in the application log).

#### Scenario: Multiple callbacks fire in order on fill

- **WHEN** a caller registers two callbacks for `on_fill` and the broker produces a fill
- **THEN** both callbacks are invoked once, in registration order, before `advance` returns

#### Scenario: Callback exception does not abort advance

- **WHEN** a callback registered for `on_fill` raises `RuntimeError`
- **THEN** the remaining `on_fill` callbacks still run, the fill is still persisted, and `advance` returns the same list of trades

### Requirement: `advance(time, prices)` is the only clock-driver

Time SHALL be advanced explicitly via `advance(time, prices)`. There SHALL be no internal timer, background thread, or implicit sleep. `prices` is a dict keyed by `symbol`. For each in-flight order in `submitted` or `partial` state, the broker SHALL attempt to fill according to `order_type` and the supplied price; orders whose `symbol` is not in `prices` SHALL remain in their current state.

#### Scenario: Market order fills on advance

- **WHEN** a market BUY order is in `submitted` and the caller invokes `advance(time, prices={"A": Decimal("100")})`
- **THEN** the broker produces one `Trade` with `price=100`, the order transitions to `filled`, and the `Trade` is returned in the `list[Trade]` result

#### Scenario: Limit BUY order fills only when price is at-or-below limit

- **WHEN** a limit BUY order with `price=Decimal("99")` is in `submitted` and the caller invokes `advance(time, prices={"A": Decimal("100")})`
- **THEN** the order remains in `submitted` and no `Trade` is produced

- **WHEN** the same caller subsequently invokes `advance(time, prices={"A": Decimal("98")})`
- **THEN** the order transitions to `filled` and one `Trade` is produced

#### Scenario: Outstanding order whose symbol is absent from prices remains open

- **WHEN** an order for `"A"` is in `submitted` and the caller invokes `advance(time, prices={})`
- **THEN** the order remains in `submitted` and no `Trade` is produced

#### Scenario: `advance` advances order status from pending to submitted

- **WHEN** a caller invokes `submit_order` and then `advance(time, prices)` without an immediate fill price
- **THEN** the order's `status` is at least `submitted` after `advance` returns

### Requirement: Position and account are maintained by the broker

The broker SHALL maintain positions and account as a side-effect of accepted fills. A fill on symbol `S` for side `BUY` (resp. `SELL`) SHALL update the position `quantity` and weighted-average `avg_price` accordingly. After each `advance`, the broker SHALL produce a new `Account` snapshot (or the in-memory equivalent) reflecting the updated cash and equity.

#### Scenario: Multiple BUY fills update average price

- **WHEN** a BUY of 10 @ 100 is filled, then a BUY of 10 @ 110 is filled for the same symbol
- **THEN** the position's `quantity` is `20` and `avg_price` is `105` (weighted average)

#### Scenario: SELL reduces position quantity

- **WHEN** a BUY of 10 @ 100 is filled and then a SELL of 4 @ 110 is filled for the same symbol
- **THEN** the position's `quantity` is `6` and `avg_price` remains `100`

#### Scenario: Account snapshot reflects cash and equity after advance

- **WHEN** the broker advances time and produces fills
- **THEN** `broker.get_account()` returns an `Account` whose `cash` and `equity` reflect the fills

### Requirement: `Order` state machine is enforced

The broker SHALL enforce the order-state transition graph defined by `xtrade.data.broker_data.order.ALLOWED_TRANSITIONS`. Any transition not in the graph SHALL raise `OrderStateError`. The broker SHALL reject `cancel_order` calls on orders that are not in `(pending, submitted, partial)` with a clear error.

#### Scenario: Cancel a pending order

- **WHEN** an order is in `pending` and the caller invokes `cancel_order(order_id)`
- **THEN** the order transitions to `cancelled` and `get_order(order_id).status == "cancelled"`

#### Scenario: Cancel a filled order is rejected

- **WHEN** an order is in `filled` and the caller invokes `cancel_order(order_id)`
- **THEN** the broker raises `OrderStateError` (or an equivalent) and the row is unchanged

### Requirement: `PostgresBroker` writes through the `data-broker` repositories

`PostgresBroker.advance` SHALL persist order state transitions, fills, position snapshots, and account snapshots via the four `xtrade.data.broker_data.Postgres*Repository` classes. `PostgresBroker` SHALL NOT make any network call outside the configured database. Each repository call commits within its own transaction; a follow-up change will route the session through the repositories so that the entire `advance` becomes a single atomic transaction.

#### Scenario: Failed repo write does not abort the rest of `advance`

- **WHEN** an `advance` call raises an exception mid-way (e.g. a `DuplicateSnapshotError` on the account write)
- **THEN** the writes that already committed before the raise remain in the database; the broker's intent is that post-commit partial writes are tolerable for the current implementation, and the future single-transaction refactor will close this gap

### Requirement: `InMemoryBroker` requires no external IO

`InMemoryBroker` SHALL NOT make any network call, database call, or file IO. All state SHALL live in the broker instance. Two `InMemoryBroker` instances SHALL NOT share state.

#### Scenario: Two instances have isolated state

- **WHEN** a caller creates two `InMemoryBroker` instances with the same `run_id` and submits an order to the first
- **THEN** `get_order` on the second instance returns `None` for that order

### Requirement: Types are reused from `xtrade.data.broker_data`

`Order`, `Trade`, `Position`, `Account`, `OrderState`, `OrderStateError`, and `DuplicateSnapshotError` SHALL be re-exported from `xtrade.execution.broker` and SHALL be the same class objects as those in `xtrade.data.broker_data`. The broker module MAY add new types (`OrderRequest`, `OrderSide`, `OrderType`) but SHALL NOT redefine the reused ones.

#### Scenario: Re-exported Order is the same class

- **WHEN** a developer imports `Order` from `xtrade.execution.broker`
- **THEN** `Order is xtrade.data.broker_data.Order` is `True`

### Requirement: No new third-party dependencies

The module SHALL depend only on existing `xtrade.data`, `xtrade.data.broker_data`, `xtrade.data.engine`, `xtrade.core.logging`, and the Python standard library. No new package SHALL be added to `pyproject.toml` for this change.

#### Scenario: `pyproject.toml` dependencies unchanged

- **WHEN** a developer inspects `pyproject.toml` after the change is applied
- **THEN** the `dependencies` list is unchanged from before the change
