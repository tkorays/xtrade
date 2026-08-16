## 1. CLI: per-interval `--batch-size-max` default

- [x] 1.1 In `src/xtrade/cli/data.py`, replace `BATCH_SIZE_MAX_DEFAULT: int = 500` with two constants: `BATCH_SIZE_MAX_DEFAULT_1D: int | None = None` and `BATCH_SIZE_MAX_DEFAULT_1M: int = 500`. Update module imports as needed.
- [x] 1.2 Refactor `--batch-size-max` to use a Click `default_map` keyed by `--interval`. The default returned is `BATCH_SIZE_MAX_DEFAULT_1M` for `1m` and `None` for `1d`. Operators who pass `--batch-size-max N` explicitly bypass the map.
- [x] 1.3 In `sync_cmd`, validate `batch_size_max` against `interval`: reject `batch_size_max <= 0` (other than `None`) before any IO. The max-vs-batch check inside `_run_sync` becomes a no-op when `batch_size_max is None`.
- [x] 1.4 Update the run-start INFO line emitted by the collector to include `batch_size_max=<value or "(unbounded)">` so the operator sees the resolved value in the log.

## 2. Tests

- [x] 2.1 In `tests/cli/test_data_cli.py`, update `test_sync_rejects_batch_size_above_max` to assert the **1m** default is `500` (the existing test already uses `1d`; change to `1m` or add a new test).
- [x] 2.2 Add `test_sync_1d_default_has_no_batch_size_max` — `xtrade data sync --interval 1d` against a mocked instrument_repo of 7000 symbols succeeds (no max-rejection error).
- [x] 2.3 Add `test_sync_1m_default_still_caps_at_500` — `xtrade data sync --interval 1m --batch-size 1000` rejected with the expected error.
- [x] 2.4 Add `test_sync_1d_explicit_batch_size_max_still_honoured` — `xtrade data sync --interval 1d --batch-size 600 --batch-size-max 500` rejected.

## 3. Validation

- [x] 3.1 `uv run ruff check src tests` clean.
- [x] 3.2 `uv run ruff format --check src tests` clean.
- [x] 3.3 `uv run mypy src` strict mode clean.
- [x] 3.4 `uv run pytest -q` passes (existing + new tests).
- [x] 3.5 `npx openspec validate --all --strict` clean.
- [ ] 3.6 Manual smoke test: `uv run xtrade data sync --interval 1d --start-date 2026-01-01 --end-date 2026-01-10` against the live 7000-instrument market proceeds without a `--batch-size` error.