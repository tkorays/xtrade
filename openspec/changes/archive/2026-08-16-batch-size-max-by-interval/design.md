## Context

`xtquant-bulk-fetch` (the change shipped immediately before this one) added `--batch-size-max` with a hardcoded default of `500` as a memory-pressure safety valve. It also made `--batch-size` default to `len(instruments)` for `1d` so a single bulk fetch covers the whole market.

The two defaults collide. On a 7000-symbol market, the `1d` default is `batch_size=7073`; the safety-valve check then rejects with `--batch-size must be <= 500; got 7073`, making the most important case (`xtrade data sync --interval 1d`) unusable.

The 500 cap was justified only for `1m`, where the wide frame from `xtquant.get_market_data_ex` can blow up memory (7000 symbols × 240 minutes × 240 trading days ≈ 400M rows). For `1d`, the wide frame is ~200MB at 10 years × 7000 symbols — well within operator hardware budgets. So the cap should be `1m`-only.

## Goals / Non-Goals

**Goals:**
- `1d` runs accept `batch_size = len(instruments)` without a max-rejection error.
- `1m` runs keep the 500 safety valve as the default; operators can override up if they want.
- The operator can still set `--batch-size-max` explicitly for either interval.

**Non-Goals:**
- Removing the safety valve entirely for `1m` — the OOM risk is real.
- Changing the CLI flag signature — `--batch-size-max` stays.
- Touching any collector or source code — this is purely a CLI parsing change.

## Decisions

### Decision 1: Per-interval default, single global override

`--batch-size-max` keeps its flag but its default varies by `--interval`:
- `1d` → `None` (no cap). The CLI bypasses the max check.
- `1m` → `500` (unchanged).

The operator can override with `--batch-size-max N` for either interval. The override is honoured uniformly.

### Decision 2: Constants instead of magic numbers

Two module-level constants in `cli/data.py`:
- `BATCH_SIZE_MAX_DEFAULT_1D: int | None = None`
- `BATCH_SIZE_MAX_DEFAULT_1M: int = 500`

The existing `BATCH_SIZE_MAX_DEFAULT = 500` is removed; its value lives in `BATCH_SIZE_MAX_DEFAULT_1M`. The `1d` default is `None` (literal "no cap"), which the CLI resolves at parse time.

### Decision 3: Resolution at the top of `sync_cmd`

The current code resolves `batch_size` (and the max) inside `_run_sync`. Move the max resolution to the top of `sync_cmd` (alongside the other input validations) so:
- The help text reads `--batch-size-max` (with a per-interval default shown by Click's `--help` output via `show_default`).
- The resolved max is shown in the run-start INFO line as `batch_size_max=<value or "(unbounded)">`.

Click's `default` callback can return the per-interval default; we capture `--interval` first via a small refactor (Click evaluates options in declaration order, but `--interval` is the first option after `sync_cmd`, so it's available before `--batch-size-max`).

### Decision 4: Skip the max check when `batch_size_max is None`

In `_run_sync`, the line `if batch_size > batch_size_max: raise ClickException(...)` becomes a no-op when `batch_size_max is None`. The `1d` no-cap path falls through silently.

## Risks / Trade-offs

- **[Risk] Operator forgets `--batch-size-max` and accidentally OOMs on `1m`** — Unchanged from `xtquant-bulk-fetch`. The 500 default for `1m` still applies. Operators who override `--batch-size-max` to a large value know what they're doing.
- **[Trade-off] Help text shows `None` for the `1d` default** — Click renders `None` defaults as `(default: None)` which looks ugly. Mitigation: use a `default=` callable that returns the per-interval default based on `--interval`, or set `default=-1` and treat `-1` as "unbounded" inside the logic. The cleanest is a Click `default_map` keyed by `--interval`.
- **[Risk] `1d` cap removed means a 7000-symbol, 30-year history is ~600MB wide frame** — Still well within operator hardware budgets. TimescaleDB chunked reads don't blow up; the only concern is the in-memory pandas frame during the bulk call.

## Migration Plan

Non-migrating. The change fixes a regression introduced by `xtquant-bulk-fetch`; no operator data or config needs to change.

## Open Questions

None.