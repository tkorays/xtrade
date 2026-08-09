"""Read-only facade over :mod:`xtrade.data` for business-layer callers.

Business code (strategy, execution, backtest) imports from
``xtrade.core.market_data`` instead of constructing a ``Postgres*Repository``
directly. The facade is a thin wrapper: it does not cache results, does not
own the database engine, and exposes only read operations.

The underlying repositories are obtained through three module-level factory
functions (:func:`_build_kline_repo`, :func:`_build_instrument_repo`,
:func:`_build_trade_calendar_repo`). Tests replace these factories with
in-memory fakes via ``monkeypatch.setattr`` to avoid touching a real database.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import TYPE_CHECKING, Literal, cast

import pandas as pd

from xtrade.core.config import get_config
from xtrade.data.engine import get_engine
from xtrade.data.market_data.kline import INTERVALS

if TYPE_CHECKING:
    from xtrade.data.market_data import (
        Instrument,
        InstrumentRepository,
        KLineRepository,
        TradeCalendarRepository,
    )

AdjustMode = Literal["none", "backward", "forward"]


def _build_kline_repo() -> KLineRepository:
    """Return a fresh K-line repository bound to the shared engine.

    ``Config.data.batch_size`` controls the upsert chunk size.
    """
    from xtrade.data.market_data import PostgresKLineRepository

    # Touching ``get_engine`` here ensures the engine singleton is warm
    # before the repository's first database call.
    get_engine()
    return PostgresKLineRepository(batch_size=get_config().data.batch_size)


def _build_instrument_repo() -> InstrumentRepository:
    """Return a fresh instrument repository bound to the shared engine."""
    from xtrade.data.market_data import PostgresInstrumentRepository

    get_engine()
    return PostgresInstrumentRepository()


def _build_trade_calendar_repo() -> TradeCalendarRepository:
    """Return a fresh trade-calendar repository bound to the shared engine."""
    from xtrade.data.market_data import PostgresTradeCalendarRepository

    get_engine()
    return PostgresTradeCalendarRepository()


def _normalize_symbols(symbols: str | Iterable[str]) -> list[str]:
    """Coerce ``str`` / ``Iterable[str]`` to a concrete ``list[str]``."""
    if isinstance(symbols, str):
        return [symbols]
    return list(symbols)


def _check_interval(interval: str) -> None:
    """Reject unsupported intervals before any IO."""
    if interval not in INTERVALS:
        raise ValueError(f"unsupported interval {interval!r}; expected one of {sorted(INTERVALS)}")


def get_bars(
    symbols: str | Iterable[str],
    start: date | datetime,
    end: date | datetime,
    interval: str,
    adjust: AdjustMode = "none",
) -> pd.DataFrame | dict[str, pd.DataFrame]:
    """Read K-line bars for one or many symbols in ``[start, end]``.

    Args:
        symbols: A single symbol string or an iterable of symbols.
        start: Inclusive lower bound (date or datetime).
        end: Inclusive upper bound (date or datetime).
        interval: One of ``"1d"``, ``"1m"``, ``"5m"``, ``"15m"``, ``"30m"``,
            ``"60m"``. Unknown values raise ``ValueError`` before any IO.
        adjust: ``"none"`` (default, raw prices), ``"backward"``, or
            ``"forward"``. Forwarded to the underlying repository.

    Returns:
        A single ``DataFrame`` when ``symbols`` is a ``str``; a
        ``dict[symbol, DataFrame]`` when ``symbols`` is an iterable. Empty
        iterable returns ``{}``.
    """
    _check_interval(interval)

    was_scalar = isinstance(symbols, str)
    normalized = _normalize_symbols(symbols)
    if not normalized:
        return {} if not was_scalar else pd.DataFrame()

    repo = _build_kline_repo()
    result = repo.get_bars(normalized, start, end, interval, adjust=adjust)

    if was_scalar:
        return result[normalized[0]]
    return result


def get_instrument(symbol: str) -> Instrument | None:
    """Return the ``Instrument`` for ``symbol`` or ``None`` if missing."""
    from xtrade.data.market_data import Instrument  # noqa: F401  re-export for type checkers

    repo = _build_instrument_repo()
    return repo.get(symbol)


def is_trading_day(d: date) -> bool:
    """Return ``True`` iff ``d`` is marked as a trading day in the calendar."""
    repo = _build_trade_calendar_repo()
    return repo.is_trading_day(d)


def get_trading_days(start: date, end: date) -> list[date]:
    """Return ascending business dates in ``[start, end]`` marked as trading."""
    repo = _build_trade_calendar_repo()
    days = repo.get_trading_days(start, end)
    # Cast through the repo's declared return; the Postgres impl returns
    # ``list[date]`` already but the Protocol uses ``Iterable``.
    return cast("list[date]", list(days))


__all__ = [
    "get_bars",
    "get_instrument",
    "get_trading_days",
    "is_trading_day",
]
