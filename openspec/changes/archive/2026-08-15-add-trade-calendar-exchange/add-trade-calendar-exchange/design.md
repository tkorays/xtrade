## Context

The current `trade_calendar` schema (see proposal) is `(date PK, is_trading)`.
The legacy `mos` dump provides `(exchange, date, is_trading)` per exchange,
with the same data also written under the leading-prefix form
`Exchange.SH/SZ/BJ`. The widening is a one-way migration: the existing
table is empty in dev (the previous import attempt was eaten by the
SQLAlchemy commit bug already fixed in this session), so no rows need
to be translated, only the DDL is rewritten.

## Goals / Non-Goals

**Goals:**

- Store every `(exchange, date, is_trading)` row from the legacy dump
  without losing the exchange dimension.
- Keep `xtrade.core.market_data.is_trading_day(d)` and
  `get_trading_days(start, end)` backward compatible at the signature
  level — date-only callers do not need to know about exchanges.
- One-shot import script that mirrors the style of
  `scripts/import_legacy_bars.py`, `import_instrument_info.py`, and
  `import_adjust_factor.py`.

**Non-Goals:**

- Exposing `exchange` in the public facade (no caller requested a
  per-exchange calendar view; can be added in a follow-up if needed).
- Modelling holiday rules or generating calendars — this is only an
  importer for an existing dump.
- Backfilling historical calendar data not present in the dump (the
  dump covers 2026-01-05 to 2026-06-12; older dates stay absent).

## Decisions

- **Primary key `(exchange, date)`**: matches the source dump's
  grain; allows each exchange to track its own calendar independently.
  Alternative: keep `(date)` as PK with `exchange` non-key — rejected
  because it cannot represent the dump's per-exchange rows without
  dropping information.
- **Any-exchange collapse for `is_trading_day` / `get_trading_days`**: the
  simplest rule that preserves caller-facing behaviour for the common
  case where `SH` and `SZ` agree. Alternative: default to a single
  exchange (`SH`) — rejected because it forces callers to know
  which exchange to query, and there is no canonical "primary"
  exchange in the data.
- **`Exchange.` prefix normalisation in the script** (rather than
  storing both forms in Postgres): keeps the database small (3
  exchanges × 105 days = 315 rows instead of 630) and avoids a
  redundant canonicalisation step at read time.
- **`upsert_days(df)` requires `exchange` column**: explicit > implicit.
  Existing in-tree call sites (`tests/data/test_market_data.py`,
  `tests/data/test_sources.py`, `tests/data/test_engine.py`,
  `tests/core/test_market_data.py`) are updated to pass `exchange`.
- **No `is_trading` flag normalisation**: the dump has only
  `is_trading=TRUE` rows today, but the schema and the import
  preserve whatever boolean the source provides.

## Risks / Trade-offs

- [Behavioural change for `is_trading_day`] → If a future caller
  inserted an `('SH', d, FALSE)` row alongside a `('BJ', d, TRUE)`
  row, `is_trading_day(d)` would still return `True` (any-exchange
  rule). Documented in `xtrade.core.market_data.is_trading_day`'s
  docstring at apply time so callers know.
- [PK rewrite requires DROP TABLE on dev] → The dev table is empty,
  so the cost is zero. Documented in tasks.md for any other dev
  instance that may have populated data.
- [Pump's `trade_calendar` count semantics] → `pump` reports the
  number of rows it wrote. With per-exchange rows the count goes
  up; this is informational only, no caller depends on a specific
  value.

## Migration Plan

1. Apply the code changes (ORM, migration, repo, sources, script).
2. On dev: `DROP TABLE trade_calendar;` then
   `uv run alembic upgrade head` to pick up the widened DDL.
3. Run `uv run python scripts/import_trade_calendar.py` to ingest
   the dump (315 rows after normalising the `Exchange.` prefix).
4. Verify with the manual SQL probes used during this session.

Rollback: revert the code changes and re-apply `alembic downgrade
base` (drops `trade_calendar`).

## Open Questions

None — the script, schema, and API are all locked in by the
Q&A above.