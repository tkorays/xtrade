"""Trade-calendar repository."""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

import pandas as pd
from sqlalchemy.engine import Connection

from xtrade.data.engine import get_connection


class TradeCalendarRepository(Protocol):
    def upsert_days(self, df: pd.DataFrame) -> int:
        """Persist calendar rows; ``df`` must have ``date`` and `` ``is_trading`` columns."""
        ...

    def is_trading_day(self, d: date) -> bool: ...

    def get_trading_days(self, start: date, end: date) -> list[date]: ...


class PostgresTradeCalendarRepository:
    """Reads / writes via raw ``Connection``."""

    REQUIRED_COLUMNS: tuple[str, ...] = ("date", "is_trading")

    def upsert_days(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        missing = [c for c in self.REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"upsert_days: missing required columns: {missing}")
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        rows = [(row.date, bool(row.is_trading)) for row in df.itertuples(index=False)]
        sql = (
            "INSERT INTO trade_calendar (date, is_trading) VALUES (%s, %s)"
            " ON CONFLICT (date) DO UPDATE SET is_trading = EXCLUDED.is_trading"
        )
        with get_connection() as conn:
            raw: Any = conn.connection.driver_connection
            with raw.cursor() as cur:
                cur.executemany(sql, rows)
            return len(rows)

    def is_trading_day(self, d: date) -> bool:
        sql = "SELECT is_trading FROM trade_calendar WHERE date = %s"
        with get_connection() as conn:
            row = conn.exec_driver_sql(sql, (d,)).first()
        if row is None:
            return False
        return bool(row[0])

    def get_trading_days(self, start: date, end: date) -> list[date]:
        sql = (
            "SELECT date FROM trade_calendar"
            " WHERE date >= %s AND date <= %s AND is_trading = TRUE"
            " ORDER BY date"
        )
        with get_connection() as conn:
            rows = conn.exec_driver_sql(sql, (start, end)).fetchall()
        return [r[0] for r in rows]


__all__ = ["PostgresTradeCalendarRepository", "TradeCalendarRepository"]
