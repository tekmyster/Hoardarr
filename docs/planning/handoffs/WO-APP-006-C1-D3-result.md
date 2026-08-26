# WO-APP-006-C1-D3 result

## Result

- **D3 exact retained-guest diagnosis: PASS.** The unchanged production `apt-get --simulate --no-install-recommends install` transaction was reproduced from the retained F2 pass-2 filesystem with exit 100. Authoritative solver, dpkg, APT policy, repository `Packages`, and package-manifest evidence identifies the first conflict as an incomplete systemd-family version closure.
- **C1 ordinary two-pass baseline: FAIL.** D3 is diagnostic only and does not correct or rerun C1.
- **OWNER-10: FAIL.** D3 does not add or validate LINSTOR, DRBD 9, DRBD Reactor, LINSTOR Gateway, kernel/Secure-Boot integration, or the offline Proxmox-plugin sidecar.

No product, test, workflow, package policy, package manifest, unattended data, live system, A/B/Build appliance, network, credential, protected media, or retained source image was changed. No GitHub Actions workflow was dispatched.

## Evidence

### Identity and scope

- Work order SHA-256: `2b18d1ce3c5d3634d22a5d841ffc38ac905819fea61759bf1061485484e00565`.
- Required and observed starting local/origin HEAD: `5f268bbd85bfbfc9c022241fd8fe1e4fc5e0269a`.
- Accepted F2 implementation: `e6806c9d5b7668c92407759a0f6d0b485b7dc0c2`.
- Accepted F2 handoff: `5f268bbd85bfbfc9c022241fd8fe1e4fc5e0269a`; file SHA-256 `9b8d0d4bd34ac450cdc1670a8238d12d618ef53449d442bd67e9cdec3c8363ec`.
- Reused retained artifact only; it was not downloaded again:
  `C:\Users\dmessana\Documents\troubleshooting\Hoardarr\.codex-temp\f2-32943411481\pass-2`.
- D3 disposable area:
  `C:\Users\dmessana\Documents\troubleshooting\Hoardarr\.codex-temp\d3-f2-pass2`.
- Handoff evidence commit: the separate commit containing this document; its pushed SHA is reported in the Builder terminal response because a commit cannot contain its own identity.

### Local tools and derived topology

- Host: Windows PowerShell 7.
- QEMU/qemu-img: `11.1.0 (v11.1.0-12130-ge470268ff4)`.
- 7-Zip: `26.02 x64`.
- Retained source:
  `...\f2-32943411481\pass-2\offline-evidence\pass-2\os.qcow2`.
- Writable overlay:
  `...\d3-f2-pass2\overlay.qcow2`, created with `qemu-img create -f qcow2 -F qcow2 -b <exact-retained-source>`.
- Read-only extraction path used to obtain the retained guest's own kernel/initrd: source QCOW2 was converted to a disposable sparse raw derivative, partition 2 was extracted to a disposable derivative, and `vmlinuz-6.8.0-100-generic` / `initrd.img-6.8.0-100-generic` were extracted. The guest then booted the overlay directly with those retained files.
- Kernel SHA-256: `8e5094cfcc9cc0d6790a38efdc19521dca4a394a34ddbba48608c211e580e474`.
- Initrd SHA-256: `528d909745819a1464848a2c4d91c609db4f54b33ccae7069aad4178fb34606f`.

The effective QEMU topology was:

```text
retained os.qcow2 (backing, unchanged)
  -> disposable overlay.qcow2 (only writable disk)
  -> retained kernel/initrd, root=/dev/vda2, init=/bin/bash
  -> -machine accel=tcg -nic none -display none
  -> serial only on host loopback TCP
```

No protected raw image path was included in any QEMU argument. The guest reported only `lo`, down, under `/sys/class/net`; no guest NIC, route, DNS, proxy, mirror, or network package source was added.

The first Start-Process attempt failed before opening the disk because the quoted kernel append string was split and QEMU treated `rw` as a filename. QEMU exited with `Could not open 'rw'`; the corrected invocation quoted the single `-append` argument and booted only the existing overlay. This method correction did not change product state or the retained source.

