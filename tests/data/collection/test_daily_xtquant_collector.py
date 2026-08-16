"""Unit tests for ``DailyXtQuantCollector``.

The collector wires a :class:`DataSource` to several repositories; we
stub each one so the tests run without xtquant, Postgres, or a real
``DataSource`` instance. The collectors's pure-Python control flow
(window resolution, watermark updates, error handling) is what matters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
import pytest

from xtrade.data.collection.xtquant import (
    DEFAULT_LOOKBACK_DAYS,
    MAX_LOOKBACK_DAYS,
    SLOW_FETCH_SECONDS,
    SLOW_UPSERT_SECONDS,
    VERBOSE_SYMBOL_PROGRESS_FORMAT,
    DailyXtQuantCollector,
    SyncReport,
)
from xtrade.data.market_data.instrument import Instrument
from xtrade.data.sources.base import DataSource
from xtrade.data.sync_state import DataSyncState

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeSource:
    """A :class:`DataSource` test double whose behaviour is programmable."""

    instruments: list[Instrument] = field(default_factory=list)
    bars_by_symbol: dict[str, pd.DataFrame] = field(default_factory=dict)
    errors_by_symbol: dict[str, Exception] = field(default_factory=dict)
    fetch_calls: list[tuple[str, date, date, str]] = field(default_factory=list)

    def fetch_instruments(self) -> list[Instrument]:
        return list(self.instruments)

    def fetch_bars(self, symbol: str, start: date, end: date, interval: str) -> pd.DataFrame:
        self.fetch_calls.append((symbol, start, end, interval))
        if symbol in self.errors_by_symbol:
            raise self.errors_by_symbol[symbol]
        df = self.bars_by_symbol.get(symbol)
        if df is None:
            return pd.DataFrame()
        return df.copy()

    def fetch_adjust_factors(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame(columns=["symbol", "ex_date", "factor"])

    def fetch_trade_calendar(self, start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame(columns=["exchange", "date", "is_trading"])


class FakeInstrumentRepo:
    def __init__(self, instruments: list[Instrument]) -> None:
        self._instruments = instruments

    def upsert(self, record: Instrument) -> None:  # pragma: no cover - not used
        pass

    def get(self, symbol: str) -> Instrument | None:  # pragma: no cover
        return next((i for i in self._instruments if i.symbol == symbol), None)

    def list_all(self) -> list[Instrument]:
        return list(self._instruments)


class FakeKLineRepo:
    def __init__(self) -> None:
        self.upserted: list[pd.DataFrame] = []

    def upsert_bars(self, df: pd.DataFrame) -> int:
        self.upserted.append(df.copy())
        return len(df)

    def get_bars(  # pragma: no cover - not used
        self,
        symbols: list[str],
        start: Any,
        end: Any,
        interval: str,
        adjust: str = "none",
    ) -> dict[str, pd.DataFrame]:
        return {}


class FakeSyncStateRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], DataSyncState] = {}

    def get(self, source: str, interval: str) -> DataSyncState | None:
        return self.rows.get((source, interval))

    def upsert(self, record: DataSyncState) -> None:
        self.rows[(record.source, record.interval)] = record

    def delete(self, source: str, interval: str) -> bool:  # pragma: no cover
        return self.rows.pop((source, interval), None) is not None

    def list_all(self) -> list[DataSyncState]:  # pragma: no cover
        return list(self.rows.values())


class FakeTradeCalendar:
    def __init__(self, trading_days: list[date]) -> None:
        self._trading_days = sorted(trading_days)

    def upsert_days(self, df: pd.DataFrame) -> int:  # pragma: no cover
        return 0

    def is_trading_day(self, d: date) -> bool:  # pragma: no cover
        return d in self._trading_days

    def get_trading_days(self, start: date, end: date) -> list[date]:
        return [d for d in self._trading_days if start <= d <= end]


def _make_instrument(symbol: str) -> Instrument:
    return Instrument(
        symbol=symbol,
        name=symbol,
        exchange="SH",
        type="Stock",
        list_date=date(2020, 1, 1),
        delist_date=None,
        status="L",
    )


def _make_bars_frame(dates: list[date], interval: str) -> pd.DataFrame:
    """Build a frame in the shape :class:`XtQuantDataSource.fetch_bars` returns.

    Per the spec, ``fetch_bars`` returns a per-symbol frame WITHOUT a
    ``symbol`` column; the collector re-attaches ``symbol`` and the
    ``interval`` column. ``time`` here is the normalised form
    (``datetime.date`` for ``1d``; tz-aware ``pd.Timestamp`` for ``1m``).
    """
    if interval == "1d":
        time_values: list[Any] = dates
    else:
        time_values = [
            pd.Timestamp(datetime.combine(d, datetime.min.time()), tz="Asia/Shanghai")
            for d in dates
        ]
    return pd.DataFrame(
        {
            "time": time_values,
            "open": [10.0] * len(dates),
            "high": [11.0] * len(dates),
            "low": [9.0] * len(dates),
            "close": [10.5] * len(dates),
            "volume": [1_000] * len(dates),
            "amount": [10_500.0] * len(dates),
        }
    )


def _build_collector(
    *,
    source: FakeSource,
    watermark: DataSyncState | None = None,
    trading_days: list[date] | None = None,
    today: date | None = None,
    slow_fetch_seconds: float | None = None,
    slow_upsert_seconds: float | None = None,
) -> tuple[DailyXtQuantCollector, FakeKLineRepo, FakeSyncStateRepo]:
    instrument_repo = FakeInstrumentRepo(source.instruments)
    kline_repo = FakeKLineRepo()
    sync_state_repo = FakeSyncStateRepo()
    if watermark is not None:
        sync_state_repo.upsert(watermark)
    if today is None:
        today = datetime.now(UTC).date()
    trading = trading_days or [today - timedelta(days=d) for d in range(MAX_LOOKBACK_DAYS + 5)]
    trade_calendar = FakeTradeCalendar(trading)
    fixed_now = datetime(today.year, today.month, today.day, tzinfo=UTC)
    collector_kwargs: dict[str, Any] = {"clock": lambda: fixed_now}
    if slow_fetch_seconds is not None:
        collector_kwargs["slow_fetch_seconds"] = slow_fetch_seconds
    if slow_upsert_seconds is not None:
        collector_kwargs["slow_upsert_seconds"] = slow_upsert_seconds
    collector = DailyXtQuantCollector(
        source=source,  # type: ignore[arg-type]  # FakeSource satisfies the Protocol structurally
        instrument_repo=instrument_repo,  # type: ignore[arg-type]
        kline_repo=kline_repo,  # type: ignore[arg-type]
        sync_state_repo=sync_state_repo,  # type: ignore[arg-type]
        trade_calendar=trade_calendar,  # type: ignore[arg-type]
        **collector_kwargs,
    )
    return collector, kline_repo, sync_state_repo


# Re-import here so the tests can patch; ``timedelta`` is referenced in
# ``_build_collector`` so we keep this import close to the fakes block.


# ---------------------------------------------------------------------------
# run() input validation
# ---------------------------------------------------------------------------


def test_run_rejects_unknown_interval() -> None:
    src = FakeSource(instruments=[])
    collector, _, _ = _build_collector(source=src)
    with pytest.raises(ValueError, match="interval must be one of"):
        collector.run("5m")


def test_run_rejects_lookback_days_too_high() -> None:
    src = FakeSource(instruments=[])
    collector, _, _ = _build_collector(source=src)
    with pytest.raises(ValueError, match="lookback_days must be <="):
        collector.run("1d", lookback_days=MAX_LOOKBACK_DAYS + 1)


def test_run_rejects_lookback_days_below_one() -> None:
    src = FakeSource(instruments=[])
    collector, _, _ = _build_collector(source=src)
    with pytest.raises(ValueError, match="lookback_days must be >="):
        collector.run("1d", lookback_days=0)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_run_no_watermark_uses_lookback_days_window() -> None:
    today = datetime.now(UTC).date()
    # ``dates`` is sorted descending (most recent first). The fake source
    # returns ``dates[-3:]`` which is the three oldest entries in this
    # list, i.e. ``[D-7, D-8, D-9]``. The collector's ``last_trade_date``
    # is therefore ``D-7`` (the max), NOT ``D-9``.
    dates = [today - timedelta(days=d) for d in range(DEFAULT_LOOKBACK_DAYS + 5)]
    src = FakeSource(
        instruments=[_make_instrument("AAA")],
        bars_by_symbol={
            "AAA": _make_bars_frame(dates[-3:], "1d"),  # last 3 trading days
        },
    )
    collector, kline_repo, sync_state_repo = _build_collector(
        source=src, trading_days=dates, today=today
    )

    report = collector.run("1d")
    assert isinstance(report, SyncReport)
    assert report.status == "ok"
    assert report.rows_written == 3
    assert report.symbols_skipped == []
    # Watermark advanced to the latest bar's date (``max(dates[-3:])``).
    assert report.last_trade_date == dates[-3]

    # KLineRepository.upsert_bars was called once (one batch).
    assert len(kline_repo.upserted) == 1
    # The upserted frame has the right columns.
    df = kline_repo.upserted[0]
    assert list(df["symbol"].unique()) == ["AAA"]
    assert (df["interval"] == "1d").all()

    # Watermark row was updated.
    wm = sync_state_repo.get("xtquant", "1d")
    assert wm is not None
    assert wm.last_trade_date == dates[-3]
    assert wm.rows_written == 3
    assert wm.status == "ok"


def test_run_with_existing_watermark_resumes_from_lookback() -> None:
    today = datetime.now(UTC).date()
    last_td = today - timedelta(days=10)
    dates = [today - timedelta(days=d) for d in range(20)]
    src = FakeSource(
        instruments=[_make_instrument("AAA")],
        bars_by_symbol={"AAA": _make_bars_frame([today - timedelta(days=2)], "1d")},
    )
    watermark = DataSyncState(
        source="xtquant",
        interval="1d",
        last_trade_date=last_td,
        last_run_at=datetime(2024, 1, 1, tzinfo=UTC),
        rows_written=100,
        status="ok",
        error=None,
    )
    collector, _, sync_state_repo = _build_collector(
        source=src, watermark=watermark, trading_days=dates
    )

    report = collector.run("1d", lookback_days=3)
    assert report.status == "ok"
    # ``source.fetch_bars`` was called with start = last_td - 3 days,
    # end = today.
    assert len(src.fetch_calls) == 1
    sym, start, end_arg, interval = src.fetch_calls[0]
    assert sym == "AAA"
    assert interval == "1d"
    assert (end_arg - start).days == (today - (last_td - timedelta(days=3))).days

    wm = sync_state_repo.get("xtquant", "1d")
    assert wm is not None
    assert wm.last_trade_date == today - timedelta(days=2)


def test_run_partial_failure_records_skipped() -> None:
    today = datetime.now(UTC).date()
    dates = [today - timedelta(days=d) for d in range(MAX_LOOKBACK_DAYS + 5)]
    src = FakeSource(
        instruments=[_make_instrument("AAA"), _make_instrument("BAD")],
        bars_by_symbol={"AAA": _make_bars_frame([today - timedelta(days=1)], "1d")},
        errors_by_symbol={"BAD": RuntimeError("simulated fetch error")},
    )
    collector, kline_repo, sync_state_repo = _build_collector(source=src, trading_days=dates)

    report = collector.run("1d")
    assert report.status == "ok"
    assert report.rows_written == 1
    assert report.skipped_count == 1
    assert report.symbols_skipped[0][0] == "BAD"
    assert "simulated fetch error" in report.symbols_skipped[0][1]

    # Watermark advanced; the run is NOT marked failed.
    wm = sync_state_repo.get("xtquant", "1d")
    assert wm is not None
    assert wm.status == "ok"
    assert wm.error is None

    # ``upsert_bars`` was called once (with one symbol's frame).
    assert len(kline_repo.upserted) == 1


def test_run_no_rows_marks_failed() -> None:
    today = datetime.now(UTC).date()
    dates = [today - timedelta(days=d) for d in range(MAX_LOOKBACK_DAYS + 5)]
    src = FakeSource(
        instruments=[_make_instrument("AAA"), _make_instrument("BBB")],
        bars_by_symbol={},  # every symbol returns empty
    )
    collector, _, sync_state_repo = _build_collector(source=src, trading_days=dates, today=today)

    report = collector.run("1d")
    assert report.status == "failed"
    assert report.rows_written == 0
    assert report.symbols_skipped == []

    wm = sync_state_repo.get("xtquant", "1d")
    assert wm is not None
    assert wm.status == "failed"
    assert wm.error is not None
    assert "no rows written" in wm.error


def test_run_dry_run_does_not_write() -> None:
    today = datetime.now(UTC).date()
    dates = [today - timedelta(days=d) for d in range(MAX_LOOKBACK_DAYS + 5)]
    src = FakeSource(
        instruments=[_make_instrument("AAA")],
        bars_by_symbol={"AAA": _make_bars_frame([today - timedelta(days=1)], "1d")},
    )
    collector, kline_repo, sync_state_repo = _build_collector(source=src, trading_days=dates)

    report = collector.run("1d", dry_run=True)
    assert report.dry_run is True
    assert report.rows_written == 0
    # No upsert calls.
    assert kline_repo.upserted == []
    # No watermark was written.
    assert sync_state_repo.get("xtquant", "1d") is None


def test_run_no_instruments_reports_ok_no_rows() -> None:
    src = FakeSource(instruments=[])
    collector, kline_repo, _ = _build_collector(source=src)

    report = collector.run("1d")
    assert report.rows_written == 0
    assert report.symbols_skipped == []
    assert kline_repo.upserted == []


def test_run_batches_respect_batch_size() -> None:
    today = datetime.now(UTC).date()
    dates = [today - timedelta(days=d) for d in range(MAX_LOOKBACK_DAYS + 5)]
    symbols = [_make_instrument(f"S{i:02d}") for i in range(5)]
    src = FakeSource(
        instruments=symbols,
        bars_by_symbol={
            s.symbol: _make_bars_frame([today - timedelta(days=1)], "1d") for s in symbols
        },
    )
    collector, kline_repo, _ = _build_collector(source=src, trading_days=dates)

    report = collector.run("1d", batch_size=2)
    # 5 symbols in chunks of 2 -> 3 batches.
    assert report.rows_written == 5
    assert len(kline_repo.upserted) == 3


# ---------------------------------------------------------------------------
# Window overrides (--start-date / --end-date) and ad-hoc backfill
# ---------------------------------------------------------------------------


def test_run_start_and_end_date_override_window() -> None:
    src = FakeSource(
        instruments=[_make_instrument("AAA")],
        bars_by_symbol={"AAA": _make_bars_frame([date(2024, 1, 15)], "1d")},
    )
    collector, _, _ = _build_collector(source=src)

    report = collector.run(
        "1d",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
    )
    assert report.status == "ok"
    assert len(src.fetch_calls) == 1
    sym, start, end_arg, interval = src.fetch_calls[0]
    assert sym == "AAA"
    assert start == date(2024, 1, 1)
    assert end_arg == date(2024, 1, 31)
    assert interval == "1d"


def test_run_only_end_date_keeps_watermark_leading_edge() -> None:
    today = datetime.now(UTC).date()
    last_td = today - timedelta(days=10)
    dates = [today - timedelta(days=d) for d in range(20)]
    src = FakeSource(
        instruments=[_make_instrument("AAA")],
        bars_by_symbol={"AAA": _make_bars_frame([date(2024, 1, 5)], "1d")},
    )
    watermark = DataSyncState(
        source="xtquant",
        interval="1d",
        last_trade_date=last_td,
        last_run_at=datetime(2024, 1, 1, tzinfo=UTC),
        rows_written=100,
        status="ok",
        error=None,
    )
    collector, _, _ = _build_collector(source=src, watermark=watermark, trading_days=dates)

    report = collector.run("1d", end_date=date(2024, 1, 10), lookback_days=3)
    assert report.status == "ok"
    assert len(src.fetch_calls) == 1
    _, start, end_arg, _ = src.fetch_calls[0]
    assert start == last_td - timedelta(days=3)
    assert end_arg == date(2024, 1, 10)


def test_run_only_start_date_ignores_watermark() -> None:
    today = datetime.now(UTC).date()
    watermark = DataSyncState(
        source="xtquant",
        interval="1d",
        last_trade_date=today - timedelta(days=100),
        last_run_at=datetime(2024, 1, 1, tzinfo=UTC),
        rows_written=999,
        status="ok",
        error=None,
    )
    src = FakeSource(
        instruments=[_make_instrument("AAA")],
        bars_by_symbol={"AAA": _make_bars_frame([today], "1d")},
    )
    collector, _, _ = _build_collector(source=src, watermark=watermark)

    report = collector.run("1d", start_date=today - timedelta(days=2))
    assert report.status == "ok"
    assert len(src.fetch_calls) == 1
    _, start, end_arg, _ = src.fetch_calls[0]
    assert start == today - timedelta(days=2)
    assert end_arg == today


def test_run_start_after_end_raises_before_any_io() -> None:
    src = FakeSource(
        instruments=[_make_instrument("AAA")],
        bars_by_symbol={"AAA": _make_bars_frame([date(2024, 1, 5)], "1d")},
    )
    collector, _, sync_state_repo = _build_collector(source=src)

    with pytest.raises(ValueError, match=r"start_date .* must be <= end_date"):
        collector.run("1d", start_date=date(2024, 2, 1), end_date=date(2024, 1, 1))

    assert src.fetch_calls == []
    assert sync_state_repo.get("xtquant", "1d") is None


def test_adhoc_run_leaves_existing_watermark_untouched() -> None:
    original_last = date(2023, 12, 29)
    original_run_at = datetime(2023, 12, 29, 16, 0, 0, tzinfo=UTC)
    watermark = DataSyncState(
        source="xtquant",
        interval="1d",
        last_trade_date=original_last,
        last_run_at=original_run_at,
        rows_written=42,
        status="ok",
        error=None,
    )
    src = FakeSource(
        instruments=[_make_instrument("AAA")],
        bars_by_symbol={"AAA": _make_bars_frame([date(2024, 1, 15)], "1d")},
    )
    collector, _, sync_state_repo = _build_collector(source=src, watermark=watermark)

    report = collector.run("1d", start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
    assert report.status == "ok"
    assert report.rows_written == 1

    wm = sync_state_repo.get("xtquant", "1d")
    assert wm is not None
    assert wm.last_trade_date == original_last
    assert wm.last_run_at == original_run_at
    assert wm.rows_written == 42
    assert wm.status == "ok"
    assert wm.error is None


def test_adhoc_run_creates_no_watermark_when_none_exists() -> None:
    src = FakeSource(
        instruments=[_make_instrument("AAA")],
        bars_by_symbol={"AAA": _make_bars_frame([date(2024, 1, 15)], "1d")},
    )
    collector, _, sync_state_repo = _build_collector(source=src)

    report = collector.run("1d", start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
    assert report.status == "ok"
    assert sync_state_repo.get("xtquant", "1d") is None


# ---------------------------------------------------------------------------
# Structural typing — confirm FakeSource satisfies DataSource
# ---------------------------------------------------------------------------


def test_fake_source_satisfies_data_source_protocol() -> None:
    src = FakeSource()
    assert isinstance(src, DataSource)


# ---------------------------------------------------------------------------
# Progress and slow-call logging
# ---------------------------------------------------------------------------

_LOGGER_NAME = "xtrade.data.collection.xtquant"


def _records_with_substring(
    records: list[logging.LogRecord], needle: str
) -> list[logging.LogRecord]:
    return [r for r in records if needle in r.getMessage()]


def test_run_start_and_end_info_lines(caplog: pytest.LogCaptureFixture) -> None:
    today = datetime.now(UTC).date()
    dates = [today - timedelta(days=d) for d in range(MAX_LOOKBACK_DAYS + 5)]
    src = FakeSource(
        instruments=[_make_instrument("AAA")],
        bars_by_symbol={"AAA": _make_bars_frame([today], "1d")},
    )
    collector, _, _ = _build_collector(source=src, trading_days=dates)

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        report = collector.run("1d")
    assert report.status == "ok"

    msgs = [r.getMessage() for r in caplog.records if r.name == _LOGGER_NAME]
    assert any(m.startswith("sync start:") and "mode=routine" in m for m in msgs)
    assert any(
        m.startswith("sync done:") and "status=ok" in m and "mode=routine" in m for m in msgs
    )


def test_run_ad_hoc_mode_logged(caplog: pytest.LogCaptureFixture) -> None:
    src = FakeSource(
        instruments=[_make_instrument("AAA")],
        bars_by_symbol={"AAA": _make_bars_frame([date(2024, 1, 15)], "1d")},
    )
    collector, _, _ = _build_collector(source=src)

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        collector.run("1d", start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))

    msgs = [r.getMessage() for r in caplog.records if r.name == _LOGGER_NAME]
    assert any("mode=ad-hoc" in m for m in msgs)


def test_per_batch_info_line(caplog: pytest.LogCaptureFixture) -> None:
    today = datetime.now(UTC).date()
    dates = [today - timedelta(days=d) for d in range(MAX_LOOKBACK_DAYS + 5)]
    symbols = [_make_instrument(f"S{i:02d}") for i in range(5)]
    src = FakeSource(
        instruments=symbols,
        bars_by_symbol={s.symbol: _make_bars_frame([today], "1d") for s in symbols},
    )
    collector, _, _ = _build_collector(source=src, trading_days=dates)

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        collector.run("1d", batch_size=2)

    batch_msgs = [r.getMessage() for r in caplog.records if r.getMessage().startswith("batch ")]
    assert len(batch_msgs) == 3
    assert "1/3" in batch_msgs[0] and "symbols_done=2/5" in batch_msgs[0]
    assert "2/3" in batch_msgs[1] and "symbols_done=4/5" in batch_msgs[1]
    assert "3/3" in batch_msgs[2] and "symbols_done=5/5" in batch_msgs[2]


def test_slow_fetch_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Threshold=0 makes every non-zero ``fetch_bars`` call trip the WARN."""
    src = FakeSource(
        instruments=[_make_instrument("AAA")],
        bars_by_symbol={"AAA": _make_bars_frame([date(2024, 1, 5)], "1d")},
    )
    collector, _, _ = _build_collector(source=src, slow_fetch_seconds=0.0)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        collector.run("1d")

    warns = _records_with_substring(caplog.records, "slow fetch")
    assert any("symbol=AAA" in r.getMessage() for r in warns)
    # The collector must still report success; the WARN is non-fatal.
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_slow_upsert_warning(caplog: pytest.LogCaptureFixture) -> None:
    src = FakeSource(
        instruments=[_make_instrument("AAA")],
        bars_by_symbol={"AAA": _make_bars_frame([date(2024, 1, 5)], "1d")},
    )
    collector, _, _ = _build_collector(source=src, slow_upsert_seconds=0.0)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        collector.run("1d")

    warns = _records_with_substring(caplog.records, "slow upsert")
    assert any("batch_rows=" in r.getMessage() for r in warns)


