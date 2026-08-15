"""Unit tests for ``scripts/fetch_historical_bars_xtquant.py``."""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from datetime import date

import pandas as pd
import pytest
from scripts.fetch_historical_bars_xtquant import (
    format_xtquant_time,
    merge_xtquant_bars,
    parse_args,
)

# ---------------------------------------------------------------------------
# Helpers for building xtdata-shaped fixtures
# ---------------------------------------------------------------------------

# Asia/Shanghai 2024-01-02 00:00 = epoch ms 1704124800000 (UTC 2024-01-01 16:00)
BEIJING_2024_01_02_MS = 1_704_124_800_000
BEIJING_2024_01_03_MS = 1_704_211_200_000  # +1 day


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


# ---------------------------------------------------------------------------
# merge_xtquant_bars
# ---------------------------------------------------------------------------


def test_merge_xtquant_bars_1d_basic() -> None:
    ret = {
        "000001.SZ": _make_xtquant_frame_1d([BEIJING_2024_01_02_MS]),
        "600000.SH": _make_xtquant_frame_1d([BEIJING_2024_01_02_MS, BEIJING_2024_01_03_MS]),
    }
    out = merge_xtquant_bars(ret, "1d")

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


def test_merge_xtquant_bars_1m_basic() -> None:
    ret = {
        "000001.SZ": _make_xtquant_frame_1m(
            [BEIJING_2024_01_02_MS, BEIJING_2024_01_02_MS + 60_000]
        ),
    }
    out = merge_xtquant_bars(ret, "1m")
    assert len(out) == 2
    assert (out["interval"] == "1m").all()

    # 1m timestamps are tz-aware datetimes, not bare dates.
    assert all(isinstance(t, pd.Timestamp) and t.tzinfo is not None for t in out["time"])


def test_merge_xtquant_bars_skips_none_and_empty() -> None:
    ret = {
        "AAA": None,
        "BBB": pd.DataFrame(),
        "CCC": _make_xtquant_frame_1d([BEIJING_2024_01_02_MS]),
    }
    out = merge_xtquant_bars(ret, "1d")
    assert list(out["symbol"]) == ["CCC"]


def test_merge_xtquant_bars_returns_empty_when_all_empty() -> None:
    out = merge_xtquant_bars({}, "1d")
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


def test_merge_xtquant_bars_tz_conversion_avoids_one_day_offset() -> None:
    """Pin the fix for the millisecond-UTC one-day offset bug."""
    # 0 ms = 1970-01-01 00:00 UTC = 1970-01-01 08:00 BJT
    ret = {"TST": _make_xtquant_frame_1d([0])}
    out = merge_xtquant_bars(ret, "1d")
    assert out["time"].iloc[0] == date(1970, 1, 1), (
        "Expected 1970-01-01 in BJT, NOT 1969-12-31 (the naive-UTC reading)"
    )


def test_merge_xtquant_bars_rejects_unknown_interval() -> None:
    with pytest.raises(ValueError, match="unsupported interval"):
        merge_xtquant_bars({}, "5m")


def test_merge_xtquant_bars_already_has_pre_close() -> None:
    """If xtquant ever returns ``pre_close`` (snake_case) directly, drop it."""
    df = _make_xtquant_frame_1d([BEIJING_2024_01_02_MS])
    df = df.rename(columns={"preClose": "pre_close"})
    ret = {"TST": df}
    out = merge_xtquant_bars(ret, "1d")
    assert "pre_close" not in out.columns


# ---------------------------------------------------------------------------
# format_xtquant_time
# ---------------------------------------------------------------------------


def test_format_xtquant_time_1d_start() -> None:
    assert format_xtquant_time(date(2024, 1, 2), "1d", end=False) == "20240102"


def test_format_xtquant_time_1d_end() -> None:
    assert format_xtquant_time(date(2024, 1, 2), "1d", end=True) == "20240102"


def test_format_xtquant_time_1m_start() -> None:
    # 1m start uses time.min → 00:00:00
    assert format_xtquant_time(date(2024, 1, 2), "1m", end=False) == "20240102000000"


def test_format_xtquant_time_1m_end() -> None:
    # 1m end uses time.max → 23:59:59.999999; strftime on datetime drops sub-seconds.
    assert format_xtquant_time(date(2024, 1, 2), "1m", end=True) == "20240102235959"


# ---------------------------------------------------------------------------
# parse_args + argument validation (no DB / xtquant touched)
# ---------------------------------------------------------------------------


def test_parse_args_rejects_5m_interval() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--interval", "5m", "--start", "2024-01-01", "--end", "2024-01-05"])


def test_parse_args_requires_interval() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--start", "2024-01-01", "--end", "2024-01-05"])


def test_parse_args_accepts_valid() -> None:
    args = parse_args(
        ["--interval", "1d", "--start", "2024-01-01", "--end", "2024-12-31", "--limit", "10"]
    )
    assert args.interval == "1d"
    assert args.start == "2024-01-01"
    assert args.end == "2024-12-31"
    assert args.limit == 10
    assert args.batch_size == 50
    assert args.dry_run is False


# ---------------------------------------------------------------------------
# main() argument validation path (no DB / xtquant needed)
# ---------------------------------------------------------------------------


def test_main_rejects_start_after_end_via_stdout() -> None:
    """``main()`` raises SystemExit on --start > --end before any DB call."""
    from scripts.fetch_historical_bars_xtquant import main

    buf = io.StringIO()
    with pytest.raises(SystemExit) as exc_info, redirect_stdout(buf):
        main(["--interval", "1d", "--start", "2024-12-31", "--end", "2024-01-01"])
    assert exc_info.value.code != 0


def test_main_prints_plan_in_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--dry-run`` short-circuits before xtquant; instrument list is empty in unit-test env."""
    from scripts.fetch_historical_bars_xtquant import main

    # Monkeypatch the symbol list to a small synthetic list so the dry-run
    # prints something concrete without needing a real DB.
    def fake_list_instrument_symbols(limit: int | None = None) -> list[str]:
        return ["AAA", "BBB", "CCC"]

    monkeypatch.setattr(
        "scripts.fetch_historical_bars_xtquant.list_instrument_symbols",
        fake_list_instrument_symbols,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(
            [
                "--interval",
                "1d",
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-05",
                "--dry-run",
                "--batch-size",
                "2",
            ]
        )
    assert rc == 0
    assert "[dry-run] would process 3 symbols in 2 batch(es)" in buf.getvalue()


def test_script_does_not_import_xtquant_at_module_load() -> None:
    """The script must not crash at import time on a machine without xtquant."""
    # If xtquant is not installed, this import would raise ModuleNotFoundError.
    # By separating the import inside ``fetch_and_write_batch``, the module
    # itself loads cleanly. We assert this by importing.
    import scripts.fetch_historical_bars_xtquant as mod  # noqa: F401

    # The module-level scope MUST NOT have ``xtquant`` in ``sys.modules``.
    assert "xtquant" not in sys.modules, "xtquant must be a lazy import"
