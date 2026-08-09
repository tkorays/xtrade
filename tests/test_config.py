"""Tests for ``xtrade.core.config`` (``Config`` + ``PostgresConfig``)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from xtrade.core import config as config_module
from xtrade.core.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_XTRADE_HOME,
    Config,
    DataConfig,
    DataDatabaseConfig,
    PostgresConfig,
    get_config,
)


@pytest.fixture
def reset_global_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the module-level cached config between tests."""
    monkeypatch.setattr(config_module, "_config", None)


@pytest.fixture(autouse=True)
def _ensure_clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip any pre-set XTRADE_/XTRADE_CONFIG env vars so tests start
    from a known baseline.
    """
    for key in list(monkeypatch.getenv.keys() if hasattr(monkeypatch, "getenv") else []):
        if key.startswith("XTRADE_") or key == "XTRADE_CONFIG":
            monkeypatch.delenv(key, raising=False)
    for var in (
        "XTRADE_CONFIG",
        "XTRADE_POSTGRES__HOST",
        "XTRADE_POSTGRES__PORT",
        "XTRADE_DATA__DATABASE__URL",
        "XTRADE_DATA__BATCH_SIZE",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_postgres_defaults_when_file_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reset_global_config: None
) -> None:
    target = tmp_path / "missing.json"
    monkeypatch.setenv("XTRADE_CONFIG", str(target))

    cfg = Config.load()

    assert cfg.postgres.host == "localhost"
    assert cfg.postgres.port == 5432
    assert cfg.postgres.user == "postgres"
    assert cfg.postgres.password == ""
    assert cfg.postgres.database == "xtrade"


def test_postgres_section_survives_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reset_global_config: None
) -> None:
    target = tmp_path / "round.json"
    monkeypatch.setenv("XTRADE_CONFIG", str(target))

    cfg = Config.load()
    cfg.postgres.port = 6543
    cfg.postgres.host = "db.local"
    cfg.save()

    reloaded = Config.load()
    assert reloaded.postgres.port == 6543
    assert reloaded.postgres.host == "db.local"
    # Untouched fields keep their defaults.
    assert reloaded.postgres.user == "postgres"
    assert reloaded.postgres.database == "xtrade"


# ---------------------------------------------------------------------------
# XTRADE_CONFIG env var
# ---------------------------------------------------------------------------


def test_xtrade_config_env_var_redirects_reads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reset_global_config: None
) -> None:
    target = tmp_path / "env-read.json"
    target.write_text(
        json.dumps({"postgres": {"host": "env-host", "port": 9876}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("XTRADE_CONFIG", str(target))

    cfg = Config.load()

    assert cfg.postgres.host == "env-host"
    assert cfg.postgres.port == 9876


def test_xtrade_config_env_var_redirects_saves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reset_global_config: None
) -> None:
    target = tmp_path / "env-save.json"
    monkeypatch.setenv("XTRADE_CONFIG", str(target))

    cfg = Config.load()
    cfg.postgres.port = 1111
    cfg.save()

    # File at the redirected path exists and contains the value.
    assert target.exists()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["postgres"]["port"] == 1111


# ---------------------------------------------------------------------------
# XTRADE_POSTGRES__* env vars override file values
# ---------------------------------------------------------------------------


def test_xtrade_env_var_overrides_file_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reset_global_config: None
) -> None:
    target = tmp_path / "env-override.json"
    target.write_text(
        json.dumps({"postgres": {"host": "file-host", "port": 5432}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("XTRADE_CONFIG", str(target))
    monkeypatch.setenv("XTRADE_POSTGRES__HOST", "env-host")
    monkeypatch.setenv("XTRADE_POSTGRES__PORT", "6543")

    cfg = Config.load()

    assert cfg.postgres.host == "env-host"
    assert cfg.postgres.port == 6543


# ---------------------------------------------------------------------------
# Corrupt JSON
# ---------------------------------------------------------------------------


def test_corrupt_json_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reset_global_config: None
) -> None:
    target = tmp_path / "bad.json"
    target.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("XTRADE_CONFIG", str(target))

    with pytest.raises(json.JSONDecodeError):
        Config.load()


# ---------------------------------------------------------------------------
# Default constants
# ---------------------------------------------------------------------------


def test_default_constants_resolve_under_home() -> None:
    assert DEFAULT_XTRADE_HOME.is_absolute()
    assert Path.home() / ".xtrade" == DEFAULT_XTRADE_HOME
    assert DEFAULT_CONFIG_PATH == DEFAULT_XTRADE_HOME / "config.json"
    assert DEFAULT_CONFIG_PATH.name == "config.json"


# ---------------------------------------------------------------------------
# get_config() global cache
# ---------------------------------------------------------------------------


def test_get_config_caches_instance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reset_global_config: None
) -> None:
    target = tmp_path / "cache.json"
    monkeypatch.setenv("XTRADE_CONFIG", str(target))

    cfg1 = get_config()
    cfg2 = get_config()
    assert cfg1 is cfg2


def test_get_config_reload_picks_up_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reset_global_config: None
) -> None:
    target = tmp_path / "reload.json"
    monkeypatch.setenv("XTRADE_CONFIG", str(target))

    cfg1 = get_config()
    assert cfg1.postgres.port == 5432

    target.write_text(
        json.dumps({"postgres": {"port": 9999}}),
        encoding="utf-8",
    )
    cfg2 = get_config(reload=True)
    assert cfg2 is not cfg1
    assert cfg2.postgres.port == 9999


# ---------------------------------------------------------------------------
# PostgresConfig model
# ---------------------------------------------------------------------------


def test_postgres_config_direct_instantiation() -> None:
    pg = PostgresConfig()
    assert pg.host == "localhost"
    assert pg.port == 5432

    pg2 = PostgresConfig(host="db", port=1234)
    assert pg2.host == "db"
    assert pg2.port == 1234


# ---------------------------------------------------------------------------
# No mos.* import
# ---------------------------------------------------------------------------


def test_no_mos_import_in_config_module() -> None:
    """The config package must not depend on mos.*."""
    mos_keys = [k for k in sys.modules if k.startswith("mos.")]
    assert mos_keys == []


def test_no_mos_dependency_in_manifest() -> None:
    """The pyproject.toml manifest must not declare mos as a dep."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    # Check dependencies block — ``mos`` as a bare package spec.
    assert '"mos' not in text
    assert "mos-core" not in text
    assert "mos_quant" not in text


