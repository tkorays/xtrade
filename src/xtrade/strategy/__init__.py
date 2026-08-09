"""Strategy: signal generation and alpha models.

Define :class:`xtrade.strategy.base.Strategy` and the per-step data
types (:class:`Bar`, :class:`Signal`, :class:`Context`). Concrete
strategies live elsewhere; this package only exposes the Protocol and
the helper :func:`signal_to_order_request`.
"""

from xtrade.strategy.base import (
    Bar,
    Context,
    Signal,
    Strategy,
    signal_to_order_request,
)

__all__ = [
    "Bar",
    "Context",
    "Signal",
    "Strategy",
    "signal_to_order_request",
]
