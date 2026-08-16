## Purpose

Provides typed Python builders for Grafana dashboards and the panel
shapes `xtrade` actually uses, on top of the official `grafana-foundation-sdk`.
The builders produce the unified-API envelope Grafana 12+ expects, so
they feed directly into `DashboardsAPI.create` / `update` without any
dict plumbing.

## ADDED Requirements

### Requirement: `DashboardBuilder` wraps the Foundation SDK dashboard builder

`xtrade.core.grafana.builder` SHALL expose a `DashboardBuilder` class
that wraps `grafana_foundation_sdk.builders.dashboard.Dashboard` and
exposes a `build() -> dict` method returning the dashboard JSON dict
ready for the unified-API `spec` block.

`DashboardBuilder.__init__` accepts at least:
- `title: str` — dashboard title (becomes `spec.title`)
- `uid: str | None = None` — dashboard uid; when `None`, the Foundation
  SDK picks one
- `tags: list[str] | None = None`
- `timezone: str | None = None`

(The Foundation SDK does not currently expose a `schema_version`
setter, so the SDK's built-in default — currently `42` — is used as-is.)

The builder SHALL expose a fluent `with_panel(panel_builder)` method
that returns `self` so callers can chain. The `build()` method SHALL
return the dashboard JSON dict with camelCase keys matching Grafana's
expected layout (e.g. `schemaVersion`, `gridPos`). Internally the SDK
returns a dataclass model; `DashboardBuilder.build()` calls
`model.to_json()` and recursively normalises any nested models / enums
into plain Python dicts / values.

#### Scenario: Title and tags flow into the spec

- **WHEN** a developer writes `DashboardBuilder(title="PnL", tags=["xtrade"]).with_panel(...).build()`
- **THEN** the returned dict has `title == "PnL"` and `tags == ["xtrade"]`

#### Scenario: Default schema version is the SDK's default

- **WHEN** `DashboardBuilder(title="X").build()` is called with no `schema_version`
- **THEN** the returned dict has `schemaVersion == 42` (the SDK's built-in default)

### Requirement: `TimeseriesPanelBuilder` and `StatPanelBuilder` produce typed panel dicts

The builder module SHALL expose at least:

- `TimeseriesPanelBuilder(title: str, *, unit: str | None = None, ...)` —
  wraps the Foundation SDK timeseries panel builder. Exposes
  `.with_target(...)`, `.build() -> dict`.
- `StatPanelBuilder(title: str, *, unit: str | None = None, ...)` —
  wraps the Foundation SDK stat panel builder. Exposes `.build() -> dict`.

Each panel builder SHALL return a dict shaped like a normal Grafana
panel: `{ id, type, title, gridPos, targets, options, fieldConfig, ... }`.
The Foundation SDK handles validation; the SDK's own validation error
propagates as-is (no wrapping).

#### Scenario: Timeseries panel renders required keys

- **WHEN** a developer builds a panel with `TimeseriesPanelBuilder(title="X").build()`
- **THEN** the returned dict has at least `type == "timeseries"`, `title == "X"`, and `gridPos`

#### Scenario: Stat panel renders required keys

- **WHEN** a developer builds a panel with `StatPanelBuilder(title="X").build()`
- **THEN** the returned dict has `type == "stat"` and `title == "X"`

### Requirement: `build_envelope` produces the unified-API envelope shape

The module SHALL expose a free function:

```python
def build_envelope(
    dashboard: dict[str, object] | object,
    *,
    name: str,
    message: str | None = None,
    folder_uid: str | None = None,
) -> dict[str, object]
```

Behaviour:

- If `dashboard` is a dict, use it directly as the spec block.
- Otherwise, expect a `.build()` method on `dashboard` that returns a
  Foundation SDK model; the SDK's model is normalised into a plain
  dict via `.to_json()` (camelCase keys, enums to `.value`, nested
  models to dicts).
- Build the unified-API envelope:
  `{ metadata: { name, annotations: { grafana.app/folder: <uid> when folder_uid, grafana.app/message: <message> when message } }, spec: <dashboard JSON> }`.
- Raise `ValueError` when `dashboard` is neither a dict nor has a
  `.build()` method.

#### Scenario: `DashboardBuilder` flows through `build_envelope`

- **WHEN** `build_envelope(DashboardBuilder(title="X"), name="x")` is called
- **THEN** the result has `metadata.name == "x"` and `spec.title == "X"`

#### Scenario: Raw dict also flows through

- **WHEN** `build_envelope({"title": "Y"}, name="y", message="hi")` is called
- **THEN** the result has `metadata.name == "y"`, `spec.title == "Y"`, and `metadata.annotations["grafana.app/message"] == "hi"`

#### Scenario: Folder annotation is added when provided

- **WHEN** `build_envelope(d, name="x", folder_uid="f1")` is called
- **THEN** the envelope has `metadata.annotations["grafana.app/folder"] == "f1"`

#### Scenario: Unknown input type raises `ValueError`

- **WHEN** `build_envelope(42, name="x")` is called
- **THEN** a `ValueError` is raised and no envelope is returned

### Requirement: `core-grafana-builder` is layered below business code

The `xtrade.core.grafana.builder` module SHALL NOT import from
`xtrade.strategy`, `xtrade.execution`, `xtrade.engine`, `xtrade.data`,
or `xtrade.risk`, nor from any `mos.*` module. It MAY import from
`xtrade.core.grafana.errors` for typed errors and from third-party
packages (`grafana-foundation-sdk`, stdlib).

#### Scenario: Static layering check

- **WHEN** `tests/core/test_grafana_builder.py` inspects the source of `xtrade/core/grafana/builder.py`
- **THEN** the file contains no `xtrade.strategy`, `xtrade.execution`, `xtrade.engine`, `xtrade.data`, `xtrade.risk`, or `mos.` imports

### Requirement: Tests do not require a running Grafana

The `tests/core/test_grafana_builder.py` module SHALL cover:

- `DashboardBuilder.build()` returns a dict with the right keys.
- `TimeseriesPanelBuilder` / `StatPanelBuilder` return typed panel dicts.
- `build_envelope` accepts `DashboardBuilder`, raw dict, and rejects
  unsupported types with `ValueError`.
- The layering invariant above.

Tests SHALL construct the Foundation SDK builders and inspect their
output without any network IO.

#### Scenario: All builder tests pass with no network

- **WHEN** `uv run pytest tests/core/test_grafana_builder.py -q` is run on a machine without Grafana
- **THEN** the suite passes and no socket connect attempt is made