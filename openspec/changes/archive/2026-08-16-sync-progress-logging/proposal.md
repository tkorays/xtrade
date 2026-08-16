## Why

`xtrade data sync` is currently silent between invocation and the final `=== sync complete ===` summary. When `xtdata.download_history_data2` / `get_local_data` block on a sluggish MiniQMT or the network, or when `kline_repo.upsert_bars` stalls on a slow COPY, operators have no way to tell whether the run is alive, where in the symbol list it currently is, or which stage is slow. The collector already imports `logging` and uses `logger.info` for the dry-run path; the work is to extend the same `logger` to cover the live-run path so the existing `logging.basicConfig` in the CLI surfaces it.

## What Changes

- Add `logger.info` calls inside `DailyXtQuantCollector.run` and `_fetch_and_write_batch` for every observable phase transition.
- Add `logger.warn` (per-spec WARN, per-design WLOG, per-source code `logger.warning`) calls when a single `source.fetch_bars` call exceeds `SLOW_FETCH_SECONDS` (default `30.0`) or when a single `kline_repo.upsert_bars` call exceeds `SLOW_UPSERT_SECONDS` (default `60.0`).
- Per-batch progress line at `INFO`: `batch 12/120 (symbols 600-650/6000) fetch=2.3s upsert=0.8s rows=247 skipped=1 elapsed=45.2s eta=02:13`.
- One `INFO` line at run start (window summary, batch count, ad-hoc vs routine) and one `INFO` line at run end (already covered by the CLI summary; collector emits an extra one for symmetry).
- No new public surface. All logging is internal; the `SyncReport` and the CLI help / exit codes are unchanged.
- New module-level constants `SLOW_FETCH_SECONDS = 30.0`, `SLOW_UPSERT_SECONDS = 60.0` in `xtrade.data.collection.xtquant`.
- New constructor kwarg `slow_fetch_seconds: float = SLOW_FETCH_SECONDS` and `slow_upsert_seconds: float = SLOW_UPSERT_SECONDS` on `DailyXtQuantCollector` for test injection.

## Capabilities

### New Capabilities
- (none)

### Modified Capabilities
- `data-collection-xtquant`: add requirements for batch-level progress logging, slow-call WARN thresholds, and the run-start / run-end INFO lines. The `DailyXtQuantCollector` capability is extended.

## Impact

- Affected code:
  - `src/xtrade/data/collection/xtquant.py` — new constants, new constructor kwargs, new `logger.info/warning` calls in `run` and `_fetch_and_write_batch`.
  - `tests/data/collection/test_daily_xtquant_collector.py` — new tests using `caplog` to assert the emitted log records.
- API: existing callers of `DailyXtQuantCollector(...)` and `run(...)` remain source-compatible (new params are kwargs with defaults).
- CLI / `SyncReport`: unchanged.
- Performance: `logger.info` is cheap when the level is above `INFO`; no extra IO; no DB calls added.