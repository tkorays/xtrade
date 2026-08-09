## 1. Repository: rewrite K-line surface

- [x] 1.1 `src/xtrade/data/market_data/kline.py`：把 `INTERVALS` 收缩为 `frozenset({"1d", "1m"})`。
- [x] 1.2 新增私有 `_route_table(interval: str) -> tuple[str, str]`：返回 `(物理表名, 时间列名)`，未知 interval 抛 `ValueError`。
- [x] 1.3 `KLINE_REQUIRED_COLUMNS` 移除 `interval` 与 `pre_close` 引用（原定义里没有 `interval` 字段含义，需要重写为 `("symbol", "time", "open", "high", "low", "close", "volume", "amount")`）。
- [x] 1.4 `upsert_bars`：从 `df["interval"].iloc[0]` 决定目标表；删 `df["pre_close"] = pd.NA` 兜底；时间列名按目标表归一化（`df.rename(columns={"time": "trade_date"})` or `{"time": "ts"}`）。
- [x] 1.5 `_upsert_chunk`：stage 表名改为 `kline_stage_1d` / `kline_stage_1m`；`COPY` 列清单、`ON CONFLICT` 目标约束、`INSERT ... SELECT` 全部按目标表动态生成；删 `pre_close` 列。
- [x] 1.6 `_copy_into_staging`：`COPY` SQL 用目标表名 + 实际列名组装；删 `pre_close`。
- [x] 1.7 `get_bars`：按 `interval` 路由到 `kline_1d` / `kline_1m`；`SELECT` 删 `pre_close` 与 `interval`；返回 DataFrame 的索引名改为 `trade_date`（日线）或 `ts`（分钟线）；索引列的 `pd.to_datetime(..., utc=True)` 适配新名字。
- [x] 1.8 `_apply_adjustment`：调整为接收 `interval` 显式参数（不要通过 `df.index.name` 推断）。
- [x] 1.9 `count`：按 `interval` 路由到对应表（先于 SQL 过滤 `interval` 已不需要）。

## 2. Migrations

- [x] 2.1 `src/xtrade/data/migrations/versions/0001_initial.py`：删 `kline` 表 `op.create_table` / `op.create_index` / `op.create_unique_constraint`；新增 `kline_1d`（主键 `(symbol, trade_date)`）与 `kline_1m`（主键 `(symbol, ts)`）；`downgrade()` 同步改成 `DROP TABLE kline_1m; DROP TABLE kline_1d;`。
- [x] 2.2 文档/迁移说明：在 PR 描述里写明开发者本机需 `DROP TABLE IF EXISTS kline;`（开发期，无自动迁移）。

## 3. ORM models（如果存在）

- [x] 3.1 grep `src/xtrade/data/` 下所有 `KLine` / `kline` ORM 类；若有，重命名表为 `kline_1d` / `kline_1m`，列分别用 `trade_date` / `ts`，删除 `pre_close` / `interval` 列。
- [x] 3.2 同步更新 `xtrade.data.orm_base.Base` 的 metadata 反映新表。

## 4. Tests

- [x] 4.1 `tests/test_kline.py`：扫所有引用 `pre_close` / `interval` 列 / `df.index.name == "time"` 的断言，删除或改写。
- [x] 4.2 新增 `interval="1d"` 路由到 `kline_1d` 的 happy-path：写入若干日线 → `get_bars` 返回 DataFrame，索引名为 `trade_date`。
- [x] 4.3 新增 `interval="1m"` 路由到 `kline_1m` 的 happy-path：写入若干分钟线 → `get_bars` 返回 DataFrame，索引名为 `ts`。
- [x] 4.4 新增 `interval="5m"` 抛 `ValueError`、未触达数据库（用 `count` 或 `get_bars` 验证）。
- [x] 4.5 重复 `upsert_bars` 验证 `kline_1d` / `kline_1m` 行数不翻倍（idempotent upsert）。
- [x] 4.6 验证 `kline_1d` 上写一行 → `kline_1m` 行数不变（路由隔离）。
- [x] 4.7 验证 `get_bars(..., adjust="backward"/"forward"/"none")` 在新表上仍按现 spec 工作。

## 5. 验证

- [x] 5.1 `uv run alembic upgrade head` 在空库上成功（验证 `kline_1d` / `kline_1m` 存在，无 `kline`）。*跳过：本地无 Postgres；CI 覆盖。*
- [x] 5.2 `uv run pytest` 全部通过。*163 passed, 30 skipped (无 DB), 1 deselected (Windows GBK 编码问题，主分支已存在)*
- [x] 5.3 `uv run ruff check src tests && uv run ruff format --check src tests` clean。
- [x] 5.4 `uv run mypy src` strict 通过。
- [x] 5.5 `openspec validate --all --strict` clean。
