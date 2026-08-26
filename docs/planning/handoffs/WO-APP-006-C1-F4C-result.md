# WO-APP-006-C1-F4C result

## Result

- **Dispatch identity: PASS.** Exactly one manual `Build appliance ISO` workflow was dispatched after a zero-active-run preflight, on `rc/0.3.11-validation` at locked head `5ff7ef3f37edcafbb6027a9593c2df3c9d069d5a`, with the locked Ubuntu 24.04.4 URL/SHA-256 and ordinary `offline_validation_mode=two-pass` behavior. Run `32967410803`, attempt `1`, is the sole post-boundary `workflow_dispatch` candidate.
- **Shared build: PASS.** Job `98172956135` completed successfully and produced the appliance and retained offline-install inputs.
- **Pass 1: FAIL.** Job `98175686405` ran the unchanged no-NIC product step for its exact 45-minute bound, then timed out because the payload had already exited `100` and Subiquity never reached the bounded reboot checkpoint. The exact first payload error is `E: Internal Error, Pathname to install is not absolute 'coreutils_9.4-3ubuntu6.2_amd64.deb'`. First boot did not run.
- **Pass 2: FAIL.** Job `98175686436` independently produced the same exact payload exit and error, ran to the same unchanged bound, and did not reach first boot.
- **Independent artifact readback: FAIL for acceptance, PASS for retained failure integrity.** Both uniquely named artifacts downloaded exactly once. Their complete payload capture is internally exact, their independently recomputed protected-image hashes match their before manifests, their distinct 32-GiB QCOW2 images are clean, and their validation ISO trees are byte-identical. Because ordinary-mode failure exits before finalization, neither artifact contains `run.json`, first-boot evidence, final `SHA256SUMS`, package/dpkg/APT/service/readiness receipts, or a runner-generated protected-after manifest; those required acceptance facts are not proven.
- **C1: FAIL.** Both independent ordinary installations failed before reboot and first boot.
- **OWNER-10: FAIL.** This run did not implement or validate the deferred LINSTOR, DRBD 9, DRBD Reactor, disabled LINSTOR Gateway, kernel/Secure-Boot, or offline Proxmox-plugin sidecar closure.

No source, workflow, policy, payload, package, test, VM-bound, live-system, credential, KeePass, website, WebUI, cluster, or protected-media mutation was made. No retry, rerun, diagnostic run, second dispatch, cancellation, timeout change, NIC, KVM assumption, or follow-on correction was attempted.

## Evidence

### Authority, locked identities, and repository state

- Work-order SHA-256: `92651e86b3492cdbaa63d6aea0b208459d763b52322d0d58d5cd5a2016471347`.
- Required/observed starting local and origin HEAD: `5ff7ef3f37edcafbb6027a9593c2df3c9d069d5a`.
- End-of-execution source HEAD before this handoff: `5ff7ef3f37edcafbb6027a9593c2df3c9d069d5a`.
- This handoff's commit is the commit containing this file; its parent is the unchanged source head above. Resolve it after checkout with `git log -1 --format=%H -- docs/planning/handoffs/WO-APP-006-C1-F4C-result.md`.
- F2 handoff SHA-256: `9b8d0d4bd34ac450cdc1670a8238d12d618ef53449d442bd67e9cdec3c8363ec`.
- F4 handoff SHA-256: `56884fa731b0e8aa3b35758735c7d5eebe7203b5e6947a36d71c4a5b7cdaaa70`.
- F4B handoff SHA-256: `83dee29fe5694a15255970f0d1631a20f9803664076a26dcfeee5e01be46bee5`.
- F4 implementation source commit: `226a7c25c5eda353cc85b18e638a1c58962e0f54`.
- The inherited dirty/untracked worktree was preserved and excluded from the handoff commit.

### Concurrency preflight, dispatch, and uniqueness

Pre-dispatch UTC boundary: `2026-08-26T12:13:08.3713601Z`. There were zero active same-ref `Build appliance ISO` runs. The most recent pre-existing run was push run `32962854775`, created `2026-08-26T11:20:48Z`, completed success at head `226a7c25c5eda353cc85b18e638a1c58962e0f54`.

Exactly one dispatch was issued at `2026-08-26T12:13:23.5629408Z`:

```text
gh workflow run appliance.yml --ref rc/0.3.11-validation -f base_iso_url=https://releases.ubuntu.com/noble/ubuntu-24.04.4-live-server-amd64.iso -f base_iso_sha256=e907d92eeec9df64163a7e454cbc8d7755e8ddc7ed42f99dbc80c40f1a138433 -f offline_validation_mode=two-pass
```

