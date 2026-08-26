# WO-APP-006-C1-F7A result

## Result

- **Dispatch uniqueness: PASS.** At `2026-08-26T17:43:01.1390269Z`, workflow `340044321` (`Build appliance ISO`, `.github/workflows/appliance.yml`) was active, there were zero queued/in-progress same-ref runs, and there was no prior manual dispatch at the locked head. Exactly one dispatch was issued at `2026-08-26T17:43:22.8755034Z`; it created run `32995803401`, attempt `1`. There was no retry, rerun, cancellation, diagnostic run, or second dispatch.
- **Exact source and inputs: PASS.** The run used branch `rc/0.3.11-validation`, exact head `ca373c6663a8560e90861bc46f88f52b8f428bb3`, Ubuntu `24.04.4` URL `https://releases.ubuntu.com/noble/ubuntu-24.04.4-live-server-amd64.iso`, SHA-256 `e907d92eeec9df64163a7e454cbc8d7755e8ddc7ed42f99dbc80c40f1a138433`, `offline_validation_mode=two-pass`, and `HOARDARR_OFFLINE_DIAGNOSTIC_MODE=false` in both pass jobs.
- **Shared build: PASS.** Job `98264575967` completed successfully.
- **Pass 1 install: FAIL.** Job `98267400487` ran the unchanged product step from `2026-08-26T17:57:41Z` through `18:42:41Z`, reached the exact 45-minute bound, and did not reach its reboot checkpoint. The complete retained payload exits `100` after `pcp` package configuration fails.
- **Pass 1 first boot/final readback: FAIL / not reached.** No first-boot, readiness, package-finalization, or final protected-media receipts exist.
- **Pass 2 install: FAIL.** Job `98267400605` independently ran the product step from `2026-08-26T17:57:01Z` through `18:42:02Z`, reached the same exact 45-minute bound, and did not reach its reboot checkpoint. Its complete retained payload independently exits `100` at the same `pcp` failure.
- **Pass 2 first boot/final readback: FAIL / not reached.** No first-boot, readiness, package-finalization, or final protected-media receipts exist.
- **Signed-local/no-network APT: PASS for acquisition; FAIL for final acceptance.** Each pass records `283` acquisitions from `file:/opt/hoardarr/offline-repository`, zero HTTP/HTTPS package acquisitions, and no former relative-path failure. Final APT validation was not reached.
- **Exact 109 roots and compatibility families: FAIL for final acceptance.** Both logs show the accepted systemd `255.4-1ubuntu8.17` and linux-meta `6.8.0-138.138` transaction progressing, but the final 109-root/family receipts are absent after `dpkg` fails.
- **dpkg/APT audit: FAIL / not reached.** `dpkg` ends with error code `1`; final `dpkg --audit` and APT checks were not produced.
- **Service/mask/alias state: FAIL.** Runtime mounts eliminated the former `/proc` failure, but a temporary `/dev/null` unit mask makes `deb-systemd-helper preset` fail for `pmcd.service`; the `pcp` post-installation script returns `1`. Final cleanup/disabled/inactive/alias readback is not reached.
- **Storage activation guards: FAIL for runtime acceptance / not reached.** Source safeguards remain unchanged, but final MD/multipath/LVM state receipts do not exist.
- **Readiness: FAIL / not reached.** Neither pass entered first boot or produced Hoardarr readiness evidence.
- **Artifact one-download integrity: PASS with transport limitation.** Each uniquely named pass artifact was requested exactly once into a new validated destination. No appliance/install-input artifact was downloaded. GitHub's API ZIP digests are recorded; `gh run download` extracts without retaining the transport ZIP, so those ZIP digests were not independently recomputed. Every extracted file was independently sized and SHA-256 hashed.
- **QCOW2 read-only checks: PASS for retained-image integrity.** QEMU `11.1.0` reports both distinct 32-GiB QCOW2 files clean, with zero check errors and no dirty/corrupt flag. SHA-256 before/after inspection is unchanged.
- **Protected-media evidence: PASS for retained marker bytes; FAIL for the complete in-run gate.** Both retained raw markers match their respective before manifests exactly. Because installation stopped before finalization, `protected-after.sha256` and `protected-diff.txt` are absent.
- **Cross-pass independence: PASS for fresh retained state; FAIL for product acceptance.** Images, ISO receipts, serial hashes, allocation, and fragmentation differ between passes, while the ISO tree manifests are byte-identical and both reproduce the same product failure. One pass was not substituted for the other.
- **C1: FAIL.** Neither independent pass completed installation, reboot, first boot, or final acceptance.
- **Source/worktree scope: PASS.** No source/workflow/package/harness/VM-bound change was made. Inherited dirty/untracked work and prior evidence remain preserved.
- **OWNER-10: FAIL.** LINSTOR, DRBD 9, DRBD Reactor, installed-but-disabled LINSTOR Gateway, kernel/Secure-Boot, and offline Proxmox-plugin sidecar closure remain separate and unvalidated.

