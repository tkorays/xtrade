"""Live engine.

Event-driven counterpart to :class:`BacktestEngine`. Internally runs an
``asyncio`` event loop that polls a :class:`LiveMarketSource` and, for
each next price, runs the per-step pipeline. The engine is the sole
owner of the system clock during a live run; each ``advance`` is driven
by the source's ``next_price`` event.

Exceptions from the strategy or the broker propagate out of the loop and
stop the engine.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from xtrade.core.logging import get_logger
from xtrade.engine.backtest import _EngineContext
from xtrade.execution.broker import Broker
from xtrade.risk import OrderIntent, RiskCheck, RiskViolationError
from xtrade.strategy.base import (
    Bar,
    Signal,
    Strategy,
    signal_to_order_request,
)

__all__ = ["LiveEngine", "LiveMarketSource"]

_logger = get_logger("xtrade.engine.live")


@runtime_checkable
class LiveMarketSource(Protocol):
    """A pushing source for the live engine.

    ``next_price`` returns the next ``(time, {symbol: price})`` tuple,
    or ``None`` to indicate the source is exhausted.
    """

    def next_price(self) -> tuple[datetime, dict[str, Decimal]] | None: ...


class LiveEngine:
    """An event-driven driver backed by a :class:`LiveMarketSource`.

    The engine starts an asyncio task that polls ``source.next_price()``
    and runs the per-step pipeline for each tick. ``stop()`` cancels the
    task and awaits it.
    """

    def __init__(
        self,
        strategy: Strategy,
        broker: Broker,
        risk: RiskCheck,
        source: LiveMarketSource,
        *,
        run_id: str,
        interval: str = "1d",
    ) -> None:
        self._strategy = strategy
        self._broker = broker
        self._risk = risk
        self._source = source
        self._run_id = run_id
        self._interval = interval
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    def start(self) -> None:
        """Start the engine. Returns immediately; the loop runs in a task."""
        if self._task is not None:
            raise RuntimeError("LiveEngine is already running")
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="LiveEngine")

    async def stop(self) -> None:
        """Stop the engine and join the task."""
        self._stopping = True
        task = self._task
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        self._task = None

    async def _run(self) -> None:
        try:
            engine_ctx = self._build_context(now=datetime.now(), bar=None)
            self._strategy.on_init(engine_ctx.to_strategy_context())
        except Exception:
            _logger.exception("LiveEngine.on_init failed")
            raise

        while not self._stopping:
            tick = self._source.next_price()
            if tick is None:
                break
            time, prices = tick
            # For live mode, OHLCV collapses to the price (the live
            # strategy consumes prices, not bar shape).
            first_sym = next(iter(prices.keys()))
            first_price = next(iter(prices.values()))
            bar = Bar(
                symbol=first_sym,
                time=time,
                open=first_price,
                high=first_price,
                low=first_price,
                close=first_price,
                volume=Decimal("0"),
                interval=self._interval,
            )
            engine_ctx = self._build_context(now=time, bar=bar)
            ctx = engine_ctx.to_strategy_context()
            signals = self._strategy.on_bar(bar, ctx)
            for sig in signals:
                intent = self._build_intent(sig, engine_ctx)
                try:
                    self._risk.check(intent, engine_ctx.risk_ctx())
                except RiskViolationError as exc:
                    _logger.warning(
                        "live risk rejected signal: rule=%s symbol=%s msg=%s",
                        exc.rule_name,
                        sig.symbol,
                        exc.message,
                    )
                    continue
                req = signal_to_order_request(sig, run_id=self._run_id)
                self._broker.submit_order(req)
            self._broker.advance(time, prices)

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
