## Context

`XtQuantDataSource.fetch_bars` calls `xtquant.xtdata.download_history_data2` and `get_market_data_ex` once per symbol. On a 7000-symbol market that is 14 000 sequential MiniQMT calls per `xtrade data sync --interval 1d`. Operators measured ~100 ms / symbol for warm-cache hits but tens of seconds for cold downloads, so a full routine run is dominated by serial MiniQMT round-trips — not by the actual download.

`xtquant` exposes bulk variants that take a `stock_list` argument:
- `xtdata.download_history_data2(stock_list, period, start_time, end_time)` — queues downloads for the whole list in one call.
- `xtdata.get_market_data_ex(field_list, stock_list, period, start_time, end_time, ...)` — returns one wide DataFrame covering all symbols in the list.

The bulk API amortises per-call setup, reuses the MiniQMT connection pool, and — critically — queues all downloads server-side at once, so MiniQMT can fetch them concurrently against its own internal scheduler. This is the documented pattern for full-market pulls.

This change adds a `fetch_bars_bulk` method to `XtQuantDataSource` (and to the `DataSource` Protocol), rewires `DailyXtQuantCollector` to use it once per batch instead of looping per-symbol, and adjusts the `batch_size` default for `1d` to "all instruments" so a routine 1d sync collapses to a single MiniQMT round-trip.

## Goals / Non-Goals

**Goals:**
- One `download_history_data2` + one `get_market_data_ex` per batch (was: per symbol).
- For `--interval 1d`, default `batch_size` to `len(instruments)` so a single bulk call covers the whole market.
- For `--interval 1m`, keep default `batch_size=50` because 1m memory cost dominates.
- Surface a `--batch-size-max` safety valve (`500` default) so a misconfigured 1m run can't blow up memory.
- Retry once on whole-batch failure; record per-symbol skips for symbols that came back all-NaN.
- `scripts/fetch_historical_bars_xtquant.py` still works via a thin `fetch_bars` wrapper.

