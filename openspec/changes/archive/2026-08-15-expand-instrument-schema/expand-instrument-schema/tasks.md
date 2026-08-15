## 1. Schema + ORM

- [x] 1.1 Update `InstrumentORM` in `src/xtrade/data/orm/market.py` to add `type` (NOT NULL VARCHAR 16, server default `''`), `list_board`, `industry`, `area` (nullable VARCHAR), `is_t0` (NOT NULL BOOLEAN, server default `false`). Change `status` default to `'L'`. Drop nothing else from the existing columns.
- [x] 1.2 Update the `instrument` `op.create_table(...)` block in `src/xtrade/data/migrations/versions/0001_initial.py` to mirror the new ORM (new columns + server defaults + new `status` default). Update the corresponding `op.drop_table("instrument")` in `downgrade()` only if needed (it stays a single drop call).

## 2. Dataclass + repository

- [x] 2.1 Widen `Instrument` in `src/xtrade/data/market_data/instrument.py` to add `type: str`, `list_board: str | None`, `industry: str | None`, `area: str | None`, `is_t0: bool = False`. Keep `status: str` (no default change in the dataclass; callers pass `'L'`/`'D'`).
- [x] 2.2 Update `_to_record` in the same file to populate every new column from the ORM row.
- [x] 2.3 Update `PostgresInstrumentRepository._upsert` to write every column on insert and on update (no partial-update shortcut).

## 3. Import script

- [x] 3.1 Rewrite `fetch_records` in `scripts/import_instrument_info.py` to project every new column (no `price_tick`). Remove `_STATUS_MAP` and pass `status` through verbatim. Update `_upsert_batch` / the `Instrument(...)` construction to supply the new fields.
- [x] 3.2 Re-run `uv run python scripts/import_instrument_info.py --dry-run` against the source DuckDB to confirm 7073 records are still discovered.

## 4. Test fixtures

- [x] 4.1 Update `Instrument(...)` literals in `tests/data/test_market_data.py` (the `test_instrument_upsert_and_get` and `test_pump_writes_source_into_repositories` tests) to include `type` / `is_t0` and `status="L"`.
- [x] 4.2 Update `Instrument(...)` literal in `tests/data/test_sources.py` (`test_in_memory_mock_source_instruments_defensive_copy`) to include `type` / `is_t0` and `status="L"`.
- [x] 4.3 Update `Instrument(...)` literal in `tests/core/test_market_data.py` (`test_get_instrument_returns_record`) to include `type` / `is_t0` and `status="L"`.

## 5. Verify

- [x] 5.1 `uv run pytest` — all unit tests pass (integration tests stay skipped without `XTRADE_TEST_DB_URL`).
- [x] 5.2 `uv run ruff check src tests && uv run ruff format --check src tests` — clean.
- [x] 5.3 `uv run mypy src` — strict passes.
- [x] 5.4 `openspec validate --all --strict` — change passes validation.
- [x] 5.5 `uv run alembic upgrade head` against the dev Postgres after manually dropping the old `instrument` table — re-creates with the new DDL.