# WO-APP-006-C1-F3G result

## Result

- **Argv preservation / guest boot: FAIL.** Argument preservation passed: QEMU started as a live process and owned the requested serial listener with the complete kernel append value retained as one `ArgumentList` element. The PowerShell launcher then terminated from an unhandled asynchronous stderr-callback exception before it could persist the mandatory launch proof or connect a serial client. Because QEMU used serial `wait=on`, no guest boot occurred.
- **Bounded transfer integrity: FAIL / NOT EXECUTED.** The locked script and 50-chunk plan were verified, but no truncate, append, acknowledgment, decode, chmod, or execution command was sent.
- **Complete-output candidate validation: FAIL / NOT EXECUTED.** No guest or APT process ran.
- **Retained-base simulation: FAIL / NOT EXECUTED.** The unchanged 109-root transaction was not reached. No package was installed.
- **F3: FAIL / pending.** No solver result was produced.
- **C1: FAIL.** F3G did not authorize or execute ordinary pass 1/pass 2.
- **OWNER-10: FAIL.** No LINSTOR, DRBD 9, DRBD Reactor, LINSTOR Gateway, kernel/Secure-Boot, or Proxmox-sidecar work was performed.

This was the one authorized F3G attempt. It stopped fail-closed at the first launch-proof mismatch and was not retried or modified. The still-waiting disposable QEMU process was forcibly stopped only to end the failed attempt and release its listener.

## Evidence

### Authority and locked identities

- Work order SHA-256: `1de6e9448bf590ec016c00306cd0065fb8ac590d5fdf248c4bfbdc9118d8b735`.
- Required and observed starting local/origin HEAD: `151164dfb2de80ce71928ecd22b00e79054aa36a`.
- F3F handoff SHA-256: `63bf07500f67c34168f69edb1c777163df43ed8680391e0b08526c66214fd296`.
- Reused F3C archive: `1,054,964,607` bytes; SHA-256 before/after `87c90870111cb81cb2aefc262874de188a50c662cbe4c0d03fe42780db42929a`.
- Reused F3C ISO: `1,023,664,128` bytes; SHA-256 before/after `631c6257fc6332b2235ce917319a0731fdb65465b75163c9f26937538e56a59b`.
- Reused repository: `1,066` files / `1,022,338,561` bytes; `SHA256SUMS` SHA-256 `ac133ac9e8401cf0b5c8d82333b3ce743541cc1700c1be9446e7d6f1b3e258e9`; `526` package identities; `109` roots; all eleven compatibility-family members at `255.4-1ubuntu8.17`.
- Artifact keyring: `965` bytes; SHA-256 `ae0b5f724cc3036196e14ea828028cc5b67fe3c6f900631a52a897f1a7c80b5b`; locked fingerprint `6AC7E77D10C48333260B2CDD1495B2CD95543BF5`.
- Retained F2 source: `3,968,401,408` physical / `34,359,738,368` virtual bytes; SHA-256 before/after `2393e9b4c90aeaf56a580634b3538defd0c3af26fa30c3a3846654962e3ba60d`; dirty/corrupt false/false; pre-run `qemu-img check` exit `0`, `check-errors=0`.
- Kernel: `15,030,664` bytes; SHA-256 before/after `528d909745819a1464848a2c4d91c609db4f54b33ccae7069aad4178fb34606f`.
- Initrd: `74,664,884` bytes; SHA-256 before/after `8e5094cfcc9cc0d6790a38efdc19521dca4a394a34ddbba48608c211e580e474`.
- Locked F3F guest script: `9,515` bytes; SHA-256 before/after `2209579c248da75ad055317b52a5e0001cc1d2709dcbfe6cd83432af86ca3a99`.
- Locked base64 plan: `12,688` ASCII characters; SHA-256 `1c2434959c09c6081415d0a78b9b57f87e1bc9980b59bd63e90033c74dee3328`; `50` chunks; minimum/maximum `144` / `256` characters.

All locked identities were recomputed before creating `.codex-temp\f3g-32953633660`; all retained file identities were recomputed after the failed launch. No input was downloaded, extracted, copied, rebuilt, or changed.

### Fresh overlay

One fresh overlay was created with the retained F2 image as its direct backing file. After the stopped launch:

- physical/virtual size: `197,120` / `34,359,738,368` bytes;
- SHA-256: `e034a0efd5f8ba0a0f08aaeb87399c5d3fb71203d72e4de16681f95ced5adb1d`;
- direct full backing filename: the locked F2 `os.qcow2`;
- dirty/corrupt: false/false;
- `qemu-img info --output=json`: exit `0`;
- `qemu-img check --output=json`: exit `0`, `check-errors=0`.

The overlay retained its creation-time size and the same SHA-256 as an untouched fresh overlay, consistent with QEMU never leaving serial-server wait state.

