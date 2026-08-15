## MODIFIED Requirements

### Requirement: Repository pattern for market data

The data layer SHALL expose one `Protocol` per market-data entity (K-lines, adjustment factors, trade calendar, instruments) and a Postgres-backed implementation for each. Each protocol method SHALL be the only supported call site for downstream modules (broker, execution, strategy). Direct SQL or ORM access from outside the data package SHALL NOT be necessary to read or write market data.

K-lines SHALL be stored in two physical tables, one per supported frequency: `kline_1d` for daily bars and `kline_1m` for 1-minute bars. The repository SHALL route `upsert_bars` / `get_bars` / `count` calls to the correct table based on the `interval` argument. The K-line `Protocol` interface and its method signatures SHALL NOT change.

`kline_1d` SHALL be a regular Postgres table. `kline_1m` SHALL be a TimescaleDB **hypertable** with `chunk_time_interval = 1 day` and a compression policy that compresses chunks older than 7 days, segmentby `symbol`, orderedby `ts`. `kline_1m` SHALL NOT have a retention policy (data is kept indefinitely). The hypertable's row-level `INSERT ... ON CONFLICT (symbol, ts) DO UPDATE` semantics and SELECT-by-range semantics SHALL be identical to a regular Postgres table from the application's perspective.

The `instrument` table SHALL store the full set of reference columns surfaced by the legacy `mos` reference dump: `symbol` (primary key), `name` (NOT NULL), `exchange` (NOT NULL), `type` (NOT NULL), `list_date` (NOT NULL), `delist_date` (nullable), `status` (NOT NULL, single-letter `L` for listed / `D` for delisted, default `L`), `list_board` (nullable), `industry` (nullable), `area` (nullable), and `is_t0` (NOT NULL BOOLEAN, default `false`). The corresponding `Instrument` dataclass SHALL expose every column except `symbol` so callers can filter by `industry` / `list_board` / `type` / `is_t0`. `status` SHALL NOT be remapped; values round-trip verbatim between the source dump and Postgres.

The `trade_calendar` table SHALL store one row per `(exchange, date, is_trading)`. `exchange` SHALL be NOT NULL VARCHAR(16) and the primary key SHALL be `(exchange, date)`. `is_trading` SHALL be NOT NULL BOOLEAN. The date-only facade methods `is_trading_day(d)` and `get_trading_days(start, end)` SHALL use the any-exchange rule: a date is reported as a trading day iff **any** `exchange` row marks it as such; `get_trading_days` returns the union of dates across exchanges, ascending and deduplicated.

#### Scenario: Caller asks for K-lines by symbols and time range

- **WHEN** a caller invokes `KLineRepository.get_bars(symbols, start, end, interval, adjust="none")` with `interval` in `{"1d", "1m"}`
- **THEN** the repository reads from the corresponding physical table (`kline_1d` for `interval="1d"`, the `kline_1m` hypertable for `interval="1m"`) and returns a `dict` keyed by `symbol` whose values are `pandas.DataFrame` indexed by `trade_date` (daily) or `ts` (1-minute), ascending and deduplicated, with columns `[open, high, low, close, volume, amount]`

#### Scenario: Caller upserts K-lines in bulk

- **WHEN** a caller invokes `KLineRepository.upsert_bars(df)` with a DataFrame whose `interval` column is uniformly `"1d"` or `"1m"` and whose columns include `[symbol, time, open, high, low, close, volume, amount]` (no `pre_close`)
- **THEN** the repository routes the rows to `kline_1d` (regular table) or `kline_1m` (hypertable) based on `interval`, writes them in batches of `Config.data.batch_size`, and returns the number of rows persisted

#### Scenario: Caller looks up an instrument

- **WHEN** a caller invokes `InstrumentRepository.get(symbol)`
- **THEN** the repository returns an `Instrument` record (or `None` if not found) with `symbol`, `name`, `exchange`, `type`, `list_date`, `delist_date`, `status`, `list_board`, `industry`, `area`, `is_t0`

#### Scenario: Instrument `status` round-trips the legacy single-letter codes

- **WHEN** a row is upserted with `status="L"` and then read back via `InstrumentRepository.get(symbol)`
- **THEN** the returned record's `status` equals `"L"` exactly; the repository SHALL NOT translate `'L'` to any other value
- **AND WHEN** a row is upserted with `status="D"` and then read back
- **THEN** the returned record's `status` equals `"D"` exactly

#### Scenario: Instrument upsert persists all reference columns

