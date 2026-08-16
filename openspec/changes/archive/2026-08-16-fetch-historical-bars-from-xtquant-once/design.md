## Context

`xtrade` persists K-lines in Postgres (`kline_1d` regular table; `kline_1m` TimescaleDB hypertable, see `convert-kline-1m-to-timescaledb-hypertable`). The `instrument` table holds symbol metadata (small, populated by operators). `xtquant` is the local MiniQMT Python client — it returns historical bars via `xtdata.download_history_data2` (cache-fill) + `xtdata.get_local_data` (read-back). MiniQMT is a single-process client; concurrent multi-process access from the script is unsafe. The user's `mos_quant.data.collector_bar_xtdata.DataCollectorXTData` already demonstrates the merge pattern but writes to local parquet, not Postgres. We adapt that pattern to call `PostgresKLineRepository.upsert_bars` for the write step. See `proposal.md` for motivation.

## Goals / Non-Goals

**Goals:**
- One-shot script at `scripts/fetch_historical_bars_xtquant.py` that pulls 1d / 1m data from `xtquant` and writes it to Postgres via the existing `PostgresKLineRepository`.
- Idempotent: re-running rewrites only overlapping `(symbol, time_col)` rows; pre-existing rows outside the window are preserved.
- Resilient: per-symbol errors are reported but do not abort the run.
- Zero concurrency: serial execution keeps MiniQMT's single-client constraint respected.

**Non-Goals:**
- Real-time / subscription ingestion. The script is purely historical.
- Pulling adjustment factors. Separate one-shot script if needed.
- Filtering `instrument` by `list_date` / `delist_date` (user decision: no filtering).
- Concurrent multi-process / multi-thread downloads (user decision: serial only).
- Adding `xtquant` to the project's required deps. It is documented as a manual install step; the script imports it lazily.

## Decisions

### Decision 1: Serial loop, no worker pool

MiniQMT's `xtdata` client is single-process; running multiple `download_history_data2` calls in parallel from different processes either crashes or returns interleaved data. We run a single Python process and process batches one at a time. Within a batch, we still issue one `download_history_data2` call (xtquant accepts a `stock_list` for this) followed by one `get_local_data` call. Throughput is bounded by xtquant itself, not by the script's loop.

### Decision 2: Batch size = 50 symbols per `download_history_data2` call

Empirically, xtquant's `download_history_data2` accepts up to ~200 symbols per call before network / serialisation overhead dominates. A batch of 50 is a conservative default; users can override via `--batch-size`. The script iterates `range(0, total_symbols, batch_size)` and processes each batch end-to-end (download → get_local_data → merge → upsert) before moving on.

### Decision 3: Reuse `PostgresKLineRepository.upsert_bars`

`upsert_bars` already implements the COPY + INSERT ... ON CONFLICT path at ~30-50k rows/sec on the dev server. Writing it again from scratch is unnecessary and risks introducing subtle bugs (column subset, NaN handling, ON CONFLICT clause). The script calls it directly.

### Decision 4: Merge helper is pure (no I/O), unit-testable

`merge_xtquant_bars(ret: dict[str, pd.DataFrame], interval: str) -> pd.DataFrame` is a top-level function that:
1. Iterates the dict.
2. Skips `None` / empty frames.
3. Renames `preClose` → `pre_close` (defensive; we drop it anyway).
4. Converts `time` from ms UTC epoch to tz-aware Asia/Shanghai datetime (or `date` for `1d`).
5. Adds the `interval` column.
6. Concatenates per-symbol frames and sorts by `(symbol, time_col)`.

The function is module-level so it can be unit-tested without xtquant installed (tests pass in-memory `dict`s).

### Decision 5: Timestamp conversion rules

xtquant returns `time` as `int64` milliseconds since the UTC epoch, **representing Beijing 00:00 of the requested date**. Naively parsed as UTC, this is the day *before* the requested date. The fix is to parse as UTC, then `tz_convert("Asia/Shanghai")`, then either:
- For `1d`: `.dt.date` to produce a `datetime.date` for `kline_1d.trade_date`.
- For `1m`: keep as tz-aware `Timestamp` for `kline_1m.ts`.

This matches the legacy `mos_quant` collector's behaviour.

### Decision 6: Date-string formatting

xtquant accepts two formats:
- For `1d`: `YYYYMMDD` (e.g. `20240101`).
- For `1m`: `YYYYMMDDHHmmss` (e.g. `20240101093000`).

