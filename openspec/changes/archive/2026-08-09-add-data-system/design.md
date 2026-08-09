## Context

`xtrade` already has a project skeleton (`src/xtrade/{cli,core,data,execution,risk,strategy}`), a Click CLI, and a JSON-backed pydantic-settings config with a single `postgres` section. The `data/` package is a placeholder. See [proposal.md](proposal.md) for motivation and the four capability specs ([data-market](specs/data-market/spec.md), [data-broker](specs/data-broker/spec.md), [data-sources](specs/data-sources/spec.md), [data-migrations](specs/data-migrations/spec.md), plus the [xtrade-config](specs/xtrade-config/spec.md) delta) for the behaviour contract. This document records the technical choices that the proposal intentionally leaves open.

## Goals / Non-Goals

**Goals:**
- Two-path storage contract enforced: time-series data via native `cursor.copy()` / `executemany + INSERT ... ON CONFLICT` on a borrowed `Connection`; small reference data (instruments) via ORM.
- One `Engine`, two facades: `get_engine()` / `get_session()` (context manager) / `get_connection()` (context manager). All three share the same engine.
- Repository Protocol for every entity so broker / execution / strategy modules can be tested against in-memory fakes.
- Alembic migrations live under `src/xtrade/data/migrations/`, configured to read the DSN from `Config`.
- Adjustment factors applied on the read path; raw prices preserved on disk.

**Non-Goals:**
- Real external data-source clients (Tushare / AKShare / WebSocket tick). Only `InMemoryMockSource` ships in this change.
- Real-time tick data, including asynchronous push.
- `BacktestRuns` / `Signals` metadata tables (deferred).
- Switching to async drivers (`asyncpg`). Driver is sync (`psycopg` v3) for this change; an async bridge can be added later as a separate capability if needed for the live-trading engine.
- Multi-database / multi-tenant sharding.
- Read replicas or query routing.

## Decisions

### Decision 1: One Engine, two facades

- **Choice**: `data.engine` exposes `create_engine()`, `get_engine()` (singleton, lazy), `get_session()` (yields `Session`, commits on success / rolls back on exception), `get_connection()` (yields `psycopg.Connection` from the underlying pool, plain commit/rollback).
- **Why**: SQLAlchemy's `Engine` already owns a connection pool — duplicating engines for "fast writes" vs "ORM" would double the pool size and confuse which pool a session actually came from. The single-engine / multi-facade pattern keeps the connection story simple while letting the call site choose its access pattern (Session vs raw Connection). The `data-market` repositories borrow `Connection`; the `data-broker` repositories borrow `Session`.
- **Alternatives considered**:
  - Two engines, one for each access pattern — rejected: doubles pool footprint, introduces ambiguity about which pool a future code path is hitting.
  - One Session-only API — rejected: Session + ORM unit-of-work is the wrong tool for streaming 100K-row upserts.

### Decision 2: Psycopg v3 (not psycopg2 / asyncpg)

- **Choice**: SQLAlchemy URL prefix `postgresql+psycopg`, runtime dep `psycopg[binary]>=3.1`.
- **Why**: psycopg v3 is the modern driver, supports `cursor.copy()` natively, has a SQLAlchemy 2.x dialect, and ships with type adaptation that matches PostgreSQL's reality. `psycopg2` would also work but is in maintenance mode. `asyncpg` is the right choice for an async stack, but the whole data layer is sync in this change (matches the rest of `xtrade` which has no async yet).
- **Alternatives considered**:
  - `psycopg2-binary` — rejected: legacy, no server-side binding improvements, slower than psycopg v3.
  - `asyncpg` + SQLAlchemy async — rejected: pulls async through the whole data layer before any consumer needs it.

### Decision 3: SQLAlchemy 2.x synchronous ORM for broker data; raw SQL for market data

