# WO-APP-006-C1-F5A result

## Result

- **Preflight / uniqueness: PASS.** At the `2026-08-26T14:12:21.3500422Z` boundary there were zero active same-ref appliance runs. Exactly one locked ordinary `two-pass` dispatch was issued, producing run `32978848891`, attempt `1`, at exact source head `27f82fe201361d8c57307d971111faef3b8950f0`.
- **Shared build: PASS.** Job `98210171762` completed successfully.
- **Pass 1: FAIL.** Job `98213693573` reached the unchanged 45-minute installer bound. Its exact retained payload capture exits `100`; the first package-configuration failure is `pcp`, after repeated evidence that `/proc` is not mounted. Kernel configuration later also fails because the chroot lacks the required pseudo-filesystem/device context. First boot did not run.
- **Pass 2: FAIL.** Job `98213693598` independently reproduces the same first failure and exact payload exit `100`, then reaches the same 45-minute bound. First boot did not run.
- **Artifact identity / single download: PASS.** Each uniquely named pass artifact was requested exactly once into a fresh destination. The appliance and install-input artifacts were not downloaded.
- **Offline payload: FAIL for installation; PASS for exact failure retention.** Both serial streams contain exactly one begin/end/exit/size/hash/capture-complete sequence. Independent parsing validates each complete capture and payload status `100`.
- **Package/family closure: FAIL for acceptance.** F5 removed the former relative-path failure: both passes acquire `283` package objects from the signed local `file:` repository, show no HTTP/HTTPS reference, and unpack the accepted systemd `8.17` and linux-meta `6.8.0-138.138` transitions. Actual configuration fails before final 109-root/family readback, `dpkg --audit`, and final APT checks, so closure cannot be accepted.
- **First boot/readiness: FAIL / not reached.** No first-boot transcript, release/readiness receipt, or loopback health result exists.
- **Service policy: FAIL / not proven.** The payload does preserve install-time denial far enough to report masked-unit preset failures, but final inactive/disabled service readback was not reached.
- **QCOW2 integrity: PASS for retained-image integrity.** Both distinct images are clean 32-GiB QCOW2 files with `qemu-img check` exit `0` and zero check errors.
- **Protected media: PASS for retained bytes; FAIL for the full in-run receipt gate.** All four retained marker images independently match their before-manifest hashes. The ordinary early exit did not create protected-after or empty-diff receipts.
- **C1: FAIL.** Neither independent ordinary pass completed installation, reboot, or first boot.
- **OWNER-10: FAIL.** LINSTOR, DRBD 9, DRBD Reactor, installed-but-disabled LINSTOR Gateway, kernel/Secure-Boot support, and the offline Proxmox-plugin sidecar remain outside this run and unvalidated.

No source, workflow, package, policy, unattended data, harness, VM bounds, NIC topology, live system, credential, KeePass, website, WebUI, cluster, or protected-media change was made. There was no retry, rerun, cancellation, second dispatch, diagnostic run, correction, or follow-on work.

## Evidence

### Authority and repository identity

- Work-order SHA-256: `a5e622c988f9292e63b1d3ce629c0c4eaa411a902d961d1bebbf524f755ef080` (`8,267` bytes).
- F5 handoff SHA-256: `e6191944734f8be6c01024fc5a19f2ff2eca1eec10ff1805e1677e004aaa343e`.
- F4C handoff SHA-256: `99cfa7cedaafd16f610b02bf9abf2c3137629c9d749a4e2bb97b825bb1935040`.
- Required and observed starting local/origin HEAD: `27f82fe201361d8c57307d971111faef3b8950f0`.
- During the long workflow, origin advanced through unrelated accepted website handoffs `b5947d7` and `72f97db`; those changes were not authored or staged by this work order. The dedicated handoff commit is based on the then-current shared head and changes only this file.
- The inherited dirty/untracked worktree and all prior evidence were preserved.

### Preflight, dispatch, and run identity

The latest pre-existing run was successful push run `32977106643` at `aa523803da12636a28eb0a5a22681e5e7a2b101d`, created `2026-08-26T13:55:22Z` and updated `2026-08-26T14:05:01Z`. No same-ref run was queued, pending, waiting, or in progress at the boundary.

Exactly one command was issued at `2026-08-26T14:12:33.5097600Z`:

