# Hoardarr validation results

Validated on 2026-08-22 against Hoardarr 0.3.10 Beta 1. Windows development
checks were followed by repository-controlled Ubuntu 24.04/Python 3.12/systemd,
loop-device and QEMU execution. Enterprise telemetry evidence is detailed in
[telemetry-validation.md](telemetry-validation.md).

## Completed

- All traceability rows that were `NOT IMPLEMENTED` now have production
  implementations and software evidence; the remaining gates are physical
  provider certification, production signing-key provisioning and the managed
  Deep Security Scan.
- The locked backend, frontend, API, migration, browser, accessibility,
  retention, analytics, entitlement, update, add-on, installer, systemd,
  storage-safety and packaging suites pass.
- Ubuntu CI builds and installs the complete versioned release, activates its
  services, migrates a fresh database, verifies file ownership/modes and web
  health, and reapplies the same signed bundle idempotently.
- Four purpose-created loop-backed devices pass the mergerFS persistent
  telemetry workload; no physical Cisco SSD was modified.
- The appliance image builds from the pinned Ubuntu 24.04.4 ISO and boots in
  QEMU/TCG to the controlled installer/autoinstall checkpoint.

## Failed and corrected

The principal release defects found during execution were corrected rather than
waived: durable cancellation/recovery journal disagreement, incomplete
system-device ancestry, connectivity path trust, secret-bearing networking
plans, browser wizard recovery, updater/install relocation, release-state
ownership and mode assertions, installed frontend permissions, Linux workflow
path assumptions, and a POSIX transfer fixture that did not reach its intended
race. Security review additionally found and corrected add-on traversal and
unit injection, mergerFS/fstab injection, block-backing and tier-transfer
TOCTOU, metric-entitlement bypass, anomaly-query amplification and unrestricted
remote ARR mutation fields. The final transfer publication uses an atomic
no-clobber link after descriptor-relative inode validation.

## Final Ubuntu workflow evidence

| Workflow / job | Run | Result |
|---|---:|---|
| CI: backend | `32580574074` | 315 passed; Ruff, Linux telemetry, 24,000-observation memory soak, wheel/sdist, 72 bootstrap and 19 release tests passed |
| CI: frontend | `32580574074` | 100 component/unit, 3 accessibility and 8 Playwright tests passed; TypeScript/Vite production build passed |
| CI: Ubuntu installer | `32580574074` | 72 bootstrap tests, shell syntax and hardware detector passed |
| CI: release bundle/systemd | `32580574074` | Bundle, 19 release tests, archive/checksum, clean wheel install and every systemd unit passed |
| CI: installed appliance | `32580574074` | Signed bundle applied, services started, database migrated, ownership/modes and web health passed, identical reapply passed |
| Four-device mergerFS telemetry | `32580574075` | Purpose-created loop workload, persistence, reconnect, collector restart, rollups and cleanup passed |
| Appliance ISO/QEMU | `32580574069` | Pinned ISO build and QEMU installer-checkpoint smoke test |

The conditional `disposable-block-devices` job was skipped because its separate
self-hosted profile was not requested; the ordinary GitHub-hosted
`mergerfs-persistent-telemetry` job performed the required four-device
privileged loop execution. The installed-appliance diagnostics step was skipped
by design because the preceding install step succeeded; it is a failure-only
diagnostic and not an unexecuted validation requirement.

## Exact primary validation commands

