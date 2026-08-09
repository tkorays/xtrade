## Context

`xtrade.data.broker_data` 已经稳定提供 4 个 ORM 仓库（Order / Trade / Position / Account）和 `OrderState` 状态机；但这些是“存什么” —— 把记录写到 PostgreSQL —— 的契约，**没有**回答“Broker 怎么按业务语义跑起来”：谁维护 Position / Account，谁推进时间、谁触发状态机、谁通知成交。

`src/xtrade/execution/` 一直是空的。Strategy / 回测 / paper-trading 现在会直接拼 `PostgresOrderRepository` + `PostgresTradeRepository` + 自己的 dict 维护 Position —— 同一段逻辑在三个地方各写一遍。

本次新增 `xtrade.execution.broker`，把订单生命周期 + 持仓/账户维护 + 回调 + 时钟推进合并成一个 `Broker` 抽象，给两种实现（in-memory / Postgres）共享同一份 Protocol。`data-broker` 现有 spec 不变。

## Goals / Non-Goals

**Goals:**

- 暴露 `Broker` Protocol：业务层只对 Protocol 编程，不知道背后是 dict 还是 Postgres。
- 两个实现：`InMemoryBroker`（dict）、`PostgresBroker`（委托 `xtrade.data.broker_data`）。两者行为契约一致。
- 同步回调：`register_callback(event, fn)`，事件点 `on_fill` / `on_order_update` / `on_account_update`，Broker 在同步事件点直接调用 `fn`。
- 显式时钟推进：`advance(time, prices)` 一次性推进 in-flight 订单到该时刻的状态（新成交 / 状态转换），然后更新一次 Position + Account snapshot。
- 复用 `xtrade.data.broker_data` 的 `Order / Trade / Position / Account / OrderState` 类型；不重复定义。
- `mypy strict` + ruff format clean；参数化测试覆盖两种实现。

**Non-Goals:**

- 不引入 `asyncio` 推送 / 队列 / WebSocket —— 现有代码全同步，回调也是同步。
- 不实现真实券商 SDK 对接（属于 future `execution-broker-live`）。
- 不改变 `data-broker` 现有 Repository 的契约 / 实现。
- 不在 Broker 层做风控 / 资金校验 —— 留给 `risk` 层。
- 不在 Broker 层做策略订单拆分（拆单是策略行为）。

## Decisions

### 1. 路径：`src/xtrade/execution/broker.py`

**Decided**: 单一模块 `src/xtrade/execution/broker.py`，Protocol + dataclass + 两个实现 + 工厂。

**Why**:
- 现有 `execution/` 为空，不引入新层级。
- 业务层（`strategy` / `backtest` / CLI）只需 `from xtrade.execution.broker import Broker, InMemoryBroker, PostgresBroker, OrderRequest`。
- 单文件避免 “一个小抽象拆 5 个文件” 的过度结构。

**Alternatives considered**:
- *拆 `protocols.py` / `inmemory.py` / `postgres.py` / `types.py`*：每个文件 < 100 行，拆得太散。
- *放回 `core`*：与 AGENTS.md “core 无业务耦合” 起冲突；`Broker` 维护 Position/Account 是业务语义。

### 2. 类型复用：直接 re-export `xtrade.data.broker_data`

**Decided**: `Order / Trade / Position / Account / OrderState / OrderStateError / DuplicateSnapshotError` 都从 `xtrade.data.broker_data` 重新导出；`Broker` 类型不定义重复 dataclass。

**Why**:
- 避免“两个 `Order` dataclass”导致业务层混淆。
- `data-broker` 的 ORM 行 ↔ dataclass 转换已经在 `_to_record` 里做，`Broker` 不需要再造一层 ORM 转换。

**Alternatives considered**:
- *新建 type alias / 新建 wrapper*：徒增一层镜像；用 `from xtrade.data.broker_data import Order` 即可。

### 3. `OrderRequest` 用 dataclass 而非 ORM

**Decided**: 新增 `OrderRequest` dataclass（`run_id / client_order_id / symbol / side / quantity / price / order_type`）作为 `submit_order` 入参。

**Why**:
- `submit_order` 的语义是“还没存的订单请求”，和数据层 `Order`（已存，含 `id` / `created_at`）不同。
- `OrderRequest` 不需要 ORM 序列化，纯 Python 类型。

**Alternatives considered**:
- *直接用 `Order`*：会让 `id=None` 看起来像可选项，语义模糊。
- *用 `TypedDict`*：违反项目 `mypy strict` 风格（AGENTS.md 要求 dataclass / pydantic）。

### 4. `OrderSide` / `OrderType` 用 `StrEnum` 复刻

**Decided**: 新增 `OrderSide` / `OrderType` 两个 `StrEnum`，与 `OrderState` 同风格。

**Why**:
- `OrderState` 已在 `data.broker_data.order` 存在；新增 `OrderSide` / `OrderType` 与之对齐。
- `OrderState` 不复用 — 因为它是 broker-data 内部的，写死引用会让 Broker 永远依赖 ORM 模块。

**Alternatives considered**:
- *直接用 `side: str` 字段*：和 `OrderState` 风格不一致，类型不安全。

### 5. 同步回调：list of Callable

**Decided**: `register_callback(event: str, fn: Callable[..., None])`；同一事件允许多个回调，按注册顺序同步调用。回调抛异常被吞掉（仅记录到日志），不阻断推进。

**Why**:
- 同步调用保持与现有代码风格一致；不需要引入 asyncio。
- “回调异常不阻断推进” 是回测期常见诉求（一个指标计算挂了不影响订单流）。
- 不引入 EventEmitter 第三方依赖。