```text
gh workflow run appliance.yml --ref rc/0.3.11-validation -f base_iso_url=https://releases.ubuntu.com/noble/ubuntu-24.04.4-live-server-amd64.iso -f base_iso_sha256=e907d92eeec9df64163a7e454cbc8d7755e8ddc7ed42f99dbc80c40f1a138433 -f offline_validation_mode=two-pass
```

Post-boundary reconciliation found exactly one new candidate: run `32978848891`, `workflow_dispatch`, attempt `1`, head `27f82fe201361d8c57307d971111faef3b8950f0`, created/started `2026-08-26T14:12:35Z`, completed `2026-08-26T15:47:37Z`, conclusion `failure`. URL: `https://github.com/tekmyster/Hoardarr/actions/runs/32978848891`. Both pass logs record the locked base URL/SHA and `HOARDARR_OFFLINE_DIAGNOSTIC_MODE: false`.

### Jobs, fixed execution contract, and timestamps

| Job | ID | Started | Completed | Conclusion | Decisive evidence |
|---|---:|---|---|---|---|
| shared build | `98210171762` | `2026-08-26T14:12:40Z` | `2026-08-26T14:22:39Z` | success | repository build/verify, appliance build, boot checkpoint, appliance upload, and install-input retention all succeeded |
| `offline-install (pass-1)` | `98213693573` | `2026-08-26T14:22:42Z` | `2026-08-26T15:46:52Z` | failure | validation ISO step succeeded; product step `14:59:43Z`-`15:44:44Z`; timeout terminated QEMU; evidence upload succeeded |
| `offline-install (pass-2)` | `98213693598` | `2026-08-26T14:22:42Z` | `2026-08-26T15:47:36Z` | failure | validation ISO step succeeded; product step `15:00:15Z`-`15:45:15Z`; timeout terminated QEMU; evidence upload succeeded |

The source-locked ordinary argv for each pass is `qemu-system-x86_64` with the harness `common` array, `-boot d -cdrom <validation-iso> -no-reboot -serial file:<pass>/installer-serial.log`, under `timeout --signal=TERM --kill-after=30s 45m`. The common array specifies 4096 MiB, 4 vCPUs, `-nic none`, no display, one fresh 32-GiB QCOW2 with serial `HOARDARR-OS-DISK`, and two 64-MiB raw `readonly=on` devices with serials `HOARDARR-PROTECTED-ONE` and `HOARDARR-PROTECTED-TWO`. The first-boot command retains the 15-minute bound but was never entered. Ordinary early failure occurs before `run.json`, so the selected `tcg` versus `kvm` accelerator and fully expanded runtime argv are not independently retained and are not claimed.

### Artifact identity and one-request destinations

The destination root `C:\Users\dmessana\Documents\troubleshooting\Hoardarr\.codex-temp\f5a-32978848891` was absent, resolved inside the repository, had `932,537,311,232` free bytes, passed a write/read/delete canary, and contained no pass evidence before download.

| Artifact | ID | API bytes | API digest | Created / expires | Disposition |
|---|---:|---:|---|---|---|
| `hoardarr-offline-pass-1` | `9613034057` | `2,872,874,958` | `sha256:057b8996d45a7cbee48f3bc69ce7c62ae8fc28550ed61d2a1a7442ab2c58eb4a` | `2026-08-26T15:46:48Z` / `2026-09-09T15:44:44Z` | requested once; extracted under `...\pass-1` |
| `hoardarr-offline-pass-2` | `9613039611` | `2,894,507,465` | `sha256:dc383cba8bfbbe0ea93fc4e3f67d3da18f8be408e5d53860ee83849ae9d8cfcd` | `2026-08-26T15:47:33Z` / `2026-09-09T15:45:16Z` | requested once; extracted under `...\pass-2` |
| `hoardarr-offline-install-inputs` | `9610974833` | `1,072,360,506` | `sha256:ff57248c3259d32555dcb7749e336a56f7a6abfcb65b1b0153effad9a18653f1` | `2026-08-26T14:22:35Z` / `2026-08-29T14:22:24Z` | metadata only; not downloaded |
| `hoardarr-appliance` | `9610967145` | `4,408,876,657` | `sha256:f69900ce1da09681a8c6b51e519215e1b80be9a2803b4920b6ca2181f01350f5` | `2026-08-26T14:22:24Z` / `2026-11-24T14:12:36Z` | metadata only; not downloaded |

