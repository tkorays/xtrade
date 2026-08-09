"""Trade repository — fills linked to orders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy.orm import Session

from xtrade.data.engine import get_session
from xtrade.data.orm import TradeORM


@dataclass(frozen=True)
class Trade:
    """Plain dataclass exposed to callers."""

    order_id: int
    run_id: str
    symbol: str
    price: Decimal
    quantity: Decimal
    fee: Decimal
    time: datetime
    id: int | None = None


def _to_record(row: TradeORM) -> Trade:
    return Trade(
        id=row.id,
        order_id=row.order_id,
        run_id=row.run_id,
        symbol=row.symbol,
        price=row.price,
        quantity=row.quantity,
        fee=row.fee,
        time=row.time,
    )


class TradeRepository(Protocol):
    def create(self, record: Trade) -> Trade: ...

    def list_by_order(self, order_id: int) -> list[Trade]: ...

    def list_by_run(self, run_id: str) -> list[Trade]: ...


class PostgresTradeRepository:
    """ORM-backed implementation."""

    def create(self, record: Trade) -> Trade:
        with get_session() as session:
            row = self._create(session, record)
            session.flush()
            session.refresh(row)
            return _to_record(row)

    @staticmethod
    def _create(session: Session, record: Trade) -> TradeORM:
        row = TradeORM(
            order_id=record.order_id,
            run_id=record.run_id,
            symbol=record.symbol,
            price=record.price,
            quantity=record.quantity,
            fee=record.fee,
            time=record.time,
        )
        session.add(row)
        return row

    def list_by_order(self, order_id: int) -> list[Trade]:
        with get_session() as session:
            rows = (
                session.query(TradeORM)
                .filter(TradeORM.order_id == order_id)
                .order_by(TradeORM.time)
                .all()
            )
            return [_to_record(r) for r in rows]

    def list_by_run(self, run_id: str) -> list[Trade]:
        with get_session() as session:
            rows = (
                session.query(TradeORM)
                .filter(TradeORM.run_id == run_id)
                .order_by(TradeORM.time)
                .all()
            )
            return [_to_record(r) for r in rows]


__all__ = ["PostgresTradeRepository", "Trade", "TradeRepository"]
