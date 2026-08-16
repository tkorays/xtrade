## Context

`xtrade.core.grafana` ships a typed HTTP SDK that talks to Grafana
12+'s unified API. Today, callers build dashboards by hand-wiring
nested dicts. The Grafana project ships an official
`grafana-foundation-sdk` (Python package) that provides typed builders
for dashboards and every panel type — strong typing, validation, and
IDE autocompletion across the entire dashboard JSON schema. Its own
README tells users to wire its JSON output to Grafana's HTTP API,
which is exactly what `DashboardsAPI` already does.

The change adds the official SDK as a project-wide dependency, exposes
a thin typed builder module for the operations `xtrade` actually uses,
and accepts SDK builders in `DashboardsAPI.create` and
`PanelsAPI.update_panel` without breaking existing dict-based
callers.

## Goals / Non-Goals

**Goals:**

- Typed builders for dashboards and the two most common panels
  (timeseries, stat) that produce the unified-API envelope shape.
- `DashboardsAPI.create` and `PanelsAPI.update_panel` accept
  Foundation SDK builders in addition to the existing dict / kwarg
  forms.
- The dict-based payload path keeps working — no breaking changes.
- Layering invariant (`xtrade.core.grafana.*` may not import
  business modules or `mos.*`) is preserved.

**Non-Goals:**

- Exhaustive typed wrappers around every Foundation SDK resource
  (logs, gauge, table, alert rules, etc.). Callers needing uncommon
  resources can `import grafana_foundation_sdk.* as sdk` directly.
- Re-exporting the full `grafana-foundation-sdk` Python surface.
- Removing the dict path. Pure-dict callers are common and useful;
  the SDK is one option, not the only option.

## Decisions

### Decision 1: Foundation SDK as a `[project.dependencies]` entry

`grafana-foundation-sdk>=0.0.18,<1.0` is added directly to
`pyproject.toml`'s `[project.dependencies]`. The SDK is a small,
Apache-2.0 package; making it optional would force every caller of
`xtrade.core.grafana.builder` (or even `DashboardsAPI.create` with a
builder) to install an extra, which violates "small set of project
defaults" from AGENTS.md. Optional dependency groups were considered
and rejected for the same reason.

### Decision 2: Thin wrapper, not a re-export

The new `xtrade.core.grafana.builder` module wraps the Foundation SDK
`dashboard.Builder` + panel builders with a minimal facade. We do
**not** re-export `grafana_foundation_sdk.*`. Reasons:

- `grafana-foundation-sdk` exposes hundreds of resources and
  thousands of types. Re-exporting the surface makes our public API
  a moving target tied to its releases.
- The two resources `xtrade` cares about today are dashboards +
  timeseries / stat panels. Wrapping just these keeps the surface
  small and easy to refactor when the SDK evolves.
- Callers needing uncommon resources (logs, table, alert rules) can
  `import grafana_foundation_sdk as sdk` and build those directly,
  then call `DashboardsAPI.create(sdk.<type>.build(), name=...)`.

### Decision 3: Duck-typed `.build()` interface

`build_envelope` accepts both Foundation SDK builders and any object
with `.build() -> dict`. This is a deliberate "structural" contract:

- The Foundation SDK's `dashboard.Builder.build()` already returns a
  Python dict that Grafana accepts.
- Any future caller can define their own builder class with a
  `.build()` method, no inheritance required.
- Tests assert the contract by passing a small fake object with a
  `.build()` method — no SDK coupling in the test layer.

### Decision 4: `create()` accepts either a dict or a builder

`DashboardsAPI.create(payload, *, name, message, folder_uid)`:

- When `payload` is a dict, the existing path runs unchanged. The
  dict is treated as the unified-API envelope (`metadata.name` +
  `spec`); validation as before.
- When `payload` is **not** a dict, the SDK calls `payload.build()` to
  obtain the dashboard JSON, wraps it as an envelope using `name` /
  `message` / `folder_uid`, and posts.

This means callers can pass a `DashboardBuilder` directly:
`client.dashboards.create(DashboardBuilder(title="PnL"), name="pnl")`.

Note: when callers already have an envelope-shaped dict (the common
case today), they keep using the dict path. The new path is for
callers who prefer to build dashboards with Foundation SDK types.

### Decision 5: `update_panel` accepts an optional `panel_builder`

`PanelsAPI.update_panel(name, panel_id, ..., panel_builder=None, ...)`.
When `panel_builder` is supplied, the SDK calls `.build()` on it and
merges the resulting dict's top-level keys into the existing panel.
Existing kwarg arguments (`title=`, `targets=`, etc.) take
precedence — explicit kwargs win over builder fields. Rationale: it
mirrors the existing kwarg contract while letting callers move any
field to the builder when they want stronger typing.

### Decision 6: Validation stays where the Foundation SDK puts it

We do not re-validate what the SDK already validates (panel types,
units, field-config schemas). The SDK raises its own errors
(`ValueError` / `RuntimeError`) which propagate as-is. Re-wrapping
them would hide what the SDK already tells the caller.

### Decision 7: Tests use the SDK directly with no Grafana

`tests/core/test_grafana_builder.py` constructs `DashboardBuilder`,
`TimeseriesPanelBuilder`, `StatPanelBuilder` instances, calls
`.build()`, and inspects the returned dicts. The Foundation SDK
itself doesn't need Grafana to construct or validate builders; only
the HTTP layer needs Grafana. So no live instance is required.

The end-to-end real-Grafana test (already part of the existing
`grafana-client-v2-apis` smoke scripts) is extended to also exercise
the builder path; a new testdata-based timeseries dashboard is
created with `DashboardBuilder` + `TimeseriesPanelBuilder`.

## Risks / Trade-offs

- **[Risk] Foundation SDK changes its builder API between versions** →
  We pin `>=0.0.18,<1.0`. Until 1.0 we accept that wrapper modules
  may need a small refresh per release; tests catch breakage at
  import time.
- **[Risk] Mixing dict payload and builder payload in the same `create`
  call is confusing** → Mitigated by clear docstrings and a single
  type that says "either dict or builder". The `payload` parameter is
  typed as `object`; callers pick.
- **[Risk] `panel_builder` argument adds API surface** → Acceptable:
  it's optional, defaults to `None`, and the existing kwarg path is
  unchanged.
- **[Risk] `grafana-foundation-sdk` import time** → Minor; the SDK is
  pure Python. Importers pay ~10–30 ms on cold start. Tests accept it.

## Migration Plan

- Install `grafana-foundation-sdk` (via `uv sync`) and add it to
  `pyproject.toml`.
- Land the new module + tests; existing tests stay green.
- Archive this change once `openspec validate --all --strict` passes.

Rollback:

- Drop the new module + remove the dep. Existing dict-based callers
  are unchanged.

## Open Questions

None.