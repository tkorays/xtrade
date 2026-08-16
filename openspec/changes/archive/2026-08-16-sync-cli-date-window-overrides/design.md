## Context

`xtrade data sync` (added by `add-daily-xtquant-data-collection`) currently exposes only `--interval`, `--batch-size`, `--lookback-days`, and `--dry-run`. The window is always `(watermark-or-today - lookback_days, today)` and the run always advances the watermark. Operators have no way to run a one-off historical backfill (e.g. "pull 2024-Q1 daily bars for all symbols") without first calling `xtrade data reset` and doing arithmetic on `--lookback-days`. Even when they do, the backfill silently pushes the watermark forward and corrupts the next scheduled cycle.

This change adds two optional flags, `--start-date` and `--end-date`, to `xtrade data sync`, with a clean semantic split:

- **Routine run** (no `start-date`): existing behaviour. Window is watermark-driven; `data_sync_state` advances.
- **Ad-hoc backfill** (`start-date` supplied): window is user-driven; `data_sync_state` is **not** mutated.

Window resolution moves into `DailyXtQuantCollector.run` (CLI is a thin pass-through). See `proposal.md` for motivation, `specs/data-collection-xtquant/spec.md` for the exact rules.

## Goals / Non-Goals

**Goals:**
- Allow ad-hoc date-range pulls from the CLI without disturbing the production watermark.
- Keep the existing `xtrade data sync` invocation identical (source-compatible default).
- Cover all four combinations: `(None, None)`, `(start, None)`, `(None, end)`, `(start, end)`.
- Validate `start_date > end_date` before any IO, both in the collector and the CLI.

**Non-Goals:**
- A separate "backfill mode" flag, a separate CLI subcommand, or a separate config knob. The presence of `--start-date` is the signal.
- Persisting the backfill run anywhere (`data_sync_state` stays untouched by design).
- Changing the existing watermark-advancement rules for the routine run.
- Changing the `lookback_days ≤ 30` cap.

## Decisions

### Decision 1: Window resolution lives in `DailyXtQuantCollector.run`, not the CLI

The CLI parses `--start-date` / `--end-date` (Click `DateTime` format → `datetime.date`) and forwards them as keyword args. All four-way precedence logic lives in `_resolve_window` (renamed from the existing helper). This matches the user's choice and keeps the CLI as a thin Click wrapper.

**Alternative considered**: have the CLI compute the window from the watermark and pass a fixed `start, end` to `run`. Rejected because it moves business logic out of the service and makes the collector awkward to call from anywhere else (e.g. a future backfill scheduler).

### Decision 2: Presence of `start_date` is the "ad-hoc backfill" signal

A run is treated as ad-hoc iff `start_date is not None`. We deliberately do **not** check `end_date` alone, because `end_date < today` with no `start_date` still wants the watermark-driven leading edge (and is, in fact, the "backfill to a fixed end date" use case the user explicitly asked for).

### Decision 3: Ad-hoc runs skip the watermark write entirely

When ad-hoc, `run` does not call `sync_state_repo.upsert` at all. `SyncReport.last_trade_date` is still computed (for the CLI to print), but no DB write happens. This guarantees:

- A scheduled `xtrade data sync --interval 1d` running at 17:00 the next day still resumes from the original watermark (or `today - lookback_days` if none).
- An operator can fire `--start-date 2024-01-01 --end-date 2024-01-31` repeatedly; each invocation re-pulls the window without side effects.
- The `status="in_progress"` best-effort write documented in the prior design is also skipped for ad-hoc runs (the row doesn't exist or shouldn't be touched).

**Alternative considered**: write a separate "backfill history" table. Rejected because it adds a migration and a new concept for a transient operator action.

### Decision 4: `start_date > end_date` raises in `_validate_inputs`

The collector's existing `_validate_inputs` is the right place. The CLI re-runs the same check after Click parses the args, before any IO (matching the existing pattern for `interval` / `lookback_days` / `batch_size`). Both produce identical error messages.

### Decision 5: Click parameter type is `click.DateTime(formats=["%Y-%D-%m", ...])` → `datetime.date`

`click.DateTime` returns `datetime.datetime` by default. We strip the time component (`dt.date()`) at the CLI boundary and pass `date` objects downstream. The collector's window API uses `date` everywhere already, so no further coercion.

**Alternative considered**: use `click.Date` (Click 8.1+). Skipped to keep the project on Click 8.0's `DateTime` family for consistency with how `lookback_days` is documented (`DEFAULT_LOOKBACK_DAYS = 5`).

### Decision 6: Re-use the existing `SyncReport`

`SyncReport` already exposes `rows_written`, `symbols_skipped`, `elapsed_seconds`, `last_trade_date`, `dry_run`, `status`. We add **no** new fields; the ad-hoc backfill is identifiable from `dry_run=False` + `last_trade_date != watermark.last_trade_date if watermark else end_date`. If operators want a machine-readable "this was a backfill" marker later, it can be added as a follow-up.

### Decision 7: No new test file

The existing `tests/data/collection/test_daily_xtquant_collector.py` and `tests/cli/test_data_cli.py` already cover the new scenarios by extending their existing parameter sets. No new file, no new fixture.

## Risks / Trade-offs

- **[Risk] Operator confusion: "I ran `--start-date 2024-01-01` and the watermark didn't advance"** → The CLI help text on `--start-date` explicitly says the run is an ad-hoc backfill and does not advance the watermark. The proposal and design carry the same note. `xtrade data status` is the canonical place to inspect the watermark.
- **[Risk] `start_date` supplied without `end_date` and the run re-pulls everything from `start_date` to `today`** → This is the documented behaviour for "pull everything from X onwards". Operators who want to stop at a specific date must also pass `--end-date`. The CLI help makes the precedence clear.
- **[Risk] Ad-hoc backfill hides failures from the watermark** → The `SyncReport.status` is still `"failed"` when no rows were written, and the CLI exit code reflects that (exit 1). Operators running an ad-hoc backfill should pipe the output to a log to capture the report.
- **[Risk] Backward-compat: `run()` callers passing only positional args** → The two new params are keyword-only with `None` defaults. All existing callers stay green; existing tests cover the no-override path.
- **[Trade-off] No automatic detection of "duplicate with watermark"** → If `start_date <= watermark.last_trade_date`, the backfill may re-pull bars that were already written; this is a no-op because `upsert_bars` is idempotent. We don't try to short-circuit it.

## Migration Plan

This change is **non-migrating** for the data plane: no DB schema change, no new env vars, no new dependencies.

Rollout:
1. Update the spec / implementation together (this change).
2. Operators can start using `--start-date` / `--end-date` immediately on the next `xtrade data sync` invocation.
3. Existing scheduled jobs are unaffected (they don't pass the new flags).

Rollback:
- Revert the commit; no DB action needed.
- `data_sync_state` is untouched by ad-hoc runs, so no cleanup is required.

## Open Questions

None. The four user clarifications (full `instrument` scope, CLI-selectable interval, `xtrade data` group, `data_sync_state` watermark) are pinned by the prior change. This change's two clarifications — ad-hoc = `start_date` is set; window resolution in collector — are pinned above.