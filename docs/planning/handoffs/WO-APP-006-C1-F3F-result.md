# WO-APP-006-C1-F3F result

## Result

- **Bounded transfer integrity: FAIL / NOT EXECUTED.** The host prepared a valid 50-chunk plan, but QEMU exited before opening the serial listener. No chunk, truncate, decode, chmod, or guest-script command was sent.
- **Complete-output candidate validation: FAIL / NOT EXECUTED.** No guest booted and no APT command ran.
- **Retained-base simulation: FAIL / NOT EXECUTED.** The unchanged 109-root simulation was not reached. No package was installed.
- **F3: FAIL / pending.** The solver result remains unknown.
- **C1: FAIL.** No ordinary pass 1/pass 2 run was authorized or executed.
- **OWNER-10: FAIL.** No LINSTOR, DRBD 9, DRBD Reactor, LINSTOR Gateway, kernel/Secure-Boot, or Proxmox-sidecar work was performed.

This was the single authorized F3F attempt. It failed closed before guest execution and was not retried, recreated, or modified. The waiting disposable controller was terminated only after the Supervisor identified the terminal QEMU failure.

## Evidence

### Authority and locked identities

- Work order SHA-256: `dbe8c4a0079aef31e3ee1d83805b08e1efde25e5871eb084484644385fea1614`.
- Required and observed starting local/origin HEAD: `8335d09af4fcd96b211bcac739a9c13bedc3732e`.
- F3E handoff: `6,758` bytes; SHA-256 `f64fb1ae95449c1997c787b158420934a44c48b4c62489498d9a71b15aa8c6ea`.
- Reused F3C archive: `1,054,964,607` bytes; SHA-256 `87c90870111cb81cb2aefc262874de188a50c662cbe4c0d03fe42780db42929a`.
- Reused F3C ISO: `1,023,664,128` bytes; SHA-256 `631c6257fc6332b2235ce917319a0731fdb65465b75163c9f26937538e56a59b`.
- Reused repository: `1,066` files / `1,022,338,561` bytes; `SHA256SUMS` SHA-256 `ac133ac9e8401cf0b5c8d82333b3ce743541cc1700c1be9446e7d6f1b3e258e9`; `526` package identities; `109` roots.
- Artifact keyring: `965` bytes; SHA-256 `ae0b5f724cc3036196e14ea828028cc5b67fe3c6f900631a52a897f1a7c80b5b`; locked fingerprint `6AC7E77D10C48333260B2CDD1495B2CD95543BF5`.
- Retained F2 source: `3,968,401,408` physical / `34,359,738,368` virtual bytes; SHA-256 after the failed launch `2393e9b4c90aeaf56a580634b3538defd0c3af26fa30c3a3846654962e3ba60d`, exactly matching the locked identity.
- Kernel: `15,030,664` bytes; SHA-256 `528d909745819a1464848a2c4d91c609db4f54b33ccae7069aad4178fb34606f`.
- Initrd: `74,664,884` bytes; SHA-256 `8e5094cfcc9cc0d6790a38efdc19521dca4a394a34ddbba48608c211e580e474`.

All retained identities above were recomputed after the failed launch. No download, extraction, repository/ISO rebuild, or input mutation occurred.

### Fresh overlay and intended QEMU boundary

One new direct F2-backed overlay was created at `.codex-temp\f3f-32953633660\overlay.qcow2`. After the failed launch:

- physical/virtual size: `197,120` / `34,359,738,368` bytes;
- SHA-256: `e034a0efd5f8ba0a0f08aaeb87399c5d3fb71203d72e4de16681f95ced5adb1d`;
- direct full backing filename: the locked retained F2 `os.qcow2`;
- dirty/corrupt: false/false;
- `qemu-img info --output=json`: exit `0`;
- `qemu-img check --output=json`: exit `0`, `check-errors=0`.

The recorded intended argv used QEMU 11.1.0, TCG, the fresh overlay, the existing ISO with `media=cdrom,readonly=on`, corrected named kernel/initrd, `root=/dev/vda2 rw init=/bin/bash console=ttyS0`, explicit `-nic none`, no monitor, no reboot, and TCP serial port `45679`. `qemu-argv.json` is `1,238` bytes with SHA-256 `b65d6dfafe969d112f871572b05cb45e76996bdf39cc0f7667b89239a6581c37`; protected-media identifier matches are zero.

The Windows `Start-Process -ArgumentList` launch did not preserve the multi-word `-append` value as one argv element. QEMU treated `rw` as a file operand and exited immediately. The complete `qemu-stderr.log` is `180` bytes, SHA-256 `0b774d67a7127befbc5faf73ad29a2a1d51b28e145bcf049b6581ccaa8744a02`, with the decisive error:

```text
qemu-system-x86_64.exe: rw: Could not open 'rw': The system cannot find the file specified.
```

The recorded QEMU PID `56284` was absent at readback, no `qemu-system-x86_64` process existed, and TCP port `45679` had no listener. These facts prove failure occurred before a guest could boot.

### Bounded transport plan and non-execution proof

The disposable guest script passed host `bash -n` and was prepared but never transported:

- decoded host script: `9,515` bytes; SHA-256 `2209579c248da75ad055317b52a5e0001cc1d2709dcbfe6cd83432af86ca3a99`;
- base64: `12,688` ASCII characters; SHA-256 `1c2434959c09c6081415d0a78b9b57f87e1bc9980b59bd63e90033c74dee3328`;
- chunk count: `50`;
- minimum/maximum chunk size: `144` / `256` characters;
- intended acknowledgment: each append had its own `F3F_CHUNK_NNNN:0` marker followed by the exact `F3F# ` prompt; later gates required exact encoded count/hash, one successful decode, and exact decoded size/hash before chmod.

At final readback all of the following were absent:

- `serial.raw`;
- `transport-result.json`;
- `guest-evidence.tar.gz`.

Therefore the acknowledged append count was zero. No truncate, append, encoded-count check, decode, decoded-identity check, chmod, or script execution occurred. No guest filesystem, repository, keyring, APT source list, package database, or network state was reached or changed.

### APT, policy, and simulation evidence

There is no update argv/status/log, authenticated-list evidence, policy argv/file, eleven-pair parser result, simulation argv/status/log, action count, or family plan because the guest never existed. Specifically:

- `apt-get update` executions: `0`;
- `apt-cache policy` executions: `0`;
- 109-root simulations: `0`;
- actual installs: `0`.

The accepted F3D signed-update/candidate evidence is not invalidated, but F3F produced no new candidate or solver result.

## Defects

1. The host launch mechanism flattened the multi-word QEMU `-append` argument, causing a deterministic pre-guest exit on operand `rw`.
2. Because QEMU never opened the serial listener, F3F did not test the new bounded transport or the retained-base solver transaction.
3. F3, C1, and OWNER-10 remain failed independently.

## Blockers

- **F3:** blocked on a separately authorized fresh-overlay execution using an argument-safe Windows process API that passes the kernel append string as one argv element, followed by the unchanged bounded transport and one solver simulation.
- **C1:** remains FAIL; no two-pass run is authorized by F3F.
- **OWNER-10:** remains FAIL independently.

## Next action

Authorize one fresh disposable successor that changes only QEMU process argument transport (for example, `System.Diagnostics.ProcessStartInfo.ArgumentList` with one element per recorded argv item), proves the launched argv boundary, and then executes the already-prepared maximum-256-character acknowledged transport plus the unchanged single-update, complete-file policy validation, and one 109-root simulation. Do not change package inputs, product source, VM geometry, time bounds, NIC state, or CI.
