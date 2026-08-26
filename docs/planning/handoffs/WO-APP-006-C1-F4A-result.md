# WO-APP-006-C1-F4A result

## Result

- **Artifact request and identity: PASS.** The locked artifact metadata was reread, exactly one binary-safe `gh api` ZIP request succeeded, and the resulting archive matched the required byte length and SHA-256 before one extraction.
- **Repository verification: PASS.** The verifier at exact source commit `226a7c25c5eda353cc85b18e638a1c58962e0f54` accepted the signed/indexed repository. Independent readback proved 109 roots, the complete coherent `systemd-noble` and `linux-meta-noble` families, target-compatible architectures, and the exact same-version `linux-generic` sibling dependencies.
- **Read-only transfer ISO: PASS.** One nonempty ISO was built from the verified repository without changing repository bytes and passed a complete 7-Zip read test.
- **Launch and bounded transport: PASS.** One fresh direct F2-backed overlay booted with the accepted 29-element `ProcessStartInfo.ArgumentList`, callback-free `ReadToEndAsync()`, TCG, explicit `-nic none`, the corrected kernel/initrd, and only the overlay plus read-only repository ISO in QEMU argv. All 58 bounded chunks and encoded/decoded identities were acknowledged and verified.
- **Guest media preflight: FAIL (fail closed).** The retained guest exposed `fd0`, `loop0` through `loop7`, `sr0`, `sr1`, `vda`, `vda1`, and `vda2`. The disposable script required the overly strict literal list `sr0`, `vda`, `vda1`, `vda2`, so it exited `1` at `F4A_STAGE=network-and-media-proof`. It never mounted optical media and never copied the repository.
- **Signed update: NOT EXECUTED.** The fail-closed media gate preceded the sole authorized APT update.
- **Runtime policy/family proof: NOT EXECUTED.** No `apt-cache policy` command ran.
- **Solver: NOT EXECUTED.** No APT simulation or installation ran.
- **F4: PASS.** The accepted F4 source/build/artifact result remains intact; this run did not change source.
- **F4A: FAIL.** The single authorized retained-base run stopped at its first mismatch and was not retried.
- **C1: FAIL.** F4A did not authorize or execute ordinary two-pass installation.
- **OWNER-10: FAIL.** No LINSTOR, DRBD 9, DRBD Reactor, LINSTOR Gateway, kernel/Secure-Boot, or Proxmox-sidecar work was performed.

## Evidence

### Authority, starting state, and retained inputs

- Work order SHA-256: `f81f52f3ad92c5d1c67cbe88631bdc83389daafdcc1fd63baf2d4c131ead3f3d`.
- Required/observed starting local and origin HEAD: `d214bff28ce37bfd4260ea96462042a169f28558`.
- F3H handoff SHA-256: `87fe935aaef5098caa18c1e63fb595cff6f8c9d9867c04b3bbd7a6f3c5e8ff7a`.
- F4 handoff SHA-256: `56884fa731b0e8aa3b35758735c7d5eebe7203b5e6947a36d71c4a5b7cdaaa70`.
- Free-space preflight: `945,046,687,744` bytes; new-directory canary write/read/delete passed before `.codex-temp/f4a-32962854775` was created.
- Retained F2 source before/after: `3,968,401,408` physical / `34,359,738,368` virtual bytes; SHA-256 `2393e9b4c90aeaf56a580634b3538defd0c3af26fa30c3a3846654962e3ba60d`; dirty/corrupt false/false; final `qemu-img check` exit `0`.
- Kernel before/after: `15,030,664` bytes; SHA-256 `528d909745819a1464848a2c4d91c609db4f54b33ccae7069aad4178fb34606f`.
- Initrd before/after: `74,664,884` bytes; SHA-256 `8e5094cfcc9cc0d6790a38efdc19521dca4a394a34ddbba48608c211e580e474`.
- QEMU 11.1 binary: `25,457,048` bytes; SHA-256 `47d57a6072e0bb3bd98f87926eb129eb1736dfe818c67b3b81ef7ce4edd0b3cd`.

All retained identities were recomputed after shutdown. No protected disk was attached or named in the QEMU argument array.

### Single artifact request and extraction

The artifact API was reread immediately before the only network request:

```text
artifact id: 9604813277
name: hoardarr-offline-install-inputs
run head: 226a7c25c5eda353cc85b18e638a1c58962e0f54
size: 1072360483
digest: sha256:a238d7d859a1686b8e9723e7bafc1495e9654e7311d0463ab5804dadb5777fe8
expires: 2026-08-29T11:29:45Z
```

