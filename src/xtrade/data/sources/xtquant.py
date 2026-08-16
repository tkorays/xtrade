"""``xtquant`` MiniQMT data source.

Implements the :class:`DataSource` Protocol against the local MiniQMT
client (``xtquant.xtdata``). ``xtquant`` is **not** on PyPI — it ships
with the user's QMT distribution and is imported lazily inside the
class so that the rest of the project (and unit tests) load cleanly
without ``xtquant`` installed.

Two pieces live here:

- :func:`merge_bars` — the pure (xtquant-free) merge helper that
  normalises ``xtdata.get_local_data``'s per-symbol output into one
  DataFrame with the schema the K-line repository expects.
- :class:`XtQuantDataSource` — the :class:`DataSource` implementation
  that talks to xtquant.

The :data:`SUPPORTED_INTERVALS` and :data:`BEIJING_TZ` constants are
re-exported so other modules (e.g. the historical-load script) can
share the canonical list without redefining it.
"""

from __future__ import annotations

from datetime import date, datetime
from datetime import time as dtime
from typing import cast

import pandas as pd

from xtrade.data.market_data.instrument import Instrument

SUPPORTED_INTERVALS: frozenset[str] = frozenset({"1d", "1m"})
BEIJING_TZ: str = "Asia/Shanghai"

__all__ = [
    "BEIJING_TZ",
    "SUPPORTED_INTERVALS",
    "XtQuantDataSource",
    "format_xtquant_time",
    "merge_bars",
]


# ---------------------------------------------------------------------------
# Pure helpers (xtquant-free; unit-testable in isolation)
# ---------------------------------------------------------------------------


def merge_bars(ret: dict[str, pd.DataFrame | None], interval: str) -> pd.DataFrame:
    """Merge ``xtdata.get_local_data``'s per-symbol output into one DataFrame.

    - Drops any ``pre_close`` column (xtquant returns ``preClose`` /
      ``pre_close``; the new schema does not store it).
    - Converts ``time`` (ms-UTC int64) to Asia/Shanghai ``Timestamp`` and
      then either ``.dt.date`` (for ``1d``) or kept tz-aware (for ``1m``).
    - Adds the ``interval`` column.
    - Sorts by ``(symbol, time)``.

    Skips ``None`` / empty frames silently. Returns an empty DataFrame
    (with the canonical column set) when every frame is empty.
    """
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"unsupported interval {interval!r}")

    time_col = "date" if interval == "1d" else "time"
    frames: list[pd.DataFrame] = []

    for symbol, df in ret.items():
        if df is None or df.empty:
            continue
        df = df.copy()

        # xtdata returns ``preClose`` (camelCase). Normalise defensively then
        # drop it; the new schema does not store ``pre_close``.
        if "preClose" in df.columns and "pre_close" not in df.columns:
            df = df.rename(columns={"preClose": "pre_close"})
        if "pre_close" in df.columns:
            df = df.drop(columns=["pre_close"])

        # xtdata returns ``time`` as int64 ms since UTC epoch, representing
        # Beijing 00:00 of the requested date. Parse as UTC then convert to
        # Asia/Shanghai to avoid a one-day offset.
        if interval == "1d":
            df[time_col] = (
                pd.to_datetime(df["time"], unit="ms", utc=True).dt.tz_convert(BEIJING_TZ).dt.date
            )
        else:
            df[time_col] = pd.to_datetime(df["time"], unit="ms", utc=True).dt.tz_convert(BEIJING_TZ)

        df["symbol"] = symbol
        df["interval"] = interval

        df = df[
            ["symbol", time_col, "interval", "open", "high", "low", "close", "volume", "amount"]
        ].rename(columns={time_col: "time"})
        frames.append(df)

    if not frames:
        return pd.DataFrame(
            columns=[
                "symbol",
                "time",
                "interval",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
            ]
        )

    result = pd.concat(frames, ignore_index=True)
    return result.sort_values(["symbol", "time"]).reset_index(drop=True)


def format_xtquant_time(d: date, interval: str, *, end: bool = False) -> str:
    """Format a ``date`` for ``xtdata.download_history_data2``.

    - ``1d``: ``YYYYMMDD``.
    - ``1m``: ``YYYYMMDDHHmmss`` (``time.min`` for start, ``time.max`` for end).
    """
    if interval == "1d":
        return d.strftime("%Y%m%d")
    base = datetime.combine(d, dtime.max if end else dtime.min)
    return base.strftime("%Y%m%d%H%M%S")


# ---------------------------------------------------------------------------
# DataSource implementation (xtquant-touching)
# ---------------------------------------------------------------------------


