"""data_sync_state table

Revision ID: 0002_data_sync_state
Revises: 0001_initial
Create Date: 2026-08-16

Adds the per-``(source, interval)`` watermark table used by
:class:`xtrade.data.collection.xtquant.DailyXtQuantCollector` to
track the latest successful data-collection run.

Additive only: no ``INSERT`` / ``bulk_insert`` calls. Seeding happens at
runtime when the collector's first ``run`` advances a watermark.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_data_sync_state"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_sync_state",
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("interval", sa.String(length=8), nullable=False),
        sa.Column("last_trade_date", sa.Date(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "rows_written",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("source", "interval", name="pk_data_sync_state_source_interval"),
    )


def downgrade() -> None:
    op.drop_table("data_sync_state")
