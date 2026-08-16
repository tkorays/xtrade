"""Public surface for the Grafana SDK.

Re-exports the documented symbols so callers can write
``from xtrade.core.grafana import GrafanaClient`` and reach every
documented type, error, and typed builder.
"""

from __future__ import annotations

from xtrade.core.grafana._client import GrafanaClient
from xtrade.core.grafana.builder import (
    DashboardBuilder,
    StatPanelBuilder,
    TimeseriesPanelBuilder,
    build_envelope,
)
from xtrade.core.grafana.dashboards import DashboardsAPI
from xtrade.core.grafana.errors import (
    GrafanaAPIError,
    GrafanaAuthError,
    GrafanaError,
)
from xtrade.core.grafana.panels import PanelsAPI
from xtrade.core.grafana.types import DashboardSummary, DashboardWithMeta, Panel

__all__ = [
    "DashboardBuilder",
    "DashboardSummary",
    "DashboardWithMeta",
    "DashboardsAPI",
    "GrafanaAPIError",
    "GrafanaAuthError",
    "GrafanaClient",
    "GrafanaError",
    "Panel",
    "PanelsAPI",
    "StatPanelBuilder",
    "TimeseriesPanelBuilder",
    "build_envelope",
]
