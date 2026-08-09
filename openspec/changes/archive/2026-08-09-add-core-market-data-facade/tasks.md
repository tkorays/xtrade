## 1. Implement `xtrade.core.market_data` module

- [x] 1.1 Create `src/xtrade/core/market_data.py` with module docstring stating "read-only facade over `xtrade.data`" and `__all__` listing the four public functions.
- [x] 1.2 Add `_build_kline_repo()`, `_build_instrument_repo()`, `_build_trade_calendar_repo()` factories: each reads `Config.data.batch_size` (only K-line needs it) and returns a fresh `Postgres*Repository` instance; import `data` modules lazily inside the factories to avoid circular imports.
- [x] 1.3 Implement `get_bars(symbols, start, end, interval, adjust="none")`: normalize `symbols` (accept `str` or `Iterable[str]`), guard empty collection, call `PostgresKLineRepository.get_bars` with `adjust` default, return `DataFrame` for single symbol and `dict[str, DataFrame]` for iterable. Interval is validated in the facade before any IO so the repo is never built for unknown intervals.
- [x] 1.4 Implement `get_instrument(symbol) -> Instrument | None` delegating to `PostgresInstrumentRepository.get`.
- [x] 1.5 Implement `is_trading_day(d: date) -> bool` and `get_trading_days(start: date, end: date) -> list[date]` delegating to `PostgresTradeCalendarRepository`.
- [x] 1.6 Full type annotations on every public function (PEP 604 unions, `from __future__ import annotations`); signatures must type-check under `mypy strict`.

## 2. Tests for the facade

- [x] 2.1 Create `tests/core/test_market_data.py` (new `tests/core/` package, including `__init__.py`).
- [x] 2.2 In the test file, replace the three `_build_*` factories with in-memory fakes via `monkeypatch.setattr`; no real database connection.
- [x] 2.3 Add tests covering: single-symbol `get_bars` returns `DataFrame`; multi-symbol `get_bars` returns `dict`; empty iterable returns `{}`; `adjust="backward"` is forwarded; default `adjust` is `"none"`; unknown interval raises `ValueError` and never touches the fake repo; `get_instrument` returns the record; `get_instrument` returns `None` for miss; `is_trading_day`; `get_trading_days`.
- [x] 2.4 Add a test asserting two consecutive `get_bars` calls return distinct `DataFrame` objects (no caching).
- [x] 2.5 Add a test asserting the facade does not own an engine: no `_engine` slot; `get_engine` reference is the same callable as `xtrade.data.engine.get_engine`; engine reset is honoured.

## 3. Quality gates

- [x] 3.1 `uv run pytest tests/core/test_market_data.py` passes; full `uv run pytest` still passes (90 passed, 26 skipped integration tests).
- [x] 3.2 `uv run ruff check src/xtrade/core/market_data.py tests/core` and `uv run ruff format --check src/xtrade/core/market_data.py tests/core` are clean.
- [x] 3.3 `uv run mypy src/xtrade/core/market_data.py` passes under strict mode.
- [x] 3.4 `openspec validate --all --strict` passes (8/8).

