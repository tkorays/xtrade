## Purpose

Provides a small Python SDK in `xtrade.core.grafana` that lets project code talk to a Grafana HTTP API for dashboards and panels, using the connection settings already present in `Config.grafana`. The SDK supports both bearer-token (Service Account) and basic-auth credentials and is exercised entirely through unit tests with a fake transport.

## ADDED Requirements

### Requirement: `GrafanaClient` is the single entry point

The `xtrade.core.grafana` package SHALL expose a `GrafanaClient` class that wraps an `httpx.Client` and exposes two attributes: `dashboards: DashboardsAPI` and `panels: PanelsAPI`. Construction SHALL accept either a `GrafanaConfig` instance or the same keyword arguments used to build one. The client SHALL honour `host`, `port`, `scheme`, `path_prefix`, `org_slug`, `timeout`, and `verify_ssl` from the config and SHALL select the auth scheme from `api_key` / `user` + `password`.

#### Scenario: Constructed from `GrafanaConfig`

- **WHEN** a developer calls `GrafanaClient(get_config().grafana)`
- **THEN** the returned object has `client.dashboards` and `client.panels` attributes, the underlying base URL is `cfg.base_url`, and the `Authorization` header is `Bearer <api_key>` when `api_key` is non-empty

#### Scenario: Basic auth is used when no API key is set

- **WHEN** a `GrafanaConfig` has `api_key=""`, `user="alice"`, `password="s3cret"`
- **THEN** requests issued by the client carry the HTTP basic-auth header `Basic <base64(alice:s3cret)>` and no `Authorization: Bearer` header

#### Scenario: Path prefix is honoured

- **WHEN** `path_prefix="/grafana"` is set
- **THEN** dashboard endpoints are issued under `<base_url>/grafana/api/...` and a missing prefix leaves them at `<base_url>/api/...`

### Requirement: `DashboardsAPI` exposes CRUD over dashboards

The `dashboards` attribute SHALL expose:

- `list() -> list[DashboardSummary]` — calls `GET /api/search?type=dash-db` (paginated, the SDK iterates `page` until empty), returning one `DashboardSummary` per row with at least `uid`, `title`, `uri`, and `type`.
- `get(uid: str) -> DashboardWithMeta` — calls `GET /api/dashboards/uid/<uid>` and returns the dashboard payload (the JSON document the user wants to mutate) and its top-level metadata (`meta.url`, `meta.slug`, etc.).
- `create(payload: dict, *, message: str = "Created via xtrade", folder_uid: str | None = None) -> DashboardWithMeta` — calls `POST /api/dashboards` with `{ "dashboard": payload, "message": ..., "folderUid": ..., "overwrite": false }`. Raises `ValueError` when the payload already has a `uid`.
- `update(uid: str, payload: dict, *, message: str = "Updated via xtrade", folder_uid: str | None = None) -> DashboardWithMeta` — calls `POST /api/dashboards` with `overwrite=True` after setting `payload["uid"] = uid`. Returns the post-update document.
- `delete(uid: str) -> None` — calls `DELETE /api/dashboards/uid/<uid>` and returns `None` on success.

A non-2xx response SHALL raise `GrafanaAPIError` carrying the HTTP status, the response body (parsed JSON when possible, raw string otherwise), and the request URL. The SDK SHALL NOT swallow the error.

#### Scenario: List returns one entry per dashboard

- **WHEN** Grafana returns two dashboard search hits
- **THEN** `dashboards.list()` returns a list of two `DashboardSummary` objects with `uid`, `title`, `uri`, and `type` populated

#### Scenario: Get by uid returns the full payload

- **WHEN** `dashboards.get("abc")` is called and Grafana responds with a `dashboard` body
- **THEN** the returned object exposes the document under `.dashboard` and `meta.slug`, `meta.url` under `.meta`

#### Scenario: Create rejects payloads with an existing `uid`

- **WHEN** a developer calls `dashboards.create({"uid": "abc", ...})`
- **THEN** the SDK raises `ValueError` before issuing any HTTP request

#### Scenario: Update sets overwrite=True and the uid

- **WHEN** `dashboards.update("abc", payload)` is called
- **THEN** the request body sent to Grafana has `overwrite=True`, `payload["uid"] == "abc"`, and `payload["id"] is None`

#### Scenario: Delete returns None on success

- **WHEN** Grafana responds `200 {"id": N, "message": "Dashboard abc deleted"}`
- **THEN** `dashboards.delete("abc")` returns `None`

#### Scenario: Non-2xx raises `GrafanaAPIError`

