## 1. Dependency + scaffolding

- [ ] 1.1 Add `httpx>=0.27,<1.0` to `[project.dependencies]` in `pyproject.toml`. Run `uv lock` (or `uv sync`) so the lockfile is updated.
- [ ] 1.2 Create the package skeleton `src/xtrade/core/grafana/__init__.py` (re-exports nothing yet), `errors.py`, `types.py`, `_client.py`, `dashboards.py`, `panels.py`. Each file MUST be empty or contain only its declared types / class stubs — no business logic until the later tasks.
- [ ] 1.3 Confirm `uv run python -c "import xtrade.core.grafana"` succeeds (modules importable, no syntax errors).

## 2. Errors + types

- [x] 2.1 In `errors.py`, define `class GrafanaError(Exception)` (base), `class GrafanaAPIError(GrafanaError)` with attributes `status_code: int`, `url: str`, `body: object` (the parsed JSON dict / list when possible, otherwise the raw string), and `class GrafanaAuthError(GrafanaError)`.
- [x] 2.2 In `types.py`, define `@dataclass(frozen=True) DashboardSummary(uid: str, title: str, uri: str, type: str)`, `@dataclass(frozen=True) Panel(id: int, title: str, type: str)`, and a typed view `DashboardWithMeta` (`@dataclass(frozen=True)`) with `dashboard: dict[str, object]` and `meta: dict[str, object]`. Provide a `class _PanelNotFoundError(ValueError)` if convenient (or raise plain `ValueError` directly in `panels.py`).

## 3. `GrafanaClient` + auth + request plumbing

- [ ] 3.1 In `_client.py`, implement `class GrafanaClient` with `__init__(self, cfg: GrafanaConfig | None = None, *, transport: httpx.BaseTransport | None = None, **cfg_kwargs: object) -> None`. Accept either a `GrafanaConfig` instance or kwargs forwarded to `GrafanaConfig(...)`. Resolve `cfg` first; raise `GrafanaAuthError` when `cfg.api_key == ""` and (`cfg.user == ""` or `cfg.password == ""`).
- [ ] 3.2 Build `self._headers`: prefer `Authorization: Bearer <api_key>` when `cfg.api_key` is non-empty; otherwise `Authorization: Basic <base64(user:password)>` using stdlib `base64.b64encode`.
- [ ] 3.3 Build `self._base_url` from `cfg.base_url`. Construct `self._client = httpx.Client(base_url=cfg.base_url, headers=self._headers, timeout=cfg.timeout, verify=cfg.verify_ssl, transport=transport)`. Expose it as `self.http` so callers can patch headers.
- [ ] 3.4 Implement `_request(method: str, path: str, **kwargs: object) -> httpx.Response` that calls `self._client.request(method, path, **kwargs)` and raises `GrafanaAPIError(status_code, url=str(response.url), body=parsed_or_raw)` on `response.status_code >= 400`. Network exceptions (`httpx.HTTPError`) propagate untouched.
- [ ] 3.5 Attach `self.dashboards = DashboardsAPI(self)` and `self.panels = PanelsAPI(self)`. Both sub-APIs receive a reference to the client and call `client._request(...)`.

## 4. `DashboardsAPI` CRUD

- [ ] 4.1 Implement `list() -> list[DashboardSummary]` in `dashboards.py`. Call `GET /api/search?type=dash-db&limit=1000` with `page=1, 2, ...` until a page returns fewer than `limit` items. Map each `{uid, title, uri, type}` entry to a `DashboardSummary`.
- [ ] 4.2 Implement `get(uid: str) -> DashboardWithMeta`. Call `GET /api/dashboards/uid/<uid>` and return `DashboardWithMeta(dashboard=body["dashboard"], meta=body["meta"])`.
- [ ] 4.3 Implement `create(payload: dict, *, message: str = "Created via xtrade", folder_uid: str | None = None) -> DashboardWithMeta`. Raise `ValueError("dashboard payload must not contain 'uid'")` when `payload` already has a `uid`. Send `POST /api/dashboards` with body `{ "dashboard": payload, "message": message, "folderUid": folder_uid, "overwrite": False }`; return the resulting `DashboardWithMeta` (from the response body which mirrors `get`).
- [ ] 4.4 Implement `update(uid: str, payload: dict, *, message: str = "Updated via xtrade", folder_uid: str | None = None) -> DashboardWithMeta`. Set `payload["uid"] = uid`, `payload.pop("id", None)`. Send `POST /api/dashboards` with `overwrite=True`. Return the resulting `DashboardWithMeta`.
- [ ] 4.5 Implement `delete(uid: str) -> None`. Call `DELETE /api/dashboards/uid/<uid>`. Return `None` on success.

