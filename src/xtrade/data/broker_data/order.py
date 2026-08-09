"""Order repository — state machine + ``(run_id, client_order_id)`` uniqueness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from sqlalchemy.orm import Session

from xtrade.data.engine import get_session
from xtrade.data.orm import OrderORM


class OrderState(StrEnum):
    """Order lifecycle states.

    Allowed transitions are encoded in :data:`ALLOWED_TRANSITIONS` and
    enforced by :meth:`PostgresOrderRepository.update_status`.
    """

    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


# Allowed (from_state, to_state) pairs. Anything not listed is rejected.
ALLOWED_TRANSITIONS: frozenset[tuple[OrderState, OrderState]] = frozenset(
    {
        (OrderState.PENDING, OrderState.SUBMITTED),
        (OrderState.PENDING, OrderState.REJECTED),
        (OrderState.SUBMITTED, OrderState.PARTIAL),
        (OrderState.SUBMITTED, OrderState.FILLED),
        (OrderState.SUBMITTED, OrderState.CANCELLED),
        (OrderState.SUBMITTED, OrderState.EXPIRED),
        (OrderState.PARTIAL, OrderState.FILLED),
        (OrderState.PARTIAL, OrderState.CANCELLED),
        (OrderState.PARTIAL, OrderState.EXPIRED),
    }
)


class OrderStateError(Exception):
    """Raised when :meth:`OrderRepository.update_status` rejects a transition."""

    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"disallowed transition: {current!r} -> {target!r}")
        self.current = current
        self.target = target


@dataclass(frozen=True)
class Order:
    """Plain dataclass exposed to callers."""

    run_id: str
    client_order_id: str
    symbol: str
    side: str  # "buy" | "sell"
    quantity: Decimal
    price: Decimal | None
    status: str
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _to_record(row: OrderORM) -> Order:
    return Order(
        id=row.id,
        run_id=row.run_id,
        client_order_id=row.client_order_id,
        symbol=row.symbol,
        side=row.side,
        quantity=row.quantity,
        price=row.price,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class OrderRepository(Protocol):
    def create(self, record: Order) -> Order: ...

    def get(self, order_id: int) -> Order | None: ...

    def list_by_run(self, run_id: str) -> list[Order]: ...

    def update_status(self, order_id: int, new_status: str) -> None: ...


class PostgresOrderRepository:
    """ORM-backed implementation."""

    def create(self, record: Order) -> Order:
        with get_session() as session:
            row = self._create(session, record)
            session.flush()
            session.refresh(row)
            return _to_record(row)

    @staticmethod
    def _create(session: Session, record: Order) -> OrderORM:
        row = OrderORM(
            run_id=record.run_id,
            client_order_id=record.client_order_id,
            symbol=record.symbol,
            side=record.side,
            quantity=record.quantity,
            price=record.price,
            status=record.status,
        )
        session.add(row)
        return row

    def get(self, order_id: int) -> Order | None:
        with get_session() as session:
            row = session.get(OrderORM, order_id)
            return _to_record(row) if row is not None else None

    def list_by_run(self, run_id: str) -> list[Order]:
        with get_session() as session:
            rows = session.query(OrderORM).filter(OrderORM.run_id == run_id).all()
            return [_to_record(r) for r in rows]

    def update_status(self, order_id: int, new_status: str) -> None:
        with get_session() as session:
            row = session.get(OrderORM, order_id)
            if row is None:
                raise LookupError(f"order not found: {order_id}")
            current = OrderState(row.status)
            target = OrderState(new_status)
            if (current, target) not in ALLOWED_TRANSITIONS:
                raise OrderStateError(current.value, target.value)
            row.status = target.value


__all__ = [
    "ALLOWED_TRANSITIONS",
    "Order",
    "OrderRepository",
    "OrderState",
    "OrderStateError",
    "PostgresOrderRepository",
]