Both pass API records name run `32978848891`, branch `rc/0.3.11-validation`, and exact source head `27f82fe...`. `gh run download` extracts without retaining the transport ZIP; therefore the API ZIP digest cannot be independently recomputed without violating the one-request constraint. The extracted bytes were independently hashed as follows.

### Complete extracted-file hashes

| Pass | File | Bytes | SHA-256 |
|---|---|---:|---|
| 1 | `hoardarr-offline-test.iso.sha256` | `128` | `90de7dd4d773e52a78f52c8b460b4a64dad5c23c204cd13d4558054ee909a8ca` |
| 1 | `hoardarr-offline-test.iso.tree-sha256` | `248,173` | `823ac12050b023ea3da9149e8a76b9665b582c7c0ecced56ae4724bb7f5d5dd0` |
| 1 | `installer-serial.log` | `248,683` | `ca6aa8c2b6e7ae8015eabd3b92529a580cfb72531b0137c9ee5f4f17a8c80f60` |
| 1 | `os.qcow2` | `4,948,033,536` | `99c5eba03ba7c40d2b0ef5b5f2957dd51b2b8fddfe223656c3a09da5486eb17f` |
| 1 | `protected-before.sha256` | `288` | `148b035bf33e3158a8fd3adad804f032ac295df2a8e05c67b024b09cbb2f7116` |
| 1 | `protected-one.raw` | `67,108,864` | `ba6170c17c667a8cfb9577349163cae578fd358b5da5d5be1859ee93da52bcae` |
| 1 | `protected-two.raw` | `67,108,864` | `e9c5cda034df938e8b4e37f8520424aa3ab36aa6078e2c1f12e85f996d5d3cc8` |
| 2 | `hoardarr-offline-test.iso.sha256` | `128` | `2542eb875aa962a59bacd482f8a89b628308523febfa68bf1fe19b6e08a342a7` |
| 2 | `hoardarr-offline-test.iso.tree-sha256` | `248,173` | `823ac12050b023ea3da9149e8a76b9665b582c7c0ecced56ae4724bb7f5d5dd0` |
| 2 | `installer-serial.log` | `248,683` | `73be2125251e28b8d5941fffcbfbbc4b474a591ad08111180bf544e5c5e93387` |
| 2 | `os.qcow2` | `5,045,747,712` | `94072a3868d37880da65cbd2f3d6475c9f02b90d7e3f8cd020e5e64ead4143aa` |
| 2 | `protected-before.sha256` | `288` | `7526781c2369021a18489ec9c75f99b2745dc95ae7efc8c399a9b8f823197c1e` |
| 2 | `protected-one.raw` | `67,108,864` | `ba6170c17c667a8cfb9577349163cae578fd358b5da5d5be1859ee93da52bcae` |
| 2 | `protected-two.raw` | `67,108,864` | `e9c5cda034df938e8b4e37f8520424aa3ab36aa6078e2c1f12e85f996d5d3cc8` |

Each ISO tree manifest contains `1,944` strict SHA-256/path rows, `1,944` unique safe relative paths, no malformed/duplicate/traversal entry, and the two manifests are byte-identical. The ISO byte receipts identify independent rebuilds (`f7b3b1aa7f520d856dcfc55987070967a806577523164acf9d6ba454ed40c578` for pass 1 and `dddb6e5bdd2832f821c12f5cde829853469d8a7048ea66cf114522a2bd5ec1ad` for pass 2). The pass artifacts do not retain the validation ISO itself, so the receipts/tree entries cannot be rehashed against ISO bytes. Neither artifact contains the final recursive `SHA256SUMS`; this is an acceptance failure, not silently treated as coverage.

### Exact payload failure and package evidence

The accepted parser independently returns its complete-nonzero classification (`10`) for both serial logs:

| Pass | Status | Target bytes / SHA-256 | Console bytes / SHA-256 | Transform |
|---|---:|---|---|---|
| 1 | `100` | `248,485` / `d0b00a033b9499ef067ffa5dc53a09ebcd100296eccbaf0ad29b75c39938a799` | `248,683` / `ca6aa8c2b6e7ae8015eabd3b92529a580cfb72531b0137c9ee5f4f17a8c80f60` | none |
| 2 | `100` | `248,485` / `f177a891843dcc2207f45a56718271a959d716383a5aa3f0cd33047801d5ccb0` | `248,683` / `73be2125251e28b8d5941fffcbfbbc4b474a591ad08111180bf544e5c5e93387` | none |

