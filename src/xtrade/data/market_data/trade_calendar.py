"""Trade-calendar repository."""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

import pandas as pd

from xtrade.data.engine import get_connection


class TradeCalendarRepository(Protocol):
    def upsert_days(self, df: pd.DataFrame) -> int:
        """Persist calendar rows; ``df`` must have ``exchange``, ``date`` and ``is_trading`` columns."""
        ...

    def is_trading_day(self, d: date) -> bool:
        """``True`` iff any exchange row marks ``d`` as trading."""
        ...

    def get_trading_days(self, start: date, end: date) -> list[date]:
        """Union of dates (across exchanges) marked as trading, ascending, deduplicated."""
        ...


class PostgresTradeCalendarRepository:
    """Reads / writes via raw ``Connection``."""

    REQUIRED_COLUMNS: tuple[str, ...] = ("exchange", "date", "is_trading")

    def upsert_days(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        missing = [c for c in self.REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"upsert_days: missing required columns: {missing}")
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        rows = [
            (str(row.exchange), row.date, bool(row.is_trading))
            for row in df.itertuples(index=False)
        ]
        sql = (
            "INSERT INTO trade_calendar (exchange, date, is_trading)"
            " VALUES (%s, %s, %s)"
            " ON CONFLICT (exchange, date) DO UPDATE SET is_trading = EXCLUDED.is_trading"
        )
        with get_connection() as conn:
            raw: Any = conn.connection.driver_connection
            with raw.cursor() as cur:
                cur.executemany(sql, rows)
            return len(rows)

    def is_trading_day(self, d: date) -> bool:
        """Return ``True`` iff any exchange row marks ``d`` as trading."""
        # ``EXISTS`` is the canonical "any row matches" predicate in
        # Postgres; combined with the ``is_trading = TRUE`` filter it
        # implements the any-exchange rule. ``rowcount`` from
        # ``SELECT EXISTS(...)`` is 1 when at least one matching row
        # exists, 0 otherwise — we expose this as a boolean.
        sql = (
            "SELECT EXISTS (  SELECT 1 FROM trade_calendar  WHERE date = %s AND is_trading = TRUE)"
        )
        with get_connection() as conn:
            row = conn.exec_driver_sql(sql, (d,)).first()
        return bool(row[0]) if row is not None else False

    def get_trading_days(self, start: date, end: date) -> list[date]:
        """Return the union of trading dates across all exchanges.

        ``DISTINCT`` deduplicates the per-exchange rows into a single
        ascending list of dates marked as trading on any exchange.
        """
        sql = (
            "SELECT DISTINCT date FROM trade_calendar"
            " WHERE date >= %s AND date <= %s AND is_trading = TRUE"
            " ORDER BY date"
        )
        with get_connection() as conn:
            rows = conn.exec_driver_sql(sql, (start, end)).fetchall()
        return [r[0] for r in rows]


__all__ = ["PostgresTradeCalendarRepository", "TradeCalendarRepository"]
