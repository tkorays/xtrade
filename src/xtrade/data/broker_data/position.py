"""Position repository — immutable snapshots keyed by ``(run_id, symbol, time)``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from xtrade.data.engine import get_session
from xtrade.data.orm import PositionORM


class DuplicateSnapshotError(Exception):
    """Raised when inserting a Position that collides on ``(run_id, symbol, time)``."""


@dataclass(frozen=True)
class Position:
    """Plain dataclass exposed to callers."""

    run_id: str
    symbol: str
    time: datetime
    quantity: Decimal
    avg_price: Decimal
    id: int | None = None


def _to_record(row: PositionORM) -> Position:
    return Position(
        id=row.id,
        run_id=row.run_id,
        symbol=row.symbol,
        time=row.time,
        quantity=row.quantity,
        avg_price=row.avg_price,
    )


class PositionRepository(Protocol):
    def create(self, record: Position) -> Position: ...

    def list_by_run(self, run_id: str) -> list[Position]: ...


class PostgresPositionRepository:
    """ORM-backed implementation; no ``update`` method (snapshots are immutable)."""

    def create(self, record: Position) -> Position:
        with get_session() as session:
            try:
                row = self._create(session, record)
                session.flush()
            except IntegrityError as exc:
                raise DuplicateSnapshotError(
                    f"position already exists: run_id={record.run_id!r} "
                    f"symbol={record.symbol!r} time={record.time.isoformat()!r}"
                ) from exc
            session.refresh(row)
            return _to_record(row)

    @staticmethod
    def _create(session: Session, record: Position) -> PositionORM:
        row = PositionORM(
            run_id=record.run_id,
            symbol=record.symbol,
            time=record.time,
            quantity=record.quantity,
            avg_price=record.avg_price,
        )
        session.add(row)
        return row

    def list_by_run(self, run_id: str) -> list[Position]:
        with get_session() as session:
            rows = (
                session.query(PositionORM)
                .filter(PositionORM.run_id == run_id)
                .order_by(PositionORM.time, PositionORM.symbol)
                .all()
            )
            return [_to_record(r) for r in rows]


__all__ = ["DuplicateSnapshotError", "Position", "PositionRepository", "PostgresPositionRepository"]
