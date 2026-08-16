"""``data_sync_state`` repository.

Wraps the :class:`xtrade.data.orm.sync_state.DataSyncStateORM` model
with a Protocol + Postgres implementation. The watermark table is small
(one row per ``(source, interval)``), so it uses the broker-data
pattern: SQLAlchemy 2.x synchronous ``Session`` via
:func:`xtrade.data.engine.get_session`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from xtrade.data.engine import get_session
from xtrade.data.orm.sync_state import DataSyncStateORM

# Valid ``status`` values stored in ``data_sync_state.status``.
STATUS_OK: str = "ok"
STATUS_FAILED: str = "failed"
STATUS_IN_PROGRESS: str = "in_progress"


@dataclass(frozen=True)
class DataSyncState:
    """Plain dataclass exposed to callers (decoupled from ORM).

    Mirrors the columns of ``data_sync_state``. ``last_trade_date`` and
    ``error`` are nullable; ``last_run_at``, ``rows_written``, and
    ``status`` are required.
    """

    source: str
    interval: str
    last_trade_date: date | None
    last_run_at: datetime
    rows_written: int
    status: str
    error: str | None


@runtime_checkable
class DataSyncStateRepository(Protocol):
    def get(self, source: str, interval: str) -> DataSyncState | None:
        """Return the row for ``(source, interval)`` or ``None`` if absent."""
        ...

    def upsert(self, record: DataSyncState) -> None:
        """Insert or replace the row for ``record.source`` / ``record.interval``."""
        ...

    def delete(self, source: str, interval: str) -> bool:
        """Delete the row; return ``True`` if a row was removed, ``False`` otherwise."""
        ...

    def list_all(self) -> list[DataSyncState]:
        """Return every row, ordered by ``(source, interval)``."""
        ...


def _to_record(row: DataSyncStateORM) -> DataSyncState:
    return DataSyncState(
        source=row.source,
        interval=row.interval,
        last_trade_date=row.last_trade_date,
        last_run_at=row.last_run_at,
        rows_written=row.rows_written,
        status=row.status,
        error=row.error,
    )


def _from_record(record: DataSyncState) -> DataSyncStateORM:
    return DataSyncStateORM(
        source=record.source,
        interval=record.interval,
        last_trade_date=record.last_trade_date,
        last_run_at=record.last_run_at,
        rows_written=record.rows_written,
        status=record.status,
        error=record.error,
    )


class PostgresDataSyncStateRepository:
    """ORM-backed implementation of :class:`DataSyncStateRepository`."""

    def get(self, source: str, interval: str) -> DataSyncState | None:
        with get_session() as session:
            return self._get(session, source, interval)

    @staticmethod
    def _get(session: Session, source: str, interval: str) -> DataSyncState | None:
        row = session.get(DataSyncStateORM, (source, interval))
        return _to_record(row) if row is not None else None

    def upsert(self, record: DataSyncState) -> None:
        with get_session() as session:
            self._upsert(session, record)

    @staticmethod
    def _upsert(session: Session, record: DataSyncState) -> None:
        existing = session.get(DataSyncStateORM, (record.source, record.interval))
        if existing is None:
            session.add(_from_record(record))
        else:
            existing.last_trade_date = record.last_trade_date
            existing.last_run_at = record.last_run_at
            existing.rows_written = record.rows_written
            existing.status = record.status
            existing.error = record.error

    def delete(self, source: str, interval: str) -> bool:
        with get_session() as session:
            row = session.get(DataSyncStateORM, (source, interval))
            if row is None:
                return False
            session.delete(row)
            return True

    def list_all(self) -> list[DataSyncState]:
        with get_session() as session:
            rows = (
                session.query(DataSyncStateORM)
                .order_by(DataSyncStateORM.source, DataSyncStateORM.interval)
                .all()
            )
            return [_to_record(row) for row in rows]


__all__ = [
    "STATUS_FAILED",
    "STATUS_IN_PROGRESS",
    "STATUS_OK",
    "DataSyncState",
    "DataSyncStateRepository",
    "PostgresDataSyncStateRepository",
]
