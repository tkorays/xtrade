"""Broker-data repositories.

Public surface: protocols + Postgres ORM implementations for orders,
trades, positions, account snapshots. All implementations use
:class:`sqlalchemy.orm.Session` because the volume is low and the
relationships benefit from ORM machinery.
"""

from xtrade.data.broker_data.account import (
    Account,
    AccountRepository,
    DuplicateSnapshotError,
    PostgresAccountRepository,
)
from xtrade.data.broker_data.order import (
    Order,
    OrderRepository,
    OrderState,
    OrderStateError,
    PostgresOrderRepository,
)
from xtrade.data.broker_data.position import (
    Position,
    PositionRepository,
    PostgresPositionRepository,
)
from xtrade.data.broker_data.trade import (
    PostgresTradeRepository,
    Trade,
    TradeRepository,
)

__all__ = [
    "Account",
    "AccountRepository",
    "DuplicateSnapshotError",
    "Order",
    "OrderRepository",
    "OrderState",
    "OrderStateError",
    "Position",
    "PositionRepository",
    "PostgresAccountRepository",
    "PostgresOrderRepository",
    "PostgresPositionRepository",
    "PostgresTradeRepository",
    "Trade",
    "TradeRepository",
]
