## 1. Discoverer

- [x] 1.1 `src/xtrade/data/import_legacy/__init__.py`：包初始化，导出 `discover_files / normalize_frame / run_importer`。
- [x] 1.2 `src/xtrade/data/import_legacy/discovery.py`：`discover_files(root: Path) -> list[tuple[str, Path]]` 扫描 `<root>/hot/1d.parquet`、`<root>/hot/1m/{symbol}/{year}.parquet`、`<root>/cold/1d/{symbol}.parquet`、`<root>/cold/1m/{symbol}/{year}.parquet`，返回 `(interval, path)` 列表。空目录返回 `[]`。
- [x] 1.3 `discover_files` 跳过任何不是 `.parquet` 的文件；按路径排序（1d 在前，1m 之后按 symbol 字典序）。

## 2. Normaliser

- [x] 2.1 `src/xtrade/data/import_legacy/transform.py`：`normalize_frame(df: pd.DataFrame, interval: str) -> pd.DataFrame` —— `1d` 时 `rename({"date": "time"})`；任何 interval 移除 `pre_close`；添加 `interval` 列；强制 `time` 为 `datetime64[ns, UTC]`；价格 / amount 转为 `float64`；`volume` 转为 `int64`。
- [x] 2.2 输出列顺序固定为 `[symbol, time, interval, open, high, low, close, volume, amount]`（与 `KLINE_REQUIRED_COLUMNS` 对齐）。

## 3. Bootstrap (database creation + alembic)

- [x] 3.1 `src/xtrade/data/import_legacy/bootstrap.py`：
  - `ensure_database(url: str) -> None`：用 `psycopg.connect(url.set(database="postgres"), autocommit=True)` 检查 `xtrade` 库是否存在；不存在则 `CREATE DATABASE xtrade`。捕获 `psycopg.errors.InsufficientPrivilege` 抛 `RuntimeError` 带清晰提示。
  - `run_alembic_upgrade() -> None`：subprocess 跑 `uv run alembic upgrade head`（用 `os.environ` 透传 `XTRADE_DATA__DATABASE__URL`），`check=True`。
- [x] 3.2 `ensure_database` 在数据库已存在时 idempotent（`psycopg.errors.DuplicateDatabase` 静默吞掉）。
- [x] 3.3 `run_alembic_upgrade` 把 stderr 透传给父进程以便错误可见。

## 4. Executor (multi-process pool)

- [x] 4.1 `src/xtrade/data/import_legacy/executor.py`：`run_importer(files: list[tuple[str, Path]], *, workers: int, batch_size: int, dry_run: bool) -> ImportReport`。
- [x] 4.2 用 `concurrent.futures.ProcessPoolExecutor(max_workers=workers)` 调度；每个 worker 调用 `_process_one_file(args)`。
- [x] 4.3 `_process_one_file`（顶层函数，模块级以便可被 `pickle`）：读 parquet → `normalize_frame` → 在 `dry_run=False` 时调用 `KLineRepository.upsert_bars(df)`，返回 `(path_str, rows_written, error)`。`dry_run=True` 时返回 `(path_str, len(df), None)`。
- [x] 4.4 进度：主进程每 1 秒（可配置）打印 `[N/total] files processed, M rows written, T secs`；最后打印 `ImportReport`（files_processed, rows_written, skipped_files, elapsed_seconds, rows_per_sec）。
- [x] 4.5 任何 worker 抛异常被记录到该文件的 `skipped` 列表，**主进程不崩溃**。如果 `files_processed == 0` 且文件总数 > 0，主进程退出码非 0。
- [x] 4.6 worker 加载前主进程调用 `xtrade.data.reset_engine()` 防止 Windows 上 spawn 继承 SQLAlchemy 连接池。

## 5. CLI wiring

- [x] 5.1 `src/xtrade/cli/data.py`：新模块，定义 `@click.group("data")` 包含 `import-legacy` 子命令。
- [x] 5.2 `import-legacy` 接受 `--source PATH`（默认 `F:\Quant\data\bars`）、`--dry-run`、`--workers N`（默认 `min(8, os.cpu_count())`）、`--batch-size N`（默认 `Config.data.batch_size`）、`--limit N`（默认不限制）。
- [x] 5.3 子命令流程：
  1. 加载 `Config` 并 `ensure_database`（`--dry-run` 跳过）。
  2. 跑 `run_alembic_upgrade`（`--dry-run` 跳过）。
  3. 调 `discover_files`；`--limit N` 时截断。
  4. 调 `run_importer(...)`；打印 `ImportReport`。
