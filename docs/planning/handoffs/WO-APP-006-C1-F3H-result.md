# WO-APP-006-C1-F3H result

## Result

- **Callback-free launch / guest boot: PASS.** QEMU was launched with the accepted 29-element `ProcessStartInfo.ArgumentList`; `StandardError.ReadToEndAsync()` was called once with no callback/event/runspace; PID, PID-owned listener, nonfaulted task, and guest-ready were proven.
- **Bounded transfer integrity: PASS.** All 50 chunks were individually acknowledged at status zero. Guest encoded and decoded identities exactly matched the locked host script before chmod and one execution.
- **Complete-policy validation: PASS.** The single signed APT update exited zero with no warning/error/signature failure. A complete regular policy file was parsed to EOF and contained exactly the eleven required `255.4-1ubuntu8.17` pairs.
- **Retained-base simulation: FAIL.** The one unchanged 109-root simulation exited `0` and planned all eleven systemd-family transitions at 8.17, but it also planned removal of installed `linux-generic 6.8.0-100.100`. The no-removal acceptance gate correctly failed. No package was installed and no retry occurred.
- **F3: FAIL.** The exact retained Noble transaction is not removal-free.
- **C1: FAIL.** F3H does not authorize and did not execute ordinary pass 1/pass 2.
- **OWNER-10: FAIL.** No LINSTOR, DRBD 9, DRBD Reactor, LINSTOR Gateway, kernel/Secure-Boot, or Proxmox-sidecar work was performed.

F3H stopped after this first solver result. No input, package policy, repository, source, CI/workflow, live system, credential, or cluster component was changed.

## Evidence

### Authority and locked identities

- Work order SHA-256: `6a609fe3dba036d3f80dc052999e9fad34ad62e51b49adef0aa2e1e6d3231e29`.
- Required and observed starting local/origin HEAD: `d6949b9777674d81afa2f2e9f62d9c6e86d78c84`.
- F3G handoff SHA-256: `dafb1adefcbe46b8474ad4af5de99439b89ea0fb03b46870bdcab9228e82886b`.
- Reused F3C archive: `1,054,964,607` bytes; SHA-256 before/after `87c90870111cb81cb2aefc262874de188a50c662cbe4c0d03fe42780db42929a`.
- Reused F3C ISO: `1,023,664,128` bytes; SHA-256 before/after `631c6257fc6332b2235ce917319a0731fdb65465b75163c9f26937538e56a59b`.
- Reused repository: `1,066` files / `1,022,338,561` bytes; `SHA256SUMS` SHA-256 `ac133ac9e8401cf0b5c8d82333b3ce743541cc1700c1be9446e7d6f1b3e258e9`; `526` identities; `109` roots.
- Root-version evidence SHA-256: `80798f4c96f2a14f817f13fa967ed376401f59b1f6da3f6e395adf3422832b1e`.
- Artifact keyring: `965` bytes; SHA-256 `ae0b5f724cc3036196e14ea828028cc5b67fe3c6f900631a52a897f1a7c80b5b`; fingerprint `6AC7E77D10C48333260B2CDD1495B2CD95543BF5`.
- Retained F2 source: `3,968,401,408` physical / `34,359,738,368` virtual bytes; SHA-256 before/after `2393e9b4c90aeaf56a580634b3538defd0c3af26fa30c3a3846654962e3ba60d`; dirty/corrupt false/false; final `qemu-img check` exit `0`, `check-errors=0`.
- Kernel: `15,030,664` bytes; SHA-256 before/after `528d909745819a1464848a2c4d91c609db4f54b33ccae7069aad4178fb34606f`.
- Initrd: `74,664,884` bytes; SHA-256 before/after `8e5094cfcc9cc0d6790a38efdc19521dca4a394a34ddbba48608c211e580e474`.
- Exact F3F guest script: `9,515` bytes; SHA-256 before/after `2209579c248da75ad055317b52a5e0001cc1d2709dcbfe6cd83432af86ca3a99`.
- Locked base64: `12,688` ASCII characters; SHA-256 `1c2434959c09c6081415d0a78b9b57f87e1bc9980b59bd63e90033c74dee3328`; `50` chunks; minimum/maximum `144` / `256` characters.