def test_no_per_batch_logs_when_dry_run(caplog: pytest.LogCaptureFixture) -> None:
    src = FakeSource(
        instruments=[_make_instrument("AAA")],
        bars_by_symbol={"AAA": _make_bars_frame([date(2024, 1, 5)], "1d")},
    )
    collector, _, _ = _build_collector(source=src)

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        collector.run("1d", dry_run=True)

    msgs = [r.getMessage() for r in caplog.records if r.name == _LOGGER_NAME]
    assert not any(m.startswith("sync start:") for m in msgs)
    assert not any(m.startswith("batch ") for m in msgs)
    assert not any(m.startswith("sync done:") for m in msgs)
    # Only the pre-existing dry-run line.
    assert any("[dry-run]" in m for m in msgs)


def test_default_thresholds_match_constants() -> None:
    """The collector's defaults match the module-level constants."""
    src = FakeSource(instruments=[])
    collector, _, _ = _build_collector(source=src)
    # Touch the internal attrs (defined as private; tests own this).
    assert collector._slow_fetch_seconds == SLOW_FETCH_SECONDS  # type: ignore[attr-defined]
    assert collector._slow_upsert_seconds == SLOW_UPSERT_SECONDS  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Per-symbol DEBUG progress line
# ---------------------------------------------------------------------------


