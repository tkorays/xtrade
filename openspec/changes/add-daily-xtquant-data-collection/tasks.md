## 1. Shared xtquant source module

- [x] 1.1 Create `src/xtrade/data/sources/xtquant.py` with the pure helpers extracted from `scripts/fetch_historical_bars_xtquant.py`: `merge_bars(ret: dict[str, pd.DataFrame | None], interval: str) -> pd.DataFrame` and the `ms_utc_to_beijing(...)` conversion utility. Drop `pre_close`, normalise timestamps per interval, return the column order `[symbol, time, interval, open, high, low, close, volume, amount]`.
- [x] 1.2 In the same module, define `class XtQuantDataSource` implementing the `DataSource` Protocol. `fetch_bars` lazy-imports `xtquant.xtdata`, calls `download_history_data2` + `get_local_data(dividend_type="none")`, and returns the per-symbol merged frame. `fetch_instruments` returns `[]`. `fetch_adjust_factors` and `fetch_trade_calendar` return empty DataFrames.
- [x] 1.3 Update `scripts/fetch_historical_bars_xtquant.py` to import `merge_bars` and `BEIJING_TZ` / `SUPPORTED_INTERVALS` from `xtrade.data.sources.xtquant` instead of defining them locally. Behaviour unchanged.
- [x] 1.4 Update `xtrade.data.sources.base.SourceRegistry._init` to lazy-register `"xtquant"`: try `from xtrade.data.sources.xtquant import XtQuantDataSource` inside a `try / except ModuleNotFoundError`; on success call `self._sources["xtquant"] = XtQuantDataSource()`; on failure skip silently.

## 2. `data_sync_state` ORM model + migration

- [x] 2.1 Create `src/xtrade/data/orm/sync_state.py` with the `DataSyncState` SQLAlchemy 2.x model: `source VARCHAR NOT NULL`, `interval VARCHAR NOT NULL`, `last_trade_date DATE NULL`, `last_run_at TIMESTAMPTZ NOT NULL`, `rows_written BIGINT NOT NULL DEFAULT 0`, `status VARCHAR NOT NULL`, `error TEXT NULL`, `PRIMARY KEY (source, interval)`. Use `mapped_column` / `Mapped` style consistent with existing broker ORM models.
- [x] 2.2 Import `DataSyncState` from `xtrade.data.orm.sync_state` in `xtrade.data.orm_base.Base.metadata` so `alembic` discovers it. Verify `Base.metadata.tables` contains `data_sync_state` after import.
- [x] 2.3 Create `src/xtrade/data/migrations/versions/0002_data_sync_state.py`: `op.create_table("data_sync_state", ...)` matching the model, with `op.create_primary_key` for `(source, interval)`. No inserts. Provide a `downgrade()` that drops the table.
- [x] 2.4 Create `src/xtrade/data/sync_state/__init__.py` exposing a `DataSyncStateRepository` Protocol with `get(source, interval) -> DataSyncState | None`, `upsert(record: DataSyncState) -> None`, `delete(source, interval) -> bool`. Plus a `PostgresDataSyncStateRepository` ORM-backed implementation that uses `data.engine.get_session()`.
- [x] 2.5 Re-export `DataSyncStateRepository` and `DataSyncState` from `xtrade.data` (`__init__.py`) for downstream callers.

## 3. `DailyXtQuantCollector` service

- [x] 3.1 Create `src/xtrade/data/collection/__init__.py` (empty) and `src/xtrade/data/collection/xtquant.py`.
- [x] 3.2 In `xtquant.py`, define `dataclass(frozen=True) SyncReport(rows_written: int, symbols_skipped: list[tuple[str, str]], elapsed_seconds: float, last_trade_date: date | None, dry_run: bool)`.
- [x] 3.3 In `xtquant.py`, define `class DailyXtQuantCollector(source, instrument_repo, kline_repo, sync_state_repo, trade_calendar, clock=datetime.now)`. The constructor SHALL NOT do any IO (no xtquant import, no DB call). All `xtquant` import happens inside `run`.
- [x] 3.4 Implement `run(interval: str, *, batch_size: int = 50, lookback_days: int = 5, dry_run: bool = False) -> SyncReport`. Resolve window via the watermark logic in `design.md` Decision 4 (use `trade_calendar` to clamp `start` to the first trading day on or after `today - lookback_days`). Iterate `instrument_repo.list_symbols()` in chunks; for each chunk call `source.fetch_bars` per-symbol, concatenate into one DataFrame, call `kline_repo.upsert_bars(df)`. Catch per-symbol errors into `symbols_skipped`. After the loop, update the watermark row.
- [x] 3.5 Validate inputs up front: reject `interval not in {"1d", "1m"}` and `lookback_days > 30` with `ValueError` before any IO. Reject `lookback_days < 1` similarly.

## 4. CLI: `xtrade data` subcommand group

