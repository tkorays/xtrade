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
from xtrade.data.market_data import (
    Instrument,
    InstrumentRepository,
    KLineRepository,
    PostgresInstrumentRepository,
    PostgresKLineRepository,
    PostgresTradeCalendarRepository,
    TradeCalendarRepository,
)
from xtrade.data.orm import (
    AccountORM,
    AdjustmentFactorORM,
    DataSyncStateORM,
    InstrumentORM,
    KLine1dORM,
    KLine1mORM,
    OrderORM,
    PositionORM,
    TradeCalendarORM,
    TradeORM,
)
from xtrade.data.orm_base import Base, TimestampMixin
from xtrade.data.sync_state import (
    DataSyncState,
    DataSyncStateRepository,
    PostgresDataSyncStateRepository,
)

__all__ = [
    "AccountORM",
    "AdjustmentFactorORM",
    "Base",
    "DataSyncState",
    "DataSyncStateORM",
    "DataSyncStateRepository",
    "Instrument",
    "InstrumentORM",
    "InstrumentRepository",
    "KLine1dORM",
    "KLine1mORM",
    "KLineRepository",
    "OrderORM",
    "PositionORM",
    "PostgresDataSyncStateRepository",
    "PostgresInstrumentRepository",
    "PostgresKLineRepository",
    "PostgresTradeCalendarRepository",
    "TimestampMixin",
    "TradeCalendarORM",
    "TradeCalendarRepository",
    "TradeORM",
    "create_engine",
    "get_connection",
    "get_engine",
    "get_session",
    "reset_engine",
]
