# WO-APP-006-C1-F1 result

## Result

- **F1 exact-mask correction: PASS (scoped software behavior).** The production payload now preserves only a pre-existing exact absolute `/dev/null` mask, rejects every other pre-existing object without changing it, removes only masks created by the current invocation, and never invokes the final mutating `systemctl disable` operation for an accepted pre-existing mask. Executable disposable-root coverage includes the full post-cleanup/final-disable lifecycle for exact `iscsi.service`.
- **C1 ordinary two-pass baseline: FAIL.** The one authorized retry-disabled `two-pass` run used the corrected exact commit. Both fresh no-NIC passes independently returned payload status 1 on a package-created `iscsi.service` vendor alias, then remained at Subiquity's interactive error state until the unchanged 45-minute installer bound. Neither pass reached first boot.
- **Superseding OWNER-10: FAIL.** This work deliberately does not add or validate LINSTOR, DRBD 9, DRBD Reactor, installed-but-disabled LINSTOR Gateway, kernel/Secure-Boot integration, or the offline Proxmox-plugin sidecar.

No live appliance, credential, vault, service, network, VM, pool, physical disk, protected disk, package root, or clustered-storage payload was mutated. There was no diagnostic dispatch, retry, rerun, timeout extension, NIC addition, or second product correction after the failed qualifying run.

## Evidence

### Commits and changed files

- Required starting local/origin commit: `df757447686b34ee17a433d63aafe84dc6afb92d`.
- F1 implementation commit: `a1aa1fd737b122c12dc4a29cb1894c70ded3a141` (`fix(appliance): preserve existing unit masks`).
  - `packaging/appliance/install-offline-payload.sh`
  - `tests/release/test_offline_appliance.py`
- Supervisor-QA correction commit: `762dfad3c1ae56b24fc66973bea69d7776a9bae7` (`test(appliance): sort offline test imports`).
  - `tests/release/test_offline_appliance.py` only; import ordering only.
- Final implementation HEAD and pushed origin before this handoff: `762dfad3c1ae56b24fc66973bea69d7776a9bae7`.

The accepted implementation records pre-existing safe masks separately from temporary masks. The final-disable phase revalidates and skips a preserved exact `/dev/null` mask; newly created temporary masks are removed and their installed units remain eligible for the intended disable/readback operation.

### Local validation

Final commands and results:

```text
backend/.venv/Scripts/ruff.exe check tests/release/test_offline_appliance.py
All checks passed!

uv run python -m unittest tests.bootstrap.test_manifests tests.release.test_release_bundle tests.release.test_appliance_assets tests.release.test_offline_appliance
Ran 51 tests; OK (skipped=1 expected platform-specific case)

bash -n packaging/appliance/install-offline-payload.sh
bash -n tests/appliance/run-offline-iso-pass.sh
PASS
```

The focused executable mask test is included in the 51-test run. It proves absent/safe/unsafe/mixed-object behavior, normal and simulated-failure cleanup, and the post-cleanup final-disable seam for exact `iscsi.service`. Both unattended YAML documents and `.github/workflows/appliance.yml` parsed successfully; `git diff --check` passed. The initially pushed implementation was not manually dispatched because Supervisor QA identified Ruff I001. The separate import-only correction made the exact repository Ruff command reproducibly green, after which the complete focused suite was rerun.

### Automatic build gate

- Automatic run `32934598311` at `a1aa1fd737b122c12dc4a29cb1894c70ded3a141`: cancelled by the import-only correction push; no manual run was dispatched at that commit.
- Corrected automatic run `32934758625` at exact head `762dfad3c1ae56b24fc66973bea69d7776a9bae7`: **PASS**.
- Build job `98073763241`: `2026-08-26T05:37:23Z` to `05:45:32Z`; all repository, release, ISO composition, visible boot-checkpoint, and artifact-upload steps passed.

### One authorized ordinary two-pass run