The script formats `--start` / `--end` (parsed as `date`) accordingly:
- `1d`: `start.strftime("%Y%m%d")` and `end.strftime("%Y%m%d")`.
- `1m`: `datetime.combine(start, time.min).strftime("%Y%m%d%H%M%S")` and `datetime.combine(end, time.max).strftime("%Y%m%d%H%M%S")`.

### Decision 7: No instrument filtering

The user explicitly opted out of `list_date` / `delist_date` filtering. The script reads `SELECT symbol FROM instrument ORDER BY symbol` and uses the full list. This means xtquant will return empty frames for symbols that have no data in the requested window; those become `symbols_skipped` entries in the final report.

### Decision 8: xtquant import is local + lazy

The script imports `xtquant.xtdata` only after argument validation passes. If xtquant is not installed, the user sees a clear `ModuleNotFoundError` at startup. The `pyproject.toml` is updated with `[project.optional-dependencies.xtquant]` containing `xtquant`, and a comment in the script's docstring explains how to install.

### Decision 9: `dividend_type="none"` for raw prices

xtquant accepts `dividend_type` ∈ `{none, front, back, front_ratio, back_ratio}`. We pass `"none"` to match the legacy parquet layout (which stored raw prices). Adjusting on the read path is owned by `PostgresKLineRepository.get_bars` via the `adjust=` argument; we do not pre-apply in the script.

### Decision 10: `--dry-run` short-circuits before any external call

`--dry-run` resolves the symbol list from `instrument`, prints the planned batches, and exits 0 without calling `xtdata`. This is the same pattern used by `import_legacy_bars.py`.

## Risks / Trade-offs

- **[Risk] xtquant not in venv** → Documented as a manual install step (`uv pip install xtquant` from the user's local QMT distribution). The script's first line of executable code is a `from xtquant import xtdata` import; failure raises a clear Python error.
- **[Risk] xtquant's millisecond-UTC timestamp semantics** → The merge helper must parse as UTC and convert to Asia/Shanghai. A naive `pd.to_datetime(df["time"], unit="ms")` would silently produce dates offset by one day. Unit tests pin the correct behaviour.
- **[Risk] xtquant returns an empty dict for unknown symbols** → Each per-symbol frame in the dict is checked for `None` / `empty` and counted as `symbols_skipped` rather than crashing the batch.
- **[Risk] MiniQMT not running** → `xtdata.connect()` raises; we let the exception propagate so the user sees the underlying error.
- **[Risk] Per-batch memory blow-up** → Each batch is processed end-to-end (download → merge → upsert) before the next batch starts; the per-batch frame size is bounded by `batch_size × bars_per_symbol`. For 1m with `--start=2024-01-01` and `--end=2024-12-31`, that's ~50 symbols × 70 000 bars ≈ 3.5 M rows / batch. The merge uses `pd.concat` (eager); for 1m at scale, consider switching to chunked concat in a follow-up.
- **[Risk] `upsert_bars` overwrites prices with newer data even when xtquant returns stale data** → Acceptable. xtquant's data is the source of truth; if a row is older than what's already in Postgres, `INSERT ... ON CONFLICT DO UPDATE` will overwrite with the (possibly stale) value. The script does not pre-filter by `ts > max(ts)`. Documented behaviour.
- **[Risk] `instrument` table is empty** → Script reports `0 symbols` and exits 0 with an empty progress report. No DB writes.

## Migration Plan

This is a new script; no migration. Deployment steps:

1. Add `xtquant` to `[project.optional-dependencies.xtquant]` in `pyproject.toml`.
2. The operator installs it locally with `uv pip install xtquant` (from their QMT distribution).
3. The operator starts MiniQMT and confirms the local cache is populated.
4. The operator runs `uv run python scripts/fetch_historical_bars_xtquant.py --interval 1d --start YYYY-MM-DD --end YYYY-MM-DD`.
5. The script writes rows into `kline_1d` (or `kline_1m` if `--interval 1m`).
6. The operator verifies `SELECT count(*), min(from_date), max(from_date) FROM kline_1d WHERE symbol = ...` matches expectations.

No rollback needed — the script is additive. If a row's value is wrong, the operator can `DELETE FROM kline_1d WHERE ...` and re-run.

## Open Questions

None. All material decisions are resolved.