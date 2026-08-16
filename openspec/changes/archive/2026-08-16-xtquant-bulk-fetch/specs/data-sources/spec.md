## MODIFIED Requirements

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