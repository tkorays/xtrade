## Why

`XtQuantDataSource.fetch_bars` currently calls `xtquant.xtdata.download_history_data2` once per symbol. On a 7000-symbol market, that means **7000 sequential network round-trips** per `xtrade data sync`. Operators observed an average of ~100ms per symbol (one batch showed `fetch=5.29s` for 50 symbols), but individual symbols stall for tens of seconds when MiniQMT is slow or the network is congested. The progress logging shipped in `sync-progress-logging` and `sync-verbose-symbol-progress` reduces operator confusion but does not address the underlying cost: a routine `1d` sync takes ~12 minutes wall-clock, almost entirely spent in serial `download_history_data2` calls.

`xtquant` exposes bulk variants — `download_history_data2(symbol_list, ...)` accepts a list of symbols in one call, and `get_market_data_ex(field_list, stock_list, ...)` returns a wide DataFrame covering all symbols. Bulk fetches reuse MiniQMT's internal connection pool and amortise per-call setup; they are documented as the recommended pattern for full-market pulls.

## What Changes

- `XtQuantDataSource` gains a new method `fetch_bars_bulk(symbols, start, end, interval)` that:
  1. Calls `download_history_data2(symbols, period=..., start_time=..., end_time=...)` once for the whole batch.
  2. Calls `get_market_data_ex(field_list=[...], stock_list=symbols, period=..., start_time=..., end_time=...)` once.
  3. Splits the wide DataFrame back into per-symbol frames (or returns a single wide frame — TBD during design).
- The existing single-symbol `fetch_bars(symbol, ...)` is **kept** for the `scripts/fetch_historical_bars_xtquant.py` smoke test path. It is now a thin wrapper that calls `fetch_bars_bulk([symbol], ...)` and slices out the one column.
- `DataSource` Protocol gains the `fetch_bars_bulk` method. Existing in-tree sources (`MockDataSource`) gain a matching implementation.
- `DailyXtQuantCollector._fetch_and_write_batch` switches from per-symbol `fetch_bars` to a single `fetch_bars_bulk` call followed by a single `upsert_bars` call. Per-symbol exception handling is replaced by per-batch exception handling: a whole-batch failure (network error, MiniQMT rejection) is retried once; per-symbol failures (a symbol present in the wide frame with all-NaN rows) are recorded as `symbols_skipped` and the rest of the batch is preserved.
- `batch_size` keeps its current CLI semantics (`symbols per xtquant call`) but with new defaults:
  - `--interval 1d`: default `batch_size = len(instruments)` (effectively one batch for the whole market, since 1d memory cost is ~200MB for 7000 symbols × 10y history).
  - `--interval 1m`: default `batch_size = 50` (memory cost is dominant; 50 keeps the wide frame under ~10MB).
  - New CLI flag `--batch-size-max` sets the upper bound; values exceeding it are rejected. Default `500`.
- Progress logging stays per-batch (one INFO line per batch, batch size now reflects the new unit). `--verbose` continues to log per-symbol DEBUG before each call — the call is now per-batch, but we log the symbol list instead, e.g. `bulk-fetch 7000 syms (000001.SZ..603999.SH) interval=1d`.
- Failure handling: if `download_history_data2` or `get_market_data_ex` raises (network error, broker drop, OOM), retry the whole batch once; on second failure, mark **the entire batch** as `symbols_skipped` with the exception class + message; continue to next batch. If the wide frame contains symbols with all-NaN rows for a field, those symbols are reported as skipped with `xtquant returned empty frame`.

## Capabilities

### Modified Capabilities
- `data-sources`: add bulk-fetch requirement to the `DataSource` Protocol; spec the `fetch_bars_bulk` contract.
- `data-collection-xtquant`: collector uses bulk fetch; document new defaults and retry semantics.

### New Capabilities
- (none)

## Impact

- Affected code:
  - `src/xtrade/data/sources/xtquant.py` — new `fetch_bars_bulk` method; refactor of `fetch_bars`.
  - `src/xtrade/data/sources/base.py` — `DataSource` Protocol adds `fetch_bars_bulk`; `MockDataSource` gains a stub.
  - `src/xtrade/data/sources/mock.py` — add `fetch_bars_bulk` that loops over the existing per-symbol fixture.
  - `src/xtrade/data/collection/xtquant.py` — `_fetch_and_write_batch` switches to bulk; per-batch exception handling; per-batch timing.
  - `src/xtrade/cli/data.py` — `batch_size` default varies by `--interval`; new `--batch-size-max` flag with default 500.
  - `tests/data/sources/test_xtquant_source.py` — new tests for `fetch_bars_bulk` happy path, empty results, error mapping.
  - `tests/data/sources/test_mock_source.py` — assert `fetch_bars_bulk` exists and returns equivalent rows.
  - `tests/data/collection/test_daily_xtquant_collector.py` — update existing tests to stub `fetch_bars_bulk`; add bulk-failure tests (one-batch retry, all-batch failure).
  - `tests/cli/test_data_cli.py` — add tests for `--batch-size-max` and per-interval defaults.
- API: `DataSource.fetch_bars` becomes optional (kept for one-off scripts); new required `fetch_bars_bulk`. Collector code adapts in lockstep.
- Backwards compatible: `scripts/fetch_historical_bars_xtquant.py` still works via the wrapper. CLI default `batch_size=50` is preserved for `1m`; only `1d` changes default to "all symbols".
- Performance: ~50× fewer MiniQMT calls per `1d` sync; expected wall-clock drop from ~12 min to ~15 sec on a 7000-symbol market (excluding the actual download time).