All identities were recomputed before creation and after shutdown. No download, extraction of a locked artifact, repository-ISO rebuild, or retained-input write occurred.

### Process launch, topology, and stderr

The exact accepted launch properties were retained:

- API: `System.Diagnostics.ProcessStartInfo.ArgumentList`;
- `UseShellExecute=false`;
- `RedirectStandardError=true`;
- `Arguments` remained empty;
- `29` individually added argument elements;
- exactly one `-append` at element `14` and the following element `15` exactly `root=/dev/vda2 rw init=/bin/bash console=ttyS0`;
- TCG, 4 GiB, two vCPUs, corrected kernel/initrd;
- fresh F3H overlay and existing read-only repository ISO as the only drives;
- explicit `-nic none`, no monitor, no reboot;
- serial `tcp:127.0.0.1:45681,server=on,wait=on`;
- protected-media identifier matches: `0`.

`StandardError.ReadToEndAsync()` was called once immediately after process start and retained without synchronous EOF wait while QEMU was live. No event, callback, script block, data-received handler, thread job, or runspace participated in stderr capture.

Pre-transfer proof:

- QEMU PID `61540` alive;
- listener `127.0.0.1:45681` owned by PID `61540`;
- stderr task status `WaitingForActivation`, faulted false;
- guest-ready at `60.817` seconds;
- argument count `29`, joined `Arguments` empty.

After guest poweroff, QEMU exited `0`; the retained stderr task was completed and nonfaulted. Its complete `240`-byte output, SHA-256 `84ea0c0cc39d075e197c4a71f15a981aa08518be745d40f087534663785502e4`, contains only QEMU's informational wait-for-serial line and no decisive error.

Host launch evidence:

- `qemu-argv.json`: `1,614` bytes, SHA-256 `3ef305a9baf3d95be8c76ea22bb3019d486e19dbb0fd22d87e0216c22af1a1a9`;
- `launch-proof.json`: `373` bytes, SHA-256 `50b811a205bd0ae499299da4be653f51a39244a9271eff8184613301b9466314`;
- `qemu-exit.json`: `209` bytes, SHA-256 `fe57a5b4e49dc3483d0ae9a28a67051241a2c42153de6e517bf91a6f29eb5957`.

Final readback found no QEMU process and no listener on port 45681.

### Fresh overlay and network proof

The F3H overlay was created once with the retained F2 source as its direct backing file. After shutdown:

- physical/virtual size: `1,048,051,712` / `34,359,738,368` bytes;
- SHA-256: `3fac2a08264499843018f0a96baa1c8ff18d9350d892add91043c8e467840266`;
- dirty/corrupt: false/false;
- `qemu-img info --output=json`: exit `0`;
- `qemu-img check --output=json`: exit `0`, `check-errors=0`.

Guest evidence reports exactly interface `lo`, explicitly down. No network adapter or protected media was attached.

### Bounded transport

The host used the exact locked F3F bytes and plan:

- truncate acknowledgment: status `0`;
- append acknowledgments: `50/50`, each status `0` and followed by the exact `F3H# ` prompt;
- maximum append payload: `256` ASCII characters;
- guest encoded count: `12,688`, exact;
- guest encoded SHA-256: `1c2434959c09c6081415d0a78b9b57f87e1bc9980b59bd63e90033c74dee3328`, exact;
- decode invocations: `1`, status `0`;
- guest decoded size/SHA-256 before chmod: `9,515` / `2209579c248da75ad055317b52a5e0001cc1d2709dcbfe6cd83432af86ca3a99`, exact;
- chmod acknowledgment: status `0`;
- script executions: `1`.

