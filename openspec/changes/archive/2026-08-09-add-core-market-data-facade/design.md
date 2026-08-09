## Context

`xtrade.data` 已经稳定提供 `KLineRepository` / `InstrumentRepository` / `TradeCalendarRepository` 三个仓库 + 一个 `data.engine` 单例（持有 `Engine` + `SessionFactory`）。但 `xtrade.core` 之下目前只有 `config / baseconfig / logging`，没有“读市场数据”的水平切面。

业务层（strategy / execution / 回测）一旦开始用数据，就必须直接 `from xtrade.data.market_data import PostgresKLineRepository(...)`，把 `core → data` 的依赖方向倒过来，也失去了 `core` 作为业务共用底座的价值。

本设计只回答 “facade 怎么写、依赖怎么注入、不在 facade 里做什么” 三件事，不改变 `xtrade.data` 任何行为。

## Goals / Non-Goals

**Goals:**

- 提供 `xtrade.core.market_data` 模块，4 个自由函数（`get_bars` / `get_instrument` / `is_trading_day` / `get_trading_days`），调用方一行 `import` 就能用。
- 完全复用 `xtrade.data` 的实现：内部函数级 `import` 三个 Postgres 仓库 + `data.engine.get_engine()` / `core.config.get_config()`，构造时把 `Config.data.batch_size` 传给 `PostgresKLineRepository`。
- 提供一个**单测可替换的注入点**（facade 内部靠 `monkeypatch` 替换它构造的 Repository / Engine），但不需要引入 DI 框架或全局设置器。
- 类型注解 + `mypy strict` 通过；`ruff format` clean。

**Non-Goals:**

- 不重写或合并 `KLineRepository` / `InstrumentRepository` / `TradeCalendarRepository`。
- 不引入缓存（functools / lru_cache / 自定义 LRU 一律不写）；业务层自己决定是否缓存。
- 不补充数据源（Tushare / AKShare / CSV）相关 facade —— 这是 `data-sources` 的职责。
- 不在线程/进程级共享 `DataFrame` / `Engine` 之外的任何状态。
- 不暴露 `upsert` / `delete` / `truncate` 写路径（facade 只读）。
- 不动 `core/__init__.py` 的 `__all__`（保持 `__all__: list[str] = []`）。

## Decisions

### 1. Facade 形态：自由函数（不引入 `MarketData` 类）

**Decided**: 模块暴露 4 个自由函数，对外签名即 API。

**Why**:
- 调用方最自然：`from xtrade.core.market_data import get_bars`。
- 与 `core.config.get_config()` 同风格（顶层函数 + 模块级单例），不引入新的对象构造范式。
- 单元测试可以在 `monkeypatch` 里把内部 helper 函数 `_build_kline_repo()` 替换为 fake，不需要构造类实例。

**Alternatives considered**:
- *`MarketData` 类 + 实例方法*：测试更优雅，但调用方多写一行，且与 `xtrade` 现有 `core.config.get_config()` 风格不一致。
- *类 + 自由函数同时提供*：重复 API，文档成本高。

### 2. Repository 构造点：模块内 `_build_*` 函数，可被 monkeypatch 替换

**Decided**: facade 内部对每个 Repository 暴露一个 `_build_kline_repo()` / `_build_instrument_repo()` / `_build_trade_calendar_repo()` 工厂函数（`_` 前缀，明确“内部”。在测试里用 `monkeypatch.setattr` 替换）。

**Why**:
- 真正的依赖注入点（不暴露给业务代码，但让测试可拦截）。
- 不引入全局 setter / 状态变量，避免“生产代码可以被偷偷替换”的隐患。
- 未来如果真的需要“跨模块共享 fake Repository”，可以扩展为支持 `set_*_factory()`，但**不现在做**（YAGNI）。

**Alternatives considered**:
- *全局 `set_engine` / `set_repositories`*：会引入可变模块状态，`core` 应保持尽量无状态。
- *用 `functools.lru_cache` 缓存 Repository 实例*：会让单测的 `monkeypatch` 失效（缓存命中后构造函数不再被调用），不推荐。

### 3. Engine 单例：直接复用 `data.engine.get_engine()`

**Decided**: facade 不构造 / 缓存自己的 Engine，每次调用走 `get_engine()`（按需懒加载）。`PostgresKLineRepository` 需要 `batch_size`，从 `Config.data.batch_size` 读取（同样懒加载）。

