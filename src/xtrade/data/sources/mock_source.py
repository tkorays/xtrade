"""In-memory mock data source for tests and examples."""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pandas as pd

from xtrade.data.market_data.instrument import Instrument


class InMemoryMockSource:
    """``DataSource`` whose data is supplied at construction time.

    No network or disk IO. ``fetch_*`` methods return defensive copies
    so that callers cannot mutate the seed data by accident.
    """

    def __init__(
        self,
        instruments: list[Instrument] | None = None,
        bars: dict[str, pd.DataFrame] | None = None,
        adj_factors: dict[str, pd.DataFrame] | None = None,
        calendar: pd.DataFrame | None = None,
    ) -> None:
        self._instruments: list[Instrument] = list(instruments or [])
        self._bars: dict[str, pd.DataFrame] = {k: v.copy() for k, v in (bars or {}).items()}
        self._adj_factors: dict[str, pd.DataFrame] = {
            k: v.copy() for k, v in (adj_factors or {}).items()
        }
        self._calendar: pd.DataFrame = (
            calendar.copy()
            if calendar is not None
            else pd.DataFrame(columns=["exchange", "date", "is_trading"])
        )

    # ---- fetch ----

    def fetch_instruments(self) -> list[Instrument]:
        return [copy.copy(i) for i in self._instruments]

    def fetch_bars(self, symbol: str, start: date, end: date, interval: str) -> pd.DataFrame:
        df = self._bars.get(symbol)
        if df is None or df.empty:
            return pd.DataFrame()
        mask = (df["time"].dt.date >= start) & (df["time"].dt.date <= end)
        out: pd.DataFrame = df.loc[mask].copy()
        return out

    def fetch_bars_bulk(
        self, symbols: list[str], start: date, end: date, interval: str
    ) -> pd.DataFrame:
        """Aggregate per-symbol frames into one long-format DataFrame.

        Symbols absent from ``self._bars`` are omitted (consistent with
        :meth:`XtQuantDataSource.fetch_bars_bulk` semantics: "no data in
        window"). The returned frame has columns ``symbol, time,
        open, high, low, close, volume, amount``.
        """
        frames: list[pd.DataFrame] = []
        for symbol in symbols:
            df = self._bars.get(symbol)
            if df is None or df.empty:
                continue
            mask = (df["time"].dt.date >= start) & (df["time"].dt.date <= end)
            sub = df.loc[mask].copy()
            if sub.empty:
                continue
            sub = sub.copy()
            sub["symbol"] = symbol
            sub["interval"] = interval
            frames.append(sub)
        if not frames:
            return pd.DataFrame(
                columns=[
                    "symbol",
                    "time",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "amount",
                ]
            )
        out = pd.concat(frames, ignore_index=True)
        return out[
            [
                "symbol",
                "time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
            ]
        ].copy()

    def fetch_adjust_factors(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        df = self._adj_factors.get(symbol)
        if df is None or df.empty:
            return pd.DataFrame(columns=["ex_date", "factor"])
        mask = (df["ex_date"] >= start) & (df["ex_date"] <= end)
        return df.loc[mask].copy()

    def fetch_trade_calendar(self, start: date, end: date) -> pd.DataFrame:
        if self._calendar.empty:
            return self._calendar.copy()
        mask = (self._calendar["date"] >= start) & (self._calendar["date"] <= end)
        return self._calendar.loc[mask].copy()

    # ---- mutation helpers (test-only convenience) ----

    def set_bars(self, symbol: str, df: pd.DataFrame) -> None:
        self._bars[symbol] = df.copy()

    def set_adj_factors(self, symbol: str, df: pd.DataFrame) -> None:
        self._adj_factors[symbol] = df.copy()

    def set_calendar(self, df: pd.DataFrame) -> None:
        self._calendar = df.copy()

    def add_instrument(self, instrument: Instrument) -> None:
        self._instruments.append(instrument)


__all__ = ["InMemoryMockSource"]

_ = Any  # silence unused-import warnings on typing.Any if not referenced
