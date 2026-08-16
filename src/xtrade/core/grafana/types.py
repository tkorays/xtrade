"""Lightweight typed views over Grafana JSON payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DashboardSummary:
    """One row from ``GET /apis/.../dashboards``.

    Grafana 12+ unified API returns:

    ```
    {
      "metadata": {"name": <uid>, "namespace": <ns>, "uid": <uid>, ...},
      "spec": {"title": "...", "panels": [...]},
      ...
    }
    ```
    """

    uid: str
    name: str
    namespace: str
    title: str


@dataclass(frozen=True)
class Panel:
    """A minimal panel view used by ``PanelsAPI.update_panel``."""

    id: int
    title: str
    type: str


@dataclass(frozen=True)
class DashboardWithMeta:
    """Pair of envelope (``dashboard``) + ``meta`` blocks returned by Grafana.

    On the unified API, ``dashboard`` is the full envelope
    (``{ metadata: {...}, spec: {...} }``) and ``meta`` is the metadata
    block. The convenience properties give easy access to the dashboard
    JSON (``spec``) and identifying fields.

    NOTE: the REST path segment is the dashboard ``name`` (URL slug), not
    the internal ``uid``. ``DashboardsAPI`` uses ``name`` for all HTTP
    operations; ``uid`` is the Grafana-side stable id surfaced back to
    callers.
    """

    dashboard: dict[str, Any]
    meta: dict[str, Any]

    @property
    def spec(self) -> dict[str, Any]:
        """The dashboard JSON block (``spec`` field of the envelope)."""
        spec: dict[str, Any] = self.dashboard.get("spec", {})
        return spec

    @property
    def uid(self) -> str:
        """Dashboard ``uid`` (Grafana-side stable id, ``.metadata.uid``)."""
        metadata: dict[str, Any] = self.dashboard.get("metadata", {})
        return str(metadata.get("uid", ""))

    @property
    def name(self) -> str:
        """Dashboard ``name`` (URL slug, ``.metadata.name``)."""
        return str(self.dashboard.get("metadata", {}).get("name", ""))

    @property
    def namespace(self) -> str:
        """Dashboard namespace (``.metadata.namespace``)."""
        return str(self.dashboard.get("metadata", {}).get("namespace", ""))
