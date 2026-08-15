## Why

`xtrade.data.market_data.instrument.Instrument` and the underlying Postgres `instrument` table currently store only 6 columns (`symbol / name / exchange / list_date / delist_date / status`), but the legacy `mos` DuckDB dump at `~/.mos/data/instrument_info.db` carries 12 columns including `type`, `list_board`, `industry`, `area`, `is_t0`, `price_tick`. The one-shot import script we just added has to drop everything except those 6 fields, which loses the sector / board classification data the strategy layer will want when filtering the universe. We need to widen the `instrument` table (and its ORM/dataclass surfaces) so the import can land the full payload and downstream code can query by `industry` / `list_board` / `type`.

## What Changes

- **Expand `InstrumentORM`** (and the matching Postgres `instrument` DDL in `0001_initial.py`) to include `type` (NOT NULL), `list_board`, `industry`, `area` (all nullable VARCHAR), and `is_t0` (NOT NULL BOOLEAN, default `false`). `price_tick` is dropped (the source data is uniformly `0`).
- **Switch `status` to the legacy single-letter codes** (`'L'` for listed, `'D'` for delisted, default `'L'`) to round-trip the DuckDB values verbatim instead of mapping to `'active'`/`'delisted'`. This removes the `_STATUS_MAP` in the import script.
- **Widen `Instrument` dataclass** with the new fields so callers see the full payload.
- **Rewrite `PostgresInstrumentRepository.upsert`** to set every column (it is still keyed on `symbol` PK and remains idempotent).
- **Rewrite the `fetch_records` projection in `scripts/import_instrument_info.py`** to load every new column; the status mapping helper is removed.
- **Update test fixtures** (`tests/data/test_market_data.py`, `tests/data/test_sources.py`, `tests/core/test_market_data.py`) that hand-construct an `Instrument`: change `status="active"` → `status="L"` and supply the new required fields (`type`, `is_t0`).
- **BREAKING** for callers that read `instrument.status` and expect `'active'`/`'delisted'`: only the import script and three test fixtures are affected inside this repo. External callers (none yet) would need to switch to `'L'`/`'D'`.
- **BREAKING** for the Postgres `instrument` table itself: existing rows would be incompatible because new NOT NULL columns (`type`, `is_t0`) cannot be added without a default or backfill. Acceptable in dev per the change author (drop + recreate is fine).

## Capabilities

### New Capabilities

- _none_

### Modified Capabilities

- `data-market`: the requirement that describes `InstrumentRepository.get(symbol)` must list the new field set (`type`, `list_board`, `industry`, `area`, `is_t0`) and the new `status` vocabulary (`'L'`/`'D'`).
- `data-migrations`: the `0001_initial` migration's `instrument` DDL must reflect the widened column list, new NOT NULL constraints, and the new `status` default.

## Impact

- **Code**: `src/xtrade/data/orm/market.py` (`InstrumentORM`), `src/xtrade/data/market_data/instrument.py` (`Instrument`, `_to_record`, `PostgresInstrumentRepository._upsert`), `src/xtrade/data/migrations/versions/0001_initial.py` (replace the `op.create_table("instrument", ...)` block and the corresponding `op.drop_table` in downgrade).
- **Script**: `scripts/import_instrument_info.py` — drop the status map, project every new column, remove the `'L'`→`'active'` translation.
- **Tests**: `tests/data/test_market_data.py`, `tests/data/test_sources.py`, `tests/core/test_market_data.py` — update hand-built `Instrument(...)` literals.
- **Specs**: `openspec/specs/data-market/spec.md`, `openspec/specs/data-migrations/spec.md` — delta sections listing the new fields and the new `status` vocabulary.
- **Database**: existing Postgres `instrument` rows would conflict with new NOT NULL columns; user accepts drop + recreate in dev (`alembic downgrade base && alembic upgrade head` after wiping the table manually if needed).
- **External consumers**: none inside this repo. The change is therefore contained.
- **No new dependencies**; SQLAlchemy types already cover `VARCHAR` / `BOOLEAN` / `Date`.