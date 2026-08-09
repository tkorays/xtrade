"""K-line repository: high-throughput reads/writes via raw ``Connection``.

The :class:`KLineRepository` Protocol defines the public contract.
:class:`PostgresKLineRepository` is the only implementation in this change.

Adjustment logic (backward / forward / none) is computed on the read
path from a discrete factor table (see :mod:`xtrade.data.market_data.adj_factor`).
Raw prices are stored once and never mutated.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

import pandas as pd
from sqlalchemy.engine import Connection

from xtrade.data.engine import get_connection

if TYPE_CHECKING:
    pass

INTERVALS: frozenset[str] = frozenset({"1d", "1m", "5m", "15m", "30m", "60m"})

AdjustMode = Literal["none", "backward", "forward"]

# Required columns on incoming DataFrames for ``upsert_bars``.
KLINE_REQUIRED_COLUMNS: tuple[str, ...] = (
    "symbol",
    "time",
    "interval",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
)


class KLineRepository(Protocol):
    """Contract for K-line persistence.

    Implementations:
    - Read K-line bars for one or more symbols within a time window.
    - Adjust prices on the read path (backward / forward / none).
    - Upsert bulk K-line rows from a ``DataFrame``.

    Implementations SHALL NOT perform external IO (no network / no file IO
    beyond the configured database).
    """

    def upsert_bars(self, df: pd.DataFrame) -> int:
        """Persist the DataFrame's rows; return number of rows written."""
        ...

    def get_bars(
        self,
        symbols: list[str],
        start: date | datetime,
        end: date | datetime,
        interval: str,
        adjust: AdjustMode = "none",
    ) -> dict[str, pd.DataFrame]:
        """Read K-line bars; return dict keyed by symbol, each value indexed by time."""
        ...

    def count(self, symbol: str | None = None, interval: str | None = None) -> int:
        """Return row count (for tests / diagnostics)."""
        ...


def _check_interval(interval: str) -> None:
    if interval not in INTERVALS:
        raise ValueError(f"unsupported interval {interval!r}; expected one of {sorted(INTERVALS)}")


