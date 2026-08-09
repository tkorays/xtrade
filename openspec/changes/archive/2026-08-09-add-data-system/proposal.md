## Why

`xtrade` 当前只有项目骨架、CLI 和 `~/.xtrade/config.json` 配置。回测和实盘要落地，第一步就是数据系统：没有 K 线 / 复权因子 / 交易日历 / 证券字典，回测引擎无数据可读；没有 Orders / Trades / Positions / Account 仓库，回测和实盘的产出无法落库供后续分析。本 change 引入数据层的最小可用集合，奠定后续 broker / execution / strategy 模块的依赖基础。

特别重要的设计原则是 **两类数据，两种用法**——市场数据（K线 / 复权因子 / 交易日历）量级大（百万到亿行），写入路径**不**走 ORM unit-of-work，而是原生 `cursor.copy()` 或 `executemany + INSERT ... ON CONFLICT`；业务数据（Orders / Trades / Positions / Account）量级小、要关联查询，走标准 SQLAlchemy 2.x 同步 ORM + `Session`。两类数据共享同一个 `Engine`（连接池），但用法分两类，避免把 ORM 写长表的性能陷阱带进数据层。

## What Changes

- 新增数据子系统模块 `src/xtrade/data/`，按子包组织：`market_data` / `broker_data` / `sources` / `migrations`，外加 `orm/` / `engine.py` / `__init__.py`。
- 引入 SQLAlchemy 2.x 同步 ORM + psycopg 驱动，统一存 PostgreSQL；新增 `Config.data` 配置节（`database.url`、`batch_size`）。
- 市场数据仓库（K线 / 复权因子 / 交易日历 / 证券字典）：
  - 接口：Repository Protocol + Postgres 实现。
  - 写：`Connection.cursor().copy()` 或 `executemany + INSERT ... ON CONFLICT DO UPDATE`，不经过 ORM unit-of-work。
  - 读：`pd.read_sql()` 走 Engine，不走 Session；返回 `Dict[symbol, DataFrame]`，以 `time` 为索引。
  - 复权：表里只存原始价 + 离散因子；后复权 / 前复权在读路径计算。
- 业务数据仓库（Orders / Trades / Positions / Account）：标准 SQLAlchemy ORM + `Session`；CRUD 接口暴露给 broker / execution 模块。
- 数据源：`DataSource` Protocol + `SourceRegistry`；本期提供一个 `InMemoryMockSource`（构造时注入 bars / instruments，不读外部）。
- 迁移：Alembic 接入；提供一份初始迁移 `0001_initial.py`，建立全部 ORM 表 + 索引。
- 依赖：在 `pyproject.toml` 新增 `sqlalchemy>=2.0`、`psycopg[binary]>=3.1`、`alembic>=1.13`、`pandas>=2.2`（pandas 已是依赖）、`pyarrow>=15`（`pd.read_sql` 大结果集用 pyarrow backend）。
- 测试：用 SQLite in-memory + 临时表隔离 repository 单测；Alembic 迁移 round-trip 测试；DataFrame round-trip 测试。
- 不引入实时行情、外部数据源（Tushare / AKShare）、BacktestRuns / Signals 表。

## Capabilities

### New Capabilities

- `data-market`：K线 / 复权因子 / 交易日历 / 证券字典的 Repository Protocol、Postgres 实现、读路径复权计算、DataFrame 接口契约。
- `data-broker`：Orders / Trades / Positions / Account 的 ORM 模型、Repository 接口、CRUD 语义、与 market data 的边界。
- `data-sources`：`DataSource` Protocol、`SourceRegistry`、`InMemoryMockSource`（用于测试和示例）。
- `data-migrations`：Alembic 接入约定、`env.py` 配置、`0001_initial` 迁移的内容约定、回滚约定。

### Modified Capabilities

- `xtrade-config`：在 `Config` 上新增 `data` section（`database.url`、`batch_size`），env override `XTRADE_DATA__*`。
- 其他 capability 不变（`project-skeleton` / `xtrade-cli` 不动）。

## Impact

- `pyproject.toml`：新增 5 个运行时依赖；锁文件重新生成；不引入 alembic 作为可选 extras（开发与运行都需要）。
- `pyproject.toml`：测试依赖无变化；测试基础设施继续用 pytest。
- 新增模块：
  - `src/xtrade/data/__init__.py`、`src/xtrade/data/engine.py`、`src/xtrade/data/orm_base.py`
  - `src/xtrade/data/orm/{__init__.py,market.py,broker.py}`
  - `src/xtrade/data/market_data/{__init__.py,kline.py,adj_factor.py,trade_calendar.py,instrument.py}`
  - `src/xtrade/data/broker_data/{__init__.py,order.py,trade.py,position.py,account.py}`
  - `src/xtrade/data/sources/{__init__.py,base.py,mock_source.py}`
  - `src/xtrade/data/migrations/env.py`、`script.py.mako`、`versions/0001_initial.py`
- 修改：
  - `src/xtrade/core/config.py`：新增 `DataConfig` + `Config.data` 字段；`XTRADE_DATA__*` env override。
  - `.env.example`：新增 `XTRADE_DATA__DATABASE__URL`、`XTRADE_DATA__BATCH_SIZE` 占位。
  - `README.md`：新增 "Data system" 段落，含安装 Postgres、运行迁移、配置 DSN、调用示例。
- 新增 env vars：`XTRADE_DATA__DATABASE__URL`、`XTRADE_DATA__BATCH_SIZE`。
- 测试新增：`tests/data/test_engine.py`、`tests/data/test_market_data.py`、`tests/data/test_broker_data.py`、`tests/data/test_sources.py`、`tests/data/test_migrations.py`。
- 破坏性变更：**无**（`Config.data` 是新增字段，向后兼容）。
- 不影响 `src/xtrade/{cli,core,data,execution,risk,strategy}` 既有 import 关系——本次只填实 `data/`。
- 部署要求：PostgreSQL ≥ 13（无需 timescaledb 扩展）。开发用本地 Postgres；CI 用 GitHub Actions 服务 Postgres 镜像。