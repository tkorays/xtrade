"""``DailyXtQuantCollector`` — recurring ingestion flow for xtquant sources.

The collector wires a :class:`DataSource` (typically the registered
``"xtquant"`` source) to the data-layer repositories and tracks
progress in the ``data_sync_state`` table. One ``run`` invocation:

1. Validates inputs (interval, ``lookback_days``).
2. Reads the existing watermark from ``data_sync_state`` and resolves
   the target window ``[start, end]``.
4. Iterates ``instrument_repo.list_all()`` in batches of ``batch_size``;
   per symbol, calls ``source.fetch_bars`` and merges into one
   DataFrame; calls ``kline_repo.upsert_bars`` per batch.
5. Updates the watermark row: advances ``last_trade_date`` on success,
   marks ``status="failed"`` with the error message when no rows were
   written.

The collector is intentionally serial — xtquant's client is single-process.
Per-symbol errors are accumulated into :attr:`SyncReport.symbols_skipped`
and do not abort the run.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from xtrade.data.market_data.instrument import InstrumentRepository
from xtrade.data.market_data.kline import KLineRepository
from xtrade.data.market_data.trade_calendar import TradeCalendarRepository
from xtrade.data.sources.base import DataSource
from xtrade.data.sources.xtquant import SUPPORTED_INTERVALS
from xtrade.data.sync_state import (
    STATUS_FAILED,
    STATUS_OK,
    DataSyncState,
    DataSyncStateRepository,
)

logger = logging.getLogger("xtrade.data.collection.xtquant")

# Hard cap on ``--lookback-days``; lifted at code-review time, not in CLI.
MAX_LOOKBACK_DAYS: int = 30

# Default batch size when the caller doesn't specify one.
DEFAULT_BATCH_SIZE: int = 50

# Default lookback window when no watermark exists.
DEFAULT_LOOKBACK_DAYS: int = 5

# Threshold above which a single ``source.fetch_bars`` call is reported
# as slow (seconds). Injectable per collector instance.
SLOW_FETCH_SECONDS: float = 30.0

# Threshold above which a single ``kline_repo.upsert_bars`` call is
# reported as slow (seconds). Injectable per collector instance.
SLOW_UPSERT_SECONDS: float = 60.0

# Per-symbol DEBUG progress line emitted by ``_fetch_and_write_batch``
# immediately before each ``source.fetch_bars`` call. The CLI's
# ``--verbose`` flag lowers the logger level to ``DEBUG`` to make this
# line visible. The format string is exposed so tests can assert the
# expected fields.
VERBOSE_SYMBOL_PROGRESS_FORMAT: str = (
    "{symbol_index}/{symbols_total}  sym={symbol}  interval={interval}"
)

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_LOOKBACK_DAYS",
    "MAX_LOOKBACK_DAYS",
    "SLOW_FETCH_SECONDS",
    "SLOW_UPSERT_SECONDS",
    "VERBOSE_SYMBOL_PROGRESS_FORMAT",
    "DailyXtQuantCollector",
    "SyncReport",
]


# ---------------------------------------------------------------------------
# SyncReport — the collector's return value
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SyncReport:
    """Outcome of one :meth:`DailyXtQuantCollector.run` invocation.

    Fields:
        rows_written: total rows persisted to ``kline_1d`` / ``kline_1m``.
        symbols_skipped: ``(symbol, error_message)`` pairs for symbols
            whose ``source.fetch_bars`` returned no data or raised.
        elapsed_seconds: wall-clock duration of the run.
        last_trade_date: watermark's new ``last_trade_date`` (or ``None``
            when no rows were written).
        dry_run: ``True`` when the run made no writes.
        status: ``"ok"`` on full / partial success; ``"failed"`` when
            no rows were written.
    """

    rows_written: int
    symbols_skipped: list[tuple[str, str]]
    elapsed_seconds: float
    last_trade_date: date | None
    dry_run: bool
    status: str

    @property
    def rows_per_sec(self) -> float:
        return self.rows_written / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0

    @property
    def skipped_count(self) -> int:
        return len(self.symbols_skipped)


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class DailyXtQuantCollector:
    """Pull bars from a :class:`DataSource` and persist via repositories.

    The constructor performs no IO; all xtquant / DB work happens inside
    :meth:`run`. This makes the class trivial to unit-test with fake
    repositories (see ``tests/data/collection/test_daily_xtquant_collector.py``).
    """

    def __init__(
        self,
        source: DataSource,
        instrument_repo: InstrumentRepository,
        kline_repo: KLineRepository,
        sync_state_repo: DataSyncStateRepository,
        trade_calendar: TradeCalendarRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        source_name: str = "xtquant",
        slow_fetch_seconds: float = SLOW_FETCH_SECONDS,
        slow_upsert_seconds: float = SLOW_UPSERT_SECONDS,
    ) -> None:
        """Construct the collector.

        Args:
            source: Producer-side data source. Must satisfy the
                :class:`DataSource` Protocol; typically the registered
                ``"xtquant"`` source.
            instrument_repo: Repository used to enumerate symbols.
            kline_repo: Repository used to persist bars.
            sync_state_repo: Repository used to read / write the
                ``data_sync_state`` watermark.
            trade_calendar: Repository used to clamp ``start`` to a
                trading day via :meth:`get_trading_days`.
            clock: Injectable wall-clock for tests. Defaults to
                ``datetime.now(UTC)``.
            source_name: The ``source`` column stored in
                ``data_sync_state``. Defaults to ``"xtquant"``.
            slow_fetch_seconds: Threshold above which a single
                ``source.fetch_bars`` call is reported as slow via a
                ``WARNING`` log record. Defaults to
                :data:`SLOW_FETCH_SECONDS` (``30.0``).
            slow_upsert_seconds: Threshold above which a single
                ``kline_repo.upsert_bars`` call is reported as slow via
                a ``WARNING`` log record. Defaults to
                :data:`SLOW_UPSERT_SECONDS` (``60.0``).
        """
        self._source = source
        self._instrument_repo = instrument_repo
        self._kline_repo = kline_repo
        self._sync_state_repo = sync_state_repo
        self._trade_calendar = trade_calendar
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._source_name = source_name
        self._slow_fetch_seconds = slow_fetch_seconds
        self._slow_upsert_seconds = slow_upsert_seconds

    # ---- public API ----

    def run(
        self,
        interval: str,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        dry_run: bool = False,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> SyncReport:
        """Execute one collection cycle for ``interval``.

        Args:
            interval: One of ``"1d"``, ``"1m"``.
            batch_size: Symbols per ``source.fetch_bars`` loop chunk.
            lookback_days: Window size when no watermark exists, or the
                trailing window when a watermark exists (see
                :meth:`_resolve_window`).
            dry_run: Plan the run but make no writes; ``data_sync_state``
                is left untouched.
            start_date: Optional explicit window-leading override. When
                supplied the run is treated as an **ad-hoc backfill**:
                the watermark row in ``data_sync_state`` is NOT mutated,
                regardless of outcome. Pairs with ``end_date`` to define
                a closed range, or alone to mean ``[start_date, today]``.
            end_date: Optional explicit window-trailing override. When
                supplied alone (without ``start_date``) the leading edge
                still derives from the watermark + ``lookback_days``; the
                trailing edge is ``end_date``. When supplied alongside
                ``start_date`` it closes the ad-hoc backfill window.

        Returns:
            :class:`SyncReport` summarising the outcome.

        Raises:
            ValueError: For invalid ``interval``, ``lookback_days``, or
                ``start_date > end_date``.
        """
        self._validate_inputs(
            interval=interval,
            lookback_days=lookback_days,
            start_date=start_date,
            end_date=end_date,
        )

        started = datetime.now(UTC)
        watermark = self._sync_state_repo.get(self._source_name, interval)
        today = started.date()
        start, end = self._resolve_window(
            interval=interval,
            today=today,
            watermark_last_trade_date=watermark.last_trade_date if watermark else None,
            lookback_days=lookback_days,
            start_date=start_date,
            end_date=end_date,
        )

        is_ad_hoc = start_date is not None
        mode = "ad-hoc" if is_ad_hoc else "routine"

        symbols = [inst.symbol for inst in self._instrument_repo.list_all()]
        if not symbols or dry_run:
            elapsed = (datetime.now(UTC) - started).total_seconds()
            if dry_run:
                logger.info(
                    "[dry-run] would process %d symbols, interval=%s, window=[%s, %s], "
                    "batch_size=%d",
                    len(symbols),
                    interval,
                    start,
                    end,
                    batch_size,
                )
            return SyncReport(
                rows_written=0,
                symbols_skipped=[],
                elapsed_seconds=elapsed,
                last_trade_date=watermark.last_trade_date if watermark else None,
                dry_run=dry_run,
                status=STATUS_OK if dry_run else (STATUS_OK if watermark else STATUS_FAILED),
            )

        symbols_total = len(symbols)
        batches_total = (symbols_total + batch_size - 1) // batch_size
        logger.info(
            "sync start: interval=%s mode=%s window=[%s, %s] symbols_total=%d "
            "batch_size=%d batches_total=%d",
            interval,
            mode,
            start,
            end,
            symbols_total,
            batch_size,
            batches_total,
        )

        rows_written = 0
        symbols_skipped: list[tuple[str, str]] = []
        latest_seen: date | None = None

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            (
                batch_rows,
                batch_skipped,
                batch_latest,
                batch_fetch_seconds,
                batch_upsert_seconds,
            ) = self._fetch_and_write_batch(
                batch,
                start,
                end,
                interval,
                symbol_offset=i,
                symbols_total=symbols_total,
            )
            rows_written += batch_rows
            symbols_skipped.extend(batch_skipped)
            if batch_latest is not None and (latest_seen is None or batch_latest > latest_seen):
                latest_seen = batch_latest

            batch_index = i // batch_size + 1
            symbols_done = i + len(batch)
            elapsed = (datetime.now(UTC) - started).total_seconds()
            eta_seconds = (
                elapsed * (symbols_total - symbols_done) / symbols_done
                if symbols_done > 0
                else None
            )
            eta_str = _format_eta(eta_seconds) if eta_seconds is not None else "--"
            logger.info(
                "batch %d/%d  symbols_done=%d/%d  rows_written=%d  symbols_skipped=%d  "
                "fetch=%.2fs upsert=%.2fs elapsed=%.2fs eta=%s",
                batch_index,
                batches_total,
                symbols_done,
                symbols_total,
                rows_written,
                len(symbols_skipped),
                batch_fetch_seconds,
                batch_upsert_seconds if batch_upsert_seconds > 0 else 0.0,
                elapsed,
                eta_str,
            )

        elapsed = (datetime.now(UTC) - started).total_seconds()
        now = self._clock()

        # Compute the report's ``last_trade_date`` and status, but only
        # PERSIST them when this is a routine run. Ad-hoc backfills leave
        # ``data_sync_state`` exactly as they found it.
        if rows_written == 0:
            status = STATUS_FAILED
            error = "no rows written (all symbols skipped or empty)"
            new_last_trade_date = watermark.last_trade_date if watermark else None
        else:
            status = STATUS_OK
            error = None
            # Advance to the latest bar actually written; fall back to
            # the existing watermark if no new bar was seen.
            if latest_seen is not None:
                new_last_trade_date = latest_seen
            elif watermark is not None:
                new_last_trade_date = watermark.last_trade_date
            else:
                new_last_trade_date = None

        if not is_ad_hoc:
            self._sync_state_repo.upsert(
                DataSyncState(
                    source=self._source_name,
                    interval=interval,
                    last_trade_date=new_last_trade_date,
                    last_run_at=now,
                    rows_written=rows_written,
                    status=status,
                    error=error,
                )
            )

        logger.info(
            "sync done: status=%s rows_written=%d symbols_skipped=%d "
            "last_trade_date=%s elapsed=%.2fs mode=%s",
            status,
            rows_written,
            len(symbols_skipped),
            new_last_trade_date.isoformat() if new_last_trade_date else "(none)",
            elapsed,
            mode,
        )

        return SyncReport(
            rows_written=rows_written,
            symbols_skipped=symbols_skipped,
            elapsed_seconds=elapsed,
            last_trade_date=new_last_trade_date,
            dry_run=False,
            status=status,
        )

    # ---- internals ----

    @staticmethod
    def _validate_inputs(
        *,
        interval: str,
        lookback_days: int,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> None:
        if interval not in SUPPORTED_INTERVALS:
            raise ValueError(
                f"interval must be one of {sorted(SUPPORTED_INTERVALS)}, got {interval!r}"
            )
        if lookback_days < 1:
            raise ValueError(f"lookback_days must be >= 1, got {lookback_days}")
        if lookback_days > MAX_LOOKBACK_DAYS:
            raise ValueError(f"lookback_days must be <= {MAX_LOOKBACK_DAYS}, got {lookback_days}")
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError(
                f"start_date ({start_date.isoformat()}) must be <= "
                f"end_date ({end_date.isoformat()})"
            )

    def _resolve_window(
        self,
        *,
        interval: str,
        today: date,
        watermark_last_trade_date: date | None,
        lookback_days: int,
        start_date: date | None,
        end_date: date | None,
    ) -> tuple[date, date]:
        """Return ``(start, end)`` for this run.

        Four-way precedence:

        1. ``start_date`` AND ``end_date``: ``(start_date, end_date)``.
        2. Only ``start_date``: ``(start_date, today)``.
        3. Only ``end_date``: leading edge is the watermark-driven
           ``lookback_days`` window clamped forward to the next trading
           day; trailing edge is ``end_date``.
        4. Neither: existing behaviour. Leading edge is the
           watermark-driven window; trailing edge is ``today``.
        """
        if start_date is not None and end_date is not None:
            return start_date, end_date

        if start_date is not None:
            return start_date, today

        # Watermark-driven leading edge, clamped to a trading day.
        if watermark_last_trade_date is not None:
            raw_start = watermark_last_trade_date - timedelta(days=lookback_days)
        else:
            raw_start = today - timedelta(days=lookback_days)

        effective_end = end_date if end_date is not None else today
        # Clamp ``start`` to the next trading day on or after ``raw_start``.
        # ``get_trading_days`` returns trading days; if ``raw_start`` is
        # itself a trading day, it appears in the list and we use it.
        # No calendar data; fall back to the raw value. The collector still
        # works against the raw ``raw_start`` even without a trade calendar
        # because xtquant will simply return empty frames for non-trading
        # days.
        trading = self._trade_calendar.get_trading_days(raw_start, effective_end)
        start = trading[0] if trading else raw_start

        return start, effective_end

    def _fetch_and_write_batch(
        self,
        batch: list[str],
        start: date,
        end: date,
        interval: str,
        *,
        symbol_offset: int = 0,
        symbols_total: int = 0,
    ) -> tuple[int, list[tuple[str, str]], date | None, float, float]:
        """Fetch one batch via the source and upsert via the repository.

        Returns ``(rows_written, skipped_symbols, latest_trade_date,
        cumulative_fetch_seconds, upsert_seconds)``. ``upsert_seconds``
        is ``0.0`` when the batch wrote nothing.

        Per-symbol exceptions are caught and returned as skipped
        records; they do NOT abort the batch. Each ``fetch_bars`` call
        is timed; a single call exceeding
        :attr:`_slow_fetch_seconds` triggers a ``WARNING``.

        Args:
            batch: Symbols to process in this batch.
            start: Window start (inclusive).
            end: Window end (inclusive).
            interval: ``"1d"`` or ``"1m"``.
            symbol_offset: 0-based run-wide index of the first symbol
                in ``batch``. Used by the per-symbol DEBUG line.
            symbols_total: Total symbols in the run; ``0`` disables the
                DEBUG line (used by tests that don't care about it).
        """
        per_symbol_frames: list[pd.DataFrame] = []
        skipped: list[tuple[str, str]] = []
        latest: date | None = None
        fetch_seconds = 0.0

        for idx, symbol in enumerate(batch):
            if symbols_total > 0:
                logger.debug(
                    VERBOSE_SYMBOL_PROGRESS_FORMAT.format(
                        symbol_index=symbol_offset + idx + 1,
                        symbols_total=symbols_total,
                        symbol=symbol,
                        interval=interval,
                    )
                )
            t0 = time.perf_counter()
            try:
                df = self._source.fetch_bars(symbol, start, end, interval)
            except Exception as exc:
                elapsed = time.perf_counter() - t0
                fetch_seconds += elapsed
                if elapsed >= self._slow_fetch_seconds:
                    logger.warning(
                        "slow fetch: symbol=%s elapsed=%.2fs (threshold=%.2fs)",
                        symbol,
                        elapsed,
                        self._slow_fetch_seconds,
                    )
                skipped.append((symbol, f"{type(exc).__name__}: {exc}"))
                continue
            elapsed = time.perf_counter() - t0
            fetch_seconds += elapsed
            if elapsed >= self._slow_fetch_seconds:
                logger.warning(
                    "slow fetch: symbol=%s elapsed=%.2fs (threshold=%.2fs)",
                    symbol,
                    elapsed,
                    self._slow_fetch_seconds,
                )
            if df is None or df.empty:
                # xtquant returned nothing for this symbol; treat as
                # "no data in window", not an error.
                continue
            df = df.copy()
            df["symbol"] = symbol
            df["interval"] = interval
            per_symbol_frames.append(df)

        if not per_symbol_frames:
            return 0, skipped, None, fetch_seconds, 0.0

        merged = pd.concat(per_symbol_frames, ignore_index=True)
        # ``upsert_bars`` requires the columns in
        # ``KLINE_REQUIRED_COLUMNS``; the column order does not matter
        # but ``time`` must be present.
        t_up = time.perf_counter()
        rows = self._kline_repo.upsert_bars(merged)
        upsert_seconds = time.perf_counter() - t_up
        if upsert_seconds >= self._slow_upsert_seconds:
            logger.warning(
                "slow upsert: batch_rows=%d elapsed=%.2fs (threshold=%.2fs)",
                rows,
                upsert_seconds,
                self._slow_upsert_seconds,
            )

        # Track the latest bar's ``time`` for the watermark. For ``1d``
        # ``time`` is ``datetime.date``; for ``1m`` it's a tz-aware
        # ``Timestamp``.
        ts_series = pd.to_datetime(merged["time"], utc=True)
        if interval == "1d":
            batch_latest: date | None = ts_series.max().date()
        else:
            batch_latest = ts_series.max().date()
        if batch_latest is not None and (latest is None or batch_latest > latest):
            latest = batch_latest

        return rows, skipped, latest, fetch_seconds, upsert_seconds


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _format_eta(eta_seconds: float) -> str:
    """Format a remaining-time estimate as ``MM:SS`` or ``HH:MM:SS``.

    Negative or non-finite inputs collapse to ``"--"``.
    """
    if eta_seconds is None or eta_seconds < 0 or eta_seconds != eta_seconds:
        return "--"
    total = int(eta_seconds)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


# Silence unused-import warning on ``field`` (kept for future dataclass
# extensions; ``SyncReport`` is frozen so no mutable defaults needed).
_ = field
