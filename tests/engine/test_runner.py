"""Tests for :mod:`xtrade.engine.runner`."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from xtrade.engine import StrategyLoadError, load_strategy
from xtrade.strategy.base import Bar, Context, Signal, Strategy

# ---------------------------------------------------------------------------
# Helper: register a fake strategy class in `sys.modules` and return its spec.
# ---------------------------------------------------------------------------


def _register_fake(
    module_name: str,
    class_name: str,
    cls: type,
    *,
    init_kwargs: dict[str, Any] | None = None,
) -> str:
    module = types.ModuleType(module_name)
    setattr(module, class_name, cls)
    sys.modules[module_name] = module
    return f"{module_name}:{class_name}"


class _SimpleStrategy:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def on_init(self, ctx: Context) -> None:
        pass

    def on_bar(self, bar: Bar, ctx: Context) -> list[Signal]:
        return []


class _BadStrategy:
    """A class that does not satisfy the Strategy Protocol."""

    pass


def test_load_strategy_parses_module_class_spec() -> None:
    """A `module:Class` spec resolves to a Strategy instance."""
    spec = _register_fake("fake_pkg1", "SimpleStrategy", _SimpleStrategy)
    try:
        s = load_strategy(spec)
        assert isinstance(s, Strategy)
        assert isinstance(s, _SimpleStrategy)
    finally:
        del sys.modules["fake_pkg1"]


def test_load_strategy_passes_params() -> None:
    """Params are forwarded to the strategy's __init__."""
    spec = _register_fake("fake_pkg2", "SimpleStrategy", _SimpleStrategy)
    try:
        s = load_strategy(spec, {"lookback": 20})
        assert s.kwargs == {"lookback": 20}
    finally:
        del sys.modules["fake_pkg2"]


def test_load_strategy_raises_on_missing_module() -> None:
    """A missing module yields StrategyLoadError."""
    with pytest.raises(StrategyLoadError, match="could not import"):
        load_strategy("does_not_exist:Cls")


def test_load_strategy_raises_on_missing_class() -> None:
    """A module without the class yields StrategyLoadError."""
    module = types.ModuleType("fake_pkg3")
    sys.modules["fake_pkg3"] = module
    try:
        with pytest.raises(StrategyLoadError, match="has no attribute"):
            load_strategy("fake_pkg3:DoesNotExist")
    finally:
        del sys.modules["fake_pkg3"]


def test_load_strategy_raises_on_protocol_violation() -> None:
    """A class that does not satisfy Strategy Protocol yields StrategyLoadError."""
    spec = _register_fake("fake_pkg4", "BadStrategy", _BadStrategy)
    try:
        with pytest.raises(StrategyLoadError, match="does not satisfy the Strategy Protocol"):
            load_strategy(spec)
    finally:
        del sys.modules["fake_pkg4"]


def test_load_strategy_raises_on_bad_spec_format() -> None:
    """A spec without `:` yields StrategyLoadError."""
    with pytest.raises(StrategyLoadError, match="must be"):
        load_strategy("no_colon_here")


class _StrictStrategy:
    def __init__(self, lookback: int) -> None:
        self.lookback = lookback

    def on_init(self, ctx: Context) -> None:
        pass

    def on_bar(self, bar: Bar, ctx: Context) -> list[Signal]:
        return []


def test_load_strategy_raises_on_unsupported_params() -> None:
    """A class whose __init__ lacks the parameter yields StrategyLoadError."""
    spec = _register_fake("fake_pkg5_strict", "StrictStrategy", _StrictStrategy)
    try:
        with pytest.raises(StrategyLoadError, match="does not accept params"):
            load_strategy(spec, {"intentional_unused": "x"})
    finally:
        del sys.modules["fake_pkg5_strict"]
