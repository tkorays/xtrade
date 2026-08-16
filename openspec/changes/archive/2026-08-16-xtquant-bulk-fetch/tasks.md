## 1. Source layer: bulk fetch

- [x] 1.1 In `src/xtrade/data/sources/xtquant.py`, add `XtQuantDataSource.fetch_bars_bulk(self, symbols: list[str], start: date, end: date, interval: str) -> pd.DataFrame`. Implementation:
  1. Call `xtdata.download_history_data2(symbol_list=symbols, period=interval, start_time=start.strftime("%Y%m%d"), end_time=end.strftime("%Y%m%d"))` once.
  2. Call `xtdata.get_market_data_ex(field_list=[], stock_list=symbols, period=interval, start_time=..., end_time=..., ...)`. Use the same field list the existing `fetch_bars` uses.
  3. Pivot the wide frame to long format with columns `[time, symbol, open, high, low, close, volume, amount]`. Reuse `merge_bars` to apply timezone + column normalisation.
  4. Drop rows where all of `open/high/low/close` are NaN.
  5. Return the long-format DataFrame.
- [x] 1.2 Refactor the existing `fetch_bars(symbol, start, end, interval)` to delegate to `fetch_bars_bulk([symbol], start, end, interval)` and return the rows for `symbol` (or an empty frame if absent). The signature, docstring, and behaviour for `scripts/fetch_historical_bars_xtquant.py` must be preserved.
- [x] 1.3 In `src/xtrade/data/sources/base.py`, add `fetch_bars_bulk` to the `DataSource` Protocol. Keep `fetch_bars` in the Protocol for backward compatibility.
- [x] 1.4 In `src/xtrade/data/sources/mock.py`, add `MockDataSource.fetch_bars_bulk(symbols, start, end, interval)` that loops over the existing per-symbol fixture (`bars` dict), concats the frames, and returns the long-format aggregate.

## 2. Collector: bulk-driven batch loop

- [x] 2.1 In `src/xtrade/data/collection/xtquant.py`, refactor `_fetch_and_write_batch` to call `self._source.fetch_bars_bulk(batch, start, end, interval)` once. Replace the per-symbol `try/except` loop with a whole-batch `try/except` that retries once; on second failure, raises a custom exception carrying the original cause so the caller can mark the entire batch as skipped.
- [x] 2.2 After a successful bulk fetch, walk the returned long-format frame and split out symbols whose all-`open/high/low/close` rows are NaN — record them in `symbols_skipped` with reason `xtquant returned empty frame for this symbol`. Upsert the rest.
- [x] 2.3 Update the return type to keep the same 5-tuple shape `(rows_written, skipped_symbols, latest_trade_date, fetch_seconds, upsert_seconds)`. Time the bulk fetch as a whole (sum of the one `download_history_data2` + one `get_market_data_ex` call) for the slow-fetch WARN.
- [x] 2.4 In `run`, replace the per-symbol DEBUG line with a single per-batch DEBUG line under `--verbose`. Format: `VERBOSE_BULK_PROGRESS_FORMAT = "{symbol_index}/{symbols_total}  bulk-fetch: syms={batch_size} interval={interval} first={first_symbol} last={last_symbol}"`. Emit only when `symbols_total > 0`. Append `VERBOSE_BULK_PROGRESS_FORMAT` to `__all__`.
- [x] 2.5 Keep `_slow_fetch_seconds` and `_slow_upsert_seconds` semantics; they now apply to the bulk fetch + bulk upsert rather than per-symbol.

## 3. CLI: `--batch-size-max` and per-interval defaults

- [x] 3.1 In `src/xtrade/cli/data.py`, add `--batch-size-max` (default `500`) to `sync_cmd`. Reject values above it before any IO.
- [x] 3.2 Compute the default `--batch-size` from `--interval` and `len(instruments)`:
  - `1d` → `len(instruments)` (whole market).
  - `1m` → `50`.
  - Other intervals → error.
  Resolve `len(instruments)` via a single `InstrumentRepository.list_all()` call (already used elsewhere).
- [x] 3.3 Update `run-start` INFO line to show `batch_size=<resolved>` so the operator sees the resolved value.

## 4. Tests

- [x] 4.1 In `tests/data/sources/test_xtquant_source.py`, add tests for `fetch_bars_bulk`:
  - Happy path: returns long-format frame with `symbol` column; missing symbols omitted.
  - One-symbol missing: present in `symbols` list, absent in result.
  - Timezone normalisation applied (existing helper from `merge_bars` test).
  - `fetch_bars` wrapper still returns the single-symbol slice.
- [x] 4.2 In `tests/data/sources/test_mock_source.py` (or wherever the mock lives), assert `MockDataSource.fetch_bars_bulk` aggregates per-symbol fixtures and adds the `symbol` column.
- [x] 4.3 In `tests/data/collection/test_daily_xtquant_collector.py`:
  - Update `FakeSource` to implement `fetch_bars_bulk(symbols, ...)`; keep `fetch_bars` for the wrapper test.
  - Add `test_bulk_fetch_per_batch` (1 batch of N symbols → 1 fetch_bars_bulk call, 1 upsert call).
  - Add `test_bulk_fetch_failure_retries_once` (first call raises, second succeeds → row count from second call).
  - Add `test_bulk_fetch_failure_twice_skips_entire_batch` (both calls raise → all symbols in `symbols_skipped`).
  - Add `test_bulk_fetch_partial_empty_symbols_recorded_as_skipped` (one symbol returns all-NaN rows → recorded, rest upserted).
  - Update the existing per-symbol tests (`test_debug_line_per_symbol` etc.) to assert the new bulk-format DEBUG line.
- [x] 4.4 In `tests/cli/test_data_cli.py`, add tests for:
  - Default `--batch-size` is `len(instruments)` for `1d`.
  - Default `--batch-size` is `50` for `1m`.
  - `--batch-size 1000 --batch-size-max 500` rejected with the expected error.

## 5. Validation

- [x] 5.1 `uv run ruff check src tests` clean.
- [x] 5.2 `uv run ruff format --check src tests` clean.
- [x] 5.3 `uv run mypy src` strict mode clean.
- [x] 5.4 `uv run pytest -q` passes (existing + new tests).
- [x] 5.5 `npx openspec validate --all --strict` clean.
- [ ] 5.6 Manual smoke test on real MiniQMT:
  - `uv run xtrade data sync --interval 1d` against a small instrument subset (≤ 50 symbols) — wall-clock under 10 seconds; one DEBUG line under `--verbose`.
  - `uv run xtrade data sync --interval 1m --batch-size 100` — bulk fetch issues one MiniQMT call per 100 symbols; per-batch INFO line shows `batch_size=100`.
  - `uv run xtrade data sync --interval 1d --batch-size 1000` (exceeds default `--batch-size-max=500`) — rejected by CLI.