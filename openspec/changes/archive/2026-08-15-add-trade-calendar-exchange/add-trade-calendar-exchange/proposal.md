## Why

The legacy `mos` trade-calendar dump (`C:\Users\tkorays\.mos\data\trade_date.db`)
stores one row per `(exchange, date)` — three exchanges (`SH`, `SZ`, `BJ`) — so
that A-share, ETF, and Beijing-exchange schedules can diverge on a future date.
The current `trade_calendar` table only models a single global calendar
(`(date PK, is_trading)`) and therefore cannot ingest the dump without losing
the exchange dimension. Importing it as a global calendar would silently merge
the three exchanges' rows, hiding any future per-exchange divergence.

This change widens `trade_calendar` to `(exchange, date, is_trading)` with
PK `(exchange, date)` so the dump can be ingested losslessly, while keeping
the date-only `is_trading_day(d)` / `get_trading_days(start, end)` facade
working — those collapse the per-exchange view using the existing
"any-exchange-is-trading" rule.

## What Changes

- `trade_calendar` table gains an `exchange` column (NOT NULL VARCHAR(16));
  primary key changes from `(date)` to `(exchange, date)`.
- `TradeCalendarORM` and the `0001_initial.py` DDL gain `exchange`; the
  column is non-nullable, so the migration will require a one-time
  `DROP TABLE trade_calendar` on dev (table is currently empty).
- `PostgresTradeCalendarRepository`:
  - `upsert_days(df)` now requires an `exchange` column in ``df``;
    its upsert key is ``(exchange, date)``.
  - `is_trading_day(d)` reads through ``ANY(exchange)`` semantics —
    a date is a trading day iff **any** exchange marks it as such.
  - `get_trading_days(start, end)` returns the union of trading dates
    across all exchanges (a date is included if any exchange marks it
    as trading).
- `InMemoryMockSource.fetch_trade_calendar(...)` continues to return
  rows with the new columns ``(exchange, date, is_trading)``;
  ``pump`` propagates ``exchange`` straight through.
- New one-shot script `scripts/import_trade_calendar.py` reads the
  legacy DuckDB, normalises the leading ``Exchange.`` prefix
  (``Exchange.SH`` → ``SH``) so the canonical three-letter codes are
  the only values persisted, and upserts into Postgres.
- Repository interface remains backward compatible at the
  ``xtrade.core.market_data`` layer: callers keep passing ``date``
  (no ``exchange`` parameter); the repository handles the
  any-exchange rule internally.

## Capabilities

### New Capabilities

- `data-import-trade-calendar`: one-shot import script for the legacy
  ``mos`` ``trade_date.db`` DuckDB into Postgres ``trade_calendar``.

### Modified Capabilities

- `data-market`: the `Repository pattern for market data` requirement
  gains an `exchange` column on `trade_calendar`, a new upsert key,
  and the any-exchange collapse rule for `is_trading_day` /
  `get_trading_days`.

## Impact

- Code:
  - `src/xtrade/data/orm/market.py` — `TradeCalendarORM` adds `exchange`.
  - `src/xtrade/data/migrations/versions/0001_initial.py` — DDL gains
    `exchange`; PK changes to `(exchange, date)`; downgrade updated.
  - `src/xtrade/data/market_data/trade_calendar.py` — `upsert_days`
    requires `exchange`; `is_trading_day` / `get_trading_days` use the
    any-exchange rule.
  - `src/xtrade/data/sources/mock_source.py` — calendar default columns
    become `(exchange, date, is_trading)`.
  - `src/xtrade/data/sources/pump.py` — pass `exchange` through.
  - `scripts/import_trade_calendar.py` — new one-shot import script.
- Tests:
  - `tests/data/test_market_data.py`,
    `tests/data/test_sources.py`,
    `tests/data/test_engine.py`,
    `tests/core/test_market_data.py`,
    `tests/data/test_migrations.py` — fixtures add `exchange`.
- Dev DB: `DROP TABLE trade_calendar` + `alembic upgrade head` to pick
  up the widened DDL (table is currently empty).
- Callers of `is_trading_day(d)` and `get_trading_days(start, end)`
  see no signature change; behaviour is unchanged in the common
  scenario where `SH` and `SZ` agree.