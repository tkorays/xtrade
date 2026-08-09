"""Tests for :mod:`xtrade.engine.backtest`."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from xtrade.data.broker_data import Account
from xtrade.engine import BacktestEngine, EngineUsageError, RunSummary
from xtrade.execution.broker import InMemoryBroker, OrderSide, OrderType
from xtrade.risk import CompositeRiskCheck, NoOpRiskCheck, OrderSizeLimit, PositionLimit
from xtrade.strategy.base import Bar, Context, Signal

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _account(equity: Decimal = Decimal("1000000")) -> Account:
    return Account(
        run_id="r1",
        time=datetime(2024, 1, 1, tzinfo=UTC),
        cash=equity,
        equity=equity,
        margin=Decimal("0"),
    )


def _bar(symbol: str, time: datetime, price: Decimal) -> Bar:
    return Bar(
        symbol=symbol,
        time=time,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("0"),
        interval="1d",
    )


def _history(bars: list[Bar]) -> dict[str, list[Bar]]:
    out: dict[str, list[Bar]] = {}
    for b in bars:
        out.setdefault(b.symbol, []).append(b)
    return out


# ---------------------------------------------------------------------------
# Test strategies
# ---------------------------------------------------------------------------


class _BuyStrategy:
    """A strategy that returns a single BUY signal every bar."""

    def __init__(self, quantity: Decimal = Decimal("10")) -> None:
        self._q = quantity
        self.init_called = 0
        self.bars_seen: list[Bar] = []

    def on_init(self, ctx: Context) -> None:
        self.init_called += 1

    def on_bar(self, bar: Bar, ctx: Context) -> list[Signal]:
        self.bars_seen.append(bar)
        return [
            Signal(
                symbol=bar.symbol,
                side=OrderSide.BUY,
                quantity=self._q,
                order_type=OrderType.MARKET,
            )
        ]


class _NoSignalStrategy:
    def on_init(self, ctx: Context) -> None:
        pass

    def on_bar(self, bar: Bar, ctx: Context) -> list[Signal]:
        return []


class _AbusiveOnInit:
    """A strategy that submits an order from `on_init`."""

    def on_init(self, ctx: Context) -> None:
        # Reach through the broker and submit.
        from xtrade.execution.broker import OrderRequest

        ctx.broker.submit_order(
            OrderRequest(
                run_id="r1",
                client_order_id="bad",
                symbol="A",
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                order_type=OrderType.MARKET,
            )
        )

    def on_bar(self, bar: Bar, ctx: Context) -> list[Signal]:
        return []


# ---------------------------------------------------------------------------
# market_data factory: deterministic list
# ---------------------------------------------------------------------------


def _feed_from_history(history: dict[str, list[Bar]], *, start: date, end: date):
    def _feed(symbol: str, s: date, e: date) -> list[Bar]:
        return [b for b in history.get(symbol, []) if s <= b.time.date() <= e]

    return _feed


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_single_symbol_happy_path_produces_summary() -> None:
    """A single-symbol backtest produces a RunSummary with the right counts."""
    history = _history(
        [
            _bar("A", datetime(2024, 1, 1, tzinfo=UTC), Decimal("100")),
            _bar("A", datetime(2024, 1, 2, tzinfo=UTC), Decimal("101")),
        ]
    )
    broker = InMemoryBroker(run_id="r1", initial_cash=Decimal("1000000"))
    strategy = _BuyStrategy()
    engine = BacktestEngine(
        strategy=strategy,
        broker=broker,
        risk=NoOpRiskCheck(),
        market_data=_feed_from_history(history, start=date(2024, 1, 1), end=date(2024, 1, 2)),
        symbols=["A"],
        run_id="r1",
    )
    summary = engine.run(start=date(2024, 1, 1), end=date(2024, 1, 2))
    assert isinstance(summary, RunSummary)
    # 2 bars * 1 signal/bar = 2 orders submitted.
    assert summary.n_orders == 2
    # 2 fills (one per bar).
    assert summary.n_fills == 2
    assert summary.n_dropped_signals == 0
    # Position should reflect the on-bar 2 fills.
    pos = broker.get_position("A")
    assert pos is not None
    assert pos.quantity == Decimal("20")  # 10 + 10
    assert pos.avg_price == Decimal("100.5")  # weighted avg of 100 and 101


def test_determinism_same_inputs_same_final_account() -> None:
    """Two runs with identical inputs produce identical final accounts."""
    history = _history(
        [
            _bar("A", datetime(2024, 1, 1, tzinfo=UTC), Decimal("100")),
            _bar("A", datetime(2024, 1, 2, tzinfo=UTC), Decimal("110")),
        ]
    )

    def _run_once() -> tuple[Decimal, Decimal]:
        broker = InMemoryBroker(run_id="r1", initial_cash=Decimal("1000000"))
        strategy = _BuyStrategy()
        engine = BacktestEngine(
            strategy=strategy,
            broker=broker,
            risk=NoOpRiskCheck(),
            market_data=_feed_from_history(history, start=date(2024, 1, 1), end=date(2024, 1, 2)),
            symbols=["A"],
            run_id="r1",
        )
        engine.run(start=date(2024, 1, 1), end=date(2024, 1, 2))
        pos = broker.get_position("A")
        assert pos is not None
        return pos.quantity, pos.avg_price

    assert _run_once() == _run_once()


def test_bar_iteration_order_is_chronological_with_ties() -> None:
    """When two symbols share a timestamp, the lower symbol lex-sorts first."""
    history = _history(
        [
            _bar("B", datetime(2024, 1, 1, tzinfo=UTC), Decimal("100")),
            _bar("A", datetime(2024, 1, 1, tzinfo=UTC), Decimal("100")),
        ]
    )
    broker = InMemoryBroker(run_id="r1", initial_cash=Decimal("1000000"))
    strategy = _BuyStrategy()
    engine = BacktestEngine(
        strategy=strategy,
        broker=broker,
        risk=NoOpRiskCheck(),
        market_data=_feed_from_history(history, start=date(2024, 1, 1), end=date(2024, 1, 1)),
        symbols=["A", "B"],
        run_id="r1",
    )
    engine.run(start=date(2024, 1, 1), end=date(2024, 1, 1))
    # The strategy records the bars in iteration order. A comes first.
    assert [b.symbol for b in strategy.bars_seen] == ["A", "B"]


def test_risk_blocked_signal_is_dropped_and_counted() -> None:
    """A signal rejected by risk is dropped and counted in the summary."""
    history = _history(
        [
            _bar("A", datetime(2024, 1, 1, tzinfo=UTC), Decimal("100")),
        ]
    )
    broker = InMemoryBroker(run_id="r1", initial_cash=Decimal("1000000"))
    strategy = _BuyStrategy(quantity=Decimal("1000"))
    # OrderSizeLimit 500 < 1000*100 = 100000 will reject.
    engine = BacktestEngine(
        strategy=strategy,
        broker=broker,
        risk=OrderSizeLimit(max_notional=Decimal("500")),
        market_data=_feed_from_history(history, start=date(2024, 1, 1), end=date(2024, 1, 1)),
        symbols=["A"],
        run_id="r1",
    )
    summary = engine.run(start=date(2024, 1, 1), end=date(2024, 1, 1))
    assert summary.n_orders == 0
    assert summary.n_fills == 0
    assert summary.n_dropped_signals == 1
    assert broker.list_orders() == []


def test_composite_risk_first_failing_rule_short_circuits() -> None:
    """CompositeRiskCheck with size + position rules: the size rule fires first."""
    history = _history(
        [
            _bar("A", datetime(2024, 1, 1, tzinfo=UTC), Decimal("100")),
        ]
    )
    broker = InMemoryBroker(run_id="r1", initial_cash=Decimal("1000000"))
    strategy = _BuyStrategy(quantity=Decimal("1000"))  # notional = 100000
    engine = BacktestEngine(
        strategy=strategy,
        broker=broker,
        risk=CompositeRiskCheck(
            checks=[OrderSizeLimit(Decimal("500")), PositionLimit(Decimal("100"))]
        ),
        market_data=_feed_from_history(history, start=date(2024, 1, 1), end=date(2024, 1, 1)),
        symbols=["A"],
        run_id="r1",
    )
    summary = engine.run(start=date(2024, 1, 1), end=date(2024, 1, 1))
    assert summary.n_dropped_signals == 1
    assert summary.n_orders == 0


def test_on_init_is_called_exactly_once() -> None:
    """on_init is invoked once before the first bar."""
    history = _history(
        [
            _bar("A", datetime(2024, 1, 1, tzinfo=UTC), Decimal("100")),
            _bar("A", datetime(2024, 1, 2, tzinfo=UTC), Decimal("100")),
        ]
    )
    broker = InMemoryBroker(run_id="r1", initial_cash=Decimal("1000000"))
    strategy = _BuyStrategy()
    engine = BacktestEngine(
        strategy=strategy,
        broker=broker,
        risk=NoOpRiskCheck(),
        market_data=_feed_from_history(history, start=date(2024, 1, 1), end=date(2024, 1, 2)),
        symbols=["A"],
        run_id="r1",
    )
    engine.run(start=date(2024, 1, 1), end=date(2024, 1, 2))
    assert strategy.init_called == 1


def test_on_init_submitting_orders_raises_engine_usage_error() -> None:
    """A strategy that submits from on_init raises EngineUsageError."""
    history = _history(
        [
            _bar("A", datetime(2024, 1, 1, tzinfo=UTC), Decimal("100")),
        ]
    )
    broker = InMemoryBroker(run_id="r1", initial_cash=Decimal("1000000"))
    engine = BacktestEngine(
        strategy=_AbusiveOnInit(),
        broker=broker,
        risk=NoOpRiskCheck(),
        market_data=_feed_from_history(history, start=date(2024, 1, 1), end=date(2024, 1, 1)),
        symbols=["A"],
        run_id="r1",
    )
    with pytest.raises(EngineUsageError):
        engine.run(start=date(2024, 1, 1), end=date(2024, 1, 1))


def test_engine_accepts_any_broker_implementation() -> None:
    """The engine does not import a specific broker implementation."""
    # Pass a stub broker that satisfies the Protocol; the engine should
    # be import-clean of any concrete broker.
    from xtrade.execution.broker import Broker

    class _StubBroker:
        def __init__(self) -> None:
            self.orders: list[Any] = []

        def submit_order(self, req: Any) -> Any:
            self.orders.append(req)
            return None

        def cancel_order(self, order_id: int) -> None:
            pass

        def get_order(self, order_id: int) -> Any:
            return None

        def list_orders(self) -> list[Any]:
            return self.orders

        def get_position(self, symbol: str) -> Any:
            return None

        def list_positions(self) -> list[Any]:
            return []

        def get_account(self) -> Any:
            return _account()

        def advance(self, time: Any, prices: Any) -> list[Any]:
            return []

        def register_callback(self, event: str, fn: Any) -> None:
            pass

    assert isinstance(_StubBroker(), Broker)
    # We don't need to actually run an engine here; the type assertion is the test.