## Evidence

### Authority and dispatch

- Work-order SHA-256: `bd82ffb18a6446502b6d8324a9f3eb40abe3aadbcbc76d252597470e2b1e8612`.
- F7 handoff SHA-256: `f1c936b171df4af6101a8f70b1270bfcf0af8d9bbd465f8dfaa335ced59fc4d4`.
- Required and observed starting local/origin head: `ca373c6663a8560e90861bc46f88f52b8f428bb3`.
- Run: `32995803401`, event `workflow_dispatch`, attempt `1`, created `2026-08-26T17:43:24Z`, completed `18:44:48Z`, conclusion `failure`.
- URL: `https://github.com/tekmyster/Hoardarr/actions/runs/32995803401`.

The one authorized command was:

```text
gh workflow run appliance.yml --ref rc/0.3.11-validation -f base_iso_url=https://releases.ubuntu.com/noble/ubuntu-24.04.4-live-server-amd64.iso -f base_iso_sha256=e907d92eeec9df64163a7e454cbc8d7755e8ddc7ed42f99dbc80c40f1a138433 -f offline_validation_mode=two-pass
```

### Jobs

| Job | ID | Started | Completed | Result |
|---|---:|---|---|---|
| shared build | `98264575967` | `17:43:29Z` | `17:52:55Z` | success |
| offline-install pass 1 | `98267400487` | `17:52:59Z` | `18:44:48Z` | failure |
| offline-install pass 2 | `98267400605` | `17:52:59Z` | `18:43:52Z` | failure |

Both job logs retain the locked base URL/SHA and `HOARDARR_OFFLINE_DIAGNOSTIC_MODE: false`. The product steps report QEMU termination by the unchanged timeout and `offline installer did not reach its bounded reboot checkpoint`. The accepted harness at this exact source uses `-nic none`, one fresh 32-GiB OS image, and two `readonly=on` protected marker devices. Ordinary early failure occurs before `run.json`, so the expanded runtime argv/accelerator receipt is absent and is not inferred as final evidence.

### Artifacts and single-download destinations

Validated root: `C:\Users\dmessana\Documents\troubleshooting\Hoardarr\.codex-temp\f7a-32995803401`. It was absent before use, resolved under the repository `.codex-temp` directory, had `952,162,967,552` free bytes, and passed write/read/delete canary validation.

