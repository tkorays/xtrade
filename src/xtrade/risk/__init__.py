"""Risk: pre-trade checks and position / exposure limits.

Public surface: :class:`RiskCheck` Protocol, three concrete rules
(:class:`OrderSizeLimit`, :class:`PositionLimit`, :class:`KillSwitch`),
and a :class:`CompositeRiskCheck` combiner. The :class:`RiskViolationError`
exception is the standard way a rule rejects an intent.
"""

from xtrade.risk.base import (
    OrderIntent,
    RiskCheck,
    RiskContext,
    RiskViolationError,
)
from xtrade.risk.checks import (
    CompositeRiskCheck,
    KillSwitch,
    NoOpRiskCheck,
    OrderSizeLimit,
    PositionLimit,
)

__all__ = [
    "CompositeRiskCheck",
    "KillSwitch",
    "NoOpRiskCheck",
    "OrderIntent",
    "OrderSizeLimit",
    "PositionLimit",
    "RiskCheck",
    "RiskContext",
    "RiskViolationError",
]
