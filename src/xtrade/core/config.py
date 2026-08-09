"""Application configuration for ``xtrade``.

Holds the project-specific :class:`Config` subclass (the ``postgres``
section is the only section so far) and the default path constants.
The generic :class:`BaseConfig` lives in :mod:`xtrade.core.baseconfig`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from xtrade.core.baseconfig import BaseConfig

# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------
# Resolved at import time. Tests should not rely on monkey-patching these;
# instead pass ``path=`` to :meth:`Config.load` (or subclass) to redirect
# the config file. Symlinking ``~/.xtrade`` is the deployment-time override.
DEFAULT_XTRADE_HOME: Path = Path.home() / ".xtrade"
DEFAULT_CONFIG_PATH: Path = DEFAULT_XTRADE_HOME / "config.json"


class PostgresConfig(BaseModel):
    """PostgreSQL connection configuration."""

    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: str = ""
    # database for the xtrade application
    database: str = "xtrade"


class Config(BaseConfig):
    """Main ``xtrade`` application config, located at
    :data:`DEFAULT_CONFIG_PATH` (``~/.xtrade/config.json`` by default).

    ``XTRADE_CONFIG`` is honoured per-instantiation: see the override of
    :meth:`BaseConfig.settings_customise_sources` below.
    """

    # Default location used by ``Config.config_file_path`` when no
    # ``XTRADE_CONFIG`` env var is set. Resolved at import time so it
    # survives later mutations of ``Path.home()`` (we don't expect that
    # to happen, but matches the mos convention).
    config_file_path: ClassVar[Path] = DEFAULT_CONFIG_PATH

    postgres: PostgresConfig = Field(default_factory=PostgresConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Read ``XTRADE_CONFIG`` per call to redirect the JSON source.

        Falls back to ``cls.config_file_path`` when the env var is
        unset. All other sources are inherited unchanged.
        """
        from pydantic_settings import JsonConfigSettingsSource  # local import

        json_path = Path(os.environ.get("XTRADE_CONFIG", str(cls.config_file_path))).expanduser()
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            JsonConfigSettingsSource(settings_cls, json_file=str(json_path)),
        )

    def save(self, path: Path | str | None = None) -> Path:
        """Persist config to the JSON path resolved via ``XTRADE_CONFIG``.

        Overrides :meth:`BaseConfig.save` so that writes honour the
        same ``XTRADE_CONFIG`` env var that reads honour. Explicit
        ``path=`` still wins; falls back to the default
        ``Config.config_file_path`` (``~/.xtrade/config.json``).
        """
        if path is None:
            env_path = os.environ.get("XTRADE_CONFIG")
            if env_path:
                path = Path(env_path).expanduser()
        return super().save(path)


_config: Config | None = None


def get_config(reload: bool = False) -> Config:
    """Get or create the global config instance.

    Args:
        reload: If ``True``, re-instantiate from disk instead of returning
            the cached instance.

    Returns:
        The global :class:`Config` instance.
    """
    global _config
    if reload or _config is None:
        _config = Config.load()
    return _config
