"""Tests for the ``xtrade.core.market_data`` read-only facade.

The facade is exercised entirely through in-memory fakes that replace the
three ``_build_*`` factories. No real database connection is opened.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd
import pytest

from xtrade.core import market_data
from xtrade.data.market_data import Instrument


class _FakeKLineRepo:
    """Stub ``KLineRepository`` that records calls and returns canned data."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.next_result: dict[str, pd.DataFrame] = {}

    def get_bars(
        self,
        symbols: list[str],
        start: date | datetime,
        end: date | datetime,
        interval: str,
        adjust: str = "none",
    ) -> dict[str, pd.DataFrame]:
        self.calls.append(
            {
                "symbols": list(symbols),
                "start": start,
                "end": end,
                "interval": interval,
                "adjust": adjust,
            }
        )
        # Return a fresh copy on every call so callers can mutate the
        # returned DataFrame without affecting subsequent reads.
        return {s: self.next_result.get(s, pd.DataFrame()).copy() for s in symbols}

    def upsert_bars(self, df: pd.DataFrame) -> int:  # pragma: no cover - not used here
        raise AssertionError("facade must not call upsert_bars")


class _FakeInstrumentRepo:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.next_value: Instrument | None = None

    def get(self, symbol: str) -> Instrument | None:
        self.calls.append(symbol)
        return self.next_value

    def upsert(self, record: Instrument) -> None:  # pragma: no cover
        raise AssertionError("facade must not call upsert")

    def list_all(self) -> list[Instrument]:  # pragma: no cover
        raise AssertionError("facade must not call list_all")


class _FakeTradeCalendarRepo:
    def __init__(self) -> None:
        self.is_calls: list[date] = []
        self.range_calls: list[tuple[date, date]] = []
        self.next_is_trading: bool = False
        self.next_range: list[date] = []

    def is_trading_day(self, d: date) -> bool:
        self.is_calls.append(d)
        return self.next_is_trading

    def get_trading_days(self, start: date, end: date) -> list[date]:
        self.range_calls.append((start, end))
        return list(self.next_range)

    def upsert_days(self, df: pd.DataFrame) -> int:  # pragma: no cover
        raise AssertionError("facade must not call upsert_days")


@pytest.fixture
def fake_kline(monkeypatch: pytest.MonkeyPatch) -> _FakeKLineRepo:
    fake = _FakeKLineRepo()
    monkeypatch.setattr(market_data, "_build_kline_repo", lambda: fake)
    return fake


@pytest.fixture
def fake_instrument(monkeypatch: pytest.MonkeyPatch) -> _FakeInstrumentRepo:
    fake = _FakeInstrumentRepo()
    monkeypatch.setattr(market_data, "_build_instrument_repo", lambda: fake)
    return fake


@pytest.fixture
def fake_calendar(monkeypatch: pytest.MonkeyPatch) -> _FakeTradeCalendarRepo:
    fake = _FakeTradeCalendarRepo()
    monkeypatch.setattr(market_data, "_build_trade_calendar_repo", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# get_bars
# ---------------------------------------------------------------------------


def test_get_bars_single_symbol_returns_dataframe(fake_kline: _FakeKLineRepo) -> None:
    df = pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex(["2025-01-02"], tz="UTC"))
    df.index.name = "time"
    fake_kline.next_result = {"AAA": df}

    out = market_data.get_bars("AAA", start=date(2025, 1, 1), end=date(2025, 1, 10), interval="1d")

    assert isinstance(out, pd.DataFrame)
    assert list(out["close"]) == [1.0]
    assert len(fake_kline.calls) == 1
    assert fake_kline.calls[0]["symbols"] == ["AAA"]
    assert fake_kline.calls[0]["adjust"] == "none"  # default


def test_get_bars_multi_symbol_returns_dict(fake_kline: _FakeKLineRepo) -> None:
    fake_kline.next_result = {
        "AAA": pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex(["2025-01-02"], tz="UTC")),
        "BBB": pd.DataFrame(),
    }

    out = market_data.get_bars(
        ["AAA", "BBB", "CCC"],
        start=date(2025, 1, 1),
        end=date(2025, 1, 10),
        interval="1d",
    )

    assert isinstance(out, dict)
    assert set(out.keys()) == {"AAA", "BBB", "CCC"}
    assert list(out["AAA"]["close"]) == [1.0]
    assert out["BBB"].empty
    assert out["CCC"].empty  # not in next_result -> empty DataFrame from repo
    assert fake_kline.calls[0]["symbols"] == ["AAA", "BBB", "CCC"]


def test_get_bars_empty_iterable_returns_empty_dict(fake_kline: _FakeKLineRepo) -> None:
    out = market_data.get_bars([], start=date(2025, 1, 1), end=date(2025, 1, 10), interval="1d")
    assert out == {}
    assert fake_kline.calls == []  # no DB call


def test_get_bars_adjust_backward_forwarded(fake_kline: _FakeKLineRepo) -> None:
    market_data.get_bars(
        "AAA",
        start=date(2025, 1, 1),
        end=date(2025, 1, 10),
        interval="1d",
        adjust="backward",
    )
    assert fake_kline.calls[0]["adjust"] == "backward"


def test_get_bars_default_adjust_is_none(fake_kline: _FakeKLineRepo) -> None:
    market_data.get_bars("AAA", start=date(2025, 1, 1), end=date(2025, 1, 10), interval="1d")
    assert fake_kline.calls[0]["adjust"] == "none"


