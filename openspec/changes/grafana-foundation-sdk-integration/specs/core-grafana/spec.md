## MODIFIED Requirements

### Requirement: `DashboardsAPI` exposes CRUD over dashboards

The `dashboards` attribute SHALL expose:

- `list() -> list[DashboardSummary]` — calls `GET /apis/dashboard.grafana.app/v1/namespaces/{ns}/dashboards?limit=N`, paginating with the `continue` token returned by Grafana in `metadata.continue` until Grafana omits it (or returns a short page). Returns one `DashboardSummary` per dashboard with `uid`, `name`, `namespace`, and `title`.
- `get(uid: str) -> DashboardWithMeta` — calls `GET /apis/dashboard.grafana.app/v1/namespaces/{ns}/dashboards/{uid}` and returns a `DashboardWithMeta` whose `.spec` is the dashboard JSON and whose `.meta` is the top-level metadata block.
- `create(payload: object, *, message: str = "Created via xtrade", folder_uid: str | None = None) -> DashboardWithMeta` —
  - When `payload` is a `dict`, the dict MUST be the unified-API envelope (`metadata.name` + `spec`). Behaviour as previously documented.
  - When `payload` is **not** a dict, the SDK MUST call `payload.build() -> dict` exactly once to obtain the dashboard JSON, then build the envelope internally using the same rules as the dict path (forcing `metadata.name = payload["metadata"]["name"]` when the caller already provided a partial envelope, or supplying `metadata.name` from the `name` kwarg when the caller passed a bare dashboard).
  - Raises `ValueError` when `payload` is neither a dict nor duck-types as a builder.
- `update(name: str, payload: dict, *, message: str = "Updated via xtrade", folder_uid: str | None = None) -> DashboardWithMeta` — unchanged; still takes a unified-API envelope dict.
- `delete(name: str) -> None` — unchanged.

A non-2xx response SHALL raise `GrafanaAPIError` carrying the HTTP status, the response body (parsed JSON when possible, raw string otherwise), and the request URL.

#### Scenario: Create accepts a Foundation SDK builder

- **WHEN** `dashboards.create(DashboardBuilder(title="X"), name="x", message="hi")` is called
- **THEN** the SDK calls `payload.build()` exactly once, wraps the result with `metadata.name == "x"` and `metadata.annotations["grafana.app/message"] == "hi"`, posts the envelope, and returns the new dashboard

#### Scenario: Create rejects non-builder, non-dict payloads

- **WHEN** `dashboards.create(42, name="x")` is called
- **THEN** the SDK raises `ValueError` before issuing any HTTP request

#### Scenario: Dict payload still works (regression)

- **WHEN** `dashboards.create({"metadata": {"name": "x"}, "spec": {...}}, message="hi")` is called
- **THEN** the SDK posts the envelope unchanged and returns the new dashboard (no behaviour change)

### Requirement: `PanelsAPI` updates a single panel

The `panels` attribute SHALL expose:

- `update_panel(dashboard_name: str, panel_id: int, *, title: str | None = None, description: str | None = None, targets: list[dict] | None = None, options: dict | None = None, field_config: dict | None = None, transparent: bool | None = None, panel_builder: object | None = None, message: str = "Panel updated via xtrade") -> DashboardWithMeta` —
  - Fetches the dashboard envelope via `dashboards.get(dashboard_name)`.
  - Locates the panel whose `id == panel_id` inside `envelope.spec["panels"]`.
  - When `panel_builder` is supplied, the SDK MUST call `panel_builder.build() -> dict` to obtain a panel dict, merge the dict's top-level keys into the existing panel object (existing keys win on conflict — i.e. explicit kwargs override the builder, builder wins on fields the caller did not set), then post.
  - Otherwise, applies the supplied field overrides (`title`, `description`, `targets`, `options`, `field_config`, `transparent`) as before.
  - Posts the mutated envelope via `dashboards.update(dashboard_name, envelope, message=message)`.
  - Returns the updated `DashboardWithMeta`.

The method SHALL raise `ValueError` when the dashboard contains no panel with the given `id`, when `panel_builder` is neither `None` nor a builder object, or when `panel_builder.build()` raises.

#### Scenario: Updating the title leaves other fields alone

- **WHEN** `panels.update_panel("abc", 4, title="PnL")` is called
- **THEN** the persisted dashboard's panel with `id=4` (inside `spec.panels`) has `title="PnL"` and its original `type`, `gridPos`, `targets`, `options`, and `fieldConfig` unchanged

#### Scenario: `panel_builder` overlays its fields

- **WHEN** `panels.update_panel("abc", 4, panel_builder=TimeseriesPanelBuilder(title="PnL"))` is called and the builder's `.build()` returns `{ "type": "timeseries", "title": "PnL", "gridPos": {...}, "targets": [...] }`
- **THEN** the persisted panel keeps its original `gridPos`, `options`, and `fieldConfig`, but `title` becomes `"PnL"` and `targets` is the builder's list

#### Scenario: Explicit kwarg wins over `panel_builder`

- **WHEN** `panels.update_panel("abc", 4, title="override", panel_builder=TimeseriesPanelBuilder(title="from-builder"))` is called
- **THEN** the persisted panel's `title` is `"override"` (the explicit kwarg wins)

#### Scenario: Unknown panel id raises `ValueError`

- **WHEN** the dashboard has no panel with `id=99`
- **THEN** the SDK raises `ValueError("panel id 99 not found in dashboard abc")` and does not issue any `PUT .../dashboards/{name}` request