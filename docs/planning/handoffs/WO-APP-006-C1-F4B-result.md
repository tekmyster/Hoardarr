# WO-APP-006-C1-F4B result

## Result

- **Retained identities: PASS.** The archive, extracted repository manifest/key, transfer ISO, retained F2 source, kernel, initrd, and QEMU binary matched every locked byte length and SHA-256 before the run and after shutdown.
- **Reuse-only constraint: PASS.** F4B made zero artifact/download/package-network requests, zero downloads, zero extractions of retained content, and zero ISO builds. No retained F4A byte was changed. Network use was limited to the work-order-required Git origin identity readback and final push; the guest had no NIC.
- **Host topology: PASS.** One fresh direct F2-backed overlay ran under QEMU 11.1 TCG with the accepted 29-element `ProcessStartInfo.ArgumentList`, callback-free `ReadToEndAsync()`, explicit `-nic none`, and exactly two drive arguments: the overlay and retained ISO as read-only CD media. No protected media was attached.
- **Bounded transport: PASS.** All 76 base64 chunks were at most 256 ASCII characters, individually acknowledged at status zero, decoded exactly once, and passed exact encoded/decoded size and SHA-256 gates before one execution.
- **Optical discovery and uniqueness: PASS.** The guest enumerated both optical nodes without assuming their names. `/dev/sr1` was recorded as a size nonmatch. `/dev/sr0` was the only candidate whose entire raw bytes, secure mount flags, volume, repository manifest/strict verification, and key identity matched every locked value.
- **Raw ISO and mounted repository identity: PASS.** `/dev/sr0` was exactly `1,041,065,984` bytes with SHA-256 `2227e8b116ae6a53ddb07af2163804155fafd8afd3d80b012f4e89425c739d5c`; its mounted repository and copied destination both passed complete strict verification.
- **Signed update: PASS.** Exactly one no-network APT update exited `0`, accepted the signed local `InRelease`, produced fresh lists, and emitted no `W:`, `E:`, or `Err:` line.
- **Complete family policy: PASS.** Complete regular-file output was parsed to EOF and contained exactly fourteen headings/candidates: eleven systemd-family members at `255.4-1ubuntu8.17` and three linux-meta members at `6.8.0-138.138`.
- **Solver: PASS.** Exactly one unchanged 109-root `--simulate --no-install-recommends` transaction exited `0`: `280` install/configure actions, zero removals, purges, downgrade/removal/unauthenticated phrases, and coherent plans for both families. No package was installed.
- **Linux generic preservation: PASS.** APT planned `linux-generic`, `linux-image-generic`, and `linux-headers-generic` from `6.8.0-100.100` to `6.8.0-138.138`; `linux-generic` was upgraded/preserved and not removed.
- **F4: PASS.** The accepted source/build/artifact correction is now removal-free in the exact retained-base solver preflight.
- **F4B: PASS.** Every authorized F4B gate passed in the single run.
- **C1: FAIL.** F4B did not authorize or execute ordinary two-pass installation.
- **OWNER-10: FAIL.** No LINSTOR, DRBD 9, DRBD Reactor, LINSTOR Gateway, kernel/Secure-Boot, or Proxmox-sidecar work was performed.

## Evidence

### Authority and starting state

- Work order SHA-256: `05ba298c67b4183dce15fe783b81f741a54375276cedc3e842930fd1a44652ae`.
- Required/observed starting local and origin HEAD: `ef8e886e064860fb9005fa8e8f89202522f9e427`.
- F4A handoff SHA-256: `f3112217ded1c54c7a2bf73580633cf53475a4a6d54f4a8b66e86dff5d54176d`.
- F4 handoff SHA-256: `56884fa731b0e8aa3b35758735c7d5eebe7203b5e6947a36d71c4a5b7cdaaa70`.
- F3H handoff SHA-256: `87fe935aaef5098caa18c1e63fb595cff6f8c9d9867c04b3bbd7a6f3c5e8ff7a`.
- `.codex-temp/f4b-32962854775` was absent before creation.
- Free space: `941,813,514,240` bytes; deterministic canary read/write/delete SHA-256 `40aff2e9d2d8922e47afd4648e6967497158785fbd1da870e7110266bf944880`.

