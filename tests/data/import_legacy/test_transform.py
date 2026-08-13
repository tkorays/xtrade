"""Tests for the legacy parquet → xtrade DataFrame normaliser."""

from __future__ import annotations

import pandas as pd
import pytest

from xtrade.data.import_legacy.transform import normalize_frame


def _one_day_row(date_str: str = "2025-01-02", symbol: str = "000001.SZ") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp(date_str),
                "symbol": symbol,
                "open": 10.0,
                "high": 10.5,
                "low": 9.5,
                "close": 10.2,
                "pre_close": 9.9,
                "volume": 1000,
                "amount": 10200.0,
            }
        ]
    )


def _one_minute_row(
    time_str: str = "2025-01-02 09:30:00", symbol: str = "000001.SZ"
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "time": pd.Timestamp(time_str),
                "symbol": symbol,
                "open": 10.0,
                "high": 10.5,
                "low": 9.5,
                "close": 10.2,
                "pre_close": 9.9,
                "volume": 1000,
                "amount": 10200.0,
            }
        ]
    )


def test_normalize_1d_renames_date_and_drops_pre_close() -> None:
    df = _one_day_row()
    out = normalize_frame(df, "1d")
    assert "pre_close" not in out.columns
    assert "date" not in out.columns
    assert "time" in out.columns
    assert out["interval"].iloc[0] == "1d"


def test_normalize_1m_keeps_time_and_drops_pre_close() -> None:
    df = _one_minute_row()
    out = normalize_frame(df, "1m")
    assert "pre_close" not in out.columns
    assert out["interval"].iloc[0] == "1m"


def test_normalize_column_order() -> None:
    out = normalize_frame(_one_day_row(), "1d")
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


def test_normalize_rejects_unknown_interval() -> None:
    with pytest.raises(ValueError, match="unsupported interval"):
        normalize_frame(_one_day_row(), "5m")


def test_normalize_missing_required_columns_raises() -> None:
    df = pd.DataFrame({"symbol": ["A"], "time": [pd.Timestamp("2025-01-01")]})
    with pytest.raises(ValueError, match="missing columns"):
        normalize_frame(df, "1d")


def test_normalize_time_is_utc() -> None:
    out = normalize_frame(_one_day_row("2025-06-01"), "1d")
    assert str(out["time"].dt.tz) == "UTC"
