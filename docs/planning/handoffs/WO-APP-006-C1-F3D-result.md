# WO-APP-006-C1-F3D result

## Result

- **Disposable trust-path correction: PASS.** The artifact key was verified before the trust write, atomically installed at the unchanged source list's fixed `signed-by` path inside only the fresh overlay, and reread with the locked size, SHA-256, fingerprint, owner and mode.
- **Clean APT update: PASS.** The sole `apt-get update` exited `0`, emitted no `W:`, `E:` or `Err:` line, accepted the fresh signed `InRelease`, wrote fresh list files, and exposed all eleven systemd-family candidates at `255.4-1ubuntu8.17`.
- **Retained-base simulation: FAIL / NOT EXECUTED.** The fail-closed evidence script exited `141` while validating the first candidate. Under `set -o pipefail`, its `awk` exited after the first `Candidate:` line and caused upstream `apt-cache policy` to receive SIGPIPE. Execution stopped before simulation argv generation. No simulation and no package install occurred; F3D was not retried.
- **F3: FAIL / pending one solver preflight.** Repository trust and candidate closure are now proven, but the required 109-root transaction was not executed.
- **C1: FAIL.** F3D did not authorize or run pass 1/pass 2.
- **OWNER-10: FAIL.** No LINSTOR, DRBD 9, DRBD Reactor, LINSTOR Gateway, kernel/Secure-Boot, or Proxmox-sidecar work was performed.

No artifact was downloaded or extracted, no ISO was rebuilt, no source/product/test/workflow was changed, no Actions workflow was dispatched, no actual package install was attempted, and no live or protected media was attached or changed.

## Evidence

### Identity and reused inputs

- Work order SHA-256: `18953f5d38235ed7de63a1606bc37a8a940e12fda2b15ba8ea979baf2e66fdbf`.
- Required and observed starting local/origin HEAD: `adb399f3d4aeed314d9eb2ce14cddd628cc142eb`.
- F3C handoff SHA-256: `249ae986d648b0d74272b34c5c5650a49d9bab45eb5b867edd010e37b519d05b`.
- Reused archive: `1,054,964,607` bytes; SHA-256 `87c90870111cb81cb2aefc262874de188a50c662cbe4c0d03fe42780db42929a`.
- Reused ISO: `1,023,664,128` bytes; SHA-256 `631c6257fc6332b2235ce917319a0731fdb65465b75163c9f26937538e56a59b`.
- Reused repository: `1,066` files / `1,022,338,561` bytes; `SHA256SUMS` SHA-256 `ac133ac9e8401cf0b5c8d82333b3ce743541cc1700c1be9446e7d6f1b3e258e9`; `526` package identities; `109` unique roots; one eleven-member family at `255.4-1ubuntu8.17`.
- Artifact key: `965` bytes; SHA-256 `ae0b5f724cc3036196e14ea828028cc5b67fe3c6f900631a52a897f1a7c80b5b`; fingerprint `6AC7E77D10C48333260B2CDD1495B2CD95543BF5`.
- Corrected kernel: `vmlinuz-6.8.0-100-generic`, `15,030,664` bytes, SHA-256 `528d909745819a1464848a2c4d91c609db4f54b33ccae7069aad4178fb34606f`.
- Corrected initrd: `initrd.img-6.8.0-100-generic`, `74,664,884` bytes, SHA-256 `8e5094cfcc9cc0d6790a38efdc19521dca4a394a34ddbba48608c211e580e474`.

All identities were recomputed before creating `.codex-temp\f3d-32953633660` and again after shutdown. F3D reused the archive, extracted tree, and ISO in place; it did not download, extract, copy, or rebuild them.

### Retained source and QEMU topology

The retained F2 backing before and after was unchanged:

- path: `.codex-temp\f2-32943411481\pass-2\offline-evidence\pass-2\os.qcow2`;
- physical/virtual size: `3,968,401,408` / `34,359,738,368` bytes;
- SHA-256: `2393e9b4c90aeaf56a580634b3538defd0c3af26fa30c3a3846654962e3ba60d`;
- dirty/corrupt: false/false;
- `qemu-img check`: exit `0`, `check-errors=0` before and after.

The new overlay was created once with the retained F2 image as its direct backing file. After shutdown:

- physical/virtual size: `1,052,246,016` / `34,359,738,368` bytes;
- SHA-256: `e7d82800bf3970006ce7754fb0bedaf9c95380a215d7bd3d27b446c169381fe7`;
- dirty/corrupt: false/false;
- `qemu-img check`: exit `0`, `check-errors=0`.

QEMU `11.1.0` used TCG, 4 GiB, two vCPUs, the corrected named kernel/initrd, `root=/dev/vda2 rw init=/bin/bash console=ttyS0`, and explicit `-nic none`. Its only drives were the writable F3D overlay and the existing F3C ISO with `media=cdrom,readonly=on`. The complete argv SHA-256 is `f7de7d889ec944fe073b6e192000c2915a32393682adac27a201d5b23c57ed6c`; protected-media name matches: `0`. Guest evidence showed exactly `lo`, down.

The guest mounted the existing ISO read-only and replaced only `/opt/hoardarr/offline-repository` in the disposable overlay. Source and destination strict `SHA256SUMS` logs are each `51,966` bytes and each has SHA-256 `973ee1a0694c05596a701329790501599cc7678e5388143d63de2e8b4e135344`. Both tree manifest hashes were `ac133ac9e8401cf0b5c8d82333b3ce743541cc1700c1be9446e7d6f1b3e258e9`.

