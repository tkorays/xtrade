"""``xtrade backtest`` subcommand group.

Exposes a single ``run`` subcommand that loads a user-supplied strategy
by ``module:Class`` spec, configures an in-memory broker (or postgres
broker), wires up a default :class:`NoOpRiskCheck`, and runs the
backtest for the requested window. The :class:`RunSummary` is printed
as JSON to stdout.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import click

from xtrade.engine import BacktestEngine, StrategyLoadError, load_strategy
from xtrade.execution.broker import InMemoryBroker
from xtrade.risk import NoOpRiskCheck
from xtrade.strategy.base import Bar

__all__ = ["backtest"]


def _to_jsonable(obj: Any) -> Any:
    """Recursively convert a dataclass / Decimal / datetime tree to JSON."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_jsonable(item) for item in obj]
    return obj


@click.group(name="backtest")
def backtest_group() -> None:
    """回测命令 (Backtest commands)."""


@backtest_group.command("run")
@click.option(
    "--strategy",
    required=True,
    help="策略 spec, 格式 `module:Class`.",
)
@click.option(
    "--start",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="回测起始日期 (YYYY-MM-DD)。",
)
@click.option(
    "--end",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="回测结束日期 (YYYY-MM-DD)。",
)
@click.option(
    "--symbols",
    required=True,
    help="逗号分隔的 symbol 列表。",
)
@click.option(
    "--broker",
    default="in-memory",
    type=click.Choice(["in-memory", "postgres"], case_sensitive=False),
    show_default=True,
    help="Broker 实现: in-memory (默认) 或 postgres (本期暂未联通)。",
)
@click.option(
    "--initial-cash",
    default=Decimal("1000000"),
    show_default=True,
    type=Decimal,
    help="初始资金。",
)
@click.option(
    "--strategy-params",
    default="",
    help="(可选) JSON 字符串, 策略 __init__ 参数.",
)
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(),
    help="(可选) 配置文件路径。",
)
def run_cmd(
    strategy: str,
    start: datetime,
    end: datetime,
    symbols: str,
    broker: str,
    initial_cash: Decimal,
    strategy_params: str,
    config_path: str | None,
) -> None:
    """运行回测并打印 RunSummary。

    示例:

        xtrade backtest run \\
            --strategy my_pkg:MyStrategy \\
            --start 2024-01-01 --end 2024-01-02 \\
            --symbols A,B
    """
    if end.date() < start.date():
        raise click.ClickException("--end must be on or after --start")

    params: dict[str, Any] = {}
    if strategy_params:
        try:
            params = json.loads(strategy_params)
        except json.JSONDecodeError as exc:
            raise click.ClickException(f"--strategy-params is not valid JSON: {exc}") from exc
        if not isinstance(params, dict):
            raise click.ClickException("--strategy-params must be a JSON object")

    try:
        strat = load_strategy(strategy, params)
    except StrategyLoadError as exc:
        raise click.ClickException(f"failed to load strategy: {exc}") from exc

    if broker == "postgres":
        raise click.ClickException(
            "postgres broker is not yet wired into the CLI; use --broker in-memory for now"
        )

    in_memory = InMemoryBroker(run_id="cli", initial_cash=initial_cash)
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise click.ClickException("--symbols must contain at least one symbol")

    # Default market_data: empty for each symbol. The CLI is intentionally
    # minimal; production runs should provide a real market_data callback.
    def _empty_feed(symbol: str, s: date, e: date) -> list[Bar]:
        return []

    engine = BacktestEngine(
        strategy=strat,
        broker=in_memory,
        risk=NoOpRiskCheck(),
        market_data=_empty_feed,
        symbols=symbol_list,
        run_id="cli",
    )
    summary = engine.run(start=start.date(), end=end.date())
    click.echo(json.dumps(_to_jsonable(summary), indent=2, ensure_ascii=False))


# Public alias used by the top-level CLI to register this group.
backtest = backtest_group
