"""Strategy layer: signal generation.

Defines the :class:`Strategy` Protocol and the per-step data types
(:class:`Bar`, :class:`Signal`, :class:`Context`) that strategies consume
and produce. A strategy is a stateless-from-the-engine's-perspective
object: it receives a :class:`Context` per bar and returns zero or more
:class:`Signal` objects. The strategy SHALL NOT interact directly with
the broker or the risk layer; those are accessible via the injected
:class:`Context` references.

Domain types (``OrderSide``, ``OrderType``, ``Account``, ``Position``)
are re-exported from ``xtrade.execution.broker`` /
``xtrade.data.broker_data`` so that callers do not need to import from
the data layer directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import uuid4

from xtrade.data.broker_data import Account, Position
from xtrade.execution.broker import OrderRequest, OrderSide, OrderType

if TYPE_CHECKING:
    from xtrade.execution.broker import Broker
    from xtrade.risk.base import RiskCheck

__all__ = [
    "Bar",
    "Context",
    "Signal",
    "Strategy",
    "signal_to_order_request",
]


@dataclass(frozen=True)
class Bar:
    """Single OHLCV bar for a strategy step.

    Decoupled from ``xtrade.data.market_data.Bar`` (which is an ORM row)
    so that strategies can be tested in isolation without spinning up a
    database.
    """

    symbol: str
    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    interval: str


@dataclass(frozen=True)
class Signal:
    """A stateless trade intent emitted by a strategy.

    The engine translates a :class:`Signal` into an :class:`OrderRequest`
    via :func:`signal_to_order_request`. ``client_order_id`` is optional;
    if absent, the engine generates a UUID.
    """

    symbol: str
    side: OrderSide
    quantity: Decimal
    order_type: OrderType
    price: Decimal | None = None
    client_order_id: str | None = None


@dataclass(frozen=True)
class Context:
    """Per-step read-only context handed to a strategy.

    ``params`` is the caller-supplied configuration (CLI flag, engine
    ``__init__`` kwargs). Strategies SHALL read their config from
    ``ctx.params`` and not from environment variables.
    """

    now: datetime
    bar: Bar | None
    broker: Broker
    risk: RiskCheck
    account: Account
    positions: Mapping[str, Position]
    params: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class Strategy(Protocol):
    """A trading strategy receives bars and emits signals.

    The engine invokes :meth:`on_init` once before the first step and
    :meth:`on_bar` once per bar. Strategies MUST NOT call
    ``broker.submit_order`` from ``on_init``; the engine raises
    :class:`xtrade.engine.EngineUsageError` if they do.
    """

    def on_init(self, ctx: Context) -> None:
        """Called once before the first bar. Read-only setup."""
        ...

    def on_bar(self, bar: Bar, ctx: Context) -> list[Signal]:
        """Called once per bar. Return the signals to act on this step."""
        ...


def signal_to_order_request(
    signal: Signal,
    *,
    run_id: str,
    client_order_id: str | None = None,
) -> OrderRequest:
    """Translate a :class:`Signal` into an :class:`OrderRequest`.

    The ``client_order_id`` argument wins if provided; otherwise the
    signal's ``client_order_id`` is used; otherwise a UUID is generated.
    """
    cid = client_order_id or signal.client_order_id or uuid4().hex
    return OrderRequest(
        run_id=run_id,
        client_order_id=cid,
        symbol=signal.symbol,
        side=signal.side,
        quantity=signal.quantity,
        order_type=signal.order_type,
        price=signal.price,
    )
