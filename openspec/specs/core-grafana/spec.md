# core-grafana Specification

## Purpose
Provides a small Python SDK in `xtrade.core.grafana` that lets project code talk to a Grafana 12+ unified HTTP API for dashboards and panels, using the connection settings already present in `Config.grafana`. The SDK supports bearer-token (Service Account) and basic-auth credentials and is exercised entirely through unit tests with a fake transport. Dashboard payloads go through the unified API envelope (`metadata.name` + `spec`) and may be supplied as raw dicts or as typed builders on top of `grafana-foundation-sdk` (see `core-grafana-builder`).

## Requirements

### Requirement: `GrafanaClient` is the single entry point

The `xtrade.core.grafana` package SHALL expose a `GrafanaClient` class that wraps an `httpx.Client` and exposes two attributes: `dashboards: DashboardsAPI` and `panels: PanelsAPI`. Construction SHALL accept either a `GrafanaConfig` instance or the same keyword arguments used to build one. The client SHALL honour `host`, `port`, `scheme`, `path_prefix`, `org_slug`, `timeout`, `verify_ssl`, and `namespace` from the config and SHALL select the auth scheme from `api_key` / `user` + `password`. When both are empty the client SHALL raise `GrafanaAuthError`.

#### Scenario: Constructed from `GrafanaConfig`

- **WHEN** a developer calls `GrafanaClient(get_config().grafana)` with `api_key="k"`
- **THEN** the returned object has `client.dashboards` and `client.panels` attributes, the underlying base URL is `cfg.base_url`, and every request carries `Authorization: Bearer k`

#### Scenario: Basic auth is used when no API key is set

- **WHEN** a `GrafanaConfig` has `api_key=""`, `user="alice"`, `password="s3cret"`
- **THEN** requests issued by the client carry the HTTP basic-auth header `Basic <base64(alice:s3cret)>` and no `Authorization: Bearer` header

#### Scenario: Path prefix is honoured

- **WHEN** `path_prefix="/grafana"` is set
- **THEN** dashboard endpoints are issued under `<base_url>/grafana/apis/dashboard.grafana.app/v1/...` and a missing prefix leaves them at `<base_url>/apis/dashboard.grafana.app/v1/...`

#### Scenario: Namespace is honoured on every dashboard path

- **WHEN** `namespace="default"` is set
- **THEN** dashboard endpoints are issued under `.../namespaces/default/dashboards[...]`

### Requirement: `DashboardsAPI` exposes CRUD over dashboards

The `dashboards` attribute SHALL expose:

- `list() -> list[DashboardSummary]` — calls `GET /apis/dashboard.grafana.app/v1/namespaces/{ns}/dashboards?limit=N`, paginating with the `continue` token returned by Grafana in `metadata.continue` until Grafana omits it (or returns a short page). Returns one `DashboardSummary` per dashboard with `uid`, `name`, `namespace`, and `title`.
- `get(name: str) -> DashboardWithMeta` — calls `GET /apis/dashboard.grafana.app/v1/namespaces/{ns}/dashboards/{name}` and returns a `DashboardWithMeta` whose `.spec` is the dashboard JSON and whose `.meta` is the top-level metadata block.
- `create(payload: dict | object, *, message: str = "Created via xtrade", folder_uid: str | None = None) -> DashboardWithMeta` —
  - When `payload` is a dict, the dict MUST be the unified-API envelope (`metadata.name` + `spec`); the SDK wraps it with the SDK's annotations and POSTs it.
  - When `payload` exposes a `.build()` method (e.g. a Foundation SDK builder), the SDK calls `payload.build()` exactly once to obtain the dashboard JSON and wraps it in the envelope itself.
  - Raises `ValueError` when `payload` is neither a dict nor exposes `.build()`, or when the resulting envelope lacks `metadata.name` or `spec`.
- `update(name: str, payload: dict, *, message: str = "Updated via xtrade", folder_uid: str | None = None) -> DashboardWithMeta` — calls `PUT /apis/dashboard.grafana.app/v1/namespaces/{ns}/dashboards/{name}` with the same envelope as `create`, forcing `payload["metadata"]["name"] = name`. Raises `ValueError` when `spec` is missing.
- `delete(name: str) -> None` — calls `DELETE /apis/dashboard.grafana.app/v1/namespaces/{ns}/dashboards/{name}` and returns `None` on the `204 No Content` response.

A non-2xx response SHALL raise `GrafanaAPIError` carrying the HTTP status, the response body (parsed JSON when possible, raw string otherwise), and the request URL. The SDK SHALL NOT swallow the error.

#### Scenario: List returns one entry per dashboard

- **WHEN** Grafana returns two dashboards and `metadata.continue` is unset
- **THEN** `dashboards.list()` returns a list of two `DashboardSummary` objects with `uid`, `name`, `namespace`, and `title` populated

#### Scenario: List paginates with the `continue` token