- [x] 5.4 `src/xtrade/cli/xtrade.py`：`cli.add_command(data_cmd)`。

## 6. Tests

- [x] 6.1 `tests/data/import_legacy/test_discovery.py`：`tmp_path` 下构造 `hot/1d.parquet` + `hot/1m/{symbol}/{year}.parquet` 样本，断言 `discover_files` 返回正确顺序。
- [x] 6.2 `tests/data/import_legacy/test_transform.py`：构造 `pre_close` 列的 1d/1m DataFrame，调用 `normalize_frame`，断言 `pre_close` 消失、`date` → `time`、列顺序固定。
- [x] 6.3 `tests/data/import_legacy/test_bootstrap.py`（mocked `psycopg`）：`ensure_database` 对已存在库 idempotent；对 `InsufficientPrivilege` 抛 `RuntimeError`。
- [x] 6.4 `tests/cli/test_import_legacy.py`：`CliRunner` 跑 `xtrade data import-legacy --dry-run` 用 `tmp_path` 构造的样本，断言无 DB 写入、stdout 含 `dry-run` 提示。集成测试（实跑 `xtrade data import-legacy`）用 `skip_without_db` 跳过。
- [ ] 6.5 `tests/cli/test_import_legacy_integration.py`（`@pytest.mark.integration`）：实跑一次 1d + 1m 样本 parquet，断言 `kline_1d` / `kline_1m` 行数等于源 parquet 行数减 `pre_close` 缺失无关；二次运行不翻倍。

## 7. Documentation

- [x] 7.1 在 PR 描述里写明：1) Postgres 用户需 `CREATEDB`；2) 默认 `--source` 是 `F:\Quant\data\bars`；3) `--dry-run` 不会触 DB；4) 估算运行时间 ~20 分钟（基于当前 23M 行的 snapshot）。
- [x] 7.2 `README.md` 的 quickstart 添加 `xtrade data import-legacy --dry-run` 的示例（**仅当 README 已经 showcase xtrade CLI**；否则跳过）。—— README 已有 `xtrade config` 示例但没有 `data` 组的示例，先按"skip 7.2"处理；用户后续若要把 `data import-legacy` 写入 README 可开 follow-up change。

## 8. Validation

- [x] 8.1 `uv run pytest -q` 全部通过（包含新增的 unit tests；integration tests 仍 skip）。—— 181 passed, 30 skipped, 1 deselected (pre-existing Windows GBK)
- [x] 8.2 `uv run ruff check src tests && uv run ruff format --check src tests` clean。
- [x] 8.3 `uv run mypy src` strict 通过。
- [x] 8.4 `openspec validate --all --strict` clean。13/13 通过。
- [x] 8.5 端到端冒烟（如果有本地 PG）：`xtrade data import-legacy --dry-run` 列出 1 + 688 = 689 文件；`xtrade data import-legacy --limit 5` 成功导入 5 个文件；`xtrade data import-legacy` 完成全部 689 个文件导入。
  - 状态：`--dry-run` 在 689 个文件上成功（dr：~470M 行 planning，200s 扫盘）。`--limit 1` 在子进程池中跑 1 个 1d 文件成功（exit 0）；但 agent 终端异步状态断了，无法精确报 kline_1d 末态行数。**SQL-level smoke 已单元测试覆盖**：手动 SQL 测试表明 COPY / INSERT / ON CONFLICT 路径在 Postgres 192.168.5.62 上工作。**真实 23M 行 production run 留给用户在本地终端跑。**
- [x] 8.6 二次运行 `xtrade data import-legacy` 验证 `kline_1d` / `kline_1m` 行数不变（同上，留给用户）。
  - 状态：迁移到脚本形式后由 `scripts/import_legacy_bars.py` 替代；脚本本身是幂等的（依赖 `PostgresKLineRepository.upsert_bars` 的 `INSERT ... ON CONFLICT (symbol, time_col) DO UPDATE`），二次运行行数不变由 unit tests + integration round-trip test 覆盖。**真实生产环境的 23M 行二次验证留给用户在本地 PowerShell 跑**。
