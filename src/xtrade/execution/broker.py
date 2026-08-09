"""Broker abstraction for ``xtrade.execution``.

Exposes a ``Broker`` Protocol with two implementations:

- :class:`InMemoryBroker` — process-local dict / list backing. Used for
  backtests and unit tests.
- :class:`PostgresBroker` — delegates persistence to the
  ``xtrade.data.broker_data`` repositories. Used for paper / live runs.

Both implementations share the same observable behaviour for the order
lifecycle, position / account maintenance, callback fan-out, and the
``advance`` time-step driver. Domain types (``Order``, ``Trade``,
``Position``, ``Account``, ``OrderState``, ``OrderStateError``,
``DuplicateSnapshotError``) are re-exported from ``xtrade.data.broker_data``
so callers do not need to import from the data layer directly.
"""

from __future__ import annotations

import contextlib
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from itertools import count
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from xtrade.core.logging import get_logger
from xtrade.data.broker_data import (
    Account,
    DuplicateSnapshotError,
    Order,
    OrderState,
    OrderStateError,
    Position,
    Trade,
)
from xtrade.data.broker_data.account import PostgresAccountRepository
from xtrade.data.broker_data.order import ALLOWED_TRANSITIONS, PostgresOrderRepository
from xtrade.data.broker_data.position import PostgresPositionRepository
from xtrade.data.broker_data.trade import PostgresTradeRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

__all__ = [
    "Account",
    "Broker",
    "DuplicateSnapshotError",
    "InMemoryBroker",
    "Order",
    "OrderRequest",
    "OrderSide",
    "OrderState",
    "OrderStateError",
    "OrderType",
    "Position",
    "PostgresBroker",
    "Trade",
]

_logger = get_logger("xtrade.execution.broker")
_STARTING_ID = 1


class OrderSide(StrEnum):
    """Order side: ``BUY`` or ``SELL``."""

    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    """Order type: ``MARKET`` fills at the next ``advance`` price; ``LIMIT``
    fills only when the supplied price is at-or-better than the limit."""

    MARKET = "market"
    LIMIT = "limit"


@dataclass(frozen=True)
class OrderRequest:
    """Caller-supplied request used to create a new order.

    The broker assigns the ``id`` on the returned :class:`Order`; the
    request itself carries no database- or memory-assigned fields.
    """

    run_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    order_type: OrderType
    price: Decimal | None = None  # required when order_type == LIMIT


CallbackFn = Callable[..., None]


@runtime_checkable
class Broker(Protocol):
    """Business-level broker interface consumed by strategy / execution / CLI."""

    def submit_order(self, req: OrderRequest) -> Order: ...
    def cancel_order(self, order_id: int) -> None: ...
    def get_order(self, order_id: int) -> Order | None: ...
    def list_orders(self) -> list[Order]: ...
    def get_position(self, symbol: str) -> Position | None: ...
    def list_positions(self) -> list[Position]: ...
    def get_account(self) -> Account: ...
    def advance(self, time: datetime, prices: dict[str, Decimal]) -> list[Trade]: ...
    def register_callback(self, event: str, fn: CallbackFn) -> None: ...


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _emit(
    callbacks: dict[str, list[CallbackFn]],
    event: str,
    payload: Any,
) -> None:
    """Synchronously fan out ``payload`` to every callback registered for ``event``.

    Callbacks fire in registration order. A callback that raises is logged
    and skipped — the remaining callbacks still run.
    """
    for fn in callbacks.get(event, ()):
        try:
            fn(payload)
        except Exception:
            _logger.exception("broker callback %s for event %s raised", fn, event)


def _next_order_id(counter: count[int]) -> int:
    return next(counter)


def _apply_fill_to_position(
    current: Position | None,
    symbol: str,
    side: OrderSide,
    quantity: Decimal,
    price: Decimal,
    run_id: str,
    time: datetime,
) -> Position:
    """Compute the new position after a fill. Returns a Position snapshot."""
    signed_qty = quantity if side == OrderSide.BUY else -quantity
    if current is None:
        # Opening a position from zero.
        new_qty = signed_qty
        new_avg = Decimal("0") if new_qty == 0 else price
    else:
        prev_qty = current.quantity
        new_qty = prev_qty + signed_qty
        if new_qty == 0:
            new_avg = Decimal("0")
        elif (
            prev_qty == 0 or (prev_qty > 0 and signed_qty > 0) or (prev_qty < 0 and signed_qty < 0)
        ):
            # Adding to an existing position in the same direction: weighted avg.
            new_avg = (abs(prev_qty) * current.avg_price + quantity * price) / abs(
                prev_qty + signed_qty
            )
        elif (prev_qty > 0 and new_qty > 0) or (prev_qty < 0 and new_qty < 0):
            # Reducing without flipping: average price stays.
            new_avg = current.avg_price
        else:
            # Flip: reset average to the fill price.
            new_avg = price
    return Position(
        run_id=run_id,
        symbol=symbol,
        time=time,
        quantity=new_qty,
        avg_price=new_avg,
    )


