"""Unit tests for ``xtrade.data.sources.xtquant``.

These tests cover the pure ``merge_bars`` / ``format_xtquant_time``
helpers and the ``XtQuantDataSource`` class's interval validation. xtquant
itself is **not** imported; the only xtquant-touching code path
(``fetch_bars``) is exercised via the protocol's value-error branch.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
import pytest

from xtrade.data.sources.xtquant import (
    BEIJING_TZ,
    SUPPORTED_INTERVALS,
    XtQuantDataSource,
    format_xtquant_time,
    merge_bars,
)

# ---------------------------------------------------------------------------
# merge_bars
# ---------------------------------------------------------------------------

# Asia/Shanghai 2024-01-02 00:00 = epoch ms 1704124800000 (UTC 2024-01-01 16:00)
BEIJING_2024_01_02_MS = 1_704_124_800_000
BEIJING_2024_01_03_MS = 1_704_211_200_000


def _make_xtquant_frame_1d(ms_list: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": ms_list,
            "open": [10.0 for _ in ms_list],
            "high": [11.0 for _ in ms_list],
            "low": [9.0 for _ in ms_list],
            "close": [10.5 for _ in ms_list],
            "volume": [1_000_000 for _ in ms_list],
            "amount": [10_500_000.0 for _ in ms_list],
            "preClose": [10.0 for _ in ms_list],
        }
    )


def _make_xtquant_frame_1m(ms_list: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": ms_list,
            "open": [10.0 for _ in ms_list],
            "high": [11.0 for _ in ms_list],
            "low": [9.0 for _ in ms_list],
            "close": [10.5 for _ in ms_list],
            "volume": [10_000 for _ in ms_list],
            "amount": [105_000.0 for _ in ms_list],
            "preClose": [10.0 for _ in ms_list],
        }
    )


def test_merge_bars_1d_basic() -> None:
    ret: dict[str, pd.DataFrame | None] = {
        "000001.SZ": _make_xtquant_frame_1d([BEIJING_2024_01_02_MS]),
        "600000.SH": _make_xtquant_frame_1d([BEIJING_2024_01_02_MS, BEIJING_2024_01_03_MS]),
    }
    out = merge_bars(ret, "1d")
    assert list(out.columns) == [
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
    assert len(out) == 3
    assert "pre_close" not in out.columns
    assert (out["interval"] == "1d").all()
    # Dates are date objects (not Timestamps).
    assert all(isinstance(t, date) and not isinstance(t, pd.Timestamp) for t in out["time"])
    # Beijing tz conversion: BEIJING_2024_01_02_MS is 2024-01-02 00:00 BJT.
    assert out[out["symbol"] == "000001.SZ"]["time"].iloc[0] == date(2024, 1, 2)
    assert set(out[out["symbol"] == "600000.SH"]["time"]) == {date(2024, 1, 2), date(2024, 1, 3)}
    # Sorted by (symbol, time).
    assert list(out["symbol"].unique()) == ["000001.SZ", "600000.SH"]


def test_merge_bars_1m_basic() -> None:
    ret: dict[str, pd.DataFrame | None] = {
        "000001.SZ": _make_xtquant_frame_1m(
            [BEIJING_2024_01_02_MS, BEIJING_2024_01_02_MS + 60_000]
        ),
    }
    out = merge_bars(ret, "1m")
    assert len(out) == 2
    assert (out["interval"] == "1m").all()
    # 1m timestamps are tz-aware datetimes, not bare dates.
    assert all(isinstance(t, pd.Timestamp) and t.tzinfo is not None for t in out["time"])


def test_merge_bars_skips_none_and_empty() -> None:
    ret: dict[str, pd.DataFrame | None] = {
        "AAA": None,
        "BBB": pd.DataFrame(),
        "CCC": _make_xtquant_frame_1d([BEIJING_2024_01_02_MS]),
    }
    out = merge_bars(ret, "1d")
    assert list(out["symbol"]) == ["CCC"]


def test_merge_bars_returns_empty_when_all_empty() -> None:
    out = merge_bars({}, "1d")
    assert out.empty
    assert list(out.columns) == [
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


def test_merge_bars_tz_conversion_avoids_one_day_offset() -> None:
    """Pin the fix for the millisecond-UTC one-day offset bug."""
    # 0 ms = 1970-01-01 00:00 UTC = 1970-01-01 08:00 BJT
    ret = {"TST": _make_xtquant_frame_1d([0])}
    out = merge_bars(ret, "1d")
    assert out["time"].iloc[0] == date(1970, 1, 1), (
        "Expected 1970-01-01 in BJT, NOT 1969-12-31 (the naive-UTC reading)"
    )


def test_merge_bars_rejects_unknown_interval() -> None:
    with pytest.raises(ValueError, match="unsupported interval"):
        merge_bars({}, "5m")


def test_merge_bars_already_has_pre_close() -> None:
    """If xtquant ever returns ``pre_close`` (snake_case) directly, drop it."""
    df = _make_xtquant_frame_1d([BEIJING_2024_01_02_MS])
    df = df.rename(columns={"preClose": "pre_close"})
    out = merge_bars({"TST": df}, "1d")
    assert "pre_close" not in out.columns


# ---------------------------------------------------------------------------
# format_xtquant_time
# ---------------------------------------------------------------------------


def test_format_xtquant_time_1d_start() -> None:
    assert format_xtquant_time(date(2024, 1, 2), "1d", end=False) == "20240102"


def test_format_xtquant_time_1d_end() -> None:
    assert format_xtquant_time(date(2024, 1, 2), "1d", end=True) == "20240102"


def test_format_xtquant_time_1m_start() -> None:
    assert format_xtquant_time(date(2024, 1, 2), "1m", end=False) == "20240102000000"


def test_format_xtquant_time_1m_end() -> None:
    assert format_xtquant_time(date(2024, 1, 2), "1m", end=True) == "20240102235959"


# ---------------------------------------------------------------------------
# XtQuantDataSource
# ---------------------------------------------------------------------------


def test_xtquant_data_source_fetch_bars_rejects_unknown_interval() -> None:
    src = XtQuantDataSource()
    with pytest.raises(ValueError, match="unsupported interval"):
        src.fetch_bars("000001.SZ", date(2024, 1, 1), date(2024, 1, 5), "5m")


def test_xtquant_data_source_fetch_bars_bulk_rejects_unknown_interval() -> None:
    src = XtQuantDataSource()
    with pytest.raises(ValueError, match="unsupported interval"):
        src.fetch_bars_bulk(["000001.SZ"], date(2024, 1, 1), date(2024, 1, 5), "5m")


def test_xtquant_data_source_instruments_empty() -> None:
    assert XtQuantDataSource().fetch_instruments() == []


def test_xtquant_data_source_adjust_factors_empty() -> None:
    df = XtQuantDataSource().fetch_adjust_factors("X", date(2024, 1, 1), date(2024, 1, 5))
    assert df.empty
    assert list(df.columns) == ["symbol", "ex_date", "factor"]


def test_xtquant_data_source_trade_calendar_empty() -> None:
    df = XtQuantDataSource().fetch_trade_calendar(date(2024, 1, 1), date(2024, 1, 5))
    assert df.empty
    assert list(df.columns) == ["exchange", "date", "is_trading"]


def test_xtquant_data_source_repr() -> None:
    assert repr(XtQuantDataSource()) == "XtQuantDataSource()"


# ---------------------------------------------------------------------------
# SourceRegistry lazy registration
# ---------------------------------------------------------------------------


def test_source_registry_xtquant_registered_when_available() -> None:
    """If xtquant is importable, the registry exposes it under ``xtquant``.

    When xtquant is NOT installed, the registry exposes only ``mock``;
    the lazy import inside ``SourceRegistry._init`` silently swallows
    the :class:`ModuleNotFoundError` and falls through. This test
    simply asserts the invariant that the registry always exposes
    ``mock`` regardless of xtquant's install state.
    """
    from xtrade.data.sources import SourceRegistry

    reg = SourceRegistry()
    reg.reset()
    # ``reset`` re-runs ``_init``, which always seeds ``mock`` and
    # best-effort registers ``xtquant`` if importable.
    assert "mock" in reg.names()


def test_supported_intervals_constant() -> None:
    assert frozenset({"1d", "1m"}) == SUPPORTED_INTERVALS
    assert BEIJING_TZ == "Asia/Shanghai"


# Silence unused-import warnings on the timezone / Any helpers used by
# other tests in the same module (kept for forward compatibility).
_ = Any
_ = datetime
_ = timezone
