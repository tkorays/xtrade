"""Broker-data ORM models: orders, trades, positions, account snapshots.

All four tables are accessed via SQLAlchemy ``Session``; see
:mod:`xtrade.data.broker_data` for repositories.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal as D

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from xtrade.data.orm_base import Base, TimestampMixin


class OrderORM(Base, TimestampMixin):
    """A single order's full state machine."""

    __tablename__ = "order"
    __table_args__ = (
        UniqueConstraint("run_id", "client_order_id", name="uq_order_run_id_client_order_id"),
        Index("ix_order_run_id", "run_id"),
        Index("ix_order_symbol", "symbol"),
        Index("ix_order_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # "buy" | "sell"
    quantity: Mapped[D] = mapped_column(Numeric(20, 6), nullable=False)
    price: Mapped[D | None] = mapped_column(Numeric(20, 6), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )  # see OrderState


class TradeORM(Base):
    """A single fill event linked to an order."""

    __tablename__ = "trade"
    __table_args__ = (
        Index("ix_trade_order_id", "order_id"),
        Index("ix_trade_run_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("order.id"), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    price: Mapped[D] = mapped_column(Numeric(20, 6), nullable=False)
    quantity: Mapped[D] = mapped_column(Numeric(20, 6), nullable=False)
    fee: Mapped[D] = mapped_column(Numeric(20, 6), nullable=False, default=D("0"))
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PositionORM(Base):
    """Immutable position snapshot keyed by ``(run_id, symbol, time)``."""

    __tablename__ = "position"
    __table_args__ = (
        UniqueConstraint("run_id", "symbol", "time", name="uq_position_run_id_symbol_time"),
        Index("ix_position_run_id_time", "run_id", "time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quantity: Mapped[D] = mapped_column(Numeric(20, 6), nullable=False)
    avg_price: Mapped[D] = mapped_column(Numeric(20, 6), nullable=False)


class AccountORM(Base):
    """Immutable account snapshot keyed by ``(run_id, time)``."""

    __tablename__ = "account"
    __table_args__ = (
        UniqueConstraint("run_id", "time", name="uq_account_run_id_time"),
        Index("ix_account_run_id_time", "run_id", "time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cash: Mapped[D] = mapped_column(Numeric(20, 6), nullable=False)
    equity: Mapped[D] = mapped_column(Numeric(20, 6), nullable=False)
    margin: Mapped[D] = mapped_column(Numeric(20, 6), nullable=False)


__all__ = [
    "AccountORM",
    "OrderORM",
    "PositionORM",
    "TradeORM",
]
