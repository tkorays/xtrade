"""Backtest engine.

The deterministic, explicit-step driver. Iterates every bar in
``[start, end]`` in chronological order (and lexicographic symbol
order when timestamps collide), calling the per-step pipeline:

    bar -> strategy.on_bar -> [risk -> broker.submit_order] -> broker.advance

Two runs with the same inputs are guaranteed to produce identical
final account / positions / orders / trades. No network, no IO, no
implicit sleep.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from xtrade.core.logging import get_logger
from xtrade.engine.clock import EngineUsageError, RunSummary
from xtrade.execution.broker import Broker
from xtrade.risk import OrderIntent, RiskCheck, RiskContext, RiskViolationError
from xtrade.strategy.base import (
    Bar,
    Context,
    Signal,
    Strategy,
    signal_to_order_request,
)

if TYPE_CHECKING:
    from xtrade.data.broker_data import Account, Position

__all__ = ["BacktestEngine", "MarketDataFeed"]

_logger = get_logger("xtrade.engine.backtest")


# A `MarketDataFeed` is a callable that returns the bars for a given
# (symbol, start, end) window. The engine iterates each symbol and calls
# this for the window. Implementations may back this with a CSV, a
# dataframe, or a network source.
MarketDataFeed = Callable[[str, date, date], list[Bar]]


class BacktestEngine:
    """A deterministic backtest driver.

    The engine does NOT make any network call, file IO, or implicit
    sleep. The caller chooses the broker implementation (in-memory or
    Postgres) and the market-data source.
    """

    def __init__(
        self,
        strategy: Strategy,
        broker: Broker,
        risk: RiskCheck,
        market_data: MarketDataFeed,
        *,
        symbols: list[str],
        run_id: str,
        interval: str = "1d",
    ) -> None:
        self._strategy = strategy
        self._broker = broker
        self._risk = risk
        self._market_data = market_data
        self._symbols = sorted(symbols)
        self._run_id = run_id
        self._interval = interval

    def run(self, start: date, end: date) -> RunSummary:
        """Run the backtest over ``[start, end]`` and return a summary."""
        # Capture initial account snapshot (used by the summary).
        initial_account = self._broker.get_account()

        # Per-step counts.
        n_orders = 0
        n_fills = 0
        n_dropped = 0

        # Build the contextual state. `on_init` runs once.
        first_bar_time = self._first_bar_time(start, end)
        engine_ctx = self._build_context(now=first_bar_time, bar=None)
        self._guard_on_init_abuse(engine_ctx)

        _logger.info(
            "backtest start: run_id=%s window=%s..%s symbols=%s",
            self._run_id,
            start.isoformat(),
            end.isoformat(),
            self._symbols,
        )

        # Collect every (time, symbol) in the window, then sort.
        timeline: list[tuple[datetime, str, Bar]] = []
        per_symbol_counts: dict[str, int] = {}
        for sym in self._symbols:
            n = 0
            for bar in self._market_data(sym, start, end):
                timeline.append((bar.time, sym, bar))
                n += 1
            per_symbol_counts[sym] = n
            _logger.debug("backtest feed: symbol=%s bars=%d", sym, n)
        timeline.sort(key=lambda t: (t[0], t[1]))

        _logger.info(
            "backtest timeline: run_id=%s total_bars=%d per_symbol=%s",
            self._run_id,
            len(timeline),
            per_symbol_counts,
        )

        # Iterate.
        for step_idx, (time, _sym, bar) in enumerate(timeline):
            _logger.debug(
                "step start: idx=%d time=%s symbol=%s close=%s",
                step_idx,
                time.isoformat(),
                bar.symbol,
                bar.close,
            )

            # Build per-step context (engine-side) and project to strategy Context.
            engine_ctx = self._build_context(now=time, bar=bar)
            ctx = engine_ctx.to_strategy_context()

            account_equity_before = engine_ctx.account.equity
            positions_before = {k: v.quantity for k, v in engine_ctx.positions.items()}
            inflight_before = [
                o.id for o in self._broker.list_orders() if o.status in {"pending", "submitted"}
            ]

            signals = self._strategy.on_bar(bar, ctx)
            _logger.debug(
                "strategy.on_bar: idx=%d time=%s symbol=%s returned=%d signal(s)",
                step_idx,
                time.isoformat(),
                bar.symbol,
                len(signals),
            )

            for sig_idx, sig in enumerate(signals):
                _logger.debug(
                    "signal received: idx=%d sig_idx=%d symbol=%s side=%s qty=%s type=%s price=%s",
                    step_idx,
                    sig_idx,
                    sig.symbol,
                    sig.side.value,
                    sig.quantity,
                    sig.order_type.value,
                    sig.price,
                )
                intent = self._build_intent(sig, engine_ctx)
                try:
                    self._risk.check(intent, engine_ctx.risk_ctx())
                except RiskViolationError as exc:
                    n_dropped += 1
                    _logger.warning(
                        "risk rejected signal: idx=%d sig_idx=%d rule=%s symbol=%s msg=%s",
                        step_idx,
                        sig_idx,
                        exc.rule_name,
                        sig.symbol,
                        exc.message,
                    )
                    continue
                req = signal_to_order_request(sig, run_id=self._run_id)
                order = self._broker.submit_order(req)
                n_orders += 1
                _logger.info(
                    "order submitted: idx=%d sig_idx=%d order_id=%s symbol=%s side=%s qty=%s type=%s status=%s",
                    step_idx,
                    sig_idx,
                    order.id,
                    order.symbol,
                    order.side,
                    order.quantity,
                    sig.order_type.value,
                    order.status,
                )

            # Advance the broker. The broker decides which in-flight
            # orders fill given the bar's close price.
            prices = {bar.symbol: bar.close}
            fills = self._broker.advance(time, prices)
            n_fills += len(fills)

            for trade in fills:
                _logger.info(
                    "fill: idx=%d trade_id=%s order_id=%s symbol=%s qty=%s price=%s",
                    step_idx,
                    trade.id,
                    trade.order_id,
                    trade.symbol,
                    trade.quantity,
                    trade.price,
                )

            account_equity_after = self._broker.get_account().equity
            positions_after = {p.symbol: p.quantity for p in self._broker.list_positions()}
            if account_equity_after != account_equity_before or positions_before != positions_after:
                _logger.info(
                    "step state changed: idx=%d time=%s equity_before=%s equity_after=%s positions_before=%s positions_after=%s",
                    step_idx,
                    time.isoformat(),
                    account_equity_before,
                    account_equity_after,
                    positions_before,
                    positions_after,
                )
            if inflight_before:
                _logger.debug(
                    "step in-flight: idx=%d before=%s remaining=%d",
                    step_idx,
                    inflight_before,
                    len(
                        [
                            o
                            for o in self._broker.list_orders()
                            if o.status in {"pending", "submitted"}
                        ]
                    ),
                )

            _logger.debug(
                "step end: idx=%d fills=%d n_orders=%d n_fills=%d n_dropped=%d",
                step_idx,
                len(fills),
                n_orders,
                n_fills,
                n_dropped,
            )

        _logger.info(
            "backtest end: run_id=%s n_orders=%d n_fills=%d n_dropped=%d final_equity=%s",
            self._run_id,
            n_orders,
            n_fills,
            n_dropped,
            self._broker.get_account().equity,
        )

        final_account = self._broker.get_account()
        return RunSummary(
            initial_account=initial_account,
            final_account=final_account,
            n_orders=n_orders,
            n_fills=n_fills,
            n_dropped_signals=n_dropped,
            start=datetime.combine(start, datetime.min.time()),
            end=datetime.combine(end, datetime.max.time()),
        )

    # ---------------------------------------------------------------------------
    # Context / helpers
    # ---------------------------------------------------------------------------

    def _build_context(self, *, now: datetime, bar: Bar | None) -> _EngineContext:
        return _EngineContext(
            now=now,
            bar=bar,
            broker=self._broker,
            risk=self._risk,
            account=self._broker.get_account(),
            positions={p.symbol: p for p in self._broker.list_positions()},
            run_id=self._run_id,
        )

    def _first_bar_time(self, start: date, end: date) -> datetime:
        for sym in self._symbols:
            bars = self._market_data(sym, start, end)
            if bars:
                return bars[0].time
        return datetime.combine(start, datetime.min.time())

    def _build_intent(self, sig: Signal, ctx: _EngineContext) -> OrderIntent:
        current = ctx.positions.get(sig.symbol)
        signed_qty = sig.quantity if sig.side.value == "buy" else -sig.quantity
        prev_qty = current.quantity if current is not None else Decimal("0")
        expected_after = prev_qty + signed_qty
        return OrderIntent(
            symbol=sig.symbol,
            side=sig.side,
            quantity=sig.quantity,
            price=sig.price,
            expected_qty_after=expected_after,
        )

    def _guard_on_init_abuse(self, ctx: _EngineContext) -> None:
        """`on_init` is supposed to be read-only. Detect writes by wrapping
        ``broker.submit_order`` with a guard that re-raises immediately."""

        original = ctx.broker.submit_order

        def _guarded(req: object) -> None:
            raise EngineUsageError(
                "Strategy.on_init must not submit orders; submit orders only in Strategy.on_bar"
            )

        # mypy can't follow the runtime swap; both functions are valid
        # `submit_order` callable shapes for the duration of `on_init`.
        ctx.broker.submit_order = _guarded  # type: ignore[method-assign, assignment]
        try:
            self._strategy.on_init(ctx.to_strategy_context())
        finally:
            ctx.broker.submit_order = original  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Internal context bridge
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _EngineContext:
    """Internal carrier: bridges an engine step to a Strategy :class:`Context`.

    Engine-side extensions (``run_id``, ``risk_ctx``) are stored here and
    projected into the user-facing :class:`Context` + :class:`RiskContext`
    on demand.
    """

    now: datetime
    bar: Bar | None
    broker: Broker
    risk: RiskCheck
    account: Account
    positions: dict[str, Position]
    run_id: str

    def to_strategy_context(self) -> Context:
        return Context(
            now=self.now,
            bar=self.bar,
            broker=self.broker,
            risk=self.risk,
            account=self.account,
            positions=self.positions,
        )

    def risk_ctx(self) -> RiskContext:
        return RiskContext(account=self.account, positions=self.positions, now=self.now)