class PostgresKLineRepository:
    """Postgres-backed K-line repository.

    Uses :func:`xtrade.data.engine.get_connection` to borrow a raw
    ``Connection``; no ORM Session is involved. Writes flush in chunks
    of ``Config.data.batch_size`` via ``cursor.copy`` (CSV format).
    """

    def __init__(self, batch_size: int) -> None:
        if batch_size <= 0:
            raise ValueError(f"batch_size must be > 0, got {batch_size}")
        self._batch_size = batch_size

    # ----- writes -----

    def upsert_bars(self, df: pd.DataFrame) -> int:
        """Persist ``df`` rows via ``cursor.copy`` followed by an upsert
        ``INSERT ... ON CONFLICT`` so that re-applied rows update the
        existing tuple instead of failing on the unique constraint.

        ``df`` must include the columns in
        :data:`KLINE_REQUIRED_COLUMNS` plus optional ``pre_close``.
        """
        if df.empty:
            return 0
        missing = [c for c in KLINE_REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"upsert_bars: missing required columns: {missing}")
        # Row-by-row datetime normalisation so psycopg's CSV format does
        # not choke on mixed-precision pandas timestamps.
        df = df.copy()
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df["interval"] = df["interval"].astype(str)
        if "pre_close" not in df.columns:
            df["pre_close"] = pd.NA

        rows_written = 0
        with get_connection() as conn:
            for chunk_start in range(0, len(df), self._batch_size):
                chunk = df.iloc[chunk_start : chunk_start + self._batch_size]
                rows_written += self._upsert_chunk(conn, chunk)
        return rows_written

    def _upsert_chunk(self, conn: Connection, chunk: pd.DataFrame) -> int:
        """Upsert a chunk using a single COPY + ON CONFLICT statement pair.

        Implementation note: ``COPY`` itself cannot upsert, so we
        temporarily insert rows into a staging table, then merge into
        ``kline`` via ``INSERT ... ON CONFLICT ... DO UPDATE``. The
        staging table is created on demand and dropped at the end of the
        statement (so concurrent writers don't collide).
        """
        if chunk.empty:
            return 0

        # Stage rows in a temporary, session-scoped table, then upsert
        # into ``kline`` via ON CONFLICT. Doing this inside a single
        # transaction (the ``get_connection`` context) keeps it atomic.
        staging_sql = (
            "CREATE TEMP TABLE IF NOT EXISTS kline_stage ("
            "  symbol TEXT NOT NULL,"
            "  time TIMESTAMPTZ NOT NULL,"
            "  interval TEXT NOT NULL,"
            "  open NUMERIC(20, 6) NOT NULL,"
            "  high NUMERIC(20, 6) NOT NULL,"
            "  low NUMERIC(20, 6) NOT NULL,"
            "  close NUMERIC(20, 6) NOT NULL,"
            "  volume BIGINT NOT NULL,"
            "  amount NUMERIC(20, 4) NOT NULL,"
            "  pre_close NUMERIC(20, 6)"
            ") ON COMMIT DROP"
        )
        upsert_sql = (
            "INSERT INTO kline (symbol, time, interval, open, high, low, close, volume, amount, pre_close)"
            " SELECT symbol, time, interval, open, high, low, close, volume, amount, pre_close"
            " FROM kline_stage"
            " ON CONFLICT (symbol, time, interval) DO UPDATE SET"
            "   open = EXCLUDED.open,"
            "   high = EXCLUDED.high,"
            "   low = EXCLUDED.low,"
            "   close = EXCLUDED.close,"
            "   volume = EXCLUDED.volume,"
            "   amount = EXCLUDED.amount,"
            "   pre_close = EXCLUDED.pre_close"
        )

        conn.exec_driver_sql(staging_sql)
        self._copy_into_staging(conn, chunk)
        result = conn.exec_driver_sql(upsert_sql)
        # ``cursor.rowcount`` is set after the underlying psycopg cursor
        # is consumed; SQLAlchemy's ``Connection`` exposes it via
        # ``result.rowcount``.
        return int(result.rowcount or 0)

    @staticmethod
    def _copy_into_staging(conn: Connection, chunk: pd.DataFrame) -> None:
        """Stream ``chunk`` into the ``kline_stage`` temp table via ``COPY FROM STDIN``."""
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        for row in chunk.itertuples(index=False, name=None):
            # ``itertuples`` returns Python objects; normalise time and
            # decimals so psycopg's CSV reader accepts them.
            out: list[object] = []
            for value in row:
                if isinstance(value, pd.Timestamp):
                    out.append(value.to_pydatetime().isoformat())
                elif (isinstance(value, float) and pd.isna(value)) or pd.isna(value):
                    out.append(None)
                else:
                    out.append(value)
            writer.writerow(out)
        raw: Any = conn.connection.driver_connection  # psycopg.Connection
        with raw.cursor() as cursor:
            cursor.copy_expert(
                "COPY kline_stage (symbol, time, interval, open, high, low, close, volume, amount, pre_close)"
                " FROM STDIN WITH (FORMAT csv)",
                io.StringIO(buf.getvalue()),
            )

    # ----- reads -----

    def get_bars(
        self,
        symbols: list[str],
        start: date | datetime,
        end: date | datetime,
        interval: str,
        adjust: AdjustMode = "none",
    ) -> dict[str, pd.DataFrame]:
        """Read K-line bars for ``symbols`` in ``[start, end]`` at ``interval``."""
        _check_interval(interval)
        if not symbols:
            return {}

        sql = (
            "SELECT symbol, time, open, high, low, close, volume, amount, pre_close"
            " FROM kline"
            " WHERE interval = %(interval)s"
            "   AND symbol = ANY(%(symbols)s)"
            "   AND time >= %(start)s"
            "   AND time <= %(end)s"
            " ORDER BY symbol, time"
        )
        params: dict[str, Any] = {
            "interval": interval,
            "symbols": list(symbols),
            "start": start,
            "end": end,
        }
        with get_connection() as conn:
            df = pd.read_sql(
                sql,
                conn,
                params=params,
            )
        if df.empty:
            return {s: pd.DataFrame() for s in symbols}

        df["time"] = pd.to_datetime(df["time"], utc=True)
        result: dict[str, pd.DataFrame] = {}
        for sym_obj, group in df.groupby("symbol", sort=False):
            sym = cast("str", sym_obj)
            sym_df = group.drop(columns=["symbol"]).set_index("time").sort_index()
            result[sym] = sym_df

        if adjust in ("backward", "forward"):
            result = self._apply_adjustment(result, symbols, start, end, interval, adjust)

        # Ensure every requested symbol appears in the result, even if empty.
        return {s: result.get(s, pd.DataFrame()) for s in symbols}

    @staticmethod
    def _apply_adjustment(
        frames: dict[str, pd.DataFrame],
        symbols: list[str],
        start: date | datetime,
        end: date | datetime,
        interval: str,
        mode: Literal["backward", "forward"],
    ) -> dict[str, pd.DataFrame]:
        """Multiply price columns by the cumulative factor (ffill).

        backward: factor / factor_at_first_bar in window
        forward:  factor / factor_at_last_bar in window
        """
        from xtrade.data.market_data.adj_factor import PostgresAdjustmentFactorRepository

        adj_repo = PostgresAdjustmentFactorRepository()
        start_d = start.date() if isinstance(start, datetime) else start
        end_d = end.date() if isinstance(end, datetime) else end
        adj = adj_repo.get(list(frames.keys()), start_d, end_d)
        price_cols = ("open", "high", "low", "close")
        for sym, sym_df in frames.items():
            if sym_df.empty or sym not in adj or adj[sym].empty:
                continue
            factors = adj[sym]
            factor_map = dict(zip(factors["ex_date"], factors["factor"], strict=True))
            bar_dates = pd.to_datetime(sym_df.index).date
            adj_series = pd.Series(
                [Decimal(str(factor_map.get(d, "1"))) for d in bar_dates],
                index=sym_df.index,
                dtype="object",
            ).astype(float)
            if mode == "backward":
                base = adj_series.iloc[0]
                if base:
                    adj_series = adj_series / base
            else:  # forward
                last = adj_series.iloc[-1]
                if last:
                    adj_series = adj_series / last
            for col in price_cols:
                if col in sym_df.columns:
                    sym_df[col] = sym_df[col].astype(float) * adj_series
        return frames

    def count(self, symbol: str | None = None, interval: str | None = None) -> int:
        """Return the row count matching the given filters."""
        clauses: list[str] = []
        params: dict[str, object] = {}
        if symbol is not None:
            clauses.append("symbol = %(symbol)s")
            params["symbol"] = symbol
        if interval is not None:
            _check_interval(interval)
            clauses.append("interval = %(interval)s")
            params["interval"] = interval
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT COUNT(*) FROM kline {where}"
        with get_connection() as conn:
            return int(conn.exec_driver_sql(sql, params).scalar() or 0)


__all__ = ["INTERVALS", "KLineRepository", "PostgresKLineRepository"]
