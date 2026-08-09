## 1. Dependencies and manifest

- [x] 1.1 Add runtime deps to `pyproject.toml`: `sqlalchemy>=2.0`, `psycopg[binary]>=3.1`, `alembic>=1.13`, `pyarrow>=15`.
- [x] 1.2 Add `[tool.alembic]` block to `pyproject.toml` with `script_location = "src/xtrade/data/migrations"`.
- [x] 1.3 Run `uv lock` + `uv sync` to regenerate `uv.lock` / `.venv`.
- [x] 1.4 Update `.env.example` with `XTRADE_DATA__DATABASE__URL` and `XTRADE_DATA__BATCH_SIZE` placeholders (commented).

## 2. Config integration

- [x] 2.1 Add `DataConfig` (with `DataDatabaseConfig` containing `url: str`) and `batch_size: int` to `src/xtrade/core/config.py`.
- [x] 2.2 Add `data: DataConfig = Field(default_factory=DataConfig)` to `Config`.
- [x] 2.3 Update `tests/test_config.py` to assert `data` defaults, round-trip, and `XTRADE_DATA__*` env overrides.
- [ ] 2.4 Update `README.md` to mention the new `data` config section.

## 3. Engine and ORM base

- [x] 3.1 Create `src/xtrade/data/__init__.py` exposing public API: `create_engine`, `get_engine`, `get_session`, `get_connection`, `Base`.
- [x] 3.2 Create `src/xtrade/data/engine.py`:
  - `_engine: Engine | None = None` module-level cache.
  - `create_engine(url: str) -> Engine` with DSN normalisation (`postgresql://` → `postgresql+psycopg`).
  - `get_engine(url: str | None = None) -> Engine` (lazy singleton, optional override).
  - `get_session() -> Iterator[Session]` context manager (commit on success / rollback on exception).
  - `get_connection() -> Iterator[psycopg.Connection]` context manager (raw Connection, commits / rolls back on success / exception).
- [x] 3.3 Create `src/xtrade/data/orm_base.py` with `Base = DeclarativeBase` and shared mixins (`TimestampMixin` for `created_at` / `updated_at`).
- [x] 3.4 Create `src/xtrade/data/orm/__init__.py` re-exporting models from `orm.market` and `orm.broker`.

## 4. ORM models — market data

- [x] 4.1 Create `src/xtrade/data/orm/market.py` with:
  - `KLineORM` (table `kline`, columns: `id BIGSERIAL PK`, `symbol TEXT`, `time TIMESTAMPTZ`, `interval TEXT`, `open/high/low/close NUMERIC`, `volume BIGINT`, `amount NUMERIC`, `pre_close NUMERIC NULL`, `created_at TIMESTAMPTZ`, unique constraint `(symbol, time, interval)`).
  - `AdjustmentFactorORM` (table `adjustment_factor`, columns: `id BIGSERIAL PK`, `symbol TEXT`, `ex_date DATE`, `factor NUMERIC`, unique constraint `(symbol, ex_date)`).
  - `TradeCalendarORM` (table `trade_calendar`, columns: `date DATE PK`, `is_trading BOOLEAN`).
  - `InstrumentORM` (table `instrument`, columns: `symbol TEXT PK`, `name TEXT`, `exchange TEXT`, `list_date DATE`, `delist_date DATE NULL`, `status TEXT`).

## 5. ORM models — broker data

- [x] 5.1 Create `src/xtrade/data/orm/broker.py` with:
  - `OrderORM` (table `order`, columns: `id BIGSERIAL PK`, `run_id TEXT`, `client_order_id TEXT`, `symbol TEXT`, `side TEXT`, `quantity NUMERIC`, `price NUMERIC NULL`, `status TEXT`, `created_at`, `updated_at`, unique constraint `(run_id, client_order_id)`).
  - `TradeORM` (table `trade`, columns: `id BIGSERIAL PK`, `order_id BIGINT FK -> order.id`, `symbol TEXT`, `price NUMERIC`, `quantity NUMERIC`, `fee NUMERIC`, `time TIMESTAMPTZ`).
  - `PositionORM` (table `position`, columns: `id BIGSERIAL PK`, `run_id TEXT`, `symbol TEXT`, `time TIMESTAMPTZ`, `quantity NUMERIC`, `avg_price NUMERIC`, unique constraint `(run_id, symbol, time)`).
  - `AccountORM` (table `account`, columns: `id BIGSERIAL PK`, `run_id TEXT`, `time TIMESTAMPTZ`, `cash NUMERIC`, `equity NUMERIC`, `margin NUMERIC`, unique constraint `(run_id, time)`).