def test_get_bars_unknown_interval_raises_without_io(
    monkeypatch: pytest.MonkeyPatch, fake_kline: _FakeKLineRepo
) -> None:
    # Replace the K-line builder with a sentinel that raises if touched.
    def _explode() -> None:
        raise AssertionError("repo must not be built for invalid interval")

    monkeypatch.setattr(market_data, "_build_kline_repo", _explode)

    with pytest.raises(ValueError, match="unsupported interval"):
        market_data.get_bars(
            "AAA",
            start=date(2025, 1, 1),
            end=date(2025, 1, 10),
            interval="7m",
        )


def test_get_bars_two_calls_returns_distinct_dataframes(
    fake_kline: _FakeKLineRepo,
) -> None:
    """No caching: two calls must produce two DataFrame objects."""
    df = pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex(["2025-01-02"], tz="UTC"))
    fake_kline.next_result = {"AAA": df}

    out1 = market_data.get_bars("AAA", start=date(2025, 1, 1), end=date(2025, 1, 10), interval="1d")
    out2 = market_data.get_bars("AAA", start=date(2025, 1, 1), end=date(2025, 1, 10), interval="1d")

    assert out1 is not out2
    assert len(fake_kline.calls) == 2


def test_facade_does_not_own_engine() -> None:
    """The facade reaches ``xtrade.data.engine.get_engine`` rather than
    owning its own engine. The facade module exposes no `_engine` slot and
    references the same ``get_engine`` callable as ``xtrade.data.engine``."""
    from xtrade.data import engine as engine_mod

    assert market_data.get_engine is engine_mod.get_engine
    facade_globals = vars(market_data)
    assert "_engine" not in facade_globals
    assert "engine" not in {k for k in facade_globals if not k.startswith("_")}


def test_get_bars_engine_survives_reset() -> None:
    """Spec: after ``reset_engine()`` the facade re-resolves via ``get_engine()``.

    The factory is replaced with one that calls ``get_engine`` and then
    returns a fake; resetting the engine between calls verifies the
    facade re-resolves rather than holding onto a stale engine."""
    from xtrade.data import engine as engine_mod

    fake = _FakeKLineRepo()
    from xtrade.core.market_data import get_engine as facade_get_engine

    def _real_engine_then_fake() -> _FakeKLineRepo:
        facade_get_engine()
        return fake

    import xtrade.core.market_data as md

    original_builder = md._build_kline_repo
    md._build_kline_repo = _real_engine_then_fake  # type: ignore[assignment]
    engine_mod.reset_engine()
    try:
        assert engine_mod._engine is None
        market_data.get_bars("AAA", start=date(2025, 1, 1), end=date(2025, 1, 10), interval="1d")
        assert engine_mod._engine is not None
    finally:
        md._build_kline_repo = original_builder  # type: ignore[assignment]
        engine_mod.reset_engine()


# ---------------------------------------------------------------------------
# get_instrument
# ---------------------------------------------------------------------------


def test_get_instrument_returns_record(fake_instrument: _FakeInstrumentRepo) -> None:
    record = Instrument(
        symbol="AAA",
        name="Alpha",
        exchange="XSHG",
        list_date=date(2020, 1, 1),
        delist_date=None,
        status="active",
    )
    fake_instrument.next_value = record

    out = market_data.get_instrument("AAA")
    assert out == record
    assert fake_instrument.calls == ["AAA"]


def test_get_instrument_missing_returns_none(fake_instrument: _FakeInstrumentRepo) -> None:
    assert market_data.get_instrument("UNKNOWN") is None


# ---------------------------------------------------------------------------
# trade calendar
# ---------------------------------------------------------------------------


def test_is_trading_day_returns_true(fake_calendar: _FakeTradeCalendarRepo) -> None:
    fake_calendar.next_is_trading = True
    assert market_data.is_trading_day(date(2025, 1, 2)) is True
    assert fake_calendar.is_calls == [date(2025, 1, 2)]


def test_is_trading_day_returns_false(fake_calendar: _FakeTradeCalendarRepo) -> None:
    fake_calendar.next_is_trading = False
    assert market_data.is_trading_day(date(2025, 1, 1)) is False


def test_get_trading_days_returns_list(fake_calendar: _FakeTradeCalendarRepo) -> None:
    fake_calendar.next_range = [date(2025, 1, 2), date(2025, 1, 3)]
    out = market_data.get_trading_days(date(2025, 1, 1), date(2025, 1, 10))
    assert out == [date(2025, 1, 2), date(2025, 1, 3)]
    assert fake_calendar.range_calls == [(date(2025, 1, 1), date(2025, 1, 10))]


# ---------------------------------------------------------------------------
# public surface
# ---------------------------------------------------------------------------


def test_public_api_is_read_only() -> None:
    """The facade exposes only the four public functions."""
    assert set(market_data.__all__) == {
        "get_bars",
        "get_instrument",
        "get_trading_days",
        "is_trading_day",
    }


def test_facade_does_not_import_data_sources() -> None:
    """Spec: facade must not import any module from ``xtrade.data.sources``."""
    import sys

    forbidden = [m for m in sys.modules if m.startswith("xtrade.data.sources")]
    # Force-import the facade if not already imported.
    import xtrade.core.market_data  # noqa: F401

    reloaded = [m for m in sys.modules if m.startswith("xtrade.data.sources")]
    assert reloaded == forbidden, (
        f"facade must not import xtrade.data.sources; got {set(reloaded) - set(forbidden)}"
    )
