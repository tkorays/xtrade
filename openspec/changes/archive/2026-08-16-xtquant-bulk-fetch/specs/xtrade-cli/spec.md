## MODIFIED Requirements

### Requirement: `xtrade data sync` flags

`xtrade data sync` SHALL accept:
- `--interval {1d, 1m}` (required)
- `--batch-size N` (default varies by `--interval`: `1d` → `len(instruments)`, `1m` → `50`)
- `--batch-size-max N` (default `500`; rejects values exceeding it)
- `--lookback-days N` (default `5`, clamped to `[1, 30]`)
- `--dry-run`, `--start-date`, `--end-date`, `--verbose` (unchanged)

#### Scenario: Default `1d` batch_size is the whole market

- **WHEN** the operator runs `xtrade data sync --interval 1d` (no `--batch-size`)
- **THEN** the CLI defaults `--batch-size` to `len(instruments)` so a single bulk call covers all symbols; the value is logged in the run-start INFO line as `batch_size=<N>`

#### Scenario: Default `1m` batch_size is 50

- **WHEN** the operator runs `xtrade data sync --interval 1m` (no `--batch-size`)
- **THEN** the CLI defaults `--batch-size` to `50`

#### Scenario: `--batch-size-max` rejects too-large values

- **WHEN** the operator runs `xtrade data sync --interval 1d --batch-size 1000 --batch-size-max 500`
- **THEN** the CLI exits non-zero with `--batch-size must be <= 500; got 1000`