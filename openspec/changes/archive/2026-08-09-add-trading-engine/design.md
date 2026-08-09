## Context

`xtrade` 目前数据层（`data.market_data` / `data.broker_data`）和执行层（`execution.broker`）已就位，但缺少把这两层串起来的**业务驱动器**。`strategy/`、`risk/` 是空包，回测 / 实盘不能跑。本次只搭建驱动器 + 三个新 capability，不修改现有 capability 的 REQUIREMENTS。

## Goals / Non-Goals

**Goals:**
- 一份 `Strategy` 代码，可同款跑回测和实盘。
- 回测：显式 `advance(time, prices)` 驱动；本地确定性、可重现。
- 实盘：本次仅 Mock 行情源 + `advance(now, live_prices)`；后续可换真实源。
- 风险规则覆盖下单前阻断 + 阻断后通知。
- `xtrade backtest run` CLI 可跑最小闭环。

**Non-Goals:**
- 不接真实交易所 / 真实行情供应商（仅 MockSource）。
- 不做回测性能优化（向量化、并行）。
- 不做 live trading CLI（live 暂 API 入口）。
- 不修改 `core.market_data` / `execution.broker` / `data.*` 现有契约。
- 不引入 web 框架 / FastAPI / Streamlit / MCP。

## Decisions

### 1. 引擎放在 `src/xtrade/engine/`（独立包）

**Why**: 引擎跨「数据 → 策略 → 风险 → 执行」四个层；放 `execution/` 会泄露调度语义，放 `strategy/` 会反向依赖 broker。独立包最干净。

**Alternatives considered**: `execution/engine.py`（语义污染）；`strategy/engine.py`（反向依赖）。

### 2. Strategy 接口是 Signal-based

`Strategy.on_bar(bar, ctx) -> list[Signal]` —— 返回 `Signal`（一个 dataclass，含 `symbol / side / quantity / order_type / price?`），**不**直接接触 broker。引擎把 `Signal` 翻译成 `OrderRequest` 后过风险、再下到 broker。

**Why**: 让 Strategy 可测（用 fake context 喂 K 线即可），不依赖 broker 状态机；同一份代码将来加 MCP / web UI / notebook 都不动。

**Alternatives considered**: Strategy 拿 broker（违反 context-only 边界）；事件回调（状态机复杂）。

### 3. 时钟模型：显式 `advance` + 纯事件驱动 live

- `BacktestEngine`：调用方循环 `engine.advance(time, prices)` 或用 `run(start, end, market_data)` 一把跑完。
- `LiveEngine`：内部维护 `asyncio` 事件循环；行情 pump 每次 `next_price` 触发 `advance(now, prices)`。**对外仍是确定性单线程 `advance` 语义**。

**Why**: 单接口、双实现。Live 不引入第二份时钟语义。

**Alternatives considered**: 全部 asyncio（回测引入 event loop 是过度工程）；双 API（行为分叉）。

### 4. Live 行情源仅 MockSource

`LiveEngine` 内部组合 `data.sources.mock_source.MockSource` 作为**唯一**行情实现，无 Protocol 抽象。本次封闭，跑通即可。

**Why**: 本 change 范围最小化。后续 change 引入 `MarketDataSource` Protocol + 真实源（问财 / 交易所）。

**Alternatives considered**: MarketDataSource Protocol（推迟）；直接接真实源（不必要）。

### 5. Risk Protocol + 3 个具体规则 + 组合器

`RiskCheck.check(intent: OrderIntent, ctx: RiskContext) -> None`；违反抛 `RiskViolationError`。`CompositeRiskCheck` 串联多个规则。`KillSwitch` 是个全局状态（独立 RiskCheck 实现，查询 `_killed` 标志）。

具体规则：
- `OrderSizeLimit(max_notional: Decimal)` —— 单笔金额上限。
- `PositionLimit(max_qty: Decimal)` —— 单 symbol 持仓上限（含本次订单后的预计持仓）。
- `KillSwitch(trigger_on_daily_loss: Decimal | None)` —— 触发后永久阻断；可通过 `reset()` 解除。

