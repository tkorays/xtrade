## 1. Implement `xtrade.execution.broker` module

- [x] 1.1 Create `src/xtrade/execution/__init__.py` and `src/xtrade/execution/broker.py` with module docstring + `__all__` listing the Protocol, two implementations, and the re-exported types.
- [x] 1.2 Add `OrderRequest` dataclass and `OrderSide` / `OrderType` `StrEnum`s. Re-export `Order`, `Trade`, `Position`, `Account`, `OrderState`, `OrderStateError`, `DuplicateSnapshotError` from `xtrade.data.broker_data`.
- [x] 1.3 Define the `Broker` Protocol with `runtime_checkable` and the methods listed in the spec: `submit_order`, `cancel_order`, `get_order`, `list_orders`, `get_position`, `list_positions`, `get_account`, `advance`, `register_callback`. Full type annotations.
- [x] 1.4 Implement `InMemoryBroker` in the same module: process-local dict / list backing; constructor takes `run_id` and an initial cash (default `Decimal("0")`); methods match the Protocol; `advance` performs the state-machine + fill + position + account update + callback fan-out described in the spec.
- [x] 1.5 Implement `PostgresBroker` in the same module: constructor takes `run_id` and an optional `sessionmaker`; on `advance` writes Order / Trade / Position / Account via `PostgresOrderRepository` / `PostgresTradeRepository` / `PostgresPositionRepository` / `PostgresAccountRepository`. (Each repo call commits in its own transaction; routing the session through the repositories for single-transaction atomicity is deferred — see spec note.)
- [x] 1.6 Implement the callback fan-out helper `_emit(event, payload)` used by both implementations: synchronously invoke every registered callable in registration order; on raised exception, log via `xtrade.core.logging` and continue with the next callback.

## 2. Tests for the `Broker` contract

- [x] 2.1 Create `tests/execution/__init__.py` and `tests/execution/test_broker.py`. Use `pytest.mark.parametrize` to run the same `Broker` contract tests against both `InMemoryBroker` and `PostgresBroker`. The Postgres parameterized run is gated on `XTRADE_TEST_DB_URL` (mirror the pattern in `tests/data/test_broker_data.py`).
- [x] 2.2 Add tests covering: `submit_order` returns an `Order` with status `pending` or `submitted`; `get_order` returns None for unknown id; `advance` with a market BUY fills at the supplied price; `advance` with a LIMIT BUY only fills when price is at-or-below the limit; `advance` with missing symbol leaves the order open; `register_callback` fires `on_fill` once per fill in registration order; a callback exception does not abort the remaining callbacks or the `advance`; weighted-average price is computed for multiple BUYs; SELL reduces position quantity; `cancel_order` works on `pending` / `submitted` / `partial`; `cancel_order` on `filled` raises `OrderStateError`; `Order` re-exported from `xtrade.execution.broker` is the same class as `xtrade.data.broker_data.Order`.
- [x] 2.3 Add a test that two `InMemoryBroker` instances with the same `run_id` have isolated state (no shared dict).

## 3. Quality gates

- [x] 3.1 `uv run pytest tests/execution/test_broker.py -v` passes; the in-memory parameterized branch runs in unit-test mode; the Postgres parameterized branch skips when `XTRADE_TEST_DB_URL` is unset. Full suite: 110 passed, 27 skipped (no DB).
- [x] 3.2 `uv run ruff check src/xtrade/execution && uv run ruff format --check src/xtrade/execution tests/execution` are clean.
- [x] 3.3 `uv run mypy src/xtrade/execution` passes under strict mode.
- [x] 3.4 `openspec validate --all --strict` passes (9/9).