- [x] 4.1 Create `src/xtrade/cli/data.py` with the Click subcommand group `data` (`@click.group(name="data")`).
- [x] 4.2 Add `@data.command("sync")` accepting `--interval {1d,1m}` (required), `--batch-size N` (default `50`), `--lookback-days N` (default `5`), `--dry-run` (default `False`). Wire it to `DailyXtQuantumCollector.run`; print a one-line summary on success; exit `0` on full or partial success, `1` on unrecoverable error. Reject `interval` and `lookback_days > 30` before any IO.
- [x] 4.3 Add `@data.command("status")` that reads `data_sync_state` and prints one line per row in the format `xtquant.1d  last_trade_date=YYYY-MM-DD  rows=N  status=ok  last_run_at=YYYY-MM-DDTHH:MM:SSZ`. Exit `0` always.
- [x] 4.4 Add `@data.command("reset")` accepting `--interval {1d,1m}` (required). Delete the `(xtquant, interval)` row via `DataSyncStateRepository.delete`. Print a confirmation. Exit `0` if deleted, `1` if the row did not exist.
- [x] 4.5 Register the new `data` group in `src/xtrade/cli/xtrade.py` (`cli.add_command(data)`).
- [x] 4.6 Configure `logging.basicConfig(level=INFO, format=..., stream=sys.stderr)` at the top of the `sync` callback so operators see the collector's progress without needing to wire up logging externally.

## 5. Tests

- [x] 5.1 `tests/data/sources/test_xtquant_source.py`:
  - Test `merge_bars` with a 2-symbol dict of synthetic DataFrames; assert schema and that `pre_close` is dropped.
  - Test that an empty dict returns an empty DataFrame.
  - Test that `None` values are skipped silently.
  - Test that for `1d`, the `time` column is `datetime.date`.
  - Test that for `1m`, the `time` column is tz-aware `Timestamp`.
  - Test the off-by-one-day fix: `time=1704038400000` (UTC midnight) → `date(2024, 1, 1)`, NOT `date(2023, 12, 31)`.
  - Test that `XtQuantDataSource.fetch_bars` raises `ValueError` for `interval="5m"` before any xtquant call.
- [x] 5.2 `tests/data/test_sync_state_repository.py`:
  - Use the existing test fixture for the Postgres engine (`tests/data/conftest.py` if present, or create one using `data.engine.create_engine` against a temporary schema).
  - Test that `upsert` then `get` round-trips a `DataSyncState` record.
  - Test that `delete` returns `True` when the row exists and `False` when it does not.
  - Test the `(source, interval)` primary key: inserting a second row with the same key replaces the first.
- [x] 5.3 `tests/data/collection/test_daily_xtquant_collector.py`:
  - Build a `FakeXtQuantSource` returning canned per-symbol frames and a fake clock; build fake repositories that record calls.
  - Test that `run(interval="1d")` with no existing watermark starts from `today - lookback_days` (5) and processes all symbols.
  - Test that `run(interval="1d")` with an existing watermark starts from `last_trade_date - lookback_days` (5).
  - Test that one failing symbol in a batch does not abort the run and ends up in `symbols_skipped`.
  - Test that an entirely-empty run marks the watermark `status="failed"`.
  - Test that `lookback_days > 30` raises `ValueError` before any IO.
  - Test that `dry_run=True` makes no calls to the repositories.
  - Test that `interval="5m"` raises `ValueError` before any IO.
- [x] 5.4 `tests/cli/test_data_cli.py`:
  - Use `click.testing.CliRunner` to invoke `data --help`, `data sync --help`, `data status --help`, `data reset --help`; assert exit `0` and that the new flags appear in the help output.
  - Test `data sync --interval 5m` exits non-zero with a clear error.
  - Test `data sync --lookback-days 365` exits non-zero with a clear error.
  - Test `data reset --interval 5m` exits non-zero.
  - Use `monkeypatch` to replace `DailyXtQuantumCollector` and the repos with fakes so `data sync` and `data reset` are tested without a real DB.
- [x] 5.5 Update `tests/scripts/test_fetch_historical_bars_xtquant.py` (if present) to verify that the script still imports cleanly from the new module — no behaviour change is expected, but the existing tests should still pass.

## 6. Validation

- [x] 6.1 `uv run pytest -q` passes (existing tests + new unit tests).
- [x] 6.2 `uv run ruff check src tests scripts` clean.
- [x] 6.3 `uv run ruff format --check src tests scripts` clean.
- [x] 6.4 `uv run mypy src` strict mode clean.
- [x] 6.5 `openspec validate --all --strict` clean (the new spec files pass validation).
- [ ] 6.6 Manual smoke test on a real MiniQMT environment (operator-driven, not CI): `uv run xtrade data sync --interval 1d --lookback-days 5 --dry-run` prints the planned window without writing; `xtrade data sync --interval 1d` writes rows and advances the watermark; `xtrade data status` shows the updated watermark.