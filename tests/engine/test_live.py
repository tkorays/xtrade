"""Tests for :mod:`xtrade.engine.live`."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from xtrade.engine import LiveEngine, LiveMarketSource
from xtrade.execution.broker import InMemoryBroker, OrderSide, OrderType
from xtrade.risk import NoOpRiskCheck
from xtrade.strategy.base import Context, Signal

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _QueueSource:
    """A LiveMarketSource backed by a static queue of ticks."""

    def __init__(self, ticks: list[tuple[datetime, dict[str, Decimal]]]) -> None:
        self._ticks = list(ticks)
        self.pulled = 0

    def next_price(self) -> tuple[datetime, dict[str, Decimal]] | None:
        if not self._ticks:
            return None
        self.pulled += 1
        return self._ticks.pop(0)


class _BuyStrategy:
    def __init__(self) -> None:
        self.init_called = 0
        self.bars_seen = 0

    def on_init(self, ctx: Context) -> None:
        self.init_called += 1

    def on_bar(self, bar: Any, ctx: Context) -> list[Signal]:
        self.bars_seen += 1
        return [
            Signal(
                symbol=bar.symbol,
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                order_type=OrderType.MARKET,
            )
        ]


class _RaisingStrategy:
    def on_init(self, ctx: Context) -> None:
        pass

    def on_bar(self, bar: Any, ctx: Context) -> list[Signal]:
        raise RuntimeError("strategy exploded")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drain(engine: LiveEngine, source: _QueueSource) -> None:
    """Run the engine until the source is exhausted, then stop."""

    async def _main() -> None:
        engine.start()
        # Yield to the event loop so the task can run.
        while source._ticks:
            await asyncio.sleep(0.005)
        # One more yield to let the loop finish the last tick.
        await asyncio.sleep(0.005)
        await engine.stop()

    asyncio.run(_main())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_queue_source_satisfies_protocol() -> None:
    """_QueueSource satisfies the LiveMarketSource Protocol."""
    assert isinstance(_QueueSource([]), LiveMarketSource)


def test_live_engine_consumes_three_prices_then_stops() -> None:
    """LiveEngine processes 3 ticks and stops when source is exhausted."""
    source = _QueueSource(
        [
            (datetime(2024, 1, 1, 9, 30, tzinfo=UTC), {"A": Decimal("100")}),
            (datetime(2024, 1, 1, 9, 31, tzinfo=UTC), {"A": Decimal("101")}),
            (datetime(2024, 1, 1, 9, 32, tzinfo=UTC), {"A": Decimal("102")}),
        ]
    )
    broker = InMemoryBroker(run_id="r1", initial_cash=Decimal("1000000"))
    strategy = _BuyStrategy()
    engine = LiveEngine(
        strategy=strategy,
        broker=broker,
        risk=NoOpRiskCheck(),
        source=source,
        run_id="r1",
    )
    _drain(engine, source)
    assert source.pulled == 3
    assert strategy.bars_seen == 3
    assert strategy.init_called == 1
    pos = broker.get_position("A")
    assert pos is not None
    assert pos.quantity == Decimal("3")


def test_live_engine_propagates_broker_exceptions() -> None:
    """A strategy that raises stops the engine and propagates the exception."""
    source = _QueueSource(
        [
            (datetime(2024, 1, 1, 9, 30, tzinfo=UTC), {"A": Decimal("100")}),
        ]
    )
    broker = InMemoryBroker(run_id="r1", initial_cash=Decimal("1000000"))
    engine = LiveEngine(
        strategy=_RaisingStrategy(),
        broker=broker,
        risk=NoOpRiskCheck(),
        source=source,
        run_id="r1",
    )

    async def _main() -> None:
        engine.start()
        # Wait for the task to fail.
        assert engine._task is not None
        with pytest.raises(RuntimeError, match="strategy exploded"):
            await engine._task

    asyncio.run(_main())


def test_live_engine_start_twice_raises() -> None:
    """Starting a running engine raises RuntimeError."""
    source = _QueueSource([])  # never produces a tick
    broker = InMemoryBroker(run_id="r1", initial_cash=Decimal("1000000"))
    engine = LiveEngine(
        strategy=_BuyStrategy(),
        broker=broker,
        risk=NoOpRiskCheck(),
        source=source,
        run_id="r1",
    )

    async def _main() -> None:
        engine.start()
        # Let the loop spin once.
        await asyncio.sleep(0.005)
        with pytest.raises(RuntimeError, match="already running"):
            engine.start()
        await engine.stop()

    asyncio.run(_main())