- **Choice**: Broker-data tables (Order, Trade, Position, Account) get full ORM models in `xtrade.data.orm.broker`. Market-data tables (K-line, adjustment factor, trade calendar) get ORM models in `xtrade.data.orm.market` but the repositories use `Session.execute(text("..."))` / `cursor.copy()` / `pd.read_sql()` rather than the ORM unit-of-work.
- **Why**: The two-path rule from the proposal — broker data has few rows but rich relations (orders → trades, positions → symbols, account → time); market data has many rows but no relations. ORM unit-of-work (per-row `Session.add` + `flush()`) is fine for hundreds of inserts per second but disastrous for millions. K-line write speed is bounded by raw INSERT throughput, not by Python-level ORM bookkeeping, so we use psycopg directly.
- **Alternatives considered**:
  - ORM everywhere — rejected: would make `KLineRepository.upsert_bars(100K rows)` OOM-ish or minute-scale slow.
  - Pure SQL everywhere — rejected: business tables benefit from relationship mappings and constraint enforcement at the ORM layer; writing that by hand is churn.

### Decision 4: Native `cursor.copy()` for K-line bulk writes; `executemany + ON CONFLICT` for everything else

- **Choice**: `KLineRepository.upsert_bars` uses psycopg `cursor.copy_expert("COPY kline (...) FROM STDIN WITH (FORMAT csv)", stream)`. Adjustment-factor and trade-calendar repositories use `executemany` with `INSERT ... ON CONFLICT DO UPDATE` / `DO NOTHING`.
- **Why**: `COPY` is the fastest ingest path in Postgres — single round trip, server-side parsing, no per-row parsing overhead. It does not natively support upsert, so we use it only for append-mostly K-line loads where `(symbol, time, interval)` is a unique key and a duplicate row is rare (handled by separate `INSERT ... ON CONFLICT` for the upsert path). For smaller tables, `executemany` is fast enough and simpler.
- **Alternatives considered**:
  - `COPY` everywhere — rejected: COPY does not handle conflicts, so an upsert-capable table needs `INSERT ... ON CONFLICT`.
  - `pandas.DataFrame.to_sql(method="multi")` — rejected: to_sql generates INSERT statements and cannot leverage `COPY`; doesn't support `ON CONFLICT`.

### Decision 5: Adjustment factors are discrete; prices are raw

- **Choice**: K-line table stores raw `open/high/low/close/volume/amount/pre_close`. Adjustment-factor table stores one row per dividend / split event `(symbol, ex_date, factor)` where `factor` is the multiplicative adjustment. Backward / forward adjustment is computed at read time by `KLineRepository.get_bars(..., adjust="backward" | "forward" | "none")`.
- **Why**: Storing raw prices once means future adjustment policy changes (different normalisation windows, alternative cumulative schemes, dividend-yield overlays) require no migration. Storing pre-applied prices would require either four columns (`open_b`, `open_f`, `open_n`, ...) or a materialised view, both of which are larger and less flexible.
- **Alternatives considered**:
  - Pre-applied backward + forward + raw columns in K-line table — rejected: 3× storage, harder to extend.
  - Adjustment applied at source (in the DataSource) — rejected: the data layer must own the data semantics; sources can return raw.

### Decision 6: `DataFrame` shape for market-data reads; `dict[symbol, DataFrame]` for multi-symbol queries

- **Choice**: `get_bars(symbols=[...]) -> dict[str, DataFrame]`. Each DataFrame is indexed by `time` (ascending), columns `[open, high, low, close, volume, amount, pre_close]`, with `pre_close` populated from the previous bar's `close` at insert time.
- **Why**: Dict-of-DataFrame is the dominant pattern in Chinese quant libraries (`rqalpha`, `vnpy`, `mos_quant`); each symbol is a separate time series. Returning one long DataFrame with `(symbol, time)` MultiIndex would force every consumer to unstack / pivot. The dict shape lets consumers pick symbols individually and avoids the implicit index gymnastics.
- **Alternatives considered**:
  - Single long DataFrame with `MultiIndex` — rejected: most consumers want per-symbol slices, dict is simpler.
  - Per-row records (list of dicts) — rejected: pandas ecosystem dominates for time-series analysis.

