# WO-APP-006-C1-F2 result

## Result

- **F2 exact package-backed alias correction: PASS (scoped software verification only).** The production payload recognizes only the exact installed Noble `open-iscsi` alias tuple, keeps the original alias untouched through pre-finalization, uses the existing fail-closed `policy-rc.d` guard for install-time start denial, and applies final inactive-state enforcement to canonical `open-iscsi.service`. Executable whole-lifecycle and negative coverage passed. The qualifying remote pass progressed beyond the former alias failure, but the later APT failure means the alias behavior was not fully installation-verified in that pass.
- **C1 ordinary two-pass baseline: FAIL.** In the one authorized ordinary run, pass-2 reached QEMU and failed after the exact 45-minute installer bound. Pass-1 never entered QEMU; it was canceled once during the external pinned-ISO download after pass-2 became terminal, as required by the stop-on-any-failure rule. No retry, rerun, diagnostic dispatch, timeout extension, NIC addition, or second correction occurred.
- **Superseding OWNER-10: FAIL.** This work did not add or validate LINSTOR, DRBD 9, DRBD Reactor, installed-but-disabled LINSTOR Gateway, kernel/Secure-Boot integration, or the offline Proxmox-plugin sidecar.

No live appliance, service, credential, vault, VM, network, pool, physical/protected disk, owner data, website, package root, workflow, harness, unattended data, VM geometry, or clustered-storage payload was mutated.

## Evidence

### Commits and authorized files

- Required starting local/origin commit: `3ce1c78f59c724a3d14dffae48263a816abb34a5`.
- F2 implementation commit: `e6806c9d5b7668c92407759a0f6d0b485b7dc0c2` (`fix(appliance): handle package-backed iscsi alias`).
- Implementation files only:
  - `packaging/appliance/install-offline-payload.sh`
  - `tests/release/test_offline_appliance.py`
- Handoff evidence commit: the separate commit containing this document; its exact pushed SHA is reported in the Builder's terminal response because a commit cannot contain its own SHA.

Before this handoff was written, local `HEAD` and `origin/rc/0.3.11-validation` both read `e6806c9d5b7668c92407759a0f6d0b485b7dc0c2`. All inherited dirty and untracked paths were preserved.

### Exact classifier and lifecycle behavior

The classifier accepts only requested unit `iscsi.service` when all of the following independently agree:

- `/etc/systemd/system/iscsi.service` is a root-owned symlink with literal target `/usr/lib/systemd/system/open-iscsi.service`;
- the canonical target is a root-owned regular non-symlink file;
- dpkg status identifies installed package `open-iscsi`;
- dpkg ownership, `.list`, and `.md5sums` metadata identify the exact canonical path and its content hash;
- the canonical unit's authoritative `[Install]` metadata declares exactly `Alias=iscsi.service`;
- relevant parent directories and package metadata are root-owned, regular/non-symlink objects as required.

Wrong units, relative or alternate targets, missing/wrong canonical objects, unsafe parents, wrong ownership, package mismatch, missing/malformed metadata, hash mismatch, extra/wrong aliases, and identity drift fail closed without modifying the original object.

The original package alias is never moved or recreated during pre-finalization. Identity is rechecked before registration and final enforcement. Temporary masks record identity before publication to cleanup state; cleanup safely aggregates errors, preserves an original nonzero payload status, and turns an otherwise successful run into failure on cleanup-integrity error. The exact production `policy-rc.d` guard causally denies the retained package lifecycle's invoke-start step. On successful finalization, strict canonical disable must remove both the alias and `sysinit.target.wants/open-iscsi.service`, and exact `is-enabled` readback must be `disabled` with status 1. Accepted pre-existing exact `/dev/null` masks retain F1 behavior.

### Local validation

Final commands after the last material diff:

```text
backend/.venv/Scripts/ruff.exe check tests/release/test_offline_appliance.py
All checks passed!

uv run python -m unittest tests.bootstrap.test_manifests tests.release.test_release_bundle tests.release.test_appliance_assets tests.release.test_offline_appliance
Ran 52 tests; OK (skipped=1 expected platform-specific case)

bash -n packaging/appliance/install-offline-payload.sh
bash -n tests/appliance/run-offline-iso-pass.sh
PASS
```

