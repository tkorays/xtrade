"""Concrete risk rules.

Three rules + one composite:

- :class:`OrderSizeLimit` — reject orders whose notional exceeds a cap.
- :class:`PositionLimit` — reject orders whose projected position
  quantity exceeds a per-symbol cap.
- :class:`KillSwitch` — engage when the day's P&L drops below a
  threshold; once engaged, every subsequent signal is rejected.
- :class:`CompositeRiskCheck` — run a list of checks in order,
  short-circuiting on the first violation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from xtrade.risk.base import OrderIntent, RiskCheck, RiskContext, RiskViolationError

if TYPE_CHECKING:
    pass

__all__ = [
    "CompositeRiskCheck",
    "KillSwitch",
    "OrderSizeLimit",
    "PositionLimit",
]


def _abs(x: Decimal) -> Decimal:
    return x if x >= 0 else -x


# ---------------------------------------------------------------------------
# OrderSizeLimit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderSizeLimit:
    """Reject any order whose notional exceeds ``max_notional``."""

    max_notional: Decimal

    def check(self, intent: OrderIntent, ctx: RiskContext) -> None:
        notional = intent.quantity * (intent.price if intent.price is not None else Decimal("1"))
        if notional > self.max_notional:
            raise RiskViolationError(
                rule_name="OrderSizeLimit",
                message=(f"order notional {notional} exceeds max_notional {self.max_notional}"),
            )


# ---------------------------------------------------------------------------
# PositionLimit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PositionLimit:
    """Reject orders whose projected position quantity exceeds ``max_qty``.

    SELL signals are checked against the same cap; a short position is
    NOT allowed by this rule (the broker / business layer governs short
    selling).
    """

    max_qty: Decimal

    def check(self, intent: OrderIntent, ctx: RiskContext) -> None:
        projected = intent.expected_qty_after
        if projected < 0:
            raise RiskViolationError(
                rule_name="PositionLimit",
                message=(
                    f"projected position {projected} for {intent.symbol} is short; "
                    "short selling is not allowed by PositionLimit"
                ),
            )
        if projected > self.max_qty:
            raise RiskViolationError(
                rule_name="PositionLimit",
                message=(
                    f"projected position {projected} for {intent.symbol} exceeds "
                    f"max_qty {self.max_qty}"
                ),
            )


# ---------------------------------------------------------------------------
# KillSwitch
# ---------------------------------------------------------------------------


class KillSwitch:
    """Engage after a daily loss; once engaged, all signals are blocked.

    ``trigger_on_daily_loss`` is a positive number representing the loss
    threshold. ``equity_at_open`` is the start-of-day snapshot of the
    account's equity. When the current equity drops below
    ``equity_at_open - trigger_on_daily_loss``, the switch engages.
    """

    def __init__(
        self,
        trigger_on_daily_loss: Decimal | None,
        *,
        equity_at_open: Decimal | None = None,
    ) -> None:
        self._trigger_on_daily_loss = trigger_on_daily_loss
        self._equity_at_open = equity_at_open
        self._engaged: bool = False

    @property
    def engaged(self) -> bool:
        return self._engaged

    def reset(self) -> None:
        """Re-enable the switch. The next call to :meth:`check` re-evaluates."""
        self._engaged = False

    def check(self, intent: OrderIntent, ctx: RiskContext) -> None:
        if self._engaged:
            raise RiskViolationError(
                rule_name="KillSwitch",
                message="kill switch is engaged; all signals are blocked",
            )
        if self._trigger_on_daily_loss is None:
            return
        current_equity = ctx.account.equity
        baseline = self._equity_at_open if self._equity_at_open is not None else current_equity
        loss = baseline - current_equity
        if loss >= self._trigger_on_daily_loss:
            self._engaged = True
            raise RiskViolationError(
                rule_name="KillSwitch",
                message=(
                    f"daily loss {loss} reached threshold {self._trigger_on_daily_loss}; "
                    "kill switch engaged"
                ),
            )


# ---------------------------------------------------------------------------
# CompositeRiskCheck
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompositeRiskCheck:
    """Run a list of checks in order. Short-circuit on first violation."""

    checks: list[RiskCheck]

    def check(self, intent: OrderIntent, ctx: RiskContext) -> None:
        for check in self.checks:
            check.check(intent, ctx)


# ---------------------------------------------------------------------------
# No-op default (used by the engines when no rules are configured)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NoOpRiskCheck:
    """A risk check that never raises. Useful as a default."""

    def check(self, intent: OrderIntent, ctx: RiskContext) -> None:
        return None


# Mark _abs as used to keep the helper if other rules later want it.
_ = _abs
