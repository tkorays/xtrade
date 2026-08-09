"""Tests for data sources — unit only, no DB required."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from xtrade.data.market_data.instrument import Instrument
from xtrade.data.sources import DataSource, InMemoryMockSource, SourceRegistry


@pytest.fixture
def fresh_registry(monkeypatch: pytest.MonkeyPatch) -> SourceRegistry:
    """Reset the singleton registry between tests."""
    reg = SourceRegistry()
    reg.reset()
    # Always re-seed with mock so tests don't depend on import-time ordering.
    reg.register("mock", InMemoryMockSource())
    return reg


def test_default_registry_has_mock() -> None:
    reg = SourceRegistry()
    assert "mock" in reg
    assert isinstance(reg["mock"], DataSource)


def test_register_and_get(fresh_registry: SourceRegistry) -> None:
    src = InMemoryMockSource()
    fresh_registry.register("custom", src)
    assert fresh_registry.get("custom") is src
    assert "custom" in fresh_registry.names()


def test_get_unknown_raises_keyerror(fresh_registry: SourceRegistry) -> None:
    with pytest.raises(KeyError):
        fresh_registry.get("nope")


def test_register_non_datasource_raises(fresh_registry: SourceRegistry) -> None:
    with pytest.raises(TypeError):
        fresh_registry.register("bad", object())  # type: ignore[arg-type]


def test_in_memory_mock_source_returns_defensive_copies() -> None:
    src = InMemoryMockSource(
        bars={
            "AAA": pd.DataFrame({"time": [pd.Timestamp("2025-01-01", tz="UTC")], "close": [1.0]})
        },
    )
    out1 = src.fetch_bars("AAA", date(2025, 1, 1), date(2025, 1, 2), "1d")
    out1["close"] = 999.0
    out2 = src.fetch_bars("AAA", date(2025, 1, 1), date(2025, 1, 2), "1d")
    assert float(out2["close"].iloc[0]) == 1.0


def test_in_memory_mock_source_instruments_defensive_copy() -> None:
    import dataclasses

    inst = Instrument(
        symbol="A",
        name="alpha",
        exchange="X",
        list_date=date(2020, 1, 1),
        delist_date=None,
        status="active",
    )
    src = InMemoryMockSource(instruments=[inst])
    fetched = src.fetch_instruments()
    # ``Instrument`` is a frozen dataclass; reassign with ``dataclasses.replace``
    # to verify the source's internal copy is unaffected by mutations.
    fetched[0] = dataclasses.replace(fetched[0], name="mutated")
    assert src.fetch_instruments()[0].name == "alpha"


def test_in_memory_mock_source_empty_results() -> None:
    src = InMemoryMockSource()
    assert src.fetch_bars("A", date(2025, 1, 1), date(2025, 1, 2), "1d").empty
    assert src.fetch_adjust_factors("A", date(2025, 1, 1), date(2025, 1, 2)).empty
    assert src.fetch_trade_calendar(date(2025, 1, 1), date(2025, 1, 2)).empty