### Atomic trust-path correction

The source list remained byte-identical at SHA-256 `d6d07fcb69b31aafdbfaefea74a510e80bba130c60a1a1ac6c4727ce2d7eae1d`:

```text
deb [signed-by=/usr/share/keyrings/hoardarr-offline-archive-keyring.gpg] file:/opt/hoardarr/offline-repository noble main
```

Prior disposable keyring:

```text
size=965
sha256=b65607cc0f26a5a984c810d7ff7aa54e0df1fae5bed47549f3808eff62ed7086
fingerprint=0D6ACDF768E2CEF201857FAA06187A791E5DB2A8
```

F3D verified the source key's locked size, hash, and fingerprint before writing. It copied it to a temporary sibling, set `root:root` and `0644`, reread all identities, fsynced the temporary file, atomically renamed it with `mv -fT`, fsynced `/usr/share/keyrings`, then reread the final object:

```text
identity=0:0:644:965
sha256=ae0b5f724cc3036196e14ea828028cc5b67fe3c6f900631a52a897f1a7c80b5b
fingerprint=6AC7E77D10C48333260B2CDD1495B2CD95543BF5
```

The prior/new key evidence hashes are respectively `d032c151e9e75e60172f7ff009dd6075e406972b7050c8b91bc5ba823d0f295d` and `c9bd973789eacfe2fa6644f95bbdd850fcd5f5dd4b688f315419e83e389f94fa`.

### One clean APT update

Exact argv:

```text
apt-get -o Dir::Etc::sourcelist=/etc/apt/sources.list.d/hoardarr-offline.list -o Dir::Etc::sourceparts=- -o Acquire::Languages=none -o Acquire::Retries=0 -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false update
```

- argv SHA-256: `f210d6bc4c21b799fdc7210aeeaef36a0be2c7e6befebf0e7588f8336fe3b573`;
- status: `0`;
- log: `242` bytes, SHA-256 `c7ee142b40b0441f316b0fed4869cf3aefbdef76f2746309ef058eab93081895`;
- warning/error/signature-failure lines: `0`;
- fetched: fresh `InRelease` (`1,802` bytes) and `main amd64 Packages` (`239 kB` displayed by APT; retained list `717,558` bytes).

Fresh list evidence was newer than the update start and is hashed by `fresh-lists.txt` SHA-256 `9ae4bb5962ecd6febcb8b9b40f0c82be14c1da60f1680b5066229256b8dbe347`.

The complete family policy log is `2,822` bytes with SHA-256 `4d3cb26ff2c0b876412f0b766b44a3893ce188d7e50c2ab0837298e2c1537c76`. It contains exactly eleven `Candidate: 255.4-1ubuntu8.17` entries for:

`systemd`, `systemd-sysv`, `systemd-timesyncd`, `systemd-resolved`, `udev`, `libudev1`, `libsystemd0`, `libsystemd-shared`, `libpam-systemd`, `libnss-systemd`, and `systemd-dev`.

This proves the formerly missing four are now visible from the authenticated corrected index.

### Simulation stop

After writing the full policy evidence, the script checked each candidate with:

```bash
candidate=$(apt-cache "${apt_opts[@]}" policy "$p" | awk '/Candidate:/ {print $2; exit}')
```

Because the script used `set -Eeuo pipefail`, `awk`'s early exit closed the pipe and caused `apt-cache` to terminate with SIGPIPE (`128 + 13 = 141`). The fail-closed trap recorded script status `141`, synchronized, retained the evidence archive, and powered off.

The following files were never created, proving the simulation gate was not reached:

- `simulation-argv.txt`;
- `simulation-status.txt`;
- `apt-simulation.log`;
- `action-counts.txt`;
- `planned-family.txt`.

Therefore there is no simulation argv, status, action count, output hash, or family plan to report. No `apt-get --simulate` or actual `apt-get install` process ran. The guest evidence archive is `16,282` bytes with SHA-256 `80391ceda4615437f101896f5880984be76995d3db6908efdf269ccb069585ef`; serial SHA-256 is `4b6f3c2cf9fdd778f48ba93097a891aa255af6f812d9a04b339aabd0053ca984`.

## Defects

1. The F3D evidence harness used an early-exit `awk` pipeline under `pipefail`; valid candidate output produced SIGPIPE status `141` and stopped the run before the authorized simulation.
2. F3 remains unverified against the retained base because no solver transaction ran.
3. C1 and OWNER-10 remain failed independently.

The product repository, trust path, and APT candidate closure showed no defect in this run.

## Blockers

- **F3:** blocked only on one fresh-overlay solver preflight whose candidate validation consumes complete command output without SIGPIPE, followed by the single unchanged 109-root simulation.
- **C1:** remains FAIL; F3D authorizes no two-pass run.
- **OWNER-10:** remains FAIL independently.

## Next action

Authorize one replacement disposable preflight reusing the same verified F3C inputs. Preserve the proven atomic key procedure and single clean update, but validate candidates from the already-written complete `family-policy.txt` (or otherwise consume full `apt-cache` output) without an early-close pipeline. If all eleven candidates match, execute exactly one unchanged 109-root simulation. Do not change source, download/rebuild inputs, install packages, or dispatch CI.
