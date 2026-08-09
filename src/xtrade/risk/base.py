"""Risk: pre-trade checks and position / exposure limits.

Defines the :class:`RiskCheck` Protocol plus the input types
(:class:`OrderIntent`, :class:`RiskContext`) and the standard
:class:`RiskViolationError` exception. Concrete rules live in
:mod:`xtrade.risk.checks`.

A :class:`RiskCheck` is invoked once per signal by the engine before
the signal is submitted to the broker. A check that rejects a signal
raises :class:`RiskViolationError`; the engine drops the signal and
records the reason. A check that does not raise returns ``None`` and
the engine treats the signal as approved.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from xtrade.data.broker_data import Account, Position
from xtrade.execution.broker import OrderSide

__all__ = [
    "OrderIntent",
    "RiskCheck",
    "RiskContext",
    "RiskViolationError",
]


@dataclass(frozen=True)
class OrderIntent:
    """The risk-layer view of a strategy's signal.

    ``expected_qty_after`` is the broker's projected position quantity
    after this signal is filled. Risk rules use this to check caps
    without having to talk to the broker (the broker is not in
    :class:`RiskContext`).
    """

    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal | None
    expected_qty_after: Decimal


@dataclass(frozen=True)
class RiskContext:
    """Read-only context for risk checks.

    ``RiskContext`` deliberately does NOT expose ``Broker``; rules that
    need to call the broker are a layering violation.
    """

    account: Account
    positions: Mapping[str, Position]
    now: datetime


class RiskViolationError(RuntimeError):
    """Raised by a rule when an :class:`OrderIntent` is rejected.

    ``rule_name`` is the class name of the firing rule. ``message`` is
    a human-readable reason — it is logged by the engine and may be
    surfaced through the run summary.
    """

    def __init__(self, rule_name: str, message: str) -> None:
        super().__init__(f"{rule_name}: {message}")
        self.rule_name = rule_name
        self.message = message


@runtime_checkable
class RiskCheck(Protocol):
    """A pre-trade check invoked once per signal."""

    def check(self, intent: OrderIntent, ctx: RiskContext) -> None:
        """Return ``None`` to approve; raise :class:`RiskViolationError` to reject."""
        ...
