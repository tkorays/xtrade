"""Tests for ``xtrade.data.engine`` URL normalisation and facades."""

from __future__ import annotations

from datetime import date

import pytest

from xtrade.data import create_engine
from xtrade.data.engine import _normalise_url, reset_engine

from .conftest import skip_without_db


def test_normalise_url_bare_postgresql_adds_driver() -> None:
    assert _normalise_url("postgresql://u:p@h:5432/d") == ("postgresql+psycopg://u:p@h:5432/d")


def test_normalise_url_explicit_driver_preserved() -> None:
    assert _normalise_url("postgresql+psycopg://u@h:5432/d") == ("postgresql+psycopg://u@h:5432/d")


def test_normalise_url_other_driver_preserved() -> None:
    assert _normalise_url("postgresql+pg8000://u@h:5432/d") == ("postgresql+pg8000://u@h:5432/d")


def test_create_engine_returns_sqlalchemy_engine() -> None:
    eng = create_engine("postgresql://u@h:5432/d")
    assert eng.url.drivername == "postgresql+psycopg"


@skip_without_db
def test_get_engine_singleton_returns_same_instance(db_url: str) -> None:
    from xtrade.data.engine import get_engine

    reset_engine()
    e1 = get_engine()
    e2 = get_engine()
    assert e1 is e2


@skip_without_db
def test_get_session_commits_on_success(engine, schema) -> None:
    from xtrade.data.engine import get_session
    from xtrade.data.orm import InstrumentORM

    with get_session() as session:
        session.add(
            InstrumentORM(
                symbol="AAA",
                name="Alpha",
                exchange="XSHG",
                type="Stock",
                list_date=date(2020, 1, 1),
                status="L",
            )
        )

    with get_session() as session:
        row = session.get(InstrumentORM, "AAA")
        assert row is not None
        assert row.name == "Alpha"


@skip_without_db
def test_get_session_rolls_back_on_exception(engine, schema) -> None:
    from xtrade.data.engine import get_session
    from xtrade.data.orm import InstrumentORM

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom), get_session() as session:
        session.add(
            InstrumentORM(
                symbol="BBB",
                name="Bravo",
                exchange="XSHG",
                type="Stock",
                list_date=date(2020, 1, 1),
                status="L",
            )
        )
        raise _Boom

    with get_session() as session:
        assert session.get(InstrumentORM, "BBB") is None


@skip_without_db
def test_get_connection_returns_usable_connection(engine, schema) -> None:
    from sqlalchemy import text

    from xtrade.data.engine import get_connection

    with get_connection() as conn:
        result = conn.execute(text("SELECT 1")).scalar()
        assert result == 1
