## 1. Collector: per-symbol DEBUG line

- [x] 1.1 In `src/xtrade/data/collection/xtquant.py`, add the module-level constant `VERBOSE_SYMBOL_PROGRESS_FORMAT: str = "{symbol_index}/{symbols_total}  sym={symbol}  interval={interval}"` and append it to `__all__`.
- [x] 1.2 In `_fetch_and_write_batch`, accept one new keyword-only parameter `symbol_offset: int = 0` (the run-wide index of the first symbol in the batch, 0-based). Plumb it through the return type's metadata OR pass it alongside the batch.
- [x] 1.3 At the top of the per-symbol `for symbol in batch:` loop (immediately before `t0 = time.perf_counter()`), call `logger.debug(VERBOSE_SYMBOL_PROGRESS_FORMAT, symbol_index=symbol_offset + idx_in_batch + 1, symbols_total=..., symbol=symbol, interval=interval)`. The `symbols_total` is supplied by the caller via the new parameter on `_fetch_and_write_batch`.
- [x] 1.4 In `run`, compute `symbols_total = len(symbols)` once (already done — verify). For each batch, pass `symbol_offset=i` and `symbols_total=symbols_total` to `_fetch_and_write_batch`.
- [x] 1.5 Update `_fetch_and_write_batch`'s return type: include `symbol_offset` and `symbols_total` if needed for the DEBUG call (the simplest design: keep the existing 5-tuple return and pass the totals as parameters to the method itself).

## 2. CLI: `--verbose` flag

- [x] 2.1 In `src/xtrade/cli/data.py`, add `@click.option("--verbose", is_flag=True, default=False, help="Lower the collector logger to DEBUG; emit one line per symbol fetch.")` to `sync_cmd`.
- [x] 2.2 In `sync_cmd`, after the existing validations and BEFORE constructing the collector, capture `prior_level = logging.getLogger("xtrade.data.collection.xtquant").level` and (if `verbose`) call `logging.getLogger("xtrade.data.collection.xtquant").setLevel(logging.DEBUG)`. Wrap the rest of the function in `try/finally` that restores the prior level.

## 3. Tests

- [x] 3.1 In `tests/data/collection/test_daily_xtquant_collector.py`, add `test_debug_line_per_symbol`: enable `caplog` at DEBUG, run with 3 symbols, assert one DEBUG record per symbol containing `sym=...` and the 1-based run-wide index.
- [x] 3.2 Add `test_debug_line_suppressed_at_info_level`: enable `caplog` at INFO, run the same scenario, assert no DEBUG records are captured.
- [x] 3.3 Add `test_verbose_format_constant_contains_required_keys`: assert `VERBOSE_SYMBOL_PROGRESS_FORMAT` contains `"symbol"`, `"sym"`, and `"interval"` substrings.
- [x] 3.4 In `tests/cli/test_data_cli.py`, extend `test_data_sync_help` to assert `--verbose` is in the help output.

## 4. Validation

- [x] 4.1 `uv run ruff check src tests` clean.
- [x] 4.2 `uv run ruff format --check src tests` clean.
- [x] 4.3 `uv run mypy src` strict mode clean.
- [x] 4.4 `uv run pytest -q` passes (existing + new tests).
- [x] 4.5 `npx openspec validate --all --strict` clean.
- [ ] 4.6 Manual smoke test: `uv run xtrade data sync --interval 1d --verbose` on a small instrument subset emits one DEBUG line per symbol fetch on stderr; running without `--verbose` emits no per-symbol lines.