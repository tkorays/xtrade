## MODIFIED Requirements

### Requirement: `DashboardsAPI` exposes CRUD over dashboards

The `dashboards` attribute SHALL expose:

- `list() -> list[DashboardSummary]` — calls `GET /apis/dashboard.grafana.app/v1/namespaces/{ns}/dashboards?limit=N`, paginating with the `continue` token returned by Grafana in `metadata.continue` until Grafana omits it (or returns a short page). Returns one `DashboardSummary` per dashboard with `uid`, `name`, `namespace`, and `title`.
- `get(uid: str) -> DashboardWithMeta` — calls `GET /apis/dashboard.grafana.app/v1/namespaces/{ns}/dashboards/{uid}` and returns a `DashboardWithMeta` whose `.spec` is the dashboard JSON and whose `.meta` is the top-level metadata block.
- `create(payload: dict, *, message: str = "Created via xtrade", folder_uid: str | None = None) -> DashboardWithMeta` — calls `POST /apis/dashboard.grafana.app/v1/namespaces/{ns}/dashboards` with body `{ metadata: { name: payload["metadata"]["name"], annotations: { grafana.app/folder: folder_uid (when set), grafana.app/message: message } }, spec: payload["spec"] }`. Raises `ValueError` when the payload does not contain both `metadata.name` (a non-empty string) and `spec` (a dict).
- `update(uid: str, payload: dict, *, message: str = "Updated via xtrade", folder_uid: str | None = None) -> DashboardWithMeta` — calls `POST .../dashboards/{uid}` with the same envelope as `create`, forcing `payload["metadata"]["name"] = uid`. Raises `ValueError` when `spec` is missing.
- `delete(uid: str) -> None` — calls `DELETE .../dashboards/{uid}` and returns `None` on the `204 No Content` response.

A non-2xx response SHALL raise `GrafanaAPIError` carrying the HTTP status, the response body (parsed JSON when possible, raw string otherwise), and the request URL. The SDK SHALL NOT swallow the error.

#### Scenario: List returns one entry per dashboard

- **WHEN** Grafana returns two dashboards and `metadata.continue` is unset
- **THEN** `dashboards.list()` returns a list of two `DashboardSummary` objects with `uid`, `name`, `namespace`, and `title` populated

#### Scenario: List paginates with the `continue` token

- **WHEN** Grafana returns a page of N dashboards with `metadata.continue = "abc"` and the next page of M dashboards with `metadata.continue` unset
- **THEN** `dashboards.list()` issues exactly two GETs (the second with `?continue=abc`) and returns N+M summaries

#### Scenario: Get by uid returns the dashboard envelope

- **WHEN** `dashboards.get("abc")` is called
- **THEN** the returned `DashboardWithMeta` has `.uid == "abc"`, `.spec` containing the dashboard JSON (with `title`, `panels`, etc.), and `.meta` containing `name`, `namespace`, `creationTimestamp`, etc.

#### Scenario: Create rejects payloads without `metadata.name` or `spec`

- **WHEN** a developer calls `dashboards.create({"spec": {...}})` (missing `metadata.name`)
- **THEN** the SDK raises `ValueError("payload must contain metadata.name")` before issuing any HTTP request

#### Scenario: Create posts the unified envelope

- **WHEN** `dashboards.create(payload, message="hi", folder_uid="f1")` is called
- **THEN** the request body has `metadata.annotations["grafana.app/message"] == "hi"` and `metadata.annotations["grafana.app/folder"] == "f1"`, and `spec == payload["spec"]`

#### Scenario: Create returns 201 Created dashboard

- **WHEN** Grafana responds `201` with the unified envelope
- **THEN** `dashboards.create(...)` returns a `DashboardWithMeta` whose `.uid` matches the new dashboard's `metadata.name` / `metadata.uid`

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

- `update_panel(dashboard_uid: str, panel_id: int, *, title: str | None = None, description: str | None = None, targets: list[dict] | None = None, options: dict | None = None, field_config: dict | None = None, transparent: bool | None = None, message: str = "Panel updated via xtrade") -> DashboardWithMeta` — fetches the dashboard via `dashboards.get(dashboard_uid)`, locates the panel whose `id == panel_id` inside `envelope.spec["panels"]`, applies the supplied field overrides, posts the envelope via `dashboards.update(dashboard_uid, envelope, message=message)`, and returns the updated `DashboardWithMeta`.

The method SHALL raise `ValueError` when the dashboard contains no panel with the given `id`. Any unset argument SHALL leave the corresponding panel attribute untouched.

#### Scenario: Updating the title leaves other fields alone

- **WHEN** `panels.update_panel("abc", 4, title="PnL")` is called
- **THEN** the persisted dashboard's panel with `id=4` (inside `spec.panels`) has `title="PnL"` and its original `type`, `gridPos`, `targets`, `options`, and `fieldConfig` unchanged

#### Scenario: Unknown panel id raises `ValueError`

- **WHEN** the dashboard has no panel with `id=99`
- **THEN** the SDK raises `ValueError("panel id 99 not found in dashboard abc")` and does not issue any `POST .../dashboards/{uid}` request

#### Scenario: Updating targets replaces the whole list

- **WHEN** `panels.update_panel("abc", 4, targets=[{...new...}])` is called
- **THEN** the persisted panel's `targets` is exactly the supplied list (replaced, not merged)

## REMOVED Requirements

### Requirement: Legacy `/api/dashboards` endpoint support

**Reason**: Grafana ≥ 12 removed the `/api/dashboards` (and `/api/dashboards/uid/<uid>`, `/api/dashboards/db`) endpoints. The SDK must use the unified `/apis/dashboard.grafana.app/v1/...` routes; legacy support would be dead code on every supported Grafana version.

**Migration**: callers must wrap dashboard JSON in the unified envelope (`{ metadata: { name, annotations: { ... } }, spec: { ... } }`) and use `client.dashboards.create/update(...)` accordingly. `client.panels.update_panel(...)` continues to take the same keyword arguments; only the internal envelope layout changed.

## ADDED Requirements

### Requirement: `GrafanaConfig.namespace` selects the API namespace

The configuration model SHALL expose a `namespace: str = "default"` field on `GrafanaConfig`, overridable via the `XTRADE_GRAFANA__NAMESPACE` environment variable. The SDK SHALL use it to build every dashboard path as `/apis/dashboard.grafana.app/v1/namespaces/{namespace}/dashboards[...]`.

#### Scenario: Default namespace is `default`

- **WHEN** a developer loads `Config` and no `config.json` is present
- **THEN** `cfg.grafana.namespace == "default"`

#### Scenario: Env var overrides the namespace

- **WHEN** `XTRADE_GRAFANA__NAMESPACE=team-a` is set
- **THEN** `cfg.grafana.namespace == "team-a"` and the SDK issues dashboard requests under `.../namespaces/team-a/dashboards[...]`

#### Scenario: Per-call namespace override

- **WHEN** a developer constructs `GrafanaClient(get_config().grafana)` and then calls `client.with_namespace("team-b").dashboards.list()`
- **THEN** the request path includes `namespaces/team-b` instead of the config's namespace; the original client's namespace is unchanged