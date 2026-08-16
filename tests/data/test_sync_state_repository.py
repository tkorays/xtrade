"""Tests for the ``data_sync_state`` repository.

Integration tests; require ``XTRADE_TEST_DB_URL``. Tests are
automatically skipped when the env var is absent (see
:mod:`tests.data.conftest`).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from xtrade.data import (
    DataSyncState,
    DataSyncStateORM,
    PostgresDataSyncStateRepository,
)
from xtrade.data.sync_state import STATUS_FAILED, STATUS_OK

from .conftest import skip_without_db

pytestmark = skip_without_db


def _make_state(
    *,
    source: str = "xtquant",
    interval: str = "1d",
    last_trade_date: date | None = date(2024, 1, 5),
    rows_written: int = 100,
    status: str = STATUS_OK,
    error: str | None = None,
) -> DataSyncState:
    return DataSyncState(
        source=source,
        interval=interval,
        last_trade_date=last_trade_date,
        last_run_at=datetime(2024, 1, 5, 16, 0, 0, tzinfo=UTC),
        rows_written=rows_written,
        status=status,
        error=error,
    )


def test_round_trip(schema: object) -> None:
    repo = PostgresDataSyncStateRepository()
    record = _make_state()
    repo.upsert(record)

    got = repo.get("xtquant", "1d")
    assert got is not None
    assert got.source == "xtquant"
    assert got.interval == "1d"
    assert got.last_trade_date == date(2024, 1, 5)
    assert got.rows_written == 100
    assert got.status == STATUS_OK
    assert got.error is None


def test_get_returns_none_when_missing(schema: object) -> None:
    repo = PostgresDataSyncStateRepository()
    assert repo.get("xtquant", "1d") is None
    assert repo.get("xtquant", "1m") is None


def test_upsert_replaces_existing_row(schema: object) -> None:
    repo = PostgresDataSyncStateRepository()
    repo.upsert(_make_state(rows_written=100, status=STATUS_OK))
    repo.upsert(_make_state(rows_written=200, status=STATUS_FAILED, error="boom"))

    got = repo.get("xtquant", "1d")
    assert got is not None
    assert got.rows_written == 200
    assert got.status == STATUS_FAILED
    assert got.error == "boom"


def test_primary_key_distinguishes_interval(schema: object) -> None:
    repo = PostgresDataSyncStateRepository()
    repo.upsert(_make_state(interval="1d", rows_written=10))
    repo.upsert(_make_state(interval="1m", rows_written=20))

    assert repo.get("xtquant", "1d") is not None
    assert repo.get("xtquant", "1m") is not None
    assert repo.get("xtquant", "1d").rows_written == 10  # type: ignore[union-attr]
    assert repo.get("xtquant", "1m").rows_written == 20  # type: ignore[union-attr]


def test_delete_returns_true_when_present(schema: object) -> None:
    repo = PostgresDataSyncStateRepository()
    repo.upsert(_make_state())
    assert repo.delete("xtquant", "1d") is True
    assert repo.get("xtquant", "1d") is None


def test_delete_returns_false_when_absent(schema: object) -> None:
    repo = PostgresDataSyncStateRepository()
    assert repo.delete("xtquant", "1d") is False


def test_list_all(schema: object) -> None:
    repo = PostgresDataSyncStateRepository()
    repo.upsert(_make_state(interval="1d", rows_written=10))
    repo.upsert(_make_state(interval="1m", rows_written=20))

    rows = repo.list_all()
    assert len(rows) == 2
    # Ordered by (source, interval).
    assert [r.interval for r in rows] == ["1d", "1m"]


def test_data_sync_state_orm_metadata_is_registered() -> None:
    """The ORM model is registered with ``Base.metadata`` so Alembic sees it."""
    from xtrade.data import Base

    assert "data_sync_state" in Base.metadata.tables
    table = Base.metadata.tables["data_sync_state"]
    cols = {c.name for c in table.columns}
    assert {
        "source",
        "interval",
        "last_trade_date",
        "last_run_at",
        "rows_written",
        "status",
        "error",
    } <= cols
    # Composite primary key on (source, interval).
    pk_cols = [c.name for c in table.primary_key.columns]
    assert pk_cols == ["source", "interval"]


# Sanity: import the ORM class to make sure the module is wired up.
def test_orm_class_importable() -> None:
    assert DataSyncStateORM.__tablename__ == "data_sync_state"


# pytest fixture parameter hint (mypy / ruff friendliness).
_ = pytest.fixture
