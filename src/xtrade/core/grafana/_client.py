"""HTTP client + auth + request plumbing for the Grafana SDK."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from xtrade.core.config import GrafanaConfig
from xtrade.core.grafana.dashboards import DashboardsAPI
from xtrade.core.grafana.errors import GrafanaAPIError, GrafanaAuthError
from xtrade.core.grafana.panels import PanelsAPI

#: Root path for the Grafana 12+ unified dashboard API.
_API_GROUP = "dashboard.grafana.app"
_API_VERSION = "v1"


def _build_auth_header(cfg: GrafanaConfig) -> dict[str, str]:
    """Pick the right ``Authorization`` header from a :class:`GrafanaConfig`.

    Precedence:

    1. ``api_key`` non-empty → Bearer.
    2. ``user`` + ``password`` non-empty → Basic.
    3. otherwise raise :class:`GrafanaAuthError` at construction time so
       the caller fails fast instead of at the first request.
    """
    if cfg.api_key:
        return {"Authorization": f"Bearer {cfg.api_key}"}
    if cfg.user and cfg.password:
        token = base64.b64encode(f"{cfg.user}:{cfg.password}".encode()).decode("ascii")
        return {"Authorization": f"Basic {token}"}
    raise GrafanaAuthError(
        "GrafanaConfig has neither api_key nor user+password; cannot authenticate."
    )


class GrafanaClient:
    """Single entry point for talking to a Grafana HTTP instance.

    Construction accepts either a :class:`GrafanaConfig` instance or the
    same keyword arguments used to build one. The underlying
    :class:`httpx.Client` is exposed as :attr:`http` so callers can patch
    headers (for example to set ``X-Grafana-Org-Id`` in a multi-org
    setup) without rebuilding the client.

    Dashboard operations go through the Grafana 12+ unified API
    (``/apis/dashboard.grafana.app/v1/namespaces/{namespace}/dashboards``).
    Use :meth:`with_namespace` to build a sibling client pointing at a
    different namespace without mutating the original.
    """

    def __init__(
        self,
        cfg: GrafanaConfig | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        **cfg_kwargs: Any,
    ) -> None:
        if cfg is None:
            cfg = GrafanaConfig(**cfg_kwargs)
        self._cfg = cfg
        self._base_url = cfg.base_url
        self._namespace = cfg.namespace
        self._headers = _build_auth_header(cfg)
        self.http = httpx.Client(
            base_url=cfg.base_url,
            headers=self._headers,
            timeout=cfg.timeout,
            verify=cfg.verify_ssl,
            transport=transport,
        )
        self.dashboards = DashboardsAPI(self)
        self.panels = PanelsAPI(self)

    # -- namespace handling -------------------------------------------------

    def _namespace_root(self) -> str:
        """Root URL (with trailing ``/``) for the unified dashboard API of the active namespace.

        Returns e.g. ``http://localhost:3000/apis/dashboard.grafana.app/v1/namespaces/default/``.
        ``_request`` joins dashboard paths onto this root via ``httpx.URL.join``.
        """
        prefix = self._base_url.rstrip("/")
        return f"{prefix}/apis/{_API_GROUP}/{_API_VERSION}/namespaces/{self._namespace}/"

    def with_namespace(self, namespace: str) -> GrafanaClient:
        """Return a sibling client pointed at ``namespace``.

        The original client is unchanged; the new client shares the
        underlying :class:`httpx.Client` (so connection pooling is
        preserved) but binds its own ``DashboardsAPI`` / ``PanelsAPI``
        instances.
        """
        clone = GrafanaClient.__new__(GrafanaClient)
        clone._cfg = self._cfg
        clone._base_url = self._base_url
        clone._namespace = namespace
        clone._headers = self._headers
        clone.http = self.http
        clone.dashboards = DashboardsAPI(clone)
        clone.panels = PanelsAPI(clone)
        return clone

    # -- request plumbing ---------------------------------------------------

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Issue a request and normalise non-2xx into :class:`GrafanaAPIError`.

        ``url`` may be absolute (e.g. ``http://host:port/apis/...``) or
        relative (resolved against the configured ``base_url``).
        Network-level failures (``httpx.HTTPError`` and subclasses) are
        NOT wrapped — the caller decides how to retry.
        """
        response = self.http.request(method, url, **kwargs)
        if response.status_code >= 400:
            body: object
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise GrafanaAPIError(
                status_code=response.status_code,
                url=str(response.url),
                body=body,
            )
        return response

    def close(self) -> None:
        """Close the underlying :class:`httpx.Client`."""
        self.http.close()
