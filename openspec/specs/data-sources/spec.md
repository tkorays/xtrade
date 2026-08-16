# Capability: data-sources

## Purpose

Provides a producer-side abstraction for fetching market reference data from external / local sources and writing it into the durable repositories defined in `data-market`. Sources are pluggable via a `DataSource` Protocol and registered through a `SourceRegistry`, so new providers (Tushare, AKShare, CSV, Parquet) can be added by future capability changes without modifying this one.

## Requirements

### Requirement: `DataSource` Protocol

The data layer SHALL define a `DataSource` Protocol with methods for fetching market reference data:
- `fetch_instruments() -> list[Instrument]`
- `fetch_bars(symbol, start, end, interval) -> pd.DataFrame` — **MAY** be a thin wrapper around `fetch_bars_bulk`.
- `fetch_bars_bulk(symbols, start, end, interval) -> pd.DataFrame` — **REQUIRED**. Returns a long-format DataFrame with columns `time, symbol, open, high, low, close, volume, amount` (column order not guaranteed) covering **all** `symbols` that produced any rows in `[start, end]` at `interval`. Symbols that produced no rows are omitted from the result; the caller SHALL treat absence as "no data in window".
- `fetch_adjust_factors(symbol, start, end) -> pd.DataFrame`
- `fetch_trade_calendar(start, end) -> pd.DataFrame`

Any class implementing the above methods (including the bulk method) is a valid `DataSource`. The Protocol SHALL NOT require a base class or inheritance, only structural typing.

#### Scenario: A class with matching methods satisfies the Protocol

- **WHEN** a developer defines a class with all five `fetch_*` methods (the four legacy ones plus `fetch_bars_bulk`) returning the documented types
- **THEN** `isinstance(obj, DataSource)` is `True` at runtime check time, and static type checkers accept it without inheritance

#### Scenario: A missing method fails the Protocol

- **WHEN** a developer defines a class missing one of the `fetch_*` methods (including `fetch_bars_bulk`)
- **THEN** static type checkers reject the class as not satisfying `DataSource`

#### Scenario: A class missing `fetch_bars_bulk` fails the Protocol

- **WHEN** a developer defines a class missing `fetch_bars_bulk`
- **THEN** static type checkers reject the class as not satisfying `DataSource`

#### Scenario: `fetch_bars_bulk` returns one wide-format frame

- **WHEN** a developer calls `source.fetch_bars_bulk(["000001.SZ", "000002.SZ"], start, end, "1d")` and MiniQMT returns 5 rows for `000001.SZ` and 0 rows for `000002.SZ`
- **THEN** the returned DataFrame contains 5 rows (long format, indexed by `(time, symbol)` or with `symbol` as a column) covering `000001.SZ` only; `000002.SZ` is absent from the result

#### Scenario: `fetch_bars_bulk` raises on MiniQMT rejection

- **WHEN** MiniQMT returns a non-zero status from `download_history_data2` or `get_market_data_ex`, or raises any exception
- **THEN** `fetch_bars_bulk` SHALL raise the original exception (or a wrapped exception with the same `__cause__`); it SHALL NOT silently return an empty frame

### Requirement: `SourceRegistry` default registration

The `SourceRegistry` SHALL default-register two `DataSource` implementations on first instantiation:
- `"mock"` — the existing `InMemoryMockSource`.
- `"xtquant"` — a new `XtQuantDataSource`, registered **lazily**: the registry SHALL attempt `import xtquant.xtdata` and SHALL skip the registration silently (without raising) when the import fails.

After both registrations succeed, `SourceRegistry().names()` SHALL include both names. After a failed `xtquant` import, `SourceRegistry().names()` SHALL include only `"mock"` (and any additional sources the caller has registered).

#### Scenario: Both defaults registered when xtquant is importable

- **WHEN** a developer imports `xtrade.data.sources` on a machine where `import xtquant` succeeds
- **THEN** `SourceRegistry().get("xtquant")` returns an `XtQuantDataSource` and `SourceRegistry().get("mock")` returns an `InMemoryMockSource`; `SourceRegistry().names()` contains both names

#### Scenario: `xtquant` is absent when the package is missing

- **WHEN** a developer imports `xtrade.data.sources` on a machine where `import xtquant` raises `ModuleNotFoundError`
- **THEN** `SourceRegistry().get("xtquant")` raises `KeyError` listing the known sources (which include `"mock"` but exclude `"xtquant"`); `SourceRegistry` itself does not re-raise the `ModuleNotFoundError`

#### Scenario: `xtquant` is re-registered after a successful install

- **WHEN** `xtquant` is not installed at first import, then installed, then `SourceRegistry().reset()` is called and the registry is reconstructed
- **THEN** the new `SourceRegistry().get("xtquant")` returns an `XtQuantDataSource` (the lazy import succeeds on the new construction)

#### Scenario: Register a custom source

- **WHEN** a developer calls `SourceRegistry().register("my-csv", my_csv_source)`
- **THEN** `SourceRegistry().get("my-csv")` returns `my_csv_source` and `SourceRegistry().names()` includes `"my-csv"`

#### Scenario: Unknown source raises

- **WHEN** a developer calls `SourceRegistry().get("missing")`
- **THEN** a `KeyError` is raised

### Requirement: `InMemoryMockSource`

The data layer SHALL provide an in-memory `InMemoryMockSource` whose data is supplied at construction time (no network / disk I/O). It is the only `DataSource` implementation shipped by this capability; real sources are added by future changes.

The `InMemoryMockSource.fetch_bars_bulk` implementation SHALL iterate over the supplied symbols and concat the per-symbol seeded frames (filtering out empty ones), producing the same long-format result as `XtQuantDataSource.fetch_bars_bulk`.

#### Scenario: Mock source returns seeded data verbatim

- **WHEN** a developer constructs `InMemoryMockSource(instruments=[...], bars={"A": df}, adj_factors={"A": df}, calendar=df)` and calls `fetch_instruments()`
- **THEN** the seeded `instruments` list is returned

#### Scenario: Mock `fetch_bars_bulk` aggregates per-symbol frames

- **WHEN** `InMemoryMockSource` is constructed with `bars={"A": df_a, "B": df_b}` and the caller invokes `fetch_bars_bulk(["A", "B", "C"], start, end, "1d")` where `df_c` is unset
- **THEN** the returned DataFrame equals `pd.concat([df_a, df_b])` (long format, with a `symbol` column added by the implementation); `C` is absent

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