**Why**: 三个粒度够覆盖最小风险场景；组合器让用户自己组装。

**Alternatives considered**: 留 NoOp（不解决真问题）；全量（本次 scope 过大）。

### 6. Strategy Context 暴露 broker / risk / account / positions

`Context` 是引擎构造的 `Mapping` 风格结构：`ctx.broker` / `ctx.risk` / `ctx.account` / `ctx.positions` / `ctx.now` / `ctx.bar`。Strategy 不直接 import 这些模块，只依赖 Protocol。

**Why**: Context 是策略唯一门面，引擎负责把 `Broker` / `RiskCheck` / `Account` / `Position` 装进来。

### 7. CLI 子命令 `xtrade backtest run`

`--strategy <module:Class>` `--start YYYY-MM-DD` `--end YYYY-MM-DD` `--symbols` `--broker {in-memory,postgres}` `--initial-cash` `--config <path>`。

**Why**: 给策略一个可验证的运行入口；live CLI 推迟。

**Alternatives considered**: 仅 API（不便验证）；live CLI（scope 大）。

### 8. `Bar` / `Signal` / `Context` 类型在 `strategy/base.py` 顶层定义

`Bar` 直接构造的 `dataclass`（不复用 `data.market_data.Bar`，避免横向依赖）。

**Why**: 横向类型应保持 `core` 风格 dataclass；market_data 层 Bar 是 ORM row 类型，策略 Bar 是输入数据。

### 9. 引擎不重新导出 Protocol

`from xtrade.engine import BacktestEngine` / `from xtrade.strategy.base import Strategy` / `from xtrade.risk.base import RiskCheck` —— 各自独立入口。

**Why**: 一致于 `core.market_data` / `execution.broker` 的非重导出约定。

### 10. 测试不依赖真实 DB

`engine` / `strategy` / `risk` 测试用 `InMemoryBroker` + 直接构造 `Bar` / `Signal`，**不**走 `core.market_data`（避免串联）。`risk` 测 `OrderSizeLimit` 用 mock `OrderIntent` / `RiskContext`。

**Why**: 三层可独立单测；与 `core-market-data` / `execution-broker` 一致。

## Risks / Trade-offs

- [Live 引擎只 Mock 行情] → 推迟真实行情抽象；后续 change 处理。本里程碑仅打通骨架。
- [运行时策略类通过 `importlib.import_module` 加载] → 加运行时错误；CLI 失败时打印明确消息。
- [Business Clock 与 broker 内的 `advance(time, prices)` 重复] → 引擎决定 `time`；broker 内部维护独立时间戳。两者**不冲突**，但 spec 文档化「引擎是时间唯一来源」。
- [RiskCheck 在 `submit_order` 前调用，`OrderIntent` 是预计执行后状态] → risk 规则依赖 broker 提供的 _即将_ 持仓；当下 `Broker` 没有「假设性」查询，本期用 `OrderIntent` 自带的 `expected_qty_after` 字段，避免给 broker 增加新接口。
- [`InMemoryBroker` 与 `PostgresBroker` 行为差异在回测中可能暴露] → 回测默认 `in-memory`；CLI 通过 `--broker postgres` 切换，但 CLI 测试只跑 `in-memory`。
- [本次新增 `risk` 模块与 `data.market_data` 无横向依赖] → 通过 `OrderIntent` 这一新类型隔开；`risk` 包不依赖 `data` 包。

## Migration Plan

- 不引入运行时配置变更（无 schema 改动）。
- CLI 新增 `xtrade backtest run`；现有 `xtrade config` 不变。
- 不需要迁移旧数据。
- 文档（README）后续 change 补；本次不写 README。

## Open Questions

- 后续是否要把 `MarketDataSource` 抽象提到 `core.market_data`？—— 本次不做，留作单独 change。
- `Strategy` 是否需要 `on_init(ctx)` / `on_finish(ctx)` 生命周期？—— 本次 **on_init 暴露**（engine 启动时调用一次），on_finish **不暴露**（结束后引擎仅做 summary）。spec 文档化。
