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
    text,
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
    __table_args__ = (UniqueConstraint("symbol", "ts", name="uq_kline_1m_symbol_ts"),)

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
    """Trade calendar (one row per ``(exchange, date)`` with ``is_trading`` flag).

    Per-exchange rows allow the underlying legacy dump to be ingested
    losslessly; the repository collapses the per-exchange view with the
    any-exchange rule (``is_trading_day`` returns ``True`` iff any
    exchange marks the date as trading).
    """

    __tablename__ = "trade_calendar"

    exchange: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_trading: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class InstrumentORM(Base):
    """Instrument / contract metadata (small reference table).

    Mirrors the legacy ``mos`` DuckDB reference dump (``instrument_info``).
    ``status`` uses the legacy single-letter codes (``'L'`` for listed,
    ``'D'`` for delisted); the repository does NOT remap.
    """

    __tablename__ = "instrument"

    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    # ``type`` is the legacy DuckDB column (e.g. ``'ETF'``, ``'LOF'``,
    # ``'Stock'``); NOT NULL with a server-side empty-string fallback so
    # future source dumps with NULL values still import cleanly.
    type: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text(""))
    list_date: Mapped[date] = mapped_column(Date, nullable=False)
    delist_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="L")
    list_board: Mapped[str | None] = mapped_column(String(32), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    area: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_t0: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


__all__ = [
    "AdjustmentFactorORM",
    "InstrumentORM",
    "KLine1dORM",
    "KLine1mORM",
    "TradeCalendarORM",
]