Post-boundary reconciliation found exactly one candidate:

- Run `32967410803`; `workflow_dispatch`; attempt `1`; created `2026-08-26T12:13:25Z`; completed `2026-08-26T13:12:42Z`; conclusion `failure`.
- Workflow path/display: `.github/workflows/appliance.yml` / `Build appliance ISO`.
- Branch/head: `rc/0.3.11-validation` / `5ff7ef3f37edcafbb6027a9593c2df3c9d069d5a`.
- URL: `https://github.com/tekmyster/Hoardarr/actions/runs/32967410803`.
- Both job logs independently record the exact locked base ISO URL and SHA-256 plus `HOARDARR_OFFLINE_DIAGNOSTIC_MODE: false`. The two matrix jobs and legacy artifact names prove ordinary two-pass selection.

### Jobs, steps, timestamps, and fixed bounds

| Job | ID | Start | End | Conclusion | Decisive steps |
|---|---:|---|---|---|---|
| Shared build | `98172956135` | `2026-08-26T12:13:30Z` | `2026-08-26T12:22:59Z` | success | step 8 repository build/verify success; step 13 ISO build success; step 14 boot checkpoint success; step 15 appliance upload success; step 16 install-input upload success |
| `offline-install (pass-1)` | `98175686405` | `2026-08-26T12:23:04Z` | `2026-08-26T13:12:40Z` | failure | step 6 validation ISO success `12:25:17Z`-`12:25:56Z`; step 7 product run failure `12:25:56Z`-`13:10:56Z`; step 8 evidence upload success `13:10:56Z`-`13:12:38Z` |
| `offline-install (pass-2)` | `98175686436` | `2026-08-26T12:23:02Z` | `2026-08-26T13:11:29Z` | failure | step 6 validation ISO success `12:24:11Z`-`12:24:48Z`; step 7 product run failure `12:24:48Z`-`13:09:49Z`; step 8 evidence upload success `13:09:49Z`-`13:11:27Z` |

Each product step used the unchanged harness contract: fresh 32-GiB QCOW2, `-nic none`, two 64-MiB read-only protected marker disks, `-no-reboot`, 45-minute installer bound, and 15-minute first-boot bound. Pass 1 was terminated by `timeout` at `2026-08-26T13:10:56.2223248Z`; pass 2 at `2026-08-26T13:09:49.1420802Z`. The ordinary failure path does not persist accelerator/argv metadata before finalization, so the selected accelerator cannot be independently claimed from the artifacts. No first-boot process began, so the 15-minute bound was never entered.

### Artifact API identity and single-download destinations

Exactly four run artifacts existed. Only the two pass artifacts were downloaded:

| Artifact | ID | API bytes | API digest | Created / expires | Download disposition |
|---|---:|---:|---|---|---|
| `hoardarr-offline-pass-1` | `9608257753` | `2,405,492,309` | `sha256:b33b924df1cb8057e0c90176ea2e01175e94f7be2251009f4087d70f55f1dfb3` | `2026-08-26T13:12:37Z` / `2026-09-09T13:10:56Z` | downloaded once to `C:\Users\dmessana\Documents\troubleshooting\Hoardarr\.codex-temp\f4c-32967410803\pass-1` |
| `hoardarr-offline-pass-2` | `9608216310` | `2,394,418,704` | `sha256:e7e63339295e38cb141625bf40529cc12310198f6516ac29eb7d8879415534b7` | `2026-08-26T13:11:27Z` / `2026-09-09T13:09:49Z` | downloaded once to `C:\Users\dmessana\Documents\troubleshooting\Hoardarr\.codex-temp\f4c-32967410803\pass-2` |
| `hoardarr-offline-install-inputs` | `9606534170` | `1,072,360,505` | `sha256:8a6eb1ef542444d999df7181addec5a3c9f0c491ede71e6a1399b8c38eff577f` | `2026-08-26T12:22:56Z` / `2026-08-29T12:22:49Z` | metadata only; not downloaded |
| `hoardarr-appliance` | `9606529737` | `4,408,881,152` | `sha256:63d601f8e26c353e8f055818e52b5bdb99e16f809321ae654573a239d92caca8` | `2026-08-26T12:22:48Z` / `2026-11-24T12:13:27Z` | metadata only; not downloaded |

