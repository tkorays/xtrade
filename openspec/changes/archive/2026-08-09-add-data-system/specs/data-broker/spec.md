## Purpose

Provides durable storage for broker-output records (orders, trades, positions, account snapshots) produced by backtests and live-trading sessions. All writes go through SQLAlchemy 2.x synchronous ORM + `Session` so that referential integrity, foreign-key relations, and transactional semantics are first-class. Broker / execution modules interact with this capability through repository protocols and never touch ORM models directly.

## ADDED Requirements

### Requirement: Orders, Trades, Positions, Account tables

The data layer SHALL persist four broker-related entities with SQLAlchemy ORM models:
- `Order`: a single order's full state machine (`pending`, `submitted`, `partial`, `filled`, `cancelled`, `rejected`, `expired`).
- `Trade`: a fill event linked to a parent `Order` via `order_id`.
- `Position`: a snapshot of one symbol's holdings at a point in time, keyed by `(run_id, symbol, time)`.
- `Account`: a snapshot of account equity / cash / margin, keyed by `(run_id, time)`.

All four tables SHALL have an explicit `run_id` foreign-key-ish column (string, not necessarily FK) so multiple backtest / live runs coexist without mixing their outputs.

#### Scenario: Insert an order

- **WHEN** a caller invokes `OrderRepository.create(record)` with a valid `Order` payload
- **THEN** the order is persisted and the repository returns the persisted record with its database-assigned `id`

#### Scenario: Insert a fill linked to an order

- **WHEN** a caller invokes `TradeRepository.create(record)` whose `order_id` matches a persisted order
- **THEN** the trade is persisted and a follow-up `OrderRepository.get(record.order_id)` returns the order unchanged (no order-side mutation by trade insert alone)

#### Scenario: Two runs coexist

- **WHEN** `run_id="backtest-2026-01-01"` and `run_id="live-2026-01-02"` both insert orders for the same `symbol`
- **THEN** `OrderRepository.list_by_run("backtest-2026-01-01")` returns only the first run's orders

### Requirement: ORM-only access path

All broker-data repositories SHALL read and write through SQLAlchemy 2.x synchronous `Session`. They SHALL NOT use `cursor.copy` / raw SQL / `pd.read_sql`. The session is acquired from `data.engine.get_session()` and SHALL be a context manager that commits on success and rolls back on exception.

#### Scenario: Session is committed on success

- **WHEN** a caller uses `with get_session() as session:` and calls `OrderRepository(session).create(record)`
- **THEN** on normal exit the order is persisted; on raised exception the order is rolled back

#### Scenario: Session is rolled back on error

- **WHEN** a caller raises an exception inside the `with` block
- **THEN** any mutations made before the raise are not committed

### Requirement: Order state transitions

`OrderRepository.update_status(order_id, new_status)` SHALL persist the new status and the `updated_at` timestamp. The repository SHALL reject transitions that are not in the allowed graph (e.g. `filled` → `pending`) with a domain-specific exception `OrderStateError`.

#### Scenario: Allowed transition succeeds

- **WHEN** an order is in `submitted` and the caller calls `update_status(order_id, "filled")`
- **THEN** the repository persists `status=filled` and updates `updated_at`

#### Scenario: Disallowed transition raises

- **WHEN** an order is in `filled` and the caller calls `update_status(order_id, "pending")`
- **THEN** the repository raises `OrderStateError` and does not modify the row

### Requirement: Positions and Account are snapshots, not aggregates

Position and Account rows SHALL be immutable once written. There SHALL be no `update` method on `PositionRepository` or `AccountRepository`. To "update" a position or account, the caller writes a new row with a fresh `time` value.

#### Scenario: Position upsert is insert-only

- **WHEN** a caller writes a Position for `(run_id="x", symbol="A", time=t1, qty=10)`
- **THEN** the row exists at `(x, A, t1)`. A subsequent call with `(x, A, t1, qty=20)` SHALL raise `DuplicateSnapshotError`; writing `(x, A, t2, qty=20)` is the supported way to advance the snapshot.

#### Scenario: Account history queries

- **WHEN** a caller invokes `AccountRepository.list_by_run("x")` for a run with multiple account snapshots over time
- **THEN** the repository returns the snapshots ordered by `time` ascending

### Requirement: Idempotent identifiers

Every order record SHALL be persistable with an externally-supplied `client_order_id` (string) unique per `run_id`. Re-inserting with the same `(run_id, client_order_id)` SHALL be rejected, so broker-side retries do not double-book.

#### Scenario: Duplicate client_order_id is rejected

- **WHEN** a caller inserts an order with `(run_id="x", client_order_id="o-1")` and again with the same key
- **THEN** the second insert raises a unique-constraint error and the row count is unchanged

### Requirement: Repository protocol + Postgres implementation

The data layer SHALL expose one `Protocol` per broker-data entity (Orders, Trades, Positions, Account) and a Postgres implementation backed by SQLAlchemy. Each protocol method SHALL be the only supported call site for downstream broker / execution modules.

#### Scenario: Broker uses the protocol, not the model

- **WHEN** a developer inspects the imports of a future broker / execution module
- **THEN** the module imports from `xtrade.data.broker_data.<entity>`, not directly from `xtrade.data.orm.broker`

### Requirement: No external IO from broker-data repositories

Broker-data repositories SHALL NOT make network calls. They persist only to the local PostgreSQL database. Live broker adapters are out of scope for this capability (they belong to a future `execution-broker` capability).

#### Scenario: Broker-data repository does not import a network client

- **WHEN** a developer inspects the imports in `src/xtrade/data/broker_data/order.py` and the other broker-data repository modules
- **THEN** no module from `requests`, `urllib`, `websockets`, `httpx`, or a future broker SDK is imported