### Process API and argument preservation

The launcher used `System.Diagnostics.ProcessStartInfo` with:

- `UseShellExecute=false`;
- `Arguments` empty;
- every intended string added separately through `ArgumentList.Add()`;
- no `Start-Process`, joined command string, shell parsing, or manual quoting.

The structural assertion found exactly one `-append` at element index `14`, immediately followed at index `15` by exactly one element:

```text
root=/dev/vda2 rw init=/bin/bash console=ttyS0
```

The argument array contained `29` elements and no standalone `rw` element. It used TCG, the fresh F3G overlay, the existing ISO as `media=cdrom,readonly=on`, the locked kernel/initrd, explicit `-nic none`, no monitor, no reboot, and serial `tcp:127.0.0.1:45680,server=on,wait=on`. Protected-media identifier matches were zero.

`qemu-argv.json` is `1,525` bytes with SHA-256 `61f320ffaffa1ab6ba1b71c3beaa5436da4ab2c574af16c1933f5224c41fc845`. It records API `System.Diagnostics.ProcessStartInfo.ArgumentList`, `use_shell_execute=false`, the exact element array, and `append_value_is_one_element=true`.

Runtime readback before cleanup proved:

- QEMU PID: `17032`;
- PID alive: true;
- listener: `127.0.0.1:45680`;
- listener owner PID: `17032`.

This proves the F3F `rw` operand defect was corrected. QEMU successfully parsed enough of the argument array to remain live and create its serial listener.

### First mismatch and fail-closed stop

The launcher attached an asynchronous `ErrorDataReceived` PowerShell script-block callback so it could retain stderr while QEMU remained live. That callback ran on a thread without a PowerShell runspace and terminated `pwsh` before `launch-proof.json` could be written. The launcher command returned Windows/.NET exit `-532462766` (`0xe0434352`). Windows Application log evidence at `2026-08-26T06:50:11.6304619-04:00`, provider `.NET Runtime`, event `1026`, states:

```text
System.Management.Automation.PSInvalidOperationException: There is no Runspace available to run scripts in this thread.
```

Application Error event `1000` at `2026-08-26T06:50:11.8014836-04:00` records faulting `pwsh.exe` with exception code `0xe0434352`.

Required pre-transfer proof was therefore incomplete:

- live PID: observed;
- PID-owned listener: observed;
- argument structure: persisted and valid;
- stderr nondecisive: **not established** (`qemu-stderr.log` was never created);
- `launch-proof.json`: absent;
- guest-ready marker: absent.

The controller was never started beyond a host `py_compile` syntax check. No serial client connected. Because QEMU's configured serial endpoint had `wait=on`, it remained before guest execution until PID `17032` was stopped. Final readback showed the PID absent and port `45680` released.

### Transfer, APT, and simulation non-execution

All downstream evidence is absent:

- `serial.raw`;
- `transport-plan.json` in the F3G run directory;
- `transport-result.json`;
- `guest-evidence.tar.gz`;
- update argv/status/log;
- authenticated-list evidence;
- policy argv/file and eleven-pair parser result;
- simulation argv/status/log/action counts/family plan.

Consequently:

- acknowledged append commands: `0`;
- decodes: `0`;
- guest-script executions: `0`;
- `apt-get update` executions: `0`;
- `apt-cache policy` executions: `0`;
- 109-root simulations: `0`;
- actual package installs: `0`.

No guest repository copy, keyring replacement, source-list access, package database access, or network action occurred. ACC-035's prior signed-update/candidate proof remains valid, but F3G generated no new candidate or solver evidence.

## Defects

1. The PowerShell asynchronous stderr callback was not runspace-safe and terminated the launcher after QEMU started but before mandatory launch proof completed.
2. F3G proved the QEMU `ArgumentList` correction but did not boot the guest or exercise bounded transport.
3. Candidate validation, the retained-base simulation, F3, C1, and OWNER-10 remain incomplete/failed.

## Blockers

- **F3:** blocked on one separately authorized fresh execution that preserves the accepted `ProcessStartInfo.ArgumentList` launch but captures stderr without a PowerShell script-block callback on an unmanaged thread, then runs the unchanged bounded transfer and solver gates.
- **C1:** remains FAIL; F3G authorizes no two-pass run.
- **OWNER-10:** remains FAIL independently.

## Next action

Authorize one fresh disposable successor that keeps every QEMU argument and topology unchanged, replaces only the runspace-unsafe stderr callback with a runspace-independent capture mechanism such as `StandardError.ReadToEndAsync()`, and requires the same PID/listener/stderr/ready gates before using the locked 50-chunk transport. If those gates pass, execute the unchanged single signed update, complete-file eleven-pair validation, and exactly one 109-root simulation. Do not change product source, package inputs, VM geometry, NIC state, CI, or time bounds.
