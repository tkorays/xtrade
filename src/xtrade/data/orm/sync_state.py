"""``data_sync_state`` ORM model.

Per-``(source, interval)`` watermark that records the latest successful
data-collection run. One row per pair, with the columns:

- ``source``: the registered :class:`DataSource` name (currently always
  ``"xtquant"``).
- ``interval``: ``"1d"`` or ``"1m"``.
- ``last_trade_date``: the latest trading date whose bars have been
  written; ``NULL`` until the first successful run.
- ``last_run_at``: wall-clock timestamp of the most recent completed
  run (success or failure).
- ``rows_written``: rows persisted by the most recent run.
- ``status``: ``"ok"`` / ``"failed"`` / ``"in_progress"``.
- ``error``: error message when ``status="failed"``, else ``NULL``.

The primary key is the composite ``(source, interval)``; inserts with an
existing key overwrite the row via the repository's upsert semantics.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from xtrade.data.orm_base import Base


class DataSyncStateORM(Base):
    """Per-``(source, interval)`` watermark for data-collection runs."""

    __tablename__ = "data_sync_state"

    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    interval: Mapped[str] = mapped_column(String(8), primary_key=True)
    last_trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rows_written: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = ["DataSyncStateORM"]

# Silence unused-import warning on ``Any`` (used implicitly by ``Mapped``).
_ = Any
