## 1. Schema + ORM

- [x] 1.1 Update `TradeCalendarORM` in `src/xtrade/data/orm/market.py` to add `exchange` (NOT NULL VARCHAR(16), PK together with `date`). Move `date` from single-column PK to composite `(exchange, date)` PK. Set up an index on `(exchange, date)` (PK creates it).
- [x] 1.2 Update the `trade_calendar` `op.create_table(...)` block in `src/xtrade/data/migrations/versions/0001_initial.py` to add the `exchange` column and the composite PK. The downgrade `op.drop_table("trade_calendar")` does not need to change.

## 2. Repository

- [x] 2.1 Update `PostgresTradeCalendarRepository.upsert_days(df)` in `src/xtrade/data/market_data/trade_calendar.py` to require an `exchange` column (add `"exchange"` to `REQUIRED_COLUMNS`). Change the SQL to `INSERT ... ON CONFLICT (exchange, date) DO UPDATE SET is_trading = EXCLUDED.is_trading`. Cast `exchange` to `str` defensively when building rows.
- [x] 2.2 Update `PostgresTradeCalendarRepository.is_trading_day(d)` so it returns `True` iff **any** `exchange` row in `trade_calendar` marks `d` as trading (use `EXISTS (SELECT 1 FROM trade_calendar WHERE date = %s AND is_trading = TRUE)`).
- [x] 2.3 Update `PostgresTradeCalendarRepository.get_trading_days(start, end)` so it returns the **union** across exchanges — `SELECT DISTINCT date FROM trade_calendar WHERE date BETWEEN %s AND %s AND is_trading = TRUE ORDER BY date`. No `exchange` filter at the call site.
- [x] 2.4 Add a one-line docstring note on `is_trading_day` and `get_trading_days` explaining the any-exchange rule (so callers don't expect a single-exchange view).

## 3. Sources

- [x] 3.1 Update `InMemoryMockSource` default `_calendar` columns in `src/xtrade/data/sources/mock_source.py` from `[date, is_trading]` to `[exchange, date, is_trading]`. `fetch_trade_calendar` is unchanged in behaviour — just propagate the new column.
- [x] 3.2 Confirm `pump` in `src/xtrade/data/sources/pump.py` already passes through whatever columns `fetch_trade_calendar` returns (it does — it just hands `cal_df` to `calendar_repo.upsert_days`).

## 4. Import script

- [x] 4.1 Create `scripts/import_trade_calendar.py` mirroring the style of `import_adjust_factor.py` / `import_legacy_bars.py`:
  - `--source` defaults to `C:\Users\tkorays\.mos\data\trade_date.db`.
  - `--source-table` defaults to `trade_date`.
  - `--batch-size` defaults to `1000`.
  - `--dry-run` flag.
  - Reuse the `ensure_database` + `run_alembic_upgrade` bootstrap pattern.
  - Read DuckDB rows with columns `(exchange, date, is_trading)`.
  - Normalise `Exchange.` prefix: if `exchange` starts with `"Exchange."`, strip the prefix; otherwise keep as-is.
  - Skip rows with NULL `exchange` / `date` / `is_trading` (warn and continue).
  - Build a `pd.DataFrame` with columns `exchange / date / is_trading` and pass to `PostgresTradeCalendarRepository.upsert_days` in batches.
- [x] 4.2 Run `uv run python scripts/import_trade_calendar.py --dry-run` against the source DuckDB to confirm row discovery (630 records; collapses to 315 on `ON CONFLICT`).

## 5. Test fixtures

- [x] 5.1 Update calendar `pd.DataFrame` literals in `tests/data/test_market_data.py` (the integration test `test_calendar_upsert_and_is_trading_day` and the pump test) to include the `exchange` column (`"SH"`). `tests/data/test_sources.py` and `tests/data/test_engine.py` have no calendar fixtures.
- [x] 5.2 Update `tests/core/test_market_data.py` `is_trading_day` / `get_trading_days` assertions if needed — no changes needed; the core tests use a fake repo and the facade API is unchanged. The any-exchange semantics live in the Postgres repository and are covered by the spec scenarios.
- [x] 5.3 Update `tests/data/test_migrations.py` schema-snapshot helpers if they hardcode the old `(date, is_trading)` shape — no changes needed; only the table name string appears.

## 6. Verify

- [x] 6.1 `uv run pytest` — 196 passed, 30 skipped.
- [x] 6.2 `uv run ruff check src tests && uv run ruff format --check src tests` — clean (84 files).
- [x] 6.3 `uv run mypy src` — strict passes (47 files, no issues).
- [x] 6.4 `openspec validate --all --strict` — 14/14 passed.
- [x] 6.5 On dev: dropped `trade_calendar` and re-created it with the new DDL via a manual psycopg script (table was empty so no data loss; alembic wouldn't re-run because `0001_initial` was already stamped).
- [x] 6.6 `uv run python scripts/import_trade_calendar.py` — 630 source rows collapsed to **315** persisted rows (105 dates × 3 exchanges after `Exchange.` prefix normalisation; `ON CONFLICT (exchange, date)` deduplicated the SH/SZ/BJ duplicates from the legacy dump). Verified: total = 315, per-exchange = 105/105/105, no `Exchange.` prefix rows, `is_trading_day` and `get_trading_days` work via the any-exchange rule.