def test_debug_line_per_symbol(caplog: pytest.LogCaptureFixture) -> None:
    """When DEBUG is enabled, one line per symbol is emitted before each
    ``fetch_bars`` call. The line carries the run-wide 1-based index and
    the symbol name."""
    today = datetime.now(UTC).date()
    dates = [today - timedelta(days=d) for d in range(MAX_LOOKBACK_DAYS + 5)]
    symbols = [_make_instrument(f"S{i:02d}") for i in range(3)]
    src = FakeSource(
        instruments=symbols,
        bars_by_symbol={s.symbol: _make_bars_frame([today], "1d") for s in symbols},
    )
    collector, _, _ = _build_collector(source=src, trading_days=dates)

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        report = collector.run("1d")
    assert report.status == "ok"

    debug_msgs = [
        r.getMessage()
        for r in caplog.records
        if r.name == _LOGGER_NAME and r.levelno == logging.DEBUG
    ]
    # Exactly one DEBUG line per symbol in the run.
    assert len(debug_msgs) == 3
    assert "sym=S00" in debug_msgs[0] and "1/3" in debug_msgs[0]
    assert "sym=S01" in debug_msgs[1] and "2/3" in debug_msgs[1]
    assert "sym=S02" in debug_msgs[2] and "3/3" in debug_msgs[2]
    # The interval appears in every line.
    for msg in debug_msgs:
        assert "interval=1d" in msg


