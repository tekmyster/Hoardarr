# Product reconciliation implementation evidence — 2026-08-23

## Scope

This batch expanded the existing 120-row roadmap without renumbering it and implements the Storage
Group lifecycle through the durable drain/move/verify/retire workflow. Physical-drive certification
remains separate; the Cisco SSDs on the beta bench were not modified.

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
lifecycle events. Immutable drain preflight is visible without implying movement. The internal
new-write exclusion boundary atomically marks a source `draining`, binds it to one operation and
plan digest, and selects a replacement preferred writer before copying.

Migration `0011_storage_drain_jobs` adds durable job and per-file manifest/checkpoint records. The
Linux mover uses descriptor-relative, no-follow traversal, stages to a temporary file, publishes
with an atomic no-replace hard link, verifies the selected mode, and deletes only the corresponding
verified source. Long file copies and hash passes refresh the worker heartbeat. Pause/resume and
stale-worker recovery preserve the manifest and phase. Completion transitions the source through
verifying and read-only to retired while retaining the Storage Group namespace.

The Storage Groups UI now includes exact approval, live phase/file/byte/rate progress, safe pause
and resume, needs-attention handling, and the durable completion report. It does not create sample
storage on the beta bench. GitHub Actions run `32649916042`, job
`storage-group-drain-lifecycle`, executed the purpose-created two-loop ext4 workflow successfully.
Artifact `storage-group-drain-lifecycle-evidence` records 4 files and 7,733,293 bytes, identical
before/after SHA-256 hashes, pause/resume, stale-worker recovery, source retirement, and unchanged
namespace `/srv/hoardarr/media`. Classification: **VERIFIED IN ISOLATION**.

The browser workflow exercises the production Storage page from immutable review through exact
approval, start, pause, resume, completion, source retirement, and stable namespace. Its screenshots
are uploaded by ordinary CI; no fake Storage Groups or storage telemetry are seeded on the beta
bench.

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

### Storage lifecycle increment (`2813c30dba1ac3f6882d2060f1e0e9786b81b7ac`)

| Evidence | Result |
|---|---|
| GitHub Actions CI `32649915997` | Passed |
| Appliance build `32649916013` | Passed |
| Isolated storage integration `32649916042` | Passed; all four storage jobs green |
| Backend suite | 353 passed, 5 skipped, 1 dependency warning |
| Frontend unit/component suite | 116 passed |
| Accessibility gate | 4 passed |
| Production frontend build | Passed; 58 modules transformed |
| Chromium E2E after terminal-state refresh correction | 11 passed, including complete lifecycle workflow |
