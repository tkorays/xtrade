"""High-level helper that pumps source data into the durable repositories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from xtrade.data.market_data.adj_factor import AdjustmentFactorRepository
from xtrade.data.market_data.instrument import InstrumentRepository
from xtrade.data.market_data.kline import KLineRepository
from xtrade.data.market_data.trade_calendar import TradeCalendarRepository
from xtrade.data.sources.base import DataSource


@dataclass(frozen=True)
class PumpResult:
    """Counts of rows persisted per entity."""

    instruments: int
    bars: int
    adjust_factors: int
    trade_calendar: int


def pump(
    source: DataSource,
    instrument_repo: InstrumentRepository,
    kline_repo: KLineRepository,
    adj_repo: AdjustmentFactorRepository,
    calendar_repo: TradeCalendarRepository,
    symbols: list[str] | None = None,
    *,
    interval: str = "1d",
    start: date | None = None,
    end: date | None = None,
) -> PumpResult:
    """Pull data from ``source`` and persist via the supplied repositories.

    Args:
        source: Any ``DataSource`` implementation.
        instrument_repo: Where to persist instruments.
        kline_repo: Where to persist K-line bars.
        adj_repo: Where to persist adjustment factors.
        calendar_repo: Where to persist trade-calendar days.
        symbols: Optional explicit symbol list; defaults to
            ``source.fetch_instruments()``.
        interval: K-line interval to fetch when ``symbols`` is supplied.
        start, end: Date range. If omitted, falls back to a no-op range
            (single-day range of today) to keep the helper stateless.

    Returns:
        ``PumpResult`` with row counts per entity.
    """
    instruments = source.fetch_instruments()
    for inst in instruments:
        instrument_repo.upsert(inst)
    symbol_list: list[str] = (
        list(symbols) if symbols is not None else [i.symbol for i in instruments]
    )

    if start is None or end is None:
        # No range specified — pump nothing for time-series entities.
        return PumpResult(instruments=len(instruments), bars=0, adjust_factors=0, trade_calendar=0)

    bars_total = 0
    adj_total = 0
    for sym in symbol_list:
        bars = source.fetch_bars(sym, start, end, interval)
        if not bars.empty:
            bars_total += kline_repo.upsert_bars(bars)
        adj = source.fetch_adjust_factors(sym, start, end)
        if not adj.empty:
            adj_total += adj_repo.upsert(adj)

    cal_df = source.fetch_trade_calendar(start, end)
    cal_count = calendar_repo.upsert_days(cal_df) if not cal_df.empty else 0

    return PumpResult(
        instruments=len(instruments),
        bars=bars_total,
        adjust_factors=adj_total,
        trade_calendar=cal_count,
    )


__all__ = ["PumpResult", "pump"]

_ = Any  # silence unused-import on Any when not referenced elsewhere