| Artifact | ID | API bytes | API digest | Download disposition |
|---|---:|---:|---|---|
| `hoardarr-offline-pass-1` | `9618683340` | `2,870,559,933` | `sha256:2b0d91aaa4234d6af929dbb37509a89125a254642da4c2ad91e0ba72ce45c924` | exactly once to `pass-1` |
| `hoardarr-offline-pass-2` | `9618647698` | `2,909,877,480` | `sha256:35bd204c774d2d4fd2375184546624dafff255a9496c201348842fe722a4c330` | exactly once to `pass-2` |
| `hoardarr-offline-install-inputs` | `9616773051` | `1,072,360,527` | `sha256:79d72f111883590650c2ac530022529bb632d6ad4be307b403cdbc6d9543fa46` | metadata only; not downloaded |
| `hoardarr-appliance` | `9616766771` | `4,408,883,714` | `sha256:f8406cbbbaf40651d652d4476c9230e1650493e351127366a344e6bf3ce66b46` | metadata only; not downloaded |

Original extracted artifacts contain seven files each. They do not contain `run.json`, first-boot serial, final package/root/family receipts, final APT/dpkg audit, service/readiness receipts, protected-after/diff, runner QEMU-check output, or a final recursive `SHA256SUMS`.

### Extracted-file identities

| Pass | File | Bytes | SHA-256 |
|---|---|---:|---|
| 1 | ISO receipt | `128` | `df2f0cccb7d3d4c0806d70d33abc03cc1010424dc3e22e1e2040a5c974421409` |
| 1 | ISO tree manifest | `248,173` | `3e1c789b0b68201fa7234720e2837dd90149a29954c23446b89eab1089f9c2cd` |
| 1 | installer serial | `248,424` | `3422b6003014e4f4900f20c53759c6d8b029b8efd31b75246b254a89622fd934` |
| 1 | OS QCOW2 | `4,887,937,024` | `abe2ceea39ee82037949c9ff6726c7266055ae1704f19931b9bc5469b0a9035b` |
| 1 | protected-before | `288` | `148b035bf33e3158a8fd3adad804f032ac295df2a8e05c67b024b09cbb2f7116` |
| 1 | protected one | `67,108,864` | `ba6170c17c667a8cfb9577349163cae578fd358b5da5d5be1859ee93da52bcae` |
| 1 | protected two | `67,108,864` | `e9c5cda034df938e8b4e37f8520424aa3ab36aa6078e2c1f12e85f996d5d3cc8` |
| 2 | ISO receipt | `128` | `797192938b864024417997bb9b0ebe588d984654999e1d5653ea1a0cd076a07b` |
| 2 | ISO tree manifest | `248,173` | `3e1c789b0b68201fa7234720e2837dd90149a29954c23446b89eab1089f9c2cd` |
| 2 | installer serial | `248,424` | `6a28c267057c9a4a2540d314e6caf1c6e1785a482c6aa61fde70f4aa9cafcb58` |
| 2 | OS QCOW2 | `5,067,243,520` | `65cfc284a972bb21422d820f7a2f3ab1daafe4eb3f65af8d2171e6959eab976a` |
| 2 | protected-before | `288` | `7526781c2369021a18489ec9c75f99b2745dc95ae7efc8c399a9b8f823197c1e` |
| 2 | protected one | `67,108,864` | `ba6170c17c667a8cfb9577349163cae578fd358b5da5d5be1859ee93da52bcae` |
| 2 | protected two | `67,108,864` | `e9c5cda034df938e8b4e37f8520424aa3ab36aa6078e2c1f12e85f996d5d3cc8` |

Each tree manifest has `1,944` strict rows, `1,944` unique safe relative paths, and zero malformed, duplicate, or traversal rows. The manifests are byte-identical. The independent ISO receipts name different ISO hashes: pass 1 `9288e15b7e2f9d8f039cca783b2ff14dd32002333b772d92fb52886508b86679`; pass 2 `fe51158bc65e98005a71e6e3f203e5c2d4b5436578d455ef57f5fd3aec5d0ce4`. The ISO bytes themselves are not retained in pass artifacts, so these receipts cannot be rehashed locally.

### Exact payload failure

The accepted parser returns its complete-nonzero classification (`10`) for each serial capture:

