"""xtrade data layer.

Two-path storage contract:
- Time-series data (K-line, adjustment factor, trade calendar) reads and
  writes through native ``psycopg.Connection`` operations (``pd.read_sql``,
  ``cursor.copy``, ``executemany`` + ``INSERT ... ON CONFLICT``); the
  SQLAlchemy ORM is **not** used for unit-of-work on these tables.
- Small reference data (instruments) and broker data (orders, trades,
  positions, account) read and write through SQLAlchemy 2.x synchronous
  ORM + ``Session``.

All paths share the same :class:`sqlalchemy.Engine` (and therefore the
same connection pool). The split lives in :func:`get_session` vs
:func:`get_connection` rather than in separate engines.
"""

from xtrade.data.engine import (
    create_engine,
    get_connection,
    get_engine,
    get_session,
    reset_engine,
)
from xtrade.data.orm import (
    AccountORM,
    AdjustmentFactorORM,
    InstrumentORM,
    KLine1dORM,
    KLine1mORM,
    OrderORM,
    PositionORM,
    TradeCalendarORM,
    TradeORM,
)
from xtrade.data.orm_base import Base, TimestampMixin

__all__ = [
    "AccountORM",
    "AdjustmentFactorORM",
    "Base",
    "InstrumentORM",
    "KLine1dORM",
    "KLine1mORM",
    "OrderORM",
    "PositionORM",
    "TimestampMixin",
    "TradeCalendarORM",
    "TradeORM",
    "create_engine",
    "get_connection",
    "get_engine",
    "get_session",
    "reset_engine",
]