class XtQuantDataSource:
    """``DataSource`` backed by the local MiniQMT ``xtquant`` client.

    xtquant is single-process; callers (e.g. ``DailyXtQuantCollector``)
    iterate batches serially. ``fetch_bars`` performs a two-call dance:

    1. ``xtdata.download_history_data2`` populates MiniQMT's local cache.
    2. ``xtdata.get_local_data`` reads it back as ``dict[symbol, df]``.

    Both calls are lazy-imported inside the method so the module loads
    even when xtquant is not installed.
    """

    def __init__(self) -> None:
        # xtquant is imported on first ``fetch_bars``; constructing the
        # source itself does no IO so it is safe to instantiate at module
        # load (in :meth:`SourceRegistry._init`).
        pass

    def fetch_instruments(self) -> list[Instrument]:
        """Return an empty list — xtquant has no batched instrument listing.

        The collector reads ``instrument`` directly from Postgres.
        """
        return []

    def fetch_bars(self, symbol: str, start: date, end: date, interval: str) -> pd.DataFrame:
        """Fetch one symbol's bars in ``[start, end]`` and normalise them.

        Thin wrapper around :meth:`fetch_bars_bulk` kept for callers that
        ask for one symbol at a time (e.g.
        ``scripts/fetch_historical_bars_xtquant.py``). New code paths
        SHOULD use :meth:`fetch_bars_bulk` directly to amortise the
        MiniQMT round-trips.

        Args:
            symbol: A single symbol (e.g. ``"000001.SZ"``).
            start: Inclusive start date.
            end: Inclusive end date.
            interval: One of ``"1d"``, ``"1m"``.

        Returns:
            A normalised per-symbol DataFrame, or an empty DataFrame when
            xtquant returns no data for the requested window.
        """
        merged = self.fetch_bars_bulk([symbol], start, end, interval)
        if merged.empty:
            return pd.DataFrame(
                columns=["time", "open", "high", "low", "close", "volume", "amount"]
            )
        return (
            merged.loc[merged["symbol"] == symbol].drop(columns=["symbol"]).reset_index(drop=True)
        )

    def fetch_bars_bulk(
        self, symbols: list[str], start: date, end: date, interval: str
    ) -> pd.DataFrame:
        """Fetch many symbols' bars in one bulk call.

        Issues a single ``download_history_data2(stock_list=symbols, ...)``
        followed by a single ``get_local_data(stock_list=symbols, ...)``
        against MiniQMT, then merges the per-symbol frames into one
        long-format DataFrame with columns ``symbol, time, interval,
        open, high, low, close, volume, amount`` (sorted by
        ``(symbol, time)``). Symbols absent from MiniQMT's cache are
        omitted from the result; the caller SHALL treat absence as
        "no data in window".

        Args:
            symbols: Symbols to fetch. May be a single-element list.
            start: Inclusive start date.
            end: Inclusive end date.
            interval: One of ``"1d"``, ``"1m"``.

        Returns:
            A long-format DataFrame. Empty (with canonical columns) when
            every symbol returned nothing.

        Raises:
            ValueError: If ``interval`` is not in ``SUPPORTED_INTERVALS``.
            ModuleNotFoundError: If ``xtquant`` is not installed.
            Exception: Any MiniQMT exception from ``download_history_data2``
                or ``get_local_data`` is propagated as-is.
        """
        if interval not in SUPPORTED_INTERVALS:
            raise ValueError(
                f"unsupported interval {interval!r}; expected one of {sorted(SUPPORTED_INTERVALS)}"
            )

        # Lazy import — xtquant is not on PyPI; surface a clear
        # ``ModuleNotFoundError`` on first call rather than failing at
        # import time.
        from xtquant import xtdata  # type: ignore[import-untyped]

        start_str = format_xtquant_time(start, interval, end=False)
        end_str = format_xtquant_time(end, interval, end=True)

        # One bulk ``download_history_data2`` for the whole list. MiniQMT
        # fans the request out internally so this single call replaces
        # the N per-symbol calls the legacy implementation issued.
        xtdata.download_history_data2(
            stock_list=symbols,
            period=interval,
            start_time=start_str,
            end_time=end_str,
        )

        ret = xtdata.get_local_data(
            stock_list=symbols,
            period=interval,
            start_time=start_str,
            end_time=end_str,
            dividend_type="none",
        )

        # xtquant returns ``None`` for an empty list / unknown symbols.
        if not ret:
            return pd.DataFrame(
                columns=[
                    "symbol",
                    "time",
                    "interval",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "amount",
                ]
            )

        per_symbol = cast("dict[str, pd.DataFrame | None]", ret)
        return merge_bars(per_symbol, interval)

    def fetch_adjust_factors(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Return an empty DataFrame — out of scope for this change."""
        return pd.DataFrame(columns=["symbol", "ex_date", "factor"])

    def fetch_trade_calendar(self, start: date, end: date) -> pd.DataFrame:
        """Return an empty DataFrame — the trade calendar is imported separately."""
        return pd.DataFrame(columns=["exchange", "date", "is_trading"])

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "XtQuantDataSource()"


# ---------------------------------------------------------------------------
# Protocol satisfaction (runtime + static)
# ---------------------------------------------------------------------------

# ``runtime_checkable`` only checks method presence, not signatures; the
# structural typing is enforced by mypy's ``@runtime_checkable Protocol``
# resolution and by the explicit subclass-like usage in
# :meth:`SourceRegistry.register`. Because :class:`XtQuantDataSource`
# defines every method in the :class:`DataSource` Protocol,
# ``isinstance(src, DataSource)`` returns ``True`` even without an
# explicit inheritance declaration.