### Exact production reconstruction

Inside the retained root, D3 used the production values without adding or removing roots:

```text
Dir::Etc::sourcelist=/etc/apt/sources.list.d/hoardarr-offline.list
Dir::Etc::sourceparts=-
Acquire::Languages=none
Acquire::Retries=0
Acquire::http::Proxy=false
Acquire::https::Proxy=false
DEBIAN_FRONTEND=noninteractive
LVM_SYSTEM_DIR=/opt/hoardarr-install/lvm-guard
NEEDRESTART_MODE=l
UCF_FORCE_CONFFOLD=1
apt-get <options> --simulate --no-install-recommends install "${exact_roots[@]}"
```

- Exact root count: `109`.
- Root list: 2,878 bytes; SHA-256 `80798f4c96f2a14f817f13fa967ed376401f59b1f6da3f6e395adf3422832b1e`.
- Repository `Packages`: 715,399 bytes; SHA-256 `9a5c259b2c0402ab32a7f7425dd6e98a755f2bbf728146c05c0fc6d586e64026`.
- Package manifest SHA-256: `feb74cd4e04a5950ec861af8629280627576d057aa5c9f69a7623621965831ff`.
- Architecture: `amd64`; no foreign architecture was reported.
- `apt-mark showhold`: empty, exit 0.
- `dpkg --audit`: empty, exit 0.

The unchanged generic simulation reproduced exit `100` and the same dependency summary seen in F2. The diagnostic simulation added only:

```text
Debug::pkgProblemResolver=yes
Debug::pkgProblemResolver::ShowScores=true
Debug::pkgDepCache::AutoInstall=yes
Debug::pkgDepCache::Marker=yes
```

It also exited `100`.

### Retained logs

All paths are below `.codex-temp\d3-f2-pass2`:

- `d3-generic.log`: 3,745 bytes; SHA-256 `3a93e7bf1da1be73b68ee4fb5ec3659992e52bab4a62cc1703a303e3bb0c6079`.
- `d3-resolver.log`: 175,618 bytes; SHA-256 `193de83c535f759b9942969637649d52151618cc3f0b9ac2e6c306f00e2362e9`.
- `d3-context.log`: 45,411 bytes; SHA-256 `936faa9415422cc1eb084b07f2584ccb31f4bc0fc6af52662805d35c93947342`.
- `d3-metadata.log`: 57,284 bytes; SHA-256 `063e061b9bc708f5449b4c14f9eda2df19e6fcd8b0acbed59b5ee51f7513bd61`.
- `boot2-serial-initial.log`: SHA-256 `5be1e8e3088884c2feec396ed1498f6eae1a4a8012446f1d15b91482789d420a`.
- `d3-shutdown-serial.log`: SHA-256 `0001cc65f384220b0716dd78db90c61fb3c741264b80813a1d0d3557f70054d8`.

The full resolver and metadata logs were transferred from the guest as base64 after suppressing kernel-console interleaving; their host hashes match the guest-recorded resolver hash. A first transfer was discarded because an asynchronous kernel timer message interrupted an unwrapped base64 stream; the simulation itself was not rerun for that transfer correction.

### First exact conflict

The first solver conflict is:

```text
installed systemd-sysv:amd64 255.4-1ubuntu8.12
  Depends: systemd (= 255.4-1ubuntu8.12)
  Pre-Depends: systemd

exact root udev:amd64 255.4-1ubuntu8.17
  Breaks: systemd (<< 255.4-1ubuntu8.17)

exact root systemd-timesyncd:amd64 255.4-1ubuntu8.17
  Depends: libsystemd-shared (= 255.4-1ubuntu8.17), systemd
```

The retained base has these installed at `255.4-1ubuntu8.12`: `systemd-timesyncd`, `udev`, `systemd`, `systemd-sysv`, `libsystemd0`, `libsystemd-shared`, `libpam-systemd`, `libnss-systemd`, `systemd-resolved`, `systemd-dev`, and `libudev1`.