The preflight and final readback each record `network_requests=0`, `extractions=0`, and `iso_builds=0`; here `network_requests` is the retained-input/artifact/package-retrieval counter, not the required Git control-plane readback. No `gh`, GitHub Actions API, artifact download, package-network request, extraction, ISO construction, source, workflow, or CI command ran in the retained-base validation. The only host network operations were the explicitly required Git origin identity readback and final handoff push; the QEMU guest had no NIC.

### Locked retained identities before/after

```text
F4A artifact archive
  bytes: 1072360483
  SHA-256: a238d7d859a1686b8e9723e7bafc1495e9654e7311d0463ab5804dadb5777fe8

F4A transfer ISO
  bytes: 1041065984
  SHA-256: 2227e8b116ae6a53ddb07af2163804155fafd8afd3d80b012f4e89425c739d5c

Repository SHA256SUMS
  bytes: 118954
  SHA-256: 5213f276f7b05b2a853b9d6a00bb4c8477e305993b211a9ad28880bbb1459673

Repository keyring
  bytes: 965
  SHA-256: 979292c0111433972d5c5300ea9406d7256252f23d1e3938e62022902a964ea7

Retained F2 QCOW2
  physical/virtual bytes: 3968401408 / 34359738368
  SHA-256: 2393e9b4c90aeaf56a580634b3538defd0c3af26fa30c3a3846654962e3ba60d
  dirty/corrupt: false/false
  final qemu-img check: exit 0, check-errors=0

Kernel
  bytes: 15030664
  SHA-256: 528d909745819a1464848a2c4d91c609db4f54b33ccae7069aad4178fb34606f

Initrd
  bytes: 74664884
  SHA-256: 8e5094cfcc9cc0d6790a38efdc19521dca4a394a34ddbba48608c211e580e474

QEMU 11.1
  bytes: 25457048
  SHA-256: 47d57a6072e0bb3bd98f87926eb129eb1736dfe818c67b3b81ef7ce4edd0b3cd
```

The extracted repository remained `1,074` files / `1,039,732,603` bytes. The locked manifest proves 530 package records and 109 product roots. No retained F4A byte changed.

### Host launch and no-protected-media proof

The one QEMU launch used:

- `System.Diagnostics.ProcessStartInfo.ArgumentList` with 29 individually added elements;
- `UseShellExecute=false`; joined `Arguments` remained empty;
- one `-append` at index 14, followed by the single exact element `root=/dev/vda2 rw init=/bin/bash console=ttyS0`;
- TCG, 4 GiB, two vCPUs, locked kernel/initrd;
- exactly two `-drive` elements: fresh F4B overlay and retained F4A ISO with `media=cdrom,readonly=on`;
- explicit `-nic none`, no display, no monitor, no reboot;
- no protected-media identifier.

Pre-transfer launch proof:

- PID `47760` alive;
- listener `127.0.0.1:45683` owned by PID `47760`;
- callback-free stderr task status `WaitingForActivation`, faulted false;
- guest-ready at `61.013` seconds.

After guest poweroff, QEMU exited `0`; stderr task completed nonfaulted. Complete stderr is `240` bytes, SHA-256 `c21e1d3a0711370c2722489b3b9c572385cc53d5befc26e4923a9a83ca9ba05d`, containing only QEMU's informational serial-listener line. Final readback found no QEMU process and no listener on port 45683.

Host proof identities:

```text
qemu-argv.json       SHA-256 f8f3d72d627d87cbf3ead6250c01ac6d55b87c9020b5c99a6c1baf8b4b6276c4
launch-proof.json    SHA-256 aee1b4ade8ebfcf1a0bdf540feb6249cb855b3650e2159a99117db69e48a8765
qemu-exit.json       SHA-256 a552200c5513ed30b5cad65749dcc5211c9043176f3cb17221d101df068f9fe6
```

### Bounded acknowledged transport

```text
guest script bytes:             14448
guest script SHA-256:           0d7dc8c35a1b3a2d335af9a06bbbe94a39e47ce8e04cf063eb636792bc8b582c
base64 characters:              19264
base64 SHA-256:                 6215cc246623ecfbd5fe0314418da464c803260c6cd0024c34108f3ca1a26d17
chunks acknowledged:            76/76
minimum/maximum chunk length:   64 / 256 ASCII characters
decode invocations/status:      1 / 0
decoded size/hash gate:         exact
chmod status:                   0
script executions:              1
controller elapsed:             168.447 seconds
```

