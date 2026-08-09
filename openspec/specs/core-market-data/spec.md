# Capability: core-market-data

## Purpose

Provides a read-only facade in `xtrade.core.market_data` for business-layer code (strategy, execution, backtest) to query market reference data — K-line bars, instrument metadata, and the trade calendar — without taking a direct dependency on `xtrade.data` repositories. The facade is a thin, dependency-injected wrapper around the existing `xtrade.data` repositories and adds no new storage behavior.

## Requirements

### Requirement: Read-only facade in `xtrade.core.market_data`

The `xtrade.core.market_data` module SHALL expose four free functions: `get_bars`, `get_instrument`, `is_trading_day`, and `get_trading_days`. Each function SHALL only read from the market-data layer; none of them SHALL expose any write operation (no `upsert`, no `delete`, no `truncate`). The module SHALL NOT re-export through `xtrade.core.__init__`; callers MUST import explicitly from `xtrade.core.market_data`.

#### Scenario: Caller reads K-line bars for one symbol

- **WHEN** a caller invokes `get_bars("000001.SZ", start=date(2025, 1, 1), end=date(2025, 1, 10), interval="1d")`
- **THEN** the function returns a single `pandas.DataFrame` indexed by `time` (ascending, deduplicated) with columns `[open, high, low, close, volume, amount, pre_close]`, and the underlying K-line repository is invoked with `["000001.SZ"]` and `adjust="none"`

#### Scenario: Caller reads K-line bars for multiple symbols

- **WHEN** a caller invokes `get_bars(["000001.SZ", "600000.SH"], start=..., end=..., interval="1d")`
- **THEN** the function returns a `dict[str, pandas.DataFrame]` keyed by symbol, each value indexed by `time`, and every requested symbol appears as a key (empty `DataFrame` if no rows)

#### Scenario: Caller reads K-line bars with backward adjustment

- **WHEN** a caller invokes `get_bars(symbols, start=..., end=..., interval="1d", adjust="backward")`
- **THEN** the function delegates to the underlying K-line repository with `adjust="backward"` and returns the prices as normalized by the repository's backward-adjustment logic

#### Scenario: Default adjustment is `none`

- **WHEN** a caller invokes `get_bars(symbols, start=..., end=..., interval="1d")` without specifying `adjust`
- **THEN** the function delegates to the underlying K-line repository with `adjust="none"` and the returned prices equal the raw stored values

#### Scenario: Unknown interval is rejected before IO

- **WHEN** a caller invokes `get_bars(symbols, start=..., end=..., interval="7m")`
- **THEN** the function raises `ValueError("unsupported interval '7m'")` and does not open a database connection

#### Scenario: Caller looks up an instrument

- **WHEN** a caller invokes `get_instrument("000001.SZ")` and the instrument exists
- **THEN** the function returns an `Instrument` record with `symbol`, `name`, `exchange`, `list_date`, `delist_date`, `status`

#### Scenario: Caller looks up a missing instrument

- **WHEN** a caller invokes `get_instrument("UNKNOWN")` and no row exists
- **THEN** the function returns `None` (does not raise)

#### Scenario: Caller asks whether a date is a trading day

- **WHEN** a caller invokes `is_trading_day(date(2025, 1, 2))` and the trade calendar marks it as trading
- **THEN** the function returns `True`; otherwise `False`

#### Scenario: Caller lists trading days in a window

- **WHEN** a caller invokes `get_trading_days(date(2025, 1, 1), date(2025, 1, 10))`
- **THEN** the function returns a `list[date]` of all dates in the range marked as trading, in ascending order

#### Scenario: Empty symbol collection returns empty result

- **WHEN** a caller invokes `get_bars([], start=..., end=..., interval="1d")`
- **THEN** the function returns `{}` and does not open a database connection

### Requirement: Facade does not cache results

The facade SHALL NOT cache any query result. Each invocation of `get_bars`, `get_instrument`, `is_trading_day`, or `get_trading_days` SHALL perform a fresh read through the underlying repository. The Engine itself MAY be cached (via `xtrade.data.engine.get_engine()`), but constructed Repository instances and returned DataFrames SHALL NOT be cached by the facade.

#### Scenario: Two calls return independent DataFrames

- **WHEN** a caller invokes `get_bars("000001.SZ", ...)` twice in succession
- **THEN** the two returned `DataFrame` instances are distinct objects (mutating one does not affect the other), and the underlying repository is invoked on each call

### Requirement: Facade does not own the database engine

The facade SHALL NOT construct or own a `sqlalchemy.Engine`. It SHALL obtain the engine through `xtrade.data.engine.get_engine()` (the existing module-level singleton) and SHALL obtain `Config.data.batch_size` through `xtrade.core.config.get_config()`. No new module-level mutable state SHALL be introduced in `xtrade.core`.

#### Scenario: Engine is reused across calls

- **WHEN** a caller invokes `get_bars` and then `get_instrument` in the same process
- **THEN** both calls operate against the same `Engine` instance returned by `xtrade.data.engine.get_engine()`

#### Scenario: Engine is reset and a new engine is picked up

- **WHEN** a test calls `xtrade.data.engine.reset_engine()` and then invokes `get_bars(...)`
- **THEN** the facade re-resolves the engine via `get_engine()` and operates against the newly installed engine

### Requirement: Facade is testable via factory injection

The facade SHALL construct its underlying repositories through three module-level factory functions (`_build_kline_repo`, `_build_instrument_repo`, `_build_trade_calendar_repo`). Tests SHALL be able to replace any of these factories via `monkeypatch.setattr` on the facade module. The facade SHALL NOT depend on the concrete `Postgres*Repository` class directly inside its public functions.

#### Scenario: Test replaces the K-line factory

- **WHEN** a test does `monkeypatch.setattr("xtrade.core.market_data._build_kline_repo", lambda: fake_repo)`
- **THEN** `get_bars(...)` delegates to `fake_repo.get_bars(...)` and returns its result

### Requirement: Facade does not perform external IO

The facade SHALL NOT make network calls, file IO outside the configured database, or any other external IO. All reads SHALL go through the underlying `xtrade.data` repositories, which are themselves bound to the PostgreSQL database.

#### Scenario: Facade does not import a data-source client

- **WHEN** a developer inspects `src/xtrade/core/market_data.py`
- **THEN** no module from `xtrade.data.sources` is imported

### Requirement: No new public dependencies

The facade SHALL depend only on `xtrade.data`, `xtrade.core.config`, `pandas`, and the Python standard library. No new third-party package SHALL be added to `pyproject.toml` for this change.

#### Scenario: No new dependency appears in `pyproject.toml`

- **WHEN** a developer inspects `pyproject.toml` after the change is applied
- **THEN** the `dependencies` list is unchanged from before the change (no new package is added)
