"""Shared fixtures for data-layer tests.

Tests that require a live Postgres are gated on ``XTRADE_TEST_DB_URL``
so they self-skip when no DB is available. The CI configuration (a
follow-up change) sets that env var to a Postgres service container.
"""

from __future__ import annotations

import os

import pytest

from xtrade.data import get_engine, reset_engine


def _has_test_db() -> bool:
    return bool(os.environ.get("XTRADE_TEST_DB_URL"))


skip_without_db = pytest.mark.skipif(
    not _has_test_db(),
    reason="XTRADE_TEST_DB_URL not set; data-layer integration tests skipped",
)


@pytest.fixture
def db_url(monkeypatch: pytest.MonkeyPatch) -> str:
    """Provide the test DB URL; monkeypatch ``XTRADE_DATA__DATABASE__URL``
    so ``get_config().data.database.url`` resolves to it.
    """
    url = os.environ["XTRADE_TEST_DB_URL"]
    monkeypatch.setenv("XTRADE_DATA__DATABASE__URL", url)
    return url


@pytest.fixture
def engine(db_url: str):
    """Yield a fresh SQLAlchemy engine bound to the test DB."""
    reset_engine()
    eng = get_engine()
    yield eng
    reset_engine()


@pytest.fixture
def schema(engine):
    """Create all ORM tables, yield, then drop them.

    Implemented via Alembic's offline-generated SQL so the test schema
    matches what a real ``alembic upgrade head`` would produce.
    """
    from sqlalchemy import text

    from xtrade.data import Base

    Base.metadata.create_all(engine)
    yield
    # Drop in reverse order to satisfy FK constraints.
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(text(f'DROP TABLE IF EXISTS "{table.name}" CASCADE'))
