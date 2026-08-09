## MODIFIED Requirements

### Requirement: Repository pattern for market data

The data layer SHALL expose one `Protocol` per market-data entity (K-lines, adjustment factors, trade calendar, instruments) and a Postgres-backed implementation for each. Each protocol method SHALL be the only supported call site for downstream modules (broker, execution, strategy). Direct SQL or ORM access from outside the data package SHALL NOT be necessary to read or write market data.

K-lines SHALL be stored in two physical tables, one per supported frequency: `kline_1d` for daily bars and `kline_1m` for 1-minute bars. The repository SHALL route `upsert_bars` / `get_bars` / `count` calls to the correct table based on the `interval` argument. The K-line `Protocol` interface and its method signatures SHALL NOT change.

#### Scenario: Caller asks for K-lines by symbols and time range

- **WHEN** a caller invokes `KLineRepository.get_bars(symbols, start, end, interval, adjust="none")` with `interval` in `{"1d", "1m"}`
- **THEN** the repository reads from the corresponding physical table (`kline_1d` for `interval="1d"`, `kline_1m` for `interval="1m"`) and returns a `dict` keyed by `symbol` whose values are `pandas.DataFrame` indexed by `trade_date` (daily) or `ts` (1-minute), ascending and deduplicated, with columns `[open, high, low, close, volume, amount]`

#### Scenario: Caller upserts K-lines in bulk

- **WHEN** a caller invokes `KLineRepository.upsert_bars(df)` with a DataFrame whose `interval` column is uniformly `"1d"` or `"1m"` and whose columns include `[symbol, time, open, high, low, close, volume, amount]` (no `pre_close`)
- **THEN** the repository routes the rows to `kline_1d` or `kline_1m` based on `interval`, writes them in batches of `Config.data.batch_size`, and returns the number of rows persisted

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

The K-line tables SHALL store raw (un-adjusted) prices and SHALL NOT store a `pre_close` column. The adjustment-factor table SHALL store one row per dividend / split event with `[symbol, ex_date, factor]` and a cumulative factor per symbol per date SHALL be computed on the read path. No pre-applied `backward` or `forward` column SHALL exist in any K-line table.

#### Scenario: Backward adjustment returns normalized series

- **WHEN** a caller invokes `KLineRepository.get_bars(symbols, ..., interval="1d", adjust="backward")`
- **THEN** the returned DataFrame's price columns are multiplied by the cumulative factor divided by the earliest factor in the window, so the first bar's `close` equals the raw first `close` * 1.0

#### Scenario: Forward adjustment returns last-bar normalized series

- **WHEN** a caller invokes `KLineRepository.get_bars(symbols, ..., interval="1m", adjust="forward")`
- **THEN** the returned DataFrame's price columns are multiplied by the cumulative factor divided by the latest factor in the window, so the last bar's `close` equals the raw last `close`

#### Scenario: `adjust="none"` does not apply factors

- **WHEN** a caller invokes `KLineRepository.get_bars(symbols, ..., adjust="none")`
- **THEN** the returned DataFrame's price columns equal the raw stored values, regardless of any adjustment factors in the database

### Requirement: K-line interval is enumerated

The K-line repository SHALL accept `interval` as a string enum restricted to `{"1d", "1m"}`. `interval="1d"` routes to table `kline_1d`; `interval="1m"` routes to table `kline_1m`. Unknown intervals SHALL raise `ValueError` before any database access.

#### Scenario: Unknown interval rejected

- **WHEN** a caller invokes any K-line method with `interval="5m"`
- **THEN** the repository raises `ValueError("unsupported interval '5m'")` and does not touch the database

#### Scenario: Routing uses the correct physical table

- **WHEN** a caller invokes `KLineRepository.upsert_bars(df)` with `interval="1d"`
- **THEN** the rows are written to `kline_1d` (verified by `SELECT count(*) FROM kline_1d` and `SELECT count(*) FROM kline_1m` reflecting the new rows only in the former)

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
- K-line daily (`kline_1d`): `(symbol, trade_date)` unique constraint.
- K-line 1-minute (`kline_1m`): `(symbol, ts)` unique constraint.
- Adjustment factors: `(symbol, ex_date)` unique constraint.
- Trade calendar: `(date)` unique constraint (single primary key).
- Instruments: `(symbol)` unique constraint.

Re-applying the same DataFrame SHALL leave row counts unchanged and SHALL update the columns (for K-lines / factors) or replace (for instruments / calendar).

#### Scenario: Re-applying the same K-line DataFrame is a no-op on counts

- **WHEN** a caller calls `upsert_bars(df)` twice with identical rows for the same `interval` (either `"1d"` or `"1m"`)
- **THEN** the row count in the corresponding K-line table (`kline_1d` or `kline_1m`) does not double, and the second call's row count returned equals the first

#### Scenario: Updating a K-line's close price works

- **WHEN** a caller calls `upsert_bars(df)` for `interval="1d"` and then calls it again with a modified `close` for the same `(symbol, trade_date)` and identical other columns
- **THEN** the stored `close` in `kline_1d` reflects the latest write

#### Scenario: Re-applying the same 1-minute K-line DataFrame is a no-op on counts

- **WHEN** a caller calls `upsert_bars(df)` twice with identical rows for `interval="1m"`
- **THEN** the row count in `kline_1m` does not double, and the second call's row count returned equals the first

### Requirement: No external IO from market-data repositories

Market-data repositories SHALL NOT make network calls. They read and write only the local PostgreSQL database. External data sources (Tushare, AKShare, CSV) are isolated behind the `DataSource` protocol in the `data-sources` capability; that capability owns the producer side that ultimately feeds these repositories.

#### Scenario: K-line repository never imports a data-source client

- **WHEN** a developer inspects the imports in `src/xtrade/data/market_data/kline.py`
- **THEN** no module from `xtrade.data.sources` is imported

## REMOVED Requirements

### Requirement: Single K-line table with `interval` column and `pre_close`

**Reason**: The K-line table is split into per-frequency physical tables (`kline_1d`, `kline_1m`). The `interval` column becomes implicit (encoded by the table name) and the `pre_close` column had no producer or consumer and is removed. Adjustment remains fully recoverable from the `adj_factor` table on the read path.

**Migration**: Callers that previously wrote `pre_close` must drop that column from their input DataFrames (the repository WILL reject it implicitly because it is no longer COPY'ed). Callers that previously read `pre_close` from `get_bars` output must compute the previous-bar value themselves (e.g. `df["close"].shift(1)`) or query `adj_factor` directly. Callers that previously relied on `df.index.name == "time"` now see `"trade_date"` for daily and `"ts"` for 1-minute.