- **WHEN** Grafana responds with `404 {"message": "Dashboard not found"}`
- **THEN** the SDK raises `GrafanaAPIError` with `status_code == 404`, `body == {"message": "Dashboard not found"}`, and `url` pointing at the failing endpoint

### Requirement: `PanelsAPI` updates a single panel

The `panels` attribute SHALL expose:

- `update_panel(dashboard_uid: str, panel_id: int, *, title: str | None = None, description: str | None = None, targets: list[dict] | None = None, options: dict | None = None, field_config: dict | None = None, transparent: bool | None = None, message: str = "Panel updated via xtrade") -> DashboardWithMeta` — fetches the dashboard via `dashboards.get(dashboard_uid)`, locates the panel whose `id == panel_id` inside the dashboard's `panels` list, applies the supplied field overrides, POSTs the dashboard back via `dashboards.update(dashboard_uid, payload, message=message)`, and returns the updated `DashboardWithMeta`.

The method SHALL raise `ValueError` when the dashboard contains no panel with the given `id`. Any unset argument SHALL leave the corresponding panel attribute untouched.

#### Scenario: Updating the title leaves other fields alone

- **WHEN** `panels.update_panel("abc", 4, title="PnL")` is called
- **THEN** the persisted dashboard's panel with `id=4` has `title="PnL"` and its original `type`, `gridPos`, `targets`, `options`, and `fieldConfig` unchanged

#### Scenario: Unknown panel id raises `ValueError`

- **WHEN** the dashboard has no panel with `id=99`
- **THEN** the SDK raises `ValueError("panel id 99 not found in dashboard abc")` and does not issue any `POST /api/dashboards` request

#### Scenario: Updating targets replaces the whole list

- **WHEN** `panels.update_panel("abc", 4, targets=[{...new...}])` is called
- **THEN** the persisted panel's `targets` is exactly the supplied list (replaced, not merged)

### Requirement: Errors are typed and propagated

The `xtrade.core.grafana` package SHALL expose:

- `GrafanaError` — base class for every exception raised by the SDK.
- `GrafanaAPIError(GrafanaError)` — raised on any non-2xx HTTP response; attributes `status_code: int`, `url: str`, `body: object`.
- `GrafanaAuthError(GrafanaError)` — raised when both `api_key` and `user`+`password` are empty, when the user is empty while a password is set, or when the configured base URL fails to parse.

Network-level failures raised by `httpx` (timeout, connection error) SHALL propagate as `httpx.HTTPError` and SHALL NOT be wrapped — the caller decides how to retry.

#### Scenario: Empty credentials raise `GrafanaAuthError`

- **WHEN** `GrafanaClient(GrafanaConfig(api_key="", user="", password=""))` is constructed
- **THEN** construction raises `GrafanaAuthError`

#### Scenario: Network failures surface as `httpx.HTTPError`

- **WHEN** the underlying `httpx.Client` raises `httpx.ConnectError` because the host is unreachable
- **THEN** the SDK does NOT wrap it; the original `httpx.ConnectError` reaches the caller

### Requirement: `core-grafana` is layered below business code

The `xtrade.core.grafana` package SHALL NOT import from `xtrade.strategy`, `xtrade.execution`, `xtrade.engine`, `xtrade.data`, `xtrade.risk`, or any `mos.*` module. It MAY import from `xtrade.core` (for `Config` / `GrafanaConfig`) and standard-library / third-party packages. The SDK SHALL be usable without any business-layer dependency by importing `GrafanaClient` together with a hand-built `GrafanaConfig`.

#### Scenario: SDK works with only `xtrade.core.grafana` + pydantic

- **WHEN** a developer writes `from xtrade.core.grafana import GrafanaClient` and constructs a `GrafanaConfig` directly
- **THEN** the SDK initialises without importing any strategy / execution / engine / data layer

### Requirement: Tests run without a real Grafana

The `tests/core/test_grafana_client.py` module SHALL cover:

- Auth selection (bearer vs basic) by inspecting the `Authorization` header on a `FakeTransport`.
- Each dashboard CRUD method (list / get / create / update / delete) with canned responses.
- Panel update merging semantics — `title` change only, `targets` replacement, missing panel id.
- Error mapping — 404 → `GrafanaAPIError(status_code=404)`, empty creds → `GrafanaAuthError`.

Tests SHALL inject a `FakeTransport` (a stub callable matching `httpx.MockTransport`'s contract) into the underlying `httpx.Client` so no network IO occurs and the suite runs in any environment.

#### Scenario: All tests pass with no network

- **WHEN** `uv run pytest tests/core/test_grafana_client.py -q` is run on a machine without Grafana
- **THEN** the suite passes and no socket connect attempt is made