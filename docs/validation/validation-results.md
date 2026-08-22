# Hoardarr validation results

Enterprise KPI, retention, bounded-memory, scale, entitlement, and analytics
evidence is recorded in [telemetry-validation.md](telemetry-validation.md).

## Environment-blocked validation

### Managed Deep Security Scan

- Scan type: repository-wide Codex Security Deep Scan.
- Status: **BLOCKED BY EXECUTION ENVIRONMENT**.
- Exact error: `Deep Scan cannot safely start a read-only worker: the parent must provide a managed filesystem permission profile.`
- Retry status: the scan workflow explicitly prohibited retrying the same operation in
  that response, starting a replacement scan, or inferring a result.
- Result inferred: none.
- Residual limitation: fallback static, dependency, test, and manual security review
  is not equivalent to the blocked managed multi-pass scan.

### Managed Standard Security Scan fallback

- Scan type: Codex Security Standard Scan, ID
  `a609f228-6c2a-436c-b167-8e7b64705e23`.
- Status: preflight incomplete; preserved as resumable and no result inferred.
- Exact preflight constraints: delegated workers were unavailable to this task and
  the effective six-slot worker capacity could not be established.
- The scan was not advanced, completed, failed, or presented as a substitute for
  the blocked Deep Scan.

### Security checks that did execute

| Check | Exit | Result |
|---|---:|---|
| `npm audit --omit=dev --audit-level=low` | 0 | 0 known vulnerabilities |
| `uvx pip-audit -r requirements-audit.txt` | 0 | 0 known vulnerabilities in the locked runtime export |
| `uvx detect-secrets scan backend/src frontend/src scripts packaging` | 0 | 0 candidate source files |
| `uvx bandit -r backend/src scripts -f json` | 1 | 0 high, 16 medium, 43 low across 24,853 lines; medium items manually reviewed as fixed-bind, fixed-HTTPS, group-scoped Unix sockets, or intended executable/directory-mode warnings |
| targeted source searches | 0 | No `shell=True`, production `dangerouslySetInnerHTML`, or hard-coded success branch found; one unreachable UI placeholder branch was removed |

Manual review covered session and API-key separation, setup-token replay,
CSRF/origin enforcement, SSRF/DNS pinning, subprocess argument construction,
managed-path validation, secret redaction, immutable approval binding, operation
idempotency/reservation/recovery, executor target revalidation, and system-device
exclusion. This provides bounded evidence only; it is not equivalent to a
managed security scan.

## Baseline (before this task's edits)

| Command | Exit | Result |
|---|---:|---|
| `uv sync --all-groups --locked` | 0 | Locked backend environment installed; Hoardarr 0.3.10 |
| `npm ci --no-audit --no-fund` | 0 | 162 packages; npm warned about `esbuild` install script review |
| `python -m py_compile scripts/bootstrap.py scripts/detect-hardware.py scripts/build-release-bundle.py` | 0 | Passed |
| `uv run --locked ruff check src tests` | 0 | Passed |
| `uv run --locked pytest` | 0 | 132 passed, 0 failed, 0 skipped; 1 deprecation warning |
| `npm test` | 0 | 69 passed in 16 files, 0 failed, 0 skipped |
| `npm run build` | 0 | TypeScript checks and Vite production build passed |
| `python -m unittest discover -s tests/bootstrap -p 'test_*.py'` | 0 | 70 run: 69 passed, 1 skipped (POSIX-only) |
| `python -m unittest discover -s tests/release -p 'test_*.py'` | 0 | 16 run: 15 passed, 1 skipped (Ubuntu 24.04 release build) |

GNU Make was not installed, so the documented aggregate `make verify` command
could not be invoked on this Windows workstation. Its constituent locked commands
were run directly as shown above.

## Completed in this continuation

- all traceability rows previously marked `NOT IMPLEMENTED` now have production
  implementations and isolated evidence; the final count is zero;
- ZFS, Linux MD, SnapRAID, existing mergerFS expansion, mixed protected pools,
  tier migration, secure wipe and unusual-sector conversion now use typed,
  capability-gated plans and privileged execution boundaries;
- ARR-family writes, granular ACLs, normalized hardware-health providers, signed
  updates, the updater UI, trusted/local add-ons and appliance build automation
  are implemented and covered by focused tests;
- Applications onboarding now discovers integrations, preselects media/torrent/
  Usenet paths and carries user-edited recommendations into storage planning;
- the Advanced wizard exposes explicit filesystem, geometry, TRIM, ZFS, MD,
  SnapRAID, mixed-pool and mergerFS controls without adding them to Guided mode;
- successful storage work survives browser refresh by reconstructing the wizard
  from its durable operation and immutable plan;
- wizard completion is a separate, idempotent, audited backend transition after
  successful execution and one-time credential handling;
- generated credentials remain hidden, are not discarded on an unreliable
  clipboard report, and disappear only after explicit save confirmation;
- a missing storage-telemetry document now renders an honest empty state instead
  of throwing in Overview.

## Failed and corrected