`packaging/appliance/user-data`, `tests/appliance/offline-user-data`, and `.github/workflows/appliance.yml` parsed as YAML. `git diff --check` passed. The executable production-function tests cover the exact tuple, causal postinst start denial, same-inode preservation, pre-finalization drift, final canonical disable, alias/wants removal, temporary-mask identity drift, stat failure before publication, cleanup/status aggregation, all required unsafe lookalikes, and the accepted F1 cases.

### Automatic build gate

- Automatic workflow run `32942660198` at exact implementation head `e6806c9d5b7668c92407759a0f6d0b485b7dc0c2`: **PASS**.
- Build job `98096699384`: **PASS**, `2026-08-26T07:25:13Z` to `07:34:02Z` (8m49s).
- The automatic push workflow did not execute the manual offline-install matrix, as designed.

### One authorized ordinary two-pass run

- Workflow run: `32943411481` (`workflow_dispatch`), exact head `e6806c9d5b7668c92407759a0f6d0b485b7dc0c2`.
- Final run conclusion: **cancelled** only because the active pass-1 infrastructure download was canceled once after pass-2 had already become terminal **FAILURE**. This does not change C1's failed product result.
- Mode: ordinary `two-pass`; diagnostic mode false; retry disabled.
- Base ISO URL: `https://releases.ubuntu.com/noble/ubuntu-24.04.4-live-server-amd64.iso`.
- Base ISO SHA-256: `e907d92eeec9df64163a7e454cbc8d7755e8ddc7ed42f99dbc80c40f1a138433`.
- Shared build job `98098956962`: **PASS**, `2026-08-26T07:34:21Z` to `07:42:54Z`.
- Pass-2 job `98101047328`: **FAILURE**. QEMU/product step ran exactly from `2026-08-26T07:45:45Z` to `08:30:45Z`; artifact upload completed and the job terminated at `08:32:32Z`.
- Pass-1 job `98101047374`: **infrastructure-cancelled before product execution**. It remained in `Download and verify the pinned Ubuntu base ISO` from `2026-08-26T07:43:38Z` until the one cancellation completed at `08:33:30Z`; the validation-ISO build and QEMU product step were both skipped. No pass-1 artifact was downloaded or inspected.
- The exact committed harness invokes QEMU with `-nic none`; the job step is named `Execute clean install and first boot with no virtual NIC`, and its environment records `HOARDARR_OFFLINE_DIAGNOSTIC_MODE: false`. The unchanged install and first-boot bounds are 45 and 15 minutes. Pass-2 did not reach first boot.

### Retained pass-2 artifact and independent readback

Only pass-2 was downloaded, exactly once, to:

```text
C:\Users\dmessana\Documents\troubleshooting\Hoardarr\.codex-temp\f2-32943411481\pass-2
```

- Artifact ID: `9599100089`.
- Name: `hoardarr-offline-pass-2`.
- API size: `2,355,972,416` bytes.
- Artifact digest: `sha256:58b252137748587f985848de9cfabcb802791a7e6fd2ee35450186ea8fdabdee`.
- Created: `2026-08-26T08:32:31Z`; expires: `2026-09-09T08:30:46Z`.
- Recorded validation-ISO SHA-256: `9c2beee68c328efabaa637cf9587ccbf173c5462c2105d33d0b624c3817c8111`.
- ISO tree-manifest file: 246,252 bytes, SHA-256 `7ae36335a22cf60745cabed8b24777a7a42753ae9094f2adea3d10a5b15865e3`.
- Tree-manifest payload entry: `316328ba2bb66c86b71c5365c5e88cf2c693e58cd77e729b8f41d39f6d39ae65`; this exactly matches the checked-out F2 payload, ruling out a stale script.

The complete D2 capture parser returned its expected nonzero-payload classification 10 and independently reconstructed:

- payload status: `100`;
- target log: 103,836 bytes, SHA-256 `54ab439c3f754404d85d73ed35bc275177180c2f972251c33875054bc680e75b`;
- console/serial log: 104,034 bytes, SHA-256 `020738ccfb73f5fed1899c845f9789e4cdb9555eb2a0685ea364a79d9d0d0b19`;
- serial transform: `none`;
- exactly one complete begin/end/exit/size/hash/capture-complete marker sequence.

The minimal decisive retained output is:

```text
E: Error, pkgProblemResolver::Resolve generated breaks, this may be caused by held packages.
HOARDARR_OFFLINE_PAYLOAD_EXIT=100
HOARDARR_OFFLINE_PAYLOAD_CAPTURE_COMPLETE
```

This is later than and distinct from F1's `iscsi.service` collision: F2 passed the former guard point, verified the offline-repository files, refreshed its local APT indexes, and failed when APT resolved the requested install set. Subiquity then remained at its interactive error state until the 45-minute harness bound. The retained output does not identify the conflicting package pair, so this handoff does not speculate about it.

Independent Windows QEMU 11.1.0 readback of `os.qcow2`:

- file/actual size: `3,968,401,408` bytes;
- virtual size: `34,359,738,368` bytes (32 GiB);
- format `qcow2`, compat 1.1, 65,536-byte clusters;
- `dirty-flag=false`, `corrupt=false`;
- `qemu-img check --output=json` exit 0, `check-errors=0`.

Retained protected images independently recompute exactly to their before-manifest hashes:

- `protected-one.raw`, 64 MiB: `ba6170c17c667a8cfb9577349163cae578fd358b5da5d5be1859ee93da52bcae`;
- `protected-two.raw`, 64 MiB: `e9c5cda034df938e8b4e37f8520424aa3ab36aa6078e2c1f12e85f996d5d3cc8`.

Ordinary timeout handling retained no `run.json`, protected-after manifest, installed-package/service/readiness receipts, first-boot serial, or final recursive checksum manifest. Therefore package installation, final service state, readiness, and first boot are **not verified**. Protected-file equality is an independent retained-file comparison, narrower than the absent final before/after receipt.

## Defects

1. The F2 run moved beyond the package-backed `iscsi.service` alias collision but the offline APT install set now fails dependency resolution with exit 100. The retained output proves the failure class but not the exact conflicting package relationship.
2. After the payload exits 100, Subiquity remains at its interactive error state until the existing 45-minute bound. This makes pass-2 fail without reaching first boot.
3. Pass-1 exposed an external-infrastructure budget defect: the pinned Ubuntu ISO download had no workflow timeout and remained active for nearly 50 minutes. It was canceled before product execution; by instruction, this turn did not change the workflow.
4. Ordinary failure finalization does not retain completion receipts or a recursive checksum manifest. The complete D2 serial capture and independently checked QCOW2/protected files are retained, but completion cannot be claimed.
5. OWNER-10 clustered-storage package/kernel/Secure-Boot/sidecar closure remains unimplemented and unevidenced.

## Blockers

- **C1 remains FAIL:** the only product-executing pass failed before first boot; the other matrix pass was infrastructure-cancelled before QEMU after the product failure made the run terminal for acceptance purposes.
- **OWNER-10 remains FAIL:** LINSTOR + DRBD 9 + DRBD Reactor + installed-but-disabled LINSTOR Gateway, kernel/Secure-Boot handling, and the offline Proxmox-plugin sidecar remain deferred.
- F2 authorizes no additional correction, diagnostic, or workflow run.

## Next action

Authorize one narrow successor to reproduce the exact F2 offline APT transaction in a disposable Noble target using the retained repository/package manifest and enable APT solver diagnostics sufficient to name the first conflicting package/version relationship. Correct only that proven dependency-closure defect, add a focused regression for the exact transaction, and then use the separately authorized ordinary two-pass gate. Do not relax the alias classifier, alter service-safety behavior, or begin the deferred clustered-storage package work as part of that diagnosis.
