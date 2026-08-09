"""Tests for ``xtrade.core.baseconfig.BaseConfig``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import Field

from xtrade.core.baseconfig import BaseConfig

# ---------------------------------------------------------------------------
# Test helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_config_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Provide a fresh temp path for each test; default-Config tests
    point ``Config.config_file_path`` at it before exercising load/save.
    """
    return tmp_path / "config.json"


def _write(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


class _DemoConfig(BaseConfig):
    """Minimal subclass used purely by the tests below."""

    config_file_path: ClassVar[Path] = Path("/tmp/xtrade-demo-default.json")

    name: str = "demo"
    count: int = 0

    @staticmethod
    def _default_nested() -> dict[str, Any]:
        return {"a": 1, "b": 2}

    nested: dict[str, Any] = Field(default_factory=_default_nested)


# ---------------------------------------------------------------------------
# load() / save() round-trip
# ---------------------------------------------------------------------------


def test_load_returns_defaults_when_file_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "missing.json"
    monkeypatch.setattr(_DemoConfig, "config_file_path", path)

    cfg = _DemoConfig.load()

    assert cfg.name == "demo"
    assert cfg.count == 0
    assert cfg.nested == {"a": 1, "b": 2}


def test_load_reads_valid_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    _write(path, {"name": "from-file", "count": 7, "nested": {"a": 99, "b": 2}})
    monkeypatch.setattr(_DemoConfig, "config_file_path", path)

    cfg = _DemoConfig.load()

    assert cfg.name == "from-file"
    assert cfg.count == 7
    # ``nested`` is a typed dict field; pydantic-settings replaces the
    # entire value rather than deep-merging into the default.
    assert cfg.nested == {"a": 99, "b": 2}


def test_load_partial_json_replaces_typed_dict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A typed-dict field with extra keys beyond the declared model is
    replaced wholesale by pydantic-settings — not deep-merged. This
    documents that behaviour; the user-facing ``update()`` API still
    deep-merges (tested separately).
    """
    path = tmp_path / "partial.json"
    _write(path, {"nested": {"a": 99}})  # ``b`` is absent from the file
    monkeypatch.setattr(_DemoConfig, "config_file_path", path)

    cfg = _DemoConfig.load()

    # Typed dict: ``nested`` is taken verbatim from the file.
    assert cfg.nested == {"a": 99}


def test_load_raises_on_corrupt_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{ not valid", encoding="utf-8")
    monkeypatch.setattr(_DemoConfig, "config_file_path", path)

    with pytest.raises(json.JSONDecodeError):
        _DemoConfig.load()


def test_save_writes_expected_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    monkeypatch.setattr(_DemoConfig, "config_file_path", path)

    cfg = _DemoConfig.load()
    cfg.count = 42
    target = cfg.save()

    assert target == path
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["count"] == 42
    # Defaults are still serialized.
    assert payload["name"] == "demo"


def test_save_round_trip_with_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "rt.json"
    monkeypatch.setattr(_DemoConfig, "config_file_path", path)

    original = _DemoConfig.load()
    original.count = 11
    original.nested = {"a": 100, "b": 200, "c": 300}
    original.save()

    reloaded = _DemoConfig.load()
    assert reloaded.count == 11
    assert reloaded.nested == {"a": 100, "b": 200, "c": 300}


def test_save_creates_parent_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "nested" / "config.json"
    monkeypatch.setattr(_DemoConfig, "config_file_path", nested)

    cfg = _DemoConfig.load()
    cfg.save()

    assert nested.exists()
    json.loads(nested.read_text(encoding="utf-8"))


def test_save_leaves_no_temp_file_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "atomic.json"
    monkeypatch.setattr(_DemoConfig, "config_file_path", path)

    _DemoConfig.load().save()

    leftover = path.parent / (path.name + ".tmp")
    assert not leftover.exists()
    assert path.exists()


def test_save_does_not_overwrite_target_on_partial_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "guard.json"
    _write(path, {"name": "original", "count": 1})
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(_DemoConfig, "config_file_path", path)

    real_open = Path.open

    def boom_open(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if str(self).endswith(".tmp") and getattr(Path, "_explode", True):
            Path._explode = False  # only explode once
            raise OSError("disk full")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", boom_open)
    Path._explode = True

    try:
        with pytest.raises(OSError):
            _DemoConfig.load().save()
    finally:
        monkeypatch.undo()
        assert path.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# update() — deep-merge semantics
# ---------------------------------------------------------------------------


def test_update_deep_merges_nested_dicts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "merge.json"
    monkeypatch.setattr(_DemoConfig, "config_file_path", path)

    base = _DemoConfig.load()
    new = base.update(count=99, nested={"a": 999})

    assert new.count == 99
    assert new.nested == {"a": 999, "b": 2}
    # Original instance is unchanged.
    assert base.count == 0
    assert base.nested == {"a": 1, "b": 2}


def test_update_coerces_string_values_via_pydantic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "coerce.json"
    monkeypatch.setattr(_DemoConfig, "config_file_path", path)

    base = _DemoConfig.load()
    new = base.update(count="123", name="renamed")

    assert new.count == 123
    assert isinstance(new.count, int)
    assert new.name == "renamed"


def test_update_then_save_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "round.json"
    monkeypatch.setattr(_DemoConfig, "config_file_path", path)

    base = _DemoConfig.load()
    base.update(count=7).save()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["count"] == 7


# ---------------------------------------------------------------------------
# get() — nested accessor
# ---------------------------------------------------------------------------


def test_get_returns_nested_value(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "get.json"
    monkeypatch.setattr(_DemoConfig, "config_file_path", path)

    cfg = _DemoConfig.load()
    assert cfg.get("name") == "demo"
    assert cfg.get("nested", "a") == 1


def test_get_returns_default_on_missing_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "get2.json"
    monkeypatch.setattr(_DemoConfig, "config_file_path", path)

    cfg = _DemoConfig.load()
    assert cfg.get("nope", default="x") == "x"
    assert cfg.get("nested", "missing", default=42) == 42


def test_get_returns_default_on_non_dict_traversal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "get3.json"
    monkeypatch.setattr(_DemoConfig, "config_file_path", path)

    cfg = _DemoConfig.load()
    # ``count`` is an int; further keying should return the default.
    assert cfg.get("count", "more", default=None) is None


# ---------------------------------------------------------------------------
# load(path=...) — per-call path override
# ---------------------------------------------------------------------------


def test_load_with_path_reads_custom_file(tmp_path: Path) -> None:
    custom = tmp_path / "custom.json"
    _write(custom, {"name": "from-custom", "count": 5})

    cfg = _DemoConfig.load(path=custom)

    assert cfg.name == "from-custom"
    assert cfg.count == 5
    assert cfg.__class__.config_file_path == custom


def test_load_with_path_save_writes_back_to_same_file(tmp_path: Path) -> None:
    custom = tmp_path / "rt.json"
    _write(custom, {"name": "x", "count": 1})

    cfg = _DemoConfig.load(path=custom)
    cfg.count = 99
    target = cfg.save()

    assert target == custom
    payload = json.loads(custom.read_text(encoding="utf-8"))
    assert payload["count"] == 99


def test_load_with_path_accepts_string(tmp_path: Path) -> None:
    custom = tmp_path / "str.json"
    _write(custom, {"name": "y", "count": 2})

    cfg = _DemoConfig.load(path=str(custom))
    assert cfg.name == "y"
    assert cfg.__class__.config_file_path == custom


def test_load_with_path_does_not_pollute_base_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Calling load(path=...) builds a dynamic subclass; the original
    base class's config_file_path must remain intact.
    """
    base_path = tmp_path / "base.json"
    _write(base_path, {"name": "base", "count": 0})
    monkeypatch.setattr(_DemoConfig, "config_file_path", base_path)

    custom = tmp_path / "custom.json"
    _write(custom, {"name": "custom", "count": 100})

    cfg_custom = _DemoConfig.load(path=custom)
    assert cfg_custom.name == "custom"

    # The base class still points at base_path.
    assert _DemoConfig.config_file_path == base_path
    # And a fresh load() (no path) reads from the base file.
    fresh = _DemoConfig.load()
    assert fresh.name == "base"


# ---------------------------------------------------------------------------
# BaseConfig — subclassing sanity
# ---------------------------------------------------------------------------


def test_subclass_subclass_uses_its_own_config_file_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two independent subclasses point at different files and read/write
    independently — the generic BaseConfig does not couple them.
    """
    a_path = tmp_path / "a.json"
    b_path = tmp_path / "b.json"
    _write(a_path, {"name": "alpha"})
    _write(b_path, {"name": "beta"})

    class A(BaseConfig):
        config_file_path: ClassVar[Path] = a_path

        name: str = "A"

    class B(BaseConfig):
        config_file_path: ClassVar[Path] = b_path

        name: str = "B"

    a = A.load()
    b = B.load()
    assert a.name == "alpha"
    assert b.name == "beta"

    # Mutating A and saving must not affect B's file.
    a.name = "alpha2"
    a.save()
    assert a_path.read_text(encoding="utf-8").find("alpha2") >= 0
    assert b_path.read_text(encoding="utf-8").find("beta") >= 0
