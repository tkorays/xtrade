## Why

`xtrade` already exposes a `GrafanaConfig` section (added in the most recent
config edit) but no programmatic way to talk to a Grafana instance — the
operator has to click through the UI to create dashboards, update panels, or
rotate API keys. We need a first-class Python SDK so downstream code
(operations scripts, a future live-trading dashboard job, alerting glue) can
list / create / update / delete dashboards and mutate individual panels
without leaving the project.

## What Changes

- Add a new `core-grafana` capability: a small, framework-free Python SDK
  built on top of `httpx` that exposes a `GrafanaClient` with two sub-APIs:
  `dashboards` (list / get / create / update / delete by uid or slug) and
  `panels` (update a single panel inside a dashboard). The SDK reads its
  connection settings from `Config.grafana` and supports both bearer-token
  (`api_key`) and basic-auth credentials.
- Extend the existing `xtrade-config` capability with the documented fields
  of `GrafanaConfig` so the configuration contract is fully pinned
  (host / port / scheme / path_prefix / auth / org_slug /
  default_dashboard_uid / timeout / verify_ssl).
- Add a single new runtime dependency, `httpx>=0.27`, declared in
  `pyproject.toml`. No CLI surface is added — the SDK is consumed by
  Python callers only.
- Add unit tests covering happy-path CRUD, panel updates, auth selection,
  and the `GrafanaAPIError` mapping for non-2xx responses. Tests use a
  `FakeTransport` injected into `httpx.Client` so no real Grafana is
  required.

**BREAKING**: none. The existing `GrafanaConfig` defaults already remove
the legacy `admin/admin` hardcoded password (now `password=""`); the
new SDK respects the same defaults.

## Capabilities

### New Capabilities

- `core-grafana`: a Python SDK in `xtrade.core.grafana` that wraps the
  Grafana HTTP API for dashboards and panels. Spec:
  `specs/core-grafana/spec.md`.

### Modified Capabilities

- `xtrade-config`: the `GrafanaConfig` model becomes part of the formal
  contract — fields, defaults, and the `XTRADE_GRAFANA__*` env-var
  override behaviour are pinned in `specs/xtrade-config/spec.md`.

## Impact

- Affected code:
  - `src/xtrade/core/grafana/__init__.py` — re-exports `GrafanaClient`,
    `DashboardsAPI`, `PanelsAPI`, error types.
  - `src/xtrade/core/grafana/_client.py` — internal `httpx`-backed
    transport, auth selection, error normalisation.
  - `src/xtrade/core/grafana/dashboards.py` — `DashboardsAPI`
    (list / get / create / update / delete).
  - `src/xtrade/core/grafana/panels.py` — `PanelsAPI.update_panel`.
  - `src/xtrade/core/grafana/errors.py` — `GrafanaError`,
    `GrafanaAPIError`, `GrafanaAuthError`.
  - `src/xtrade/core/grafana/types.py` — `DashboardSummary`,
    `Dashboard`, `DashboardWithMeta`, `Panel` dataclasses.
  - `tests/core/test_grafana_client.py` — new test module using
    `httpx.MockTransport`.
- Affected APIs: new public surface
  `xtrade.core.grafana.{GrafanaClient, DashboardsAPI, PanelsAPI, ...}`.
  No changes to existing public APIs.
- Affected dependencies: `httpx>=0.27` added to `pyproject.toml`'s
  `[project.dependencies]`. No other deps change.
- Affected systems: requires a reachable Grafana HTTP endpoint at
  runtime; the SDK is otherwise self-contained.
- Out of scope:
  - A `xtrade grafana` CLI subcommand group (deferred; the SDK is
    sufficient for now).
  - Datasource / folder / organisation CRUD beyond what dashboard
    operations need.
  - Alerting rules, annotations, provisioning endpoints.
  - Async support — only the synchronous `httpx.Client` path is
    exposed; async wrappers can be added later if a use-case appears.