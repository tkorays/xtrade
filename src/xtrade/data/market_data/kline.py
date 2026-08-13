"""K-line repository: high-throughput reads/writes via raw ``Connection``.

The :class:`KLineRepository` Protocol defines the public contract.
:class:`PostgresKLineRepository` is the only implementation in this change.

K-lines are stored in two physical tables, one per supported frequency:
``kline_1d`` (daily, primary key ``(symbol, trade_date)``) and ``kline_1m``
(1-minute, primary key ``(symbol, ts)``). The repository routes reads and
writes to the correct table based on the ``interval`` argument; the public
``Protocol`` interface is unchanged.

Adjustment logic (backward / forward / none) is computed on the read
path from a discrete factor table (see :mod:`xtrade.data.market_data.adj_factor`).
Raw prices are stored once and never mutated. No ``pre_close`` column is
kept; callers that need the previous bar's close must compute it themselves
(e.g. ``df["close"].shift(1)``).
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol, cast

import pandas as pd
from sqlalchemy.engine import Connection

from xtrade.data.engine import get_connection

INTERVALS: frozenset[str] = frozenset({"1d", "1m"})

AdjustMode = Literal["none", "backward", "forward"]

# Mapping from a supported interval to (physical table name, time column name).
_ROUTE: dict[str, tuple[str, str]] = {
    "1d": ("kline_1d", "trade_date"),
    "1m": ("kline_1m", "ts"),
}

# Required columns on incoming DataFrames for ``upsert_bars``.
# Note: ``interval`` is still required because it drives the routing decision;
# the value is not persisted.
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


def _route_table(interval: str) -> tuple[str, str]:
    """Resolve ``interval`` to ``(table_name, time_column)``.

    Raises ``ValueError`` for unknown intervals.
    """
    _check_interval(interval)
    return _ROUTE[interval]


class PostgresKLineRepository:
    """Postgres-backed K-line repository.

    Uses :func:`xtrade.data.engine.get_connection` to borrow a raw
    ``Connection``; no ORM Session is involved. Writes flush in chunks
    of ``Config.data.batch_size`` via ``cursor.copy`` (CSV format).

    ``kline_1m`` is a TimescaleDB **hypertable** (see the ``0001_initial``
    migration); TimescaleDB chunk pruning and compression are transparent
    to this repository — the ``COPY`` + ``INSERT ... ON CONFLICT`` write
    path and the time-range read path work identically against a
    hypertable as against a regular Postgres table.
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
        :data:`KLINE_REQUIRED_COLUMNS`. The ``interval`` column must be
        uniform across the frame (it determines the target table); a
        mixed frame raises ``ValueError``.
        """
        if df.empty:
            return 0
        missing = [c for c in KLINE_REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"upsert_bars: missing required columns: {missing}")
        # ``interval`` must be uniform — the physical table is implied by it.
        intervals = df["interval"].astype(str).unique()
        if len(intervals) != 1:
            raise ValueError(
                f"upsert_bars: interval column must be uniform, got {sorted(intervals.tolist())}"
            )
        interval = intervals[0]
        table, time_col = _route_table(interval)

        df = df.copy()
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df["interval"] = df["interval"].astype(str)
        # Rename the time column to the target table's column name.
        if time_col != "time":
            df = df.rename(columns={"time": time_col})

        rows_written = 0
        with get_connection() as conn:
            for chunk_start in range(0, len(df), self._batch_size):
                chunk = df.iloc[chunk_start : chunk_start + self._batch_size]
                rows_written += self._upsert_chunk(conn, chunk, table, time_col)
        return rows_written

    def _upsert_chunk(
        self,
        conn: Connection,
        chunk: pd.DataFrame,
        table: str,
        time_col: str,
    ) -> int:
        """Upsert a chunk using a single COPY + ON CONFLICT statement pair.

        Implementation note: ``COPY`` itself cannot upsert, so we
        temporarily insert rows into a staging table, then merge into
        the target K-line table via ``INSERT ... ON CONFLICT ... DO UPDATE``.
        The staging table is created on demand and dropped at the end of
        the statement (so concurrent writers don't collide).
        """
        if chunk.empty:
            return 0

        data_cols = ["symbol", time_col, "open", "high", "low", "close", "volume", "amount"]
        stage_name = f"kline_stage_{table[len('kline_') :]}"

        staging_sql = (
            f"CREATE TEMP TABLE IF NOT EXISTS {stage_name} ("
            "  symbol TEXT NOT NULL,"
            f"  {time_col} {'TIMESTAMPTZ' if time_col == 'ts' else 'DATE'} NOT NULL,"
            "  open NUMERIC(20, 6) NOT NULL,"
            "  high NUMERIC(20, 6) NOT NULL,"
            "  low NUMERIC(20, 6) NOT NULL,"
            "  close NUMERIC(20, 6) NOT NULL,"
            "  volume BIGINT NOT NULL,"
            "  amount NUMERIC(20, 4) NOT NULL"
            ") ON COMMIT DROP"
        )
        upsert_sql = (
            f"INSERT INTO {table} (symbol, {time_col}, open, high, low, close, volume, amount)"
            f" SELECT symbol, {time_col}, open, high, low, close, volume, amount"
            f" FROM {stage_name}"
            f" ON CONFLICT (symbol, {time_col}) DO UPDATE SET"
            "   open = EXCLUDED.open,"
            "   high = EXCLUDED.high,"
            "   low = EXCLUDED.low,"
            "   close = EXCLUDED.close,"
            "   volume = EXCLUDED.volume,"
            "   amount = EXCLUDED.amount"
        )

        conn.exec_driver_sql(staging_sql)
        self._copy_into_staging(conn, chunk, stage_name, data_cols)
        result = conn.exec_driver_sql(upsert_sql)
        # ``cursor.rowcount`` is set after the underlying psycopg cursor
        # is consumed; SQLAlchemy's ``Connection`` exposes it via
        # ``result.rowcount``.
        return int(result.rowcount or 0)

    @staticmethod
    def _copy_into_staging(
        conn: Connection,
        chunk: pd.DataFrame,
        stage_name: str,
        data_cols: list[str],
    ) -> None:
        """Stream ``chunk`` into the staging temp table via ``COPY FROM STDIN``.

        Only the columns requested in ``data_cols`` are written to the CSV,
        even if the input ``chunk`` has additional columns (e.g. ``interval``).
        """
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        # Subset to data_cols so the CSV's column count matches the staging
        # table's column count.
        sub = chunk.loc[:, data_cols]
        for row in sub.itertuples(index=False, name=None):
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
        col_list = ", ".join(data_cols)
        with (
            raw.cursor() as cursor,
            cursor.copy(f"COPY {stage_name} ({col_list}) FROM STDIN WITH (FORMAT csv)") as copy,
        ):
            copy.write(buf.getvalue())

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
        table, time_col = _route_table(interval)
        if not symbols:
            return {}

        sql = (
            f"SELECT symbol, {time_col}, open, high, low, close, volume, amount"
            f" FROM {table}"
            f" WHERE symbol = ANY(%(symbols)s)"
            f"   AND {time_col} >= %(start)s"
            f"   AND {time_col} <= %(end)s"
            " ORDER BY symbol, time"
        )
        params: dict[str, Any] = {
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

        df[time_col] = pd.to_datetime(df[time_col], utc=True)
        result: dict[str, pd.DataFrame] = {}
        for sym_obj, group in df.groupby("symbol", sort=False):
            sym = cast("str", sym_obj)
            sym_df = group.drop(columns=["symbol"]).set_index(time_col).sort_index()
            sym_df.index.name = time_col
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
        """Return the row count matching the given filters.

        ``interval`` is required to route the count to the correct physical
        table (``kline_1d`` or ``kline_1m``).
        """
        if interval is None:
            raise ValueError("count: interval is required to route to the correct table")
        table, _ = _route_table(interval)
        clauses: list[str] = []
        params: dict[str, object] = {}
        if symbol is not None:
            clauses.append("symbol = %(symbol)s")
            params["symbol"] = symbol
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT COUNT(*) FROM {table} {where}"
        with get_connection() as conn:
            return int(conn.exec_driver_sql(sql, params).scalar() or 0)


__all__ = ["INTERVALS", "KLineRepository", "PostgresKLineRepository"]
