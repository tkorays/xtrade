"""Panel-level mutations over the Grafana 12+ unified HTTP API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from xtrade.core.grafana.types import DashboardWithMeta

if TYPE_CHECKING:
    from xtrade.core.grafana._client import GrafanaClient


class PanelsAPI:
    """Mutations of a single panel inside an existing dashboard.

    The unified API nests the panel list inside ``spec.panels``; the SDK
    reads / writes that path so callers can treat the dashboard as a
    normal envelope.
    """

    #: Keys on a dashboard panel object that ``update_panel`` can patch.
    _PATCHABLE_KEYS = (
        "title",
        "description",
        "targets",
        "options",
        "fieldConfig",
        "transparent",
    )

    def __init__(self, client: GrafanaClient) -> None:
        self._client = client

    def update_panel(
        self,
        dashboard_name: str,
        panel_id: int,
        *,
        title: str | None = None,
        description: str | None = None,
        targets: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
        field_config: dict[str, Any] | None = None,
        transparent: bool | None = None,
        panel_builder: object | None = None,
        message: str = "Panel updated via xtrade",
    ) -> DashboardWithMeta:
        """Update a single panel by ``id`` inside the named dashboard.

        ``dashboard_name`` is the dashboard's URL slug
        (``:attr:`DashboardWithMeta.name```), not the Grafana-side
        ``uid``. Fetches the dashboard envelope, mutates only the
        fields explicitly supplied on the panel with matching ``id``,
        and posts the envelope back via :meth:`DashboardsAPI.update`.
        Raises :class:`ValueError` when no panel with ``panel_id``
        exists on the dashboard (no ``POST .../dashboards/{name}`` is
        sent in that case).

        When ``panel_builder`` is supplied, its ``.build()`` dict's
        top-level keys are merged into the existing panel before the
        POST. Explicit kwargs (``title``, ``targets``, ...) take
        precedence — they overwrite any field the builder would have set.
        """
        if panel_builder is not None and not hasattr(panel_builder, "build"):
            raise ValueError(
                f"panel_builder must expose .build() or be None; got {type(panel_builder).__name__}"
            )
        current = self._client.dashboards.get(dashboard_name)
        spec = current.spec
        if not isinstance(spec, dict):
            raise ValueError(
                f"dashboard {dashboard_name} has no spec dict (got {type(spec).__name__})"
            )
        panels = spec.get("panels")
        if not isinstance(panels, list):
            raise ValueError(
                f"dashboard {dashboard_name} has no 'spec.panels' list (got {type(panels).__name__})"
            )
        for panel in panels:
            if isinstance(panel, dict) and panel.get("id") == panel_id:
                if panel_builder is not None:
                    self._apply_builder(panel, panel_builder)
                self._apply_overrides(
                    panel,
                    title=title,
                    description=description,
                    targets=targets,
                    options=options,
                    field_config=field_config,
                    transparent=transparent,
                )
                return self._client.dashboards.update(
                    dashboard_name, current.dashboard, message=message
                )
        raise ValueError(f"panel id {panel_id} not found in dashboard {dashboard_name}")

    @classmethod
    def _apply_builder(cls, panel: dict[str, Any], panel_builder: object) -> None:
        """Overlay a panel builder's output into ``panel``.

        Top-level keys from the builder's ``.build()`` dict overwrite
        the corresponding keys in the existing panel. Subsequent
        explicit kwargs (``title=``, ``targets=``, ...) overwrite any
        field the builder just set, so the kwarg path keeps its
        precedence.
        """
        from xtrade.core.grafana.builder import _to_plain_dict

        built = panel_builder.build()  # type: ignore[attr-defined]
        if not isinstance(built, dict):
            built = _to_plain_dict(built.to_json())
        for key, value in built.items():
            panel[key] = value

    @classmethod
    def _apply_overrides(
        cls,
        panel: dict[str, Any],
        *,
        title: str | None,
        description: str | None,
        targets: list[dict[str, Any]] | None,
        options: dict[str, Any] | None,
        field_config: dict[str, Any] | None,
        transparent: bool | None,
    ) -> None:
        overrides: dict[str, Any] = {
            "title": title,
            "description": description,
            "targets": targets,
            "options": options,
            "fieldConfig": field_config,
            "transparent": transparent,
        }
        for key, value in overrides.items():
            if value is not None:
                panel[key] = value
