## Context

The `instrument` reference table today carries only 6 columns; the legacy `mos` DuckDB dump we want to import carries 11 (excluding `price_tick`). The `Instrument` dataclass is used by `InMemoryMockSource`, `pump`, and a couple of integration tests, all of which already construct records by keyword and tolerate new required fields as long as they are supplied. The repository already does per-PK upsert keyed on `symbol`, so the write path is naturally idempotent and adding columns does not change conflict semantics.

## Goals / Non-Goals

**Goals:**

- Make the `instrument` schema a one-to-one mirror of the legacy DuckDB columns (sans `price_tick`), so the one-shot import script can land every field with zero projection loss.
- Keep the `Instrument` dataclass the canonical in-memory shape; the dataclass and ORM stay 1-to-1 except the dataclass drops the PK constraint (it stays a plain frozen dataclass).
- Make `status` round-trip verbatim (`'L'` / `'D'`) so future source dumps from the same `mos` reference DB can be loaded without any value transformation.
- Keep `PostgresInstrumentRepository` as the only writer; no new repository abstractions.

**Non-Goals:**

- No new indexes on `instrument` (the table is small and lookups are PK-only).
- No new repository methods (`list_all`, `get`, `upsert` remain; no `query_by_industry` / etc. — those can be ADDED Requirements later if needed).
- No changes to the other market-data tables (`kline_*`, `adjustment_factor`, `trade_calendar`) or the broker-data tables.
- No re-implementation of `kline_1m` / TimescaleDB concerns — out of scope.

## Decisions

1. **Edit `0001_initial.py` in place instead of adding a follow-up migration.**
   The user has accepted drop + recreate in dev. A second migration would force a backfill for the new NOT NULL columns (`type`, `is_t0`) against production-shaped data we don't have, and the schema history is still one revision deep anyway. **Alternative considered**: a `0002_expand_instrument.py` with `ALTER TABLE ADD COLUMN ... DEFAULT ...`. Rejected because it complicates downgrade ordering and offers no value while dev DBs can be wiped.

2. **Drop `price_tick`.**
   Source data is uniformly `0` (sampled all 7073 rows), so the field carries no information today and adding a `Numeric(20, 8)` column would force every caller to populate a meaningless zero. If xtquant / QMT later exposes a real tick size, we will reintroduce the column in a follow-up change with proper data.

3. **Keep `status` as `'L'` / `'D'` raw codes.**
   The current `InstrumentRepository` defaults to `'active'`, but downstream filtering ("list only active symbols") is more naturally expressed as `status == 'L'`. The single-letter vocabulary also matches what the source DB stores, eliminating the import script's `_STATUS_MAP`. **Alternative considered**: keep `'active'`/`'delisted'` and translate on the way in / out. Rejected because every external dump from the same DB would need the same translation and the mapping is fragile (any new code letter would silently land as `'active'`).

4. **Default `type` to empty string `""` in the dataclass / Postgres server default.**
   A handful of source rows (`type` not in the shown samples but possible in future dumps) may be NULL in DuckDB; Postgres enforces NOT NULL, so we let `'L' / 'D'` provide the same safety net for `status` and let `type` default to `''` server-side. The `upsert` helper still requires callers to pass a string — only the SQL column has a fallback. **Alternative considered**: make `type` nullable too. Rejected: every sample we have is non-NULL and the dataclass is more useful with a guaranteed string.

5. **Default `is_t0` to `false` both server-side and in the dataclass.**
   The DuckDB samples are all `False`, the dataclass mirrors the column, and downstream code that doesn't know about T+0 should see a safe default.

6. **`list_board` / `industry` / `area` are nullable VARCHAR.**
   The DuckDB samples show empty strings for many rows. We accept empty strings as valid (no special handling) and use nullable columns so callers can distinguish "unknown" (`NULL`) from "explicitly empty" (`""`) if they need to.

7. **Test fixtures updated to supply the new required fields.**
   The four `Instrument(...)` literals in tests are updated to include `type`, `is_t0` (and any of the optional fields they want to assert). This is required because the dataclass stays a frozen positional/keyword dataclass — there is no default for required fields.

## Risks / Trade-offs

- [Existing Postgres `instrument` rows conflict with new NOT NULL columns on `type` and `is_t0`.] → Dev workflow: `alembic downgrade base && alembic upgrade head` (or drop the table manually if foreign keys are involved). Documented in `proposal.md`.
- [Test fixtures that constructed `Instrument(...)` without `type` / `is_t0` will break.] → Fixed in the same change; tests are updated to supply the new fields. Listed as a task.
- [Callers downstream (strategy, etc.) that read `status` and compare to `'active'` will silently match nothing.] → No such callers exist in this repo today (only the import script and tests). Any future caller must use `'L'`. The spec delta records the new vocabulary.
- [Import script's `_STATUS_MAP` becomes dead code and is removed.] → Small deletion; behaviour is simpler.

## Migration Plan

1. Apply the code changes (`InstrumentORM`, `Instrument` dataclass, `_upsert`, `_to_record`, `0001_initial.py` DDL, `import_instrument_info.py`, test fixtures).
2. `uv run pytest` (without DB) — all unit tests pass.
3. `uv run ruff check src tests && uv run ruff format --check src tests` — clean.
4. `uv run mypy src` — strict passes.
5. `openspec validate --all --strict` — change passes validation.
6. Drop + recreate the Postgres `instrument` table on dev: connect with `psql` and `DROP TABLE instrument;`, then `uv run alembic upgrade head` to re-create with the new DDL.
7. Re-run the one-shot importer: `uv run python scripts/import_instrument_info.py` — 7073 rows land with the full column set.

## Open Questions

None. The two design-level questions that did exist (status vocabulary, `price_tick`) were resolved up-front in the `AskUserQuestion` round.