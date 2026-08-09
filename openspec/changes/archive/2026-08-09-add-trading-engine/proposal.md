## Why

`xtrade` 已经具备数据层（`data.market_data` / `data.broker_data`）和执行层（`execution.broker` 提供 `Broker` Protocol + 两套实现），但缺少一个**把行情、策略、风险、下单串起来的驱动器**。当前的 `strategy/` 与 `risk/` 模块还是空包，无法跑通最小闭环「读 bar → 出 signal → 过风险 → 下单」。本次新增交易引擎（回测 + 实盘）+ 策略协议 + 风险协议，让业务侧能写一个策略后既可以回测，也可以接同一颗 broker 跑纸交易 / 实盘。

## What Changes

- 新建 `src/xtrade/engine/` 包，包含 `backtest.py` / `live.py` / `runner.py` / `clock.py`。
- 新建 `src/xtrade/strategy/` 的 `Strategy` Protocol + `Signal` / `Context` 类型 + `Bar` 类型。
- 新建 `src/xtrade/risk/` 的 `RiskCheck` Protocol + 三个具体规则：`OrderSizeLimit` / `PositionLimit` / `KillSwitch` + `CompositeRiskCheck` 组合器 + `RiskViolationError` / `RiskBlocked` 异常类型。
- 新增 `xtrade backtest run` CLI 子命令（`--strategy SYM` / `--data-source` / `--start` / `--end` / `--broker {in-memory,postgres}`）作为最小可运行入口；live 暂只 API，不加 CLI。
- 新增 `LiveEngine` 包装 `MockSource`（`data.sources.mock_source`） 作为本次唯一行情来源，next_price 触发式 `advance`。
- 不修改 `xtrade.core.market_data` / `xtrade.execution.broker` / `xtrade.data.*` 的现有契约。本次仅在它们之上**搭建应用层**。

## Capabilities

### New Capabilities

- `engine`: 回测引擎 + 实盘引擎 + 共享时钟模型 + Runner 入口（含 CLI 子命令 `backtest run`）。
- `strategy`: `Strategy` Protocol + `Signal` / `Context` / `Bar` 类型契约。
- `risk`: `RiskCheck` Protocol + 三个具体规则 + 组合器 + 阻断行为。

### Modified Capabilities

（无。本次仅在现有 `core-market-data` / `execution-broker` 之上构建；二者 REQUIREMENTS 不变。）

## Impact

- **新增依赖**：无（仅使用 stdlib + `click` + `pandas` + `numpy`，已在 `pyproject.toml`）。
- **新增 / 修改的文件**：
  - `src/xtrade/engine/__init__.py`、`backtest.py`、`live.py`、`runner.py`、`clock.py`
  - `src/xtrade/strategy/__init__.py`、`base.py`（Protocol + types）
  - `src/xtrade/risk/__init__.py`、`base.py`（Protocol + error）、`checks.py`（三个规则 + 组合器）
  - `tests/engine/`、`tests/strategy/`、`tests/risk/`
  - `src/xtrade/cli/xtrade.py` 注册 `backtest` 子命令
  - `src/xtrade/cli/backtest.py` 新 CLI（仅 `backtest run`）
- **依赖关系**：`strategy` / `risk` 不依赖 `engine`；`engine` 依赖三者。本 change 内三个新 capability 互相通过 Protocol 耦合，**不互相 import 具体实现**。
- **受影响的现有 API**：`core.market_data` / `execution.broker` 保持不变；调用方按 `[Broker Protocol]` 编程。
- **CLI 入口扩展**：`xtrade backtest run --strategy <module:Class> --start ... --end ...`。
- **测试**：单测使用 `InMemoryBroker` + `MockSource`；CLI 测试用 `click.testing.CliRunner`。
