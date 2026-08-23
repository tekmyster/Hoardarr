# Product reconciliation implementation evidence — 2026-08-23

## Scope

This batch expanded the existing 120-row roadmap without renumbering it and implemented the first
safe Storage Group lifecycle slice. It did not implement or claim the drain engine.

## Roadmap

- Previous rows: 120
- Reconciliation rows added: 100
- Release workflow notification row added after operator report: 1 (`RC-05`)
- Total: 221

## Storage lifecycle evidence

Migration `0010_storage_groups` adds Storage Groups, the physical disk registry, backends, and
ordered lifecycle events. Hardware scans reconcile only detector records carrying a stable identity;
kernel-path changes update the same disk row. Database constraints prevent the same physical disk or
logical storage object being assigned twice and prevent more than one preferred-write backend per
group.

Implemented UI behavior includes loading/error/empty states, group creation, stable namespace,
registered-disk assignment, activation, preferred-write selection, backend state, and recent
lifecycle events. Drain/retire remains unavailable until its durable operation exists.

## GitHub Actions notification incident

The operator-reported `Two-node storage graph stress` failure is run
`32604164475` on commit `c954cc1d1ce95229a1b7adf71b22ea96c885d807`. The storage workload,
path-failure, recovery, multipath restart, API disconnect, and node handoff phases ran; the job failed
when Playwright waited 60 seconds for the Overview heading. Its corrected successor,
`32604639824`, passed on commit `92b14ac15b564b986c418305b2444c1faf5cd5e1`, including evidence
validation and artifact upload.

The expensive workflow previously ran on every push and cancelled superseded runs. It is now a
deliberate `workflow_dispatch` release gate with `cancel-in-progress: false`. Normal pushes remain
covered by `ci.yml`. This avoids failure/cancellation email storms without weakening ordinary CI.
The changed workflow passes actionlint 1.7.7. It was not rerun because its last code-bearing run is
already green and this change affects trigger policy only.

## Executed validation

| Command | Result |
|---|---|
| `uv run --project backend ruff check backend/src/hoardarr ...` | Passed |
| focused lifecycle/API/migration tests | 11 passed |
| focused lifecycle/worker/API tests after scan reconciliation | 15 passed |
| `uv run --project backend pytest backend/tests -q --tb=short` | 347 passed, 3 skipped, 1 dependency deprecation warning |
| `npm test --prefix frontend -- --run` | 113 passed |
| `npm run build --prefix frontend` | Passed; 58 modules transformed |
| `npm run test:e2e --prefix frontend` | 10 Chromium browser tests passed; 200 navigation/range-change memory scenario passed |
| `uv build --project backend --wheel --out-dir dist/reconciliation-wheel` | Passed; `hoardarr-0.3.11-py3-none-any.whl` |
| `actionlint 1.7.7 .github/workflows/two-node-storage.yml` | Passed |

Wheel SHA-256:
`ED64AB43BB394768AC54E57D6C3738E2CC5D399557145A606D031B65339E2423`.

The three backend skips are Windows-host limitations for descriptor-relative POSIX operations and
POSIX ownership/mode enforcement. They were pre-existing and remain covered by Linux workflows.