**Why**:
- 现有 `data.engine.get_engine()` 已经是“模块级单例 + 接受 `XTRADE_DATA__DATABASE__URL` 覆盖”，facade 复用即可，没有重复设计。
- 没必要在 `core` 再造一个 Engine 缓存 —— 那才叫耦合。

**Alternatives considered**:
- *Facade 持有自己的 Engine 句柄*：和 `data.engine` 双轨，违反 DRY。

### 4. `get_bars` 的 symbols 形态：`str | Iterable[str]`

**Decided**: 接受 `str | Iterable[str]`。单 symbol 返 `DataFrame`；可迭代（非空）返 `dict[symbol, DataFrame]`；空可迭代返 `{}`。

**Why**:
- 调用方最常见的两种用法——“拉一支” 和 “拉一篮子”——都不用写 list。
- `PostgresKLineRepository.get_bars` 本身强制 `list[str]`，facade 在边界做一次 normalize。

**Alternatives considered**:
- *统一返 `dict[symbol, DataFrame]`*：单 symbol 调用方多写 `["X"]`。
- *永远返 `DataFrame`，多 symbol 用 MultiIndex*：与 `data-market` spec 的语义不同，会让 facade 和仓库出现两种返回类型。

### 5. `adjust` 默认：`"none"`

**Decided**: `get_bars(..., adjust="none")` 为默认。`backward` / `forward` 透传给 `PostgresKLineRepository.get_bars`，由它承担复权计算。

**Why**:
- 原始价是底层存储，最小惊讶；回测 / 实时分析各自显式选择复权方式。
- 复权算法（累计因子 / 边界归一）已经在 `KLineRepository._apply_adjustment` 里实现并被单测覆盖，facade 不重复实现。

### 6. `get_instrument`：未知 symbol 返 `None`

**Decided**: 与 `InstrumentRepository.get` 对齐 —— 不存在返 `None`，不抛 `KeyError`。

**Why**:
- 业务层（策略订阅、合约筛选）经常需要 “if instrument exists then ...” 模式，`None` 比 `try/except` 自然。
- `instrument` 表 miss 与 hard error 不是一回事。

### 7. 不动 `core/__init__.py`

**Decided**: facade 不通过 `xtrade.core.__init__` 重新导出，必须显式 `from xtrade.core.market_data import get_bars`。

**Why**:
- AGENTS.md 已要求 `core` 是横向基础；在 `core/__init__.py` 暴露业务命名（`get_bars`）会让 `core` 看起来像业务层。
- 与 `core.baseconfig` / `core.logging` 现有风格一致（模块自管 `__all__`，`core/__init__.py` 始终空）。

## Risks / Trade-offs

- **facade 改动导致 `data` 仓库的版本同步问题** → facade 只调 `_build_*` 工厂，data 仓库接口稳定后 facade 完全无需改动。缓解：单测覆盖 facade 的四类调用。
- **`data.engine.get_engine()` 是模块级单例，单元测试改 DSN 后不会重置** → 这是 `data.engine` 已有行为（`reset_engine()` 已存在），facade 不引入新风险。测试用 `monkeypatch.setattr("xtrade.data.engine._engine", test_engine)` 即可。
- **`Config.data.batch_size` 实时改动不会反映到已构造的 `PostgresKLineRepository`** → 当前实现每次调用都重新构造（无 `_repo` 缓存），自动跟随最新配置；如有变化只需新增一行业务级缓存。
- **未覆盖 `list_all` / `count` 等诊断接口** → 这些是 `data` 层内部 / 测试用的，不是业务层 “读市场数据” 的常用路径；YAGNI。
- **facade 内的 `_build_*` 下划线命名会被 ruff 认为是“未使用”** → `_` 前缀在 Python 仅是约定，ruff 默认不因此警告；如 warning 出现再加 `noqa`。

## Migration Plan

- 无破坏性改动，直接部署。
- 灰度策略：本次不强制既有调用方迁移（暂无 `data.market_data` 的业务调用方）。后续 strategy / execution 改动时按需 `from xtrade.core.market_data import ...` 即可。
- 回滚：删除 `src/xtrade/core/market_data.py` + `tests/core/test_market_data.py` 即可，无 schema 改动。

## Open Questions

（无。已和用户确认所有会影响 spec 的决策：facade / 自由函数 / `adjust="none"` / 不缓存 / 暴露 trade_calendar / `dict[symbol, DataFrame]` / `None` 表 miss / Engine 单例。）