```text
uv sync --project backend --locked --all-groups
uv run --project backend --locked ruff check backend/src backend/tests scripts/benchmark-telemetry.py scripts/soak-telemetry.py tests/integration/mergerfs_persistent_telemetry.py
uv run --project backend --locked pytest backend/tests -q
uv run --project backend --locked python scripts/soak-telemetry.py --drives 60 --cycles 100
uv run --project backend --locked python tests/integration/verify-linux-telemetry.py
uv build --project backend
python -m unittest discover -s tests/bootstrap -p 'test_*.py'
python -m unittest discover -s tests/release -p 'test_*.py'
npm ci --prefix frontend
npm test --prefix frontend -- --run
npm run test:a11y --prefix frontend
npm run build --prefix frontend
npm run test:e2e --prefix frontend
python3 scripts/build-release-bundle.py build --output-dir dist/releases
python3.12 -m unittest discover -s tests/release -p 'test_*.py'
scripts/install-release-bundle.sh --apply <verified-plan>
tests/integration/run-mergerfs-telemetry-workload.sh
scripts/build-appliance.sh ubuntu.iso <pinned-sha256> dist/hoardarr-release.tar.gz hoardarr.iso
qemu-system-x86_64 -accel tcg ...
```

All listed CI steps exited 0. Pytest emitted one upstream
Starlette/httpx deprecation warning. GitHub Actions warned that Node 20 support
in actions is being retired while executing those actions with Node 24.

## First clean validation pass

The earlier clean implementation pass recreated locked dependencies, test
databases and production outputs. It passed 288 backend tests, 99 frontend
tests, 3 accessibility tests, 8 browser tests, 72 bootstrap tests and 19 release
tests, plus production builds, migrations, audits, telemetry scale and
120,000-observation memory validation. Ubuntu runs `32578060020` and
`32578060013` then proved the four-loop workload and QEMU appliance boot after
the first Linux workflow corrections.

## Second adversarial validation pass

The final code SHA reran the complete Ubuntu workflow set from clean runners.
Run `32580574074` passed all five CI jobs, including 315 backend, 100 frontend,
3 accessibility, 8 browser, 72 bootstrap and 19 release tests. Run
`32580574075` independently recreated and destroyed four loop devices and passed
the persistent workload. Run `32580574069` rebuilt the release and appliance and
repeated the QEMU checkpoint. The two passes agree on the corrected code paths.

## Security validation

- Managed repository-wide Deep Security Scan: **BLOCKED BY EXECUTION
  ENVIRONMENT**. Exact error:
  `Deep Scan cannot safely start a read-only worker: the parent must provide a managed filesystem permission profile.`
  Retry was prohibited by that workflow, no substitute worker was launched and
  no result was inferred.
- Standard scan `b2a348a2-ad01-428f-b51a-4568d42bbd13`, pinned revision
  `99dcc6b1290aea549d9b815f514123c175e1f170`: five high, three medium and one
  low finding. The eight actionable findings were corrected. The low root
  process blast-radius finding is an explicit accepted product risk.
- Differential scan `e09ff547-3021-49a9-ab4e-57447f62a593`: one medium
  destination-publication race, corrected and verified by a Linux regression
  test.
- `npm audit --omit=dev --audit-level=low`: exit 0, zero known runtime
  vulnerabilities.
- `pip-audit`: exit 0, zero known runtime vulnerabilities.
- Bandit source scan: exit 1, zero high, four reviewed medium and 38 low findings
  across 25,738 lines. Medium items are the group-scoped executor sockets and
  intentional fixed service binds.
- `detect-secrets`: exit 0; candidates were reviewed as schema field names,
  fixture hashes, vendor checksums or dependency-lock hashes. This is not
  represented as zero candidate text.
- Production-source searches found no `shell=True`, `os.system`, unsafe HTML
  injection sink, `eval`, unsafe pickle load or unsafe YAML load.

The fallback evidence is not equivalent to the blocked managed Deep Scan and
does not erase the accepted root-process risk.

## Environment and hardware limits

No matching physical storage controller, shelf, SAS PHY, multipath fabric,
Fibre Channel/FCoE fabric, physical ZFS/MD/SnapRAID deployment or the four Cisco
SSDs was exercised. Provider fixtures, capability gates, QEMU and loop devices
are software/isolated evidence only. Production signing requires the release
owner's public trust root and offline private key; no production private key is
embedded. No public production release was created.

Final classification: **SOFTWARE-READY; PHYSICAL HARDWARE CERTIFICATION
PENDING**. If project policy makes the managed Deep Security Scan mandatory,
that formal release gate remains pending separately.