# ---------------------------------------------------------------------------
# Data layer config (added in add-data-system)
# ---------------------------------------------------------------------------


def test_data_defaults_when_no_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reset_global_config: None
) -> None:
    monkeypatch.setenv("XTRADE_CONFIG", str(tmp_path / "missing.json"))
    cfg = Config.load()

    assert cfg.data.database.url == "postgresql+psycopg://postgres:postgres@localhost:5432/xtrade"
    assert cfg.data.batch_size == 10_000


def test_existing_user_file_without_data_block_still_loads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reset_global_config: None
) -> None:
    """A pre-existing ``postgres``-only config file must still load."""
    target = tmp_path / "legacy.json"
    target.write_text(
        json.dumps({"postgres": {"host": "legacy-host", "port": 6543}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("XTRADE_CONFIG", str(target))

    cfg = Config.load()

    assert cfg.postgres.host == "legacy-host"
    assert cfg.postgres.port == 6543
    # Defaults kick in for the new ``data`` section.
    assert cfg.data.database.url == "postgresql+psycopg://postgres:postgres@localhost:5432/xtrade"
    assert cfg.data.batch_size == 10_000


def test_data_user_supplied_block_round_trips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reset_global_config: None
) -> None:
    monkeypatch.setenv("XTRADE_CONFIG", str(tmp_path / "rt.json"))

    cfg = Config.load()
    cfg.data.database.url = "postgresql+psycopg://u:p@h:5432/d"
    cfg.data.batch_size = 5000
    cfg.save()

    reloaded = Config.load()
    assert reloaded.data.database.url == "postgresql+psycopg://u:p@h:5432/d"
    assert reloaded.data.batch_size == 5000


def test_xtrade_data_env_vars_override_file_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reset_global_config: None
) -> None:
    target = tmp_path / "override.json"
    target.write_text(
        json.dumps({"data": {"batch_size": 5000}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("XTRADE_CONFIG", str(target))
    monkeypatch.setenv("XTRADE_DATA__BATCH_SIZE", "20000")

    cfg = Config.load()

    assert cfg.data.batch_size == 20000


def test_xtrade_data_env_var_nested_database_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reset_global_config: None
) -> None:
    monkeypatch.setenv("XTRADE_CONFIG", str(tmp_path / "nested.json"))
    monkeypatch.setenv("XTRADE_DATA__DATABASE__URL", "postgresql+psycopg://other:5432/d")

    cfg = Config.load()

    assert cfg.data.database.url == "postgresql+psycopg://other:5432/d"


def test_data_database_config_direct_instantiation() -> None:
    db = DataDatabaseConfig(url="postgresql+psycopg://x/y")
    assert db.url == "postgresql+psycopg://x/y"

    data = DataConfig()
    assert data.batch_size == 10_000
    assert data.database.url == "postgresql+psycopg://postgres:postgres@localhost:5432/xtrade"