### Decision 7: Repository Protocol + Postgres implementation, no in-memory broker repo yet

- **Choice**: Every entity has a `Protocol` and a `PostgresXRepository` implementation. `data-market` ships an `InMemoryMarketRepository` test helper inside the test module (not in `src/`) so unit tests do not require Postgres. `data-broker` does not ship an in-memory repo in this change; tests use SQLite via SQLAlchemy dialect swap.
- **Why**: In-memory broker repos are nice-to-have but the broker-data volume is small enough that SQLite test runs cover the semantics. Adding an in-memory broker repo would inflate the test surface without unlocking new behaviour. If a future capability (e.g. backtest dry-run) needs an in-memory broker repo, it can be added then.
- **Alternatives considered**:
  - Ship an in-memory broker repo now — rejected: YAGNI; SQLite is enough for unit tests.

### Decision 8: DataSource Protocol is structural, no base class

- **Choice**: `DataSource` is a `typing.Protocol`. `InMemoryMockSource` and any future source implement the four `fetch_*` methods. `isinstance(obj, DataSource)` works at runtime via `@runtime_checkable` if we want it; otherwise duck-typed.
- **Why**: Structural typing matches Python idioms and lets contributors drop in a `DataSource` without inheriting from a project class. No magic methods, no metaclass tricks.
- **Alternatives considered**:
  - Abstract base class — rejected: forces inheritance; we'd still need Protocol to type-hint third-party sources cleanly.

### Decision 9: `SourceRegistry` is a process-local singleton

- **Choice**: `SourceRegistry()` returns the same instance across the process. Default-populated with `InMemoryMockSource` named `"mock"`. Tests can `SourceRegistry().reset()` to start fresh.
- **Why**: Downstream code (broker / execution / future data-pull scripts) imports `SourceRegistry().get("tushare")` without needing to thread the source through every layer. Process-local keeps it simple; cross-process config (e.g. JSON file listing enabled sources) is a future concern.
- **Alternatives considered**:
  - Per-call registry (no singleton) — rejected: callers would have to plumb a registry through every layer, defeating the point.
  - `entry_points`-based plugin discovery — rejected: out of scope; this change ships one source.

### Decision 10: Alembic migrations live under `src/xtrade/data/migrations/`

- **Choice**: One `alembic.ini`-equivalent section in `pyproject.toml` (`[tool.alembic]`) pointing `script_location = src/xtrade/data/migrations`. `env.py` reads `Config.data.database.url` (with `XTRADE_DATA__DATABASE__URL` env override). A single `versions/0001_initial.py` migration creates every table from the ORM metadata via `Base.metadata.create_all`-equivalent operations, hand-rolled as `op.create_table` so downgrade works symmetrically.
- **Why**: Embedding Alembic in the data package keeps it discoverable alongside the code it migrates. Reading DSN from `Config` keeps the dev experience consistent (one place to set env vars / config). Hand-rolling `op.create_table` rather than `op.metadata.create_all` keeps the migration reviewable in code review and ensures downgrade is symmetric.
- **Alternatives considered**:
  - Top-level `migrations/` directory — rejected: scatters the data layer across the repo root.
  - `Base.metadata.create_all` only (no Alembic) — rejected: no version history, no rollback story.

### Decision 11: SQLAlchemy `URL.create()` normalises `postgresql://` → `postgresql+psycopg`

- **Choice**: `data.engine.create_engine(url)` inspects the URL; if it starts with `postgresql://` (no driver) and no other driver is specified, it is normalised to `postgresql+psycopg`.
- **Why**: Many users will paste a DSN they copied from a Postgres dashboard (e.g. `postgresql://user:pass@...`). Accepting the bare form and normalising it lowers friction; users who explicitly want a different driver (e.g. `postgresql+psycopc2`) keep their explicit prefix.
- **Alternatives considered**:
  - Reject bare `postgresql://` with an error — rejected: needless friction.
  - Always use psycopg silently even if user specified another driver — rejected: violates principle of least surprise.