Exactly one request ran:

```text
gh api repos/tekmyster/Hoardarr/actions/artifacts/9604813277/zip
```

- request count: `1`;
- exit: `0`;
- stderr: `0` bytes;
- archive: `.codex-temp/f4a-32962854775/hoardarr-offline-install-inputs-9604813277.zip`;
- archive size: `1,072,360,483` bytes;
- archive SHA-256: `a238d7d859a1686b8e9723e7bafc1495e9654e7311d0463ab5804dadb5777fe8`.

The archive was extracted exactly once. It contained the offline repository and release archive; the repository has `1,074` files / `1,039,732,603` bytes. No second request or extraction occurred.

### Repository, roots, families, and control metadata

`scripts/build-offline-apt-repository.py` was read from the current tree only after proving its Git blob (`e33b383bb4d3a6f77be60e29c0ba0551ff2826b0`) equals the blob at exact source commit `226a7c25c5eda353cc85b18e638a1c58962e0f54`. Verifier SHA-256 is `0923858f33fb79ed3d9cc4ebbbf215c336d44b392b07578b4cedf8bdd9ca89c4`; `verify` exited successfully.

Independent evidence:

```text
SHA256SUMS SHA-256:              5213f276f7b05b2a853b9d6a00bb4c8477e305993b211a9ad28880bbb1459673
root-package-versions SHA-256:   80798f4c96f2a14f817f13fa967ed376401f59b1f6da3f6e395adf3422832b1e
package-manifest SHA-256:        3fa392228de0c1f264043e32d1da7884e723e044be2ebf35083ab3e5776d01af
family evidence SHA-256:         0d424865d71612dfa9ee8e78de053c3c3111bc37b0b7bf94e5213caead7291d2
compatibility matrix SHA-256:    3d9e44d0fb918ae05eb69308f8b87c44a1f85c6cda46d9964bf0ef04fe450090
package records:                530
product roots:                  109
root-name set SHA-256:          71f0bc60aa3dba665b25511479cce5b853e0ae25ccd3dd0eb264d5ead0f23170
```

`systemd-noble` is complete at `255.4-1ubuntu8.17`: eleven exact members; `systemd-dev` is `all`, and the other ten are `amd64`.

`linux-meta-noble` is complete at `6.8.0-138.138`: `linux-generic`, `linux-image-generic`, and `linux-headers-generic`, all `amd64`, source `linux-meta`. Exact retained control metadata is:

```text
linux-generic Depends: linux-image-generic (= 6.8.0-138.138), linux-headers-generic (= 6.8.0-138.138)
```

Only `linux-image-generic` intersects the unchanged 109 product roots; the other two members are compatibility closure rather than new roots.

The archive keyring is `965` bytes, SHA-256 `979292c0111433972d5c5300ea9406d7256252f23d1e3938e62022902a964ea7`, fingerprint `11285A7FFA06F74C276A4D72C7013FBA1A7B9E5B`.

### Read-only transfer ISO

- path: `.codex-temp/f4a-32962854775/f4a-repository.iso`;
- volume: `HOARDARR_F4A`;
- filesystem: `ISO9660+Joliet`;
- size: `1,041,065,984` bytes;
- SHA-256: `2227e8b116ae6a53ddb07af2163804155fafd8afd3d80b012f4e89425c739d5c`;
- files: `1,074`; repository payload: `1,039,732,603` bytes;
- 7-Zip complete image test: PASS;
- repository `SHA256SUMS` before/after ISO creation: exact `5213f276f7b05b2a853b9d6a00bb4c8477e305993b211a9ad28880bbb1459673`.

Two pre-image IMAPI API setup attempts failed before a usable image existed: the first hit the default media-capacity limit; the second produced only a zero-byte placeholder before a COM stream-method error. The exact zero-byte placeholder inside the new F4A directory was verified and removed. Only the final nonempty image above was attached; repository bytes were never rewritten or re-signed.

### Launch, no-NIC topology, and bounded transport

The single run used:

- `System.Diagnostics.ProcessStartInfo.ArgumentList`, 29 individual elements;
- `UseShellExecute=false`; joined `Arguments` empty;
- exactly one `-append` at index 14 followed by the single exact element `root=/dev/vda2 rw init=/bin/bash console=ttyS0`;
- TCG, 4 GiB, two vCPUs;
- fresh direct F2-backed overlay and the read-only repository ISO as the only two `-drive` arguments;
- explicit `-nic none`, no monitor, no reboot;
- callback-free `StandardError.ReadToEndAsync()` immediately after start.

