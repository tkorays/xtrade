## Why

当前 K 线数据全部落入单一 `kline` 表，靠 `interval` 列区分 1d / 1m / 5m / 15m / 30m / 60m。这带来两个问题：

1. **混合频率共享表 + 复合主键 `(symbol, time, interval)`**，导致主键索引膨胀、按 `interval` 过滤的查询计划不能完全 narrowing；分钟线和日线的写入路径也彼此争抢同一张表的 WAL。
2. **`pre_close` 列从未被任何写入路径填充，也没有被任何读取路径消费**（复权完全靠 `adj_factor` 表），属于死字段，无端占用存储、COPY 列、列定义维护成本。

项目仍处于开发阶段，无历史数据包袱，可以直接切到两张专用表。

## What Changes

- **拆分 K 线表**：在 Postgres 中创建 `kline_1d`（日线）和 `kline_1m`（1 分钟线）两张表。其他分钟频率（5m / 15m / 30m / 60m）不在本次范围，未来需要时按同一模式新增。
- **重命名时间字段**：日线表的主键时间字段为 `trade_date`（`DATE NOT NULL`），分钟线表的主键时间字段为 `ts`（`TIMESTAMPTZ NOT NULL`）。
- **删除 `pre_close` 列**：两张表都不再保留 `pre_close`；写入、读取、COPY、stage 表的 SQL 全部移除。
- **删除 `interval` 列**：因为分表后 `interval` 是隐含的（表名本身就是 interval），两张表都不再保留 `interval` 列。
- **修正 `KLineRepository` 实现**：`upsert_bars` / `get_bars` / `count` 内部按 `interval` 路由到对应物理表；`Protocol` 接口保持不变（外部 caller 不知情）。
- **更新迁移**：替换现有 `kline` 表的 DDL，新增 `kline_1d` 与 `kline_1m` 的 DDL，旧 `kline` 表直接 `DROP`（开发阶段，无保留必要）。
- **BREAKING**（对 aenmic 历史迁移脚本而言）：`0001_initial.py` 中 `kline` 表的 DDL 改为新表；任何对旧 `kline` 表的 SQL 引用都要迁移到新表。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `data-market`：K 线由单表变两表（`kline_1d` / `kline_1m`）、时间字段命名（`trade_date` / `ts`）、移除 `pre_close` 与 `interval` 列、`KLineRepository.get_bars` 返回 DataFrame 的索引名随之变化（`time` → `trade_date` 或 `ts`）、`INTERVALS` 收缩为 `{"1d", "1m"}`。
- `data-migrations`：初始迁移 `0001_initial.py` 不再创建 `kline`，改为创建 `kline_1d` 与 `kline_1m`（含对应主键、约束、索引）；后续 "rebuild from scratch" 步骤需要 `DROP TABLE kline`（开发期一次性）。

## Impact

- `src/xtrade/data/market_data/kline.py`：
  - `INTERVALS` 收缩为 `{"1d", "1m"}`。
  - `KLINE_REQUIRED_COLUMNS` 移除 `interval`。
  - `upsert_bars` 移除 `interval` 列处理、按 `interval` 路由到 `kline_1d` / `kline_1m`、调整列名。
  - `get_bars` 按 `interval` 路由到对应表；SELECT 移除 `pre_close`、`interval`；返回 DataFrame 的索引名改为 `trade_date`（日线）或 `ts`（分钟线）。
  - `count` 同理路由。
  - 私有 `_upsert_chunk` / `_copy_into_staging` 的 SQL 同步调整。
- `src/xtrade/data/` 下的 ORM 模型（如果有 `KLine` Model）：列定义改为 `kline_1d` / `kline_1m` 各自的字段。
- `src/xtrade/data/migrations/versions/0001_initial.py`：删除 `kline` 表相关 `op.create_table` / `op.create_index` / `op.create_unique_constraint`，新增 `kline_1d`（`(symbol, trade_date)` 唯一）与 `kline_1m`（`(symbol, ts)` 唯一）的 DDL。
- `tests/test_kline.py`（及任何引用 `kline` 表 / `pre_close` 列 / `interval` 列的测试）：删 `pre_close` 断言、`interval` 入参断言；改表名；改索引名。
- `openspec/specs/data-market/spec.md`：`K-line reads return index by time` 这类场景描述需要更新索引名。
- 任何下游模块（`broker`、`strategy`、`backtest`）如果直接读 `df.index.name`，需要适配新的索引名（`trade_date` / `ts`）。
- 不影响 `data-sources` / `data-broker` / `engine` / `execution-broker` / `risk` / `strategy` / `core-market-data` / `xtrade-cli` / `xtrade-config` / `project-skeleton` 的外部接口。
