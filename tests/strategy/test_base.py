"""Tests for :mod:`xtrade.strategy.base`."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from xtrade.execution.broker import Broker, OrderSide, OrderType
from xtrade.strategy.base import (
    Bar,
    Context,
    Signal,
    Strategy,
    signal_to_order_request,
)

# ---------------------------------------------------------------------------
# Strategy protocol structural typing
# ---------------------------------------------------------------------------


class _GoodStrategy:
    def on_init(self, ctx: Context) -> None:
        return None

    def on_bar(self, bar: Bar, ctx: Context) -> list[Signal]:
        return []


class _MissingOnBar:
    def on_init(self, ctx: Context) -> None:
        return None


class _MissingOnInit:
    def on_bar(self, bar: Bar, ctx: Context) -> list[Signal]:
        return []


def test_strategy_satisfied_by_class_with_both_methods() -> None:
    """A class with `on_init` + `on_bar` satisfies the Protocol."""
    assert isinstance(_GoodStrategy(), Strategy)


def test_strategy_not_satisfied_when_on_bar_missing() -> None:
    """A class missing `on_bar` does NOT satisfy the Protocol (regardless of duck typing)."""
    # The Protocol declares on_bar; an object missing it is not a structural match.
    assert not isinstance(_MissingOnBar(), Strategy)


def test_strategy_not_satisfied_when_on_init_missing() -> None:
    """A class missing `on_init` does NOT satisfy the Protocol."""
    assert not isinstance(_MissingOnInit(), Strategy)


# ---------------------------------------------------------------------------
# Bar
# ---------------------------------------------------------------------------


def test_bar_is_hashable_and_carries_ohlcv() -> None:
    """Bar dataclass is frozen and hashable; OHLCV fields are accessible."""
    bar = Bar(
        symbol="A",
        time=datetime(2024, 1, 1, tzinfo=UTC),
        open=Decimal("1"),
        high=Decimal("2"),
        low=Decimal("1"),
        close=Decimal("2"),
        volume=Decimal("10"),
        interval="1d",
    )
    assert bar.symbol == "A"
    assert bar.interval == "1d"
    # Hashable means it can be a set element / dict key.
    assert {bar} == {bar}


# ---------------------------------------------------------------------------
# Signal -> OrderRequest
# ---------------------------------------------------------------------------


def test_signal_to_order_request_market() -> None:
    """A market signal with no price becomes a MARKET OrderRequest with price=None."""
    sig = Signal(
        symbol="A", side=OrderSide.BUY, quantity=Decimal("10"), order_type=OrderType.MARKET
    )
    req = signal_to_order_request(sig, run_id="r1")
    assert req.run_id == "r1"
    assert req.symbol == "A"
    assert req.side == OrderSide.BUY
    assert req.quantity == Decimal("10")
    assert req.order_type == OrderType.MARKET
    assert req.price is None
    # client_order_id is auto-generated when absent.
    assert isinstance(req.client_order_id, str) and req.client_order_id


def test_signal_to_order_request_limit_carries_price() -> None:
    """A limit signal carries price through to the OrderRequest."""
    sig = Signal(
        symbol="A",
        side=OrderSide.SELL,
        quantity=Decimal("5"),
        order_type=OrderType.LIMIT,
        price=Decimal("9.99"),
    )
    req = signal_to_order_request(sig, run_id="r2")
    assert req.order_type == OrderType.LIMIT
    assert req.price == Decimal("9.99")


def test_signal_to_order_request_uses_signal_client_order_id() -> None:
    """A signal with explicit client_order_id is preserved on the OrderRequest."""
    sig = Signal(
        symbol="A",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        order_type=OrderType.MARKET,
        client_order_id="my-cid",
    )
    req = signal_to_order_request(sig, run_id="r3")
    assert req.client_order_id == "my-cid"


def test_signal_to_order_request_caller_overrides_client_order_id() -> None:
    """A caller-supplied client_order_id wins over the signal's own."""
    sig = Signal(
        symbol="A",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        order_type=OrderType.MARKET,
        client_order_id="signal-cid",
    )
    req = signal_to_order_request(sig, run_id="r4", client_order_id="caller-cid")
    assert req.client_order_id == "caller-cid"


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


class _StubBroker:
    """Bare-minimum broker stub satisfying `Broker` Protocol for context tests."""

    def submit_order(self, req: Any) -> Any:  # pragma: no cover - not used
        return None

    def cancel_order(self, order_id: int) -> None:  # pragma: no cover
        return None

    def get_order(self, order_id: int) -> Any:  # pragma: no cover
        return None

    def list_orders(self) -> list[Any]:  # pragma: no cover
        return []

    def get_position(self, symbol: str) -> Any:  # pragma: no cover
        return None

    def list_positions(self) -> list[Any]:  # pragma: no cover
        return []

    def get_account(self) -> Any:  # pragma: no cover
        return None

    def advance(self, time: Any, prices: Any) -> list[Any]:  # pragma: no cover
        return []

    def register_callback(self, event: str, fn: Any) -> None:  # pragma: no cover
        return None


class _StubRisk:
    def check(self, intent: Any, ctx: Any) -> None:
        return None


def test_context_is_frozen() -> None:
    """Context is a frozen dataclass; assignment raises FrozenInstanceError."""
    import dataclasses

    bar = Bar(
        symbol="A",
        time=datetime(2024, 1, 1, tzinfo=UTC),
        open=Decimal("1"),
        high=Decimal("2"),
        low=Decimal("1"),
        close=Decimal("2"),
        volume=Decimal("10"),
        interval="1d",
    )
    ctx = Context(
        now=datetime(2024, 1, 1, tzinfo=UTC),
        bar=bar,
        broker=_StubBroker(),  # type: ignore[arg-type]
        risk=_StubRisk(),  # type: ignore[arg-type]
        account=type("A", (), {})(),  # type: ignore[arg-type]
        positions={},
    )
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        ctx.now = datetime(2024, 1, 2, tzinfo=UTC)  # type: ignore[misc]


def test_context_params_default_to_empty_mapping() -> None:
    """ctx.params defaults to an empty mapping when none supplied."""
    ctx = Context(
        now=datetime(2024, 1, 1, tzinfo=UTC),
        bar=None,
        broker=_StubBroker(),  # type: ignore[arg-type]
        risk=_StubRisk(),  # type: ignore[arg-type]
        account=type("A", (), {})(),  # type: ignore[arg-type]
        positions={},
    )
    assert dict(ctx.params) == {}


def test_context_params_plumb_through() -> None:
    """ctx.params is the caller-supplied mapping."""
    ctx = Context(
        now=datetime(2024, 1, 1, tzinfo=UTC),
        bar=None,
        broker=_StubBroker(),  # type: ignore[arg-type]
        risk=_StubRisk(),  # type: ignore[arg-type]
        account=type("A", (), {})(),  # type: ignore[arg-type]
        positions={},
        params={"lookback": 20},
    )
    assert ctx.params["lookback"] == 20


def test_stub_broker_satisfies_protocol() -> None:
    """The test stub satisfies the Broker Protocol (sanity for `_StubBroker`)."""
    assert isinstance(_StubBroker(), Broker)
