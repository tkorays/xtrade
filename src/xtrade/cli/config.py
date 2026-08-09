"""``xtrade config`` subcommand group.

Mirrors the surface area of ``mos config``: ``list``, ``get``, ``set``,
and ``types``. Only the ``main`` config type is registered for now.
"""

from __future__ import annotations

import json
from typing import Any

import click
from pydantic import ValidationError

from xtrade.core.config import Config, get_config


@click.group(name="config")
def config_group() -> None:
    """配置管理命令 (Manage application configuration)."""


def _format_value(value: Any) -> str:
    """Format a config value as a human-readable string."""
    value_type = type(value)
    if value_type is bool:
        return "true" if value else "false"
    if value_type in (list, tuple):
        return ", ".join(str(item) for item in value)
    return str(value)


def _print_config_tree(data: Any, prefix: str = "") -> None:
    """Recursively print a config tree to stdout."""
    data_type = type(data)
    if data_type is not dict:
        click.echo(f"{prefix}{data}")
        return

    for key, value in data.items():
        value_type = type(value)
        if value is None:
            click.echo(f"{prefix}{key}: null")
        elif value_type is dict:
            click.echo(f"{prefix}{key}:")
            _print_config_tree(value, prefix + "  ")
        else:
            click.echo(f"{prefix}{key}: {_format_value(value)}")


def _coerce_cli_value(value: str) -> Any:
    """Infer a Python scalar from a CLI string literal.

    Supported coercions:
      - ``~...`` → returned as-is (preserves user-supplied strings).
      - ``true`` / ``false`` → ``bool``.
      - integer strings → ``int``.
      - numeric strings → ``float``.
      - JSON arrays / objects (``[`` or ``{`` prefix) → parsed.
      - anything else → plain string.
    """
    if value.startswith("~"):
        return value
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value.isdigit():
        return int(value)
    try:
        return float(value)
    except ValueError:
        if value.startswith("[") or value.startswith("{"):
            return json.loads(value)
        return value


@config_group.command("list")
@click.option(
    "--type",
    "config_type",
    default="main",
    show_default=True,
    help="配置类型: main(主配置)。其他类型尚未注册。",
)
def list_cmd(config_type: str) -> None:
    """列出所有配置项。

    示例:

        xtrade config list            # 列出主配置
        xtrade config list --type main
    """
    if config_type != "main":
        raise click.ClickException(f"未知的配置类型: '{config_type}'。目前仅支持 'main'。")
    try:
        cfg = get_config()
        config_dict = cfg.model_dump()
        config_file = Config.config_file_path

        click.echo(f"当前配置 ({config_type}):\n")
        click.echo(f"配置文件: {config_file}")
        click.echo(f"配置文件存在: {config_file.exists()}")
        click.echo()
        _print_config_tree(config_dict)
        click.echo()
        click.echo("提示: 使用 'xtrade config set <key> <value>' 修改配置")
    except click.ClickException:
        raise
    except Exception as exc:
        raise click.ClickException(f"列出配置失败: {exc}") from exc


@config_group.command("get")
@click.argument("key")
@click.option(
    "--type",
    "config_type",
    default="main",
    show_default=True,
    help="配置类型: main(主配置)。",
)
def get_cmd(key: str, config_type: str) -> None:
    """获取指定配置项的值。

    示例:

        xtrade config get postgres.port
        xtrade config get postgres.host --type main
    """
    if config_type != "main":
        raise click.ClickException(f"未知的配置类型: '{config_type}'。目前仅支持 'main'。")
    try:
        cfg = get_config()
        value = cfg.get(*key.split("."))
        if value is None:
            click.echo(f"配置项 '{config_type}.{key}' 不存在")
            return
        click.echo(f"{config_type}.{key} = {_format_value(value)}")
    except click.ClickException:
        raise
    except Exception as exc:
        raise click.ClickException(f"获取配置失败: {exc}") from exc


@config_group.command("set")
@click.argument("key")
@click.argument("value")
@click.option(
    "--type",
    "config_type",
    default="main",
    show_default=True,
    help="配置类型: main(主配置)。",
)
def set_cmd(key: str, value: str, config_type: str) -> None:
    """设置配置项并写回磁盘。

    示例:

        xtrade config set postgres.port 5433
        xtrade config set postgres.host db.local
        xtrade config set postgres.host ~keep-as-string
    """
    if config_type != "main":
        raise click.ClickException(f"未知的配置类型: '{config_type}'。目前仅支持 'main'。")
    try:
        keys = key.split(".")
        if not keys or not all(keys):
            raise click.ClickException(f"无效的 key: {key!r}")

        # Reload from disk to pick up any changes made outside this process
        # (or by an earlier ``set`` in a multi-step CLI invocation).
        current = get_config(reload=True)
        override: dict[str, Any] = {}
        cursor: dict[str, Any] = override
        for k in keys[:-1]:
            cursor[k] = {}
            cursor = cursor[k]
        cursor[keys[-1]] = _coerce_cli_value(value)

        try:
            new_cfg = current.update(**override)
        except ValidationError as exc:
            raise click.ClickException(f"配置项 {key!r} 校验失败: {exc}") from exc

        target = new_cfg.save()
        # Reload the global so subsequent commands see the new state.
        get_config(reload=True)

        click.echo(f"[OK] 已设置 {config_type}.{key} = {value}")
        click.echo(f"  配置文件: {target}")
        click.echo()
        click.echo("提示: 某些配置可能需要重启应用才能生效")
    except click.ClickException:
        raise
    except Exception as exc:
        raise click.ClickException(f"设置配置失败: {exc}") from exc


@config_group.command("types")
def types_cmd() -> None:
    """列出所有可用的配置类型。"""
    click.echo("可用的配置类型:")
    click.echo("  main - xtrade 主配置 (postgres)")
    click.echo()
    click.echo("示例:")
    click.echo("  xtrade config list --type main")


# Public alias used by the top-level CLI to register this group.
config = config_group
__all__ = ["config", "config_group"]
