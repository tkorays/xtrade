"""Tests for :mod:`xtrade.risk`."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from xtrade.data.broker_data import Account
from xtrade.execution.broker import OrderSide
from xtrade.risk import (
    CompositeRiskCheck,
    KillSwitch,
    OrderIntent,
    OrderSizeLimit,
    PositionLimit,
    RiskCheck,
    RiskContext,
    RiskViolationError,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _account(equity: Decimal = Decimal("1000000")) -> Account:
    return Account(
        run_id="r1",
        time=datetime(2024, 1, 1, tzinfo=UTC),
        cash=equity,
        equity=equity,
        margin=Decimal("0"),
    )


def _ctx(equity: Decimal = Decimal("1000000")) -> RiskContext:
    return RiskContext(account=_account(equity), positions={}, now=datetime(2024, 1, 1, tzinfo=UTC))


def _buy_intent(
    quantity: Decimal = Decimal("10"),
    price: Decimal | None = Decimal("100"),
    expected_qty_after: Decimal = Decimal("10"),
) -> OrderIntent:
    return OrderIntent(
        symbol="A",
        side=OrderSide.BUY,
        quantity=quantity,
        price=price,
        expected_qty_after=expected_qty_after,
    )


# ---------------------------------------------------------------------------
# RiskCheck protocol
# ---------------------------------------------------------------------------


class _GoodCheck:
    def check(self, intent: OrderIntent, ctx: RiskContext) -> None:
        return None


class _NoCheck:
    pass


def test_risk_check_satisfied_by_class_with_check_method() -> None:
    """A class with a `check` method satisfies the Protocol."""
    assert isinstance(_GoodCheck(), RiskCheck)


def test_risk_check_not_satisfied_when_check_missing() -> None:
    assert not isinstance(_NoCheck(), RiskCheck)


# ---------------------------------------------------------------------------
# RiskViolationError
# ---------------------------------------------------------------------------


def test_risk_violation_error_carries_rule_name() -> None:
    """RiskViolationError.rule_name is the class name of the firing rule."""
    try:
        raise RiskViolationError("MyRule", "boom")
    except RiskViolationError as exc:
        assert exc.rule_name == "MyRule"
        assert exc.message == "boom"
        assert "MyRule" in str(exc)
        assert "boom" in str(exc)


# ---------------------------------------------------------------------------
# OrderSizeLimit
# ---------------------------------------------------------------------------


def test_order_size_limit_rejects_oversized_order() -> None:
    """OrderSizeLimit rejects when quantity * price > max_notional."""
    check = OrderSizeLimit(max_notional=Decimal("50000"))
    intent = _buy_intent(quantity=Decimal("1000"), price=Decimal("100"))
    with pytest.raises(RiskViolationError) as exc_info:
        check.check(intent, _ctx())
    assert exc_info.value.rule_name == "OrderSizeLimit"
    assert "exceeds" in exc_info.value.message


def test_order_size_limit_accepts_under_cap_order() -> None:
    """OrderSizeLimit returns None when quantity * price <= max_notional."""
    check = OrderSizeLimit(max_notional=Decimal("50000"))
    intent = _buy_intent(quantity=Decimal("100"), price=Decimal("100"))
    assert check.check(intent, _ctx()) is None


def test_order_size_limit_uses_quantity_when_price_is_none() -> None:
    """When price is None, notional is treated as quantity (e.g. for market orders)."""
    check = OrderSizeLimit(max_notional=Decimal("50"))
    intent = _buy_intent(quantity=Decimal("100"), price=None)
    with pytest.raises(RiskViolationError):
        check.check(intent, _ctx())


# ---------------------------------------------------------------------------
# PositionLimit
# ---------------------------------------------------------------------------


def test_position_limit_rejects_when_projected_breaches_cap() -> None:
    """PositionLimit rejects when expected_qty_after > max_qty."""
    check = PositionLimit(max_qty=Decimal("50"))
    intent = _buy_intent(quantity=Decimal("100"), expected_qty_after=Decimal("100"))
    with pytest.raises(RiskViolationError) as exc_info:
        check.check(intent, _ctx())
    assert exc_info.value.rule_name == "PositionLimit"


def test_position_limit_accepts_within_cap() -> None:
    """PositionLimit returns None when expected_qty_after <= max_qty."""
    check = PositionLimit(max_qty=Decimal("50"))
    intent = _buy_intent(quantity=Decimal("30"), expected_qty_after=Decimal("30"))
    assert check.check(intent, _ctx()) is None


def test_position_limit_rejects_short_selling() -> None:
    """A projected negative position raises (short selling forbidden by this rule)."""
    check = PositionLimit(max_qty=Decimal("50"))
    intent = _buy_intent(quantity=Decimal("5"), expected_qty_after=Decimal("-5"))
    with pytest.raises(RiskViolationError) as exc_info:
        check.check(intent, _ctx())
    assert exc_info.value.rule_name == "PositionLimit"
    assert "short" in exc_info.value.message


# ---------------------------------------------------------------------------
# KillSwitch
# ---------------------------------------------------------------------------


def test_kill_switch_engages_after_daily_loss() -> None:
    """When current equity <= baseline - threshold, the switch engages."""
    baseline = Decimal("1000000")
    switch = KillSwitch(trigger_on_daily_loss=Decimal("5000"), equity_at_open=baseline)
    # Equity dropped 5000 (or more) from baseline.
    with pytest.raises(RiskViolationError) as exc_info:
        switch.check(_buy_intent(), _ctx(equity=baseline - Decimal("5000")))
    assert exc_info.value.rule_name == "KillSwitch"
    assert switch.engaged is True


def test_kill_switch_blocks_after_engagement() -> None:
    """Once engaged, every subsequent check raises."""
    switch = KillSwitch(trigger_on_daily_loss=Decimal("5000"), equity_at_open=Decimal("1000000"))
    # Engage.
    with pytest.raises(RiskViolationError):
        switch.check(_buy_intent(), _ctx(equity=Decimal("995000")))
    # Subsequent call should also raise (still engaged).
    with pytest.raises(RiskViolationError) as exc_info:
        switch.check(_buy_intent(), _ctx(equity=Decimal("1000000")))
    assert "engaged" in exc_info.value.message


def test_kill_switch_reset_disengages() -> None:
    """reset() clears the engaged flag; the next check evaluates the loss again."""
    switch = KillSwitch(trigger_on_daily_loss=Decimal("5000"), equity_at_open=Decimal("1000000"))
    with pytest.raises(RiskViolationError):
        switch.check(_buy_intent(), _ctx(equity=Decimal("995000")))
    assert switch.engaged is True
    switch.reset()
    assert switch.engaged is False
    # After reset, with the loss already past threshold, the next check
    # should re-engage (because the threshold is still breached).
    with pytest.raises(RiskViolationError):
        switch.check(_buy_intent(), _ctx(equity=Decimal("995000")))
    assert switch.engaged is True


def test_kill_switch_no_threshold_passes() -> None:
    """When trigger_on_daily_loss is None, the switch never engages."""
    switch = KillSwitch(trigger_on_daily_loss=None)
    assert switch.check(_buy_intent(), _ctx()) is None
    assert switch.engaged is False


# ---------------------------------------------------------------------------
# CompositeRiskCheck
# ---------------------------------------------------------------------------


def test_composite_short_circuits_on_first_violation() -> None:
    """The first rule that raises prevents subsequent rules from running."""
    called = {"size": 0, "pos": 0}

    class _SizeCheck:
        def check(self, intent: OrderIntent, ctx: RiskContext) -> None:
            called["size"] += 1
            raise RiskViolationError("SizeCheck", "too big")

    class _PosCheck:
        def check(self, intent: OrderIntent, ctx: RiskContext) -> None:
            called["pos"] += 1

    composite = CompositeRiskCheck(checks=[_SizeCheck(), _PosCheck()])
    with pytest.raises(RiskViolationError) as exc_info:
        composite.check(_buy_intent(), _ctx())
    assert exc_info.value.rule_name == "SizeCheck"
    assert called == {"size": 1, "pos": 0}


def test_composite_passes_when_no_violations() -> None:
    """When no rule raises, the composite returns None."""
    composite = CompositeRiskCheck(
        checks=[OrderSizeLimit(Decimal("1000000")), PositionLimit(Decimal("1000"))]
    )
    assert (
        composite.check(_buy_intent(quantity=Decimal("10"), price=Decimal("100")), _ctx()) is None
    )


# ---------------------------------------------------------------------------
# RiskContext does not expose broker
# ---------------------------------------------------------------------------


def test_risk_context_has_no_broker_attribute() -> None:
    """RiskContext deliberately omits `broker`; rules that try to access it fail."""
    ctx = _ctx()
    assert not hasattr(ctx, "broker")
    with pytest.raises(AttributeError):
        _ = ctx.broker  # type: ignore[attr-defined]