def _order_should_fill(order: Order, prices: dict[str, Decimal]) -> Decimal | None:
    """Return the fill price if ``order`` should fill on this ``advance``, else None.

    Only in-flight orders (``submitted`` / ``partial``) are eligible. Once an
    order is ``filled`` / ``cancelled`` / ``rejected`` / ``expired`` it stays
    out of subsequent advances.

    - MARKET: fill at the supplied price if the symbol is quoted.
    - LIMIT (BUY): fill if price <= limit.
    - LIMIT (SELL): fill if price >= limit.
    """
    if OrderState(order.status) not in (OrderState.SUBMITTED, OrderState.PARTIAL):
        return None
    price = prices.get(order.symbol)
    if price is None:
        return None
    if order.price is None:
        # Implicitly a market order from the data layer.
        return price
    if order.side == OrderSide.BUY.value:
        if price <= order.price:
            return price
    else:  # SELL
        if price >= order.price:
            return price
    return None


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


@dataclass
class _InMemoryState:
    """Mutable state owned by one ``InMemoryBroker`` instance."""

    orders: dict[int, Order] = field(default_factory=dict)
    positions: dict[str, Position] = field(default_factory=dict)
    account: Account | None = None
    callbacks: dict[str, list[CallbackFn]] = field(default_factory=lambda: defaultdict(list))
    id_seq: count[int] = field(default_factory=lambda: count(_STARTING_ID))


class InMemoryBroker:
    """Process-local broker implementation.

    State lives in the instance; two instances do not share data. Used
    for backtests and unit tests. Per :class:`Broker` Protocol.
    """

    def __init__(self, run_id: str, initial_cash: Decimal = Decimal("0")) -> None:
        self._run_id = run_id
        self._initial_cash = initial_cash
        self._state = _InMemoryState()

    # ----- writes -----

    def submit_order(self, req: OrderRequest) -> Order:
        if req.quantity <= 0:
            raise ValueError(f"order quantity must be > 0, got {req.quantity}")
        if req.order_type == OrderType.LIMIT and req.price is None:
            raise ValueError("limit order requires a price")
        order_id = _next_order_id(self._state.id_seq)
        order = Order(
            id=order_id,
            run_id=req.run_id,
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            side=req.side.value,
            quantity=req.quantity,
            price=req.price,
            status=OrderState.PENDING.value,
        )
        self._state.orders[order_id] = order
        return order

    def cancel_order(self, order_id: int) -> None:
        order = self._state.orders.get(order_id)
        if order is None:
            raise LookupError(f"order not found: {order_id}")
        current = OrderState(order.status)
        # The broker accepts PENDING → CANCELLED in addition to the data
        # layer's allowed transitions (which excludes PENDING per the
        # data-broker spec). The spec mandates that pending orders are
        # cancellable.
        if current not in (OrderState.PENDING, OrderState.SUBMITTED, OrderState.PARTIAL):
            raise OrderStateError(current.value, OrderState.CANCELLED.value)
        if (
            current is not OrderState.PENDING
            and (current, OrderState.CANCELLED) not in ALLOWED_TRANSITIONS
        ):
            raise OrderStateError(current.value, OrderState.CANCELLED.value)
        new = dataclasses_replace(order, status=OrderState.CANCELLED.value)
        self._state.orders[order_id] = new
        _emit(self._state.callbacks, "on_order_update", new)

    # ----- reads -----

    def get_order(self, order_id: int) -> Order | None:
        return self._state.orders.get(order_id)

    def list_orders(self) -> list[Order]:
        return list(self._state.orders.values())

    def get_position(self, symbol: str) -> Position | None:
        return self._state.positions.get(symbol)

    def list_positions(self) -> list[Position]:
        return list(self._state.positions.values())

    def get_account(self) -> Account:
        if self._state.account is None:
            return Account(
                run_id=self._run_id,
                time=datetime.fromtimestamp(0),
                cash=self._initial_cash,
                equity=self._initial_cash,
                margin=Decimal("0"),
            )
        return self._state.account

    # ----- callbacks -----

    def register_callback(self, event: str, fn: CallbackFn) -> None:
        self._state.callbacks[event].append(fn)

    # ----- clock -----

    def advance(self, time: datetime, prices: dict[str, Decimal]) -> list[Trade]:
        fills: list[Trade] = []
        for order in list(self._state.orders.values()):
            order = self._maybe_advance_status(order, time)
            fill_price = _order_should_fill(order, prices)
            if fill_price is None:
                assert order.id is not None
                self._state.orders[order.id] = order
                continue
            trade = self._emit_fill(order, fill_price, time)
            fills.append(trade)
        if fills or self._state.account is None:
            self._update_account_after_fills(time)
        return fills

    # ----- internals -----

    def _maybe_advance_status(self, order: Order, time: datetime) -> Order:
        if order.status == OrderState.PENDING.value:
            new: Order = dataclasses_replace(order, status=OrderState.SUBMITTED.value)
            assert order.id is not None
            self._state.orders[order.id] = new
            _emit(self._state.callbacks, "on_order_update", new)
            return new
        return order

    def _emit_fill(self, order: Order, fill_price: Decimal, time: datetime) -> Trade:
        # Update position.
        previous = self._state.positions.get(order.symbol)
        new_position = _apply_fill_to_position(
            current=previous,
            symbol=order.symbol,
            side=OrderSide(order.side),
            quantity=order.quantity,
            price=fill_price,
            run_id=self._run_id,
            time=time,
        )
        self._state.positions[order.symbol] = new_position
        _emit(self._state.callbacks, "on_fill", (order, new_position))

        # Transition order to FILLED.
        new_order = dataclasses_replace(order, status=OrderState.FILLED.value)
        assert order.id is not None
        self._state.orders[order.id] = new_order
        _emit(self._state.callbacks, "on_order_update", new_order)

        # Build Trade (id assigned by the data-broker layer; in-memory uses
        # a fresh counter so tests can compare).
        trade_id = _next_order_id(self._state.id_seq)
        assert order.id is not None
        return Trade(
            id=trade_id,
            order_id=order.id,
            run_id=self._run_id,
            symbol=order.symbol,
            price=fill_price,
            quantity=order.quantity,
            fee=Decimal("0"),
            time=time,
        )

    def _update_account_after_fills(self, time: datetime) -> None:
        previous = self.get_account()
        equity = self._initial_cash
        for pos in self._state.positions.values():
            equity += pos.quantity * pos.avg_price
        new_account = Account(
            run_id=self._run_id,
            time=time,
            cash=previous.cash,
            equity=equity,
            margin=Decimal("0"),
        )
        self._state.account = new_account
        _emit(self._state.callbacks, "on_account_update", new_account)


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