`transport-result.json` is `11,879` bytes, SHA-256 `fd524cfbded09e7c2c1983a73e660c4969d8a2be163f58bb35f72de6d51d5b61`. Serial is `75,152` bytes, SHA-256 `3c6eaf17d3ab5c6c3db7f0e7ebbd0d01d2ee8881a015ac73054952bbfd7926ab`. Guest evidence archive is `29,372` bytes, SHA-256 `1e6c4b2cc46d27b60c0333c8c4bebb8430b5adb2108e14013adf3f24821a5649`.

Guest proof shows exactly interface `lo`, explicitly down, and an empty route file before media inspection.

### Content-addressed optical discovery

The kernel reported two type-5 optical/ROM candidates. Both were recorded before selection:

```text
/dev/sr0
  sectors: 2033332
  bytes: 1041065984
  blkid status: 0
  type: iso9660
  label: HOARDARR_F4A
  raw SHA status: 0
  raw SHA-256: 2227e8b116ae6a53ddb07af2163804155fafd8afd3d80b012f4e89425c739d5c
  mount source: /dev/sr0
  mount options: ro,nosuid,nodev,noexec,relatime,norock,check=r,map=n,blocksize=2048,iocharset=utf8
  manifest SHA-256: 5213f276f7b05b2a853b9d6a00bb4c8477e305993b211a9ad28880bbb1459673
  complete strict verification: status 0
  key bytes: 965
  key SHA-256: 979292c0111433972d5c5300ea9406d7256252f23d1e3938e62022902a964ea7
  key fingerprint: 11285A7FFA06F74C276A4D72C7013FBA1A7B9E5B
  result: match

/dev/sr1
  sectors: 2097151
  bytes: 1073741312
  blkid status: 2
  result: nonmatch:size
```

Uniqueness result: `matched_count=1`, node `/dev/sr0`, mount `/mnt/f4b-optical-sr0`. Selection was based on the entire raw device plus all mounted-content gates, never ordering. `optical-sr0-strict.log` and the copied destination strict log are both `52,428` bytes with identical SHA-256 `8969bea5a3bf798a8447790e046c3b73c554e28738b79bfd3ac606c587ff295f`.

### Repository copy, key pairing, and source identity

The unique read-only source and disposable destination retained the same manifest:

```text
source node: /dev/sr0
source SHA256SUMS:      5213f276f7b05b2a853b9d6a00bb4c8477e305993b211a9ad28880bbb1459673
destination SHA256SUMS: 5213f276f7b05b2a853b9d6a00bb4c8477e305993b211a9ad28880bbb1459673
```

The artifact key was installed only in the disposable overlay using a temporary sibling, root:root `0644`, file fsync, atomic `mv -fT`, and directory fsync. Final key identity:

```text
identity=0:0:644:965
sha256=979292c0111433972d5c5300ea9406d7256252f23d1e3938e62022902a964ea7
fingerprint=11285A7FFA06F74C276A4D72C7013FBA1A7B9E5B
```

The signed-by source list stayed byte-identical, SHA-256 `d6d07fcb69b31aafdbfaefea74a510e80bba130c60a1a1ac6c4727ce2d7eae1d`:

```text
deb [signed-by=/usr/share/keyrings/hoardarr-offline-archive-keyring.gpg] file:/opt/hoardarr/offline-repository noble main
```

### One signed update

Exactly one update ran with the locked source list, disabled source parts, no language downloads, zero retries, and false HTTP/HTTPS proxies. The retained argv file is `222` bytes. Update result:

```text
status: 0
log bytes: 242
log SHA-256: b4306bb3a7a77e0c273f1cf50fc860af201a0dfaef9eb76208e9f15811e87b09
W:/E:/Err: lines: 0
signed local InRelease: present
fresh authenticated list evidence: present
```

### Complete fourteen-member policy