Both captures show the former F4C error count is zero, HTTP/HTTPS references are zero, and `283` acquisitions originate from `file:/opt/hoardarr/offline-repository`. Both unpack the complete accepted systemd-family transition to `255.4-1ubuntu8.17`, plus `linux-generic`, `linux-image-generic`, and `linux-headers-generic` at `6.8.0-138.138`. The first decisive retained failure in each is:

```text
Setting up pcp (6.2.0-1.1build4) ...
/proc/ is not mounted. This is not a supported mode of operation.
...
Failed to preset unit, unit /etc/systemd/system/pmcd.service is masked.
dpkg: error processing package pcp (--configure):
 installed pcp package post-installation script subprocess returned error exit status 1
```

The same transaction later reports `/dev/pts`, `/proc/mounts`, `/proc/cmdline`, `/proc/swaps`, and `/proc/cpuinfo` unavailable, then `grub-probe: error: cannot find a device for /` and a second configuration failure for `linux-image-6.8.0-138-generic`. It ends with `E: Sub-process /usr/bin/dpkg returned an error code (1)`, exact payload exit `100`, and capture complete. This proves the actual-install acquisition correction worked and exposes a new missing target-chroot runtime-mount/preparation defect. It does not prove final closure.

### QCOW2 and protected-media readback

Windows QEMU 11.1 independently reported:

| Pass | Virtual bytes | Actual/file bytes | Dirty / corrupt | Allocated / fragmented clusters | Check |
|---|---:|---:|---|---|---|
| 1 | `34,359,738,368` | `4,948,033,536` | false / false | `75,468` / `5,909` | exit `0`, `check-errors=0` |
| 2 | `34,359,738,368` | `5,045,747,712` | false / false | `76,959` / `6,268` | exit `0`, `check-errors=0` |

The images have distinct file sizes, allocation/fragmentation counts, and SHA-256 identities, consistent with independent fresh state; byte identity is not used as an independence criterion.

For each pass, independent hashes of `protected-one.raw` and `protected-two.raw` exactly equal the two entries in that pass's `protected-before.sha256`. This proves the retained marker bytes did not drift. Because the ordinary harness exits immediately at the failed installer bound, `protected-after.sha256` and `protected-diff.txt` are absent, so the full in-run gate remains failed.

### Missing acceptance receipts

Both artifacts contain only the seven files listed above. They do not contain `run.json`, first-boot serial, installed-package/root/family receipts, final APT/dpkg audit, service-state/readiness evidence, runner-generated QEMU check, protected-after/diff, or final recursive `SHA256SUMS`. These facts remain unproven rather than inferred from the clean retained images or source configuration.

## Defects

The deterministic release-gate defect is now target-chroot preparation for real package configuration. F5 successfully permits local signed-repository acquisition into an empty cache, but the production payload's actual-install chroot does not provide the mounted `/proc`, `/sys`, `/dev`/`/dev/pts`, and root-device context required by package maintainer scripts. Both independent installs therefore fail identically at `pcp` and later the kernel hook, while Subiquity remains at its error state until the unchanged outer bound expires.

The ordinary failure path's lack of finalized run/argv/accelerator, protected-after, QEMU-check, and recursive checksum receipts remains an evidence limitation. It is not the first product failure because the exact complete payload captures identify the package-configuration defect.

## Blockers

- **C1:** blocked by the reproducible target-chroot pseudo-filesystem/device preparation defect during actual package configuration.
- **OWNER-10:** independently blocked by the unimplemented/unvalidated LINSTOR, DRBD 9, DRBD Reactor, installed-but-disabled LINSTOR Gateway, kernel/Secure-Boot, and offline Proxmox-plugin sidecar closure.

## Next action

Authorize one narrow source-correction work order to reproduce the retained failure in a disposable target root and correct only the production payload's target-chroot mount/runtime preparation and guaranteed cleanup. It must preserve the signed local-only APT source, exact 109 roots and both compatibility families, service-start denial, read-only protected disks, and all accepted F1/F2/F3/F4/F5 safeguards; add executable actual-install tests covering `pcp` and kernel maintainer scripts; then require one fresh ordinary two-pass run. Do not change package roots, network policy, timeouts, VM geometry, or begin OWNER-10 as part of that correction.
