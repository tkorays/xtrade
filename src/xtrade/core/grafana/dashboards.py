"""Dashboard CRUD over the Grafana 12+ unified HTTP API.

Every dashboard operation goes through:

    {cfg.base_url}/apis/dashboard.grafana.app/v1/namespaces/{namespace}/dashboards[...]
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from xtrade.core.grafana.types import DashboardSummary, DashboardWithMeta

if TYPE_CHECKING:
    from xtrade.core.grafana._client import GrafanaClient

_LIST_LIMIT = 200


def _root(client: GrafanaClient) -> str:
    """Absolute dashboard-collection root for the client's active namespace."""
    return client._namespace_root() + "dashboards"


class DashboardsAPI:
    """CRUD operations on Grafana dashboards (Grafana 12+ unified API)."""

    def __init__(self, client: GrafanaClient) -> None:
        self._client = client

    # -- read ---------------------------------------------------------------

    def list(self) -> list[DashboardSummary]:
        """Iterate ``GET .../dashboards?limit=200`` over the ``continue`` token."""
        url = _root(self._client)
        out: list[DashboardSummary] = []
        params: dict[str, str | int] = {"limit": _LIST_LIMIT}
        while True:
            response = self._client._request("GET", url, params=params)
            body = response.json()
            items = body.get("items", [])
            out.extend(_summary_from_envelope(item) for item in items)
            token = body.get("metadata", {}).get("continue")
            if not token:
                return out
            params = {"limit": _LIST_LIMIT, "continue": token}

    def get(self, name: str) -> DashboardWithMeta:
        """Fetch a dashboard by ``name`` (``GET .../dashboards/{name}``).

        ``name`` is the URL-safe slug Grafana uses in its REST path
        (``metadata.name``). For convenience the parameter is still
        named ``uid`` historically — pass the dashboard's
        ``metadata.name`` (the value returned in
        :attr:`DashboardSummary.name` / :attr:`DashboardWithMeta.name`).
        """
        response = self._client._request("GET", f"{_root(self._client)}/{name}")
        envelope = response.json()
        return DashboardWithMeta(
            dashboard=envelope,
            meta=envelope.get("metadata", {}),
        )

    # -- write --------------------------------------------------------------

    def create(
        self,
        payload: dict[str, Any] | object,
        *,
        message: str = "Created via xtrade",
        folder_uid: str | None = None,
    ) -> DashboardWithMeta:
        """Create a new dashboard (``POST .../dashboards``).

        ``payload`` may be:

        * a dict that is the unified-API envelope (``metadata.name`` + ``spec``); or
        * an object exposing ``.build() -> dict`` (e.g. a
          :class:`xtrade.core.grafana.builder.DashboardBuilder`). The
          SDK calls ``.build()`` and wraps the result in the envelope
          using ``message`` / ``folder_uid``.

        Raises :class:`ValueError` when ``payload`` is neither a dict nor
        exposes ``.build()``. Raises ``ValueError`` when the resulting
        envelope is missing ``metadata.name`` or ``spec``.
        """
        envelope = self._build_create_envelope(payload, message=message, folder_uid=folder_uid)
        response = self._client._request(
            "POST",
            _root(self._client),
            json=envelope,
        )
        new_envelope = response.json()
        return DashboardWithMeta(
            dashboard=new_envelope,
            meta=new_envelope.get("metadata", {}),
        )

    def update(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        message: str = "Updated via xtrade",
        folder_uid: str | None = None,
    ) -> DashboardWithMeta:
        """Update an existing dashboard (``PUT .../dashboards/{name}``).

        ``name`` is the dashboard's URL slug (``:attr:`DashboardWithMeta.name```).
        Forces ``payload["metadata"]["name"] = name``; raises ``ValueError``
        when ``spec`` is missing.
        """
        envelope = _validate_update_envelope(
            payload, name=name, message=message, folder_uid=folder_uid
        )
        response = self._client._request(
            "PUT",
            f"{_root(self._client)}/{name}",
            json=envelope,
        )
        new_envelope = response.json()
        return DashboardWithMeta(
            dashboard=new_envelope,
            meta=new_envelope.get("metadata", {}),
        )

    def delete(self, uid: str) -> None:
        """Delete a dashboard (``DELETE .../dashboards/{uid}``). 204 → None."""
        self._client._request("DELETE", f"{_root(self._client)}/{uid}")
        return None

    def _build_create_envelope(
        self,
        payload: dict[str, Any] | object,
        *,
        message: str,
        folder_uid: str | None,
    ) -> dict[str, Any]:
        """Turn a dict or builder into a validated unified-API envelope."""
        # Lazy import to avoid an import cycle: builder.py already
        # imports from xtrade.core.grafana.errors and could in future
        # import this module's types.
        from xtrade.core.grafana.builder import build_envelope

        if isinstance(payload, dict):
            return _envelope_from_envelope_dict(payload, message=message, folder_uid=folder_uid)
        if hasattr(payload, "build"):
            built = payload.build()
            if not isinstance(built, dict):
                raise ValueError(f"builder.build() must return a dict; got {type(built).__name__}")
            # ``built`` is the dashboard JSON (i.e. the future ``spec`` block);
            # we still need a name, so we fall through to ``build_envelope``
            # which wraps it with metadata.
            return build_envelope(built, name="", message=message, folder_uid=folder_uid)
        raise ValueError(f"payload must be a dict or expose .build(); got {type(payload).__name__}")


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def _summary_from_envelope(envelope: dict[str, Any]) -> DashboardSummary:
    metadata = envelope.get("metadata", {}) if isinstance(envelope, dict) else {}
    spec = envelope.get("spec", {}) if isinstance(envelope, dict) else {}
    return DashboardSummary(
        uid=str(metadata.get("uid", "")),
        name=str(metadata.get("name", "")),
        namespace=str(metadata.get("namespace", "")),
        title=str(spec.get("title", "")),
    )


