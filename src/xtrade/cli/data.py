"""``xtrade data`` subcommand group.

Wraps :class:`xtrade.data.collection.xtquant.DailyXtQuantCollector` and
the data-layer repositories behind a thin Click surface. Operators wire
``xtrade data sync`` into a scheduler (cron / Task Scheduler / systemd
timer) to keep Postgres fresh on a daily cadence.

Subcommands:
- ``xtrade data sync --interval {1d,1m}``: run one collection cycle.
- ``xtrade data status``: print the watermark table.
- ``xtrade data reset --interval {1d,1m}``: delete a watermark row so
   the next ``sync`` re-pulls from the configured ``lookback_days``.
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, date, datetime

import click

from xtrade.data import (
    DataSyncStateRepository,
    InstrumentRepository,
    KLineRepository,
    PostgresDataSyncStateRepository,
    PostgresInstrumentRepository,
    PostgresKLineRepository,
    PostgresTradeCalendarRepository,
    TradeCalendarRepository,
)
from xtrade.data.collection.xtquant import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_LOOKBACK_DAYS,
    MAX_LOOKBACK_DAYS,
    DailyXtQuantCollector,
    SyncReport,
)
from xtrade.data.engine import get_engine
from xtrade.data.sources.base import SourceRegistry
from xtrade.data.sources.xtquant import SUPPORTED_INTERVALS

# Upper bounds for ``--batch-size``. The cap matters for ``1m`` where
# the wide frame from ``xtquant.get_market_data_ex`` can blow up memory;
# ``1d`` wide frames are ~200MB at 7000 symbols x 10 years, well below
# operator hardware budgets, so the ``1d`` default is ``None`` (no cap).
BATCH_SIZE_MAX_DEFAULT_1D: int | None = None
BATCH_SIZE_MAX_DEFAULT_1M: int = 500

__all__ = ["data"]


# ---------------------------------------------------------------------------
# Repository / source builders (overridable by tests via monkeypatch)
# ---------------------------------------------------------------------------


def _build_instrument_repo() -> InstrumentRepository:
    """Default Postgres-backed :class:`InstrumentRepository`."""
    return PostgresInstrumentRepository()


def _build_kline_repo() -> KLineRepository:
    """Default Postgres-backed :class:`KLineRepository`."""
    # ``batch_size`` is read from the global config at CLI startup.
    from xtrade.core.config import get_config

    return PostgresKLineRepository(batch_size=get_config().data.batch_size)


def _build_sync_state_repo() -> DataSyncStateRepository:
    """Default Postgres-backed :class:`DataSyncStateRepository`."""
    return PostgresDataSyncStateRepository()


def _build_trade_calendar_repo() -> TradeCalendarRepository:
    """Default Postgres-backed :class:`TradeCalendarRepository`."""
    return PostgresTradeCalendarRepository()


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@click.group(name="data")
def data() -> None:
    """数据采集与同步命令 (Data collection commands)."""


# ---------------------------------------------------------------------------
# xtrade data sync
# ---------------------------------------------------------------------------


def _default_batch_size_max(
    ctx: click.Context, param: click.Parameter, value: int | None
) -> int | None:
    """Per-interval default for ``--batch-size-max``.

    Click resolves options in declaration order; ``--interval`` is the
    first option after ``sync_cmd``, so its value is already in
    ``ctx.params`` when this callback runs. We return the per-interval
    default unless the operator explicitly passed a value.
    """
    if value is not None:
        return value
    interval = ctx.params.get("interval")
    if interval == "1d":
        return BATCH_SIZE_MAX_DEFAULT_1D
    if interval == "1m":
        return BATCH_SIZE_MAX_DEFAULT_1M
    # Unknown interval (Click has already rejected it, but be defensive).
    return None


@data.command("sync")
@click.option(
    "--interval",
    required=True,
    type=click.Choice(sorted(SUPPORTED_INTERVALS), case_sensitive=False),
    help="K-line frequency: 1d or 1m.",
)
@click.option(
    "--batch-size",
    type=int,
    default=None,
    help=(
        "Symbols per bulk fetch_bars call. Default depends on --interval: "
        "1d → len(instruments) (whole market); 1m → 50. Use --batch-size "
        "to override. Must be <= --batch-size-max."
    ),
)
@click.option(
    "--batch-size-max",
    type=int,
    default=None,
    show_default=False,
    callback=_default_batch_size_max,
    help=(
        "Upper bound for --batch-size. Values above it are rejected "
        "before any IO. Default depends on --interval: 1d → no cap; "
        "1m → 500. Pass an explicit value to override."
    ),
)
@click.option(
    "--lookback-days",
    type=int,
    default=DEFAULT_LOOKBACK_DAYS,
    show_default=True,
    help=(
        f"Window size (days). Clamped to <= {MAX_LOOKBACK_DAYS}; the "
        "next sync re-pulls from the resolved window."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the planned window and exit; do not write.",
)
@click.option(
    "--start-date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help=(
        "Ad-hoc backfill leading edge (YYYY-MM-DD). When supplied, the "
        "run does NOT mutate data_sync_state; it is treated as a one-off "
        "backfill. Pairs with --end-date for a closed range, or used "
        "alone to mean [start-date, today]."
    ),
)
@click.option(
    "--end-date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help=(
        "Ad-hoc backfill trailing edge (YYYY-MM-DD). When supplied "
        "alone, the leading edge is the watermark-driven lookback "
        "window; the trailing edge is end-date. When supplied with "
        "--start-date, both sides are user-controlled (closed range)."
    ),
)
@click.option(
    "--verbose",
    is_flag=True,
    default=False,
    help=(
        "Lower the collector logger to DEBUG; emit one bulk-fetch "
        "DEBUG line per batch on stderr. Use this when debugging a "
        "stuck or slow run."
    ),
)
def sync_cmd(
    interval: str,
    batch_size: int | None,
    batch_size_max: int | None,
    lookback_days: int,
    dry_run: bool,
    start_date: datetime | None,
    end_date: datetime | None,
    verbose: bool,
) -> None:
    """运行一次数据采集 (Run one data collection cycle)."""
    # Configure logging so the collector's progress is visible.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
        force=True,
    )

    if interval not in SUPPORTED_INTERVALS:
        raise click.ClickException(
            f"--interval must be one of {sorted(SUPPORTED_INTERVALS)}; got {interval!r}"
        )
    if lookback_days < 1:
        raise click.ClickException(f"--lookback-days must be >= 1; got {lookback_days}")
    if lookback_days > MAX_LOOKBACK_DAYS:
        raise click.ClickException(
            f"--lookback-days must be <= {MAX_LOOKBACK_DAYS}; got {lookback_days}"
        )
    if batch_size is not None and batch_size < 1:
        raise click.ClickException(f"--batch-size must be >= 1; got {batch_size}")
    if batch_size_max is not None and batch_size_max < 1:
        raise click.ClickException(f"--batch-size-max must be >= 1; got {batch_size_max}")

    # Coerce Click's datetime → date and validate the closed range.
    start_d: date | None = start_date.date() if start_date is not None else None
    end_d: date | None = end_date.date() if end_date is not None else None
    if start_d is not None and end_d is not None and start_d > end_d:
        raise click.ClickException(
            f"start_date ({start_d.isoformat()}) must be <= end_date ({end_d.isoformat()})"
        )

    # --verbose lowers the collector logger to DEBUG for the duration of
    # this run; we capture the prior level and restore it in the
    # ``finally`` block below so a stray exception or KeyboardInterrupt
    # does not leak the level change to subsequent commands.
    collector_logger = logging.getLogger("xtrade.data.collection.xtquant")
    prior_level = collector_logger.level
    if verbose:
        collector_logger.setLevel(logging.DEBUG)

    try:
        report = _run_sync(
            interval=interval,
            batch_size=batch_size,
            batch_size_max=batch_size_max,
            lookback_days=lookback_days,
            dry_run=dry_run,
            start_date=start_d,
            end_date=end_d,
        )
    finally:
        collector_logger.setLevel(prior_level)

    click.echo(
        f"=== sync {'(dry-run) ' if dry_run else ''}complete ===\n"
        f"  rows_written:    {report.rows_written}\n"
        f"  symbols_skipped: {report.skipped_count}\n"
        f"  elapsed_seconds: {report.elapsed_seconds:.2f}\n"
        f"  rows_per_sec:    {report.rows_per_sec:.1f}\n"
        f"  last_trade_date: {report.last_trade_date.isoformat() if report.last_trade_date else '(none)'}\n"
        f"  status:          {report.status}"
    )

    if report.symbols_skipped:
        click.echo("\nskipped symbols (first 20):")
        for symbol, err in report.symbols_skipped[:20]:
            click.echo(f"  - {symbol}: {err}")
        if report.skipped_count > 20:
            click.echo(f"  ... and {report.skipped_count - 20} more")

    # Exit code: 0 on full or partial success; 1 when the run failed
    # outright (no rows written but instruments existed).
    if report.status == "failed":
        raise click.ClickException(
            f"sync failed: rows_written=0, last_trade_date={report.last_trade_date}"
        )


def _run_sync(
    *,
    interval: str,
    batch_size: int | None,
    batch_size_max: int | None,
    lookback_days: int,
    dry_run: bool,
    start_date: date | None,
    end_date: date | None,
) -> SyncReport:
    """Run the collector. Extracted from ``sync_cmd`` so the
    ``try/finally`` that restores the collector logger level wraps the
    collector call exactly, with no Click glue in between.
    """
    # Trigger engine construction (fail early if DSN is misconfigured).
    get_engine()

    try:
        source = SourceRegistry().get("xtquant")
    except KeyError as exc:
        raise click.ClickException(
            "xtquant source is not registered (is the xtquant package installed?). "
            f"Known sources: {SourceRegistry().names()}"
        ) from exc

    # Resolve ``batch_size`` from the per-interval default when the
    # operator did not pass one. We have to count instruments first;
    # this is a cheap indexed ``SELECT COUNT(*)``.
    instrument_repo = _build_instrument_repo()
    if batch_size is None:
        symbols_count = sum(1 for _ in instrument_repo.list_all())
        if interval == "1d":
            batch_size = symbols_count
        elif interval == "1m":
            batch_size = DEFAULT_BATCH_SIZE
        else:
            raise click.ClickException(
                f"unsupported interval {interval!r}; expected one of {sorted(SUPPORTED_INTERVALS)}"
            )

    if batch_size_max is not None and batch_size > batch_size_max:
        raise click.ClickException(
            f"--batch-size must be <= {batch_size_max} (--batch-size-max); got {batch_size}"
        )

    collector = DailyXtQuantCollector(
        source=source,
        instrument_repo=instrument_repo,
        kline_repo=_build_kline_repo(),
        sync_state_repo=_build_sync_state_repo(),
        trade_calendar=_build_trade_calendar_repo(),
        clock=lambda: datetime.now(UTC),
        source_name="xtquant",
    )

    try:
        return collector.run(
            interval=interval,
            batch_size=batch_size,
            lookback_days=lookback_days,
            dry_run=dry_run,
            start_date=start_date,
            end_date=end_date,
        )
    except ValueError as exc:
        # Collector-level validation errors (defence-in-depth; the CLI
        # already validates the same args above).
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# xtrade data status
# ---------------------------------------------------------------------------


@data.command("status")
def status_cmd() -> None:
    """打印当前 data_sync_state 水位线 (Print the current watermark)."""
    # Trigger engine construction.
    get_engine()

    rows = _build_sync_state_repo().list_all()
    if not rows:
        click.echo("(no watermark rows; run `xtrade data sync --interval 1d` first)")
        return

    for row in rows:
        last_run = row.last_run_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        last_td = row.last_trade_date.isoformat() if row.last_trade_date else "(none)"
        click.echo(
            f"{row.source}.{row.interval}  last_trade_date={last_td}  "
            f"rows={row.rows_written}  status={row.status}  last_run_at={last_run}"
        )


# ---------------------------------------------------------------------------
# xtrade data reset
# ---------------------------------------------------------------------------


@data.command("reset")
@click.option(
    "--interval",
    required=True,
    type=click.Choice(sorted(SUPPORTED_INTERVALS), case_sensitive=False),
    help="K-line frequency: 1d or 1m.",
)
def reset_cmd(interval: str) -> None:
    """删除指定 interval 的水位线 (Delete the watermark for ``--interval``)."""
    if interval not in SUPPORTED_INTERVALS:
        raise click.ClickException(
            f"--interval must be one of {sorted(SUPPORTED_INTERVALS)}; got {interval!r}"
        )

    # Trigger engine construction.
    get_engine()

    deleted = _build_sync_state_repo().delete("xtquant", interval)
    if deleted:
        click.echo(f"deleted watermark for xtquant.{interval}; next sync will re-pull.")
    else:
        raise click.ClickException(f"no watermark row for xtquant.{interval}; nothing to delete")