Controller elapsed time was `134.063` seconds. `transport-result.json` is `8,267` bytes, SHA-256 `a5a6e7b0b918c7fac28ba5f99b12c32cc6704ab6e903677ccebd22ac6f886684`. Serial is `69,635` bytes, SHA-256 `b43fa29d76a30a36ec8064413ce31c32290757ca62270f546df42c733dc3b906`. The guest evidence archive is `25,961` bytes, SHA-256 `ad46432dd1660a389ef4285c7c2ac49e71b412bd20dfdd76b5c4f35e5bc90e75`.

The evidence script's final status is `1` solely because its post-simulation no-removal assertion failed; transport itself passed.

### Repository, trust, and signed update

The existing ISO was mounted read-only. Source and destination strict `SHA256SUMS` logs are each `51,966` bytes and SHA-256 `973ee1a0694c05596a701329790501599cc7678e5388143d63de2e8b4e135344`; both manifest identities are `ac133ac9e8401cf0b5c8d82333b3ce743541cc1700c1be9446e7d6f1b3e258e9`.

The disposable prior key was recorded, then the artifact key was verified and installed with the accepted temporary-sibling, root:root `0644`, fsync, atomic `mv -fT`, directory-fsync procedure. Final identity:

```text
identity=0:0:644:965
sha256=ae0b5f724cc3036196e14ea828028cc5b67fe3c6f900631a52a897f1a7c80b5b
fingerprint=6AC7E77D10C48333260B2CDD1495B2CD95543BF5
```

The source list remained byte-identical, SHA-256 `d6d07fcb69b31aafdbfaefea74a510e80bba130c60a1a1ac6c4727ce2d7eae1d`:

```text
deb [signed-by=/usr/share/keyrings/hoardarr-offline-archive-keyring.gpg] file:/opt/hoardarr/offline-repository noble main
```

Exactly one update ran:

```text
apt-get -o Dir::Etc::sourcelist=/etc/apt/sources.list.d/hoardarr-offline.list -o Dir::Etc::sourceparts=- -o Acquire::Languages=none -o Acquire::Retries=0 -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false update
```

- argv SHA-256: `f210d6bc4c21b799fdc7210aeeaef36a0be2c7e6befebf0e7588f8336fe3b573`;
- status: `0`;
- log: `242` bytes, SHA-256 `c7ee142b40b0441f316b0fed4869cf3aefbdef76f2746309ef058eab93081895`;
- `W:` / `E:` / `Err:` lines: `0`;
- fresh signed `InRelease` and package list evidence present.

### Complete policy validation

Exact policy argv:

```text
apt-cache -o Dir::Etc::sourcelist=/etc/apt/sources.list.d/hoardarr-offline.list -o Dir::Etc::sourceparts=- -o Acquire::Languages=none -o Acquire::Retries=0 -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false policy systemd systemd-sysv systemd-timesyncd systemd-resolved udev libudev1 libsystemd0 libsystemd-shared libpam-systemd libnss-systemd systemd-dev
```

The complete output was first written to a regular file, then parsed to EOF. `family-policy.txt` is `2,815` bytes, SHA-256 `4f4112f2084f4cf8499c49f21de92ab58cb87f4121b8c172da4075e2ea6898bd`. It contained exactly eleven headings and eleven candidates. `validated-candidates.txt` is SHA-256 `f8d4d7809eb392582d31eb5f0ee31a293109daccd36cf18fd2932f838e2c2998`, with all eleven values exactly `255.4-1ubuntu8.17`.

### One retained-base simulation

The exact argv is retained verbatim in `simulation-argv.txt` (`3,136` bytes, SHA-256 `ba646a52dd859ba32652316c9ba7946489a0ce96f0f428c31a96b27f1819fc29`). It is:

