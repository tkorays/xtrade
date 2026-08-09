"""Alembic environment configuration for the ``xtrade`` data layer.

Reads the DSN from the ``XTRADE_DATA__DATABASE__URL`` env var if set,
otherwise from :func:`xtrade.core.config.get_config`. Runs both in
online mode (live connection) and offline mode (``alembic upgrade head
--sql`` for CI).
"""

from __future__ import annotations

import os

# Make the project importable so ``xtrade.data.orm`` is on the path.
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from xtrade.data import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ``target_metadata`` is the combined SQLAlchemy ``MetaData`` of every
# ORM model in :mod:`xtrade.data.orm`. Importing :mod:`xtrade.data`
# registers them with ``Base.metadata``.
target_metadata = Base.metadata


def _resolve_database_url() -> str:
    """Resolve the DSN: env var wins, then ``Config``, else raise."""
    env_url = os.environ.get("XTRADE_DATA__DATABASE__URL")
    if env_url:
        return env_url
    try:
        from xtrade.core.config import get_config

        return get_config().data.database.url
    except Exception as exc:
        raise RuntimeError(
            "Alembic could not resolve a database URL. Set "
            "XTRADE_DATA__DATABASE__URL or configure the data section "
            "of ~/.xtrade/config.json."
        ) from exc


def run_migrations_offline() -> None:
    """Emit SQL to a script (``alembic upgrade head --sql ...``)."""
    url = _resolve_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live database connection."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _resolve_database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
