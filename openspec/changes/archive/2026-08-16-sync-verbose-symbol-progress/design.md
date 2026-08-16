## Context

`sync-progress-logging` shipped per-batch INFO lines (one every `batch_size=50` symbols). On a 7000-symbol market that is ~140 lines. Between two consecutive batch lines there can be many minutes of wall-clock time — each batch loops over `batch_size` symbols and calls `source.fetch_bars` for each, which inside `XtQuantDataSource` makes two synchronous xtquant calls (`download_history_data2` + `get_local_data`). The first call in particular can stall on MiniQMT or the network. Operators running `xtrade data sync --interval 1d` see `sync start` and then silence until the first batch completes — minutes can pass with no log line.

The fix is to add a second cadence: one DEBUG line per symbol, emitted **before** the `fetch_bars` call (so the line is visible even when the call itself is the slow stage). DEBUG is gated behind a new `--verbose` flag on `xtrade data sync` because emitting 7000 lines on every run is noise; operators opt in when they're debugging a stuck run.

## Goals / Non-Goals

**Goals:**
- `--verbose` lowers the collector logger to DEBUG; per-symbol DEBUG lines appear on stderr.
- A first-class module-level format string `VERBOSE_SYMBOL_PROGRESS_FORMAT` so the format is testable and discoverable.
- Default behaviour (no `--verbose`) is unchanged.
- Logger level is restored on CLI exit so subsequent commands aren't affected.

**Non-Goals:**
- Replacing the per-batch INFO cadence (it's still useful at-a-glance).
- Changing `XtQuantDataSource.fetch_bars` to skip `download_history_data2` (out of scope; the operator explicitly declined that change).
- Adding a progress bar / TUI element.
- Threading the verbose flag into `DailyXtQuantCollector.__init__` — the CLI sets the logger level directly; no constructor churn.

## Decisions

### Decision 1: Use `logger.debug`, gated by `--verbose`

A `logger.debug` line emitted before each `fetch_bars` call is the simplest, most idiomatic change. Operators running with the default `INFO` level see no per-symbol noise. Operators running with `--verbose` (which lowers the logger to `DEBUG`) see one line per symbol. The DEBUG line goes through the same `logging.basicConfig` handler as the existing INFO lines.

**Alternative considered**: add a `verbose: bool` constructor parameter and an `if self._verbose:` guard inside the loop. Rejected because threading a flag through the constructor for a single `logger.debug` call adds API surface for no benefit — `logger.debug` is already gated by the active level.

### Decision 2: Emit the line **before** the call, not after

The DEBUG line is the operator's "is this thing alive?" signal. If we emit it after the call, a single stalled `fetch_bars` call still produces silence. Emitting before means the line appears even when the call itself blocks — the operator sees the symbol name that is currently in flight, which is exactly what they need to diagnose a stall.

### Decision 3: Format string is a module constant

`VERBOSE_SYMBOL_PROGRESS_FORMAT = "{symbol_index}/{symbols_total}  sym={symbol}  interval={interval}"`. Tests assert the constant contains the substrings `"symbol"`, `"sym"`, and `"interval"` to lock down the contract. Production code uses `logger.debug(format_string, ...)` so the message is lazily rendered.

### Decision 4: `--verbose` mutates logger level for the duration of `sync_cmd`

CLI captures the prior level, sets `logging.getLogger("xtrade.data.collection.xtquant").setLevel(logging.DEBUG)` once at the top of `sync_cmd` when `--verbose` is set, and restores the prior level in a `try/finally` so a `KeyboardInterrupt` or unexpected exception doesn't leak the level change. We don't mutate the root logger — only the collector's logger (and its children).

### Decision 5: `symbol_index` is the run-wide 1-based index, not the batch index

The operator needs to know "how far through the whole run am I?" The DEBUG line therefore shows `<symbol_index>/<symbols_total>` where `symbol_index = i + offset_in_batch + 1`. The batch INFO already shows `symbols_done=N/total`; the DEBUG line is consistent with that running counter.

**Alternative considered**: `<symbol_index>/<batch_size>`. Rejected — tells the operator only where they are in the current batch, useless for overall progress.

## Risks / Trade-offs

- **[Risk] 7000 DEBUG lines flush slowly on Windows** → Standard library `logging` with `StreamHandler(sys.stderr)` flushes line-by-line. On a fast run this can be ~7000 lines / few minutes, well within operator tolerance. Operators who want compact output omit `--verbose`.
- **[Risk] Operator forgets `--verbose` and still sees silence on a stall** → The CLI help text and the design explicitly call this out: "use `--verbose` when debugging a stuck run". The batch INFO cadence is still the default; per-symbol DEBUG is opt-in.
- **[Risk] Logger level leak if `sync_cmd` raises before the `try/finally`** → The `setLevel` call happens **inside** the `try` block, so the `finally` always restores the prior level, even on exception.

## Migration Plan

Non-migrating. Operators just see a new `--verbose` flag in the help text. No DB / config / env-var change. No change to existing log output.

## Open Questions

None.