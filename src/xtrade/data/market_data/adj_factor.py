"""Adjustment-factor repository.

Discrete factors are stored once per dividend / split event; the read
path (:meth:`xtrade.data.market_data.kline.PostgresKLineRepository.get_bars`)
turns them into cumulative per-bar series and multiplies the price
columns.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Protocol, cast

import pandas as pd

from xtrade.data.engine import get_connection


class AdjustmentFactorRepository(Protocol):
    def upsert(self, df: pd.DataFrame) -> int:
        """Persist factor rows; return count persisted."""
        ...

    def get(self, symbols: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
        """Read factors for symbols in ``[start, end]``; dict keyed by symbol."""
        ...


class PostgresAdjustmentFactorRepository:
    """Postgres implementation; reads / writes via raw ``Connection``."""

    REQUIRED_COLUMNS: tuple[str, ...] = ("symbol", "ex_date", "factor")

    def upsert(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        missing = [c for c in self.REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"upsert: missing required columns: {missing}")
        # Cast dates to ``date`` so psycopg binds them as DATE.
        df = df.copy()
        df["ex_date"] = pd.to_datetime(df["ex_date"]).dt.date
        # Convert factors to ``Decimal`` via a Python list, then wrap in
        # a ``pd.Series`` with ``dtype=object`` so pandas-stubs doesn't
        # reject ``Decimal`` against its strict ``.apply`` allowlist.
        factor_values: list[Decimal] = [Decimal(str(v)) for v in df["factor"].tolist()]
        df["factor"] = pd.Series(factor_values, index=df.index, dtype=object)

        rows = [(row.symbol, row.ex_date, row.factor) for row in df.itertuples(index=False)]
        sql = (
            "INSERT INTO adjustment_factor (symbol, ex_date, factor) VALUES (%s, %s, %s)"
            " ON CONFLICT (symbol, ex_date) DO UPDATE SET factor = EXCLUDED.factor"
        )
        with get_connection() as conn:
            raw: Any = conn.connection.driver_connection
            with raw.cursor() as cur:
                cur.executemany(sql, rows)
            return len(rows)

    def get(self, symbols: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
        if not symbols:
            return {}
        sql = (
            "SELECT symbol, ex_date, factor FROM adjustment_factor"
            " WHERE symbol = ANY(%(symbols)s)"
            "   AND ex_date >= %(start)s"
            "   AND ex_date <= %(end)s"
            " ORDER BY symbol, ex_date"
        )
        params: dict[str, Any] = {"symbols": list(symbols), "start": start, "end": end}
        with get_connection() as conn:
            df = pd.read_sql(sql, conn, params=params)
        if df.empty:
            return {s: pd.DataFrame(columns=["ex_date", "factor"]) for s in symbols}
        result: dict[str, pd.DataFrame] = {}
        for sym_obj, group in df.groupby("symbol", sort=False):
            sym = cast("str", sym_obj)
            result[sym] = group.drop(columns=["symbol"]).reset_index(drop=True)
        return {s: result.get(s, pd.DataFrame(columns=["ex_date", "factor"])) for s in symbols}


__all__ = ["AdjustmentFactorRepository", "PostgresAdjustmentFactorRepository"]
