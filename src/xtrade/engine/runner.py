"""Strategy loader for the CLI.

Parse a ``module:Class`` spec into a :class:`Strategy` instance. The
class MAY take constructor arguments (kwargs) which the loader provides
from the ``params`` mapping.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any, cast

from xtrade.strategy.base import Strategy

__all__ = ["StrategyLoadError", "load_strategy"]


class StrategyLoadError(RuntimeError):
    """Raised when a strategy spec cannot be loaded or instantiated."""


def load_strategy(spec: str, params: Mapping[str, Any] | None = None) -> Strategy:
    """Load a strategy by ``module:Class`` spec.

    If ``params`` is provided and the class accepts those kwargs in its
    ``__init__``, the loader constructs an instance with them. Otherwise
    the class is constructed with no arguments.
    """
    if ":" not in spec:
        raise StrategyLoadError(f"strategy spec must be 'module:Class', got {spec!r}")
    module_name, class_name = spec.split(":", 1)
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise StrategyLoadError(f"could not import module {module_name!r}: {exc}") from exc
    try:
        cls = getattr(module, class_name)
    except AttributeError as exc:
        raise StrategyLoadError(f"module {module_name!r} has no attribute {class_name!r}") from exc
    if not isinstance(cls, type) or not issubclass(cls, object):
        raise StrategyLoadError(f"{spec!r} does not resolve to a class")
    # Verify the class satisfies the Strategy Protocol via runtime check
    # when it can be instantiated without arguments. Classes with
    # required args are validated by the caller providing params.
    try:
        probe = cls()
    except TypeError:
        probe = None
    if probe is not None and not isinstance(probe, Strategy):
        raise StrategyLoadError(f"{spec!r} does not satisfy the Strategy Protocol")
    if params:
        try:
            instance = cls(**params)
        except TypeError as exc:
            raise StrategyLoadError(
                f"strategy {spec!r} does not accept params {list(params)}: {exc}"
            ) from exc
        return cast(Strategy, instance)
    return cast(Strategy, cls())
