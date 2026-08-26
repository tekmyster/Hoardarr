# WO-APP-006-C1-F7 result

## Result

- **Explicit command handling: PASS.** Runtime receipt creation, initial durability sync, every bind, every propagation change, post-command mountinfo classification, receipt append/final sync, ordinary unmount, cleanup receipt append, and cleanup sync now branch on explicit status. Safety no longer depends on caller `errexit`.
- **No-mutation failure: PASS.** All five bind positions return fail-closed when the injected command fails before changing kernel state. No nonexistent mount is tracked and cleanup receipts contain only predecessors that were actually created.
- **Post-mutation nonzero failure: PASS.** All five bind positions and the propagation seam execute the real bounded mutation and then return nonzero. The exact observed destination/mount ID is reconciled, every created mount is removed, and the original nonzero status is retained.
- **Ordinary / OR-list / `set +e`: PASS.** The complete pre-mutation and post-mutation matrix passes under all three caller modes in one long-lived disposable private mount namespace.
- **Mount identity and propagation: PASS.** Preflight-empty destinations are classified from `/proc/self/mountinfo`. A valid exact ID/path is tracked before source/propagation validation. Parser failure, invalid/non-numeric ID, wrong parsed path, source mismatch, propagation-command failure, and post-propagation identity failure all fail closed.
- **Immediate ambiguous-state rollback: PASS.** If the just-attempted destination cannot be assigned a trustworthy ID/path, the bounded rollback checks the exact safe target path, uses one ordinary non-lazy/nonrecursive `umount`, verifies absence directly from mountinfo, and then cleans recorded predecessors. It cannot act on a pre-existing mount because the destination was authoritatively empty in the private namespace immediately before the attempt.
- **Reverse cleanup and receipt: PASS.** Receipts match the exact mounts actually created, in reverse order. No row is fabricated for a bind that made no mount or for an unclassified just-attempted mount. Repeated empty cleanup remains a non-truncating no-op.
- **Success/package-postinst path: PASS.** The executed Linux harness mounts exactly `/proc`, `/sys`, `/dev`, `/dev/pts`, and `/run`; a real disposable local package post-install reads all five interfaces and the kernel-hook-equivalent probe passes before exact cleanup.
- **Work/signal/cleanup failures: PASS.** Chroot-work failure, TERM, cleanup failure after success, and cleanup failure while preserving an existing status all remain visible and preserve the intended status semantics.
- **Containment/pre-existing state: PASS.** Parent namespace inspection finds no leaked mounts. Symlink, nondirectory, unexpected pre-existing mount, caller-sentinel spoofing, and later-index path drift are rejected while preserving pre-existing state.
- **Service/storage/offline invariants: PASS at source and automatic-build scope.** Signed local-only APT, disabled inherited sources, false network proxies, zero retries, 109 roots, both compatibility families, removal/downgrade rejection, service-start denial, mask/alias handling, final inactive checks, and MD/multipath/LVM activation guards are unchanged.
- **Focused/full local QA: PASS with platform boundary.** All 57 release tests pass locally with 3 expected Windows platform skips; Bash syntax, Ruff, format, compile, workflow YAML, package plan, and diff checks pass.
- **Automatic CI: PASS.** Run `32994209490` passes all seven jobs. The privileged lifecycle test executes successfully in both `release-bundle-systemd` and `backend`; each reports 57/57 release tests passing.
- **Automatic appliance build: PASS.** Run `32994209539`, build job `98259091771`, passes signed repository creation, release bundle, ISO construction, boot checkpoint, and artifact retention.
- **Manual-only offline-install: PASS (skipped as required).** Job `98262203266` is skipped with zero steps. No manual workflow was dispatched.
- **Artifact metadata: PASS.** Both automatic-build artifacts were read from the API without downloading either artifact.
- **Source scope: PASS.** The implementation commit changes only the production payload and two directly coupled test files. All inherited dirty/untracked work remains preserved.
- **C1: FAIL / pending separate gate.** F7 does not authorize a fresh ordinary two-pass installation.
- **OWNER-10: FAIL.** The separate LINSTOR/DRBD 9/DRBD Reactor/installed-but-disabled LINSTOR Gateway, kernel/Secure-Boot, and offline Proxmox-plugin sidecar requirements remain outstanding.

