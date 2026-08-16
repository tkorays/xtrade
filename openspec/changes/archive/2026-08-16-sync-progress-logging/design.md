## Context

`DailyXtQuantCollector.run` already imports the standard `logging` module and uses a module-level `logger` (`xtrade.data.collection.xtquant`) for the dry-run path, but emits nothing during the live-run phase transitions. The CLI (`xtrade.cli.data.sync_cmd`) calls `logging.basicConfig(level=INFO, ..., stream=sys.stderr)` before invoking the collector, so any new `logger.info` / `logger.warning` call from the collector flows directly to stderr without further wiring. We just need to add the calls.

The collector already iterates in fixed-size batches and has all the per-batch timings available implicitly via wall-clock measurement (`datetime.now(UTC)`). No state machine or new infrastructure is needed; the change is localised to `src/xtrade/data/collection/xtquant.py` and its tests.

## Goals / Non-Goals

**Goals:**
- Operators running `xtrade data sync` see steady progress lines (one per batch) and immediate warnings when any single `fetch_bars` or `upsert_bars` call exceeds the configured threshold.
- Threshold is testable without `time.sleep` (zero-threshold injection).
- No new public surface area; no change to `SyncReport`; no change to CLI args.

**Non-Goals:**
- Per-symbol progress lines (batch-level is enough and avoids 6000-line scroll on full-market runs).
- TUI / progress bar (the CLI is non-interactive and the stderr log is the operator's monitor).
- Adaptive throttling: thresholds are constants; per-environment tuning is left to a future change.

## Decisions

### Decision 1: Use the standard `logging` module — no new abstraction

The collector already has `logger = logging.getLogger("xtrade.data.collection.xtquant")` and the CLI already wires `basicConfig(level=INFO)`. Adding `logger.info(...)` and `logger.warning(...)` calls is sufficient; no `rich.progress`, no custom handler.

### Decision 2: Per-batch, not per-symbol, progress

The user explicitly chose batch-level granularity (120 lines for 6000 symbols at default `batch_size=50`). Per-symbol would scroll too fast for `1m` runs. The `batch_index` / `batches_total` plus `symbols_done` / `symbols_total` together give enough information to localise a stall.

### Decision 3: ETA derived from symbols_done, not batches_done

ETA = `elapsed * (symbols_total - symbols_done) / symbols_done`. When `symbols_done == 0` (still on the first batch) the ETA is `"--"` rather than a misleading infinity. Using `batches_done` instead would be less accurate when batches have unequal sizes (a 50-symbol batch in a fast region vs. a 50-symbol batch in a slow region).

### Decision 4: Thresholds injected via constructor kwargs

`slow_fetch_seconds` and `slow_upsert_seconds` default to `30.0` and `60.0` respectively; tests pass `0.0` to make every call trip the threshold without needing `time.sleep`. This matches the existing `clock` injection pattern on the collector (same constructor, same style).

### Decision 5: Slow-call WARNING fires from `_fetch_and_write_batch`, not `run`

The per-symbol timing belongs inside `_fetch_and_write_batch` (it owns the loop). The per-batch upsert timing also belongs there because that's where `kline_repo.upsert_bars` is invoked. The batch loop in `run` only sees the aggregate `(batch_rows, batch_skipped, batch_latest)`; we'd have to thread the timings back up. Putting the timing in `_fetch_and_write_batch` keeps the data flow one-directional and avoids changing the method's return type.

### Decision 6: Ad-hoc backfill mode is reported in the log lines

The `mode = "ad-hoc" if start_date is not None else "routine"` value is included in the run-start INFO and run-end INFO. The CLI summary already includes `last_trade_date`; the log adds the explicit mode so operators don't confuse a routine run with a one-off.

### Decision 7: No log inside the per-symbol `for symbol in batch:` loop

Per-symbol lines would flood stderr on full-market `1m` runs. Per-batch is the right cadence; slow-fetch WARN lines are the per-symbol escape hatch when something is genuinely stuck on one symbol.

## Risks / Trade-offs

- **[Risk] Log spam on full-market `1m` runs** → At `batch_size=50` and 6000 symbols, a routine `1m` run produces ~120 INFO lines plus the start / end lines. Acceptable; operators redirect to a file in cron.
- **[Risk] `logger.warning` triggers unrelated WARNING-level filters** → Standard library behaviour; operators who pipe the output to a stricter handler see the WARNING correctly. No filter to suppress.
- **[Risk] ETA formatting ambiguity** → Format `eta_seconds` as `MM:SS` for `eta < 1h`, `HH:MM:SS` otherwise. Spec keeps it loose ("formatted") so we can adjust the format later without re-spec.
- **[Risk] Test flakiness from wall-clock measurement** → The default `slow_fetch_seconds=30.0` would never trip in unit tests (calls take microseconds). Injection via constructor kwargs (`0.0` threshold) makes the WARN path deterministic.

## Migration Plan

Non-migrating. Operators just see more output on their next `xtrade data sync` invocation. No DB / config / env-var change.

## Open Questions

None.