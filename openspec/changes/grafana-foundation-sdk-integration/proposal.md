## Why

The current `xtrade.core.grafana` SDK builds dashboards by hand-wiring
nested Python dicts. That is error-prone (one missing key rejects the
unified API with a 400), and every dashboard builder code duplicates
the same nested schema. Grafana publishes an official
**Foundation SDK** (`grafana-foundation-sdk`) that provides typed,
strongly-validated builders for every dashboard / panel / datasource
field. The SDK's own README tells users to wire its JSON output to the
Grafana HTTP API — exactly what our `DashboardsAPI` already does. The
two halves are complementary: Foundation SDK for **constructing**
dashboards, our SDK for **transport + CRUD + panel mutation**.

## What Changes

- Add `grafana-foundation-sdk` to `[project.dependencies]` (Python
  pin `>=0.0.18,<1.0`).
- Expose a thin `xtrade.core.grafana.builder` module that wraps the
  Foundation SDK builders for the operations `xtrade` actually needs
  — `DashboardBuilder` (typed wrapper around `dashboard.Builder`),
  `TimeseriesPanelBuilder`, `StatPanelBuilder`, and a small helper
  `build_envelope(builder, *, name, message=None, folder_uid=None)
  -> dict` that produces the unified-API envelope Grafana expects
  (`{ metadata: { name, annotations: { ... } }, spec: <dashboard JSON> }`).
- `DashboardsAPI.create` accepts either a raw envelope dict
  (existing behaviour, preserved for tests) **or** an object that
  exposes `.build() -> dict` or is a Foundation SDK `Dashboard` /
  `dashboard.Builder`. Internally it calls `.build()` when needed and
  forwards the resulting envelope to the HTTP path. This is **not
  breaking** — pure-dict callers keep working.
- `PanelsAPI.update_panel` accepts an optional Foundation SDK panel
  builder in place of the `title=` / `targets=` kwargs. When the
  caller supplies a builder, the SDK merges its fields into the
  existing panel object before the round-trip POST. Again **not
  breaking** — keyword-arg callers keep working.
- Update tests to exercise the Foundation-SDK path end-to-end: build a
  dashboard with `DashboardBuilder`, post it, mutate a panel via a
  `TimeseriesPanelBuilder`, verify, and delete. Existing dict-based
  tests stay green.
- Keep `xtrade.core.grafana` free of business-layer imports. The
  Foundation SDK is a third-party dependency, not a business module.

**BREAKING**: none. The dict-based payload path stays, so any caller
that already works with `create({"metadata": {...}, "spec": {...}})`
or `update_panel(uid, panel_id=..., title="...")` continues to work.

## Capabilities

### New Capabilities

- `core-grafana-builder`: typed builder helpers on top of
  `grafana-foundation-sdk` that produce the unified-API envelope
  shape. Spec: `specs/core-grafana-builder/spec.md`.

### Modified Capabilities

- `core-grafana`: `DashboardsAPI.create` and `PanelsAPI.update_panel`
  accept Foundation SDK builders in addition to the existing dict /
  kwarg forms. Spec:
  `specs/core-grafana/spec.md` (delta against the existing capability
  archived by `add-grafana-client` + `grafana-client-v2-apis`).

## Impact

- Affected code:
  - `src/xtrade/core/grafana/builder.py` — new module: typed
    `DashboardBuilder`, `TimeseriesPanelBuilder`, `StatPanelBuilder`,
    `build_envelope(...)`.
  - `src/xtrade/core/grafana/__init__.py` — re-export the new typed
    builders.
  - `src/xtrade/core/grafana/dashboards.py` — `create` accepts either
    an envelope dict or an object with `.build() -> dict`.
  - `src/xtrade/core/grafana/panels.py` — `update_panel` accepts an
    optional `panel_builder` keyword; when given, it overlays the
    SDK-built panel's fields onto the existing one before the POST.
  - `tests/core/test_grafana_builder.py` — new test module for the
    Foundation-SDK builders (no real Grafana).
  - `tests/core/test_grafana_client.py` — add one or two tests for
    the new Foundation-SDK-aware paths (dict behaviour stays).
- Affected APIs: `DashboardsAPI.create` and `PanelsAPI.update_panel`
  accept additional input types (additive).
- Affected dependencies: `grafana-foundation-sdk>=0.0.18,<1.0` added
  to `pyproject.toml [project.dependencies]`. No other deps change.
- Affected systems: requires `grafana-foundation-sdk` at import time;
  the package must be importable in any environment that uses
  `xtrade.core.grafana.builder`. Optional callers that only use
  `DashboardsAPI.list/get/delete` are unaffected.
- Out of scope:
  - Adding Foundation SDK builders for resources other than dashboards
    and the two most common panels (timeseries / stat). If a future
    need shows up (logs, gauge, table, ...) extend `builder.py`
    additively.
  - Re-exporting the raw `grafana_foundation_sdk.*` package surface.
    Callers needing uncommon resources can import them directly.
  - Removing the dict-based payload path. It stays for the entire
    0.x lifecycle because the SDK is typed but the unified API is
    JSON, and round-tripping through a builder for every call would
    punish callers that already have a dashboard dict.