**Alternatives considered**:
- *asyncio + loop.create_task*：与同步 psycopg 冲突。
- *callback 异常直接 raise*：脆弱，回测里一回调崩溃就让回测失败。

### 6. `advance(time, prices)` 推进语义

**Decided**: `advance(time, prices: dict[str, Decimal])` 一次性：
1. 对每个 `status in (submitted, partial)` 的 Order，根据 `prices[symbol]` 决定：
   - 市价单（`OrderType.MARKET`）：立刻全部成交（生成 1 笔 Trade）。
   - 限价单（`OrderType.LIMIT`）：`price` 满足（买单 ≤ price，sell ≥ price）触发成交，否则保持原状态。
2. 推进订单状态：`pending → submitted`（新提交订单）。
3. 若订单终止（filled / cancelled / expired / rejected），移除 in-flight 集合。
4. 对每个新成交：更新 `Position`（按加权平均价维护 `quantity` / `avg_price`），新增 `Position` snapshot。
5. 更新 `Account` snapshot（`cash` / `equity` / `margin`）。

**Why**:
- 调用方显式推进，回测可重放；live 由定时器 / 行情回调驱动同一 API。
- 所有更新在一次“推进”内成事务：原子。

**Alternatives considered**:
- *内部维护 `threading.Thread` 定时器*：与现有同步风格割裂，且不可重放。
- *每个事件调用方手动 `on_quote` / `on_fill`*：调用方复杂度上升，不是 broker 抽象重点。

### 7. 事务边界：每个 `advance` 一次 `get_session`

**Decided**: `PostgresBroker.advance` 在一个 `with get_session() as session:` 块内：状态机推进 + 4 个 Repository 调用 + snapshot 写入，一次 commit / 一次 rollback。

**Why**:
- 一次推进 = 一次事务；和 “broker 推进一步” 的语义一致。
- 异常时全部 rollback，回测可重试。

**Alternatives considered**:
- *每个 Repository 调用各自一个 session*：可能半推进状态被持久化，违反推进原子性。

### 8. InMemory 启动时间由 `__init__(run_id, ...)` 接收；Postgres 启动时间由 `run_id` 决定

**Decided**: 两种实现都接收 `run_id` 字符串，标识本次回测 / paper 运行的命名空间（与现有 `data-broker` Repository 的 `run_id` 字段对齐）。

**Why**:
- 一次 `run_id` 对应一次回测 / 一次 paper session；同一 `run_id` 复用同一个 Broker 实例即可。
- `InMemoryBroker` 把 `run_id` 写进 `submit_order` 的 `Order.run_id`，无需另存。

**Alternatives considered**:
- *每次 `submit_order` 强制传 `run_id`*：容易与 `OrderRequest.run_id` 字段重复。

### 9. `PostgresBroker` 仅在启动时校验 Repository / session，不在每次调用重复校验

**Decided**: `PostgresBroker.__init__(run_id, session_factory=None)`：可选注入 `sessionmaker[Session]`；默认走 `xtrade.data.engine.get_session()`。

**Why**:
- 与 `monkeypatch` 替换 `xtrade.data.engine.get_session` 的测试模式一致。
- 单测可以注入 fake session_factory。

**Alternatives considered**:
- *强制 4 个 Repository 实例作为构造参数*：参数列表太长；调用方写法奇怪。

### 10. `Broker` Protocol 是 `runtime_checkable` Protocol

**Decided**: `Broker` 用 `Protocol` with `runtime_checkable`；`isinstance(obj, Broker)` 用于测试断言。

**Why**:
- 业务层依赖 Protocol，但单测断言“fake 是 Broker” 是常见用法。
- 与 `InstrumentRepository` 等现有 Protocol 一致。

**Alternatives considered**:
- *ABC*：与 `runtime_checkable` 一样的能力，但 ABC 强制继承，Protocol 鸭子类型更轻。

## Risks / Trade-offs

- **`PostgresBroker` 在每次 `advance` 触发多次 ORM 写** → 每个 `advance` 内 1 个 session / 1 个 commit；事务粒度粗，符合“一步推进”的语义。Mitigation：spec 写明“一次 advance 一次事务”。
- **InMemory 状态 living 多进程不一致** → 逃出 Broker 抽象的目标（InMemory 不持久化契约不成立）；文档写明。
- **回调异常吞掉** → 调试时难追踪。Mitigation：使用 `xtrade.core.logging` 记录异常；spec 明确“回调异常不阻断推进”。
- **Limit 单不支持部分成交** → 当前 spec 假设 limit 单要么全部成交要么不成交；支持 partial 留给后续 spec。
- **`OrderSide` / `OrderType` 新增 enum 与 `data-broker` 内部 `OrderState` 形成两套 enum** → 不可避免（`OrderState` 是 broker-data 内部；`OrderSide` / `OrderType` 是 broker 接口）。spec 说明两者职责不同。
- **`advance(time, prices)` 价格缺失** → `prices` 中没有 `symbol` 视为该 symbol 当期不强平（订单保持原状态）。spec 写明。

## Migration Plan

- 无破坏性改动。直接部署。
- 调用方：新代码用 `from xtrade.execution.broker import Broker`；现有 strategy / 回测代码（暂无）逐步迁移。
- 回滚：删除 `src/xtrade/execution/broker.py` + `tests/execution/` 即可。

## Open Questions

（无。已确认所有会影响 spec 的决策：路径 / 接口形状 / 状态归属 / 同步回调 / 显式推进 / 模型复用 / 事务边界。）
