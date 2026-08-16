"""Tests for ``xtrade.core.grafana.builder`` — Foundation SDK wrappers.

No real Grafana is contacted. Tests build dashboards / panels with the
typed wrappers and inspect the returned dicts / envelopes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from xtrade.core.grafana.builder import (
    DashboardBuilder,
    StatPanelBuilder,
    TimeseriesPanelBuilder,
    build_envelope,
)

# ---------------------------------------------------------------------------
# DashboardBuilder
# ---------------------------------------------------------------------------


def test_dashboard_builder_title_and_tags_flow_into_spec() -> None:
    dash = DashboardBuilder(title="PnL", tags=["xtrade"])
    built = dash.build()
    assert built["title"] == "PnL"
    assert built["tags"] == ["xtrade"]


def test_dashboard_builder_default_schema_version_is_sdk_default() -> None:
    """When no ``schema_version`` is provided, the SDK's own default applies."""
    built = DashboardBuilder(title="X").build()
    assert built["schemaVersion"] == 42


def test_dashboard_builder_uid_and_timezone() -> None:
    built = DashboardBuilder(title="X", uid="abc", timezone="browser").build()
    assert built["uid"] == "abc"
    assert built["timezone"] == "browser"


def test_dashboard_builder_with_panel_appends_panel() -> None:
    dash = (
        DashboardBuilder(title="D")
        .with_panel(TimeseriesPanelBuilder("P1"))
        .with_panel(StatPanelBuilder("P2"))
    )
    built = dash.build()
    assert len(built["panels"]) == 2
    assert built["panels"][0]["title"] == "P1"
    assert built["panels"][0]["type"] == "timeseries"
    assert built["panels"][1]["title"] == "P2"
    assert built["panels"][1]["type"] == "stat"


def test_dashboard_builder_with_panel_accepts_raw_sdk_builder() -> None:
    """The underlying ``grafana_foundation_sdk.builders.timeseries.Panel`` also works."""
    from grafana_foundation_sdk.builders.timeseries import Panel as RawTS

    dash = DashboardBuilder(title="D").with_panel(RawTS().title("P1"))
    built = dash.build()
    assert built["panels"][0]["title"] == "P1"


def test_dashboard_builder_build_returns_camelcase_dict() -> None:
    """SDK uses camelCase keys — verify they survive the wrapper."""
    built = DashboardBuilder(title="X").build()
    assert "schemaVersion" in built
    assert "fiscalYearStartMonth" in built
    assert "graphTooltip" in built


# ---------------------------------------------------------------------------
# Panel builders
# ---------------------------------------------------------------------------


def test_timeseries_panel_builder_returns_typed_panel_dict() -> None:
    panel = TimeseriesPanelBuilder(title="Net").build()
    assert panel["type"] == "timeseries"
    assert panel["title"] == "Net"
    assert "gridPos" in panel
    assert "options" in panel
    assert "fieldConfig" in panel


def test_stat_panel_builder_returns_typed_panel_dict() -> None:
    panel = StatPanelBuilder(title="Count").build()
    assert panel["type"] == "stat"
    assert panel["title"] == "Count"
    assert "gridPos" in panel


def test_panel_builder_dict_serialises_to_json() -> None:
    """The dict from ``.build()`` round-trips through ``json.dumps``."""
    panel = TimeseriesPanelBuilder(title="Net").build()
    s = json.dumps(panel)
    parsed = json.loads(s)
    assert parsed["type"] == "timeseries"
    assert parsed["title"] == "Net"


# ---------------------------------------------------------------------------
# build_envelope
# ---------------------------------------------------------------------------


def test_build_envelope_with_dashboard_builder() -> None:
    dash = DashboardBuilder(title="X")
    env = build_envelope(dash, name="x")
    assert env["metadata"]["name"] == "x"
    assert env["spec"]["title"] == "X"


def test_build_envelope_with_raw_dict_uses_it_as_spec() -> None:
    env = build_envelope({"title": "Y"}, name="y", message="hi")
    assert env["metadata"]["name"] == "y"
    assert env["metadata"]["annotations"]["grafana.app/message"] == "hi"
    assert env["spec"]["title"] == "Y"


def test_build_envelope_adds_folder_annotation_when_provided() -> None:
    env = build_envelope({"title": "X"}, name="x", folder_uid="f1")
    assert env["metadata"]["annotations"]["grafana.app/folder"] == "f1"


def test_build_envelope_omits_annotations_when_no_message_or_folder() -> None:
    env = build_envelope({"title": "X"}, name="x")
    assert "annotations" not in env["metadata"]


def test_build_envelope_rejects_unsupported_input() -> None:
    with pytest.raises(ValueError, match="dict or an object"):
        build_envelope(42, name="x")  # type: ignore[arg-type]


def test_build_envelope_accepts_object_with_build_method() -> None:
    class FakeBuilder:
        def build(self) -> dict[str, object]:
            return {"title": "F"}

    env = build_envelope(FakeBuilder(), name="f")
    assert env["spec"]["title"] == "F"
    assert env["metadata"]["name"] == "f"


# ---------------------------------------------------------------------------
# Layering
# ---------------------------------------------------------------------------


def test_builder_module_does_not_import_business_layers() -> None:
    """``xtrade.core.grafana.builder`` MUST NOT import business layers or ``mos.*``."""
    builder_path = (
        Path(__file__).resolve().parents[2] / "src" / "xtrade" / "core" / "grafana" / "builder.py"
    )
    text = builder_path.read_text(encoding="utf-8")
    forbidden_business = (
        "xtrade.strategy",
        "xtrade.execution",
        "xtrade.engine",
        "xtrade.data",
        "xtrade.risk",
    )
    for needle in forbidden_business:
        assert needle not in text, f"builder.py imports {needle}"
    assert "mos." not in text, "builder.py imports mos.*"

    forbidden_mos = [k for k in sys.modules if k.startswith("mos.")]
    assert forbidden_mos == []
