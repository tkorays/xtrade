"""Market-data repositories.

Public surface: protocols + Postgres implementations for K-line, adjustment
factor, trade calendar, and instrument. All time-series repositories borrow
a raw ``Connection`` via :func:`xtrade.data.engine.get_connection` so that
``cursor.copy`` / ``pd.read_sql`` can run without ORM overhead.
"""

from xtrade.data.market_data.adj_factor import (
    AdjustmentFactorRepository,
    PostgresAdjustmentFactorRepository,
)
from xtrade.data.market_data.instrument import (
    Instrument,
    InstrumentRepository,
    PostgresInstrumentRepository,
)
from xtrade.data.market_data.kline import (
    INTERVALS,
    KLineRepository,
    PostgresKLineRepository,
)
from xtrade.data.market_data.trade_calendar import (
    PostgresTradeCalendarRepository,
    TradeCalendarRepository,
)

__all__ = [
    "INTERVALS",
    "AdjustmentFactorRepository",
    "Instrument",
    "InstrumentRepository",
    "KLineRepository",
    "PostgresAdjustmentFactorRepository",
    "PostgresInstrumentRepository",
    "PostgresKLineRepository",
    "PostgresTradeCalendarRepository",
    "TradeCalendarRepository",
]