```text
apt-get -o Dir::Etc::sourcelist=/etc/apt/sources.list.d/hoardarr-offline.list -o Dir::Etc::sourceparts=- -o Acquire::Languages=none -o Acquire::Retries=0 -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false --simulate --no-install-recommends install acl=2.3.2-1build1.1 attr=1:2.5.2-1build1.1 b3sum=1.2.0-1 bcache-tools=1.0.8-5build1 btrfs-progs=6.6.3-1.1build2 bzip2=1.0.8-5.1build0.1 ca-certificates=20260601~24.04.1 coreutils=9.4-3ubuntu6.2 corosync=3.1.7-1ubuntu3.2 crmsh=4.6.0-1ubuntu2 cryptsetup=2:2.7.0-1ubuntu4.2 ctdb=2:4.19.5+dfsg-4ubuntu9.7 curl=8.5.0-2ubuntu10.13 dmidecode=3.5-3ubuntu0.1 dmsetup=2:1.02.185-3ubuntu3.2 dosfstools=4.2-1.1build1 e2fsprogs=1.47.0-2.4~exp1ubuntu4.1 ethtool=1:6.7-1build1 exfatprogs=1.2.2-1build1 f3=8.0-2build2 fcoe-utils=1.0.34-4 fence-agents-base=4.12.1-2~exp1ubuntu4 fio=3.36-1ubuntu0.1 freeipmi-tools=1.6.13-3ubuntu0.1 gddrescue=1.27-1 gdisk=1.0.10-1build1 gnupg=2.4.4-2ubuntu17.4 gzip=1.12-1ubuntu3.2 hdparm=9.65+ds-1build1 ilorest=3.6.0.0-3 inotify-tools=3.22.6.0-4 iotop-c=1.26-1 ipmitool=1.8.19-7ubuntu0.24.04.3 iproute2=6.1.0-1ubuntu6.4 iputils-ping=3:20240117-1ubuntu0.1 jq=1.7.1-3ubuntu0.24.04.2 ledmon=0.97-0ubuntu1 linux-image-generic=6.8.0-138.138 lldpad=1.1+git20221028.aa18720-2 lldpd=1.0.18-1build3 lm-sensors=1:3.6.0-9build1 logrotate=3.21.0-2build1 lsof=4.95.0-1build3 lsscsi=0.32-1build1 lvm2=2.03.16-3ubuntu3.2 mdadm=4.3-1ubuntu2.1 mergerfs=2.33.5-1ubuntu2 multipath-tools=0.9.4-5ubuntu8.2 ncdu=1.19-0.1 netplan.io=1.1.2-8ubuntu1~24.04.2 nfs-common=1:2.6.4-3ubuntu5.1 nfs-kernel-server=1:2.6.4-3ubuntu5.1 nftables=1.0.9-1ubuntu0.1 ntfs-3g=1:2022.10.3-1.2ubuntu3.2 nvme-cli=2.8-1ubuntu0.1 open-iscsi=2.1.9-3ubuntu5.4 open-vm-tools=2:13.0.10-0ubuntu0.24.04.1 openssh-server=1:9.6p1-3ubuntu13.18 pacemaker=2.1.6-5ubuntu2 partclone=0.3.27+repack-2build2 parted=3.6-4build1 pciutils=1:3.10.0-2build1 pcp=6.2.0-1.1build4 procps=2:4.0.4-4ubuntu3.3 pv=1.8.5-2build1 python3.12=3.12.3-1ubuntu0.16 python3.12-venv=3.12.3-1ubuntu0.16 qemu-guest-agent=1:8.2.2+ds-0ubuntu1.18 quota=4.06-1build6 rauc=1.11.3-2 rclone=1.60.1+dfsg-3ubuntu0.24.04.6 redfishtool=1.1.5-1 resource-agents-base=1:4.13.0-1ubuntu4.2 resource-agents-extra=1:4.13.0-1ubuntu4.2 rsync=3.2.7-1ubuntu1.5 rsyslog=8.2312.0-3ubuntu9.3 samba=2:4.19.5+dfsg-4ubuntu9.7 samba-common-bin=2:4.19.5+dfsg-4ubuntu9.7 samba-vfs-modules=2:4.19.5+dfsg-4ubuntu9.7 sdparm=1.12-1 sg3-utils=1.46-3ubuntu4 smartmontools=7.4-2build1 smp-utils=0.99-1 snapraid=12.3-1 snmp=5.9.4+dfsg-1.1ubuntu3.2 snmpd=5.9.4+dfsg-1.1ubuntu3.2 strace=6.8-0ubuntu2 sysfsutils=2.1.1-6build1 sysstat=12.6.1-2 systemd-timesyncd=255.4-1ubuntu8.17 tar=1.35+dfsg-3ubuntu0.4 targetcli-fb=1:2.1.53-1ubuntu3 testdisk=7.1-5+nmu1build2 thin-provisioning-tools=0.9.0-2ubuntu5.1 traceroute=1:2.1.5-1 tree=2.1.1-2ubuntu3.24.04.2 udev=255.4-1ubuntu8.17 unzip=6.0-28ubuntu4.1 usbutils=1:017-3build1 util-linux=2.39.3-9ubuntu6.5 watchdog=5.16-1 wget=1.21.4-1ubuntu4.5 winbind=2:4.19.5+dfsg-4ubuntu9.7 xfsprogs=6.6.0-1ubuntu2.1 xxhash=0.8.2-2build1 xz-utils=5.6.1+really5.4.5-1ubuntu0.3 zfs-zed=2.2.2-0ubuntu9.4 zfsutils-linux=2.2.2-0ubuntu9.4 zstd=1.5.5+dfsg2-2build1.1
```

