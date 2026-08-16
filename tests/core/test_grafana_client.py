"""Tests for ``xtrade.core.grafana`` — Grafana SDK against the unified API.

No real Grafana is contacted. Tests inject a small ``FakeTransport``
(callable matching ``httpx.MockTransport``'s contract) into
``GrafanaClient(..., transport=fake)`` and assert request shape plus
response decoding.
"""

from __future__ import annotations

import base64
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from xtrade.core.grafana import (
    DashboardSummary,
    DashboardWithMeta,
    GrafanaAPIError,
    GrafanaAuthError,
    GrafanaClient,
)

# ---------------------------------------------------------------------------
# Test fixtures: canned envelopes + a tiny namespace switching helper.
# ---------------------------------------------------------------------------

NAMESPACE = "default"
DASHBOARDS_BASE = f"/apis/dashboard.grafana.app/v1/namespaces/{NAMESPACE}/dashboards"


def _envelope(
    name: str, *, title: str = "T", panels: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Build a unified-API dashboard envelope.

    ``name`` is the URL slug Grafana uses in REST paths
    (``metadata.name``); ``uid`` is Grafana's internal stable id. In
    real Grafana responses they are usually different; in tests we let
    them match for simplicity unless a test specifically needs to
    distinguish them.
    """
    return {
        "kind": "Dashboard",
        "apiVersion": "dashboard.grafana.app/v1",
        "metadata": {"name": name, "namespace": NAMESPACE, "uid": f"uid-{name}"},
        "spec": {"title": title, "panels": panels if panels is not None else []},
    }


def _response(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, json=body)


# ---------------------------------------------------------------------------
# Fake transport
# ---------------------------------------------------------------------------


class FakeTransport(httpx.BaseTransport):
    """Routes requests by ``(method, path-prefix)`` and dispatches handlers.

    A handler is a callable ``(request) -> httpx.Response``. When more
    than one handler is registered under the same prefix, each call
    consumes the next one (so multi-page list tests can queue canned
    responses without manual routing).
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.fail: bool = False
        # Maps (METHOD, path-prefix) -> list[handler]; first registered is consumed first.
        self._routes: dict[tuple[str, str], list[Callable[[httpx.Request], httpx.Response]]] = {}

    def route(
        self,
        method: str,
        path_prefix: str,
        handler: Callable[[httpx.Request], httpx.Response],
    ) -> None:
        self._routes.setdefault((method.upper(), path_prefix), []).append(handler)

    def route_json(self, method: str, path_prefix: str, status: int, body: Any) -> None:
        """Convenience: register a handler that always returns ``status`` + JSON ``body``."""

        def _h(_req: httpx.Request) -> httpx.Response:
            return _response(status, body)

        self.route(method, path_prefix, _h)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("network down")
        key = (request.method, request.url.path)
        # Exact match first.
        handlers = self._routes.get(key)
        if handlers:
            return handlers.pop(0)(request)
        # Prefix match (longest prefix wins).
        candidates = [
            (prefix, h)
            for (method, prefix), hs in self._routes.items()
            if method == request.method and request.url.path.startswith(prefix)
            for h in [hs[0]]
        ]
        candidates.sort(key=lambda x: -len(x[0]))
        if candidates:
            prefix, handler = candidates[0]
            queue = self._routes[(request.method, prefix)]
            queue.pop(0)
            return handler(request)
        return _response(404, {"message": f"no route for {request.method} {request.url.path}"})


def _make_client(
    transport: FakeTransport,
    *,
    api_key: str = "glsa_test",
    user: str = "",
    password: str = "",
    path_prefix: str = "",
    namespace: str = NAMESPACE,
) -> GrafanaClient:
    return GrafanaClient(
        host="grafana.local",
        port=3000,
        api_key=api_key,
        user=user,
        password=password,
        path_prefix=path_prefix,
        namespace=namespace,
        transport=transport,
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_bearer_header_when_api_key_set() -> None:
    transport = FakeTransport()
    transport.route_json("GET", DASHBOARDS_BASE, 200, {"items": [], "metadata": {}})

    client = _make_client(transport, api_key="glsa_xyz")

    client.dashboards.list()

    assert transport.requests[0].headers["Authorization"] == "Bearer glsa_xyz"


def test_basic_header_when_only_credentials_set() -> None:
    transport = FakeTransport()
    transport.route_json("GET", DASHBOARDS_BASE, 200, {"items": [], "metadata": {}})

    client = _make_client(transport, api_key="", user="alice", password="s3cret")

    client.dashboards.list()

    expected = base64.b64encode(b"alice:s3cret").decode("ascii")
    assert transport.requests[0].headers["Authorization"] == f"Basic {expected}"


def test_auth_error_when_no_credentials() -> None:
    transport = FakeTransport()
    with pytest.raises(GrafanaAuthError):
        GrafanaClient(host="x", api_key="", user="", password="", transport=transport)


def test_path_prefix_is_honoured() -> None:
    transport = FakeTransport()
    prefixed = f"/grafana{DASHBOARDS_BASE}"
    transport.route_json("GET", prefixed, 200, {"items": [], "metadata": {}})

    client = _make_client(transport, path_prefix="/grafana")

    client.dashboards.list()

    assert transport.requests[0].url.path == prefixed


# ---------------------------------------------------------------------------
# DashboardsAPI.list
# ---------------------------------------------------------------------------


def test_list_single_short_page() -> None:
    transport = FakeTransport()
    transport.route_json(
        "GET",
        DASHBOARDS_BASE,
        200,
        {
            "items": [
                _envelope("a", title="A"),
                _envelope("b", title="B"),
            ],
            "metadata": {},
        },
    )

    client = _make_client(transport)
    out = client.dashboards.list()

    assert out == [
        DashboardSummary(uid="uid-a", name="a", namespace=NAMESPACE, title="A"),
        DashboardSummary(uid="uid-b", name="b", namespace=NAMESPACE, title="B"),
    ]
    list_calls = [r for r in transport.requests if r.url.path == DASHBOARDS_BASE]
    assert len(list_calls) == 1
    assert list_calls[0].url.params.get("limit") == "200"


def test_list_paginates_with_continue_token() -> None:
    transport = FakeTransport()
    transport.route_json(
        "GET",
        DASHBOARDS_BASE,
        200,
        {
            "items": [_envelope(f"u{i}") for i in range(200)],
            "metadata": {"continue": "abc"},
        },
    )
    transport.route_json(
        "GET",
        DASHBOARDS_BASE,
        200,
        {
            "items": [_envelope("last", title="L")],
            "metadata": {},
        },
    )

    client = _make_client(transport)
    out = client.dashboards.list()

    assert len(out) == 201
    assert out[0].uid == "uid-u0"
    assert out[-1].uid == "uid-last"

    list_calls = [r for r in transport.requests if r.url.path == DASHBOARDS_BASE]
    assert len(list_calls) == 2
    assert "continue" not in list_calls[0].url.params
    assert list_calls[1].url.params.get("continue") == "abc"


# ---------------------------------------------------------------------------
# DashboardsAPI.get
# ---------------------------------------------------------------------------


def test_get_returns_dashboard_and_meta() -> None:
    transport = FakeTransport()
    body = _envelope("abc", title="ABC", panels=[])
    transport.route_json("GET", f"{DASHBOARDS_BASE}/abc", 200, body)

    client = _make_client(transport)
    result = client.dashboards.get("abc")

    assert isinstance(result, DashboardWithMeta)
    assert result.uid == "uid-abc"  # Grafana-side stable id (metadata.uid)
    assert result.name == "abc"  # URL slug used in REST path
    assert result.namespace == NAMESPACE
    assert result.spec["title"] == "ABC"


# ---------------------------------------------------------------------------
# DashboardsAPI.create / update / delete
# ---------------------------------------------------------------------------


def test_create_rejects_payload_without_metadata_name() -> None:
    transport = FakeTransport()
    client = _make_client(transport)
    with pytest.raises(ValueError, match=r"metadata\.name"):
        client.dashboards.create({"metadata": {}, "spec": {}})


def test_create_rejects_payload_without_spec() -> None:
    transport = FakeTransport()
    client = _make_client(transport)
    with pytest.raises(ValueError, match="spec"):
        client.dashboards.create({"metadata": {"name": "x"}})


def test_create_posts_envelope_and_returns_dashboard() -> None:
    transport = FakeTransport()
    transport.route_json("POST", DASHBOARDS_BASE, 201, _envelope("new", title="NEW"))

    client = _make_client(transport)
    payload = {
        "metadata": {"name": "new"},
        "spec": {"title": "NEW", "panels": []},
    }
    result = client.dashboards.create(payload, message="hi")

    assert result.uid == "uid-new"
    request_body = json.loads(transport.requests[0].content.decode("utf-8"))
    assert request_body["metadata"]["annotations"]["grafana.app/message"] == "hi"
    assert request_body["metadata"]["name"] == "new"
    assert request_body["spec"]["title"] == "NEW"


def test_create_includes_folder_annotation_when_provided() -> None:
    transport = FakeTransport()
    transport.route_json("POST", DASHBOARDS_BASE, 201, _envelope("n"))

    client = _make_client(transport)
    client.dashboards.create(
        {"metadata": {"name": "n"}, "spec": {"title": "X"}},
        folder_uid="f1",
    )

    request_body = json.loads(transport.requests[0].content.decode("utf-8"))
    assert request_body["metadata"]["annotations"]["grafana.app/folder"] == "f1"


def test_create_preserves_caller_annotations() -> None:
    transport = FakeTransport()
    transport.route_json("POST", DASHBOARDS_BASE, 201, _envelope("n"))

    client = _make_client(transport)
    client.dashboards.create(
        {
            "metadata": {
                "name": "n",
                "annotations": {"team": "data", "grafana.app/message": "stale"},
            },
            "spec": {"title": "X"},
        }
    )

    request_body = json.loads(transport.requests[0].content.decode("utf-8"))
    ann = request_body["metadata"]["annotations"]
    assert ann["team"] == "data"
    assert ann["grafana.app/message"] != "stale"  # SDK overrides stale grafana.app/message


def test_update_sets_metadata_name_and_posts_envelope() -> None:
    transport = FakeTransport()
    transport.route_json(
        "PUT",
        f"{DASHBOARDS_BASE}/abc",
        200,
        _envelope("abc", title="ABC"),
    )

    client = _make_client(transport)
    payload = {
        "metadata": {"name": "stale", "annotations": {"team": "x"}},
        "spec": {"title": "ABC"},
    }
    result = client.dashboards.update("abc", payload, message="upd")

    assert result.uid == "uid-abc"
    sent = json.loads(transport.requests[0].content.decode("utf-8"))
    assert sent["metadata"]["name"] == "abc"  # URL slug, forces by SDK
    assert sent["metadata"]["annotations"]["grafana.app/message"] == "upd"
    assert sent["metadata"]["annotations"]["team"] == "x"
    assert sent["spec"]["title"] == "ABC"


def test_create_accepts_builder() -> None:
    """Create accepts a builder; ``metadata.name`` is read from ``name`` kwarg.

    The current :meth:`DashboardsAPI.create` does NOT derive ``name`` from
    the URL — the caller supplies it through the envelope / builder.
    For Foundation SDK callers, ``name`` flows through the
    ``metadata.name`` field of the envelope built by
    :func:`xtrade.core.grafana.builder.build_envelope`. To match the
    HTTP API where ``name`` is part of the URL, callers using the
    builder must wrap it via :func:`build_envelope` and pass the dict.
    """
    transport = FakeTransport()
    transport.route_json("POST", DASHBOARDS_BASE, 201, _envelope("new", title="NEW"))

    client = _make_client(transport)
    from xtrade.core.grafana.builder import DashboardBuilder, build_envelope

    builder = DashboardBuilder(title="NEW")
    envelope = build_envelope(builder, name="new", message="hi")
    result = client.dashboards.create(envelope, message="hi")

    assert result.uid == "uid-new"
    request_body = json.loads(transport.requests[0].content.decode("utf-8"))
    assert request_body["metadata"]["name"] == "new"
    assert request_body["metadata"]["annotations"]["grafana.app/message"] == "hi"
    assert request_body["spec"]["title"] == "NEW"


def test_create_rejects_unsupported_payload() -> None:
    transport = FakeTransport()
    client = _make_client(transport)
    with pytest.raises(ValueError, match=r"dict or expose .build\(\)"):
        client.dashboards.create(42)  # type: ignore[arg-type]


def test_update_panel_accepts_builder() -> None:
    transport = FakeTransport()
    _stub_get_and_update(transport, _two_panel_dashboard())

    client = _make_client(transport)
    from xtrade.core.grafana.builder import TimeseriesPanelBuilder

    builder = TimeseriesPanelBuilder(title="X")
    client.panels.update_panel("abc", 4, panel_builder=builder)

    sent = json.loads(transport.requests[1].content.decode("utf-8"))
    updated_panel = next(p for p in sent["spec"]["panels"] if p["id"] == 4)
    other_panel = next(p for p in sent["spec"]["panels"] if p["id"] == 1)
    assert updated_panel["title"] == "X"
    # The builder only sets title + gridPos + scaffolded options /
    # fieldConfig; the original ``targets`` is untouched because the
    # builder doesn't have one.
    assert updated_panel["targets"] == [{"refId": "A", "expr": "up"}]
    assert other_panel["title"] == "A"


def test_update_panel_explicit_kwarg_wins_over_builder() -> None:
    transport = FakeTransport()
    _stub_get_and_update(transport, _two_panel_dashboard())

    client = _make_client(transport)
    from xtrade.core.grafana.builder import TimeseriesPanelBuilder

    builder = TimeseriesPanelBuilder(title="from-builder")
    client.panels.update_panel("abc", 4, title="override", panel_builder=builder)

    sent = json.loads(transport.requests[1].content.decode("utf-8"))
    updated_panel = next(p for p in sent["spec"]["panels"] if p["id"] == 4)
    assert updated_panel["title"] == "override"


def test_update_panel_rejects_non_builder_panel_builder() -> None:
    transport = FakeTransport()
    _stub_get_and_update(transport, _two_panel_dashboard())

    client = _make_client(transport)
    with pytest.raises(ValueError, match="panel_builder must expose"):
        client.panels.update_panel("abc", 4, panel_builder=42)  # type: ignore[arg-type]


def test_delete_returns_none() -> None:
    transport = FakeTransport()
    transport.route_json("DELETE", f"{DASHBOARDS_BASE}/abc", 204, None)

    client = _make_client(transport)
    assert client.dashboards.delete("abc") is None


# ---------------------------------------------------------------------------
# PanelsAPI.update_panel
# ---------------------------------------------------------------------------


def _stub_get_and_update(transport: FakeTransport, dashboard: dict[str, Any]) -> None:
    transport.route_json("GET", f"{DASHBOARDS_BASE}/abc", 200, dashboard)
    transport.route_json("PUT", f"{DASHBOARDS_BASE}/abc", 200, dashboard)


def _two_panel_dashboard() -> dict[str, Any]:
    return _envelope(
        "abc",
        title="Demo",
        panels=[
            {"id": 1, "type": "stat", "title": "A", "gridPos": {"x": 0}},
            {
                "id": 4,
                "type": "timeseries",
                "title": "PnL",
                "gridPos": {"x": 6},
                "targets": [{"refId": "A", "expr": "up"}],
                "options": {"legend": {"showLegend": True}},
                "fieldConfig": {"defaults": {"unit": "percent"}},
            },
        ],
    )


def test_update_panel_title_only_leaves_other_fields_alone() -> None:
    transport = FakeTransport()
    _stub_get_and_update(transport, _two_panel_dashboard())

    client = _make_client(transport)
    client.panels.update_panel("abc", 4, title="PnL (renamed)")

    sent = json.loads(transport.requests[1].content.decode("utf-8"))
    updated_panel = next(p for p in sent["spec"]["panels"] if p["id"] == 4)
    other_panel = next(p for p in sent["spec"]["panels"] if p["id"] == 1)

    assert updated_panel["title"] == "PnL (renamed)"
    assert updated_panel["type"] == "timeseries"
    assert updated_panel["gridPos"] == {"x": 6}
    assert updated_panel["targets"] == [{"refId": "A", "expr": "up"}]
    assert updated_panel["options"] == {"legend": {"showLegend": True}}
    assert updated_panel["fieldConfig"] == {"defaults": {"unit": "percent"}}
    assert other_panel["title"] == "A"


def test_update_panel_targets_replaces_list() -> None:
    transport = FakeTransport()
    _stub_get_and_update(transport, _two_panel_dashboard())

    client = _make_client(transport)
    new_targets = [{"refId": "A", "expr": "vector(1)"}]
    client.panels.update_panel("abc", 4, targets=new_targets)

    sent = json.loads(transport.requests[1].content.decode("utf-8"))
    updated_panel = next(p for p in sent["spec"]["panels"] if p["id"] == 4)
    assert updated_panel["targets"] == new_targets


def test_update_panel_unknown_id_raises_and_does_not_post() -> None:
    transport = FakeTransport()
    _stub_get_and_update(transport, _two_panel_dashboard())

    client = _make_client(transport)
    with pytest.raises(ValueError, match="panel id 99 not found"):
        client.panels.update_panel("abc", 99, title="X")

    write_calls = [r for r in transport.requests if r.method in {"POST", "PUT", "DELETE", "PATCH"}]
    assert write_calls == []


# ---------------------------------------------------------------------------
# with_namespace
# ---------------------------------------------------------------------------


def test_with_namespace_overrides_request_path() -> None:
    transport = FakeTransport()
    ns_b_root = "/apis/dashboard.grafana.app/v1/namespaces/team-b/dashboards"
    transport.route_json("GET", ns_b_root, 200, {"items": [], "metadata": {}})

    client = _make_client(transport)
    scoped = client.with_namespace("team-b")

    out = scoped.dashboards.list()

    assert out == []
    assert transport.requests[0].url.path == ns_b_root


def test_with_namespace_does_not_mutate_original() -> None:
    transport = FakeTransport()
    transport.route_json("GET", DASHBOARDS_BASE, 200, {"items": [], "metadata": {}})
    ns_b_root = "/apis/dashboard.grafana.app/v1/namespaces/team-b/dashboards"
    transport.route_json("GET", ns_b_root, 200, {"items": [], "metadata": {}})

    client = _make_client(transport)
    _ = client.with_namespace("team-b").dashboards.list()
    client.dashboards.list()

    paths = [r.url.path for r in transport.requests]
    assert DASHBOARDS_BASE in paths
    assert ns_b_root in paths


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def test_non_2xx_raises_grafana_api_error_with_parsed_body() -> None:
    transport = FakeTransport()
    transport.route_json("GET", f"{DASHBOARDS_BASE}/missing", 404, {"message": "not found"})

    client = _make_client(transport)
    with pytest.raises(GrafanaAPIError) as exc_info:
        client.dashboards.get("missing")

    err = exc_info.value
    assert err.status_code == 404
    assert err.body == {"message": "not found"}
    assert "missing" in err.url
    assert isinstance(err, GrafanaAPIError)


def test_non_json_body_is_kept_as_string() -> None:
    """On a non-JSON body, the raw text is kept."""

    def _h(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error")

    transport = FakeTransport()
    transport.route("GET", f"{DASHBOARDS_BASE}/x", _h)

    client = _make_client(transport)
    with pytest.raises(GrafanaAPIError) as exc_info:
        client.dashboards.get("x")
    assert exc_info.value.status_code == 500
    assert exc_info.value.body == "internal server error"


def test_network_error_propagates_as_httpx_error() -> None:
    """httpx-level failures are NOT wrapped."""

    class BoomTransport(httpx.BaseTransport):
        def __init__(self) -> None:
            self.requests: list[httpx.Request] = []

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            raise httpx.ConnectError("boom", request=request)

    client = GrafanaClient(host="x", api_key="k", transport=BoomTransport())

    with pytest.raises(httpx.ConnectError):
        client.dashboards.list()


# ---------------------------------------------------------------------------
# Layering
# ---------------------------------------------------------------------------


def test_package_does_not_import_business_modules_or_legacy_paths() -> None:
    """``xtrade.core.grafana`` MUST NOT import business layers or ``mos.*``."""
    grafana_pkg = Path(__file__).resolve().parents[2] / "src" / "xtrade" / "core" / "grafana"
    forbidden_business = (
        "xtrade.strategy",
        "xtrade.execution",
        "xtrade.engine",
        "xtrade.data",
        "xtrade.risk",
    )
    forbidden_mos = [k for k in sys.modules if k.startswith("mos.")]

    pkg_modules = [m for m in sys.modules if m.startswith("xtrade.core.grafana")]
    for mod_name in pkg_modules:
        mod = sys.modules[mod_name]
        source_file = getattr(mod, "__file__", "") or ""
        assert source_file.startswith(str(grafana_pkg)), (
            f"module {mod_name} resolves outside the grafana package: {source_file}"
        )

    for path in grafana_pkg.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden_business:
            assert needle not in text, f"{path.name} imports {needle}"
        assert "mos." not in text, f"{path.name} imports mos.*"
        # No legacy /api/dashboards references in the package source.
        assert "/api/dashboards" not in text, (
            f"{path.name} still references the legacy /api/dashboards endpoint"
        )

    cfg_text = (grafana_pkg / "_client.py").read_text(encoding="utf-8")
    assert "from xtrade.core.config import" in cfg_text

    assert forbidden_mos == []