## 5. `PanelsAPI.update_panel`

- [ ] 5.1 In `panels.py`, implement `update_panel(dashboard_uid: str, panel_id: int, *, title: str | None = None, description: str | None = None, targets: list[dict] | None = None, options: dict | None = None, field_config: dict | None = None, transparent: bool | None = None, message: str = "Panel updated via xtrade") -> DashboardWithMeta`.
- [ ] 5.2 Internally call `self._client.dashboards.get(dashboard_uid)`, locate the panel whose `id == panel_id` in `dashboard["panels"]`, raise `ValueError(f"panel id {panel_id} not found in dashboard {dashboard_uid}")` if missing, apply only the supplied overrides, then call `self._client.dashboards.update(dashboard_uid, dashboard, message=message)`. Return the result.

## 6. Public re-exports

- [x] 6.1 In `__init__.py`, re-export `GrafanaClient`, `DashboardsAPI`, `PanelsAPI`, `DashboardSummary`, `DashboardWithMeta`, `Panel`, `GrafanaError`, `GrafanaAPIError`, `GrafanaAuthError` so `from xtrade.core.grafana import GrafanaClient` works for every documented symbol.

## 7. Tests

- [ ] 7.1 Create `tests/core/test_grafana_client.py` with a `FakeTransport` helper (a callable returning canned `httpx.Response` objects based on `request.url.path` and `request.method`). Inject it via `GrafanaClient(..., transport=fake)`.
- [ ] 7.2 Test auth selection: bearer header when `api_key` is set, basic header when `api_key=""` and credentials are set, `GrafanaAuthError` when both are empty.
- [ ] 7.3 Test `dashboards.list()`: fake `/api/search` returning two pages (limit=2, two pages of 2 → 4 items) and verify the helper concatenates correctly. Test single-page behaviour when the page is short.
- [ ] 7.4 Test `dashboards.get(uid)` returns `DashboardWithMeta` with `dashboard` and `meta`.
- [ ] 7.5 Test `dashboards.create(payload)` sends `overwrite=False`, raises `ValueError` when the payload has a `uid`, and returns the response's dashboard.
- [ ] 7.6 Test `dashboards.update(uid, payload)` forces `uid`, drops `id`, sends `overwrite=True`.
- [ ] 7.7 Test `dashboards.delete(uid)` returns `None`.
- [ ] 7.8 Test `panels.update_panel(uid, 4, title="X")` only changes the matching panel's title and leaves other panels untouched; test `targets=` replaces the list; test unknown `panel_id` raises `ValueError` and no `POST /api/dashboards` is sent.
- [ ] 7.9 Test that a non-2xx response raises `GrafanaAPIError(status_code=404, url=..., body={...})`.
- [ ] 7.10 Verify the package has no business-layer imports by asserting the only `xtrade.*` import is `xtrade.core.config.GrafanaConfig`.

## 8. Validation

- [x] 8.1 `uv run pytest tests/core/test_grafana_client.py tests/test_config.py -q` passes (the new tests plus the existing config tests stay green).
- [x] 8.2 `uv run pytest -q` passes (full suite, no regressions).
- [x] 8.3 `uv run ruff check src tests` clean.
- [x] 8.4 `uv run ruff format --check src tests` clean.
- [x] 8.5 `uv run mypy src` strict mode clean.
- [x] 8.6 `openspec validate --all --strict` clean (this change and every other spec still pass).