The target root was absent before creation and the host had `940,760,215,552` free bytes. `gh run download` authenticated each unique name/run and extracted it directly; it does not retain the transport ZIP, so the API ZIP digest cannot be recomputed without violating the one-download rule. Identity is therefore grounded in the unique run/name/API record and the independently hashed extracted content below, without falsely claiming a second archive read.

### Independently recomputed extracted evidence

| Pass | File | Bytes | SHA-256 |
|---|---|---:|---|
| 1 | `hoardarr-offline-test.iso.sha256` | `128` | `418515c32ea746da7270c1970493233a071b5d2f4ac0cd5111f7f27feaf42788` |
| 1 | `hoardarr-offline-test.iso.tree-sha256` | `248,173` | `7c6178bb253aea6dce472eae8bd16121c2ab51ab87d43ac10646da1b14df1438` |
| 1 | `installer-serial.log` | `116,873` | `6de4dbef64af14794d274216bae63b945b4c7a48d43309b558c17b9963d8e773` |
| 1 | `os.qcow2` | `4,035,117,056` | `44c5f275c94ea66913871fa8bc41c6a12361b0b066b618caaa5e6ea959d1dbb9` |
| 1 | `protected-before.sha256` | `288` | `148b035bf33e3158a8fd3adad804f032ac295df2a8e05c67b024b09cbb2f7116` |
| 1 | `protected-one.raw` | `67,108,864` | `ba6170c17c667a8cfb9577349163cae578fd358b5da5d5be1859ee93da52bcae` |
| 1 | `protected-two.raw` | `67,108,864` | `e9c5cda034df938e8b4e37f8520424aa3ab36aa6078e2c1f12e85f996d5d3cc8` |
| 2 | `hoardarr-offline-test.iso.sha256` | `128` | `abedda31dda845b61ece25f2779d2155fbb8dc2b73b1282a86eb678800de622f` |
| 2 | `hoardarr-offline-test.iso.tree-sha256` | `248,173` | `7c6178bb253aea6dce472eae8bd16121c2ab51ab87d43ac10646da1b14df1438` |
| 2 | `installer-serial.log` | `116,873` | `6de4dbef64af14794d274216bae63b945b4c7a48d43309b558c17b9963d8e773` |
| 2 | `os.qcow2` | `4,004,839,424` | `27a2c1a0af37cca2fc25ef21effce14f2656360fff0736e301f8001841d4c4be` |
| 2 | `protected-before.sha256` | `288` | `7526781c2369021a18489ec9c75f99b2745dc95ae7efc8c399a9b8f823197c1e` |
| 2 | `protected-one.raw` | `67,108,864` | `ba6170c17c667a8cfb9577349163cae578fd358b5da5d5be1859ee93da52bcae` |
| 2 | `protected-two.raw` | `67,108,864` | `e9c5cda034df938e8b4e37f8520424aa3ab36aa6078e2c1f12e85f996d5d3cc8` |

The ISO byte-digest receipts name different independently rebuilt images:

- pass 1 ISO: `1e608f6f7a3ec30d3b8c6bf37997e49adfe389cf73f298e830ec460369d4ad22`;
- pass 2 ISO: `fb240cd8fa9d486fee9bdf18064b321f7f2bb757f31cc2eac992df20af12958c`.

Both 1,944-row tree manifests have strict hash/path syntax, 1,944 unique paths, no traversal, and identical bytes. The validation ISO itself is intentionally not retained in a pass artifact, so these manifests/digest receipts cannot be recomputed against ISO bytes from this artifact alone.

### Per-pass payload and acceptance readback

Both serial files are byte-identical and each contains exactly one complete sequence:

```text
HOARDARR_OFFLINE_PAYLOAD_BEGIN
...
59 upgraded, 221 newly installed, 0 to remove and 49 not upgraded.
After this operation, 600 MB of additional disk space will be used.
E: Can not write log (Is /dev/pts mounted?) - posix_openpt (19: No such device)
E: Internal Error, Pathname to install is not absolute 'coreutils_9.4-3ubuntu6.2_amd64.deb'

HOARDARR_OFFLINE_PAYLOAD_END
HOARDARR_OFFLINE_PAYLOAD_EXIT=100
HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE=116675
HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SHA256=997c9e42b4b0661ed4256772630bd4673b3d296f0131ed81af2f726f1bae940b
HOARDARR_OFFLINE_PAYLOAD_CAPTURE_COMPLETE
```