The exact root list requests `systemd-timesyncd=255.4-1ubuntu8.17` and `udev=255.4-1ubuntu8.17`. The offline repository supplies candidates `systemd`, `libsystemd0`, `libsystemd-shared`, `systemd-dev`, and `libudev1` at `255.4-1ubuntu8.17`. It does **not** contain `systemd-sysv`, `libpam-systemd`, `libnss-systemd`, or `systemd-resolved` at 8.17; their only candidate remains installed 8.12. The repository `Packages` stanza and manifest lookup for each of those missing names is empty.

APT begins with broken count 4 and its first `Investigating (0)` item is `systemd-sysv:amd64 255.4-1ubuntu8.12`. It reports:

```text
Broken systemd-sysv:amd64 Depends on systemd:amd64 ... (= 255.4-1ubuntu8.12)
Considering systemd:amd64 ... as a solution to systemd-sysv
MarkKeep systemd:amd64 ...
Fixing systemd-sysv:amd64 via keep of systemd:amd64
```

That keep makes the exact `udev=255.4-1ubuntu8.17` root impossible because its authoritative repository stanza says `Breaks: systemd (<< 255.4-1ubuntu8.17)`. The solver later explicitly reports that break and repeatedly cannot reconcile the family. This is not an explicit hold, architecture mismatch, or dirty dpkg state. It is a repository-closure defect relative to the retained base: the payload carries a partial 8.17 systemd-family upgrade while omitting installed exact-version reverse dependencies needed to move the family coherently.

Other downstream broken relationships (`libpam-systemd`, `libnss-systemd`, `systemd-resolved`, and consumers of `systemd-sysv`) corroborate the same family closure defect. Netplan's 24.04.2 `netplan-generator` and `libnetplan1` candidates are present, so netplan is not the first missing package relationship.

### Source immutability and shutdown

Before and after D3, the retained source was identical:

- size: `3,968,401,408` bytes;
- SHA-256 before: `2393e9b4c90aeaf56a580634b3538defd0c3af26fa30c3a3846654962e3ba60d`;
- SHA-256 after: `2393e9b4c90aeaf56a580634b3538defd0c3af26fa30c3a3846654962e3ba60d`;
- virtual size: `34,359,738,368` bytes;
- dirty false, corrupt false;
- `qemu-img check --output=json`: exit 0, `check-errors=0` before and after.

The disposable guest ran `sync` and `poweroff -f`; QEMU exited normally. The overlay grew from 197,120 to 13,172,736 bytes, remained clean/non-corrupt, and `qemu-img check` returned 0 with zero errors. Protected media was never attached or read during D3.

## Defects

1. The offline closure includes exact roots from Noble updates without closing installed reverse dependencies that require exact source-family versions. The first proven omission is `systemd-sysv=255.4-1ubuntu8.17` when upgrading the retained systemd family from 8.12 to 8.17.
2. The same repository also omits matching `libpam-systemd`, `libnss-systemd`, and `systemd-resolved` 8.17 packages present as installed 8.12 exact-version consumers, so correcting only the first filename would likely expose the next member of the same defect.
3. C1 remains failed before first boot; D3 made no correction.
4. OWNER-10 clustered-storage closure remains unimplemented and unvalidated.

## Blockers

- **C1 remains FAIL** pending a separately authorized package-closure correction and ordinary two-pass validation.
- **OWNER-10 remains FAIL** independently of this baseline diagnosis.
- D3 authorizes no product change or workflow run.

## Next action

Authorize one narrow correction to the offline repository resolver so an upgraded source-package family is closed against exact-version installed reverse dependencies from the supported Noble base. For the proven transaction, include the matching `255.4-1ubuntu8.17` candidates needed to move the installed systemd family coherently—starting with `systemd-sysv` and covering the independently proven installed exact-version consumers `libpam-systemd`, `libnss-systemd`, and `systemd-resolved`. Add an executable retained-base regression that reproduces the 8.12-to-8.17 family transition with no removals/downgrades, then use a separately authorized ordinary two-pass no-NIC gate. Do not change the exact root set, relax service safety, or begin OWNER-10 clustered packages in that correction.
