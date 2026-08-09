## Purpose

Provides durable storage for market reference data (bars, adjustment factors, trade calendar, instruments) with a DataFrame-shaped public interface and a strict two-path contract: high-throughput reads/writes for time-series data use native `Connection.cursor()` (`COPY` or `executemany` + `INSERT ... ON CONFLICT`); small reference tables use SQLAlchemy 2.x ORM. Adjustment (backward / forward) is computed on the read path from a discrete factor table, never stored pre-applied.

## ADDED Requirements

### Requirement: Repository pattern for market data

The data layer SHALL expose one `Protocol` per market-data entity (K-lines, adjustment factors, trade calendar, instruments) and a Postgres-backed implementation for each. Each protocol method SHALL be the only supported call site for downstream modules (broker, execution, strategy). Direct SQL or ORM access from outside the data package SHALL NOT be necessary to read or write market data.

#### Scenario: Caller asks for K-lines by symbols and time range

- **WHEN** a caller invokes `KLineRepository.get_bars(symbols, start, end, interval, adjust="none")`
- **THEN** the repository returns a `dict` keyed by `symbol` whose values are `pandas.DataFrame` indexed by `time` (ascending, deduplicated), with columns `[open, high, low, close, volume, amount, pre_close]` (where `pre_close` is optional)

#### Scenario: Caller upserts K-lines in bulk

- **WHEN** a caller invokes `KLineRepository.upsert_bars(df)` with a DataFrame of shape `(N, M)` containing columns `[symbol, time, interval, open, high, low, close, volume, amount, pre_close]`
- **THEN** the repository writes the rows in batches of `Config.data.batch_size` and returns the number of rows persisted

#### Scenario: Caller looks up an instrument

- **WHEN** a caller invokes `InstrumentRepository.get(symbol)`
- **THEN** the repository returns an `Instrument` record (or `None` if not found) with `symbol`, `name`, `exchange`, `list_date`, `delist_date`, `status`

### Requirement: Two-path storage contract

K-line, adjustment-factor, and trade-calendar repositories SHALL read and write via native `psycopg.Connection` operations (`pd.read_sql`, `cursor.copy`, `executemany` with `INSERT ... ON CONFLICT DO UPDATE`). They SHALL NOT use SQLAlchemy ORM unit-of-work semantics. Instrument repository MAY use ORM because its row count is small (thousands). All repositories SHALL share the same `Engine` from `data.engine.create_engine()`.

#### Scenario: K-line write uses COPY or executemany

- **WHEN** `KLineRepository.upsert_bars` is called with a 100 000-row DataFrame
- **THEN** the implementation issues a small bounded number of SQL statements (at most `ceil(N / batch_size)`) and does not instantiate ORM `Session` objects

#### Scenario: K-line read uses pandas, not Session

- **WHEN** `KLineRepository.get_bars` returns a DataFrame
- **THEN** the rows are read through `pandas.read_sql(...)` against the shared `Engine` (not via `session.execute(select(...))`)

#### Scenario: Trade calendar write uses COPY or executemany

- **WHEN** `TradeCalendarRepository.upsert_days(df)` is called with a year of trading days (~240 rows)
- **THEN** the implementation uses native cursor + `INSERT ... ON CONFLICT DO NOTHING`, not the ORM

#### Scenario: Instrument write uses ORM (allowed)

- **WHEN** `InstrumentRepository.upsert(instrument)` is called for a small batch
- **THEN** the implementation is allowed to use SQLAlchemy `Session` because instrument cardinality is small

### Requirement: Adjustment factors are discrete, prices are raw

The K-line table SHALL store raw (un-adjusted) prices. The adjustment-factor table SHALL store one row per dividend / split event with `[symbol, ex_date, factor]` and a cumulative factor per symbol per date SHALL be computed on the read path. No pre-applied `backward` or `forward` column SHALL exist in the K-line table.

#### Scenario: Backward adjustment returns normalized series

- **WHEN** a caller invokes `KLineRepository.get_bars(symbols, ..., adjust="backward")`
- **THEN** the returned DataFrame's price columns are multiplied by the cumulative factor divided by the earliest factor in the window, so the first bar's `close` equals the raw first `close` * 1.0

#### Scenario: Forward adjustment returns last-bar normalized series

- **WHEN** a caller invokes `KLineRepository.get_bars(symbols, ..., adjust="forward")`
- **THEN** the returned DataFrame's price columns are multiplied by the cumulative factor divided by the latest factor in the window, so the last bar's `close` equals the raw last `close`

#### Scenario: `adjust="none"` does not apply factors

- **WHEN** a caller invokes `KLineRepository.get_bars(symbols, ..., adjust="none")`
- **THEN** the returned DataFrame's price columns equal the raw stored values, regardless of any adjustment factors in the database

### Requirement: K-line interval is enumerated

The K-line repository SHALL accept `interval` as a string enum (`"1d"` for daily, `"1m"` for 1-minute, `"5m"` for 5-minute, `"15m"` for 15-minute, `"30m"` for 30-minute, `"60m"` for 60-minute). Unknown intervals SHALL raise `ValueError`.

#### Scenario: Unknown interval rejected

- **WHEN** a caller invokes any K-line method with `interval="7m"`
- **THEN** the repository raises `ValueError("unsupported interval '7m'")` and does not touch the database

### Requirement: Trade calendar exposes trading days and is-open predicate

The trade calendar repository SHALL provide `is_trading_day(date)` and `get_trading_days(start, end)`. Trading days are stored as one row per trading date with `is_trading: bool` so non-trading days are explicitly recorded.

#### Scenario: `is_trading_day` returns True on a stored trading day

- **WHEN** the trade calendar has `2025-01-02` marked as trading
- **THEN** `TradeCalendarRepository.is_trading_day(date(2025, 1, 2))` returns `True`

#### Scenario: `get_trading_days` returns ascending business dates

- **WHEN** a caller invokes `TradeCalendarRepository.get_trading_days(date(2025, 1, 1), date(2025, 1, 10))`
- **THEN** the repository returns the list of dates within that range marked as trading, in ascending order

### Requirement: Idempotent upsert with conflict key

Every market-data repository SHALL upsert on a stable conflict key:
- K-lines: `(symbol, time, interval)` unique constraint.
- Adjustment factors: `(symbol, ex_date)` unique constraint.
- Trade calendar: `(date)` unique constraint (single primary key).
- Instruments: `(symbol)` unique constraint.

Re-applying the same DataFrame SHALL leave row counts unchanged and SHALL update the columns (for K-lines / factors) or replace (for instruments / calendar).

#### Scenario: Re-applying the same K-line DataFrame is a no-op on counts

- **WHEN** a caller calls `upsert_bars(df)` twice with identical rows
- **THEN** the row count in the K-line table does not double, and the second call's row count returned equals the first

#### Scenario: Updating a K-line's close price works

- **WHEN** a caller calls `upsert_bars(df)` and then calls it again with a modified `close` for the same `(symbol, time, interval)` and identical other columns
- **THEN** the stored `close` reflects the latest write

### Requirement: No external IO from market-data repositories

Market-data repositories SHALL NOT make network calls. They read and write only the local PostgreSQL database. External data sources (Tushare, AKShare, CSV) are isolated behind the `DataSource` protocol in the `data-sources` capability; that capability owns the producer side that ultimately feeds these repositories.

#### Scenario: K-line repository never imports a data-source client

- **WHEN** a developer inspects the imports in `src/xtrade/data/market_data/kline.py`
- **THEN** no module from `xtrade.data.sources` is imported