## Evidence

### Authority and repository identity

- Work-order SHA-256: `fa84771db0f548522fac6e53f595d8ca25a13d5443fa7d2e93087cec91684f23`.
- F6 handoff SHA-256: `fb27d1bda30ba4e546b1d07e98aed70731477e7b796280926086c5327d69dfcd`.
- Required and observed starting local/origin HEAD: `c3beb271119e5a9b3238b4c6539ada6d5ac8afe7`.
- Implementation commit: `650054bb948693ccdd81562ac57fdb0fb837e710` (`fix: fail closed on runtime mount mutations`).
- Implementation diff:
  - `packaging/appliance/install-offline-payload.sh`: modified.
  - `tests/appliance/test-target-chroot-runtime-mounts.sh`: modified; mode remains `100755`.
  - `tests/release/test_offline_appliance.py`: modified.
  - Total: 497 insertions, 98 deletions.

No package/root/family, repository-builder, workflow, timeout, QEMU, unattended-data, application, live-system, credential, website, cluster, or protected-media change was made.

### Root cause and correction

F6 CI run `32990166031` proved that a shell function called in an OR-list does not inherit the caller's expected `errexit` behavior: injected first-bind status `71` fell through and was treated as a created mount. F7 therefore treats command status and kernel state as separate facts.

For each bind attempt, F7 now:

1. Proves the exact target path is safe and empty from `/proc/self/mountinfo`.
2. Captures the bind command status without `set -e` dependence.
3. Reads authoritative post-command state even for nonzero status.
4. If there is no mount, reports no-mutation failure and cleans predecessors.
5. If an exact valid ID/path exists, tracks it immediately before later fallible identity/propagation work.
6. If ID/path classification is ambiguous or poisoned, uses the exact-destination private-namespace rollback and verifies absence before cleaning predecessors.
7. Treats nonzero-after-mutation as failure even if the expected mount exists.

The propagation command follows the same model: status and post-command state are captured independently, the same mount ID/source identity must remain, private propagation is verified, and a nonzero command status remains failure even when the kernel state changed.

### Executed mutation matrix

The Linux harness executes inside one child mount namespace created with `unshare --mount --propagation private`, then proves every target path remains absent in the parent namespace.

| Matrix | Cases | Required result |
|---|---:|---|
| bind positions × before/after mutation × ordinary/OR-list/`set +e` | 30 | nonzero; zero child/parent mounts; exact predecessor/current receipt rows |
| propagation before/after mutation × ordinary/OR-list/`set +e` | 6 | nonzero; exact one-mount cleanup receipt |
| post-bind classification seams | 7 | persistent record failure, persistent ID/path parse failure, invalid ID, wrong path, post-private record failure, and source mismatch all clean safely |
| later-index path drift | 1 | first predecessor cleaned through deliberate preparation failure path |
| receipt mutation seams | 5 | header/truncation, cleanup header, initial sync, append, and final sync failures are explicit and leave no mounts |

Additional retained cases cover successful five-path package configuration, reverse-order receipt/idempotence, work failure, TERM, cleanup failure, original-status preservation, symlink/nondirectory/pre-existing-mount rejection, namespace containment, and caller-controlled sentinel rejection.

### Local validation

```text
backend/.venv/Scripts/python.exe -m unittest discover -s tests/release -p 'test_*.py' -v
Result: Ran 57 tests in 43.468s; OK (skipped=3 platform-specific Linux gates).

backend/.venv/Scripts/ruff.exe check tests/release/test_offline_appliance.py
Result: PASS.

backend/.venv/Scripts/ruff.exe format --check tests/release/test_offline_appliance.py
Result: PASS.

backend/.venv/Scripts/python.exe -m compileall -q tests/release/test_offline_appliance.py
Result: PASS.

C:/Program Files/Git/bin/bash.exe -n packaging/appliance/install-offline-payload.sh tests/appliance/test-target-chroot-runtime-mounts.sh tests/appliance/test-local-file-apt-install.sh
Result: PASS.

uv run --with pyyaml==6.0.2 python -c <parse appliance.yml and ci.yml>
Result: workflow_yaml=valid.

backend/.venv/Scripts/python.exe scripts/build-offline-apt-repository.py plan
Result: PASS; existing package plan remains valid.

git diff --check -- <three authorized files>
Result: PASS; only the inherited Windows line-ending warning was emitted.
```

