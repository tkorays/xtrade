"""Smoke tests ensuring the package is importable and wired up correctly."""

from __future__ import annotations

import xtrade


def test_package_imports() -> None:
    """The ``xtrade`` package imports without error."""
    assert xtrade is not None


def test_version_is_non_empty_string() -> None:
    """``xtrade.__version__`` is a non-empty string."""
    assert isinstance(xtrade.__version__, str)
    assert xtrade.__version__ != ""


def test_subpackages_are_importable() -> None:
    """All four top-level subpackages import without error."""
    import xtrade.core
    import xtrade.data
    import xtrade.execution
    import xtrade.risk
    import xtrade.strategy

    assert xtrade.data is not None
    assert xtrade.strategy is not None
    assert xtrade.execution is not None
    assert xtrade.risk is not None
    assert xtrade.core is not None


def test_cli_group_importable() -> None:
    """The top-level Click CLI is importable and registers ``config``."""
    from xtrade.cli.xtrade import cli

    assert cli is not None
    assert "config" in cli.commands
