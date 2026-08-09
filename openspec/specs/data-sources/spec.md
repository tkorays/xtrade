# Capability: data-sources

## Purpose

Provides a producer-side abstraction for fetching market reference data from external / local sources and writing it into the durable repositories defined in `data-market`. Sources are pluggable via a `DataSource` Protocol and registered through a `SourceRegistry`, so new providers (Tushare, AKShare, CSV, Parquet) can be added by future capability changes without modifying this one.

## Requirements

### Requirement: `DataSource` Protocol

The data layer SHALL define a `DataSource` Protocol with methods for fetching market reference data:
- `fetch_instruments() -> list[Instrument]`
- `fetch_bars(symbol, start, end, interval) -> pd.DataFrame`
- `fetch_adjust_factors(symbol, start, end) -> pd.DataFrame`
- `fetch_trade_calendar(start, end) -> pd.DataFrame`

Any class implementing these methods is a valid `DataSource`. The Protocol SHALL NOT require a base class or inheritance, only structural typing.

#### Scenario: A class with matching methods satisfies the Protocol

- **WHEN** a developer defines a class with the four `fetch_*` methods returning the documented types
- **THEN** `isinstance(obj, DataSource)` is `True` at runtime check time, and static type checkers accept it without inheritance

#### Scenario: A missing method fails the Protocol

- **WHEN** a developer defines a class missing one of the `fetch_*` methods
- **THEN** static type checkers reject the class as not satisfying `DataSource`

### Requirement: `SourceRegistry`

The data layer SHALL expose a `SourceRegistry` with `register(name: str, source: DataSource)`, `get(name: str) -> DataSource`, `unregister(name: str)`, and `names() -> list[str]`. The registry SHALL default-populate one in-memory mock source named `"mock"` on first import.

#### Scenario: Default `mock` source is available

- **WHEN** a developer imports `xtrade.data.sources`
- **THEN** `SourceRegistry().get("mock")` returns a working `InMemoryMockSource` and `SourceRegistry().names()` includes `"mock"`

#### Scenario: Register a custom source

- **WHEN** a developer calls `SourceRegistry().register("my-csv", my_csv_source)`
- **THEN** `SourceRegistry().get("my-csv")` returns `my_csv_source` and `SourceRegistry().names()` includes `"my-csv"`

#### Scenario: Unknown source raises

- **WHEN** a developer calls `SourceRegistry().get("missing")`
- **THEN** a `KeyError` is raised

### Requirement: `InMemoryMockSource`

The data layer SHALL provide an in-memory `InMemoryMockSource` whose data is supplied at construction time (no network / disk I/O). It is the only `DataSource` implementation shipped by this capability; real sources are added by future changes.

#### Scenario: Mock source returns seeded data verbatim

- **WHEN** a developer constructs `InMemoryMockSource(instruments=[...], bars={"A": df}, adj_factors={"A": df}, calendar=df)` and calls `fetch_instruments()`
- **THEN** the seeded `instruments` list is returned

#### Scenario: Mock source returns a copy, not a reference

- **WHEN** a developer mutates the DataFrame returned by `fetch_bars`
- **THEN** the next call to `fetch_bars` returns the original DataFrame unchanged (the source returns defensive copies)

### Requirement: Source-to-repository handoff

The data layer SHALL expose a high-level helper that pulls from a named `DataSource` and writes into the durable repositories. The helper SHALL be implemented in `xtrade.data.sources.pump` and SHALL accept a `DataSource`, the four repositories, and an optional list of `symbols` (defaulting to `source.fetch_instruments()`).

#### Scenario: Pump instruments into repository

- **WHEN** a developer calls `pump(source, instrument_repo, kline_repo, adj_repo, calendar_repo, symbols=None)`
- **THEN** instruments, bars, adjustment factors, and trade calendar rows from the source are written into their respective repositories, and the helper returns a dict of inserted counts per entity

#### Scenario: Pump is idempotent on repeated runs

- **WHEN** a developer calls `pump(...)` twice with the same source
- **THEN** row counts in the repositories do not double (upsert semantics from the repositories apply)

### Requirement: No producer-side IO outside `DataSource`

Modules outside `xtrade.data.sources` SHALL NOT import a real data-source client (Tushare, AKShare, requests, websockets). The data layer reads/writes only the local PostgreSQL database.

#### Scenario: Market and broker repositories do not import source clients

- **WHEN** a developer inspects the imports in `src/xtrade/data/market_data/` and `src/xtrade/data/broker_data/`
- **THEN** no module named `tushare`, `akshare`, `requests`, `websockets`, or `httpx` is imported