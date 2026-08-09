## Why

`xtrade` 已经有完整的 `data` 层（`KLineRepository` / `InstrumentRepository` / `TradeCalendarRepository`），但调用方（未来的策略、回测引擎、execution）目前必须自己直接 `import` `xtrade.data` 路径并构造仓库对象。`core` 作为横向基础层，目前只提供配置、日志、基类，缺少一个统一的“读市场数据”的入口，导致：

1. 业务层（strategy / execution / 回测）会散布 `from xtrade.data.market_data import PostgresKLineRepository(...)` 这样的样板，违反 `core` 应作为业务层共用底座的定位。
2. 一旦数据层 Repository 的接口或依赖形态变化（Engine 单例、Batch size、Transaction 边界），所有调用方都要跟着改。
3. 没有显式的“只读 facade”语义：`core.market_data` 只提供查询方法，不暴露 upsert，让业务层在编译期就拿不到写入路径。

本次新增 `xtrade.core.market_data`，作为同一进程内所有“读市场数据”调用的统一入口，对 `xtrade.data` 做薄包装。

## What Changes

- 新增 `src/xtrade/core/market_data.py`：对外暴露 4 个自由函数 `get_bars` / `get_instrument` / `is_trading_day` / `get_trading_days`，内部按需构造 `PostgresKLineRepository` / `PostgresInstrumentRepository` / `PostgresTradeCalendarRepository`。
- 内部走 `xtrade.data.engine.get_engine()`（沿用现有 Engine 单例 + `data.batch_size` 配置），调用方无需自行管理 Engine / Session / Connection。
- `get_bars` 默认 `adjust="none"`（原始价），可选 `backward` / `forward`（复用既有 `KLineRepository._apply_adjustment` 逻辑）。
- `get_instrument(symbol)` 不存在时返回 `None`（与 `InstrumentRepository.get` 对齐）。
- `get_bars` 接受 `symbols: str | Iterable[str]`：单 symbol 返 `DataFrame`；多 symbol 返 `dict[symbol, DataFrame]`（空集合返 `{}`）。
- 模块层不缓存任何结果；Engine 仍走 `data.engine.get_engine()` 的现有单例。
- 新增 `tests/core/test_market_data.py`：覆盖 happy-path、边界（空 symbols / 单 symbol / 未知 symbol / 未知 interval）、依赖注入（用 monkeypatch 替换 Repository 构造点即可）。
- `src/xtrade/core/__init__.py` 不再导出新符号（保持 `__all__ = []` 一致），调用方按需 `from xtrade.core.market_data import get_bars`。

无破坏性改动。

## Capabilities

### New Capabilities

- `core-market-data`: 提供 `xtrade.core.market_data` 模块：业务层读市场数据（K-line、instrument、trade calendar）的统一 facade；只读、不缓存、不做 IO 重映射。

### Modified Capabilities

（无。`data-market` / `data-broker` / `data-sources` 的需求未变 —— `core` 仅是薄包装，未引入新的存储协议或行为契约。）

## Impact

- 受影响代码：
  - 新增 `src/xtrade/core/market_data.py`（< 80 行）。
  - 新增 `tests/core/test_market_data.py`。
  - 不修改 `data/` 任何文件。
- 调用方：未来 strategy / execution / 回测引擎应 `from xtrade.core.market_data import get_bars, get_instrument, is_trading_day, get_trading_days`；本次不强制改造既有调用方（暂无）。
- 依赖：仅复用现有 `xtrade.data` 与 `xtrade.core.config`，不引入新三方依赖。
- 配置：仍读 `Config.data.batch_size`（决定 `KLineRepository` 构造参数）；无需新增配置项。
- 风险：低。Facade 是单向薄包装；既有 `data` 测试覆盖不变。