| Pass | Payload | Target bytes / SHA-256 | Console bytes / SHA-256 | Transform |
|---|---:|---|---|---|
| 1 | `100` | `248,226` / `7d59842de3e520edcea7d1f71f4c45389abb3024109058b46987bfbdcee5c03e` | `248,424` / `3422b6003014e4f4900f20c53759c6d8b029b8efd31b75246b254a89622fd934` | none |
| 2 | `100` | `248,226` / `cb31401011429aedd8d34749b58c3ee37a77471350efb9e53cd856f58c16dabb` | `248,424` / `6a28c267057c9a4a2540d314e6caf1c6e1785a482c6aa61fde70f4aa9cafcb58` | none |

Each capture has exactly one begin/end/exit/capture-complete sequence. Both have zero `/proc/ is not mounted` occurrences, proving the accepted F7 runtime-mount correction reached the real package transaction. The first decisive package failure is identical:

```text
Setting up pcp (6.2.0-1.1build4) ...
Failed to preset unit, unit /etc/systemd/system/pmcd.service is masked.
/usr/bin/deb-systemd-helper: error: systemctl preset failed on pmcd.service: No such file or directory
dpkg: error processing package pcp (--configure):
 installed pcp package post-installation script subprocess returned error exit status 1
```

The transaction later finishes kernel hooks successfully enough to regenerate initramfs/GRUB, but terminates with `Errors were encountered while processing: pcp` and `E: Sub-process /usr/bin/dpkg returned an error code (1)`. `policy-rc.d` evidence separately shows service starts being denied with status `101`; the failure is the preset/mask interaction, not an observed service start.

### QEMU 11.1 and protected markers

Pinned tool: `qemu-img version 11.1.0 (v11.1.0-12130-ge470268ff4)`.

| Pass | Virtual bytes | Actual bytes | Dirty/corrupt | Allocated/fragmented clusters | Check | SHA before/after |
|---|---:|---:|---|---:|---|---|
| 1 | `34,359,738,368` | `4,887,937,024` | false/false | `74,551` / `5,444` | exit `0`, errors `0` | unchanged `abe2ceea...035b` |
| 2 | `34,359,738,368` | `5,067,243,520` | false/false | `77,287` / `6,122` | exit `0`, errors `0` | unchanged `65cfc284...976a` |

In each pass, the independent protected-one/protected-two hashes exactly equal both entries in that pass's `protected-before.sha256`. No image was booted, attached, mounted, repaired, converted, resized, or compacted locally.

## Defects

The deterministic product defect is now the package-install service guard's interaction with maintainer-script preset behavior. The production payload creates temporary `/dev/null` masks for denied units before the real transaction. `pcp` treats `deb-systemd-helper preset pmcd.service` failing against that mask as fatal even though `policy-rc.d` separately denies service starts. Both independent installs therefore stop at `pcp`, and Subiquity waits until the unchanged outer bound.

The early ordinary failure path also leaves final run/argv/accelerator, first-boot, package audit, service/readiness, protected-after/diff, and recursive checksum receipts absent. These are reported as unproven rather than inferred from source or clean retained images.

## Blockers

- **C1:** blocked by the reproducible temporary-mask/preset failure for `pmcd.service` during `pcp` configuration.
- **OWNER-10:** independently blocked by the unimplemented/unvalidated LINSTOR, DRBD 9, DRBD Reactor, installed-but-disabled LINSTOR Gateway, kernel/Secure-Boot, and offline Proxmox-plugin sidecar closure.

## Next action

Authorize one narrow source/test correction for install-time service activation denial. It must reproduce the exact `pcp` post-install path with production functions, preserve `policy-rc.d` start denial, avoid presenting `pmcd.service` with a mask state that makes its package preset fatal, and still guarantee all denied units are disabled/inactive after successful package configuration. Preserve the accepted mask/alias safety rules for pre-existing objects, signed-local APT, 109 roots/families, runtime mounts, storage guards, no-NIC geometry, and all bounds. Run focused/automatic gates first; authorize a new two-pass run only after that correction is independently accepted.
