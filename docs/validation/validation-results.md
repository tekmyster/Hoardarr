# Hoardarr validation results

Validated on 2026-08-22 against Hoardarr 0.3.11 Beta 1. Windows development
checks were followed by repository-controlled Ubuntu 24.04/Python 3.12/systemd,
loop-device and QEMU execution. Enterprise telemetry evidence is detailed in
[telemetry-validation.md](telemetry-validation.md).

## Current product classification

- **CORE STORAGE LIFECYCLE: SOFTWARE-READY**
- **FULL P1 PRODUCT SCOPE: IN PROGRESS**
- **PHYSICAL HARDWARE CERTIFICATION: PENDING**

The complete Storage Group and drain lifecycle remains accepted. The current
increment advances the remaining P1 expansion, onboarding/replacement, SMART,
and topology dependencies without representing fixture, disposable-loop, or
beta-bench observation as physical controller or drive certification.

## P1 dependency continuation — commit `66246ac27447`

- The expansion planner now emits immutable, capability-aware independent disk,
  mergerFS, SnapRAID, ZFS, download-tier, reserve/spare, and exact Linux MD
  RAID1/5/6/10 candidates. Guided review explains capacity, protection, and
  work in plain language; Advanced review binds the exact array level, member
  count, target, disk identities, and hardware snapshot hash.
- Browser execution proves a data-bearing disk follows the non-destructive
  import path without partition/filesystem creation, and a degraded Linux MD
  member follows the provider-aware replacement path with the exact array UUID,
  stable replacement identity, destructive consent, and durable Activity result.
- SMART short and extended self-tests are explicit Storage controls backed by
  the existing immutable durable operation. Unsupported or unreported
  passthrough disables execution rather than reporting a false failure; completed
  results remain visible in Activity.
- Sanitized SAS2308/SAS3008 and DS424IOM6/DS224IOM6 evidence now has an executed
  browser regression for the full controller/PHY/expander/enclosure/bay/path/
  drive/logical-storage chain. A real nested-grid clipping defect that hid deep
  topology nodes was corrected.
- GitHub Actions CI run `32718177066` passed all six jobs at the immutable commit:
  542 backend tests, 162 frontend tests, 4 accessibility tests, 33 Chromium E2E
  tests, 74 installer tests, and 19 release tests, plus Ruff, production builds,
  wheel/sdist, release/systemd, installed-appliance, and live-MinIO validation.
  Storage integration run `32718177058` passed the Storage Group drain lifecycle,
  four-device mergerFS persistent telemetry, extended storage stacks, and
  controller-redundancy lifecycle jobs.
- Release archive
  `hoardarr-0.3.11-66246ac27447-ubuntu24.04-amd64-cp312.tar.gz` has SHA-256
  `7e764ecb5af30dc9947df40b19f12839df5ea1cd7f134e9f5424e8820dd27536`.
  That exact artifact is installed at
  `/usr/lib/hoardarr/releases/0.3.11-66246ac27447` on the beta bench. All five
  actual Hoardarr services and both local/public readiness checks passed.
- Live browser inspection at `http://10.81.200.250:5173/` confirmed the new
  expansion and topology surfaces against the real persisted bench state: no
  managed Storage Group or pool, four Cisco SSDs classified as existing-data
  drives, honest Not reported health/bay/temperature values, idle real telemetry,
  and disabled SMART execution because the provider reported no supported
  passthrough. No saved draft was applied and no physical drive was mutated.

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
| CI: backend | `32589920251` | 337 passed; Ruff, Linux telemetry, 24,000-observation memory soak and wheel/sdist passed |
| CI: frontend | `32589920251` | 107 component/unit, 3 accessibility and 9 Playwright tests passed; TypeScript/Vite production build passed |
| CI: Ubuntu installer | `32589920251` | 72 bootstrap tests, shell syntax and hardware detector passed |
| CI: release bundle/systemd | `32589920251` | Bundle, 19 release tests, archive/checksum, clean wheel install and every systemd unit passed |
| CI: installed appliance | `32589920251` | Signed 0.3.11 bundle applied, services started, database migrated, ownership/modes and web health passed, identical reapply passed |
| Four-device mergerFS telemetry | `32589920257` | Purpose-created loop workload, persistence, reconnect, collector restart, rollups and cleanup passed |
| Extended storage stacks | `32581789533` | Hosted Ubuntu purpose-created loops passed ext4/POSIX ACL, MD RAID6/XFS, ZFS RAIDZ2/snapshot/scrub and SnapRAID sync/status/diff/check |
| SnapRAID failed-data replacement | `32663073443` | Hosted Ubuntu production executor partitioned and formatted a purpose-created replacement loop through a persistent by-id alias, then completed status, targeted fix, audit-only check, sync, independent recovered-file SHA-256 verification, current parity, and a succeeded durable journal |
| Controller redundancy lifecycle | `32589920257` | A disposable LIO LUN started on one iSCSI path, converted to two-path DM-Multipath, replaced one controller path, survived verified continuous I/O failover/recovery on both paths, restarted multipathd, then returned to one direct path |
| Appliance ISO/QEMU | `32589920245` | Pinned Ubuntu 24.04.4 ISO rebuilt and reached the QEMU installer/autoinstall checkpoint |

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
tests/integration/run-multipath-redundancy-lifecycle.sh
HOARDARR_EXTENDED_STORAGE_TESTS=1 tests/integration/run-loop-device-tests.sh
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
Targeted release-gate run `32581789533` subsequently exercised ext4 ACLs, MD
RAID6, ZFS RAIDZ2 and SnapRAID against newly created loop devices on hosted
Ubuntu and cleaned them through the purpose-bound harness.

