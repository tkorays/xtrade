## Why

`xtrade data sync` currently prints one batch-level INFO line per `batch_size` symbols. On a 7000-symbol market that is ~140 lines, but each batch internally issues one `source.fetch_bars(symbol, ...)` call per symbol (each of which makes two synchronous xtquant calls). On a sluggish MiniQMT the operator sees `sync start` followed by silence for many minutes — the gap between two consecutive batch lines is wide enough that an operator has no way to tell whether the run is alive, what symbol it is currently fetching, or whether a single `fetch_bars` call is the slow one.

`sync-progress-logging` added the per-batch cadence. This change adds a second cadence — one DEBUG line per symbol fetch — gated behind a new `--verbose` flag (default off) so non-verbose runs are not flooded. The symbol line is emitted **before** the `fetch_bars` call, which guarantees the operator sees activity even when the call itself is the slow stage.

## What Changes

- Add a `--verbose` flag to `xtrade data sync`. When set, the collector's logger level is lowered to `DEBUG` for `xtrade.data.collection.xtquant` for the duration of the run.
- Add a `logger.debug` call inside `_fetch_and_write_batch`'s per-symbol loop, immediately before the `fetch_bars` call. Format: `symbol=N/Total  sym=XXXXX.SZ  interval=1d`.
- `verbose=False` is the default; existing operators see no behavioural change beyond the verbose flag being absent.
- No change to `xtquant` source code, no change to `_resolve_window`, no change to `SyncReport`.
- New constant `VERBOSE_SYMBOL_PROGRESS_FORMAT: str` in `xtrade.data.collection.xtquant` so the format string lives with the collector (not in CLI).

## Capabilities

### New Capabilities
- (none)

### Modified Capabilities
- `data-collection-xtquant`: add a Requirement for the per-symbol DEBUG progress line.
- `xtrade-cli`: add `--verbose` to `xtrade data sync`; document the DEBUG-level behaviour.

## Impact

- Affected code:
  - `src/xtrade/data/collection/xtquant.py` — one `logger.debug` call inside the per-symbol loop; new module constant.
  - `src/xtrade/cli/data.py` — new `--verbose` flag; sets the logger level for `xtrade.data.collection.xtquant` (and its children) to `DEBUG` while `sync_cmd` runs, restoring on exit.
  - `tests/data/collection/test_daily_xtquant_collector.py` — one new test asserting DEBUG lines are emitted (via `caplog`).
  - `tests/cli/test_data_cli.py` — assert `--verbose` is in the help text.
- API: `SyncReport` unchanged; constructor signature unchanged; new `verbose` parameter is read from the CLI only.
- Backwards compatible: `--verbose` defaults to False; default log level is unchanged.