## 6. Market-data repositories

- [x] 6.1 Create `src/xtrade/data/market_data/__init__.py` re-exporting repositories.
- [x] 6.2 Create `src/xtrade/data/market_data/kline.py`:
  - `KLineRepository` Protocol with `upsert_bars(df) -> int`, `get_bars(symbols, start, end, interval, adjust="none") -> dict[str, DataFrame]`, `count(symbol=None, interval=None) -> int`.
  - `PostgresKLineRepository` using `cursor.copy_expert("COPY kline (...) FROM STDIN WITH (FORMAT csv)", buffer)` for upsert; `pd.read_sql(query, engine, params=...)` for reads.
  - Adjustment computation in `get_bars` (load factors, build per-bar cumulative factor, multiply price columns; honour `adjust="backward"` / `"forward"` / `"none"`).
  - Validation: reject unknown `interval` with `ValueError`.
- [x] 6.3 Create `src/xtrade/data/market_data/adj_factor.py`:
  - `AdjustmentFactorRepository` Protocol with `upsert(df)`, `get(symbol, start, end) -> pd.DataFrame`.
  - `PostgresAdjustmentFactorRepository` using `executemany` with `INSERT ... ON CONFLICT (symbol, ex_date) DO UPDATE SET factor = EXCLUDED.factor`.
- [x] 6.4 Create `src/xtrade/data/market_data/trade_calendar.py`:
  - `TradeCalendarRepository` Protocol with `upsert_days(df)`, `is_trading_day(d) -> bool`, `get_trading_days(start, end) -> list[date]`.
  - `PostgresTradeCalendarRepository` using `executemany` with `INSERT ... ON CONFLICT (date) DO UPDATE SET is_trading = EXCLUDED.is_trading`.
- [x] 6.5 Create `src/xtrade/data/market_data/instrument.py`:
  - `InstrumentRepository` Protocol with `upsert(record)`, `get(symbol) -> Instrument | None`, `list_all() -> list[Instrument]`.
  - `PostgresInstrumentRepository` using ORM (Session) — allowed because instruments are a small reference table.
  - Plain dataclass `Instrument` exposed for callers (not an ORM model).

## 7. Broker-data repositories

- [x] 7.1 Create `src/xtrade/data/broker_data/__init__.py` re-exporting repositories.
- [x] 7.2 Create `src/xtrade/data/broker_data/order.py`:
  - `OrderRepository` Protocol with `create(record)`, `get(order_id)`, `list_by_run(run_id)`, `update_status(order_id, new_status)` (raises `OrderStateError` on disallowed transition).
  - `PostgresOrderRepository` using SQLAlchemy Session; allowed transitions defined as a class-level frozenset.
  - Plain dataclass `Order` exposed for callers.
- [x] 7.3 Create `src/xtrade/data/broker_data/trade.py`:
  - `TradeRepository` Protocol with `create(record)`, `list_by_order(order_id)`, `list_by_run(run_id)`.
  - `PostgresTradeRepository` using SQLAlchemy Session.
  - Plain dataclass `Trade` exposed for callers.
- [x] 7.4 Create `src/xtrade/data/broker_data/position.py`:
  - `PositionRepository` Protocol with `create(record)` (raises `DuplicateSnapshotError` on `(run_id, symbol, time)` collision), `list_by_run(run_id)`.
  - `PostgresPositionRepository` using SQLAlchemy Session.
  - Plain dataclass `Position` exposed for callers.
- [x] 7.5 Create `src/xtrade/data/broker_data/account.py`:
  - `AccountRepository` Protocol with `create(record)` (raises `DuplicateSnapshotError` on `(run_id, time)` collision), `list_by_run(run_id)`.
  - `PostgresAccountRepository` using SQLAlchemy Session.
  - Plain dataclass `Account` exposed for callers.

## 8. Data sources

- [x] 8.1 Create `src/xtrade/data/sources/__init__.py` re-exporting `DataSource`, `SourceRegistry`, `InMemoryMockSource`, `pump`.
- [x] 8.2 Create `src/xtrade/data/sources/base.py`:
  - `DataSource(Protocol)` with the four `fetch_*` methods (typed returns).
  - `class SourceRegistry:` with class-level singleton (`_instance`), `register`, `get`, `unregister`, `names`, `reset`.
  - Default-populated with `InMemoryMockSource` named `"mock"` on first instantiation.