- **WHEN** Grafana returns a page of N dashboards with `metadata.continue = "abc"` and the next page of M dashboards with `metadata.continue` unset
- **THEN** `dashboards.list()` issues exactly two GETs (the second with `?continue=abc`) and returns N+M summaries

#### Scenario: Get by name returns the dashboard envelope

- **WHEN** `dashboards.get("abc")` is called
- **THEN** the returned `DashboardWithMeta` has `.uid == "uid-abc"`, `.name == "abc"`, `.spec` containing the dashboard JSON (with `title`, `panels`, etc.), and `.meta` containing `name`, `namespace`, `creationTimestamp`, etc.

#### Scenario: Create rejects payloads without `metadata.name` or `spec`

- **WHEN** a developer calls `dashboards.create({"spec": {...}})` (missing `metadata.name`)
- **THEN** the SDK raises `ValueError("payload must contain metadata.name")` before issuing any HTTP request

#### Scenario: Create accepts a Foundation SDK builder

- **WHEN** a developer passes `DashboardBuilder(title="X")` after wrapping it via `build_envelope(builder, name="x", message="hi")`
- **THEN** the resulting POST body has `metadata.name == "x"`, `metadata.annotations["grafana.app/message"] == "hi"`, and `spec.title == "X"`

#### Scenario: Create rejects non-builder, non-dict payloads

- **WHEN** `dashboards.create(42)` is called
- **THEN** the SDK raises `ValueError("payload must be a dict or expose .build()")` before issuing any HTTP request

#### Scenario: Create posts the unified envelope

- **WHEN** `dashboards.create(payload, message="hi", folder_uid="f1")` is called with a dict payload
- **THEN** the request body has `metadata.annotations["grafana.app/message"] == "hi"` and `metadata.annotations["grafana.app/folder"] == "f1"`, and `spec == payload["spec"]`

#### Scenario: Update sets the name and posts the envelope

- **WHEN** `dashboards.update("abc", payload, message="upd")` is called
- **THEN** the request body has `metadata.name == "abc"`, `metadata.annotations["grafana.app/message"] == "upd"`, and `spec == payload["spec"]`

#### Scenario: Delete returns None on 204

- **WHEN** Grafana responds `204 No Content`
- **THEN** `dashboards.delete("abc")` returns `None`

#### Scenario: Non-2xx raises `GrafanaAPIError`

- **WHEN** Grafana responds with `404 {"message": "not found"}`
- **THEN** the SDK raises `GrafanaAPIError` with `status_code == 404`, `body == {"message": "not found"}`, and `url` pointing at the failing endpoint

### Requirement: `PanelsAPI` updates a single panel

The `panels` attribute SHALL expose:

- `update_panel(dashboard_name: str, panel_id: int, *, title: str | None = None, description: str | None = None, targets: list[dict] | None = None, options: dict | None = None, field_config: dict | None = None, transparent: bool | None = None, panel_builder: object | None = None, message: str = "Panel updated via xtrade") -> DashboardWithMeta` —
  - Fetches the dashboard envelope via `dashboards.get(dashboard_name)`.
  - Locates the panel whose `id == panel_id` inside `envelope.spec["panels"]`.
  - When `panel_builder` is supplied (any object exposing `.build() -> dict`), the SDK calls `.build()` and overlays the returned dict's top-level keys into the existing panel object. Explicit kwargs (`title=`, `targets=`, ...) overwrite any field the builder set, so the kwarg path keeps precedence.
  - Otherwise, applies the supplied field overrides (`title`, `description`, `targets`, `options`, `field_config`, `transparent`).
  - Posts the mutated envelope via `dashboards.update(dashboard_name, envelope, message=message)`.
  - Returns the updated `DashboardWithMeta`.

The method SHALL raise `ValueError` when the dashboard contains no panel with the given `id`, when `panel_builder` is neither `None` nor a builder object, or when `panel_builder.build()` raises. Any unset explicit argument SHALL leave the corresponding panel attribute untouched.

#### Scenario: Updating the title leaves other fields alone

- **WHEN** `panels.update_panel("abc", 4, title="PnL")` is called
- **THEN** the persisted dashboard's panel with `id=4` (inside `spec.panels`) has `title="PnL"` and its original `type`, `gridPos`, `targets`, `options`, and `fieldConfig` unchanged

#### Scenario: `panel_builder` overlays its fields

- **WHEN** `panels.update_panel("abc", 4, panel_builder=TimeseriesPanelBuilder(title="PnL"))` is called
- **THEN** the persisted panel's `title` becomes `"PnL"`; any field not set by the builder keeps its previous value

#### Scenario: Explicit kwarg wins over `panel_builder`

- **WHEN** `panels.update_panel("abc", 4, title="override", panel_builder=TimeseriesPanelBuilder(title="from-builder"))` is called
- **THEN** the persisted panel's `title` is `"override"` (the explicit kwarg wins)

#### Scenario: Unknown panel id raises `ValueError`

