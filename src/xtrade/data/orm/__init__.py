"""SQLAlchemy ORM models for the data layer.

Two ORM namespaces:
    - :mod:`xtrade.data.orm.market`: K-line, adjustment factor, trade
      calendar, instrument. The first three are accessed via raw
      ``Connection`` even though ORM models exist (so Alembic can
      introspect metadata). Instrument is small enough to use ORM
      directly.
    - :mod:`xtrade.data.orm.broker`: order, trade, position, account.
      Always accessed via SQLAlchemy ``Session``.
    - :mod:`xtrade.data.orm.sync_state`: data-collection watermark.
      Accessed via SQLAlchemy ``Session`` (small reference table).
"""

from xtrade.data.orm.broker import (
    AccountORM,
    OrderORM,
    PositionORM,
    TradeORM,
)
from xtrade.data.orm.market import (
    AdjustmentFactorORM,
    InstrumentORM,
    KLine1dORM,
    KLine1mORM,
    TradeCalendarORM,
)
from xtrade.data.orm.sync_state import DataSyncStateORM

__all__ = [
    "AccountORM",
    "AdjustmentFactorORM",
    "DataSyncStateORM",
    "InstrumentORM",
    "KLine1dORM",
    "KLine1mORM",
    "OrderORM",
    "PositionORM",
    "TradeCalendarORM",
    "TradeORM",
]
