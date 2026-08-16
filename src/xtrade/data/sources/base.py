"""DataSource Protocol and SourceRegistry."""

from __future__ import annotations

from datetime import date
from threading import Lock
from typing import Any, Protocol, runtime_checkable

import pandas as pd

from xtrade.data.market_data.instrument import Instrument


@runtime_checkable
class DataSource(Protocol):
    """Producer-side abstraction for fetching market reference data.

    Concrete sources (Tushare, AKShare, CSV, ...) implement the five
    ``fetch_*`` methods. The Protocol is structural — any class with
    matching methods satisfies it without inheritance.
    """

    def fetch_instruments(self) -> list[Instrument]: ...

    def fetch_bars(self, symbol: str, start: date, end: date, interval: str) -> pd.DataFrame: ...

    def fetch_bars_bulk(
        self, symbols: list[str], start: date, end: date, interval: str
    ) -> pd.DataFrame: ...

    def fetch_adjust_factors(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...

    def fetch_trade_calendar(self, start: date, end: date) -> pd.DataFrame: ...


class SourceRegistry:
    """Process-local singleton registry of named data sources.

    A default ``InMemoryMockSource`` named ``"mock"`` is registered on
    first instantiation. Use :meth:`register` to add new sources,
    :meth:`get` to retrieve one, :meth:`reset` (in tests) to clear all.
    """

    _instance: SourceRegistry | None = None
    _lock = Lock()

    def __new__(cls) -> SourceRegistry:
        with cls._lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._init()
                cls._instance = inst
            return cls._instance

    def _init(self) -> None:
        self._sources: dict[str, DataSource] = {}
        # Lazy import to avoid a circular reference: ``mock_source`` does
        # not depend on this module, so a local import is safe.
        from xtrade.data.sources.mock_source import InMemoryMockSource

        self._sources["mock"] = InMemoryMockSource()

        # ``xtquant`` ships with the user's QMT distribution (not on PyPI).
        # Register it lazily so the rest of the project loads cleanly when
        # the package is missing. A failed import is silently skipped;
        # callers see the absence as ``KeyError`` from :meth:`get`.
        try:
            from xtrade.data.sources.xtquant import XtQuantDataSource

            self._sources["xtquant"] = XtQuantDataSource()
        except ModuleNotFoundError:
            # xtquant is optional — the operator installs it locally.
            pass

    def register(self, name: str, source: DataSource) -> None:
        if not isinstance(source, DataSource):
            # ``runtime_checkable`` only checks method presence, not
            # type. We additionally guard against obvious misuse.
            raise TypeError(f"{type(source).__name__} does not satisfy DataSource protocol")
        self._sources[name] = source

    def unregister(self, name: str) -> None:
        self._sources.pop(name, None)

    def get(self, name: str) -> DataSource:
        if name not in self._sources:
            raise KeyError(f"unknown data source: {name!r}; known: {sorted(self._sources)}")
        return self._sources[name]

    def names(self) -> list[str]:
        return sorted(self._sources)

    def reset(self) -> None:
        """Drop all registered sources and re-seed the defaults (used by tests).

        Calling ``reset`` mirrors the state the registry has on first
        instantiation: the ``mock`` source is always re-registered, and
        ``xtquant`` is re-registered when its optional import succeeds.
        Tests that depend on a known starting point should call this
        between cases.
        """
        self._sources.clear()
        self._init()

    def __contains__(self, name: str) -> bool:
        return name in self._sources

    def __getitem__(self, name: str) -> DataSource:
        return self.get(name)

    def __len__(self) -> int:
        return len(self._sources)

    def __repr__(self) -> str:
        return f"SourceRegistry(names={self.names()!r})"


__all__ = ["DataSource", "SourceRegistry"]


def _suppress_unused(*_args: Any) -> None:
    """Quiet mypy when Protocol methods are referenced in docstrings only."""


_ = _suppress_unused