- **WHEN** the dashboard has no panel with `id=99`
- **THEN** the SDK raises `ValueError("panel id 99 not found in dashboard abc")` and does not issue any `PUT .../dashboards/{name}` request

#### Scenario: Updating targets replaces the whole list

- **WHEN** `panels.update_panel("abc", 4, targets=[{...new...}])` is called
- **THEN** the persisted panel's `targets` is exactly the supplied list (replaced, not merged)

### Requirement: `GrafanaClient.with_namespace` rebinds the API namespace

The client SHALL expose a `with_namespace(namespace: str) -> GrafanaClient` method that returns a sibling client pointed at the supplied namespace. The original client SHALL stay bound to its config-supplied namespace.

#### Scenario: Sibling client uses the new namespace

- **WHEN** `client.with_namespace("team-b").dashboards.list()` is called
- **THEN** the request path includes `namespaces/team-b` instead of the config's namespace

#### Scenario: Original client is not mutated

- **WHEN** a developer calls `client.with_namespace("team-b").dashboards.list()` and then `client.dashboards.list()`
- **THEN** the second call uses the config's namespace; the original client is unchanged

### Requirement: `GrafanaConfig.namespace` selects the API namespace

The configuration model SHALL expose a `namespace: str = "default"` field on `GrafanaConfig`, overridable via the `XTRADE_GRAFANA__NAMESPACE` environment variable. The SDK SHALL use it to build every dashboard path as `/apis/dashboard.grafana.app/v1/namespaces/{namespace}/dashboards[...]`.

#### Scenario: Default namespace is `default`

- **WHEN** a developer loads `Config` and no `config.json` is present
- **THEN** `cfg.grafana.namespace == "default"`

#### Scenario: Env var overrides the namespace

- **WHEN** `XTRADE_GRAFANA__NAMESPACE=team-a` is set
- **THEN** `cfg.grafana.namespace == "team-a"` and the SDK issues dashboard requests under `.../namespaces/team-a/dashboards[...]`

### Requirement: Errors are typed and propagated

The `xtrade.core.grafana` package SHALL expose:

- `GrafanaError` — base class for every exception raised by the SDK.
- `GrafanaAPIError(GrafanaError)` — raised on any non-2xx HTTP response; attributes `status_code: int`, `url: str`, `body: object`.
- `GrafanaAuthError(GrafanaError)` — raised when both `api_key` and `user`+`password` are empty.

Network-level failures raised by `httpx` (timeout, connection error) SHALL propagate as `httpx.HTTPError` and SHALL NOT be wrapped — the caller decides how to retry.

#### Scenario: Empty credentials raise `GrafanaAuthError`

- **WHEN** `GrafanaClient(GrafanaConfig(api_key="", user="", password=""))` is constructed
- **THEN** construction raises `GrafanaAuthError`

#### Scenario: Network failures surface as `httpx.HTTPError`

- **WHEN** the underlying `httpx.Client` raises `httpx.ConnectError` because the host is unreachable
- **THEN** the SDK does NOT wrap it; the original `httpx.ConnectError` reaches the caller

### Requirement: `core-grafana` is layered below business code

The `xtrade.core.grafana` package SHALL NOT import from `xtrade.strategy`, `xtrade.execution`, `xtrade.engine`, `xtrade.data`, `xtrade.risk`, or any `mos.*` module. It MAY import from `xtrade.core` (for `Config` / `GrafanaConfig`), third-party packages (`httpx`, `grafana-foundation-sdk` via the optional builder module), and standard-library modules. The SDK SHALL be usable without any business-layer dependency by importing `GrafanaClient` together with a hand-built `GrafanaConfig`.

#### Scenario: SDK works with only `xtrade.core.grafana` + pydantic

- **WHEN** a developer writes `from xtrade.core.grafana import GrafanaClient` and constructs a `GrafanaConfig` directly
- **THEN** the SDK initialises without importing any strategy / execution / engine / data layer

### Requirement: Tests run without a real Grafana

The `tests/core/test_grafana_client.py` module SHALL cover:

- Auth selection (bearer vs basic) by inspecting the `Authorization` header on a `FakeTransport`.
- Each dashboard CRUD method (list / get / create / update / delete) with canned responses.
- Both dict and Foundation-SDK-builder payload shapes for `create`.
- Both kwarg-only and `panel_builder` shapes for `update_panel`.
- `with_namespace` overrides the request path; the original client is unchanged.
- Panel update merging semantics — `title` change only, `targets` replacement, missing panel id, kwarg-wins-over-builder.
- Error mapping — 404 → `GrafanaAPIError(status_code=404)`, empty creds → `GrafanaAuthError`.

Tests SHALL inject a `FakeTransport` (a stub callable matching `httpx.MockTransport`'s contract) into the underlying `httpx.Client` so no network IO occurs and the suite runs in any environment.

#### Scenario: All tests pass with no network

- **WHEN** `uv run pytest tests/core/test_grafana_client.py -q` is run on a machine without Grafana
- **THEN** the suite passes and no socket connect attempt is made