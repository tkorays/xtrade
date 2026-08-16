## ADDED Requirements

### Requirement: Progress and slow-call logging

The `DailyXtQuantCollector` SHALL emit `logging` records at `INFO` level for every observable phase transition so operators can confirm the run is alive and locate the slow stage. The collector's `__init__` SHALL accept two new keyword-only parameters:
- `slow_fetch_seconds: float` — threshold above which a single `source.fetch_bars` call is reported as slow. Default `30.0`.
- `slow_upsert_seconds: float` — threshold above which a single `kline_repo.upsert_bars` call is reported as slow. Default `60.0`.

#### Scenario: Run-start INFO line

- **WHEN** `DailyXtQuantCollector.run(interval="1d")` is called and proceeds past input validation
- **THEN** a single `INFO` record is emitted containing: `interval`, `mode` (`"ad-hoc"` if `start_date is not None`, else `"routine"`), window `[start, end]`, `symbols_total`, `batch_size`, and `batches_total`

#### Scenario: Per-batch progress INFO line

- **WHEN** `DailyXtQuantCollector.run` finishes processing a batch (one iteration of the `for i in range(0, len(symbols), batch_size)` loop)
- **THEN** one `INFO` record is emitted containing: `batch_index` (1-based), `batches_total`, `symbols_done`, `symbols_total`, `rows_written` (cumulative), `symbols_skipped` (cumulative), `batch_fetch_seconds`, `batch_upsert_seconds` (only when the batch wrote at least one row), `elapsed_seconds`, and a formatted `eta_seconds` derived from `elapsed / symbols_done * (symbols_total - symbols_done)` (or `"--"` when no symbols are done yet)

#### Scenario: Slow `fetch_bars` emits a WARNING

- **WHEN** a single `source.fetch_bars(symbol, ...)` call takes longer than `slow_fetch_seconds`
- **THEN** one `WARNING` record is emitted containing the offending `symbol` and the measured `elapsed_seconds`; the run is NOT aborted

#### Scenario: Slow `upsert_bars` emits a WARNING

- **WHEN** a single `kline_repo.upsert_bars(df)` call takes longer than `slow_upsert_seconds` and `df` is non-empty
- **THEN** one `WARNING` record is emitted containing the batch index and the measured `elapsed_seconds`; the run is NOT aborted

#### Scenario: Run-end INFO line (one record)

- **WHEN** `DailyXtQuantCollector.run` returns (success, partial, or failed)
- **THEN** one `INFO` record is emitted with `status`, `rows_written`, `symbols_skipped`, `last_trade_date`, `elapsed_seconds`, and `mode` (`"ad-hoc"` or `"routine"`)

#### Scenario: Dry-run path still logs only the planned-window line

- **WHEN** `DailyXtQuantCollector.run(interval="1d", dry_run=True)` is called
- **THEN** the existing dry-run `INFO` record is emitted and NO per-batch / run-end records are emitted (because no batches run)

#### Scenario: Threshold parameters are injectable for tests

- **WHEN** the caller constructs the collector with `slow_fetch_seconds=0.0` and `slow_upsert_seconds=0.0`
- **THEN** every `fetch_bars` and `upsert_bars` call emits a `WARNING` (because every non-zero duration exceeds the threshold), making the slow-call path testable without `time.sleep`