## MODIFIED Requirements

### Requirement: `xtrade data sync --batch-size-max` defaults

`xtrade data sync --batch-size-max N` SHALL default to a value that depends on `--interval`:
- `--interval 1d` → no upper bound (the max check is bypassed); the operator's `--batch-size` is accepted up to `len(instruments)`.
- `--interval 1m` → `500`.

The `--batch-size-max` flag itself stays; the operator MAY override the resolved default with an explicit value (including a large value for `1m` runs, at their own risk). The resolved max is shown in the run-start INFO line as `batch_size_max=<value or "(unbounded)">`.

#### Scenario: `1d` default accepts the whole market

- **WHEN** the operator runs `xtrade data sync --interval 1d` against 7000 instruments
- **THEN** the CLI resolves `--batch-size` to `len(instruments) = 7000`; no max-rejection error fires; the run proceeds with one bulk fetch covering all 7000 symbols

#### Scenario: `1d` accepts an explicit batch-size above 500

- **WHEN** the operator runs `xtrade data sync --interval 1d --batch-size 6000`
- **THEN** the CLI accepts it (no max-rejection error); one bulk fetch covers the 6000 symbols

#### Scenario: `1m` default still caps at 500

- **WHEN** the operator runs `xtrade data sync --interval 1m --batch-size 1000` (no `--batch-size-max`)
- **THEN** the CLI rejects with `--batch-size must be <= 500 (--batch-size-max); got 1000`

#### Scenario: `1m` accepts an explicit `--batch-size-max` raise

- **WHEN** the operator runs `xtrade data sync --interval 1m --batch-size 800 --batch-size-max 1000`
- **THEN** the CLI accepts it; one bulk fetch is issued for the 800 symbols

#### Scenario: `1d` with an explicit `--batch-size-max` is honoured

- **WHEN** the operator runs `xtrade data sync --interval 1d --batch-size 600 --batch-size-max 500`
- **THEN** the CLI rejects with `--batch-size must be <= 500 (--batch-size-max); got 600` (the explicit max still wins for `1d`)