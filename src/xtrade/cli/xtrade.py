"""Top-level ``xtrade`` CLI.

Single console entry point that exposes the ``config`` subcommand group.
Other subcommands (init, plugin, MCP, task, streamlit, ...) are out of
scope for this change and will be added by their own capability changes.
"""

from __future__ import annotations

from importlib import metadata

import click

from xtrade import __version__ as _pkg_version
from xtrade.cli.config import config as config_cmd
from xtrade.core.logging import setup_logging


def _resolve_version() -> str:
    """Return the installed distribution version when available, else
    fall back to the in-package ``__version__``.
    """
    try:
        return metadata.version("xtrade")
    except metadata.PackageNotFoundError:
        return _pkg_version


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(_resolve_version(), "--version", "-V", prog_name="xtrade")
def cli() -> None:
    """xtrade — 量化交易系统 (backtest + live)."""
    setup_logging()


cli.add_command(config_cmd)


if __name__ == "__main__":
    cli()
