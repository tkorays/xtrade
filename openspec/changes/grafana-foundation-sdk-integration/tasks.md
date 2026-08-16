## 1. Dependency + Foundation SDK sanity

- [x] 1.1 In `pyproject.toml`, add `grafana-foundation-sdk>=0.0.18,<1.0` to `[project.dependencies]`. Run `uv sync` so the lockfile picks it up.
- [x] 1.2 Confirm `uv run python -c "from grafana_foundation_sdk.builders.dashboard import Dashboard; print(Dashboard('T').build().to_json())"` succeeds — verifies the SDK import path, that `.build()` returns a model with `.to_json()`, and that the returned dict has camelCase keys (`schemaVersion`, `gridPos`, ...).

## 2. `xtrade.core.grafana.builder` module

- [x] 2.1 Create `src/xtrade/core/grafana/builder.py` with the typed wrappers: `DashboardBuilder`, `TimeseriesPanelBuilder`, `StatPanelBuilder`, plus `build_envelope(dashboard, *, name, message=None, folder_uid=None) -> dict`.
- [x] 2.2 `DashboardBuilder` wraps `grafana_foundation_sdk.dashboard.Builder`. Constructor accepts `title`, `uid=None`, `tags=None`, `timezone=None`, `schema_version=41`. Exposes `with_panel(panel_builder)` and `with_row(row_builder)` returning `self`; `build()` calls the underlying builder and returns the dict.
- [x] 2.3 `TimeseriesPanelBuilder` and `StatPanelBuilder` wrap the corresponding Foundation SDK panel builders. Expose `.build() -> dict`.
- [x] 2.4 `build_envelope` duck-types `dashboard` — when it has a `.build()` callable, call it; when it's a `dict`, use it directly; otherwise raise `ValueError`. Build the unified-API envelope: `metadata.name` from `name`, `metadata.annotations["grafana.app/folder"]` only when `folder_uid`, `metadata.annotations["grafana.app/message"]` only when `message`.

## 3. `DashboardsAPI.create` accepts builders

- [x] 3.1 In `src/xtrade/core/grafana/dashboards.py`, replace the `create` body so that:
  - if `payload` is a dict, behave exactly as today (validate `metadata.name` + `spec`, build envelope, POST).
  - if `payload` is not a dict, call `payload.build() -> dict` to get the dashboard JSON, then build the envelope using the same validation as the dict path (`metadata.name` is taken from the `name` kwarg; raises `ValueError` when the result has no `metadata.name` after validation).
  - if `payload` is neither a dict nor has `.build()`, raise `ValueError`.

## 4. `PanelsAPI.update_panel` accepts builders

- [x] 4.1 Add an optional `panel_builder: object | None = None` kwarg to `update_panel`. When supplied, call `.build() -> dict` and merge the resulting dict's top-level keys into the existing panel. Existing kwargs (`title`, `description`, `targets`, `options`, `field_config`, `transparent`) take precedence — when both a kwarg and a builder key would set the same field, the kwarg wins.
- [x] 4.2 Raise `ValueError` when `panel_builder` is neither `None` nor has a callable `.build()`.

## 5. Public re-exports

- [x] 5.1 In `src/xtrade/core/grafana/__init__.py`, add `DashboardBuilder`, `TimeseriesPanelBuilder`, `StatPanelBuilder`, `build_envelope` to the imports and `__all__`. Keep existing symbols exported unchanged.

## 6. Tests

- [x] 6.1 Create `tests/core/test_grafana_builder.py`. Cover:
  - `DashboardBuilder(title="X", tags=["a"]).build()` returns a dict having `title == "X"` and `tags == ["a"]`.
  - `DashboardBuilder(title="X").build()` defaults `schemaVersion == 41`.
  - `TimeseriesPanelBuilder(title="X").build()` returns `{ "type": "timeseries", "title": "X", "gridPos": {...}, ... }`.
  - `StatPanelBuilder(title="X").build()` returns `{ "type": "stat", "title": "X", ... }`.
  - `build_envelope(DashboardBuilder(title="X"), name="x")` returns an envelope with `metadata.name == "x"` and `spec.title == "X"`.
  - `build_envelope({"title": "Y"}, name="y", message="hi")` returns an envelope with `metadata.annotations["grafana.app/message"] == "hi"`.
  - `build_envelope(d, name="x", folder_uid="f1")` adds `metadata.annotations["grafana.app/folder"] == "f1"`.
  - `build_envelope(42, name="x")` raises `ValueError`.
  - Static layering check: no business-layer / `mos.*` imports in `xtrade/core/grafana/builder.py`.
- [x] 6.2 Add to `tests/core/test_grafana_client.py`:
  - `test_create_accepts_builder`: register a `POST .../dashboards` route, call `client.dashboards.create(DashboardBuilder(title="NEW"), name="new", message="hi")`, assert request body has `metadata.name == "new"` and `spec.title == "NEW"`.
  - `test_create_rejects_unsupported_payload`: call `client.dashboards.create(42, name="x")`, assert `ValueError` and no HTTP request is made.
  - `test_update_panel_accepts_builder`: register GET + PUT routes; call `panels.update_panel("abc", 4, panel_builder=TimeseriesPanelBuilder(title="X"))`; assert the persisted panel's `title == "X"` and the other fields are unchanged.
  - `test_update_panel_explicit_kwarg_wins_over_builder`: assert `panels.update_panel(..., title="override", panel_builder=TimeseriesPanelBuilder(title="builder"))` results in `title == "override"`.

## 7. Validation

- [ ] 7.1 `uv run pytest tests/core/test_grafana_builder.py tests/core/test_grafana_client.py tests/test_config.py -q` passes (new + existing tests stay green).
- [ ] 7.2 `uv run pytest -q` passes (full suite, no regressions).
- [ ] 7.3 `uv run ruff check src tests` clean.
- [ ] 7.4 `uv run ruff format --check src tests` clean.
- [ ] 7.5 `uv run mypy src` strict mode clean.
- [ ] 7.6 `openspec validate --all --strict` clean (this change + the two previous Grafana changes still validate).