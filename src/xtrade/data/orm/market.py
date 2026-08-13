"""Market-data ORM models.

These models exist primarily so Alembic can introspect the table schema
and so future code can map row tuples into typed objects when convenient.
The hot paths (K-line, adjustment factor, trade calendar) read and write
via raw ``Connection`` — see :mod:`xtrade.data.market_data`.

K-lines are stored in two physical tables, one per supported frequency:
``kline_1d`` (daily) and ``kline_1m`` (1-minute). The ``interval`` column
is implicit (encoded by the table name) and no ``pre_close`` column is
kept.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal as D

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from xtrade.data.orm_base import Base


class KLine1dORM(Base):
    """K-line daily bar (one row per ``(symbol, trade_date)``)."""

    __tablename__ = "kline_1d"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", name="uq_kline_1d_symbol_trade_date"),
        Index("ix_kline_1d_symbol_trade_date", "symbol", "trade_date"),
    )

    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[D] = mapped_column(Numeric(20, 6), nullable=False)
    high: Mapped[D] = mapped_column(Numeric(20, 6), nullable=False)
    low: Mapped[D] = mapped_column(Numeric(20, 6), nullable=False)
    close: Mapped[D] = mapped_column(Numeric(20, 6), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[D] = mapped_column(Numeric(20, 4), nullable=False)


class KLine1mORM(Base):
    """K-line 1-minute bar (one row per ``(symbol, ts)``).

    The underlying ``kline_1m`` table is a TimescaleDB **hypertable**
    (see ``0001_initial.py`` for the DDL) with ``chunk_time_interval = 1 day``
    and a 7-day compression policy segmentby ``symbol``. The redundant
    ``(symbol, ts)`` index is intentionally omitted from ``__table_args__``
    because TimescaleDB creates an equivalent index for chunk metadata.
    """

    __tablename__ = "kline_1m"
    __table_args__ = (
        UniqueConstraint("symbol", "ts", name="uq_kline_1m_symbol_ts"),
    )

    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[D] = mapped_column(Numeric(20, 6), nullable=False)
    high: Mapped[D] = mapped_column(Numeric(20, 6), nullable=False)
    low: Mapped[D] = mapped_column(Numeric(20, 6), nullable=False)
    close: Mapped[D] = mapped_column(Numeric(20, 6), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[D] = mapped_column(Numeric(20, 4), nullable=False)


class AdjustmentFactorORM(Base):
    """Discrete adjustment factor (one row per dividend / split event)."""

    __tablename__ = "adjustment_factor"
    __table_args__ = (
        UniqueConstraint("symbol", "ex_date", name="uq_adjustment_factor_symbol_ex_date"),
        Index("ix_adjustment_factor_symbol", "symbol"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    factor: Mapped[D] = mapped_column(Numeric(20, 8), nullable=False)


class TradeCalendarORM(Base):
    """Trade calendar (one row per calendar date with ``is_trading`` flag)."""

    __tablename__ = "trade_calendar"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_trading: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class InstrumentORM(Base):
    """Instrument / contract metadata (small reference table)."""

    __tablename__ = "instrument"

    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    list_date: Mapped[date] = mapped_column(Date, nullable=False)
    delist_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")


__all__ = [
    "AdjustmentFactorORM",
    "InstrumentORM",
    "KLine1dORM",
    "KLine1mORM",
    "TradeCalendarORM",
]