### Decision 12: CI uses a Postgres service container

- **Choice**: A follow-up to this change will add `.github/workflows/ci.yml` with a Postgres 16 service container. This change does not add CI itself — the test suite is wired to skip data-layer tests when `XTRADE_TEST_DB_URL` is unset.
- **Why**: Keeps this change focused on the data layer; CI plumbing is its own capability.
- **Alternatives considered**:
  - SQLite-only tests — rejected: psycopg-specific features (`cursor.copy()`, `ON CONFLICT`, `JSONB`, partial indexes) are part of the contract.
  - Testcontainers — rejected: extra dep without value here.

## Risks / Trade-offs

- [Risk] `cursor.copy()` strings need to escape carefully. Naive string formatting can corrupt rows. → Mitigation: use `COPY ... FROM STDIN WITH (FORMAT csv)` so psycopg handles quoting, or use `COPY ... FROM STDIN WITH (FORMAT binary)` and feed a typed buffer. Tests include a row with embedded commas / quotes / null bytes to assert escaping works.
- [Risk] ORM session leak if a caller forgets the `with get_session()` context. → Mitigation: `get_session()` is the only supported entry point; using `Session(bind=engine)` directly is forbidden by code review convention. Tests assert the session is closed after exit.
- [Risk] Adjustment factors mis-computed if the time-series has gaps (e.g. factor is recorded for `2025-01-02` but no bar on that day). → Mitigation: factor is applied per-bar by mapping `bar.time.date()` → most-recent factor with `ex_date <= bar.time.date()`. ffill semantics documented in the spec.
- [Risk] Big `KLineRepository.upsert_bars` calls block the EventLoop if added to async code paths later. → Mitigation: documented as sync-only for this change. The future async bridge will run these in `asyncio.to_thread`.
- [Risk] Alembic `env.py` reading `Config.data.database.url` couples Alembic to the application config. → Mitigation: `env.py` accepts an `XTRADE_DATA__DATABASE__URL` env override and falls back to `Config`. This keeps both paths valid.
- [Risk] Mixing ORM models and raw SQL in the same module is confusing to newcomers. → Mitigation: clear file naming (`orm/market.py` for models, `market_data/kline.py` for repository code), and explicit docstrings explaining which path each function takes.
- [Risk] Postgres-only test runs mean CI needs a DB container; local dev without Postgres will skip data tests. → Mitigation: documented; tests are skipped cleanly when `XTRADE_TEST_DB_URL` is unset.
- [Risk] Adding `psycopg[binary]` and `alembic` increases wheel size / install time. → Mitigation: `psycopg[binary]` is the standard distribution; `alembic` is required (not extras), and only one-time installation cost.

## Migration Plan

This change is purely additive on the application side (`Config.data` is a new field with a default, so existing config files still load). Deployment steps:

1. `uv lock` + `uv sync` to pull new deps (`sqlalchemy`, `psycopg[binary]`, `alembic`, `pyarrow`).
2. Run `uv run alembic upgrade head` against the target database. The initial migration creates every table.
3. (Optional) `uv run xtrade config set data.database.url ...` to point at the production database; otherwise the default DSN is fine for local dev.
4. (Optional) seed the database via `from xtrade.data.sources.pump import pump` with `InMemoryMockSource` or a real source added by a future change.

Rollback:
- `uv run alembic downgrade base` removes every table.
- Removing the `data` section from the config file restores default behaviour.

No data backfill is required (this change is the first release).

## Open Questions

None. All decisions are settled (driver / engine / paths / adjustment semantics / source registry shape / migration location / in-memory mock as the only shipped source). Future capability changes (Tushare/AKShare adapters, real-time tick ingest, async driver bridge, `BacktestRuns`/`Signals` tables) will be addressed by their own dedicated changes.