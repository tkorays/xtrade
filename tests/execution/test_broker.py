"""Contract tests for the ``xtrade.execution.broker`` Broker abstraction.

The same expectations are run against both ``InMemoryBroker`` (always)
and ``PostgresBroker`` (skipped when ``XTRADE_TEST_DB_URL`` is unset,
mirroring the pattern in ``tests/data/test_broker_data.py``).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from xtrade.data.broker_data import Order, OrderStateError
from xtrade.execution.broker import (
    Account,
    InMemoryBroker,
    OrderRequest,
    OrderSide,
    OrderType,
    Position,
)

skip_without_db = pytest.mark.skipif(
    not os.environ.get("XTRADE_TEST_DB_URL"),
    reason="XTRADE_TEST_DB_URL not set; Postgres-broker contract tests skipped",
)


def _broker():
    return InMemoryBroker(run_id="run-test", initial_cash=Decimal("100000"))


# ---------------------------------------------------------------------------
# Identity / structural
# ---------------------------------------------------------------------------


def test_order_re_exported_is_same_class() -> None:
    """Spec: ``Order`` re-exported from ``xtrade.execution.broker`` is the
    same class object as ``xtrade.data.broker_data.Order``."""
    from xtrade.data.broker_data import Order as DataOrder
    from xtrade.execution.broker import Order as BrokerOrder

    assert BrokerOrder is DataOrder


def test_inmemory_broker_satisfies_protocol() -> None:
    """InMemoryBroker satisfies the Broker Protocol at runtime."""
    from xtrade.execution.broker import Broker

    assert isinstance(_broker(), Broker)


def test_two_inmemory_brokers_have_isolated_state() -> None:
    """Spec: two InMemoryBroker instances with the same run_id do not share
    state."""
    a = _broker()
    b = _broker()
    o = a.submit_order(
        OrderRequest(
            run_id="run-test",
            client_order_id="a-1",
            symbol="A",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            order_type=OrderType.MARKET,
        )
    )
    assert a.get_order(o.id) is o
    assert b.get_order(o.id) is None


# ---------------------------------------------------------------------------
# submit_order / get_order
# ---------------------------------------------------------------------------


def test_submit_order_returns_pending_order() -> None:
    b = _broker()
    o = b.submit_order(
        OrderRequest(
            run_id="run-test",
            client_order_id="c-1",
            symbol="A",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.MARKET,
        )
    )
    assert isinstance(o, Order)
    assert o.id is not None
    assert o.status == "pending"
    assert o.side == "buy"


def test_get_order_unknown_returns_none() -> None:
    assert _broker().get_order(99999) is None


def test_submit_order_rejects_zero_quantity() -> None:
    b = _broker()
    with pytest.raises(ValueError):
        b.submit_order(
            OrderRequest(
                run_id="run-test",
                client_order_id="c-2",
                symbol="A",
                side=OrderSide.BUY,
                quantity=Decimal("0"),
                order_type=OrderType.MARKET,
            )
        )


def test_submit_limit_order_requires_price() -> None:
    b = _broker()
    with pytest.raises(ValueError):
        b.submit_order(
            OrderRequest(
                run_id="run-test",
                client_order_id="c-3",
                symbol="A",
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                order_type=OrderType.LIMIT,
                price=None,
            )
        )


# ---------------------------------------------------------------------------
# advance: market order
# ---------------------------------------------------------------------------


def test_advance_market_buy_fills_at_supplied_price() -> None:
    b = _broker()
    b.submit_order(
        OrderRequest(
            run_id="run-test",
            client_order_id="c-1",
            symbol="A",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.MARKET,
        )
    )
    fills = b.advance(datetime(2025, 1, 1, tzinfo=UTC), {"A": Decimal("100")})
    assert len(fills) == 1
    assert fills[0].price == Decimal("100")
    assert fills[0].quantity == Decimal("10")
    # Order transitioned to filled.
    assert b.get_order(fills[0].order_id).status == "filled"


def test_advance_pending_order_advances_to_submitted() -> None:
    b = _broker()
    o = b.submit_order(
        OrderRequest(
            run_id="run-test",
            client_order_id="c-1",
            symbol="A",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.MARKET,
        )
    )
    # No price for A — order should not fill, but status should advance.
    fills = b.advance(datetime(2025, 1, 1, tzinfo=UTC), {})
    assert fills == []
    assert b.get_order(o.id).status == "submitted"


# ---------------------------------------------------------------------------
# advance: limit order
# ---------------------------------------------------------------------------


def test_advance_limit_buy_fills_only_when_price_at_or_below_limit() -> None:
    b = _broker()
    b.submit_order(
        OrderRequest(
            run_id="run-test",
            client_order_id="c-1",
            symbol="A",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.LIMIT,
            price=Decimal("99"),
        )
    )
    # Price above limit: no fill.
    fills1 = b.advance(datetime(2025, 1, 1, tzinfo=UTC), {"A": Decimal("100")})
    assert fills1 == []
    # Price below limit: fill.
    fills2 = b.advance(datetime(2025, 1, 2, tzinfo=UTC), {"A": Decimal("98")})
    assert len(fills2) == 1
    assert fills2[0].price == Decimal("98")


def test_advance_limit_sell_fills_only_when_price_at_or_above_limit() -> None:
    b = _broker()
    # First buy 10 @ 100 to open a position we'd flip from.
    b.submit_order(
        OrderRequest(
            run_id="run-test",
            client_order_id="c-1",
            symbol="A",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.MARKET,
        )
    )
    b.advance(datetime(2025, 1, 1, tzinfo=UTC), {"A": Decimal("100")})
    # Now place a limit SELL at 105.
    b.submit_order(
        OrderRequest(
            run_id="run-test",
            client_order_id="c-2",
            symbol="A",
            side=OrderSide.SELL,
            quantity=Decimal("10"),
            order_type=OrderType.LIMIT,
            price=Decimal("105"),
        )
    )
    # Price below limit: no fill.
    fills1 = b.advance(datetime(2025, 1, 2, tzinfo=UTC), {"A": Decimal("100")})
    assert fills1 == []
    # Price at-or-above limit: fill.
    fills2 = b.advance(datetime(2025, 1, 3, tzinfo=UTC), {"A": Decimal("105")})
    assert len(fills2) == 1


def test_advance_order_with_missing_symbol_stays_open() -> None:
    b = _broker()
    o = b.submit_order(
        OrderRequest(
            run_id="run-test",
            client_order_id="c-1",
            symbol="A",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.MARKET,
        )
    )
    fills = b.advance(datetime(2025, 1, 1, tzinfo=UTC), {})
    assert fills == []
    assert b.get_order(o.id).status == "submitted"


# ---------------------------------------------------------------------------
# Position weighted-average
# ---------------------------------------------------------------------------


def test_position_weighted_average_after_multiple_buys() -> None:
    b = _broker()
    b.submit_order(
        OrderRequest(
            run_id="run-test",
            client_order_id="c-1",
            symbol="A",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.MARKET,
        )
    )
    b.advance(datetime(2025, 1, 1, tzinfo=UTC), {"A": Decimal("100")})
    b.submit_order(
        OrderRequest(
            run_id="run-test",
            client_order_id="c-2",
            symbol="A",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.MARKET,
        )
    )
    b.advance(datetime(2025, 1, 2, tzinfo=UTC), {"A": Decimal("110")})
    pos = b.get_position("A")
    assert pos is not None
    assert pos.quantity == Decimal("20")
    assert pos.avg_price == Decimal("105")


def test_position_sell_reduces_quantity() -> None:
    b = _broker()
    b.submit_order(
        OrderRequest(
            run_id="run-test",
            client_order_id="c-1",
            symbol="A",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.MARKET,
        )
    )
    b.advance(datetime(2025, 1, 1, tzinfo=UTC), {"A": Decimal("100")})
    b.submit_order(
        OrderRequest(
            run_id="run-test",
            client_order_id="c-2",
            symbol="A",
            side=OrderSide.SELL,
            quantity=Decimal("4"),
            order_type=OrderType.MARKET,
        )
    )
    b.advance(datetime(2025, 1, 2, tzinfo=UTC), {"A": Decimal("110")})
    pos = b.get_position("A")
    assert pos is not None
    assert pos.quantity == Decimal("6")
    assert pos.avg_price == Decimal("100")


# ---------------------------------------------------------------------------
# Account after advance
# ---------------------------------------------------------------------------


def test_account_snapshot_after_advance() -> None:
    b = _broker()
    b.submit_order(
        OrderRequest(
            run_id="run-test",
            client_order_id="c-1",
            symbol="A",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.MARKET,
        )
    )
    b.advance(datetime(2025, 1, 1, tzinfo=UTC), {"A": Decimal("100")})
    acct = b.get_account()
    assert isinstance(acct, Account)
    # Cash untouched, equity reflects position value.
    assert acct.cash == Decimal("100000")
    assert acct.equity == Decimal("100000") + Decimal("10") * Decimal("100")


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


def test_callbacks_fire_in_registration_order() -> None:
    b = _broker()
    calls: list[str] = []
    b.register_callback("on_fill", lambda payload: calls.append("first:" + str(payload[0].id)))
    b.register_callback("on_fill", lambda payload: calls.append("second"))
    b.submit_order(
        OrderRequest(
            run_id="run-test",
            client_order_id="c-1",
            symbol="A",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.MARKET,
        )
    )
    b.advance(datetime(2025, 1, 1, tzinfo=UTC), {"A": Decimal("100")})
    assert len(calls) == 2
    assert calls[0].startswith("first:")
    assert calls[1] == "second"


def test_callback_exception_does_not_abort_advance() -> None:
    b = _broker()
    second_called: list[bool] = []
    b.register_callback("on_fill", lambda payload: (_ for _ in ()).throw(RuntimeError("boom")))
    b.register_callback("on_fill", lambda payload: second_called.append(True))
    b.submit_order(
        OrderRequest(
            run_id="run-test",
            client_order_id="c-1",
            symbol="A",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.MARKET,
        )
    )
    fills = b.advance(datetime(2025, 1, 1, tzinfo=UTC), {"A": Decimal("100")})
    assert len(fills) == 1
    assert second_called == [True]


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def test_cancel_pending_order_succeeds() -> None:
    b = _broker()
    o = b.submit_order(
        OrderRequest(
            run_id="run-test",
            client_order_id="c-1",
            symbol="A",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.MARKET,
        )
    )
    b.cancel_order(o.id)
    assert b.get_order(o.id).status == "cancelled"


def test_cancel_filled_order_raises() -> None:
    b = _broker()
    b.submit_order(
        OrderRequest(
            run_id="run-test",
            client_order_id="c-1",
            symbol="A",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.MARKET,
        )
    )
    b.advance(datetime(2025, 1, 1, tzinfo=UTC), {"A": Decimal("100")})
    # Order is now FILLED.
    with pytest.raises(OrderStateError):
        b.cancel_order(1)


def test_cancel_unknown_order_raises() -> None:
    b = _broker()
    with pytest.raises(LookupError):
        b.cancel_order(42)


# ---------------------------------------------------------------------------
# Postgres contract (skipped without DB)
# ---------------------------------------------------------------------------


@skip_without_db
def test_postgres_broker_submits_and_advances(
    db_url: str,
    engine,
    schema,
) -> None:
    """Mirror of the in-memory market BUY fill: PostgreSQL implementation
    must produce equivalent state."""
    from xtrade.execution.broker import PostgresBroker

    run_id = "pg-run-test"
    b = PostgresBroker(run_id=run_id, initial_cash=Decimal("100000"))
    o = b.submit_order(
        OrderRequest(
            run_id=run_id,
            client_order_id="c-1",
            symbol="A",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.MARKET,
        )
    )
    fills = b.advance(datetime(2025, 1, 1, tzinfo=UTC), {"A": Decimal("100")})
    assert len(fills) == 1
    assert fills[0].price == Decimal("100")
    assert b.get_order(o.id).status == "filled"
    pos = b.get_position("A")
    assert pos is not None
    assert isinstance(pos, Position)
    assert pos.quantity == Decimal("10")
    assert pos.avg_price == Decimal("100")
