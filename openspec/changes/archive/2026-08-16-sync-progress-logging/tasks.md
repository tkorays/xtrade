## 1. Constants and constructor kwargs

- [x] 1.1 In `src/xtrade/data/collection/xtquant.py`, add module-level constants `SLOW_FETCH_SECONDS: float = 30.0` and `SLOW_UPSERT_SECONDS: float = 60.0`, and append them to `__all__`.
- [x] 1.2 Add `slow_fetch_seconds: float = SLOW_FETCH_SECONDS` and `slow_upsert_seconds: float = SLOW_UPSERT_SECONDS` as keyword-only parameters on `DailyXtQuantCollector.__init__`. Store them on `self._slow_fetch_seconds` / `self._slow_upsert_seconds`. Update the docstring.

## 2. Run-start and run-end INFO lines

- [x] 2.1 In `DailyXtQuantCollector.run`, immediately after `_resolve_window` (and the `is_ad_hoc` computation), emit one `logger.info` with the fields: `interval`, `mode` (`"ad-hoc"` / `"routine"`), `window=[start, end]`, `symbols_total`, `batch_size`, `batches_total`.
- [x] 2.2 At the very end of `run` (just before returning the `SyncReport`), emit one `logger.info` with: `status`, `rows_written`, `symbols_skipped`, `last_trade_date`, `elapsed_seconds`, `mode`.

## 3. Per-batch progress INFO line

- [x] 3.1 Refactor `_fetch_and_write_batch` to return a 5-tuple `(rows, skipped, latest, fetch_seconds, upsert_seconds)` where the last two are aggregate timings (sum of per-symbol fetch seconds, and one upsert seconds or `0.0` if the batch wrote nothing). Do not change the public surface of `run`.
- [x] 3.2 In the `run` batch loop, accumulate `cumulative_fetch_seconds` / `cumulative_upsert_seconds` and, after each batch, emit one `logger.info` with: `batch_index` (1-based), `batches_total`, `symbols_done`, `symbols_total`, `rows_written`, `symbols_skipped`, `batch_fetch_seconds`, `batch_upsert_seconds` (only when > 0), `elapsed_seconds`, and `eta_seconds` (formatted as `MM:SS` or `HH:MM:SS`, or `--` when `symbols_done == 0`).
- [x] 3.3 In `_fetch_and_write_batch`, time each `source.fetch_bars(symbol, ...)` call (a `perf_counter` before/after). When `elapsed >= slow_fetch_seconds`, emit `logger.warning("slow fetch: symbol=%s elapsed=%.2fs", symbol, elapsed)`.

## 4. Slow-upsert WARNING

- [x] 4.1 In `_fetch_and_write_batch`, time the `kline_repo.upsert_bars(merged)` call when `per_symbol_frames` is non-empty. When `elapsed >= slow_upsert_seconds`, emit `logger.warning("slow upsert: batch_rows=%d elapsed=%.2fs", rows, elapsed)`.

## 5. Tests

- [x] 5.1 In `tests/data/collection/test_daily_xtquant_collector.py`, add `test_run_start_and_end_info_lines` using `caplog` (level=INFO, logger=`xtrade.data.collection.xtquant`). Assert one record at start with `interval=1d`, `symbols_total=1`, `batches_total=1`, `mode="routine"`; one record at end with `status="ok"`, `rows_written=1`, `mode="routine"`.
- [x] 5.2 Add `test_run_ad_hoc_mode_logged`: pass `start_date=...`, assert the start/end INFO records carry `mode="ad-hoc"`.
- [x] 5.3 Add `test_per_batch_info_line`: 5 symbols, `batch_size=2` → assert one INFO record per batch (3 batches) with the expected `batch_index`, `batches_total`, `symbols_done` values.
- [x] 5.4 Add `test_slow_fetch_warning`: construct collector with `slow_fetch_seconds=0.0`; assert a WARNING record is emitted containing the offending symbol name and the elapsed seconds.
- [x] 5.5 Add `test_slow_upsert_warning`: construct collector with `slow_upsert_seconds=0.0` and a batch that writes rows; assert a WARNING record containing `slow upsert`.
- [x] 5.6 Add `test_no_per_batch_logs_when_dry_run`: pass `dry_run=True`; assert the only INFO record is the dry-run one (no run-start / per-batch / run-end INFO records).

## 6. Validation

- [x] 6.1 `uv run ruff check src tests` clean.
- [x] 6.2 `uv run ruff format --check src tests` clean.
- [x] 6.3 `uv run mypy src` strict mode clean.
- [x] 6.4 `uv run pytest -q` passes (existing + new tests).
- [x] 6.5 `npx openspec validate --all --strict` clean.
- [ ] 6.6 Manual smoke test on real MiniQMT: `uv run xtrade data sync --interval 1d` (small `instrument` subset) emits one INFO line per batch plus the run-end line on stderr; a slow `fetch_bars` triggers a WARNING.