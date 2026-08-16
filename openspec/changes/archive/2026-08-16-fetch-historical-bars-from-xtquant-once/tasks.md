## 1. Merge helper (pure, unit-testable)

- [x] 1.1 In `scripts/fetch_historical_bars_xtquant.py`, add a top-level function `merge_xtquant_bars(ret: dict[str, pd.DataFrame], interval: str) -> pd.DataFrame` that merges per-symbol frames.
- [x] 1.2 The helper drops any `pre_close` column before insert; renames `preClose` → `pre_close` defensively (then drops it); converts `time` (ms-UTC int64) to Asia/Shanghai `Timestamp` and then either `.dt.date` (for `1d`) or kept tz-aware (for `1m`); adds the `interval` column; sorts by `(symbol, time_col)`.
- [x] 1.3 The helper skips `None` / empty frames silently; returns an empty DataFrame if every frame is empty.
- [x] 1.4 Output column order is `[symbol, time, interval, open, high, low, close, volume, amount]` (matches `KLINE_REQUIRED_COLUMNS`).

## 2. Instrument discovery

- [x] 2.1 Add a top-level function `list_instrument_symbols(limit: int | None = None) -> list[str]` that reads `SELECT symbol FROM instrument ORDER BY symbol` via `get_engine().connect()`. If `limit` is set, take the first `limit` rows.
- [x] 2.2 If the `instrument` table is empty, return `[]` (the script's main loop will report `0 symbols` and exit 0).

## 3. Per-batch download + write

- [x] 3.1 In `scripts/fetch_historical_bars_xtquant.py`, add a top-level function `fetch_and_write_batch(symbols: list[str], start: date, end: date, interval: str, batch_size: int, dry_run: bool) -> tuple[int, list[tuple[str, str]]]` returning `(rows_written, skipped_symbols)`.
- [x] 3.2 Format the date strings per Decision 6 (1d → `%Y%m%d`, 1m → `%Y%m%d%H%M%S` with `time.min`/`time.max`).
- [x] 3.3 Call `xtdata.download_history_data2(stock_list=symbols, period=interval, start_time=..., end_time=...)`. Catch any exception and add a `(symbol, error)` record to `skipped_symbols` for every symbol in the batch.
- [x] 3.4 Call `xtdata.get_local_data(stock_list=symbols, period=interval, start_time=..., end_time=..., dividend_type="none")`.
- [x] 3.5 Pass the result through `merge_xtquant_bars(ret, interval)`. If empty, return `(0, [])`.
- [x] 3.6 Call `PostgresKLineRepository(batch_size=batch_size).upsert_bars(df)` and return `len(df)` as `rows_written`.
- [x] 3.7 In `dry_run=True`, skip the xtquant calls and the upsert; return `(0, [])`.

## 4. CLI surface + main loop

- [x] 4.1 Add `argparse` with `--interval {1d,1m}` (required), `--start YYYY-MM-DD` (required), `--end YYYY-MM-DD` (required), `--batch-size N` (default `50`), `--limit N` (default `0` = unlimited), `--dry-run`.
- [x] 4.2 Validate `--start <= --end` before any DB / xtquant access; exit non-zero on violation.
- [x] 4.3 Validate `--interval ∈ {1d, 1m}`; exit non-zero on violation.
- [x] 4.4 Lazy-import `xtquant.xtdata` after argument validation passes.
- [x] 4.5 Build a list of `date` objects from the `--start` / `--end` strings.
- [x] 4.6 Iterate `list_instrument_symbols(limit=...)` in chunks of `--batch-size`; for each chunk call `fetch_and_write_batch(...)`; accumulate `(rows_written, skipped_symbols)` into a `Report` dataclass.
- [x] 4.7 Print a progress line every ≥1 second: `[N/total] symbols processed, M rows written, T secs, S skipped`.
- [x] 4.8 At end, print a final summary: symbols_processed / symbols_total, rows_written, elapsed_seconds, rows_per_sec, symbols_skipped. Exit non-zero only if `symbols_processed == 0` AND `symbols_total > 0`.

## 5. Project metadata

- [x] 5.1 In `pyproject.toml`, add `[project.optional-dependencies.xtquant]` with `xtquant`. The script imports it lazily and surfaces `ModuleNotFoundError` if not installed.
- [x] 5.2 No CLI registration; the script is a standalone file under `scripts/`. Add `scripts/fetch_historical_bars_xtquant.py` to the existing `ruff.src` scope if needed (already in place from `import-legacy-parquet-bars-once`).

## 6. Tests

- [x] 6.1 `tests/scripts/test_fetch_historical_bars_xtquant.py`:
  - Test `merge_xtquant_bars` with a 2-symbol dict of synthetic DataFrames; assert the result has the expected schema and the `pre_close` column is dropped.
  - Test that an empty dict returns an empty DataFrame (no exception).
  - Test that a frame with `None` value is skipped silently.
  - Test that for `1d`, the `time` column is `datetime.date`, not `Timestamp`.
  - Test that for `1m`, the `time` column is a tz-aware `Timestamp`.
  - Test that the tz-conversion fix is correct: `time=0` (1970-01-01 UTC) → `1970-01-01` in Asia/Shanghai, NOT `1969-12-31`.
  - Test that `--start > --end` is rejected by argument parsing (use `CliRunner`-style or direct argparse test).
  - Test that `--interval 5m` is rejected by argument parsing.
- [x] 6.2 No integration test that requires xtquant / MiniQMT — it cannot run in CI.

## 7. Validation

- [x] 7.1 `uv run pytest -q --deselect tests/data/test_migrations.py::test_alembic_offline_sql_generation` passes (existing tests + new unit tests). 195 passed, 30 skipped, 1 deselected.
- [x] 7.2 `uv run ruff check src tests scripts` clean.
- [x] 7.3 `uv run ruff format --check src tests scripts` clean.
- [x] 7.4 `uv run mypy src scripts` (or whichever scope mypy uses) clean.
- [x] 7.5 `openspec validate --all --strict` clean. 13/13.
- [ ] 7.6 Manual smoke on a real xtquant / MiniQMT environment (deferred to operator): `uv run python scripts/fetch_historical_bars_xtquant.py --interval 1d --start 2024-01-01 --end 2024-01-05 --limit 5` should download 5 symbols × 5 trading days of 1d data, write to `kline_1d`, and report rows_processed=25 ± weekend adjustments.