- Workflow: `Build appliance ISO`.
- Run: `32935316262`; exact head `762dfad3c1ae56b24fc66973bea69d7776a9bae7`; event `workflow_dispatch`; overall **FAIL**.
- Dispatch mode: ordinary `two-pass`, retry disabled.
- Pinned base ISO URL: `https://releases.ubuntu.com/noble/ubuntu-24.04.4-live-server-amd64.iso`.
- Pinned base ISO SHA-256: `e907d92eeec9df64163a7e454cbc8d7755e8ddc7ed42f99dbc80c40f1a138433`.
- Shared build job `98075319416`: **PASS**, `2026-08-26T05:45:50Z` to `05:54:19Z`.
- Pass-1 job `98076902153`: **FAIL**. Install execution `2026-08-26T05:59:04Z` to `06:44:04Z`, exactly the retained 45-minute bound; job completed `06:45:49Z` after artifact upload.
- Pass-2 job `98076902167`: **FAIL**. Install execution `2026-08-26T05:55:53Z` to `06:40:53Z`, exactly the retained 45-minute bound; job completed `06:42:33Z` after artifact upload.
- Harness proof at the exact commit retains `-nic none` for common install/first-boot QEMU arguments, a 45-minute install bound, and a 15-minute first-boot bound. Neither pass reached the first-boot phase.

Artifacts were each downloaded once and read independently from:

```text
C:\Users\dmessana\Documents\troubleshooting\Hoardarr\.codex-temp\f1-32935316262\pass-1
C:\Users\dmessana\Documents\troubleshooting\Hoardarr\.codex-temp\f1-32935316262\pass-2
```

- Pass-1 artifact: ID `9596027658`; name `hoardarr-offline-pass-1`; API size `2,380,657,822`; digest `sha256:3eb4457ece9ec3cb58e62883cc2a4aa0c7586017a2b49dffcc7b09e0b35f3251`; expires `2026-09-09T06:44:04Z`.
- Pass-2 artifact: ID `9595949420`; name `hoardarr-offline-pass-2`; API size `2,349,348,662`; digest `sha256:72a7a365632867ce90e4b9cf82d2e899dc416032a70078045dedc10d40d472c5`; expires `2026-09-09T06:40:54Z`.
- Retained install-input artifact: ID `9594807475`; digest `sha256:95fab05561287f0b14f44e9baef181c219fb8dda7188180477950b237437d2d0`.
- Appliance artifact: ID `9594804314`; digest `sha256:8c8de5596240e196d82c694c20e81e6217474f1cabc40dc89de7e209cfbaf49f`.

### Cross-pass readback and exact failure

The two independently rebuilt validation ISOs have different byte identities as expected:

- Pass-1 recorded ISO SHA-256: `4980698430e3248df3b0576e641c360131f41a51bfb286ae51c36e09f07bda67`.
- Pass-2 recorded ISO SHA-256: `d47a20687082739895bcca381118da703b4ed7c3511bb5d18d34fb3003ced481`.

Their 246,252-byte tree manifests are byte-identical and each has SHA-256 `a4206beadb7bcc0932f8129c74604a351e7f901f758dd9f6789fa0a6bb841666`. Both tree manifests record:

```text
0833de5863c2c18421e5d3445d045baae08d462aa5645571525af9f35b43eaeb  ./hoardarr/install-offline-payload.sh
```

That exactly matches the corrected payload in the checked-out implementation, proving the CI validation ISOs did not package a stale pre-F1 script.

Both 103,776-byte installer serial logs are byte-identical with SHA-256 `1d78ce6765f1be957d7af4925551dda4bab11fa3366fbd87c9c34555f5ce9e09`. Each contains one complete D2 marker sequence. The minimal decisive retained output is:

```text
offline install refuses to replace a pre-existing unit override: iscsi.service
HOARDARR_OFFLINE_PAYLOAD_EXIT=1
HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE=103578
HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SHA256=2fc51d961b6e5aa31ec581f3c3760aa3aae7df6f97d418f0e7a885932ccb8548
HOARDARR_OFFLINE_PAYLOAD_CAPTURE_COMPLETE
```

