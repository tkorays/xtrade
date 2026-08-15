"""Instrument repository — small reference table, allowed to use ORM."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from xtrade.data.engine import get_session
from xtrade.data.orm import InstrumentORM


@dataclass(frozen=True)
class Instrument:
    """Plain dataclass exposed to callers (decoupled from ORM).

    Mirrors the columns stored in Postgres ``instrument`` (and the legacy
    ``mos`` DuckDB reference dump, minus ``price_tick`` which is uniformly
    ``0`` in the source). ``status`` uses the legacy single-letter codes
    (``'L'`` for listed, ``'D'`` for delisted); the repository does NOT
    remap.
    """

    symbol: str
    name: str
    exchange: str
    type: str
    list_date: date
    delist_date: date | None
    status: str
    list_board: str | None = None
    industry: str | None = None
    area: str | None = None
    is_t0: bool = False


@runtime_checkable
class InstrumentRepository(Protocol):
    def upsert(self, record: Instrument) -> None: ...

    def get(self, symbol: str) -> Instrument | None: ...

    def list_all(self) -> list[Instrument]: ...


def _to_record(row: InstrumentORM) -> Instrument:
    return Instrument(
        symbol=row.symbol,
        name=row.name,
        exchange=row.exchange,
        type=row.type,
        list_date=row.list_date,
        delist_date=row.delist_date,
        status=row.status,
        list_board=row.list_board,
        industry=row.industry,
        area=row.area,
        is_t0=row.is_t0,
    )


class PostgresInstrumentRepository:
    """ORM-backed implementation (allowed because instruments are small)."""

    def upsert(self, record: Instrument) -> None:
        with get_session() as session:
            self._upsert(session, record)

    @staticmethod
    def _upsert(session: Session, record: Instrument) -> None:
        existing = session.get(InstrumentORM, record.symbol)
        if existing is None:
            session.add(
                InstrumentORM(
                    symbol=record.symbol,
                    name=record.name,
                    exchange=record.exchange,
                    type=record.type,
                    list_date=record.list_date,
                    delist_date=record.delist_date,
                    status=record.status,
                    list_board=record.list_board,
                    industry=record.industry,
                    area=record.area,
                    is_t0=record.is_t0,
                )
            )
        else:
            existing.name = record.name
            existing.exchange = record.exchange
            existing.type = record.type
            existing.list_date = record.list_date
            existing.delist_date = record.delist_date
            existing.status = record.status
            existing.list_board = record.list_board
            existing.industry = record.industry
            existing.area = record.area
            existing.is_t0 = record.is_t0

    def get(self, symbol: str) -> Instrument | None:
        with get_session() as session:
            row = session.get(InstrumentORM, symbol)
            return _to_record(row) if row is not None else None

    def list_all(self) -> list[Instrument]:
        with get_session() as session:
            return [_to_record(row) for row in session.query(InstrumentORM).all()]


__all__ = ["Instrument", "InstrumentRepository", "PostgresInstrumentRepository"]