- **WHEN** a caller invokes `InstrumentRepository.upsert(record)` with `record.type="ETF"`, `record.industry="金融"`, `record.area="上海"`, `record.is_t0=False`
- **THEN** the row stored in Postgres `instrument` carries the same `type`, `industry`, `area`, `is_t0` values (verified by a subsequent `SELECT type, industry, area, is_t0 FROM instrument WHERE symbol = record.symbol`)
- **AND** the subsequent `InstrumentRepository.get(record.symbol)` call returns an `Instrument` with identical `type`, `industry`, `area`, `is_t0` values

#### Scenario: `kline_1m` is a hypertable with a 1-day chunk interval

- **WHEN** a developer inspects the Postgres server after `alembic upgrade head` succeeds
- **THEN** `SELECT hypertable_name FROM timescaledb_information.hypertables WHERE hypertable_name = 'kline_1m'` returns one row, and the hypertable's `chunk_time_interval` corresponds to a 1-day partition

#### Scenario: `kline_1m` chunks compress after 7 days

- **WHEN** a hypertable chunk's range_start is older than 7 days and the background compression job has run
- **THEN** the chunk's compression_status in `timescaledb_information.chunks` reads `Compressed`

#### Scenario: Hypertable write path is unchanged from the application

- **WHEN** `KLineRepository.upsert_bars(df)` is called with `interval="1m"` and 10 000 rows
- **THEN** the implementation uses the same `COPY` + `INSERT ... ON CONFLICT` flow against `kline_1m` and no TimescaleDB-specific API is invoked from application code

#### Scenario: Hypertable read path is unchanged from the application

- **WHEN** `KLineRepository.get_bars(symbols, start, end, interval="1m")` is called
- **THEN** the implementation issues a single `SELECT ... FROM kline_1m WHERE symbol = ANY(...) AND ts BETWEEN ... AND ...`; TimescaleDB's chunk pruning and compressed-chunk reads are transparent

#### Scenario: Trade calendar upsert is keyed by `(exchange, date)`

- **WHEN** a caller invokes `TradeCalendarRepository.upsert_days(df)` with `df` containing `[exchange, date, is_trading]` rows for two different exchanges on the same date
- **THEN** the repository inserts two distinct rows into `trade_calendar` keyed by `(exchange, date)`; calling `upsert_days` again with the same `(exchange, date, is_trading)` values SHALL NOT raise and SHALL overwrite only the matching pair (one upsert per exchange, not a global upsert)
- **AND** calling `upsert_days` with a DataFrame missing the `exchange` column SHALL raise `ValueError` mentioning the missing column

#### Scenario: `is_trading_day` uses the any-exchange rule

- **WHEN** `is_trading_day(d)` is called and the `trade_calendar` table contains a row `(exchange='BJ', date=d, is_trading=TRUE)` even if `('SH', d)` and `('SZ', d)` are absent or marked not trading
- **THEN** the function returns `True`
- **AND WHEN** no exchange row in `trade_calendar` marks `d` as trading
- **THEN** the function returns `False`

#### Scenario: `get_trading_days` returns the union across exchanges

- **WHEN** `get_trading_days(start, end)` is called and `trade_calendar` contains `('SH', d, TRUE)`, `('SZ', d, FALSE)`, `('BJ', d, FALSE)` for some date `d` in the window
- **THEN** `d` SHALL appear exactly once in the returned list (the any-exchange rule), ascending, deduplicated
- **AND** dates outside `[start, end]` SHALL NOT appear

### Requirement: Trade calendar exposes trading days and is-open predicate

The trade calendar repository SHALL provide `is_trading_day(date)` and `get_trading_days(start, end)`. Trading days are stored as one row per `(exchange, date)` with `is_trading: bool` so non-trading days are explicitly recorded and per-exchange divergence is preserved. The two facade methods SHALL collapse the per-exchange view with the any-exchange rule (a date is "a trading day" iff any exchange marks it as such).

#### Scenario: `is_trading_day` returns True on a stored trading day

- **WHEN** the trade calendar has `('SH', 2025-01-02, TRUE)` marked as trading
- **THEN** `TradeCalendarRepository.is_trading_day(date(2025, 1, 2))` returns `True`

#### Scenario: `get_trading_days` returns ascending business dates

- **WHEN** a caller invokes `TradeCalendarRepository.get_trading_days(date(2025, 1, 1), date(2025, 1, 10))`
- **THEN** the repository returns the list of dates within that range marked as trading on any exchange, in ascending order

### Requirement: Idempotent upsert with conflict key

Every market-data repository SHALL upsert on a stable conflict key:
- K-line daily (`kline_1d`): `(symbol, trade_date)` unique constraint.
- K-line 1-minute (`kline_1m`): `(symbol, ts)` unique constraint.
- Adjustment factors: `(symbol, ex_date)` unique constraint.
- Trade calendar: `(exchange, date)` unique constraint (composite primary key).
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