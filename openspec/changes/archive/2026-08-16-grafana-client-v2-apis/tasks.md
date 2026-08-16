## 1. `GrafanaConfig.namespace`

- [x] 1.1 In `src/xtrade/core/config.py`, add `namespace: str = "default"` to `GrafanaConfig` (after `org_slug`). Add the corresponding env-var override comment to match the existing fields.

## 2. Client + namespace path helper

- [ ] 2.1 In `src/xtrade/core/grafana/_client.py`, add a private `_namespace_root(self) -> str` method on `GrafanaClient` that returns `{cfg.base_url}/apis/dashboard.grafana.app/v1/namespaces/{self._namespace}`.
- [ ] 2.2 Add `with_namespace(self, namespace: str) -> GrafanaClient` returning a shallow copy of `self` with `_namespace` replaced; the new `DashboardsAPI` / `PanelsAPI` instances are bound to the copy.

## 3. `DashboardsAPI` rewrite

- [ ] 3.1 In `src/xtrade/core/grafana/types.py`, update `DashboardSummary` to `(uid, name, namespace, title)` and add a `DashboardEnvelope` helper exposing `.spec`, `.uid`, `.name`, `.namespace` properties over `DashboardWithMeta.dashboard`.
- [ ] 3.2 In `src/xtrade/core/grafana/dashboards.py`, replace `list()` to call `GET {root}?limit=200` and loop on `metadata.continue` until absent. Map each `{metadata: {name, namespace, uid}, spec: {title}}` row to `DashboardSummary(uid=metadata.uid, name=metadata.name, namespace=metadata.namespace, title=spec.title)`.
- [ ] 3.3 Replace `get(uid)` to call `GET {root}/{uid}` and return `DashboardWithMeta(dashboard=<envelope>, meta=<metadata>)`.
- [ ] 3.4 Replace `create(payload, *, message, folder_uid)`:
  - Validate `metadata.name` (non-empty str) and `spec` (dict); raise `ValueError` otherwise.
  - Build body `{ "metadata": { **name, "annotations": { "grafana.app/message": message, **({ "grafana.app/folder": folder_uid } if folder_uid else {}) } }, "spec": payload["spec"] }` (preserve any caller-supplied `metadata.annotations` keys other than the two grafana.app ones by merging carefully).
  - POST `{root}` and return the result wrapped via `DashboardWithMeta`.
- [ ] 3.5 Replace `update(uid, payload, *, message, folder_uid)`:
  - Validate `payload["spec"]` is a dict; raise `ValueError` otherwise.
  - Force `payload["metadata"]["name"] = uid`.
  - Send the same envelope as `create()` to `POST {root}/{uid}`.
- [ ] 3.6 Replace `delete(uid)` to call `DELETE {root}/{uid}`; expect `204 No Content`; return `None`.

## 4. `PanelsAPI.update_panel`

- [x] 4.1 In `src/xtrade/core/grafana/panels.py`, change the panel-lookup to use `envelope.spec["panels"]` (where `envelope` is `current.dashboard`).
- [x] 4.2 Change the post step to call `self._client.dashboards.update(dashboard_uid, current.dashboard, message=message)` so the same envelope (with the mutated `spec`) goes back to Grafana.

## 5. Public re-exports

- [x] 5.1 In `src/xtrade/core/grafana/__init__.py`, ensure `DashboardSummary` (new shape) and `DashboardEnvelope` (if exposed) are imported and listed in `__all__`. Keep `GrafanaClient`, `DashboardsAPI`, `PanelsAPI`, `DashboardWithMeta`, `GrafanaError`, `GrafanaAPIError`, `GrafanaAuthError` exported unchanged.

## 6. Tests

- [x] 6.1 In `tests/core/test_grafana_client.py`, rewrite the `FakeTransport` routes to match the new paths: `/apis/dashboard.grafana.app/v1/namespaces/default/dashboards`, `.../dashboards/{uid}`, `.../dashboards/{uid}` (POST/PUT/DELETE). Update canned responses to use the unified envelope.
- [x] 6.2 Rewrite the existing tests:
  - `test_list_single_short_page` returns summaries built from the unified envelope.
  - `test_list_paginates_until_short_page` becomes `test_list_paginates_with_continue_token`: first response carries `metadata.continue = "abc"` and a second response without it; assert both pages issue with the right `?continue=` query.
  - `test_get_returns_dashboard_and_meta` returns the unified envelope.
  - `test_create_rejects_payload_with_uid` becomes `test_create_rejects_payload_without_metadata_name`: assert `ValueError`.
  - `test_create_sends_overwrite_false_and_returns_dashboard` becomes `test_create_posts_envelope`: assert request body shape and 201 response.
  - `test_create_includes_folder_uid_when_provided` becomes `test_create_includes_folder_annotation`: assert `metadata.annotations["grafana.app/folder"] == "f1"`.
  - `test_update_sets_uid_drops_id_and_sends_overwrite_true` becomes `test_update_sets_metadata_name_and_posts_envelope`.
  - `test_delete_returns_none` still passes against `204`.
  - Panel tests now feed / assert against the unified envelope.
  - `test_non_2xx_raises_grafana_api_error_with_parsed_body` stays, route renamed.
- [x] 6.3 Add a new test `test_with_namespace_overrides_request_path` that registers a route under `.../namespaces/team-b/dashboards` and asserts that `client.with_namespace("team-b").dashboards.list()` hits it.
- [x] 6.4 Add a new test `test_config_namespace_env_var_override` that monkey-patches `XTRADE_GRAFANA__NAMESPACE` and asserts the SDK builds the new path.
- [x] 6.5 Add a new test `test_auth_*` regression: bearer/basic/GrafanaAuthError cases stay green with the new request paths.
- [x] 6.6 Update `test_package_does_not_import_business_modules` to also assert no legacy `/api/dashboards` strings appear in the package source.

## 7. Validation

- [x] 7.1 `uv run pytest tests/core/test_grafana_client.py tests/test_config.py -q` passes (rewritten tests + config tests stay green).
- [x] 7.2 `uv run pytest -q` passes (full suite, no regressions).
- [x] 7.3 `uv run ruff check src tests` clean.
- [x] 7.4 `uv run ruff format --check src tests` clean.
- [x] 7.5 `uv run mypy src` strict mode clean.
- [x] 7.6 `openspec validate --all --strict` clean (this change + the previous `add-grafana-client` both still validate).