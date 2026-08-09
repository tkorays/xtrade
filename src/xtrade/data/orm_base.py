"""SQLAlchemy declarative ``Base`` and shared mixins."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utc_now() -> datetime:
    """Return current UTC time, tz-aware."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Project-wide SQLAlchemy declarative base.

    Every ORM model in :mod:`xtrade.data.orm` inherits from this so that
    Alembic's ``target_metadata`` resolves to one combined metadata
    object.
    """


class TimestampMixin:
    """Adds ``created_at`` / ``updated_at`` columns with UTC defaults.

    The ``updated_at`` column is updated on ``UPDATE`` via SQLAlchemy's
    ``onupdate`` hook, which fires when the session is flushed.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
    )


__all__ = ["Base", "TimestampMixin"]
