## Why

`xtrade` 已经有 `data-broker` PostgreSQL 仓库（orders / trades / positions / account snapshots），但**还没有 `Broker` 这一层抽象** —— 业务层（strategy / execution / 回测）目前要直接选一个具体实现并自己拼装订单簿、状态机、账户更新、回测推进逻辑，结果就是：

1. 回测和 paper / live 模式分别写两套流程，状态机/账户维护不能复用。
2. 业务层直接依赖 `data.broker_data.Postgres*Repository`，把“存哪儿”和“怎么按 broker 语义跑起来”混在一起。
3. 没有 `Broker` Protocol，无法在测试里轻松换一个简化实现。

本次新增 `xtrade.execution.broker`（Protocol + `InMemoryBroker` + `PostgresBroker`），统一封装订单生命周期、持仓/账户维护、回调通知、回测时间推进。`data-broker` 仍负责 PostgreSQL 持久化（不动），`execution-broker` 是它之上的业务层抽象。

## What Changes

- 新增 `src/xtrade/execution/broker.py`：
  - `Broker` Protocol：业务层只对 Protocol 编程。
  - `InMemoryBroker`：进程内 dict 实现，用于回测 / 单测 / dry-run。
  - `PostgresBroker`：委托 `xtrade.data.broker_data` 4 个 Repository 做持久化，行为与现有 `data-broker` spec 一致。
  - 数据类型（`Order` / `Trade` / `Position` / `Account` / `OrderRequest` / `OrderSide` / `OrderType`）：复用 `xtrade.data.broker_data` 里已有的 dataclass + enum（避免重复定义）；如缺类型（如 `OrderRequest`）作为薄 dataclass 新增。
  - 回调接口：`register_callback(event: str, fn: Callable)`，支持 `on_fill` / `on_order_update` / `on_account_update`，回调由 Broker 在同步事件点直接调用。
- 新增 `src/xtrade/execution/__init__.py`：导出 `Broker`、`InMemoryBroker`、`PostgresBroker`、相关类型。
- 新增 `tests/execution/test_broker.py`：覆盖两种实现共有的行为契约（提交、单步推进、取消、回调、状态机推进、Position/Account 维护）。
- 两实现共享一份 `Broker` 行为测试（参数化），不合规的实现将在测试里暴露。

无破坏性改动（`data-broker` 不动）。

## Capabilities

### New Capabilities

- `execution-broker`: `Broker` Protocol + `InMemoryBroker` + `PostgresBroker`。统一订单生命周期、持仓/账户维护、回调注册、回测时间推进。`InMemoryBroker` 主用于回测 / 测试；`PostgresBroker` 主用于 paper / live 持久化。

### Modified Capabilities

（无。`data-broker` 是 PostgreSQL 存储契约，不受 `execution-broker` 影响 —— `PostgresBroker` 是它的 1 个消费者，契约不变。）

## Impact

- 受影响代码：
  - 新增 `src/xtrade/execution/broker.py`（按 spec 估算 ~250–400 行）+ `__init__.py`。
  - 新增 `tests/execution/test_broker.py`（参数化测试两种实现）。
  - 不修改 `data/` 任何文件。
- 调用方：未来 strategy / execution / 回测引擎 / CLI 仅依赖 `xtrade.execution.broker.Broker` Protocol；本次不强制改造既有调用方（暂无）。
- 依赖：仅复用 `xtrade.data.broker_data` + `xtrade.data.engine` + Python stdlib，不引入新三方依赖。
- 性能：`InMemoryBroker` 同步推进 O(orders)，适合回测；`PostgresBroker` 单事务内协调 4 个 Repository，对低频成交（paper / live）足够。
- 风险：中等。`PostgresBroker` 的事务边界（一次 `advance` 走 1 个 `get_session`，避免多次 commit 散布状态）需要在 design 里讲清楚。
