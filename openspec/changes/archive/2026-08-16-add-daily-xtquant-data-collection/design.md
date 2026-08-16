## Context

The data layer (`xtrade.data`) already has the producer-side abstraction in place: `DataSource` Protocol + `SourceRegistry` (default `mock`), repositories for K-lines / instruments / adjustment factors / trade calendar, and a one-shot script `scripts/fetch_historical_bars_xtquant.py` that pulls historical bars from `xtquant` and writes them via `PostgresKLineRepository.upsert_bars`. The Alembic migration `0001_initial.py` creates `kline_1d`, `kline_1m` (TimescaleDB hypertable), `instrument`, `adjustment_factor`, `trade_calendar`, plus the broker tables.

`xtquant` is the local MiniQMT Python client. It is single-process and not on PyPI (installed from the user's QMT distribution). It exposes `xtdata.download_history_data2` (cache-fill) and `xtdata.get_local_data` (read-back), with results keyed by symbol. Timestamps come back as millisecond UTC, but the value represents **Beijing 00:00** of the requested day — so a naive `pd.to_datetime(unit="ms")` produces dates one day off; the fix is to parse as UTC and `tz_convert("Asia/Shanghai")`.

The user wants a recurring, daily ingestion path that:
1. Pulls bars (1d or 1m, CLI-selectable) for every symbol in `instrument`.
2. Persists via the existing `KLineRepository.upsert_bars`.
3. Resumes from a watermark so each daily run is incremental.
4. Is exposed as `xtrade data sync | status | reset`.

This design covers the architectural decisions, the data-model changes, and the layering rules. See `proposal.md` for motivation and `specs/` for the behavioural contract.

## Goals / Non-Goals

**Goals:**
- A new `DataSource` implementation `XtQuantDataSource` registered lazily (no hard dep on xtquant).
- A reusable `DailyXtQuantCollector` service that wires `SourceRegistry → instruments → xtquant → KLineRepository` and tracks progress in `data_sync_state`.
- A new `xtrade data` CLI subcommand group with `sync`, `status`, `reset`.
- A new `data_sync_state` table managed by an Alembic migration.
- The xtquant timestamp normalisation logic shared between the new collector and the existing one-shot script (no duplication).

**Non-Goals:**
- Real-time / tick / subscription ingestion (订阅 / 推送). The collector is batched and historical.
- Adjustment-factor ingestion. `XtQuantDataSource.fetch_adjust_factors` returns an empty DataFrame; a separate future change may cover that.
- Per-exchange calendar ingestion. The current `trade_calendar` is populated by `import_trade_calendar.py` and is read-only from the collector's perspective.
- Multi-process orchestration. The CLI is invoked once per cycle; cron / Task Scheduler / systemd timer is the operator's responsibility.
- Re-applying adjustment on the write side. xtquant is called with `dividend_type="none"` (raw prices); backward / forward adjustment stays on the read path in `KLineRepository.get_bars`.

## Decisions

### Decision 1: Layering — `data-collection-xtquant` is a `data-layer` capability, not a CLI-only script

The new module lives under `src/xtrade/data/collection/xtquant.py` (next to `data/sources/`, `data/market_data/`, etc.). The CLI is a thin wrapper. This puts the collector in the same layer as the repositories it calls, lets it be unit-tested via constructor injection, and keeps the `core` / `strategy` / `execution` layers free of any MiniQMT concern.

**Alternative considered**: put the collector under `xtrade.cli.data` directly. Rejected because it makes unit testing harder (Click commands are awkward to drive without subprocesses) and couples the collection logic to the CLI entrypoint.

### Decision 2: Lazy import of `xtquant` in both the source and the CLI

`xtquant` is not on PyPI and is not installed on every developer's machine. The `SourceRegistry._init` and the CLI's `sync` subcommand SHALL attempt `from xtquant import xtdata` inside a `try / except ModuleNotFoundError`. On failure:
- `SourceRegistry`: silently skip the `xtquant` registration; the rest of the registry is unaffected.
- CLI: surface a clear `ModuleNotFoundError: No module named 'xtquant'` so the operator can install it via `uv pip install xtquant --find-links F:/QMT/python/Lib/site-packages`.

The lazy import is local to each call site; we do not wrap it in a higher-level abstraction because the failure modes and messages differ between the two contexts.

### Decision 3: Watermark storage — a dedicated `data_sync_state` table

We store per-`(source, interval)` watermarks in a new SQLAlchemy ORM table `data_sync_state`. This is preferable to two alternatives:
- **`SELECT max(trade_date)` from `kline_1d`**: works, but invisible to operators (no audit trail), and conflated with the actual data table.
- **Checkpoint file under `~/.xtrade/`**: harder to inspect, harder to back up alongside Postgres, and breaks in containerised deployments.

The table is small (one row per `(source, interval)`), so we use the broker-data pattern (SQLAlchemy 2.x synchronous `Session`) rather than the two-path COPY pattern from `data-market`. New ORM model: `src/xtrade/data/orm/sync_state.py`; new protocol + repository: `src/xtrade/data/sync_state/__init__.py`.

**Alternative considered**: add a row-level watermark to `kline_1d` itself (e.g. a `last_synced_at` column). Rejected because it confuses the data model with the ingestion control plane.

### Decision 4: `lookback_days` semantics — overlap, not strict-resume

Each run starts from `max(watermark.last_trade_date - lookback_days, earliest_unfilled_trade_date_in_window)`, where `lookback_days` defaults to `5`. We re-pull the trailing window every cycle so late corrections from xtquant (revised bars, restatements, late fills) overwrite earlier rows via `INSERT ... ON CONFLICT`. This is intentionally not a strict-resume: the cost is bounded (`lookback_days ≤ 30`, see Decision 7) and the robustness is much better.

### Decision 5: Per-symbol errors are warnings, not failures

A run is marked `"failed"` only when **no** rows were written (or an unrecoverable error happened before any IO). Partial success (some symbols skipped because xtquant returned nothing, but most succeeded) is marked `"ok"` with `last_trade_date` advanced to the latest bar actually written. This matches the one-shot script's behaviour and avoids blocking the watermark on a single bad symbol.

### Decision 6: Reuse the existing one-shot script's merge helper

The `merge_xtquant_bars` function and the ms-UTC → Asia/Shanghai conversion live in `scripts/fetch_historical_bars_xtquant.py`. We extract them into `xtrade.data.sources.xtquant` (renamed `merge_bars`) and have the script import from the new module. This:
- Keeps one source of truth for the off-by-one-day fix.
- Lets unit tests cover the merge helper without xtquant installed (it's a pure function on `dict[str, pd.DataFrame]`).
- Is a non-breaking refactor: the script's CLI behaviour is unchanged.

### Decision 7: Hard-cap `lookback_days` at 30

The CLI rejects `--lookback-days > 30` with a clear error. This protects the operator from accidentally triggering a multi-year backfill on first-ever run (no watermark → start = today − lookback_days). The cap is configurable in code (`_MAX_LOOKBACK_DAYS = 30`) but not in the CLI; lifting it is a separate decision because at-scale backfill needs different batching, error handling, and progress reporting.

### Decision 8: Serial execution, no worker pool

Same as the existing one-shot script: MiniQMT's `xtdata` client is single-process; multi-process / multi-thread access either crashes or interleaves data. The collector iterates batches serially. Within a batch, `fetch_bars` is called per-symbol — xtquant's `download_history_data2` accepts a `stock_list` so we batch the cache-fill call to amortise the round-trip.

**Alternative considered**: `concurrent.futures.ThreadPoolExecutor` for the per-symbol `fetch_bars` calls. Rejected because xtquant's client does not support concurrent calls from a single process.

### Decision 9: The new `xtquant` source uses the shared `merge_bars`

`XtQuantDataSource.fetch_bars(symbol, start, end, interval)` returns a **single-symbol** normalised DataFrame (matching the `DataSource` Protocol). The collector then concatenates per-symbol frames into a multi-symbol frame before calling `kline_repo.upsert_bars`. This keeps `DataSource` per-symbol (matches `xtquant`'s natural API) while letting the collector batch the repository call.

### Decision 10: Logging goes through the project's standard `logging`

The collector uses `logger = logging.getLogger("xtrade.data.collection.xtquant")`. The CLI's `sync` subcommand configures a stdout `StreamHandler` at `INFO` level when invoked (so operators see the report even without `xtrade` configured). No new logging framework is introduced.

### Decision 11: Migration is additive and reversible

`0002_data_sync_state.py` only creates one table; it does not alter any `0001_initial.py` table. `alembic downgrade base` from the head revision drops both `data_sync_state` and the `0001_initial` tables (in the correct order — `0002` drops first). No data backfill in the migration; seeding happens at runtime on first `sync`.

### Decision 12: No new public dependency on xtquant

`pyproject.toml` already declares `xtquant` under `[project.optional-dependencies.xtquant]`. We add nothing. The `[xtquant]` extras group remains the install path (`uv pip install -e ".[xtquant]"`).

## Risks / Trade-offs

- **[Risk] xtquant not installed at import time** → The lazy import inside `SourceRegistry._init` and the CLI's `sync` subcommand catches `ModuleNotFoundError`. On the source side: skipped silently, `SourceRegistry().get("xtquant")` raises `KeyError`. On the CLI side: clear error message printed to stderr, exit code 1.
- **[Risk] xtquant returns an empty DataFrame for symbols outside the cached window** → Per-symbol empty frames are dropped by `merge_bars`; the symbol is added to `SyncReport.symbols_skipped`. The watermark is not penalised — partial success is treated as success.
- **[Risk] MiniQMT not running** → `xtdata.download_history_data2` raises on the first call; the collector wraps the call site and adds the offending batch's symbols to `symbols_skipped`. A fully empty run is marked `"failed"` with `error = <message>`.
- **[Risk] Two `sync` invocations overlap** → Each invocation takes a `Session`, writes its `status="in_progress"` row at start (best-effort), then `"ok"` / `"failed"` at end. Without a process lock, two concurrent runs could double-process — acceptable for the operator-driven scheduling model (cron / Task Scheduler); documented in the CLI help.
- **[Risk] Long first-ever run with `lookback_days=5` for 6 000 symbols × 4 years of 1m data** → Bounded by `lookback_days ≤ 30` and the existing `upsert_bars` batching. For 1m at scale, operators should start with `lookback_days=1` and a small `--limit`. We document this in the CLI help.
- **[Risk] `data_sync_state` becomes the single source of truth for "what was synced"** → Acceptable because the table is small and transactional. If it ever drifts (operator manually deletes a row), `xtrade data reset` rebuilds it. We do NOT auto-reconcile against `kline_1d` because that would mask real bugs.
- **[Risk] Existing `fetch_historical_bars_xtquant.py` users must rebuild after the merge-helper extraction** → The script's CLI surface and behaviour are unchanged. The only difference is an additional import. Tested via `tests/scripts/test_fetch_historical_bars_xtquant.py` (existing tests stay green).

## Migration Plan

This change is additive. Rollout:

1. Run `alembic upgrade head` against the configured database to create `data_sync_state`.
2. (Optional, only if not already installed) `uv pip install -e ".[xtquant]"` to get the `xtquant` package from the local QMT distribution.
3. Start MiniQMT and confirm the local cache has data for the symbols in `instrument`.
4. (Optional) Run `xtrade data status` to confirm `data_sync_state` exists and is empty.
5. Run `xtrade data sync --interval 1d --lookback-days 5` once for an initial pull. Confirm `xtrade data status` shows `last_trade_date` advanced.
6. Schedule `xtrade data sync --interval 1d` (and separately `--interval 1m` if desired) in cron / Task Scheduler / systemd timer for after each trading day's close.

Rollback:
- Delete the scheduled cron entry.
- Run `xtrade data reset --interval 1d` (and `1m`) to clear watermarks if desired.
- `alembic downgrade -1` removes `data_sync_state`. Existing `kline_1d` / `kline_1m` rows are unaffected.

## Open Questions

None. The four user clarifications (full `instrument` scope, CLI-selectable interval, `xtrade data` group, `data_sync_state` watermark) are pinned in the proposal and the spec files. Anything else can be deferred.