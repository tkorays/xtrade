## 1. Collector: extend `run` with window overrides

- [x] 1.1 In `src/xtrade/data/collection/xtquant.py`, add `start_date: date | None = None` and `end_date: date | None = None` as keyword-only parameters on `DailyXtQuantCollector.run`. Update the docstring to describe the four combinations and the ad-hoc backfill semantics.
- [x] 1.2 In `_validate_inputs`, after the existing checks, reject `start_date > end_date` with `ValueError("start_date ({start}) must be <= end_date ({end})")`. Both `None` counts as "not supplied" and skips this check.
- [x] 1.3 In `_resolve_window`, implement the four-way precedence from `specs/data-collection-xtquant/spec.md` (Requirement `DailyXtQuantCollector service`, step 3). Return type stays `tuple[date, date]`.
- [x] 1.4 In `run`, gate the `sync_state_repo.upsert(...)` call on `start_date is None`. When ad-hoc, the watermark row is left untouched; `SyncReport.last_trade_date` is still populated for the CLI to print.

## 2. CLI: add `--start-date` / `--end-date` flags

- [x] 2.1 In `src/xtrade/cli/data.py`, add two new `@click.option` decorators on `sync_cmd`: `--start-date` and `--end-date`, both `type=click.DateTime(formats=["%Y-%m-%d"])`, default `None`, with help text describing the ad-hoc backfill behaviour.
- [x] 2.2 In `sync_cmd`, after the existing validations, reject `start_date and end_date and start_date.date() > end_date.date()` with `click.ClickException(start_date + " must be <= " + end_date)`. Coerce both to `datetime.date` before passing to the collector.
- [x] 2.3 Pass `start_date=start_date.date() if start_date else None` and `end_date=end_date.date() if end_date else None` to `collector.run(...)`.

## 3. Tests

- [x] 3.1 In `tests/data/collection/test_daily_xtquant_collector.py`, add a test: `start_date` and `end_date` together override the window — `source.fetch_bars` is called with `(start_date, end_date)`.
- [x] 3.2 Add a test: only `end_date` is supplied — the leading edge is `watermark.last_trade_date - lookback_days`, the trailing edge is `end_date`.
- [x] 3.3 Add a test: only `start_date` is supplied — the leading edge is `start_date`, the trailing edge is `today`; the existing watermark is ignored.
- [x] 3.4 Add a test: `start_date > end_date` raises `ValueError` before any IO (`source.fetch_bars`, `kline_repo.upsert_bars`, `sync_state_repo.get`/`upsert` are not called).
- [x] 3.5 Add a test: when `start_date` is supplied and an existing watermark row is present, the watermark row is unchanged after `run` (still the same `last_trade_date`, `status`, `last_run_at`).
- [x] 3.6 Add a test: when `start_date` is supplied and no watermark row exists, no row is created after `run`.
- [x] 3.7 In `tests/cli/test_data_cli.py`, add a test: `xtrade data sync --start-date 2024-02-01 --end-date 2024-01-01` exits non-zero with the validation error and no xtquant / DB call.
- [x] 3.8 Add a test: `xtrade data sync --start-date 2024-01-01 --end-date 2024-01-31` (with stubbed collector) prints a one-line summary, exits `0`, and the stub collector received the parsed `date` objects.

## 4. Validation

- [x] 4.1 `uv run ruff check src tests` clean.
- [x] 4.2 `uv run ruff format --check src tests` clean.
- [x] 4.3 `uv run mypy src` strict mode clean.
- [x] 4.4 `uv run pytest -q` passes (existing + new tests).
- [x] 4.5 `npx openspec validate --all --strict` clean.
- [ ] 4.6 Manual smoke test: `uv run xtrade data sync --interval 1d --start-date 2024-01-01 --end-date 2024-01-31 --dry-run` prints the planned window, exits `0`, and leaves `data_sync_state` untouched.