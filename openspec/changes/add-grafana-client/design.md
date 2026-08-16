## Context

`Config.grafana` already exists in `xtrade.core.config` (added in the most
recent config edit) with all the fields a Grafana client needs: `host`,
`port`, `scheme`, `path_prefix`, basic-auth (`user`/`password`), bearer
token (`api_key`), `org_slug`, `default_dashboard_uid`, `timeout`, and
`verify_ssl`. The missing piece is a Python SDK that consumes this config
and calls the Grafana HTTP API.

The project has no existing HTTP client dependency. The smallest, best-fit
choice for a synchronous SDK is `httpx`, which gives us a typed response
object, built-in timeout / TLS verification, and a `MockTransport` for
unit tests.

`xtrade.core` is the project's "horizontal" layer — it must not import
business modules (`strategy`, `execution`, `engine`, `data`, `risk`) and
must not depend on `mos.*`. The new package therefore lives under
`xtrade.core.grafana` and depends only on `xtrade.core` (for the config
type) + stdlib + `httpx`.

## Goals / Non-Goals

**Goals:**

- A small, dependency-light Python SDK that exposes Grafana dashboard
  CRUD and panel update.
- Reuse `GrafanaConfig` and honour every documented field (auth, scheme,
  path_prefix, timeout, verify_ssl, org_slug).
- Be testable without a real Grafana — every test injects a fake
  transport into `httpx.Client`.

**Non-Goals:**

- Async API surface.
- Datasource / folder / organisation / alerting CRUD.
- A `xtrade grafana` CLI group.
- Caching, retries, rate limiting — callers wrap these as needed.

## Decisions

### Decision 1: `httpx` over `requests` / `urllib`

`httpx` is the smallest addition that gives us a typed `Response`,
structured timeouts, `verify_ssl` plumbing, and a `MockTransport` for
tests. `requests` would also work but lacks the `MockTransport` test
ergonomics; `urllib` would force us to hand-roll JSON / TLS plumbing.
A new dependency is unavoidable because the project today has no HTTP
client — `httpx>=0.27` is added to `[project.dependencies]`.

### Decision 2: Package layout under `xtrade.core.grafana/`

```
core/grafana/
├── __init__.py          # re-exports the public surface
├── _client.py           # GrafanaClient: httpx.Client + auth + base URL
├── dashboards.py        # DashboardsAPI
├── panels.py            # PanelsAPI
├── errors.py            # GrafanaError, GrafanaAPIError, GrafanaAuthError
└── types.py             # DashboardSummary, DashboardWithMeta, Panel (TypedDict/dataclass)
```

`_client.py` is private (`_`-prefixed) because it owns the `httpx.Client`
and the request plumbing; users only interact with `GrafanaClient`,
`dashboards`, `panels`, and the error classes.

### Decision 3: Auth selection happens at construction time

`GrafanaClient.__init__` builds the auth header once and stores it on
`self._headers`. There is no per-request auth logic — this matches
httpx's `Client.headers` and keeps the request path trivial. Precedence:

1. `api_key` non-empty → `Authorization: Bearer <api_key>`.
2. `user` + `password` → `Authorization: Basic <base64(user:password)>`.
3. `api_key` empty and `user` empty → raise `GrafanaAuthError` at
   construction (no point lazy-failing on the first call).
4. `user` set, `password` empty → raise `GrafanaAuthError` (we never
   allow asymmetric basic auth).

The `X-Grafana-Org-Id` header is **not** sent because `org_slug` cannot
be resolved to a numeric id from the config alone — we accept the
default org on the server side. (Grafana accepts the `X-Grafana-Org-Id`
header as an integer; if a future caller knows the numeric id, they can
patch `client._client.headers["X-Grafana-Org-Id"]` themselves.)

### Decision 4: `DashboardsAPI.update` posts the dashboard, not a partial

Grafana has no "update one panel" endpoint. To mutate a panel we must
re-POST the entire dashboard JSON with `overwrite=True`. We split that
into two layers:

- `DashboardsAPI.update(uid, payload, *, overwrite=True, ...)` — generic
  POST. `create` calls it with `overwrite=False`.
- `PanelsAPI.update_panel(uid, panel_id, **fields)` — fetches with
  `dashboards.get(uid)`, mutates the matching panel, calls
  `dashboards.update(uid, payload)`.

This means `update` is the lowest-level entry point and `update_panel`
is a convenience on top. Tests verify that `update` honours
`overwrite=True` and that the convenience merges only the supplied
fields.

### Decision 5: `update` forces `payload["uid"] = uid` and clears `id`

