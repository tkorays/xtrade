"""Migration smoke tests."""

from __future__ import annotations

import subprocess

from .conftest import skip_without_db

EXPECTED_TABLES: set[str] = {
    "kline",
    "adjustment_factor",
    "trade_calendar",
    "instrument",
    "order",
    "trade",
    "position",
    "account",
}


@skip_without_db
def test_alembic_upgrade_creates_all_tables(db_url: str) -> None:
    """``alembic upgrade head`` against a fresh DB creates every table."""
    # Use a one-off subprocess so we exercise the same ``alembic`` CLI
    # that users will invoke, including env-var DSN resolution.
    import os

    env = {**os.environ, "XTRADE_DATA__DATABASE__URL": db_url}
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"alembic failed: {result.stderr}"

    # Verify tables via SQL.
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        ).fetchall()
    actual = {row[0] for row in rows}
    assert EXPECTED_TABLES.issubset(actual), f"missing tables: {EXPECTED_TABLES - actual}"


@skip_without_db
def test_alembic_downgrade_drops_all_tables(db_url: str) -> None:
    import os

    env = {**os.environ, "XTRADE_DATA__DATABASE__URL": db_url}
    result = subprocess.run(
        ["uv", "run", "alembic", "downgrade", "base"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"alembic failed: {result.stderr}"

    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        ).fetchall()
    actual = {row[0] for row in rows}
    project_tables = actual & EXPECTED_TABLES
    assert project_tables == set(), f"tables remained: {project_tables}"


def test_alembic_offline_sql_generation() -> None:
    """Running ``alembic upgrade head --sql`` works without a live DB."""
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head", "--sql"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"alembic failed: {result.stderr}"
    sql = result.stdout
    for table in EXPECTED_TABLES:
        # Some table names (e.g. ``order``) are SQL reserved words and
        # are emitted quoted by Alembic.
        assert f"CREATE TABLE {table} (" in sql or f'CREATE TABLE "{table}" (' in sql, (
            f"missing CREATE TABLE for {table}"
        )