| Discovered problem | Correction | Evidence |
|---|---|---|
| Running storage work could be marked cancelled even after the executor had completed | Reject running mutation cancellation and reconcile the journal's actual outcome | worker/API tests |
| Recovery treated every stale running operation alike | Query the durable executor status before stale recovery | restart/journal tests |
| Complex system-device ancestry was not fully included | Follow mounted partitions, swap and device-mapper slaves | detector tests |
| Connectivity accepted broader path strings than the privileged executor should consume | Enforce canonical safe absolute managed paths in service and executor | connectivity negative tests |
| Networking plan/status exposed SNMP communities and tool errors could echo them | Redact public fields, bind secret hashes into immutable plans, preserve unchanged secrets, sanitize errors | networking tests |
| Health and an unreachable fallback could display placeholder production content | Add live Health states and remove fallback placeholder | component and production-browser tests |
| Mixed-layout normalization rejected its own normalized empty ZFS auxiliary-device lists | Accept normalized empty special/cache/log lists and retain strict validation for populated lists | layout/policy/executor tests |
| A completed apply was durable in the backend but a browser reload returned to Overview | Recover the wizard, immutable plan, selected devices, settings and operation state during authenticated load | full browser reload E2E |
| Overview dereferenced a missing storage-performance document | Treat unavailable telemetry as `Not reported`/collecting state | Overview component test and browser run |
| First-run E2E omitted the newly required integrations collection | Add the real empty integration contract to the fixture rather than weakening product loading | full browser suite rerun |

## Remaining product defects

The traceability matrix records **0** requirements as `NOT IMPLEMENTED`. No known
software defect remains in the tested paths. Four requirements remain blocked by
the unavailable Ubuntu/systemd execution environment or managed security-scan
infrastructure, and four require matching physical hardware validation. Those
limits are recorded per row rather than represented as successful execution.

## First validation pass

The pass used locked dependency reinstalls, Pytest cache clearing, rebuilt
artifacts, fresh test databases and newly generated browser state. No prior test
result was reused.

| Command | Exit | Result |
|---|---:|---|
| `uv sync --all-groups --locked` | 0 | Fresh locked backend environment; 39 packages |
| `uv run --locked ruff check src tests` | 0 | Passed |
| `uv run --locked pytest --cache-clear -q` | 0 | 250 passed, 0 failed, 0 skipped; 1 upstream Starlette/httpx deprecation warning |
| `uv build --wheel` | 0 | `hoardarr-0.3.10-py3-none-any.whl` built |
| direct migration suite | 0 | Every retained schema revision and an empty database upgraded to Alembic head with preserved records |
| `npm ci --no-audit --no-fund` | 0 | Fresh locked frontend install; 166 packages; esbuild lifecycle-script review warning |
| `npm test` | 0 | 92 passed in 22 files, 0 failed, 0 skipped |
| `npm run test:a11y` | 0 | 3 accessibility tests passed across primary pages and critical UI |
| `npm run test:e2e` | 0 after correction | Initial run: 5 passed/1 fixture failure; corrected missing integration fixture; rerun: 6 passed/0 failed |
| `npm run build` | 0 | Both TypeScript checks and Vite production build passed |
| `npm audit --omit=dev --audit-level=low` | 0 | 0 known vulnerabilities |
| script `py_compile` | 0 | Bootstrap, detector, and release builder passed |
| bootstrap unittest discovery | 0 | 72 run: 71 passed, 1 POSIX-only skip |
| release unittest discovery | 0 | 19 run: 18 passed, 1 Ubuntu/systemd-only skip |
| release bundle `plan` | 0 | Deterministic Ubuntu 24.04/amd64/Python 3.12 plan for 0.3.10 |
| Oracle/LSI/NVMe fixture detector | 0 | Read-only fixture parse succeeded |
| `pip-audit` | 0 | No known runtime dependency vulnerabilities |
| source-only `detect-secrets` | 0 | No candidate files |
| critical migration/update/storage/ARR/ACL subset | 0 | 94 passed, 0 failed, 0 skipped |
| backend wheel installation/import smoke | 0 | Wheel installed into a fresh temporary environment; `hoardarr 0.3.10` and `create_app` imported |
| Bandit | 1 | No high findings; 16 medium and 43 low warnings manually reviewed |

The two skips are explained, not silently accepted: Windows cannot validate
POSIX mode preservation, and this workstation is not the required Ubuntu
24.04/systemd/Python 3.12 installer target.

## Second adversarial validation pass

The second pass reran locked installs, cache-cleared tests, production builds,
browser state, critical feature slices and security fallback checks. It was an
adversarial repeat, not an independent review.

| Command | Exit | Result |
|---|---:|---|
| locked backend sync, Ruff, and Pytest | 0 | 250 passed, 0 failed, 0 skipped; same single upstream deprecation warning |
| backend wheel build | 0 | Fresh 0.3.10 wheel built |
| direct migration/update/storage/ARR/ACL subset | 0 | 94 passed, 0 failed, 0 skipped |
| locked frontend install, tests, accessibility, production build and E2E | 0 | 92 unit/component, 3 accessibility and 6 browser tests passed; TypeScript and Vite passed |
| bootstrap and release suites | 0 | 72/71 passed/1 skip and 19/18 passed/1 skip; reasons unchanged |
| npm and Python dependency audits | 0 | No known vulnerabilities |
| source-only secret scan | 0 | No candidate files |
| Bandit | 1 | Same 0 high, 16 medium, 43 low reviewed warnings |
| dangerous source pattern rescan | 0 | 0 `shell=True`, `dangerouslySetInnerHTML`, or `os.system` matches in production source |

The two passes agree. No result from the blocked managed security scans was
inferred. Linux-only release installation, service restart, namespace storage,
and physical-hardware execution remain outside this Windows environment.

## Packaging and environment limits

`python scripts/build-release-bundle.py build --uv uv --npm npm` exited 2 and
failed closed with:

```text
error: incompatible build host:
- host platform is win32, expected linux
- host OS is unknown unknown, expected ubuntu 24.04
- host machine is AMD64, expected x86_64
- builder Python is 3.13, expected 3.12
```

The release plan, static package contracts, in-process atomic update/rollback
tests and repository Linux CI definitions passed. No Ubuntu bundle build,
installer boot, systemd start, or disposable loop-device execution was claimed
from this Windows host. `wsl.exe --list --quiet` returned no installed Linux
distribution; Docker, Podman and QEMU were unavailable.
