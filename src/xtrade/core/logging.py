"""Logging primitives for ``xtrade``.

Stub implementation: the CLI calls :func:`setup_logging` from its command
group callback, but for now it does nothing. Later capability changes can
swap this out for a richer loguru-backed implementation without changing
the public signature.
"""

from __future__ import annotations

import logging


def setup_logging() -> None:
    """Configure the root logger. Currently a no-op stub."""
    return None


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a stdlib :class:`logging.Logger` instance.

    Args:
        name: Optional logger name. When ``None``, returns the root logger.
    """
    if name is None:
        return logging.getLogger()
    return logging.getLogger(name)


__all__ = ["get_logger", "setup_logging"]
