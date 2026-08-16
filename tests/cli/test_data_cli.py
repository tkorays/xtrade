"""Tests for the ``xtrade data`` CLI subcommand group.

These tests use :class:`click.testing.CliRunner` and stub out the
data-layer dependencies (no real Postgres / xtquant required). The
focus is on argument validation, error messages, and the help
text — not the collector itself (covered by
``tests/data/collection/test_daily_xtquant_collector.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import pytest
from click.testing import CliRunner

from xtrade.cli.xtrade import cli

# ---------------------------------------------------------------------------
# Test stubs
# ---------------------------------------------------------------------------


class _NoopRepo:
    """Stand-in for any repository that does no work."""

    def list_all(self) -> list[Any]:
        return []

    def upsert(self, *args: Any, **kwargs: Any) -> None:
        return None

    def get(self, *args: Any, **kwargs: Any) -> None:
        return None


class _StubSource:
    """Stand-in for an xtquant ``DataSource`` returning empty frames."""

    def fetch_bars(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_instruments(self) -> list[Any]:
        return []

    def fetch_adjust_factors(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_trade_calendar(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        return pd.DataFrame()


class _StubRegistry:
    """Stand-in for ``SourceRegistry`` returning the stub source."""

    def get(self, name: str) -> _StubSource:
        return _StubSource()

    def names(self) -> list[str]:
        return ["xtquant"]


def _install_noop_stubs(monkeypatch: pytest.MonkeyPatch, collector_cls: type) -> None:
    """Install stubs so the CLI exercises ``collector_cls.run`` end-to-end."""
    monkeypatch.setattr("xtrade.cli.data.get_engine", lambda: None)
    monkeypatch.setattr("xtrade.cli.data._build_instrument_repo", lambda: _NoopRepo())
    monkeypatch.setattr("xtrade.cli.data._build_kline_repo", lambda: _NoopRepo())
    monkeypatch.setattr("xtrade.cli.data._build_sync_state_repo", lambda: _NoopRepo())
    monkeypatch.setattr("xtrade.cli.data._build_trade_calendar_repo", lambda: _NoopRepo())
    monkeypatch.setattr("xtrade.cli.data.DailyXtQuantCollector", collector_cls)
    monkeypatch.setattr("xtrade.cli.data.SourceRegistry", lambda: _StubRegistry())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Help / structure
# ---------------------------------------------------------------------------


def test_xtrade_help_lists_data(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0, result.stderr
    assert "data" in result.stdout


def test_data_help_lists_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "--help"])
    assert result.exit_code == 0, result.stderr
    assert "sync" in result.stdout
    assert "status" in result.stdout
    assert "reset" in result.stdout


def test_data_sync_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "sync", "--help"])
    assert result.exit_code == 0, result.stderr
    assert "--interval" in result.stdout
    assert "--batch-size" in result.stdout
    assert "--batch-size-max" in result.stdout
    assert "--lookback-days" in result.stdout
    assert "--dry-run" in result.stdout
    assert "--start-date" in result.stdout
    assert "--end-date" in result.stdout
    assert "--verbose" in result.stdout


def test_data_reset_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "reset", "--help"])
    assert result.exit_code == 0, result.stderr
    assert "--interval" in result.stdout


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def test_sync_rejects_unknown_interval(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "sync", "--interval", "5m"])
    assert result.exit_code != 0
    assert "5m" in result.stderr or "Invalid value" in result.stderr


def test_sync_rejects_batch_size_above_max(runner: CliRunner) -> None:
    result = runner.invoke(
        cli,
        [
            "data",
            "sync",
            "--interval",
            "1d",
            "--batch-size",
            "1000",
            "--batch-size-max",
            "500",
            "--dry-run",
        ],
    )
    assert result.exit_code != 0
    assert "--batch-size" in result.stderr
    assert "500" in result.stderr


def test_sync_1d_default_has_no_batch_size_max(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 1d default ``--batch-size-max`` is ``None`` (no cap), so
    ``batch_size=len(instruments)`` is accepted without rejection."""
    # Stub the instrument repo so the run does not require a real DB.
    # We only need a list of symbols; any object with a ``symbol`` attr
    # works because the collector only reads ``i.symbol``.
    from xtrade.cli import data as data_cli

    @dataclass(frozen=True)
    class _FakeInstr:
        symbol: str

    class _StubInstrumentRepo:
        def list_all(self) -> list[_FakeInstr]:
            return [_FakeInstr(symbol=f"S{i:05d}") for i in range(7000)]

        def upsert(self, record: object) -> None:  # pragma: no cover
            pass

        def get(self, symbol: str) -> _FakeInstr | None:  # pragma: no cover
            return None

    monkeypatch.setattr(data_cli, "_build_instrument_repo", _StubInstrumentRepo)

    # Use ``--dry-run`` so we exit before any xtquant / DB IO. If the
    # ``batch_size_max`` cap were still 500, this would reject 7073.
    result = runner.invoke(
        cli,
        [
            "data",
            "sync",
            "--interval",
            "1d",
            "--start-date",
            "2024-01-01",
            "--end-date",
            "2024-01-31",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.stderr


def test_sync_1m_default_still_caps_at_500(runner: CliRunner) -> None:
    """1m keeps the 500 safety valve."""
    result = runner.invoke(
        cli,
        [
            "data",
            "sync",
            "--interval",
            "1m",
            "--batch-size",
            "1000",
            "--dry-run",
        ],
    )
    assert result.exit_code != 0
    assert "--batch-size" in result.stderr
    assert "500" in result.stderr


def test_sync_1d_explicit_batch_size_max_still_honoured(runner: CliRunner) -> None:
    """Explicit ``--batch-size-max`` is honoured for 1d too."""
    result = runner.invoke(
        cli,
        [
            "data",
            "sync",
            "--interval",
            "1d",
            "--batch-size",
            "600",
            "--batch-size-max",
            "500",
            "--dry-run",
        ],
    )
    assert result.exit_code != 0
    assert "500" in result.stderr


def test_reset_rejects_unknown_interval(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["data", "reset", "--interval", "5m"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# sync with stubbed collector
# ---------------------------------------------------------------------------


def test_sync_dry_run_runs_collector_and_exits_zero(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--dry-run`` invokes the collector and exits 0."""

    @dataclass
    class StubReport:
        rows_written: int = 0
        elapsed_seconds: float = 0.0
        last_trade_date: date | None = None
        status: str = "ok"
        dry_run: bool = True
        symbols_skipped: list[tuple[str, str]] = field(default_factory=list)

        @property
        def skipped_count(self) -> int:
            return 0

        @property
        def rows_per_sec(self) -> float:
            return 0.0

    class StubCollector:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.init_calls: list[dict[str, Any]] = []

        def run(self, **kwargs: Any) -> StubReport:  # type: ignore[override]
            self.run_calls = kwargs  # type: ignore[attr-defined]
            return StubReport()

    _install_noop_stubs(monkeypatch, StubCollector)

    result = runner.invoke(cli, ["data", "sync", "--interval", "1d", "--dry-run"])
    assert result.exit_code == 0, result.stderr
    assert "(dry-run)" in result.stdout


def test_sync_failed_status_exits_nonzero(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the collector reports ``status="failed"``, exit code is 1."""

    @dataclass
    class StubReport:
        rows_written: int = 0
        elapsed_seconds: float = 0.1
        last_trade_date: date | None = None
        status: str = "failed"
        dry_run: bool = False
        symbols_skipped: list[tuple[str, str]] = field(default_factory=list)

        @property
        def skipped_count(self) -> int:
            return 0

        @property
        def rows_per_sec(self) -> float:
            return 0.0

    class StubCollector:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run(self, **kwargs: Any) -> StubReport:  # type: ignore[override]
            return StubReport()

    _install_noop_stubs(monkeypatch, StubCollector)

    result = runner.invoke(cli, ["data", "sync", "--interval", "1d"])
    assert result.exit_code != 0
    assert "sync failed" in result.stderr


# ---------------------------------------------------------------------------
# status / reset with stubbed repos
# ---------------------------------------------------------------------------


def test_status_prints_empty_when_no_rows(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("xtrade.cli.data.get_engine", lambda: None)
    monkeypatch.setattr("xtrade.cli.data._build_sync_state_repo", lambda: _NoopRepo())

    result = runner.invoke(cli, ["data", "status"])
    assert result.exit_code == 0
    assert "no watermark rows" in result.stdout


def test_status_prints_rows(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("xtrade.cli.data.get_engine", lambda: None)

    @dataclass
    class FakeRow:
        source: str
        interval: str
        last_trade_date: date | None
        last_run_at: datetime
        rows_written: int
        status: str
        error: str | None

    class StubRepo:
        def list_all(self) -> list[FakeRow]:
            return [
                FakeRow(
                    source="xtquant",
                    interval="1d",
                    last_trade_date=date(2024, 1, 5),
                    last_run_at=datetime(2024, 1, 5, 16, 0, 0, tzinfo=UTC),
                    rows_written=42,
                    status="ok",
                    error=None,
                )
            ]

    monkeypatch.setattr("xtrade.cli.data._build_sync_state_repo", lambda: StubRepo())

    result = runner.invoke(cli, ["data", "status"])
    assert result.exit_code == 0
    assert "xtquant.1d" in result.stdout
    assert "last_trade_date=2024-01-05" in result.stdout
    assert "rows=42" in result.stdout


def test_reset_succeeds_when_row_exists(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("xtrade.cli.data.get_engine", lambda: None)

    class StubRepo:
        def delete(self, source: str, interval: str) -> bool:
            return True

    monkeypatch.setattr("xtrade.cli.data._build_sync_state_repo", lambda: StubRepo())

    result = runner.invoke(cli, ["data", "reset", "--interval", "1d"])
    assert result.exit_code == 0
    assert "deleted watermark" in result.stdout


def test_reset_fails_when_row_absent(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("xtrade.cli.data.get_engine", lambda: None)

    class StubRepo:
        def delete(self, source: str, interval: str) -> bool:
            return False

    monkeypatch.setattr("xtrade.cli.data._build_sync_state_repo", lambda: StubRepo())

    result = runner.invoke(cli, ["data", "reset", "--interval", "1d"])
    assert result.exit_code != 0
    assert "nothing to delete" in result.stderr


# ---------------------------------------------------------------------------
# Ad-hoc backfill flags (--start-date / --end-date)
# ---------------------------------------------------------------------------


def test_sync_rejects_start_after_end(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--start-date > --end-date`` is rejected before any IO."""

    class GuardCollector:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run(self, *args: Any, **kwargs: Any) -> None:
            pytest.fail("collector.run must not be called when start > end")

    _install_noop_stubs(monkeypatch, GuardCollector)

    result = runner.invoke(
        cli,
        [
            "data",
            "sync",
            "--interval",
            "1d",
            "--start-date",
            "2024-02-01",
            "--end-date",
            "2024-01-01",
        ],
    )
    assert result.exit_code != 0
    assert "start_date" in result.stderr
    assert "must be <=" in result.stderr


def test_sync_passes_parsed_dates_to_collector(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI parses the date flags and forwards ``datetime.date`` to the collector."""

    @dataclass
    class StubReport:
        rows_written: int = 3
        elapsed_seconds: float = 0.1
        last_trade_date: date | None = date(2024, 1, 31)
        status: str = "ok"
        dry_run: bool = False
        symbols_skipped: list[tuple[str, str]] = field(default_factory=list)

        @property
        def skipped_count(self) -> int:
            return 0

        @property
        def rows_per_sec(self) -> float:
            return 30.0

    received: dict[str, Any] = {}

    class CapturingCollector:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run(self, **kwargs: Any) -> StubReport:  # type: ignore[override]
            received.update(kwargs)
            return StubReport()

    _install_noop_stubs(monkeypatch, CapturingCollector)

    result = runner.invoke(
        cli,
        [
            "data",
            "sync",
            "--interval",
            "1d",
            "--start-date",
            "2024-01-01",
            "--end-date",
            "2024-01-31",
        ],
    )
    assert result.exit_code == 0, result.stderr
    assert received["start_date"] == date(2024, 1, 1)
    assert received["end_date"] == date(2024, 1, 31)