Supervisor independently converted the retained pass-1 QCOW2 read-only and inspected its ext4 root. The exact retained object is:

- `etc/systemd/system/iscsi.service`: `lrwxrwxrwx`, root-owned, inode `1573825`, size 42, target `/usr/lib/systemd/system/open-iscsi.service`.
- `usr/lib/systemd/system/open-iscsi.service`: regular root-owned unit, inode `670040`, size 1003.

This is a vendor-unit alias, not the exact `/dev/null` mask F1 was authorized to accept. F1 therefore behaved fail closed and did not alter the alias. The result disproves both a stale-payload explanation and an unsafe-mask acceptance explanation.

### QCOW2 and protected media

Independent Windows QEMU 11.1.0 readback:

- Pass-1 QCOW2: 32 GiB (`34,359,738,368` bytes) virtual, `4,021,813,248` bytes actual, `dirty-flag=false`, `corrupt=false`; `qemu-img check --output=json` returned 0 with `check-errors=0`.
- Pass-2 QCOW2: 32 GiB virtual, `3,973,513,216` bytes actual, `dirty-flag=false`, `corrupt=false`; `qemu-img check --output=json` returned 0 with `check-errors=0`.
- Each pass's 64 MiB `protected-one.raw` recomputes to `ba6170c17c667a8cfb9577349163cae578fd358b5da5d5be1859ee93da52bcae`, exactly its before manifest.
- Each pass's 64 MiB `protected-two.raw` recomputes to `e9c5cda034df938e8b4e37f8520424aa3ab36aa6078e2c1f12e85f996d5d3cc8`, exactly its before manifest.

Because ordinary timeout handling stops before final evidence finalization, neither artifact contains `run.json`, a protected-after manifest, first-boot serial, installed package/service/readiness receipts, or the final recursive checksum manifest. Therefore no package-install, service-policy, readiness, first-boot, or before/after protected-media completion claim is made. The independently recomputed retained protected images do match their before manifests, but that is narrower than the absent final before/after receipt.

## Defects

1. Noble's clean target represents `iscsi.service` as a package/vendor alias to `open-iscsi.service`, not as an absent entry or a `/dev/null` mask. F1 correctly rejects it under its explicit safety contract, so the deny-unit setup cannot progress to package installation.
2. After the payload's explicit exit 1, Subiquity remains at its interactive error state until the ordinary 45-minute harness bound. This is expected from the retained unattended-error behavior but makes both qualifying passes fail.
3. Ordinary failure finalization does not retain `run.json`, protected-after/readback, first-boot evidence, or a recursive checksum manifest. D2 still provides the decisive exact payload failure; this F1 turn did not broaden into a harness change.
4. The OWNER-10 clustered-storage package/kernel/Secure-Boot/sidecar closure remains entirely unimplemented and unevidenced by this baseline run.

## Blockers

- **C1 remains FAIL:** both independent ordinary no-NIC passes failed before first boot on the same proven vendor-alias collision.
- **OWNER-10 remains FAIL:** the full LINSTOR + DRBD 9 + DRBD Reactor + installed-but-disabled LINSTOR Gateway and offline Proxmox-plugin sidecar gate is deferred and cannot be inferred from this pre-cluster baseline.
- F1 permits no second correction or workflow run in this turn.

## Next action

Authorize one narrow successor that explicitly handles package-owned/vendor unit aliases without weakening F1's local-override protection: use authoritative package/systemd metadata to distinguish the clean Noble `iscsi.service -> /usr/lib/systemd/system/open-iscsi.service` alias from an administrator-created override, apply the deny policy to the correct canonical unit while leaving the alias untouched, add disposable whole-lifecycle tests for alias/canonical-unit behavior and unsafe lookalikes, and then run one ordinary two-pass no-NIC gate. Do not relax the existing rule for arbitrary non-`/dev/null` objects and do not begin clustered-storage packages in that correction.