The privileged lifecycle test is deliberately skipped on Windows. Its two independent automatic Ubuntu executions below are the acceptance evidence.

### Automatic CI

Run `32994209490`:

- Event: `push`.
- Exact head: `650054bb948693ccdd81562ac57fdb0fb837e710`.
- Created: `2026-08-26T17:27:05Z`.
- Completed: `2026-08-26T17:29:54Z`.
- Conclusion: **success**.
- URL: `https://github.com/tekmyster/Hoardarr/actions/runs/32994209490`.

| Job | ID | Conclusion |
|---|---:|---|
| frontend | `98259091545` | success |
| installed-appliance-smoke | `98259091735` | success |
| minio-control-plane-backup | `98259091753` | success |
| central-fleet-postgres | `98259091783` | success |
| backend | `98259091837` | success |
| ubuntu-installer | `98259091884` | success |
| release-bundle-systemd | `98259091907` | success |

The two Linux release-suite executions provide independent privileged evidence:

```text
release-bundle-systemd: Ran 57 tests in 49.532s — OK
backend:                Ran 57 tests in 62.416s — OK
```

Because `test_target_runtime_mount_lifecycle_and_package_postinst` is enabled only on Linux and is part of both 57-test executions, these successful suites prove the complete executable matrix ran rather than being satisfied by Windows static assertions.

### Automatic appliance build and artifacts

Run `32994209539`:

- Event: `push`.
- Exact head: `650054bb948693ccdd81562ac57fdb0fb837e710`.
- Created: `2026-08-26T17:27:05Z`.
- Completed: `2026-08-26T17:36:10Z`.
- Conclusion: **success**.
- Build job `98259091771`: success (`17:27:09Z` to `17:36:09Z`).
- Offline-install job `98262203266`: skipped at `17:36:10Z`, zero steps.
- URL: `https://github.com/tekmyster/Hoardarr/actions/runs/32994209539`.

| Artifact | ID | API bytes | API digest | Created / expires | Disposition |
|---|---:|---:|---|---|---|
| `hoardarr-offline-install-inputs` | `9616148946` | `1,072,360,497` | `sha256:749b87631840c78030afef2ebebb1813b7f83a581886778fc3fb09fe0bb0c6f1` | `2026-08-26T17:36:06Z` / `2026-08-29T17:35:56Z` | metadata only; not downloaded |
| `hoardarr-appliance` | `9616142142` | `4,408,885,300` | `sha256:1a1b544d0074f8b7ede408f8b9a69dccbb2ed2702d247c17cbca5becaeb5dbfc` | `2026-08-26T17:35:56Z` / `2026-11-24T17:27:05Z` | metadata only; not downloaded |

No manual dispatch, retry, rerun, cancellation, two-pass run, or artifact download occurred.

## Defects and limits

- No F7 implementation defect remains known from the focused, full local, dual Linux release-suite, or automatic appliance-build evidence.
- This work order does not prove a complete ordinary offline installation or first boot; the manual-only job correctly remained skipped.
- It does not address the superseding OWNER-10 clustered-storage package/kernel/plugin closure.

## Blockers

- **C1:** pending a separately authorized fresh ordinary retry-disabled two-pass no-NIC installation using the accepted F7 source.
- **OWNER-10:** pending the complete offline LINSTOR, DRBD 9, DRBD Reactor, installed-but-disabled LINSTOR Gateway, kernel/Secure-Boot, and Proxmox-plugin sidecar gates.

## Next action

After Supervisor acceptance of F7, authorize exactly one ordinary retry-disabled two-pass no-NIC workflow at `650054bb948693ccdd81562ac57fdb0fb837e710`. Require both fresh passes to complete installation and first boot independently under unchanged bounds, and retain/read back the complete package, service, readiness, QCOW2, checksum, and protected-media evidence. Do not combine that gate with OWNER-10 package or cluster work.
