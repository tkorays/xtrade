## MODIFIED Requirements

### Requirement: `DailyXtQuantCollector.run` per-batch fetch and write

The collector's run loop SHALL process the symbol universe in batches of `batch_size`. For each batch the collector SHALL call `source.fetch_bars_bulk(batch, start, end, interval)` **once**, then `kline_repo.upsert_bars(merged)` **once** with the merged long-format frame returned by the source.

`batch_size` defaults SHALL be:
- `--interval 1d`: `batch_size = len(instruments)` (the operator can override with `--batch-size`).
- `--interval 1m`: `batch_size = 50` (the operator can override with `--batch-size`).
- `--batch-size-max` defaults to `500`; any value above it SHALL be rejected by the CLI before any IO.

When `source.fetch_bars_bulk` raises (network error, MiniQMT rejection, OOM), the collector SHALL retry the same call **once**. If the retry also raises, the entire batch SHALL be recorded as `symbols_skipped` with the exception class + message; the run SHALL continue with the next batch.

When the bulk fetch succeeds but returns a wide frame containing some symbols with all-NaN rows for required fields, those symbols SHALL be recorded as `symbols_skipped` with `xtquant returned empty frame for this symbol`; the rest of the batch SHALL still be upserted.

#### Scenario: Routine run with no watermark, default 1d batch_size

- **WHEN** the operator runs `xtrade data sync --interval 1d` and `len(instruments) = 7000`
- **THEN** the collector invokes `fetch_bars_bulk(<all 7000>, ...)` exactly once, then `upsert_bars(<merged>)` once; the run produces one INFO progress line per batch (a single batch in this scenario), one `sync done` line, and exits `0`

#### Scenario: 1m run with default batch_size

- **WHEN** the operator runs `xtrade data sync --interval 1m` and `len(instruments) = 7000`
- **THEN** the collector splits the symbols into `ceil(7000 / 50) = 140` batches, invokes `fetch_bars_bulk(50 syms, ...)` once per batch, and `upsert_bars(<50-symbol merged frame>)` once per batch

#### Scenario: Bulk fetch raises; retry succeeds

- **WHEN** the first call to `source.fetch_bars_bulk(batch, ...)` raises `ConnectionError` and the second call returns a valid frame
- **THEN** the run proceeds normally: rows from the second call are upserted, `symbols_skipped` is empty for this batch, the slow-fetch WARN (if any) reports the cumulative fetch time

#### Scenario: Bulk fetch raises twice; entire batch skipped

- **WHEN** both calls to `source.fetch_bars_bulk(batch, ...)` raise the same exception class
- **THEN** every symbol in `batch` is appended to `symbols_skipped` with the exception class + message; the run continues to the next batch

#### Scenario: Bulk fetch returns frame with all-NaN rows for some symbols

- **WHEN** the bulk fetch returns a DataFrame containing `(symbol, time, open, ..., volume)` rows for symbols A and B, but A's rows have NaN for `open`/`high`/`low`/`close`
- **THEN** `A` is appended to `symbols_skipped` with `xtquant returned empty frame for this symbol`; `B`'s rows are upserted

#### Scenario: `--batch-size > --batch-size-max` is rejected

- **WHEN** the operator runs `xtrade data sync --interval 1d --batch-size 1000` and `--batch-size-max` defaults to `500`
- **THEN** the CLI exits non-zero with `--batch-size must be <= 500; got 1000` and no xtquant / DB call is made

### Requirement: Progress logging cadence

The collector's progress lines (the INFO line per batch and the per-batch DEBUG line under `--verbose`) SHALL be emitted exactly once per `fetch_bars_bulk + upsert_bars` pair. The DEBUG line under `--verbose` SHALL report the symbol list as `bulk-fetch: syms=<count> interval=<interval> first=<symbol> last=<symbol>`. Operators SHALL use `--verbose` to confirm which batch is in flight when wall-clock time is dominated by `fetch_bars_bulk`.

#### Scenario: `--verbose` on a 7000-symbol 1d run

- **WHEN** the operator runs `xtrade data sync --interval 1d --verbose` and `len(instruments) = 7000`
- **THEN** one DEBUG line `bulk-fetch: syms=7000 interval=1d first=000001.SZ last=603999.SH` is emitted, followed by the existing batch INFO line and the run-end INFO line; no per-symbol DEBUG lines fire (because the fetch is now batch-scoped)