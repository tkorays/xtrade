"""Tests for the ``xtrade backtest`` CLI subcommand."""

from __future__ import annotations

import json
import sys
import types

import pytest
from click.testing import CliRunner

from xtrade.cli.xtrade import cli
from xtrade.strategy.base import Bar, Context, Signal

# ---------------------------------------------------------------------------
# Fake strategy module registered in sys.modules
# ---------------------------------------------------------------------------


class _CountingStrategy:
    """A strategy that emits 1 BUY signal per bar."""

    def on_init(self, ctx: Context) -> None:
        pass

    def on_bar(self, bar: Bar, ctx: Context) -> list[Signal]:
        return []


def _register_fake_strategy(
    module_name: str,
    class_name: str,
    cls: type,
) -> str:
    module = types.ModuleType(module_name)
    setattr(module, class_name, cls)
    sys.modules[module_name] = module
    return f"{module_name}:{class_name}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_backtest_run_happy_path(runner: CliRunner) -> None:
    """A valid run call exits 0 and prints a RunSummary JSON."""
    spec = _register_fake_strategy("cli_fake_pkg1", "CountingStrategy", _CountingStrategy)
    try:
        result = runner.invoke(
            cli,
            [
                "backtest",
                "run",
                "--strategy",
                spec,
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-02",
                "--symbols",
                "A",
            ],
        )
    finally:
        del sys.modules["cli_fake_pkg1"]
    assert result.exit_code == 0, result.stderr
    # The output should be a JSON document containing the RunSummary fields.
    data = json.loads(result.stdout)
    assert "n_orders" in data
    assert "n_fills" in data
    assert "n_dropped_signals" in data
    assert "initial_account" in data
    assert "final_account" in data


def test_backtest_run_unknown_strategy_errors(runner: CliRunner) -> None:
    """A non-existent strategy yields a non-zero exit and a clear message."""
    result = runner.invoke(
        cli,
        [
            "backtest",
            "run",
            "--strategy",
            "does_not_exist:Cls",
            "--start",
            "2024-01-01",
            "--end",
            "2024-01-02",
            "--symbols",
            "A",
        ],
    )
    assert result.exit_code != 0
    assert "failed to load strategy" in result.stderr


def test_backtest_run_end_before_start_errors(runner: CliRunner) -> None:
    """--end before --start yields a clear error."""
    spec = _register_fake_strategy("cli_fake_pkg2", "CountingStrategy", _CountingStrategy)
    try:
        result = runner.invoke(
            cli,
            [
                "backtest",
                "run",
                "--strategy",
                spec,
                "--start",
                "2024-01-02",
                "--end",
                "2024-01-01",
                "--symbols",
                "A",
            ],
        )
    finally:
        del sys.modules["cli_fake_pkg2"]
    assert result.exit_code != 0
    assert "must be on or after" in result.stderr


def test_backtest_run_postgres_broker_not_yet_supported(runner: CliRunner) -> None:
    """--broker postgres yields a clear not-yet-supported error."""
    spec = _register_fake_strategy("cli_fake_pkg3", "CountingStrategy", _CountingStrategy)
    try:
        result = runner.invoke(
            cli,
            [
                "backtest",
                "run",
                "--strategy",
                spec,
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-02",
                "--symbols",
                "A",
                "--broker",
                "postgres",
            ],
        )
    finally:
        del sys.modules["cli_fake_pkg3"]
    assert result.exit_code != 0
    assert "postgres" in result.stderr.lower()


def test_backtest_run_bad_strategy_params_json(runner: CliRunner) -> None:
    """--strategy-params that's not valid JSON yields a clear error."""
    spec = _register_fake_strategy("cli_fake_pkg4", "CountingStrategy", _CountingStrategy)
    try:
        result = runner.invoke(
            cli,
            [
                "backtest",
                "run",
                "--strategy",
                spec,
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-02",
                "--symbols",
                "A",
                "--strategy-params",
                "not-json",
            ],
        )
    finally:
        del sys.modules["cli_fake_pkg4"]
    assert result.exit_code != 0
    assert "JSON" in result.stderr