Grafana's `POST /api/dashboards` rejects a payload whose `id` does not
match the existing row, and a payload whose `uid` mismatches the URL
when `overwrite=True`. To make `update(uid, payload)` safe regardless of
how the caller built `payload`, we set `payload["uid"] = uid` and delete
`payload["id"]` (if present) before sending.

### Decision 6: Error mapping is centralised in one helper

`_client.py` exposes `_request(method, path, **kwargs) -> httpx.Response`
that:

- Builds the URL via `urljoin(self._base_url, path)`.
- Calls `self._client.request(...)`.
- If `response.status_code >= 400`, raises `GrafanaAPIError(...)`.
- Otherwise returns the response.

All public methods (`list`, `get`, `create`, `update`, `delete`,
`update_panel`) call `_request`. Network exceptions (`httpx.HTTPError`,
`httpx.ConnectError`, etc.) propagate untouched — the spec says so
explicitly, and the caller decides on retries.

### Decision 7: `list()` paginates transparently

`/api/search?type=dash-db` accepts `page` (1-based) and `limit`. We call
it with `limit=1000` and loop `page=1, 2, ...` until the response is
shorter than `limit`. This keeps the public API as
`list() -> list[DashboardSummary]` while remaining correct for any
Grafana install. A Grafana with ≤ 1000 dashboards returns one page;
installations larger than that paginate automatically.

### Decision 8: Types are lightweight `TypedDict`s, not pydantic models

The SDK returns Grafana JSON; we don't need to validate it back. The
public types (`DashboardSummary`, `DashboardWithMeta`, `Panel`) are
`@dataclass(frozen=True)` for ergonomic attribute access and IDE help,
without a pydantic validation step. They are also exposed as `TypedDict`
for callers that prefer dict-style access. Both live in `types.py`.

### Decision 9: Tests use `httpx.MockTransport`

`httpx.MockTransport` accepts a callable
`(request: httpx.Request) -> httpx.Response`. We hand it a tiny
`FakeTransport` that dispatches on `request.url.path` and returns canned
JSON, so the full CRUD path runs in-process with no socket IO. This
keeps the suite fast and deterministic without introducing a new HTTP
mocking framework.

### Decision 10: No business-layer imports; no `mos.*` imports

`xtrade.core.grafana` MAY import:

- `xtrade.core.config.GrafanaConfig`
- stdlib (`base64`, `json`, `urllib.parse`)
- third-party (`httpx`, `pydantic` — only for the `BaseModel` it inherits
  from `GrafanaConfig`; the SDK itself does not construct models).

It MUST NOT import `xtrade.strategy`, `xtrade.execution`,
`xtrade.engine`, `xtrade.data`, `xtrade.risk`, or anything from `mos.*`.

## Risks / Trade-offs

- **[Risk] `org_slug` is not sent as `X-Grafana-Org-Id`** → Operators
  with multi-org setups must resolve the numeric id themselves and
  patch `client._client.headers["X-Grafana-Org-Id"]`. Documented in the
  module docstring of `_client.py`. Mitigated by exposing the underlying
  `httpx.Client` as `client.http` so callers can adjust headers.

- **[Risk] `update` is a full-dashboard POST, not a PATCH** → Grafana
  itself doesn't expose PATCH; this is the canonical pattern. Mitigated
  by `update_panel` which fetches → mutates → posts so callers don't
  have to assemble the full payload themselves.

- **[Risk] Network errors are not wrapped** → Callers must `try/except
  httpx.HTTPError` themselves. Spec'd behaviour; documents the boundary
  clearly so retry / circuit-breaker code belongs at the caller.

- **[Risk] New runtime dependency (`httpx`)** → `httpx` is a small,
  well-maintained dep with a permissive license; impact on
  install size and supply-chain is minimal. Mitigated by pinning to
  `>=0.27,<1.0` in `pyproject.toml`.

- **[Risk] Mocked tests could miss real Grafana quirks** → The
  `FakeTransport` returns JSON in the exact shape Grafana returns; we
  spot-checked against Grafana 10.x docs. If a future Grafana version
  changes a payload shape, the relevant `tests/core/test_grafana_client.py`
  test will start failing and we'll fix the type contract.

## Migration Plan

This change is purely additive:

1. Add `httpx>=0.27` to `[project.dependencies]` in `pyproject.toml`.
2. Land the new package `xtrade.core.grafana`.
3. Existing callers continue to work — no existing code imports from
   `xtrade.core.grafana`. The `Config.grafana` field is the only
   existing touchpoint; its defaults already avoid the legacy
   `admin/admin` hardcoded password.

Rollback:

- Drop the new package and the `httpx` dependency. No DB migration, no
  config migration, no other touchpoint.

## Open Questions

None.