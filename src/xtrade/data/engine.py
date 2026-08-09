"""Engine, session, and raw-connection facades for the data layer.

Why one engine with two facades:
    The two-path contract (see :mod:`xtrade.data`) requires the time-series
    repos to use :func:`get_connection` (raw psycopg) and the broker / ORM
    repos to use :func:`get_session`. Both facades draw from the same
    :class:`sqlalchemy.Engine` so the connection pool stays coherent and
    only one place needs to know the DSN.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine
from sqlalchemy import create_engine as _sa_create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from xtrade.data.orm_base import Base

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _normalise_url(url: str) -> str:
    """Convert bare ``postgresql://`` URIs to ``postgresql+psycopg``.

    Bare URIs are accepted for ergonomics (DSNs copied from dashboards
    rarely include the driver prefix). Explicit driver prefixes are
    preserved verbatim so users who deliberately chose another driver
    (e.g. ``postgresql+pg8000``) get exactly what they asked for.

    Implementation note: ``URL.create`` does not parse a bare
    ``postgresql://`` URI into host/port/database components (it keeps
    the drivername as the full string). We use ``sqlalchemy.engine.url.make_url``
    which honours the full SQLAlchemy URL grammar and gives us a
    proper ``URL`` object back, then we rebuild it with the
    ``postgresql+psycopg`` driver.
    """
    from sqlalchemy.engine.url import make_url

    parsed = make_url(url)
    if parsed.drivername != "postgresql":
        return url
    return parsed.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def create_engine(url: str, **kwargs: Any) -> Engine:
    """Build a SQLAlchemy :class:`Engine` from a DSN, normalising the driver.

    The engine is **not** cached — call sites that want the singleton
    must use :func:`get_engine` instead.
    """
    return _sa_create_engine(_normalise_url(url), future=True, **kwargs)


def get_engine(url: str | None = None) -> Engine:
    """Return the process-wide singleton engine.

    On first call the URL is taken from
    :func:`xtrade.core.config.get_config` (or the ``url`` argument if
    provided). Subsequent calls return the cached engine unless ``url``
    is explicitly supplied, in which case a new engine is built and
    installed as the singleton.
    """
    global _engine, _session_factory
    if url is not None or _engine is None:
        target_url = url
        if target_url is None:
            from xtrade.core.config import get_config

            target_url = get_config().data.database.url
        _engine = create_engine(target_url)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    assert _engine is not None
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (used by tests / ``XTRADE_DATA__DATABASE__URL`` changes)."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a SQLAlchemy ``Session`` that commits on success / rolls back on exception.

    Usage::

        with get_session() as session:
            repo = OrderRepository(session)
            repo.create(record)
        # commit fires here on normal exit.
    """
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def get_connection() -> Iterator[Connection]:
    """Yield a raw SQLAlchemy ``Connection`` (psycopg underneath).

    Use this when the time-series repos borrow a connection to run
    ``cursor.copy()`` / ``executemany`` / ``pd.read_sql``. The connection
    is committed on success / rolled back on exception.
    """
    engine = get_engine()
    conn = engine.connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


__all__ = [
    "Base",
    "create_engine",
    "get_connection",
    "get_engine",
    "get_session",
    "reset_engine",
]
