"""Tests for market-data repositories."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd
import pytest

from xtrade.data.market_data import (
    AdjustmentFactorRepository,
    Instrument,
    InstrumentRepository,
    KLineRepository,
    PostgresAdjustmentFactorRepository,
    PostgresInstrumentRepository,
    PostgresKLineRepository,
    PostgresTradeCalendarRepository,
    TradeCalendarRepository,
)
from xtrade.data.sources.mock_source import InMemoryMockSource
from xtrade.data.sources.pump import pump

from .conftest import skip_without_db

# ---------------------------------------------------------------------------
# Pure-unit tests (no DB)
# ---------------------------------------------------------------------------


def test_kline_unknown_interval_rejected() -> None:
    repo = PostgresKLineRepository(batch_size=1000)

    with pytest.raises(ValueError, match="unsupported interval"):
        repo.get_bars(symbols=["A"], start=date(2025, 1, 1), end=date(2025, 1, 2), interval="7m")


def test_kline_upsert_empty_returns_zero() -> None:
    repo = PostgresKLineRepository(batch_size=1000)
    assert repo.upsert_bars(pd.DataFrame()) == 0


def test_kline_upsert_missing_columns_raises() -> None:
    repo = PostgresKLineRepository(batch_size=1000)
    with pytest.raises(ValueError, match="missing required columns"):
        repo.upsert_bars(pd.DataFrame({"symbol": ["A"], "time": [datetime.now(UTC)]}))


def test_kline_upsert_mixed_interval_raises() -> None:
    repo = PostgresKLineRepository(batch_size=1000)
    df = pd.DataFrame(
        [
            {
                "symbol": "A",
                "time": datetime(2025, 1, 1, tzinfo=UTC),
                "interval": "1d",
                "open": Decimal("10"),
                "high": Decimal("11"),
                "low": Decimal("9"),
                "close": Decimal("10.5"),
                "volume": 1000,
                "amount": Decimal("10500"),
            },
            {
                "symbol": "A",
                "time": datetime(2025, 1, 1, 0, 1, tzinfo=UTC),
                "interval": "1m",
                "open": Decimal("10"),
                "high": Decimal("11"),
                "low": Decimal("9"),
                "close": Decimal("10.5"),
                "volume": 1000,
                "amount": Decimal("10500"),
            },
        ]
    )
    with pytest.raises(ValueError, match="interval column must be uniform"):
        repo.upsert_bars(df)


def test_kline_count_requires_interval() -> None:
    repo = PostgresKLineRepository(batch_size=1000)
    with pytest.raises(ValueError, match="interval is required"):
        repo.count(symbol="A")


def test_adj_factor_upsert_missing_columns_raises() -> None:
    repo: AdjustmentFactorRepository = PostgresAdjustmentFactorRepository()
    with pytest.raises(ValueError, match="missing required columns"):
        repo.upsert(pd.DataFrame({"symbol": ["A"]}))


def test_calendar_upsert_days_missing_columns_raises() -> None:
    repo: TradeCalendarRepository = PostgresTradeCalendarRepository()
    with pytest.raises(ValueError, match="missing required columns"):
        repo.upsert_days(pd.DataFrame({"date": [date(2025, 1, 1)]}))


def test_instrument_repository_protocol_satisfied() -> None:
    """Static check: ``PostgresInstrumentRepository`` satisfies the protocol."""
    inst = PostgresInstrumentRepository()
    assert isinstance(inst, InstrumentRepository)


# ---------------------------------------------------------------------------
# Integration tests (skipped without DB)
# ---------------------------------------------------------------------------


def _sample_bars(
    symbol: str,
    days: int = 5,
    interval: str = "1d",
    base_minute_offset: int = 0,
) -> pd.DataFrame:
    """Generate sample bars at minute precision.

    For ``interval="1d"`` the time column carries a midnight UTC stamp
    so daily bars and 1-minute bars share the same row generator.
    """
    base = datetime(2025, 1, 1, tzinfo=UTC)
    rows = []
    for i in range(days):
        if interval == "1d":
            t = base.replace(day=1 + i)
        else:  # "1m"
            t = base.replace(day=1, hour=9, minute=base_minute_offset + i)
        rows.append(
            {
                "symbol": symbol,
                "time": t,
                "interval": interval,
                "open": Decimal("10") + i,
                "high": Decimal("11") + i,
                "low": Decimal("9") + i,
                "close": Decimal("10.5") + i,
                "volume": 1000 * (i + 1),
                "amount": Decimal("10500") + i,
            }
        )
    return pd.DataFrame(rows)


@skip_without_db
def test_kline_upsert_and_get_round_trip(engine, schema) -> None:
    repo: KLineRepository = PostgresKLineRepository(batch_size=2)
    df = _sample_bars("AAA", interval="1d")
    written = repo.upsert_bars(df)
    assert written == len(df)

    out = repo.get_bars(
        symbols=["AAA"],
        start=date(2025, 1, 1),
        end=date(2025, 1, 5),
        interval="1d",
    )
    assert "AAA" in out
    assert len(out["AAA"]) == 5
    assert list(out["AAA"].columns) == ["open", "high", "low", "close", "volume", "amount"]
    assert out["AAA"].index.name == "trade_date"


@skip_without_db
def test_kline_1m_routing(engine, schema) -> None:
    repo: KLineRepository = PostgresKLineRepository(batch_size=10)
    df_1m = _sample_bars("AAA", days=5, interval="1m")
    assert repo.upsert_bars(df_1m) == 5

    out = repo.get_bars(
        symbols=["AAA"],
        start=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
        end=datetime(2025, 1, 1, 23, 59, tzinfo=UTC),
        interval="1m",
    )
    assert "AAA" in out
    assert len(out["AAA"]) == 5
    assert out["AAA"].index.name == "ts"
    assert list(out["AAA"].columns) == ["open", "high", "low", "close", "volume", "amount"]


@skip_without_db
def test_kline_routing_isolates_tables(engine, schema) -> None:
    """Writing to ``1d`` must not touch ``1m`` and vice versa."""
    repo: KLineRepository = PostgresKLineRepository(batch_size=10)
    repo.upsert_bars(_sample_bars("AAA", days=3, interval="1d"))

    assert repo.count(symbol="AAA", interval="1d") == 3
    assert repo.count(symbol="AAA", interval="1m") == 0

    repo.upsert_bars(_sample_bars("AAA", days=4, interval="1m"))

    assert repo.count(symbol="AAA", interval="1d") == 3
    assert repo.count(symbol="AAA", interval="1m") == 4


@skip_without_db
def test_kline_upsert_idempotent(engine, schema) -> None:
    repo: KLineRepository = PostgresKLineRepository(batch_size=10)
    repo.upsert_bars(_sample_bars("AAA", interval="1d"))
    repo.upsert_bars(_sample_bars("AAA", interval="1d"))
    assert repo.count(symbol="AAA", interval="1d") == 5


@skip_without_db
def test_kline_update_persists_latest(engine, schema) -> None:
    repo: KLineRepository = PostgresKLineRepository(batch_size=10)
    df = _sample_bars("AAA", interval="1d")
    repo.upsert_bars(df)

    # Update the close of the first bar.
    df.iloc[0, df.columns.get_loc("close")] = Decimal("99")
    repo.upsert_bars(df)

    out = repo.get_bars(
        symbols=["AAA"], start=date(2025, 1, 1), end=date(2025, 1, 1), interval="1d"
    )
    assert float(out["AAA"]["close"].iloc[0]) == 99.0


@skip_without_db
def test_kline_adjust_none_is_raw(engine, schema) -> None:
    repo: KLineRepository = PostgresKLineRepository(batch_size=10)
    repo.upsert_bars(_sample_bars("AAA", interval="1d"))

    out = repo.get_bars(
        symbols=["AAA"],
        start=date(2025, 1, 1),
        end=date(2025, 1, 5),
        interval="1d",
        adjust="none",
    )
    # First close should be the raw stored value.
    assert float(out["AAA"]["close"].iloc[0]) == 10.5


@skip_without_db
def test_kline_adjust_backward_returns_first_bar_unchanged(engine, schema) -> None:
    repo: KLineRepository = PostgresKLineRepository(batch_size=10)
    repo.upsert_bars(_sample_bars("AAA", interval="1d"))

    adj_repo: AdjustmentFactorRepository = PostgresAdjustmentFactorRepository()
    adj_repo.upsert(
        pd.DataFrame([{"symbol": "AAA", "ex_date": date(2025, 1, 3), "factor": Decimal("1.1")}])
    )

    out = repo.get_bars(
        symbols=["AAA"],
        start=date(2025, 1, 1),
        end=date(2025, 1, 5),
        interval="1d",
        adjust="backward",
    )
    # First close is the divisor (factor @ first bar) → close stays at 10.5.
    assert float(out["AAA"]["close"].iloc[0]) == 10.5


@skip_without_db
def test_kline_escapes_csv_specials(engine, schema) -> None:
    """A bar with commas / quotes / newlines must survive the COPY path."""
    repo: KLineRepository = PostgresKLineRepository(batch_size=10)
    base = datetime(2025, 1, 1, tzinfo=UTC)
    weird = pd.DataFrame(
        [
            {
                "symbol": "WEIRD,NAME",
                "time": base,
                "interval": "1d",
                "open": Decimal("1.0"),
                "high": Decimal("2.0"),
                "low": Decimal("0.5"),
                "close": Decimal("1.5"),
                "volume": 10,
                "amount": Decimal("15.0"),
            },
            {
                "symbol": 'has"quote',
                "time": base.replace(day=2),
                "interval": "1d",
                "open": Decimal("1.0"),
                "high": Decimal("2.0"),
                "low": Decimal("0.5"),
                "close": Decimal("1.5"),
                "volume": 10,
                "amount": Decimal("15.0"),
            },
        ]
    )
    written = repo.upsert_bars(weird)
    assert written == 2

    out = repo.get_bars(
        symbols=["WEIRD,NAME", 'has"quote'],
        start=date(2025, 1, 1),
        end=date(2025, 1, 2),
        interval="1d",
    )
    assert not out["WEIRD,NAME"].empty
    assert not out['has"quote'].empty


@skip_without_db
def test_adj_factor_upsert_round_trip(engine, schema) -> None:
    repo: AdjustmentFactorRepository = PostgresAdjustmentFactorRepository()
    df = pd.DataFrame(
        [
            {"symbol": "AAA", "ex_date": date(2025, 1, 2), "factor": Decimal("1.1")},
            {"symbol": "AAA", "ex_date": date(2025, 1, 4), "factor": Decimal("1.21")},
        ]
    )
    assert repo.upsert(df) == 2

    out = repo.get(["AAA"], date(2025, 1, 1), date(2025, 1, 5))
    assert "AAA" in out
    assert len(out["AAA"]) == 2


@skip_without_db
def test_calendar_upsert_and_is_trading_day(engine, schema) -> None:
    repo: TradeCalendarRepository = PostgresTradeCalendarRepository()
    df = pd.DataFrame(
        [
            {"date": date(2025, 1, 1), "is_trading": False},
            {"date": date(2025, 1, 2), "is_trading": True},
        ]
    )
    repo.upsert_days(df)

    assert repo.is_trading_day(date(2025, 1, 2)) is True
    assert repo.is_trading_day(date(2025, 1, 1)) is False
    assert repo.is_trading_day(date(2025, 1, 3)) is False  # not in calendar

    days = repo.get_trading_days(date(2025, 1, 1), date(2025, 1, 5))
    assert days == [date(2025, 1, 2)]


@skip_without_db
def test_instrument_upsert_and_get(engine, schema) -> None:
    repo: InstrumentRepository = PostgresInstrumentRepository()
    record = Instrument(
        symbol="AAA",
        name="Alpha",
        exchange="XSHG",
        type="Stock",
        list_date=date(2020, 1, 1),
        delist_date=None,
        status="L",
    )
    repo.upsert(record)

    fetched = repo.get("AAA")
    assert fetched == record


@skip_without_db
def test_pump_writes_source_into_repositories(engine, schema) -> None:
    """End-to-end: ``pump`` writes a seeded InMemoryMockSource into repos."""
    inst = Instrument(
        symbol="AAA",
        name="Alpha",
        exchange="XSHG",
        type="Stock",
        list_date=date(2020, 1, 1),
        delist_date=None,
        status="L",
    )
    source = InMemoryMockSource(
        instruments=[inst],
        bars={"AAA": _sample_bars("AAA", interval="1d")},
        adj_factors={
            "AAA": pd.DataFrame([{"ex_date": date(2025, 1, 2), "factor": Decimal("1.1")}])
        },
        calendar=pd.DataFrame(
            [
                {"date": date(2025, 1, 1), "is_trading": False},
                {"date": date(2025, 1, 2), "is_trading": True},
            ]
        ),
    )
    kline_repo: KLineRepository = PostgresKLineRepository(batch_size=10)
    adj_repo: AdjustmentFactorRepository = PostgresAdjustmentFactorRepository()
    calendar_repo: TradeCalendarRepository = PostgresTradeCalendarRepository()
    inst_repo: InstrumentRepository = PostgresInstrumentRepository()

    result = pump(
        source,
        instrument_repo=inst_repo,
        kline_repo=kline_repo,
        adj_repo=adj_repo,
        calendar_repo=calendar_repo,
        symbols=["AAA"],
        start=date(2025, 1, 1),
        end=date(2025, 1, 5),
    )
    assert result.instruments == 1
    assert result.bars == 5
    assert result.adjust_factors == 1
    assert result.trade_calendar == 2

    # Pump again — counts should not double.
    result2 = pump(
        source,
        instrument_repo=inst_repo,
        kline_repo=kline_repo,
        adj_repo=adj_repo,
        calendar_repo=calendar_repo,
        symbols=["AAA"],
        start=date(2025, 1, 1),
        end=date(2025, 1, 5),
    )
    assert result2.bars == 5
    assert result2.adjust_factors == 1
