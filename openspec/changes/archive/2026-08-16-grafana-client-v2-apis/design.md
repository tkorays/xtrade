## Context

The Grafana SDK shipped by `add-grafana-client` talks only to the
legacy `/api/dashboards[/...]` endpoints that were removed in Grafana
12. A live smoke test against the user's Grafana 13.1.3 instance
returned `404` for every legacy route and `200` for the new
`/apis/dashboard.grafana.app/v1/...` routes — so the migration is
mandatory, not optional.

The unified API is structurally different:

- Endpoints are namespaced: every path includes
  `/namespaces/{namespace}/dashboards[...]`.
- Dashboard JSON is wrapped in an envelope:
  `{ kind, apiVersion, metadata: { name, namespace, uid, ... }, spec:
  { ...dashboard json... } }`. The legacy `{ dashboard, message,
  folderUid, overwrite }` shape is gone.
- Pagination uses the `continue` token in
  `metadata.continue`, not `?page=N`.
- `POST` create returns `201 Created`, not `200`.
- `DELETE` returns `204 No Content`, not `200` with a body.
- `folderUid` is replaced by `metadata.annotations["grafana.app/folder"]`
  and the commit `message` by
  `metadata.annotations["grafana.app/message"]`.

The change keeps the public method names on `DashboardsAPI` /
`PanelsAPI` so call sites stay readable; the inputs and returned types
change to match the new envelope.

## Goals / Non-Goals

**Goals:**

- All dashboard operations work against Grafana ≥ 12 out of the box.
- Keep the auth / error-mapping / layering contract from
  `add-grafana-client` intact.
- Add a `namespace` field to `GrafanaConfig` with `default` as the
  default, overridable via `XTRADE_GRAFANA__NAMESPACE` and
  `client.with_namespace(...)`.

**Non-Goals:**

- Grafana ≤ 11 support.
- Folder / organisation / datasource CRUD.
- Async API surface.
- A CLI subcommand group.

## Decisions

### Decision 1: All dashboard endpoints go through one helper

`_client.py` exposes `_namespace_root(namespace) -> str` returning
`{cfg.base_url}/apis/dashboard.grafana.app/v1/namespaces/{namespace}`.
Every dashboard path is then a join off this root. Centralising the
path makes the per-call `with_namespace` override a one-line change
(see Decision 4).

### Decision 2: New types live alongside the old ones

- `DashboardSummary(uid, name, namespace, title)` — `type` removed
  (Grafana 12 doesn't populate it the same way).
- `DashboardWithMeta(dashboard: dict[str, object], meta: dict[str, object])`
  stays, but `dashboard` now holds the unified envelope. Convenience
  properties `envelope.spec` (the dashboard JSON), `.uid`,
  `.name`, `.namespace` are exposed so call sites don't reach through
  nested dicts.

We keep `DashboardWithMeta` as the return type to minimise churn
downstream; we add a separate `DashboardEnvelope` type only if a future
need shows up. For this change the unified payload is just "the dict
returned by Grafana".

### Decision 3: `create` / `update` take the envelope as input

The SDK no longer wraps a bare dashboard JSON inside an envelope; the
caller passes the envelope directly:

```python
client.dashboards.create(
    {
        "metadata": {"name": "abc", "annotations": {"grafana.app/folder": "f1"}},
        "spec": {"panels": [...]},
    },
    message="hi",
)
```

This is unambiguous, lets the caller control folder / annotations
exactly, and keeps the SDK a thin wrapper. `create()` only requires
`metadata.name` (string) and `spec` (dict); anything else is forwarded
as-is.

### Decision 4: `with_namespace(name) -> GrafanaClient`

Returns a *new* `GrafanaClient` whose `_namespace` differs from the
original. The original client is unchanged. Implementation: copy
`self.__dict__` and replace the `_namespace` attribute, plus
construct a new `DashboardsAPI` / `PanelsAPI` bound to the copy. The
underlying `httpx.Client` is shared so connection pooling is preserved.

Alternative considered: make `namespace` a per-method kwarg. Rejected
because every dashboard method would have to accept it, which clutters
the API for the common (single-namespace) case.

### Decision 5: Pagination uses the `continue` token

`list()` builds a `GET ?limit=N`, then on each response checks
`body["metadata"]["continue"]`. While it's non-empty / non-null we
re-issue the same path with `?continue=<token>&limit=N`. We loop until
the token is absent or the page is shorter than `limit`. We use the
Grafana-side `limit` default of 200 (matches the legacy default of
`?limit=1000` well enough — operators with > 10k dashboards per
namespace are out of scope).

### Decision 6: `update_panel` reads / writes inside `spec.panels`

The convenience mutation lives at `envelope.spec["panels"]`. The
updated envelope (with the same `metadata` block plus the message
annotation) is handed to `dashboards.update(uid, envelope, message=...)`
unchanged.

### Decision 7: Validation up front, errors down the line

- `create()` validates `metadata.name` (non-empty str) and `spec`
  (dict) before issuing the request.
- `update()` validates `spec` (dict); forces `metadata["name"] = uid`
  so a caller who built a fresh envelope doesn't have to remember the
  name.
- `delete()` does not pre-validate (the URL is fully determined by
  `uid`).

### Decision 8: 201 vs 200 are both "success" for `_request`

The wrapper raises `GrafanaAPIError` only on `>= 400`. Create returns
201, delete returns 204 — both are accepted as success by the wrapper.

### Decision 9: No business-layer imports; same layering rules

The migration stays inside `xtrade.core.grafana` and may import only
`xtrade.core.config` (now also `GrafanaConfig.namespace`) and stdlib +
`httpx`. Tests assert the same layering invariant.

## Risks / Trade-offs

- **[Risk] Caller code written against the legacy SDK shape breaks** →
  documented as **BREAKING** in the proposal. Migration is mechanical
  (wrap JSON in `{ metadata: { name, annotations: { grafana.app/folder?
  } }, spec: { ... } }`). The SDK raises `ValueError` on a payload
  missing `metadata.name` / `spec` so failures are loud and early.
- **[Risk] `with_namespace` returns a shallow copy; mutations on the
  returned `DashboardsAPI` would leak** → the copy is constructed
  fresh from the same `GrafanaClient`, so sub-API references are new
  and cannot mutate the original.
- **[Risk] Older Grafana versions (< 12) lose support** → the SDK
  raises 404 from every dashboard route; the error message points at
  the new endpoints. Documented in the module docstring.
- **[Risk] Pagination semantics differ between Grafana 12 and 13** →
  both versions expose `metadata.continue`. If a future Grafana
  removes the field entirely, the SDK's pagination loop terminates on
  the first short page (it falls through the `continue` check), which
  is the safer default.

## Migration Plan

This change is purely additive to the file layout and a breaking
rewrite of the payload contract:

1. Land the new implementation; archive `add-grafana-client` and
   `grafana-client-v2-apis` together (or in sequence).
2. Any caller that built dashboards before this change must migrate
   to the envelope shape. `dashboards.create({ "dashboard": {...},
   "message", "folderUid" })` now raises `ValueError` with the
   expected new shape in the message.
3. No DB migration, no config file migration; `Config.grafana` loads
   as before, with `namespace` defaulting to `"default"`.

Rollback:

- Revert this commit. `add-grafana-client`'s legacy SDK is restored.

## Open Questions

None.