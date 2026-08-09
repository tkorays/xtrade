"""Account repository — immutable snapshots keyed by ``(run_id, time)``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from xtrade.data.engine import get_session
from xtrade.data.orm import AccountORM


class DuplicateSnapshotError(Exception):
    """Raised when inserting an Account that collides on ``(run_id, time)``."""


@dataclass(frozen=True)
class Account:
    """Plain dataclass exposed to callers."""

    run_id: str
    time: datetime
    cash: Decimal
    equity: Decimal
    margin: Decimal
    id: int | None = None


def _to_record(row: AccountORM) -> Account:
    return Account(
        id=row.id,
        run_id=row.run_id,
        time=row.time,
        cash=row.cash,
        equity=row.equity,
        margin=row.margin,
    )


class AccountRepository(Protocol):
    def create(self, record: Account) -> Account: ...

    def list_by_run(self, run_id: str) -> list[Account]: ...


class PostgresAccountRepository:
    """ORM-backed implementation; no ``update`` method (snapshots are immutable)."""

    def create(self, record: Account) -> Account:
        with get_session() as session:
            try:
                row = self._create(session, record)
                session.flush()
            except IntegrityError as exc:
                raise DuplicateSnapshotError(
                    f"account snapshot already exists: run_id={record.run_id!r} "
                    f"time={record.time.isoformat()!r}"
                ) from exc
            session.refresh(row)
            return _to_record(row)

    @staticmethod
    def _create(session: Session, record: Account) -> AccountORM:
        row = AccountORM(
            run_id=record.run_id,
            time=record.time,
            cash=record.cash,
            equity=record.equity,
            margin=record.margin,
        )
        session.add(row)
        return row

    def list_by_run(self, run_id: str) -> list[Account]:
        with get_session() as session:
            rows = (
                session.query(AccountORM)
                .filter(AccountORM.run_id == run_id)
                .order_by(AccountORM.time)
                .all()
            )
            return [_to_record(r) for r in rows]


__all__ = ["Account", "AccountRepository", "DuplicateSnapshotError", "PostgresAccountRepository"]
