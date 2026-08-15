## MODIFIED Requirements

### Requirement: Repository pattern for market data

The data layer SHALL expose one `Protocol` per market-data entity (K-lines, adjustment factors, trade calendar, instruments) and a Postgres-backed implementation for each. Each protocol method SHALL be the only supported call site for downstream modules (broker, execution, strategy). Direct SQL or ORM access from outside the data package SHALL NOT be necessary to read or write market data.

K-lines SHALL be stored in two physical tables, one per supported frequency: `kline_1d` for daily bars and `kline_1m` for 1-minute bars. The repository SHALL route `upsert_bars` / `get_bars` / `count` calls to the correct table based on the `interval` argument. The K-line `Protocol` interface and its method signatures SHALL NOT change.

`kline_1d` SHALL be a regular Postgres table. `kline_1m` SHALL be a TimescaleDB **hypertable** with `chunk_time_interval = 1 day` and a compression policy that compresses chunks older than 7 days, segmentby `symbol`, orderedby `ts`. `kline_1m` SHALL NOT have a retention policy (data is kept indefinitely). The hypertable's row-level `INSERT ... ON CONFLICT (symbol, ts) DO UPDATE` semantics and SELECT-by-range semantics SHALL be identical to a regular Postgres table from the application's perspective.

The `instrument` table SHALL store the full set of reference columns surfaced by the legacy `mos` reference dump: `symbol` (primary key), `name` (NOT NULL), `exchange` (NOT NULL), `type` (NOT NULL), `list_date` (NOT NULL), `delist_date` (nullable), `status` (NOT NULL, single-letter `L` for listed / `D` for delisted, default `L`), `list_board` (nullable), `industry` (nullable), `area` (nullable), and `is_t0` (NOT NULL BOOLEAN, default `false`). The corresponding `Instrument` dataclass SHALL expose every column except `symbol` so callers can filter by `industry` / `list_board` / `type` / `is_t0`. `status` SHALL NOT be remapped; values round-trip verbatim between the source dump and Postgres.

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