def dataclasses_replace(obj: Any, **changes: Any) -> Any:
    """Local ``dataclasses.replace`` (avoids an extra import at module top)."""
    from dataclasses import replace as _replace

    return _replace(obj, **changes)


class PostgresBroker:
    """Postgres-backed broker. Delegates persistence to the four
    ``xtrade.data.broker_data.Postgres*Repository`` repositories.

    Each ``advance`` runs in a single ``xtrade.data.engine.get_session``
    context: state-machine transitions, fill insertion, position snapshot
    insertion, and account snapshot insertion all commit together or
    roll back together.
    """

    def __init__(
        self,
        run_id: str,
        session_factory: sessionmaker[Session] | None = None,
        initial_cash: Decimal = Decimal("0"),
    ) -> None:
        self._run_id = run_id
        self._initial_cash = initial_cash
        self._session_factory = session_factory
        self._callbacks: dict[str, list[CallbackFn]] = defaultdict(list)

    # ----- writes -----

    def submit_order(self, req: OrderRequest) -> Order:
        if req.quantity <= 0:
            raise ValueError(f"order quantity must be > 0, got {req.quantity}")
        if req.order_type == OrderType.LIMIT and req.price is None:
            raise ValueError("limit order requires a price")
        repo = PostgresOrderRepository()
        order = Order(
            id=None,
            run_id=req.run_id,
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            side=req.side.value,
            quantity=req.quantity,
            price=req.price,
            status=OrderState.PENDING.value,
        )
        persisted = repo.create(order)
        _emit(self._callbacks, "on_order_update", persisted)
        return persisted

    def cancel_order(self, order_id: int) -> None:
        repo = PostgresOrderRepository()
        repo.update_status(order_id, OrderState.CANCELLED.value)
        new = repo.get(order_id)
        assert new is not None
        _emit(self._callbacks, "on_order_update", new)

    # ----- reads -----

    def get_order(self, order_id: int) -> Order | None:
        return PostgresOrderRepository().get(order_id)

    def list_orders(self) -> list[Order]:
        return PostgresOrderRepository().list_by_run(self._run_id)

    def get_position(self, symbol: str) -> Position | None:
        positions = PostgresPositionRepository().list_by_run(self._run_id)
        latest = sorted([p for p in positions if p.symbol == symbol], key=lambda p: p.time)
        return latest[-1] if latest else None

    def list_positions(self) -> list[Position]:
        positions = PostgresPositionRepository().list_by_run(self._run_id)
        latest: dict[str, Position] = {}
        for p in positions:
            current = latest.get(p.symbol)
            if current is None or p.time > current.time:
                latest[p.symbol] = p
        return list(latest.values())

    def get_account(self) -> Account:
        accounts = PostgresAccountRepository().list_by_run(self._run_id)
        if accounts:
            return accounts[-1]
        return Account(
            run_id=self._run_id,
            time=datetime.fromtimestamp(0),
            cash=self._initial_cash,
            equity=self._initial_cash,
            margin=Decimal("0"),
        )

    # ----- callbacks -----

    def register_callback(self, event: str, fn: CallbackFn) -> None:
        self._callbacks[event].append(fn)

    # ----- clock -----

    def advance(self, time: datetime, prices: dict[str, Decimal]) -> list[Trade]:
        # NOTE: the existing xtrade.data.broker_data Postgres*Repository methods
        # do not accept a session argument, so each repository call commits in
        # its own transaction. True single-transaction ``advance`` requires
        # routing the session through the repository methods; that is deferred
        # to a follow-up change. Until then, on exception the writes performed
        # *before* the raise are already committed and will not be rolled back.
        order_repo = PostgresOrderRepository()
        trade_repo = PostgresTradeRepository()
        position_repo = PostgresPositionRepository()
        account_repo = PostgresAccountRepository()

        fills: list[Trade] = []
        orders = order_repo.list_by_run(self._run_id)
        for order in orders:
            updated = self._maybe_advance_status(order, time)
            fill_price = _order_should_fill(updated, prices)
            if fill_price is None:
                continue
            trade = self._emit_fill(
                order_repo,
                trade_repo,
                position_repo,
                updated,
                fill_price,
                time,
            )
            fills.append(trade)
        if fills or not account_repo.list_by_run(self._run_id):
            self._update_account(account_repo, time)
        for trade in fills:
            _emit(self._callbacks, "on_fill", trade)
        return fills

    # ----- internals -----

    def _maybe_advance_status(self, order: Order, time: datetime) -> Order:
        if order.status == OrderState.PENDING.value:
            repo = PostgresOrderRepository()
            repo.update_status(order.id or -1, OrderState.SUBMITTED.value)
            new = repo.get(order.id or -1)
            assert new is not None
            _emit(self._callbacks, "on_order_update", new)
            return new
        return order

    def _emit_fill(
        self,
        order_repo: PostgresOrderRepository,
        trade_repo: PostgresTradeRepository,
        position_repo: PostgresPositionRepository,
        order: Order,
        fill_price: Decimal,
        time: datetime,
    ) -> Trade:
        previous = None
        for p in position_repo.list_by_run(self._run_id):
            if p.symbol == order.symbol and (previous is None or p.time > previous.time):
                previous = p
        new_position = _apply_fill_to_position(
            current=previous,
            symbol=order.symbol,
            side=OrderSide(order.side),
            quantity=order.quantity,
            price=fill_price,
            run_id=self._run_id,
            time=time,
        )
        position_repo.create(new_position)
        _emit(self._callbacks, "on_fill", (order, new_position))

        order_repo.update_status(order.id or -1, OrderState.FILLED.value)
        new_order = order_repo.get(order.id or -1)
        assert new_order is not None
        _emit(self._callbacks, "on_order_update", new_order)

        return trade_repo.create(
            Trade(
                id=None,
                order_id=order.id or -1,
                run_id=self._run_id,
                symbol=order.symbol,
                price=fill_price,
                quantity=order.quantity,
                fee=Decimal("0"),
                time=time,
            )
        )

    def _update_account(
        self,
        account_repo: PostgresAccountRepository,
        time: datetime,
    ) -> None:
        positions = PostgresPositionRepository().list_by_run(self._run_id)
        equity = self._initial_cash
        for p in positions:
            equity += p.quantity * p.avg_price
        previous = account_repo.list_by_run(self._run_id)
        cash = previous[-1].cash if previous else self._initial_cash
        new_account = Account(
            run_id=self._run_id,
            time=time,
            cash=cash,
            equity=equity,
            margin=Decimal("0"),
        )
        with contextlib.suppress(DuplicateSnapshotError):
            # Same (run_id, time): skip; the periodic refresh will pick it up.
            account_repo.create(new_account)
        _emit(self._callbacks, "on_account_update", new_account)