- [x] 8.3 Create `src/xtrade/data/sources/mock_source.py`:
  - `class InMemoryMockSource:` storing `instruments`, `bars`, `adj_factors`, `calendar` in dicts / lists.
  - `fetch_*` methods return defensive copies (deep-copy DataFrames, list copies).
- [x] 8.4 Create `src/xtrade/data/sources/pump.py`:
  - `def pump(source, instrument_repo, kline_repo, adj_repo, calendar_repo, symbols=None) -> dict[str, int]` — pulls from source, writes into repos, returns counts.

## 9. Migrations

- [x] 9.1 Create `src/xtrade/data/migrations/env.py`:
  - Read DSN from `os.environ["XTRADE_DATA__DATABASE__URL"]` if set, else from `Config.load().data.database.url`.
  - `target_metadata = Base.metadata`.
  - Support both online and `--sql` offline modes.
- [x] 9.2 Create `src/xtrade/data/migrations/script.py.mako` (standard Alembic template).
- [x] 9.3 Create `src/xtrade/data/migrations/versions/0001_initial.py`:
  - `upgrade()` creates every table from `Base.metadata` via explicit `op.create_table` (so downgrade is symmetric).
  - Creates unique constraints and indexes from ORM models.
  - `downgrade()` drops every table.
  - No `INSERT` / `bulk_insert` calls (no seeding).

## 10. Tests — engine and config

- [x] 10.1 Create `tests/data/__init__.py`.
- [x] 10.2 Add `tests/data/test_engine.py` (skipped if `XTRADE_TEST_DB_URL` is unset): `create_engine` normalises `postgresql://`; `get_session` commits on success / rolls back on exception; `get_connection` returns a usable psycopg connection.
- [x] 10.3 Add `tests/test_config.py` data-section tests (unit, no DB): `Config.data` defaults, `XTRADE_DATA__*` env override, round-trip. (Covered by extending the existing `tests/test_config.py` rather than a new file.)

## 11. Tests — market data

- [x] 11.1 Add `tests/data/test_market_data.py` (skipped if `XTRADE_TEST_DB_URL` is unset):
  - `KLineRepository`: upsert round-trip, get_bars returns dict[str, DataFrame] indexed by time, adjustment `backward` / `forward` / `none`, unknown interval rejected, copy-expert path correctly escapes commas / quotes / null bytes.
  - `AdjustmentFactorRepository`: upsert round-trip, get by symbol and date range.
  - `TradeCalendarRepository`: upsert_days, is_trading_day, get_trading_days.
  - `InstrumentRepository`: upsert, get, list_all (uses ORM — works on the same test DB).

## 12. Tests — broker data

- [x] 12.1 Add `tests/data/test_broker_data.py` (skipped if `XTRADE_TEST_DB_URL` is unset):
  - `OrderRepository`: create, get, list_by_run, update_status allowed transition, update_status disallowed transition raises `OrderStateError`, duplicate `(run_id, client_order_id)` rejected.
  - `TradeRepository`: create linked to existing order, list_by_order, list_by_run.
  - `PositionRepository`: create, duplicate `(run_id, symbol, time)` raises `DuplicateSnapshotError`, list_by_run.
  - `AccountRepository`: create, duplicate `(run_id, time)` raises, list_by_run returns snapshots ordered by time.
  - Session rollback on exception (write a record, raise, assert no row was persisted).

## 13. Tests — sources

- [x] 13.1 Add `tests/data/test_sources.py` (unit, no DB):
  - `SourceRegistry` default-populates `"mock"`, register / get / unregister / names, unknown source raises `KeyError`.
  - `InMemoryMockSource` returns defensive copies (mutate returned DF, observe next call returns original).
  - `pump` writes source data into in-memory fakes and returns counts.

## 14. Tests — migrations

- [x] 14.1 Add `tests/data/test_migrations.py` (skipped if `XTRADE_TEST_DB_URL` is unset):
  - Apply `alembic upgrade head` to a fresh test database, query `information_schema.tables` for every expected table.
  - Run `alembic downgrade base`, assert no project table remains.
  - Run `alembic upgrade head --sql` to produce an offline script; assert every table appears in the SQL.

## 15. Final validation

- [x] 15.1 `uv run pytest` passes with all data tests skipped gracefully when `XTRADE_TEST_DB_URL` is unset, and passes with the DB tests active when the URL is set.
- [x] 15.2 `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src` clean.
- [x] 15.3 `openspec validate add-data-system --type change --strict` passes.