Controller lifecycle run `32589493527` and final reconfirmation `32589920257`
exercised Hoardarr's real planner and privileged executor against one purpose-created LIO LUN presented
through three loopback iSCSI portals. The original storage entity UUID
`577d61b3-3c67-4c00-a2ae-5f63b5e6aa18`, filesystem UUID
`d4d53536-a6ae-4e73-9a48-b0c8ebf14264`, and public mount path remained
unchanged through add, replacement, failover, recovery, multipathd restart and
redundancy removal. File hashes remained valid, `fio` completed through both
path failures, and no format command ran after Day 1 creation.

An intervening adversarial rerun exposed a transient Device Mapper busy result
while returning from two paths to one. Hoardarr now settles udev, performs a
bounded journaled retry, and restores the existing mapper mount if the map
cannot be released. Focused tests exercise retry and rollback; final run
`32589920257` passed the complete lifecycle after that correction.

The control-plane recovery increment at immutable commit `aea2063d4cb7` passed
both clean attempts of CI run `32691347484`. Each attempt completed all six
jobs: 539 backend tests, 162 frontend tests, 4 accessibility tests, 26 Chromium
E2E tests, 74 bootstrap tests and 19 release tests, plus Ruff, the bounded
telemetry soak, real read-only Linux telemetry collection, wheel and release
bundle construction, systemd verification, and live disposable MinIO. The
installed-appliance job created a source owner, produced an encrypted console
export, stopped all Hoardarr services, restored to a separate fresh root, ran
migrations, proved that the source owner and setup token did not transfer,
created a fresh owner, restarted services and passed readiness. A defect found
by the first pre-final attempt (`sqlite3.OperationalError: invalid uri
authority: tmp`) was corrected by canonical absolute/encoded read-only SQLite
URIs and a valid POSIX database URL; focused recovery tests then passed 17/17.

Release archive
`hoardarr-0.3.11-aea2063d4cb7-ubuntu24.04-amd64-cp312.tar.gz` has SHA-256
`29ddc0cfd4cea76d4d4aa3c187e57846bf60e8da49d9e3629059138adb01ce30`.
That exact artifact is installed at
`/usr/lib/hoardarr/releases/0.3.11-aea2063d4cb7` on the visible beta bench. All
five Hoardarr services and readiness passed. Browser inspection confirmed the
new recovery guidance, no managed Storage Groups or pools, four Cisco SSDs with
honest Not reported health, idle real telemetry, and an unapplied saved draft;
no physical storage was mutated.

Two-node graph-stress run `32599605672` booted two Ubuntu 24.04 QEMU nodes,
installed Hoardarr 0.3.11 as systemd services, attached two local virtual SSDs
to each node and presented one shared LUN over two controller paths to both.
The production durable scan populated per-drive telemetry. Node A failed and
recovered a path under fio, the API was stopped while the worker continued
persisting, and shared ext4 ownership then moved to Node B without changing the
WWID, filesystem UUID, mapper, mount or SHA-256. Node B restarted its worker
and resumed collection.

That run also found a release-visible defect: the 24-hour graph request selected
an empty hourly rollup on a fresh installation even though bounded raw samples
existed. Automatic resolution now uses the actual raw sample population while
it fits the point budget. The focused regression added to
`test_enterprise_telemetry.py` passed, as did the complete local suite (340
backend passed, 3 Windows-only skipped; 110 frontend passed; 4 accessibility
passed). The corrected CI screenshots contained real plotted series (87 and 92
commands maximum), and 80-cycle per-node browser heap growth after warm-up was
813,699 and 829,612 bytes. The two workers' RSS increased by 4,348 and 6,356
KiB while their high-water marks remained unchanged.

The final expanded scenario additionally fails and restores the second path,
restarts multipathd during open-file IO and records a bounded 95%-read mixed
phase. Its machine-readable summary performs phase-aligned fio/Linux versus
Hoardarr comparisons and rejects any phase for which Hoardarr records no
activity. Final run IDs and artifact digest are recorded in the completion
report.

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

Current classification:

- **CORE STORAGE LIFECYCLE: SOFTWARE-READY**
- **FULL P1 PRODUCT SCOPE: IN PROGRESS**
- **PHYSICAL HARDWARE CERTIFICATION: PENDING**

If project policy makes the managed Deep Security Scan mandatory, that formal
release gate remains pending separately.
