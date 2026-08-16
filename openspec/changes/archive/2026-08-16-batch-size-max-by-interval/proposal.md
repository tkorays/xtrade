## Why

`xtquant-bulk-fetch` shipped with `--batch-size-max=500` as a hard upper bound and `--batch-size` defaulting to `len(instruments)` for `1d`. On a 7000-symbol market the two defaults collide: `1d` resolves to `batch_size=7073`, then the max check rejects it with `--batch-size must be <= 500; got 7073`. The CLI is unusable for the very case `xtquant-bulk-fetch` was built to support.

The 500 cap was a safety valve against the 1m wide-frame OOM risk. That risk only matters for `1m`; `1d` wide frames are ~200MB at 7000 symbols × 10 years, well below operator hardware budgets.

## What Changes

- `--batch-size-max` becomes per-interval:
  - `--interval 1d` → no upper bound (effectively `len(instruments)`); the operator's `--batch-size` is accepted up to that.
  - `--interval 1m` → default 500 (unchanged behaviour for `1m`).
- The constant `BATCH_SIZE_MAX_DEFAULT` is split into:
  - `BATCH_SIZE_MAX_DEFAULT_1D: int | None = None` (no cap; `None` means "no cap").
  - `BATCH_SIZE_MAX_DEFAULT_1M: int = 500` (unchanged).
- CLI resolves the active max from `--interval` at parse time. `--batch-size-max <N>` still overrides; the resolved value is shown in the run-start INFO line.
- The check `if batch_size > batch_size_max` is removed when `batch_size_max is None` (the `1d` no-cap case).

## Capabilities

### Modified Capabilities
- `xtrade-cli`: `--batch-size-max` defaults vary by `--interval`; `1d` has no upper bound.

## Impact

- Affected code:
  - `src/xtrade/cli/data.py` — split `BATCH_SIZE_MAX_DEFAULT`; resolve the max from `--interval` at the top of `sync_cmd`.
  - `tests/cli/test_data_cli.py` — update `test_sync_rejects_batch_size_above_max` and add a 1d-no-cap test.
- API: `--batch-size-max` flag stays; only the default changes.
- Backwards compatible for `1m`: same `500` default. For `1d`: previously impossible `1d` runs (the ones that triggered the user-reported error) now work.