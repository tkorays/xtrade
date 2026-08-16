## Why

The Grafana SDK shipped under `core-grafana` was designed against Grafana
≤ 11.x's legacy `/api/dashboards[/...]` endpoints. A live smoke test
against a Grafana **13.1.3** instance shows those legacy routes are no
longer mounted: `GET /api/dashboards` returns `404`, while the new
unified-API routes under `/apis/dashboard.grafana.app/v1/...` work and
return correct data. Until the SDK is migrated to those new routes it
cannot talk to any Grafana ≥ 12 install — which is the floor for every
new deployment from now on.

## What Changes

- Rewrite `core-grafana` so every dashboard operation goes through the
  Grafana 12+ unified API:
  - List: `GET /apis/dashboard.grafana.app/v1/namespaces/{ns}/dashboards?limit=N`,
    paginated with the `continue` token returned by Grafana (not the
    legacy `page` parameter).
  - Get: `GET /apis/dashboard.grafana.app/v1/namespaces/{ns}/dashboards/{uid}`.
  - Create: `POST /apis/dashboard.grafana.app/v1/namespaces/{ns}/dashboards`
    with body `{ metadata: { name, annotations: { grafana.app/folder } }, spec: { ...dashboard json... } }`.
    On success Grafana returns the new dashboard at HTTP **201**.
  - Update: `POST .../dashboards/{uid}` with the same shape; commit
    message goes into `metadata.annotations["grafana.app/message"]`.
  - Delete: `DELETE .../dashboards/{uid}` returns **204 No Content**.
- Keep `client.dashboards.list()` / `get()` / `create()` / `update()` /
  `delete()` signatures, but the *returned* types change:
  - `DashboardSummary` adds `namespace` (Grafana 12+ namespaces
    dashboards per-folder) and drops `type` (no longer in the new
    payload).
  - `DashboardWithMeta.dashboard` now wraps the unified payload:
    `{ kind, apiVersion, metadata: { name, namespace, uid, ... }, spec: { ... dashboard json ... } }`.
    Convenience accessors `.spec` (the dashboard body) and `.uid`
    / `.name` / `.namespace` are exposed so callers don't have to reach
    through the nested dict.
  - `create()` / `update()` raise `ValueError` on a payload that does
    not look like the legacy `{ dashboard, message, folderUid,
    overwrite }` shape — call sites that pass legacy payloads get a
    clear error pointing at the new shape.
- Adjust `PanelsAPI.update_panel`: it now mutates `dashboard.spec`
    inside the unified envelope (not `dashboard["panels"]` at the root).
- Add a `namespace` field to `GrafanaConfig` (default `"default"`),
    exposed as `XTRADE_GRAFANA__NAMESPACE`, so multi-namespace
    installations work without reaching into the client.
- Update the spec for `core-grafana` to cover the new endpoints and
    response shapes; keep the auth / error-mapping / no-business-import
    requirements from `add-grafana-client` unchanged.

**BREAKING**: callers passing the legacy `{ dashboard: ..., message,
folderUid, overwrite }` payload (as the previous SDK documented) will
get a `ValueError`. The accepted payload shape is now `{ metadata: {
name, annotations: { grafana.app/folder?, grafana.app/message? } },
spec: { ...dashboard json... } }`. The panel-mutation convenience
works the same way, but reads/writes inside `spec.panels`.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `core-grafana`: rewrite dashboard CRUD + panel update to use the
  Grafana 12+ unified `/apis/dashboard.grafana.app/v1/...` endpoints.
  `GrafanaConfig` gains a `namespace` field. Spec:
  `specs/core-grafana/spec.md` (delta against the existing capability
  archived by `add-grafana-client`).

## Impact

- Affected code:
  - `src/xtrade/core/grafana/_client.py` — namespace path helper
    (`_api_base` that joins `cfg.base_url` + `/apis/dashboard.grafana.app/v1/namespaces/{ns}`).
  - `src/xtrade/core/grafana/dashboards.py` — full rewrite of
    `list` / `get` / `create` / `update` / `delete` against the new
    endpoints; payload and response wrappers (`DashboardEnvelope`).
  - `src/xtrade/core/grafana/panels.py` — `update_panel` now mutates
    `envelope.spec["panels"]` instead of `dashboard["panels"]`.
  - `src/xtrade/core/grafana/types.py` — `DashboardSummary` and
    `DashboardWithMeta` updated; add `DashboardEnvelope` with `.spec`,
    `.uid`, `.name`, `.namespace` accessors.
  - `src/xtrade/core/config.py` — `GrafanaConfig.namespace: str =
    "default"`.
  - `tests/core/test_grafana_client.py` — replace canned-response
    routes and payload assertions with the new shapes; add a test for
    the `continue`-token pagination loop.
- Affected APIs: existing `client.dashboards.list/get/create/update/delete`
  and `client.panels.update_panel` keep their names; their inputs and
  outputs change as described above.
- Affected dependencies: no new deps; `httpx` stays.
- Affected systems: requires Grafana ≥ 12. Grafana ≤ 11 is no longer
  supported (the new endpoints don't exist there). Documented in the
  module docstring.
- Out of scope:
  - Folder / organisation / datasource CRUD.
  - Async API surface.
  - A `xtrade grafana` CLI subcommand group.
  - Reading dashboards via the legacy `/api/dashboards/uid/<uid>`
    fallback for installations that haven't migrated — out of scope
    because every Grafana ≥ 12 has the new endpoints.