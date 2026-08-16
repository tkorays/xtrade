## Why

`xtrade` now has a populated `kline_1d` / `kline_1m` Postgres store, but there is no recurring path that keeps it fresh — every new trading day the operator must re-invoke the one-shot script `fetch_historical_bars_xtquant.py` by hand. We want a first-class daily ingestion capability so that running `xtrade data sync` (or a scheduler-driven invocation) on each trading day pulls the latest bars from the local MiniQMT `xtquant` client and writes them through the existing `PostgresKLineRepository.upsert_bars`. This closes the loop between the data layer and the source-of-truth MiniQMT cache, and is the prerequisite for any further live-trading features that depend on a current bar set.

## What Changes

- Add a new `xtquant` `DataSource` implementation, `XtQuantDataSource`, registered under the name `"xtquant"` in `SourceRegistry`. It implements the four `fetch_*` methods by calling `xtdata.download_history_data2` + `xtdata.get_local_data` and normalising timestamps to the schema the repositories expect (ms-UTC → Asia/Shanghai, then `date` for `1d` / tz-aware datetime for `1m`, dropping `pre_close`).
- Add a new capability `data-collection-xtquant` that exposes a reusable `DailyXtQuantCollector` service: given an `interval` (`"1d"` or `"1m"`) and a `DataSource`, it iterates `instrument.symbol`, pumps bars through `KLineRepository.upsert_bars`, and updates a watermark in a new `data_sync_state` table.
- Add a new table `data_sync_state` (`source`, `interval`, `last_trade_date`, `last_run_at`, `rows_written`, `status`, `error`) created by a new Alembic migration under the existing `data-migrations` capability. The collector reads the watermark before a run and advances it after a successful run.
- Add a new CLI subcommand group `xtrade data` under the existing Click group in `xtrade.cli.xtrade`. Subcommands:
  - `xtrade data sync --interval {1d,1m} [--batch-size N] [--dry-run]` — run one collection cycle for the given interval using the registered `xtquant` source.
  - `xtrade data status` — print the current watermark per `(source, interval)` from `data_sync_state`.
  - `xtrade data reset --interval {1d,1m}` — delete the watermark row so the next `sync` re-pulls from the configured `lookback_days`.
- Reuse the merge / transform logic that lives in `scripts/fetch_historical_bars_xtquant.py`: extract the pure helpers (`merge_xtquant_bars`, the xtdata-ms → tz conversion) into a small `xtrade.data.sources.xtquant` module so both the existing one-shot script and the new collector share the same normalisation. The existing one-shot script is updated to import from the shared module (no behaviour change).
- Add tests for the new module: a `FakeXtQuantClient` test double so the collector is unit-testable without MiniQMT.

**BREAKING**: anyone using `scripts/fetch_historical_bars_xtquant.py` after this change must run `alembic upgrade head` once to create `data_sync_state` (the script itself does not require it — it is the new CLI that uses it).

## Capabilities

### New Capabilities

- `data-collection-xtquant`: the new end-to-end capability "pull market data from xtquant on a schedule, persist it through the data layer, and track per-interval watermarks". Covers the `XtQuantDataSource`, the `DailyXtQuantCollector`, the CLI subcommand group, and the watermark semantics. Spec: `specs/data-collection-xtquant/spec.md`.

### Modified Capabilities

- `data-sources`: a new `DataSource` implementation named `XtQuantDataSource` is registered by default alongside the existing `InMemoryMockSource` under the name `"xtquant"`. This adds an entry to the default registry; it does not change the `DataSource` Protocol itself.
- `data-migrations`: a new Alembic revision `0002_data_sync_state.py` creates the `data_sync_state` table described in `data-collection-xtquant`. The migration is reversible.
- `xtrade-cli`: a new subcommand group `xtrade data` is added under the existing Click group. The new subcommands are listed in `xtrade-cli/spec.md`.

## Impact

- Affected code:
  - `src/xtrade/data/sources/xtquant.py` — new `XtQuantDataSource` + shared merge helper extracted from the one-shot script.
  - `src/xtrade/data/sources/__init__.py` — register `"xtquant"` on first `SourceRegistry` construction.
  - `src/xtrade/data/sources/base.py` — extend `SourceRegistry._init` to lazy-register `xtquant` only when the optional `xtquant` package is importable (so the rest of the project does not gain a hard dependency).
  - `src/xtrade/data/collection/__init__.py`, `src/xtrade/data/collection/xtquant.py` — new `DailyXtQuantCollector` service.
  - `src/xtrade/data/orm/sync_state.py` — new SQLAlchemy ORM model for `data_sync_state`.
  - `src/xtrade/data/migrations/versions/0002_data_sync_state.py` — new Alembic migration.
  - `src/xtrade/cli/data.py` — new Click subcommand group `xtrade data sync | status | reset`.
  - `src/xtrade/cli/xtrade.py` — register the new sub-group.
  - `scripts/fetch_historical_bars_xtquant.py` — import merge helper from the new module; behaviour unchanged.
  - `tests/data/sources/test_xtquant_source.py`, `tests/data/collection/test_daily_xtquant_collector.py`, `tests/cli/test_data_cli.py` — unit tests.
- Affected APIs: a new public class `XtQuantDataSource`, a new public class `DailyXtQuantCollector`, a new Click group `xtrade data`. No changes to existing public APIs.
- Affected dependencies: `xtquant` remains optional (`[project.optional-dependencies.xtquant]`). The `SourceRegistry` lazy-registers `xtquant` only when importable, so the project imports cleanly without xtquant installed.
- Affected systems: requires a running MiniQMT client at `sync` time; this is the same prerequisite as the one-shot script and is documented in the CLI help.
- Out of scope:
  - Real-time / tick-level ingestion (订阅 / 推送). This change covers historical daily batches only.
  - Pulling adjustment factors (`adjustment_factor` table) — a future change may cover that with a parallel collector.
  - Multi-process or distributed scheduling. The CLI is invoked once per cycle; orchestration (cron / Task Scheduler / systemd timer) is the operator's responsibility.
  - Instrument filtering by `list_date` / `delist_date`. Per user decision, the full `instrument` table is the source-of-truth.
  - Pre-applied adjustment (复权). xtquant is called with `dividend_type="none"` (raw prices); backward / forward adjustment is still applied on the read path in `KLineRepository.get_bars`.