Pre-transfer proof:

- PID `2712` alive;
- listener `127.0.0.1:45682` owned by PID `2712`;
- stderr task nonfaulted, `WaitingForActivation`;
- guest-ready at `60.828` seconds.

Transport proof:

```text
guest script bytes:             10978
guest script SHA-256:           13f3949a0c759721a63707dfddf63fdc391722920fa3e3cc07fda736e3d08141
base64 characters:              14640
base64 SHA-256:                 22547cb371bbad3419a391fd06aaeea43fd3d7cffe409cc0b2f680304f4319a6
chunks acknowledged:            58/58
minimum/maximum chunk length:   48 / 256 ASCII characters
decode invocations/status:      1 / 0
decoded size/hash gate:         exact
chmod status:                   0
script executions:              1
```

Serial is `35,626` bytes, SHA-256 `3ae0e3fd698d8b396b1194e8eabb1fe0b25ed5fd37277e37135b119db278faeb`. `transport-result.json` is `9,374` bytes, SHA-256 `d0282f7fcae25a6a273fd0833f84139c3f3a8b6f054ae78fac6fb73a41090f13`.

QEMU exited `0`. Complete stderr is `240` bytes, SHA-256 `7a1fbe8fddb270310c024c3fcd5fc966d3aef87e0b0b2a60139add2aee5d7d2b`; it contains only QEMU's informational serial-listener line and no decisive error. Final readback found no QEMU process and no listener on port 45682.

### Exact fail-closed boundary

Guest evidence proved only interface `lo`, explicitly down, and no route. The host QEMU argument record proves no NIC and no protected media. The guest block-device evidence was:

```text
fd0
loop0
loop1
loop2
loop3
loop4
loop5
loop6
loop7
sr0
sr1
vda
vda1
vda2
```

The next assertion required the literal block list `sr0`, `vda`, `vda1`, `vda2`. Consequently the only guest stage marker is:

```text
F4A_STAGE=network-and-media-proof
F4A_SCRIPT_EXIT=1
F4A_COMPLETE status=1
```

The evidence archive is `412` bytes, SHA-256 `7a09d5fcc4a3c90aab70f8e04ee7f9b5a738372eec1d4fc46942f5dc116b628f`. It contains only network, loopback, block-device, and script-status evidence. Absence of the later stage markers and files proves that no ISO mount, repository copy, key replacement, APT update, policy query, simulation, or installation occurred.

### Overlay and shutdown readback

The overlay was created once with the retained F2 QCOW2 as its direct backing file. Final state:

- physical/virtual size: `12,779,520` / `34,359,738,368` bytes;
- dirty/corrupt: false/false;
- direct backing file: the exact retained F2 source;
- `qemu-img info`: exit `0`;
- `qemu-img check`: exit `0`, `check-errors=0`.

The retained F2 source remained byte-identical and clean after shutdown. Kernel, initrd, QEMU binary, archive, repository manifest, and transfer ISO identities remained unchanged.

## Defects

1. The disposable F4A guest preflight incorrectly equated the safe host attachment contract with an exact `/sys/class/block` name list. The retained guest already exposes loop devices, `fd0`, and two optical device nodes (`sr0`, `sr1`); therefore the check rejected the authorized topology before identifying which optical node contained the verified transfer ISO.
2. Because the first mismatch correctly stopped the one-run work order, the signed-update, complete-policy, and removal-free 109-root solver gates remain unexecuted.
3. F4A, C1, and OWNER-10 remain failed.

## Blockers

- **F4A:** blocked on a separately authorized single-use preflight that distinguishes attached backing media from harmless retained device nodes and identifies the one read-only transfer medium by verified content/volume identity instead of assuming `/dev/sr0`.
- **C1:** remains FAIL; no ordinary two-pass run is authorized.
- **OWNER-10:** remains FAIL independently on the deferred LINSTOR + DRBD 9 + DRBD Reactor + installed-disabled LINSTOR Gateway closure, kernel/Secure-Boot compatibility, and offline Proxmox-plugin sidecar requirements.

## Next action

Authorize one bounded retained-base retry that preserves the exact host QEMU argv/no-NIC/protected-media gate but replaces the literal guest block-name assertion with a fail-closed optical-media discovery gate: inspect all optical nodes read-only, accept exactly one device only when its volume/content hashes match the locked transfer ISO/repository, reject zero or multiple matches, and then run the already-defined single signed update and one unchanged 109-root simulation. Do not alter source, repository, roots, packages, VM geometry, or install anything.
