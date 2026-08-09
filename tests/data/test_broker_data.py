"""Tests for broker-data repositories (ORM path)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from xtrade.data.broker_data import (
    Account,
    AccountRepository,
    DuplicateSnapshotError,
    Order,
    OrderRepository,
    OrderState,
    OrderStateError,
    Position,
    PositionRepository,
    PostgresAccountRepository,
    PostgresOrderRepository,
    PostgresPositionRepository,
    PostgresTradeRepository,
    Trade,
    TradeRepository,
)

from .conftest import skip_without_db

# ---------------------------------------------------------------------------
# Pure-unit tests
# ---------------------------------------------------------------------------


def test_order_state_is_str_enum() -> None:
    assert OrderState.PENDING.value == "pending"
    assert isinstance(OrderState.PENDING, str)


def test_order_state_error_carries_current_and_target() -> None:
    err = OrderStateError("filled", "pending")
    assert err.current == "filled"
    assert err.target == "pending"
    assert "filled" in str(err) and "pending" in str(err)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


def _new_order(**overrides) -> Order:
    defaults = dict(
        run_id="r1",
        client_order_id="coid-1",
        symbol="AAA",
        side="buy",
        quantity=Decimal("100"),
        price=Decimal("10.5"),
        status=OrderState.PENDING.value,
    )
    defaults.update(overrides)
    return Order(**defaults)


@skip_without_db
def test_order_create_and_get(engine, schema) -> None:
    repo: OrderRepository = PostgresOrderRepository()
    record = _new_order()
    persisted = repo.create(record)
    assert persisted.id is not None

    fetched = repo.get(persisted.id)
    assert fetched is not None
    assert fetched.symbol == "AAA"
    assert fetched.status == "pending"


@skip_without_db
def test_order_duplicate_client_order_id_rejected(engine, schema) -> None:
    from sqlalchemy.exc import IntegrityError

    repo: OrderRepository = PostgresOrderRepository()
    repo.create(_new_order(client_order_id="coid-1"))

    with pytest.raises(IntegrityError):
        repo.create(_new_order(client_order_id="coid-1"))


@skip_without_db
def test_order_list_by_run_filters_correctly(engine, schema) -> None:
    repo: OrderRepository = PostgresOrderRepository()
    repo.create(_new_order(run_id="run-A", client_order_id="a-1"))
    repo.create(_new_order(run_id="run-A", client_order_id="a-2"))
    repo.create(_new_order(run_id="run-B", client_order_id="b-1"))

    a_orders = repo.list_by_run("run-A")
    assert len(a_orders) == 2
    assert all(o.run_id == "run-A" for o in a_orders)
    assert len(repo.list_by_run("run-B")) == 1


@skip_without_db
def test_order_allowed_status_transition(engine, schema) -> None:
    repo: OrderRepository = PostgresOrderRepository()
    persisted = repo.create(_new_order())

    repo.update_status(persisted.id, OrderState.SUBMITTED.value)
    fetched = repo.get(persisted.id)
    assert fetched.status == "submitted"


@skip_without_db
def test_order_disallowed_status_transition_raises(engine, schema) -> None:
    repo: OrderRepository = PostgresOrderRepository()
    persisted = repo.create(_new_order())
    repo.update_status(persisted.id, OrderState.SUBMITTED.value)
    repo.update_status(persisted.id, OrderState.FILLED.value)

    with pytest.raises(OrderStateError):
        repo.update_status(persisted.id, OrderState.PENDING.value)


@skip_without_db
def test_trade_create_and_list_by_order(engine, schema) -> None:
    order_repo: OrderRepository = PostgresOrderRepository()
    trade_repo: TradeRepository = PostgresTradeRepository()
    order = order_repo.create(_new_order())

    trade_repo.create(
        Trade(
            order_id=order.id,
            run_id="r1",
            symbol="AAA",
            price=Decimal("10.5"),
            quantity=Decimal("100"),
            fee=Decimal("1.0"),
            time=datetime(2025, 1, 2, tzinfo=UTC),
        )
    )
    trades = trade_repo.list_by_order(order.id)
    assert len(trades) == 1
    assert trades[0].symbol == "AAA"

    run_trades = trade_repo.list_by_run("r1")
    assert len(run_trades) == 1


@skip_without_db
def test_position_create_and_duplicate(engine, schema) -> None:
    repo: PositionRepository = PostgresPositionRepository()
    record = Position(
        run_id="r1",
        symbol="AAA",
        time=datetime(2025, 1, 1, tzinfo=UTC),
        quantity=Decimal("100"),
        avg_price=Decimal("10"),
    )
    repo.create(record)

    with pytest.raises(DuplicateSnapshotError):
        repo.create(record)


@skip_without_db
def test_position_list_by_run_orders_by_time(engine, schema) -> None:
    repo: PositionRepository = PostgresPositionRepository()
    t1 = datetime(2025, 1, 1, tzinfo=UTC)
    t2 = datetime(2025, 1, 2, tzinfo=UTC)
    repo.create(
        Position(
            run_id="r1",
            symbol="AAA",
            time=t2,
            quantity=Decimal("100"),
            avg_price=Decimal("11"),
        )
    )
    repo.create(
        Position(
            run_id="r1",
            symbol="BBB",
            time=t1,
            quantity=Decimal("50"),
            avg_price=Decimal("20"),
        )
    )

    rows = repo.list_by_run("r1")
    assert len(rows) == 2
    assert rows[0].time == t1
    assert rows[1].time == t2


@skip_without_db
def test_account_create_and_duplicate(engine, schema) -> None:
    repo: AccountRepository = PostgresAccountRepository()
    record = Account(
        run_id="r1",
        time=datetime(2025, 1, 1, tzinfo=UTC),
        cash=Decimal("100000"),
        equity=Decimal("100500"),
        margin=Decimal("0"),
    )
    repo.create(record)

    with pytest.raises(DuplicateSnapshotError):
        repo.create(record)


@skip_without_db
def test_account_list_by_run_returns_chronological(engine, schema) -> None:
    repo: AccountRepository = PostgresAccountRepository()
    t1 = datetime(2025, 1, 1, tzinfo=UTC)
    t2 = datetime(2025, 1, 2, tzinfo=UTC)
    repo.create(
        Account(run_id="r1", time=t2, cash=Decimal("1"), equity=Decimal("1"), margin=Decimal("0"))
    )
    repo.create(
        Account(run_id="r1", time=t1, cash=Decimal("1"), equity=Decimal("1"), margin=Decimal("0"))
    )

    rows = repo.list_by_run("r1")
    assert [r.time for r in rows] == [t1, t2]


@skip_without_db
def test_session_rolls_back_on_exception(engine, schema) -> None:
    """A mutation followed by an exception is not persisted."""
    from xtrade.data.engine import get_session
    from xtrade.data.orm import OrderORM

    repo: OrderRepository = PostgresOrderRepository()

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom), get_session() as session:
        session.add(
            OrderORM(
                run_id="r1",
                client_order_id="coid-rollback",
                symbol="AAA",
                side="buy",
                quantity=Decimal("100"),
                price=Decimal("10"),
                status="pending",
            )
        )
        raise _Boom

    # Nothing should have been persisted.
    assert repo.list_by_run("r1") == []
