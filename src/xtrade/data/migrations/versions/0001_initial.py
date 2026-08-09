"""initial data schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-09

Creates every table required by :mod:`xtrade.data.orm`:

- kline_1d, kline_1m, adjustment_factor, trade_calendar, instrument (market data)
- order, trade, position, account (broker data)

Unique constraints and indexes mirror the ORM metadata. This migration is
intentionally data-free: no ``INSERT`` / ``bulk_insert`` calls.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- market data ----

    op.create_table(
        "kline_1d",
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(20, 6), nullable=False),
        sa.Column("high", sa.Numeric(20, 6), nullable=False),
        sa.Column("low", sa.Numeric(20, 6), nullable=False),
        sa.Column("close", sa.Numeric(20, 6), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "trade_date", name="pk_kline_1d"),
        sa.UniqueConstraint("symbol", "trade_date", name="uq_kline_1d_symbol_trade_date"),
    )
    op.create_index("ix_kline_1d_symbol_trade_date", "kline_1d", ["symbol", "trade_date"])

    op.create_table(
        "kline_1m",
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(20, 6), nullable=False),
        sa.Column("high", sa.Numeric(20, 6), nullable=False),
        sa.Column("low", sa.Numeric(20, 6), nullable=False),
        sa.Column("close", sa.Numeric(20, 6), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "ts", name="pk_kline_1m"),
        sa.UniqueConstraint("symbol", "ts", name="uq_kline_1m_symbol_ts"),
    )
    op.create_index("ix_kline_1m_symbol_ts", "kline_1m", ["symbol", "ts"])

    op.create_table(
        "adjustment_factor",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("factor", sa.Numeric(20, 8), nullable=False),
        sa.UniqueConstraint("symbol", "ex_date", name="uq_adjustment_factor_symbol_ex_date"),
    )
    op.create_index("ix_adjustment_factor_symbol", "adjustment_factor", ["symbol"])

    op.create_table(
        "trade_calendar",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column(
            "is_trading",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )

    op.create_table(
        "instrument",
        sa.Column("symbol", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("exchange", sa.String(length=16), nullable=False),
        sa.Column("list_date", sa.Date(), nullable=False),
        sa.Column("delist_date", sa.Date(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="active",
        ),
    )

    # ---- broker data ----

    op.create_table(
        "order",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("client_order_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("price", sa.Numeric(20, 6), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("run_id", "client_order_id", name="uq_order_run_id_client_order_id"),
    )
    op.create_index("ix_order_run_id", "order", ["run_id"])
    op.create_index("ix_order_symbol", "order", ["symbol"])
    op.create_index("ix_order_status", "order", ["status"])

    op.create_table(
        "trade",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("price", sa.Numeric(20, 6), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column(
            "fee",
            sa.Numeric(20, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["order.id"], name="fk_trade_order_id"),
    )
    op.create_index("ix_trade_order_id", "trade", ["order_id"])
    op.create_index("ix_trade_run_id", "trade", ["run_id"])

    op.create_table(
        "position",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("avg_price", sa.Numeric(20, 6), nullable=False),
        sa.UniqueConstraint("run_id", "symbol", "time", name="uq_position_run_id_symbol_time"),
    )
    op.create_index("ix_position_run_id_time", "position", ["run_id", "time"])

    op.create_table(
        "account",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cash", sa.Numeric(20, 6), nullable=False),
        sa.Column("equity", sa.Numeric(20, 6), nullable=False),
        sa.Column("margin", sa.Numeric(20, 6), nullable=False),
        sa.UniqueConstraint("run_id", "time", name="uq_account_run_id_time"),
    )
    op.create_index("ix_account_run_id_time", "account", ["run_id", "time"])


def downgrade() -> None:
    op.drop_index("ix_account_run_id_time", table_name="account")
    op.drop_table("account")

    op.drop_index("ix_position_run_id_time", table_name="position")
    op.drop_table("position")

    op.drop_index("ix_trade_run_id", table_name="trade")
    op.drop_index("ix_trade_order_id", table_name="trade")
    op.drop_table("trade")

    op.drop_index("ix_order_status", table_name="order")
    op.drop_index("ix_order_symbol", table_name="order")
    op.drop_index("ix_order_run_id", table_name="order")
    op.drop_table("order")

    op.drop_table("instrument")
    op.drop_table("trade_calendar")

    op.drop_index("ix_adjustment_factor_symbol", table_name="adjustment_factor")
    op.drop_table("adjustment_factor")

    op.drop_index("ix_kline_1m_symbol_ts", table_name="kline_1m")
    op.drop_table("kline_1m")

    op.drop_index("ix_kline_1d_symbol_trade_date", table_name="kline_1d")
    op.drop_table("kline_1d")
