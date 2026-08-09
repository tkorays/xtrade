# AGENTS.md — `xtrade` 项目开发规范

> 给 AI 代理（和贡献者）的工作约束。先读一遍再动手。

## 1. 项目概览

- **Domain**: 量化交易系统（backtest + live）。
- **Tech stack**: Python 3.13+，pandas / numpy / asyncio；Click CLI + pydantic-settings config。
- **Layout**: `src/xtrade/{cli,core,data,execution,risk,strategy}/`（src 布局）。
- **Package mgr**: uv（`uv sync`、`uv run`）。

## 2. OpenSpec 是真相之源

**任何非琐碎改动必须走 OpenSpec。** 不要先写代码再补 spec。

| 场景 | 走哪条工作流 |
|---|---|
| 新增能力 / 改动既有能力 / 重命名 / 删除 | `openspec-propose` → `openspec-apply-change` → `openspec-archive-change` |
| 只想探索、不动代码 | `openspec-explore` |
| 计划与代码出现分歧 | `openspec-update-change` |
| 修改 delta spec 但还不准备归档 | `openspec-sync-specs` |

- 一个 change = 一个能力/任务单元；不要把多个能力塞进一个 change。
- 先 `openspec status --change <name>` 看依赖图，再动手。
- 归档前必须 `openspec validate --all --strict` 全部通过。

## 3. 代码规范

- **类型**：所有公开函数 / 方法必须带类型注解；mypy strict mode。
- **格式化**：ruff format（已在 `pyproject.toml` 中配置）；PR 前 `ruff check` + `ruff format --check` 必须 clean。
- **Imports**：单文件内分组（stdlib / 第三方 / 内部），绝对不要 `import *`。
- **命名**：模块级常量 `UPPER_SNAKE`，类 `PascalCase`，函数 / 变量 `snake_case`。
- **错误**：边界处校验（用户输入、外部 API）；内部代码相信框架与契约。区分 expected error（自定义异常类）和 unexpected error（让 bubble）。
- **不要**：
  - 硬编码密钥 / 密码 / token。读环境变量或 `Config`。
  - 引入业务耦合到 `core/` 的代码（core 是横向基础）。
  - 引入 `mos.*` 依赖（参考实现，不复用）。
  - 为了"未来需要"而做的抽象。

## 4. 测试规范

- **Framework**: pytest，测试放 `tests/`，文件 `test_*.py`，函数 `test_*`。
- **覆盖**：每个公开函数至少一个 happy-path + 一个边界 case。新模块必须带测试。
- **隔离**：用 `tmp_path` 写文件；用 `monkeypatch.setenv` / `monkeypatch.setattr` 改全局状态；**不要**真实触碰 `~/.xtrade/config.json` 或真实数据库。
- **CLI 测试**：用 `click.testing.CliRunner`，通过 `XTRADE_CONFIG` 隔离配置文件。
- **标记**：慢测试加 `@pytest.mark.slow`；网络/IO 测试加 `@pytest.mark.integration`。当前默认排除它们。
- **不**允许：
  - 测试之间共享隐式状态（必须每个测试自给自足）。
  - `assert` 之外用 `print` 做断言。
  - 用真实时间 / 真实随机种子（注入 `clock` / `seed` fixture）。

## 5. 提交流程

```bash
uv sync                                            # 同步依赖
uv run pytest                                      # 全部测试通过
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src                                    # strict 通过
openspec validate --all --strict                   # spec 全部通过
```

## 6. 文档与提交

- 改 `README.md` 时保持精简：一句"是什么"，一段 quickstart，必要的 CLI/配置示例。
- 改 `openspec/config.yaml` 的 `context` 时要确保它仍准确反映现状（这是一段 AI 注入提示）。
- 提交信息：中文或英文都行，但 `type(scope): subject` 格式；例如 `feat(cli): support multiple set operands`。