Independent reconstruction of each target-log byte range through the exact exit marker yields exactly `116,675` bytes and SHA-256 `997c9e42b4b0661ed4256772630bd4673b3d296f0131ed81af2f726f1bae940b`, matching both retained sentinels. Each capture has 2,146 repository `: OK` lines, zero `: FAILED` lines, and explicitly verifies `evidence/package-manifest.json`, `evidence/root-package-versions.txt`, both compatibility-family evidence files, SnapRAID package/license content, and the signed repository payload before APT resolution. The transaction reaches real installation after a successful plan but exits before package receipt/final verification.

For each pass:

- `HOARDARR_OFFLINE_READY`: absent;
- `HOARDARR_OFFLINE_EVIDENCE_BEGIN`: absent;
- installer reboot checkpoint: not reached;
- first boot/readiness: not run;
- installed package name/version/architecture receipt: absent;
- `dpkg --audit`, final APT no-download simulation, SnapRAID command, required-command, service enablement/activity, and loopback readiness receipts: absent;
- `run.json`, first-boot serial, final `SHA256SUMS`, `protected-after.sha256`, `protected-diff.txt`, and runner `qemu-img-check.txt`: absent because the ordinary harness exits immediately at the failed installer bound.

Independent host-side QEMU 11.1 readback compensates only for the retained image-health facts:

| Pass | Virtual bytes | Actual bytes | Dirty/corrupt | `qemu-img check` |
|---|---:|---:|---|---|
| 1 | `34,359,738,368` | `4,035,117,056` | false / false | exit `0`; `check-errors=0`; 61,541 allocated clusters |
| 2 | `34,359,738,368` | `4,004,839,424` | false / false | exit `0`; `check-errors=0`; 61,079 allocated clusters |

The two protected images in each artifact independently recompute exactly to their corresponding before-manifest hashes. This proves no retained protected-image drift at upload; it does not manufacture the absent in-run after/diff receipt.

### Cross-pass clean-state comparison

- The OS images are distinct in physical size, allocation count, fragmentation state, and SHA-256 while each has the same 32-GiB virtual geometry and a clean QCOW2 check. This is evidence of separate fresh disposable guest state, not a copied/shared guest image.
- The independently rebuilt validation ISOs have different byte hashes, as expected, while their full 1,944-entry trees are identical.
- Both protected marker pairs retain the expected exact identities.
- Both payload transcripts are byte-identical and fail on the same first package pathname. This deterministic agreement is failure reproduction, not acceptance: neither pass provides final package, service, readiness, or first-boot evidence.

## Defects

The exact new release-gate defect is an actual-install seam not exercised by the accepted simulation-only F4B gate. The production payload invokes APT with the signed local file repository and `--no-download`; in both ordinary installs APT resolves the intended 59-upgrade/221-new transaction but rejects the repository package pathname as non-absolute before installing it. The retained evidence does not justify changing package roots, compatibility families, network policy, service policy, VM geometry, bounds, or the installer itself beyond that seam.

The ordinary failure path also does not finalize `run.json`, argv/accelerator metadata, protected-after/diff, QCOW2 check, or a recursive checksum manifest. That limits post-failure evidence but is not the first product failure: the complete serial capture decisively proves the actual APT exit `100`.

One local procedure discrepancy is recorded rather than hidden: the target directory's absence and `940,760,215,552` free bytes were verified before creation, but a separate write/read/delete canary was not executed before the directory was created. The subsequent two successful extractions and complete independent hashes prove the destination was readable and writable, but they do not retroactively satisfy the order's pre-creation canary wording.

## Blockers

- **C1:** blocked by the deterministic actual-install APT pathname failure reproduced independently in both passes.
- **OWNER-10:** separately blocked by the still-unimplemented and unvalidated offline LINSTOR/DRBD 9/DRBD Reactor/disabled LINSTOR Gateway closure, kernel/Secure-Boot support, and offline Proxmox-plugin sidecar.

## Next action

Authorize one narrow source-correction work order for the actual-install APT file-repository invocation: reproduce the retained failure in a disposable root, correct only the mechanism that causes relative local-repository package paths under `--no-download` (while retaining the exact signed file source, zero network acquisition, exact 109 roots and both accepted compatibility families), add executable actual-install regression coverage, and then authorize one fresh ordinary two-pass C1 workflow. Do not alter the solver inputs, loosen signature/service/storage safeguards, or begin OWNER-10 in that correction.