def _envelope_from_envelope_dict(
    payload: dict[str, Any],
    *,
    message: str,
    folder_uid: str | None,
) -> dict[str, Any]:
    """Wrap an envelope-shaped dict in a fresh envelope with the SDK annotations.

    Requires the envelope to have ``metadata.name`` (non-empty string)
    and ``spec`` (dict). The caller passes these up front; the SDK does
    not derive ``name`` from anything else for dict payloads.
    """
    if not isinstance(payload.get("metadata"), dict):
        raise ValueError("payload must contain metadata (dict)")
    metadata = payload["metadata"]
    name = metadata.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("payload must contain metadata.name (non-empty string)")
    if not isinstance(payload.get("spec"), dict):
        raise ValueError("payload must contain spec (dict)")
    spec = payload["spec"]
    return _build_envelope(metadata, spec, message=message, folder_uid=folder_uid)


def _validate_update_envelope(
    payload: dict[str, Any],
    *,
    name: str,
    message: str,
    folder_uid: str | None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    metadata = payload.get("metadata")
    spec = payload.get("spec")
    if not isinstance(metadata, dict):
        raise ValueError("payload must contain metadata (dict)")
    if not isinstance(spec, dict):
        raise ValueError("payload must contain spec (dict)")
    metadata["name"] = name
    return _build_envelope(metadata, spec, message=message, folder_uid=folder_uid)


def _build_envelope(
    metadata: dict[str, Any],
    spec: dict[str, Any],
    *,
    message: str,
    folder_uid: str | None,
) -> dict[str, Any]:
    """Wrap ``metadata`` + ``spec`` with the standard grafana.app annotations.

    Caller-supplied annotations on ``metadata.annotations`` are preserved;
    only ``grafana.app/message`` and (when provided) ``grafana.app/folder``
    are owned by this SDK.
    """
    annotations = dict(metadata.get("annotations") or {})
    annotations["grafana.app/message"] = message
    if folder_uid is not None:
        annotations["grafana.app/folder"] = folder_uid
    new_metadata = dict(metadata)
    new_metadata["annotations"] = annotations
    return {"metadata": new_metadata, "spec": spec}
