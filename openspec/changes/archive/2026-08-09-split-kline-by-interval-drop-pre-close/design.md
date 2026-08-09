## Context

当前 `PostgresKLineRepository` 在 [kline.py](file:///f:/Quant/xtrade/src/xtrade/data/market_data/kline.py) 把 1d / 1m / 5m / 15m / 30m / 60m 全部塞进单一 `kline` 表，靠 `interval TEXT` 列 + 复合主键 `(symbol, time, interval)` 区分。表里还有一列 `pre_close NUMERIC(20, 6)`，从设置到使用都是死代码：写入端 `upsert_bars` 只是 `df["pre_close"] = pd.NA` 兜底，读取端 `_apply_adjustment` 只用 `open/high/low/close` 乘复权因子，没人消费 `pre_close`。

项目仍处于开发阶段，没有迁移包袱，可以直接切到两张按频率拆分的物理表，并把死字段清掉。`KLineRepository` 的对外 `Protocol` 接口、能力规约（`requirements` 级别）需要变，但下游 caller 不需要改调用方式。

## Goals / Non-Goals

**Goals:**
- K 线物理表按 interval 拆分：`kline_1d` / `kline_1m`，其他频率不在本期范围。
- 日线时间字段叫 `trade_date DATE NOT NULL`；分钟线时间字段叫 `ts TIMESTAMPTZ NOT NULL`。
- 删 `pre_close` 列、删 `interval` 列（分表后已隐含）。
- `KLineRepository` 公共 Protocol 接口 `upsert_bars / get_bars / count` 签名不变；调用方零改动。
- `INTERVALS` 收缩为 `{"1d", "1m"}`，任何其他值仍然抛 `ValueError`。
- 迁移一次性把旧 `kline` 表 DROP（开发期无历史数据）。

**Non-Goals:**
- 不引入 5m / 15m / 30m / 60m 表（未来需要时按相同模式新增）。
- 不做"按 broker 拆 kline"、不按 exchange 拆、不按 symbol hash 拆。
- 不在 K 线表里加 pre_close / post_close / adj_close 等衍生列。
- 不动 `adj_factor` / `trade_calendar` / `instrument` / `order` / `trade` / `position` / `account` 任何表。
- 不改 `Config` / `engine` / `cli` / `broker` / `execution` / `risk` / `strategy` 任何公开接口。

## Decisions

### Decision 1: 物理表按 interval 拆分，时间字段按表语义命名

两张表的 DDL：

```sql
-- kline_1d
CREATE TABLE kline_1d (
  symbol    TEXT           NOT NULL,
  trade_date DATE          NOT NULL,
  open      NUMERIC(20, 6) NOT NULL,
  high      NUMERIC(20, 6) NOT NULL,
  low       NUMERIC(20, 6) NOT NULL,
  close     NUMERIC(20, 6) NOT NULL,
  volume    BIGINT         NOT NULL,
  amount    NUMERIC(20, 4) NOT NULL,
  PRIMARY KEY (symbol, trade_date)
);

-- kline_1m
CREATE TABLE kline_1m (
  symbol TEXT           NOT NULL,
  ts     TIMESTAMPTZ    NOT NULL,
  open   NUMERIC(20, 6) NOT NULL,
  high   NUMERIC(20, 6) NOT NULL,
  low    NUMERIC(20, 6) NOT NULL,
  close  NUMERIC(20, 6) NOT NULL,
  volume BIGINT         NOT NULL,
  amount NUMERIC(20, 4) NOT NULL,
  PRIMARY KEY (symbol, ts)
);
```

**为什么不像现在这样 `interval` 列 + 复合主键？**
- 主键更窄：`kline_1d` 是 `(symbol, trade_date)` 二元组，`kline_1m` 是 `(symbol, ts)` 二元组，B-tree 索引条目更短，相同 symbol 集合下索引体积约减半。
- 查询 plan：日线查询不再需要 `WHERE interval = '1d'` 过滤，Postgres 直接命中主键前缀。
- 写入并发：日线和分钟线不再共用同一张表的 WAL / autovacuum 窗口。

**为什么 `trade_date` 用 `DATE` 而不是 `TIMESTAMPTZ`？**
- `trade_date` 的语义是"哪一个交易日"，不带日内时间；用 `DATE` 表达更自然，和现有 `trade_calendar` / `adj_factor` 的 `date` 字段类型一致。
- `ts` 用 `TIMESTAMPTZ` 因为 1 分钟线必须精确表达分钟边界（hh:mm），且 xtquant/上游数据源是带时间的。

### Decision 2: `Protocol` 接口签名不动；实现内部按 `_route_table(interval)` 路由

`_route_table(interval)` 是私有函数，返回目标物理表名 + 时间列名（`trade_date` 或 `ts`）。`upsert_bars` / `get_bars` / `count` 内部按这个元信息生成 SQL。

**接口签名不变**：
- `upsert_bars(df)` —— 还是接 `df`；`df` 里仍要求 `interval` 列（值必须是 `1d` 或 `1m` 之一），repository 内部按 `df["interval"].iloc[0]` 决定物理表。
- `get_bars(symbols, start, end, interval, adjust)` —— 多了 `interval` 路由逻辑，对外不感知。
- `count(symbol, interval)` —— 同理。

**为什么不把 `interval` 入参从 `upsert_bars` 拿掉？**
- 保持 `KLineRepository` 公共 Protocol 签名稳定（调用方零改动）。
- 复用 `df["interval"]` 列做路由，避免在 `upsert_bars` 签名加新参数。

**返回 DataFrame 索引名变化在协议内声明**：日线 `df.index.name == "trade_date"`，分钟线 `df.index.name == "ts"`。`sample scenario` 已在 `data-market` spec 中固化。

### Decision 3: `pre_close` 完全删除

不在 `kline_1d` / `kline_1m` 上保留 `pre_close`，原因：
- 没有 producer（`upsert_bars` 永远写 `pd.NA`）。
- 没有 consumer（`_apply_adjustment` 走单纯 `factor` 倍乘路径）。
- 复权完全靠 `adj_factor` 表（`ex_date` + `factor`）恢复，`pre_close` 不可恢复的"昨收"语义对当前策略无需求。

如果未来需要"昨收差价"，调用方在读取后用 `df["close"].shift(1)` 自己算，或者查 `adj_factor` 获取 `ex_date` 序列。

### Decision 4: stage 表 / COPY / ON CONFLICT 全部按"目标表 + 实际列"动态生成

`_upsert_chunk` 里的 stage 表名改为 `kline_stage_1d` / `kline_stage_1m`（因为 `CREATE TEMP TABLE IF NOT EXISTS` 在同一连接里跨 interval 复用会列结构不一致，分开命名最稳）。`COPY` 的列清单、`ON CONFLICT` 的目标约束、`INSERT ... SELECT` 的列顺序都按目标表动态生成。

实际上同一连接同一 interval 不会出现 schema 突变，stage 表 split 主要是"代码可读性 + 防止误用"。也可以维持单 `kline_stage` 但加 `IF NOT EXISTS` 防御——本期选 split 命名，因为更显式。

### Decision 5: 直接 DROP 旧 `kline` 表

调用方已在开发期，文档化要求开发者本机手动 `DROP TABLE IF EXISTS kline;`，迁移脚本不再保留任何 `kline` 表 DDL。`alembic upgrade head` 始终可重现（empty DB → `kline_1d` + `kline_1m` + 其他），无需保留旧表。

### Decision 6: `INTERVALS` 收缩

```python
INTERVALS: frozenset[str] = frozenset({"1d", "1m"})
```

`5m` / `15m` / `30m` / `60m` 一律抛 `ValueError`。不预存、其他频率未来要再开 `_route_table` 加白名单。

## Risks / Trade-offs

- **[Risk] 测试 `tests/test_kline.py` 里有引用 `pre_close` / `interval` 列 / `time` 索引名的断言** → 一并改写；测试场景要新增 `interval="1d"` vs `interval="1m"` 路由验证。
- **[Risk] 任何下游模块（broker / strategy / backtest）依赖 `df.index.name == "time"`** → 影响范围有限（当前仓库调用者只有 `kline.py` 自身 + 测试），但要 grep 一次 `time` 作为索引名的地方。
- **[Risk] SQL fixture / 本地数据库里已有 `kline` 表** → 文档化要求开发者 `DROP TABLE IF EXISTS kline;`；本仓库 `0001_initial.py` 修改后，旧 `kline` 表不会被自动迁移。
- **[Risk] `_apply_adjustment` 内部 `_route_table` 直接用 `df.index.name` 推断要查哪张表** → 不要这样做，`_apply_adjustment` 应接收 `interval` 显式参数，避免依赖索引名判断。
- **[Risk] `pd.read_sql` 读 `TIMESTAMPTZ` 列默认会被 pandas 解析成 `datetime64[ns, UTC]`** → 保持现有 `df["time"] = pd.to_datetime(df["time"], utc=True)` 的等价操作（改为按列名 `ts` / `trade_date`）。
- **[Risk] 反压：表名动态拼接增加 SQL 注入面** → 表名从 `_route_table` 返回的固定白名单 `{"kline_1d", "kline_1m"}` 取值，不接受外部输入；不需要参数化。

## Migration Plan

开发期一次性步骤（执行 `apply` 工作流时手工跑）：

1. 跑 `alembic upgrade head` 之前，执行 `DROP TABLE IF EXISTS kline;`（开发机）。
2. `apply` 工作流修改 `0001_initial.py`：`kline` → `kline_1d` + `kline_1m`。
3. `uv run alembic upgrade head` 创建新表。
4. 重跑上游数据接入（`pump`）把 1d / 1m 数据重新入库。
5. 跑 `uv run pytest` 验证全部通过。
6. 跑 `uv run mypy src` + `uv run ruff check src tests && uv run ruff format --check src tests` 通过。

回滚：开发期直接 `DROP TABLE kline_1d; DROP TABLE kline_1m;` 即可；`alembic downgrade base` 也能完整回滚。

## Open Questions

无。所有可能影响 spec / approach / tasks 分解的歧义都已在前面对话中解决。
