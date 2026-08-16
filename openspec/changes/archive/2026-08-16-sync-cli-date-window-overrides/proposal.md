## Why

The `xtrade data sync` command added by `add-daily-xtquant-data-collection` always derives its pull window from the `(source, interval)` watermark and `today`. Operators cannot ask for a one-off backfill over a specific date range (e.g. "pull all bars between 2024-01-01 and 2024-01-31") without `data reset` followed by `--lookback-days` arithmetic, and any such run would also silently advance the production watermark, polluting the next scheduled cycle.

## What Changes

- Extend `xtrade data sync` with two new optional flags: `--start-date YYYY-MM-DD` and `--end-date YYYY-MM-DD` (both default `None`).
- Extend `DailyXtQuantCollector.run` with `start_date: date | None = None` and `end_date: date | None = None` keyword-only parameters. Window resolution and watermark semantics depend on which combination is supplied.
- When **either** `--start-date` or `--end-date` is supplied, the run is treated as an **ad-hoc backfill**:
  - The window is `(start_date, end_date)` (or `(watermark, end_date)` / `(start_date, today)` when only one is supplied).
  - The `(source, interval)` watermark row is **not** mutated — the run is dry with respect to `data_sync_state`. The `status`, `error`, and `last_run_at` columns are left untouched. `rows_written` is reported on the `SyncReport` but not persisted.
- Validation: `start_date > end_date` raises `ValueError` before any IO; `end_date` in the future is allowed (no restriction) but logged at `INFO`.
- No new columns, tables, or modules. All changes live in `xtrade.data.collection.xtquant.DailyXtQuantCollector`, `xtrade.cli.data.sync_cmd`, and their tests.

## Capabilities

### New Capabilities
- (none)

### Modified Capabilities
- `data-collection-xtquant`: add `start_date` / `end_date` parameters and "ad-hoc backfill" watermark semantics to `DailyXtQuantCollector.run`; add requirements for window override + watermark immutability.
- `xtrade-cli`: add `--start-date` / `--end-date` flags to `xtrade data sync`; document the "ad-hoc backfill does not advance watermark" behaviour.

## Impact

- Affected code:
  - `src/xtrade/data/collection/xtquant.py` — `run` signature, `_resolve_window` logic, watermark branch in `run`.
  - `src/xtrade/cli/data.py` — `sync_cmd` decorator + body; new validation.
  - `tests/data/collection/test_daily_xtquant_collector.py` — new tests for each window-override combination + watermark immutability.
  - `tests/cli/test_data_cli.py` — new tests for the flags and rejection of `start > end`.
- API: existing callers of `DailyXtQuantCollector.run(interval, ...)` remain source-compatible (new params are kwargs with `None` defaults).
- Backwards compatible: `xtrade data sync --interval 1d` (no date flags) keeps today's behaviour exactly.