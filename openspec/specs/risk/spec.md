# Capability: risk

## Purpose

`xtrade.risk` defines pre-trade risk checks. A `RiskCheck` is invoked once per signal by the engine before the signal is submitted to the broker. A check that rejects a signal raises `RiskViolationError`; the engine drops the signal and logs the reason. Three concrete rules are mandated: `OrderSizeLimit`, `PositionLimit`, and `KillSwitch`. A `CompositeRiskCheck` SHALL compose multiple checks in order.

## Requirements

### Requirement: `RiskCheck` Protocol

`xtrade.risk.base` SHALL expose a `RiskCheck` Protocol with one method: `check(intent: OrderIntent, ctx: RiskContext) -> None`. The Protocol SHALL be `@runtime_checkable`. Implementation classes SHOULD live in `xtrade.risk.checks`. A check that does not raise returns `None` and the engine treats the signal as approved.

#### Scenario: A class with `check` satisfies the Protocol

- **WHEN** a developer defines `class MyCheck: def check(self, intent, ctx): ...`
- **THEN** `isinstance(MyCheck(), RiskCheck)` returns `True`

#### Scenario: A non-violating check returns None

- **WHEN** a caller's `check` does not raise
- **THEN** the engine treats the signal as approved and proceeds to `broker.submit_order`

### Requirement: `RiskViolationError` carries a reason

`RiskViolationError` SHALL be a subclass of `RuntimeError` with two fields: `rule_name: str` (the class name of the rule that fired) and `message: str` (a human-readable reason). The engine SHALL log the failure with `rule_name` and `message` and decrement the count of accepted signals by one.

#### Scenario: RiskViolationError contains rule name

- **WHEN** a `PositionLimit` check raises
- **THEN** the exception's `rule_name` is `"PositionLimit"` and a structured log entry is emitted

### Requirement: `OrderIntent` and `RiskContext` are input types

`OrderIntent` SHALL be a frozen dataclass with `symbol: str`, `side: OrderSide`, `quantity: Decimal`, `price: Decimal | None`, `expected_qty_after: Decimal` (the broker's projected position quantity after this signal is filled). `RiskContext` SHALL be a frozen dataclass with `account: Account`, `positions: dict[str, Position]`, `now: datetime`. Both SHALL be constructed by the engine; rules MUST NOT mutate them.

#### Scenario: OrderIntent carries expected post-fill quantity

- **WHEN** the engine builds an `OrderIntent` for a BUY 10 of "A" against a current position of 5
- **THEN** `intent.expected_qty_after` is `15`

### Requirement: `OrderSizeLimit` rejects over-cap orders

`OrderSizeLimit(max_notional: Decimal)` SHALL reject any signal whose `price * quantity` (or `quantity` when `price` is `None`) exceeds `max_notional`. `OrderSizeLimit` SHALL raise `RiskViolationError` with `rule_name="OrderSizeLimit"` and a message that includes the configured `max_notional` and the actual notional.

#### Scenario: OrderSizeLimit rejects a single oversized order

- **WHEN** a BUY 1000 @ 100 is checked against `OrderSizeLimit(max_notional=50_000)`
- **THEN** the check raises `RiskViolationError` with `rule_name="OrderSizeLimit"` and the engine drops the signal

#### Scenario: OrderSizeLimit accepts an under-cap order

- **WHEN** a BUY 100 @ 100 is checked against `OrderSizeLimit(max_notional=50_000)`
- **THEN** the check returns `None`

### Requirement: `PositionLimit` rejects orders that exceed a per-symbol cap

`PositionLimit(max_qty: Decimal)` SHALL reject any signal whose `expected_qty_after` exceeds `max_qty`. SELL signals SHALL be checked against the same cap; a short position is not allowed by this rule (the broker implementation, not this rule, governs short selling). The check SHALL raise `RiskViolationError` with `rule_name="PositionLimit"`.

#### Scenario: PositionLimit rejects an order that breaches the cap

- **WHEN** a BUY 100 of "A" is checked against `PositionLimit(max_qty=50)` and the current position is 0
- **THEN** the check raises `RiskViolationError` with `rule_name="PositionLimit"` and `expected_qty_after=100`

#### Scenario: PositionLimit accepts an order within the cap

- **WHEN** a BUY 30 of "A" is checked against `PositionLimit(max_qty=50)` and the current position is 0
- **THEN** the check returns `None`

### Requirement: `KillSwitch` blocks all signals when triggered

`KillSwitch(trigger_on_daily_loss: Decimal | None)` SHALL track the day's realized + unrealized P&L against `trigger_on_daily_loss` (a positive number representing a loss threshold; e.g. `Decimal("5000")` means block when daily loss reaches 5000). When the threshold is exceeded, `KillSwitch.check` SHALL raise `RiskViolationError` with `rule_name="KillSwitch"`. Once triggered, the kill switch remains engaged until `reset()` is called. `KillSwitch` SHALL expose `engaged: bool` (read-only property) and `reset()` (clears the engaged flag and the day's P&L).

#### Scenario: KillSwitch engages after a daily loss

- **WHEN** `KillSwitch(trigger_on_daily_loss=Decimal("5000"))` is configured with an account whose equity decreased by 5000 or more from the start-of-day snapshot
- **THEN** the next signal's check raises `RiskViolationError` and `engaged` is `True`

#### Scenario: Once engaged, all subsequent signals are blocked

- **WHEN** a `KillSwitch` is engaged
- **THEN** every subsequent `check` raises `RiskViolationError` regardless of intent

#### Scenario: reset re-enables the switch

- **WHEN** the caller invokes `kill_switch.reset()` and the daily loss is now within the threshold
- **THEN** `engaged` is `False` and the next `check` returns `None`

### Requirement: `CompositeRiskCheck` runs checks in order

`CompositeRiskCheck(checks: list[RiskCheck])` SHALL run `checks` in the given order and short-circuit on the first `RiskViolationError`. The order of checks matters and is the caller's responsibility.

#### Scenario: First failing rule short-circuits

- **WHEN** a `CompositeRiskCheck([OrderSizeLimit(50_000), PositionLimit(50)])` receives a BUY 1000 @ 100
- **THEN** `OrderSizeLimit` raises first and `PositionLimit` is not invoked

#### Scenario: Composite with no violations passes

- **WHEN** a `CompositeRiskCheck` contains three rules and none raise for the given intent
- **THEN** `check` returns `None`

### Requirement: Risk checks do not call the broker

A `RiskCheck.check` method SHALL NOT call `ctx.broker.submit_order` or any other broker mutation method. Reads of `ctx.account` / `ctx.positions` are permitted. The engine SHALL inject a `RiskContext` that exposes `account` and `positions` (broker-style objects) but does NOT expose `Broker`.

#### Scenario: RiskContext exposes account and positions, not broker

- **WHEN** a rule calls `ctx.broker`
- **THEN** the call raises `AttributeError` (the value is not present)
