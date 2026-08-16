## MODIFIED Requirements

### Requirement: `SourceRegistry` default registration

The `SourceRegistry` SHALL default-register two `DataSource` implementations on first instantiation:
- `"mock"` — the existing `InMemoryMockSource`.
- `"xtquant"` — a new `XtQuantDataSource`, registered **lazily**: the registry SHALL attempt `import xtquant.xtdata` and SHALL skip the registration silently (without raising) when the import fails.

After both registrations succeed, `SourceRegistry().names()` SHALL include both names. After a failed `xtquant` import, `SourceRegistry().names()` SHALL include only `"mock"` (and any additional sources the caller has registered).

#### Scenario: Both defaults registered when xtquant is importable

- **WHEN** a developer imports `xtrade.data.sources` on a machine where `import xtquant` succeeds
- **THEN** `SourceRegistry().get("xtquant")` returns an `XtQuantDataSource` and `SourceRegistry().get("mock")` returns an `InMemoryMockSource`; `SourceRegistry().names()` contains both names

#### Scenario: `xtquant` is absent when the package is missing

- **WHEN** a developer imports `xtrade.data.sources` on a machine where `import xtquant` raises `ModuleNotFoundError`
- **THEN** `SourceRegistry().get("xtquant")` raises `KeyError` listing the known sources (which include `"mock"` but exclude `"xtquant"`); `SourceRegistry` itself does not re-raise the `ModuleNotFoundError`

#### Scenario: `xtquant` is re-registered after a successful install

- **WHEN** `xtquant` is not installed at first import, then installed, then `SourceRegistry().reset()` is called and the registry is reconstructed
- **THEN** the new `SourceRegistry().get("xtquant")` returns an `XtQuantDataSource` (the lazy import succeeds on the new construction)