**Non-Goals:**
- Concurrent fetching from the Python side (xtquant's bulk API already does the concurrency inside MiniQMT).
- A different source layout for `get_market_data_ex` field names — keep `["time", "open", "high", "low", "close", "volume", "amount"]` to match the existing `merge_bars` shape.
- Changing `KLineRepository.upsert_bars` (it already accepts long-format frames).
- Changing the `data_sync_state` watermark behaviour.

## Decisions

### Decision 1: `DataSource.fetch_bars_bulk` is required, `fetch_bars` becomes optional

`fetch_bars` is preserved as a thin wrapper around `fetch_bars_bulk([symbol], ...)` for the `scripts/fetch_historical_bars_xtquant.py` smoke test. Anything new that talks to a `DataSource` MUST go through `fetch_bars_bulk`. `MockDataSource` implements both; `XtQuantDataSource` implements both.

This means the `DataSource` Protocol's structural typing requires `fetch_bars_bulk`. Any future source has to implement it.

### Decision 2: `fetch_bars_bulk` returns a single long-format DataFrame

`get_market_data_ex` returns a wide frame: one column per `(field, symbol)` tuple (`"open$000001.SZ"`, `"close$000001.SZ"`, ...). The implementation SHALL pivot that to long format with columns `time, symbol, open, high, low, close, volume, amount`. This matches what `merge_bars` produces today, so `KLineRepository.upsert_bars` and the watermark logic stay unchanged.

Symbols absent from the wide frame (MiniQMT returned no rows) are simply not in the long-format output — same convention as `fetch_bars` returning an empty frame.

### Decision 3: Default `batch_size` varies by interval

| Interval | Default `--batch-size` | Why |
|---|---|---|
| `1d` | `len(instruments)` (whole market in one call) | 7000 symbols × ~10 years × 1 row/day ≈ 17M rows; pandas long-format ≈ 200 MB — fits comfortably in operator hardware. |
| `1m` | `50` | 7000 symbols × 240 trading days × 240 minutes ≈ 400M rows / 50 = 8M rows per batch ≈ 250 MB; safe headroom. |

The CLI computes the default from `--interval` and `len(instruments)` (which requires a DB hit). The DB hit is cheap (one indexed `SELECT COUNT(*)`).

### Decision 4: `--batch-size-max` defaults to `500`

This is a safety valve: a `--batch-size` larger than this is almost certainly a misconfiguration (especially for `1m`). The CLI rejects before any IO. The default `500` is generous for `1d` (still 1/14 of the 7000-symbol market) and a hard ceiling for `1m`.

### Decision 5: Whole-batch retry, per-symbol skip

The previous design caught per-symbol exceptions inside the batch loop. With bulk fetch the failure modes are different:
- A whole-batch exception (network drop, MiniQMT rejection) — retry the same call once; on second failure, mark **all symbols in the batch** as `symbols_skipped`. This is the dominant failure mode and the one we want to retry.
- A symbol present in the wide frame but with all-NaN rows for required fields (likely delisted, suspended, or no history in window) — record as `symbols_skipped` with reason `xtquant returned empty frame for this symbol`; the rest of the batch is upserted.

We lose the per-symbol `try/except` granularity because `download_history_data2` returns a status code per symbol rather than raising. The retry covers the more impactful case (whole batch dies) and the per-symbol skip covers the residual.

### Decision 6: One DEBUG line per batch under `--verbose`

The previous `--verbose` mode printed one line per symbol fetch. With bulk fetch there is one call per batch; the DEBUG line now reports the batch's symbol list summary: `bulk-fetch: syms=7000 interval=1d first=000001.SZ last=603999.SH`. Operators lose visibility into "which single symbol is slow" but gain visibility into "which batch is in flight". This is the right tradeoff: bulk fetch is server-concurrent, so individual symbols don't meaningfully stall the batch.

## Risks / Trade-offs

- **[Risk] Wide DataFrame allocation in MiniQMT side** — `get_market_data_ex(stock_list=7000)` materialises a frame covering all 7000 symbols on the xtquant side before returning. For `1d` and a 10-year window this is ~17M rows; xtquant has been observed to handle this. For longer windows or `1m` we cap via `--batch-size-max=500`.
- **[Risk] Whole-batch retry doubles latency on a transient failure** — A single batch's bulk call retry adds at most one MiniQMT round-trip (~10s) before we record the batch as skipped. Acceptable; the alternative is silent loss of data.
- **[Risk] Removing the per-symbol `fetch_bars` path** — We keep `fetch_bars` as a wrapper, so `scripts/fetch_historical_bars_xtquant.py` and any third-party code that imports `XtQuantDataSource.fetch_bars` keeps working. The Protocol's structural-typing requirement shifts to `fetch_bars_bulk`.
- **[Risk] `1d` whole-market call may exceed MiniQMT's internal limits** — Some MiniQMT versions cap `stock_list` length. The design accepts this as out-of-scope: the operator can lower `--batch-size` if they hit it, and the safety valve prevents catastrophic misconfiguration.
- **[Trade-off] Cannot diagnose per-symbol slowness** — Bulk fetch is opaque; a slow symbol slows the whole batch but we can't tell which one. Acceptable because the upsert dominates wall-time for warm caches and the slow-fetch WARN still fires on the whole-batch `fetch_bars_bulk` call.

## Migration Plan

Non-migrating. The CLI's `--batch-size` default changes for `1d` (was `50`, now `len(instruments)`); operators with shell scripts that hardcode `--batch-size 50` for `1d` will see no behaviour change. Operators relying on the *implicit* default to be `50` for `1d` will see a single bulk call instead of 140 batches — strictly faster, no functional difference.

`SyncReport` shape is unchanged. `data_sync_state` semantics are unchanged. Existing ad-hoc `--start-date` / `--end-date` flags keep working.

## Open Questions

None.