The complete policy output was saved to a regular file before parsing. `family-policy.txt` is `3,552` bytes, SHA-256 `da906ae019e1544de97355549008dd623f320f069d3dc92d05678fafd7e760dd`. It was parsed to EOF and produced exactly fourteen validated rows (`validated-candidates.txt` SHA-256 `5120880bf21870a1821f62594439687c190ac0fb02a0decfb5c6dc68bbbf134a`):

- eleven `systemd-noble` members, each candidate `255.4-1ubuntu8.17`;
- `linux-generic`, `linux-image-generic`, and `linux-headers-generic`, each candidate `6.8.0-138.138`;
- no unexpected heading or candidate.

Installed state before simulation was all eleven systemd-family members at `255.4-1ubuntu8.12`, and all three linux-meta members at `6.8.0-100.100`. There were no holds and `dpkg --audit` was empty.

### One unchanged 109-root simulation

The exact simulation argv is retained in `simulation-argv.txt`, `3,136` bytes, SHA-256 `ba646a52dd859ba32652316c9ba7946489a0ce96f0f428c31a96b27f1819fc29`, identical to the accepted F3H 109-root argv. It ran exactly once with `--simulate --no-install-recommends`; no install ran.

Result:

```text
status=0
inst=280
conf=280
remv=0
purg=0
removal_phrases=0
downgrade_phrases=0
unauthenticated_phrases=0
summary: 59 upgraded, 221 newly installed, 0 to remove and 49 not upgraded
```

`apt-simulation.log` is `55,987` bytes, SHA-256 `85bda12c9bdf66847496be73c294c52fa3eca3093f2f911ec6cf68cf5137c1cb`. `action-counts.txt` SHA-256 is `8c596c18ffe331ad0b68d47477f246c4dcac3360f16bc978092b704b3507692f`.

All eleven systemd-family packages were planned coherently from `255.4-1ubuntu8.12` to `255.4-1ubuntu8.17`, including `systemd-dev` as architecture `all`. The linux-meta plan was exact:

```text
Inst linux-generic [6.8.0-100.100] (6.8.0-138.138 Hoardarr Offline Appliance:noble [amd64])
Inst linux-image-generic [6.8.0-100.100] (6.8.0-138.138 Hoardarr Offline Appliance:noble [amd64])
Inst linux-headers-generic [6.8.0-100.100] (6.8.0-138.138 Hoardarr Offline Appliance:noble [amd64])
```

`planned-families.txt` is `1,525` bytes, SHA-256 `b51bd767423197a9b2653faafb57958baf8da5b16a93d7c8ab0a1ac7c693872c`. Explicit disposition:

```text
installed_before=6.8.0-100.100
planned=6.8.0-138.138
removed=false
preserved=true
```

### Guest completion and overlay integrity

All stages completed in order through `F4B_STAGE=success`; final markers were `F4B_SCRIPT_EXIT=0` and `F4B_COMPLETE status=0`.

The fresh overlay is a direct child of the locked F2 source:

- physical/virtual size: `1,065,418,752` / `34,359,738,368` bytes;
- SHA-256: `5700de236173a2b82be43235a200b06a11535a39facc27c5e339bb87b3162b69`;
- dirty/corrupt: false/false;
- final `qemu-img info`: exit `0`;
- final `qemu-img check`: exit `0`, `check-errors=0`.

The retained F2 source remained byte-identical and clean. No input, source, retained repository/ISO, package, workflow, live system, credential, protected media, or clustered-storage component was changed.

## Defects

- No F4B retained-base solver defect remains. The linux-meta compatibility closure removes the prior `linux-generic` removal while preserving the exact 109 product roots.
- Ordinary two-pass C1 installation remains unexecuted and therefore failed/pending.
- OWNER-10 remains failed independently.

## Blockers

- **C1:** requires a separately authorized ordinary retry-disabled two-pass no-NIC installation run using the now-proven package closure. F4B itself does not authorize it.
- **OWNER-10:** remains blocked on the deferred LINSTOR + DRBD 9 + DRBD Reactor + installed-disabled LINSTOR Gateway closure, kernel/Secure-Boot compatibility, and offline Proxmox-plugin sidecar requirements.

## Next action

Authorize exactly one ordinary retry-disabled two-pass no-NIC C1 workflow at the accepted source commit, preserving the established installer/first-boot bounds and artifact evidence. Stop on either pass failure; do not begin OWNER-10 clustered-storage work in that run.