def test_debug_line_suppressed_at_info_level(caplog: pytest.LogCaptureFixture) -> None:
    """At the default INFO level, no per-symbol DEBUG records are
    captured. The DEBUG lines are still emitted but discarded by the
    logger."""
    today = datetime.now(UTC).date()
    dates = [today - timedelta(days=d) for d in range(MAX_LOOKBACK_DAYS + 5)]
    symbols = [_make_instrument("AAA"), _make_instrument("BBB")]
    src = FakeSource(
        instruments=symbols,
        bars_by_symbol={s.symbol: _make_bars_frame([today], "1d") for s in symbols},
    )
    collector, _, _ = _build_collector(source=src, trading_days=dates)

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        collector.run("1d")

    debug_msgs = [
        r.getMessage()
        for r in caplog.records
        if r.name == _LOGGER_NAME and r.levelno == logging.DEBUG
    ]
    assert debug_msgs == []
    # Per-batch INFO records still fired.
    info_batch = [r.getMessage() for r in caplog.records if r.getMessage().startswith("batch ")]
    assert len(info_batch) == 1


def test_verbose_format_constant_contains_required_keys() -> None:
    """The format string carries the three keys the spec requires."""
    assert isinstance(VERBOSE_SYMBOL_PROGRESS_FORMAT, str)
    assert "symbol" in VERBOSE_SYMBOL_PROGRESS_FORMAT
    assert "sym" in VERBOSE_SYMBOL_PROGRESS_FORMAT
    assert "interval" in VERBOSE_SYMBOL_PROGRESS_FORMAT
