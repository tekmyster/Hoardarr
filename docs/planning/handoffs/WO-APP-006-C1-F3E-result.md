# WO-APP-006-C1-F3E result

## Result

- **Complete-output candidate validation: FAIL / NOT EXECUTED.** The fresh guest reached its shell, but the one-line serial transfer of the evidence script returned `base64: invalid input`. Because the command chained decode, chmod and execution with `&&`, the script never executed and produced zero `F3E_STAGE` markers.
- **Retained-base simulation: FAIL / NOT EXECUTED.** No simulation argv or transaction was created. No APT update, policy query, package simulation, or actual install ran.
- **F3: FAIL / pending.** F3D's authenticated candidate evidence remains valid, but F3E did not reach the solver.
- **C1: FAIL.** F3E did not authorize or execute pass 1/pass 2.
- **OWNER-10: FAIL.** No LINSTOR, DRBD 9, DRBD Reactor, LINSTOR Gateway, kernel/Secure-Boot, or Proxmox-sidecar work was performed.

F3E was not retried. No artifact was downloaded or extracted, no repository or ISO was copied/rebuilt, no source/product/test/workflow changed, no Actions workflow was dispatched, no live system or protected media was touched, and no package command ran.

## Evidence

### Identity and locked inputs

- Work order SHA-256: `62538fa6c61c237df50b45f87a9a0e563b88fa70c49f43ac015a6d13612552c4`.
- Required and observed starting local/origin HEAD: `efc1651adba99ed49e506e180735b7e0361a461b`.
- F3D handoff SHA-256: `30be448d606f7a1bef34542c51485cef8ddfe0404d29ee3834835f0ebad297e7`.
- Reused F3C archive: `1,054,964,607` bytes; SHA-256 `87c90870111cb81cb2aefc262874de188a50c662cbe4c0d03fe42780db42929a`.
- Reused F3C ISO: `1,023,664,128` bytes; SHA-256 `631c6257fc6332b2235ce917319a0731fdb65465b75163c9f26937538e56a59b`.
- Reused repository: `1,066` files / `1,022,338,561` bytes; `SHA256SUMS` SHA-256 `ac133ac9e8401cf0b5c8d82333b3ce743541cc1700c1be9446e7d6f1b3e258e9`; `526` identities; `109` unique roots; eleven family members at `255.4-1ubuntu8.17`.
- Artifact key: `965` bytes; SHA-256 `ae0b5f724cc3036196e14ea828028cc5b67fe3c6f900631a52a897f1a7c80b5b`; fingerprint `6AC7E77D10C48333260B2CDD1495B2CD95543BF5`.
- Kernel: `vmlinuz-6.8.0-100-generic`, `15,030,664` bytes, SHA-256 `528d909745819a1464848a2c4d91c609db4f54b33ccae7069aad4178fb34606f`.
- Initrd: `initrd.img-6.8.0-100-generic`, `74,664,884` bytes, SHA-256 `8e5094cfcc9cc0d6790a38efdc19521dca4a394a34ddbba48608c211e580e474`.

All identities were recomputed before `.codex-temp\f3e-32953633660` was created and after QEMU stopped. The archive, extracted repository and ISO were reused in place; no network request, extraction, copy, or rebuild occurred.

### Retained source and fresh overlay

The retained F2 backing remained unchanged:

- physical/virtual size: `3,968,401,408` / `34,359,738,368` bytes;
- SHA-256 before/after: `2393e9b4c90aeaf56a580634b3538defd0c3af26fa30c3a3846654962e3ba60d`;
- dirty/corrupt: false/false;
- `qemu-img check`: exit `0`, `check-errors=0` before and after.

The new F3E overlay was created once with the exact F2 image as its direct backing file. After QEMU stopped:

- physical/virtual size: `12,779,520` / `34,359,738,368` bytes;
- SHA-256: `5f6cfc1c07b7fff19b7f9e8ac9648447574df93c7631549a0d2a7c7795dcae56`;
- dirty/corrupt: false/false;
- `qemu-img check`: exit `0`, `check-errors=0`.

### QEMU and pre-execution failure

QEMU `11.1.0` used TCG, 4 GiB, two vCPUs, corrected named kernel/initrd, `root=/dev/vda2 rw init=/bin/bash console=ttyS0`, and explicit `-nic none`. Its only drives were the fresh F3E overlay and the existing F3C ISO with `media=cdrom,readonly=on`. The complete argv SHA-256 is `d00b9aa5c569e9511d9b3ac142bd4d0fac8fa76dae9edbeef666e2745114b41a`; protected-media identifier matches: `0`.

The guest reached the serial shell and emitted exactly one `F3E_READY`. The host then sent a single command whose intended guest script was `9,529` bytes / `12,708` base64 characters:

```text
printf '%s' '<base64>' | base64 -d > /tmp/run-f3e.sh && chmod 700 /tmp/run-f3e.sh && /tmp/run-f3e.sh
```

The only resulting diagnostic was:

```text
base64: invalid input
```

Serial evidence contains:

- `F3E_READY`: `1`;
- `base64: invalid input`: `1`;
- `F3E_STAGE=`: `0`.

Because `base64 -d` failed, shell `&&` semantics prevented both `chmod` and `/tmp/run-f3e.sh`. Consequently none of these actions occurred:

- guest-visible network proof;
- ISO mount or repository verification/copy;
- keyring read or atomic replacement;
- source-list read/write;
- APT update;
- policy file creation or complete-file parsing;
- 109-root simulation;
- actual package installation.

The serial log is `32,128` bytes with SHA-256 `2f7d851b2799d1fa4ee29ec0443eac6a686db250409d2ba9b5ec9c065ef640ce`. No guest evidence archive exists because the evidence script never ran. A bounded cleanup connection sent `sync; poweroff -f`; it retained no serial bytes, but QEMU terminated and both overlay and backing QCOW2 checks are clean.

The exact transport-level cause of the corrupted/truncated base64 command was not independently isolated. The proven boundary is the guest decoder's nonzero `invalid input` response before script execution. Per F3E's no-improvisation/no-retry rule, no alternative transfer method was attempted.

### Required but absent transaction evidence

There is no update argv/status/log, policy argv/file/hash, eleven-pair parser result, simulation argv/status/action count/output hash, or family plan. This is not missing evidence for an executed operation: the serial record proves those operations never began.

## Defects

1. The F3E validation harness attempted to deliver a 12,708-character base64 payload as one interactive serial command; the guest decoder rejected the received data before execution.
2. Complete-output candidate validation and the retained-base solver transaction remain unexecuted.
3. C1 and OWNER-10 remain failed independently.

No new product/repository/keyring/APT defect was observed because F3E did not reach those gates.

## Blockers

- **F3:** blocked on a separately authorized fresh-overlay run using a bounded, integrity-checked guest-script transfer that does not depend on one oversized interactive serial line, followed by the already-specified complete-file parser and one simulation.
- **C1:** remains FAIL; no two-pass run is authorized by F3E.
- **OWNER-10:** remains FAIL independently.

## Next action

Authorize one replacement disposable preflight that transfers the unchanged evidence script in bounded chunks (or through a purpose-created read-only transfer medium), verifies its complete size/SHA inside the guest before execution, then preserves the F3E sequence: one signed update, complete regular policy file parsed to EOF, and exactly one unchanged 109-root simulation. Do not change product source, package inputs, VM geometry, or dispatch CI.
