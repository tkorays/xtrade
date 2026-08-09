"""Engine clock types and shared errors.

Engine-owned summary type (:class:`RunSummary`) and the engine-side
exception (:class:`EngineUsageError`) for the one rule the engine
mandates: strategies MUST NOT submit orders from ``on_init``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from xtrade.data.broker_data import Account

__all__ = ["EngineUsageError", "RunSummary"]


class EngineUsageError(RuntimeError):
    """Raised when a strategy violates the engine's usage contract.

    Currently raised when a strategy calls ``broker.submit_order`` from
    ``on_init`` (which is setup-only) and the engine detects this.
    """


@dataclass(frozen=True)
class RunSummary:
    """The terminal state of a backtest run.

    ``n_orders`` is the number of orders submitted to the broker
    (regardless of fill status). ``n_fills`` is the number of trades
    produced by ``broker.advance`` over the run. ``n_dropped_signals``
    is the number of signals rejected by a risk rule.
    """

    initial_account: Account
    final_account: Account
    n_orders: int
    n_fills: int
    n_dropped_signals: int
    start: datetime
    end: datetime