The transaction executed exactly once and never installed packages.

Simulation evidence:

- status: `0`;
- log: `55,375` bytes, SHA-256 `c74e52650f29b3578e73341234c88c20cb21eb17510022a981886b67cc3382d9`;
- `Inst`: `276`;
- `Conf`: `276`;
- `Remv`: `1`;
- `Purg`: `0`;
- removal phrases: `1`;
- downgrade phrases: `0`;
- unauthenticated phrases: `0`.

All eleven family members were planned coherently from installed `255.4-1ubuntu8.12` to `255.4-1ubuntu8.17`; `systemd-dev` was correctly planned as architecture `all`, the other applicable members as `amd64`. `planned-family.txt` is `1,195` bytes, SHA-256 `3c4a540369a156fa9a5159a807bcdd80d1566fcc308bc30e4b857582b93a436e`.

The first and only removal is exact:

```text
The following packages will be REMOVED:
  linux-generic
...
Remv linux-generic [6.8.0-100.100]
```

The locked roots request `linux-image-generic=6.8.0-138.138`. The authenticated repository contains that package/version but contains neither `linux-generic` nor `linux-headers-generic`. This evidence isolates the remaining closure seam to the Noble `linux-meta` family; it does not justify guessing or changing the family without a separately authorized metadata-backed correction.

## Defects

1. The exact 109-root simulation would remove installed `linux-generic 6.8.0-100.100`; this violates the fail-closed no-removal contract even though APT exits zero.
2. The repository includes the requested newer `linux-image-generic` but not the related `linux-generic` or `linux-headers-generic` meta packages.
3. F3, C1, and OWNER-10 remain failed.

## Blockers

- **F3:** blocked on a narrow, authoritative-metadata-backed Noble `linux-meta` closure correction that preserves all 109 roots and prevents removal of the installed generic-kernel meta package.
- **C1:** remains FAIL; no two-pass run is authorized by F3H.
- **OWNER-10:** remains FAIL independently.

## Next action

Authorize one bounded correction that first records the exact installed `linux-generic` dependency relationship, then—if the metadata confirms the expected version coupling—adds a declarative version-coherent compatibility family for the required Noble `linux-generic`, `linux-image-generic`, and `linux-headers-generic` identities without making new product roots. Fail closed on any different relationship, rebuild once, and rerun only the retained-base simulation gate before any C1 two-pass attempt.
