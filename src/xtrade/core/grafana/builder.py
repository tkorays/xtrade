"""Typed builders for Grafana dashboards and panels.

Wraps the official ``grafana-foundation-sdk`` package and produces the
unified-API envelope shape that ``DashboardsAPI.create`` /
``DashboardsAPI.update`` expect.

The Foundation SDK returns dataclass models from ``.build()`` and
exposes a ``.to_json()`` method that yields a camelCase dict with
nested model / enum placeholders. ``_to_plain_dict`` recursively
normalises those into plain Python dicts / values.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from grafana_foundation_sdk.builders.dashboard import Dashboard as _SDKDashboard
from grafana_foundation_sdk.builders.dashboard import Panel as _SDKPanel
from grafana_foundation_sdk.builders.stat import Panel as _SDKStatPanel
from grafana_foundation_sdk.builders.timeseries import Panel as _SDKTimeseriesPanel
from grafana_foundation_sdk.models import dashboard as _sdk_models


def _to_plain_dict(obj: Any) -> Any:
    """Recursively convert SDK model / enum values into plain dicts.

    The Foundation SDK's ``.to_json()`` produces a nested structure with
    plain dicts and lists at the outer level, but leaves nested models
    and enum instances untouched. We expand anything that exposes a
    ``to_json()`` and unwrap any ``Enum`` to its ``.value``.

    Note: ``StrEnum`` members are also ``str`` instances, so the enum
    branch must run BEFORE the string branch.
    """
    if isinstance(obj, Enum):
        return obj.value
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if hasattr(obj, "to_json"):
        return _to_plain_dict(obj.to_json())
    if isinstance(obj, dict):
        return {k: _to_plain_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_plain_dict(v) for v in obj]
    return obj


class DashboardBuilder:
    """Typed wrapper around ``grafana_foundation_sdk.builders.dashboard.Dashboard``."""

    def __init__(
        self,
        title: str,
        *,
        uid: str | None = None,
        tags: list[str] | None = None,
        timezone: str | None = None,
    ) -> None:
        sdk_builder = _SDKDashboard(title)
        if uid is not None:
            sdk_builder = sdk_builder.uid(uid)
        if tags is not None:
            sdk_builder = sdk_builder.tags(tags)
        if timezone is not None:
            sdk_builder = sdk_builder.timezone(timezone)
        self._sdk_builder = sdk_builder

    def with_panel(self, panel_builder: Any) -> DashboardBuilder:
        """Append a panel builder (or any object exposing ``.build()``).

        Accepts a ``TimeseriesPanelBuilder`` / ``StatPanelBuilder`` /
        the underlying ``grafana_foundation_sdk.builders.dashboard.Panel``,
        or anything else the Foundation SDK accepts.
        """
        sdk_panel = self._unwrap_panel(panel_builder)
        self._sdk_builder = self._sdk_builder.with_panel(sdk_panel)
        return self

    def with_row(self, row_builder: Any) -> DashboardBuilder:
        """Append a row builder."""
        self._sdk_builder = self._sdk_builder.with_row(row_builder)
        return self

    @staticmethod
    def _unwrap_panel(panel_builder: Any) -> Any:
        """Extract the underlying SDK panel builder from our wrappers."""
        for attr in ("_sdk_builder", "_sdk_panel"):
            if hasattr(panel_builder, attr):
                return getattr(panel_builder, attr)
        return panel_builder

    def build(self) -> dict[str, Any]:
        """Return the dashboard JSON dict for the unified-API ``spec`` block."""
        model = self._sdk_builder.build()
        payload: dict[str, Any] = _to_plain_dict(model.to_json())
        return payload


class TimeseriesPanelBuilder:
    """Typed wrapper around ``grafana_foundation_sdk.builders.timeseries.Panel``."""

    def __init__(
        self,
        title: str,
        *,
        grid_pos: _sdk_models.GridPos | None = None,
    ) -> None:
        self._sdk_builder = _SDKTimeseriesPanel().title(title)
        if grid_pos is not None:
            self._sdk_builder = self._sdk_builder.grid_pos(grid_pos)
        else:
            # Grafana requires panels to have a ``gridPos``. Provide a
            # sensible 8x12 default at (0, 0) so the SDK accepts it.
            self._sdk_builder = self._sdk_builder.grid_pos(_sdk_models.GridPos(h=8, w=12, x=0, y=0))

    def build(self) -> dict[str, Any]:
        result: dict[str, Any] = _to_plain_dict(self._sdk_builder.build().to_json())
        return result


class StatPanelBuilder:
    """Typed wrapper around ``grafana_foundation_sdk.builders.stat.Panel``."""

    def __init__(
        self,
        title: str,
        *,
        grid_pos: _sdk_models.GridPos | None = None,
    ) -> None:
        self._sdk_builder = _SDKStatPanel().title(title)
        if grid_pos is not None:
            self._sdk_builder = self._sdk_builder.grid_pos(grid_pos)
        else:
            self._sdk_builder = self._sdk_builder.grid_pos(_sdk_models.GridPos(h=8, w=12, x=0, y=0))

    def build(self) -> dict[str, Any]:
        result: dict[str, Any] = _to_plain_dict(self._sdk_builder.build().to_json())
        return result


# Re-export the underlying SDK Panel builder so callers needing the full
# surface (custom targets, field-config overrides, etc.) can compose
# panels without leaving xtrade.core.grafana.builder.
__all_sdk__: tuple[Any, ...] = (
    _SDKPanel,
    _sdk_models.GridPos,
)


def build_envelope(
    dashboard: dict[str, Any] | Any,
    *,
    name: str,
    message: str | None = None,
    folder_uid: str | None = None,
) -> dict[str, Any]:
    """Wrap a dashboard (dict or builder) in the unified-API envelope.

    Raises ``ValueError`` when ``dashboard`` is neither a dict nor has a
    ``.build()`` method.
    """
    if isinstance(dashboard, dict):
        spec_block: dict[str, Any] = dashboard
    elif hasattr(dashboard, "build"):
        built = dashboard.build()
        if not isinstance(built, dict):
            built = _to_plain_dict(built.to_json())
        spec_block = built
    else:
        raise ValueError(
            f"dashboard must be a dict or an object with .build(); got {type(dashboard).__name__}"
        )

    annotations: dict[str, str] = {}
    if message is not None:
        annotations["grafana.app/message"] = message
    if folder_uid is not None:
        annotations["grafana.app/folder"] = folder_uid

    metadata: dict[str, Any] = {"name": name}
    if annotations:
        metadata["annotations"] = annotations

    return {"metadata": metadata, "spec": spec_block}
