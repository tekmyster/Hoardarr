# WO-APP-006-C1-D2 result

## Result

- **D2 exact-failure retention: PASS.** The one authorized diagnostic run retained one complete marker sequence, the payload's complete combined stdout/stderr, and exact payload exit status `1`. The first decisive failure is now proven from retained text rather than inferred from a framebuffer.
- **C1 two-pass offline installation: FAIL.** D2 ran diagnostic pass 1 only and is acceptance-ineligible. The payload defect was deliberately not corrected in this work order.
- **OWNER-10 superseding closure: FAIL.** LINSTOR, DRBD 9, DRBD Reactor, installed-but-disabled LINSTOR Gateway, kernel/Secure-Boot compatibility, and the offline Proxmox-plugin sidecar remain outside D2 and unvalidated.
- Implementation commit: `78a468a6307c36e7f63cf67e3fe325068c57c030` (`test(appliance): retain offline payload failure output`), based on required baseline `f2fea9ea5db0ecef76a58dde967d48f1e04c9bc1`.
- Handoff commit: the separate commit containing this document; its exact SHA is reported with the Supervisor handoff because a Git commit cannot contain its own final SHA.

## Evidence

### Scoped implementation and local gates

Only these implementation/test files changed:

- `tests/appliance/offline-user-data`
- `tests/appliance/parse-offline-payload-capture.py`
- `tests/appliance/run-offline-iso-pass.sh`
- `tests/release/test_offline_appliance.py`

The CI-only wrapper:

- runs `/cdrom/hoardarr/install-offline-payload.sh /target /cdrom/hoardarr/offline-repository` exactly once with unchanged argv;
- requires a regular writable target log and writable character device `/dev/ttyS0` before payload execution;
- disables terminal output post-processing with verified `stty ... -opost` before the begin marker;
- preserves the payload side of `PIPESTATUS` and always returns that exact status;
- emits begin, end, exit, size, SHA-256, and capture-complete markers only after all required tee/marker/sync/size/hash operations succeed;
- supports exact ONLCR reversal in the host parser as a guarded fallback, while size/SHA verification rejects arbitrary carriage-return corruption;
- makes the parser dependency diagnostic-only, leaving the ordinary two-pass path and legacy artifact names unchanged;
- treats any malformed capture as sticky-invalid and never reclassifies it as valid later.

Executed after the final material diff:

```text
uv run python -m unittest tests.bootstrap.test_manifests tests.release.test_release_bundle tests.release.test_appliance_assets tests.release.test_offline_appliance
Ran 50 tests in 2.012s
OK (skipped=1)

uv run python -m ruff check scripts/build-offline-apt-repository.py scripts/build-release-bundle.py tests/appliance/parse-offline-payload-capture.py tests/release/test_offline_appliance.py tests/release/test_release_bundle.py
All checks passed!
```

Additional checks:

- Focused offline-appliance suite: 14/14 passed.
- `python -m py_compile` for the parser and focused test: passed.
- CI-only cloud-config parsed with PyYAML; exact payload argv count was one.
- Both embedded Python blocks in `run-offline-iso-pass.sh` parsed with `ast.parse`.
- `git diff --check`: passed for the scoped diff.
- Local Bash syntax execution was unavailable because Windows WSL has no installed distribution. The exact shell path subsequently executed on Ubuntu 24.04 CI through complete evidence finalization.

The required push-triggered ordinary appliance build completed before manual dispatch:

- Run `32930789235`, exact head `78a468a6307c36e7f63cf67e3fe325068c57c030`.
- Build job `98062482425`: success in 8m13s.
- The push event did not run the manual offline-install matrix.
- Ordinary two-pass mode still creates `pass-1` and `pass-2` and retains legacy artifact names `hoardarr-offline-pass-1` and `hoardarr-offline-pass-2`.

### Exact diagnostic execution

- Workflow run: `32931317289`
- URL: `https://github.com/tekmyster/Hoardarr/actions/runs/32931317289`
- Event: `workflow_dispatch`
- Exact head: `78a468a6307c36e7f63cf67e3fe325068c57c030`
- Input `offline_validation_mode`: `diagnostic-pass-1`
- Input `base_iso_url`: `https://releases.ubuntu.com/noble/ubuntu-24.04.4-live-server-amd64.iso`
- Input `base_iso_sha256`: `e907d92eeec9df64163a7e454cbc8d7755e8ddc7ed42f99dbc80c40f1a138433`
- Generated validation ISO SHA-256: `ce83d306d04fed013cf66047321ebe1135a226a2d7a75220b936cf05160be5a0`
- Generated ISO tree-manifest file SHA-256: `c033954bb0f0af967f05229108cdddcdeb7a280f9f2a8dd61fdd62b4ec2bf70c`
- Build job `98063990536`: success, `04:44:05Z` through `04:53:16Z`.
- Only offline job: `98065618486`, `offline-install (pass-1)`, expected workflow failure in 20m36s.
- Diagnostic execution step: `04:55:33Z` through `05:12:30Z`.
- No pass-2 job, retry, or second created diagnostic run occurred. An initial CLI attempt using a raw SHA as the workflow ref was rejected by GitHub with HTTP 422 and created no run; the branch ref was then verified at the exact SHA before this sole dispatch.

Artifact:

- ID: `9593925039`
- Name: `hoardarr-offline-diagnostic-pass-1`
- API-reported archive digest: `sha256:96d0b44e23af5d55c36f42079b3e2213c0207ef6a1b368f17dbbf578f5a46575`
- Compressed size: `2,313,770,620` bytes
- Retention expiry: `2026-09-09T05:12:30Z`
- Downloaded exactly once to `C:\Users\dmessana\Documents\troubleshooting\Hoardarr\.codex-temp\d2-32931317289`.

### Exact retained failure

The retained sequence appears exactly once, in this order:

```text
HOARDARR_OFFLINE_PAYLOAD_BEGIN
<complete payload stdout/stderr>
HOARDARR_OFFLINE_PAYLOAD_END
HOARDARR_OFFLINE_PAYLOAD_EXIT=1
HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE=103578
HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SHA256=2fc51d961b6e5aa31ec581f3c3760aa3aae7df6f97d418f0e7a885932ccb8548
HOARDARR_OFFLINE_PAYLOAD_CAPTURE_COMPLETE
```

The first decisive failing output, at target-log line 2122, is:

```text
offline install refuses to replace a pre-existing unit override: iscsi.service
```

Necessary code context, inspected but not changed, is `packaging/appliance/install-offline-payload.sh:113-118`: the payload aborts whenever the intended temporary-mask destination already exists or is a symlink, before attempting to create its `/dev/null` mask. This is an exact retained assertion, not an inference.

Capture readback:

- Payload exit status: `1`.
- Target log: 103,578 bytes; SHA-256 `2fc51d961b6e5aa31ec581f3c3760aa3aae7df6f97d418f0e7a885932ccb8548`.
- Marker-delimited console/serial capture: 103,776 bytes; SHA-256 `1d78ce6765f1be957d7af4925551dda4bab11fa3366fbd87c9c34555f5ce9e09`.
- Serial transform: `none`; no CRLF or bare CR bytes were present in the executed stream.
- Parser stderr: zero bytes.
- Classification: `offline_payload_failure_observed`.
- Validation mode: `diagnostic-pass-1`; acceptance eligible: `false`.
- Network: `absent (-nic none)`; accelerator: `tcg`; KVM available: `false`.
- Installer start `2026-08-26T04:55:33+00:00`; completion `2026-08-26T05:12:27+00:00`; recorded elapsed 1,014 seconds.
- Status zero is explicitly non-terminating in code/tests. This executed status 1 triggered a five-second flush delay, final framebuffer/HMP/process capture, and bounded termination; the last process/frame checkpoint was `05:12:25Z`, two seconds before recorded completion and within the 15-second post-sentinel budget.
- QEMU stderr: `qemu-system-x86_64: terminating on signal 15 from pid 9245 (bash)`.

### Frame, HMP, process, QCOW2, and protected-media evidence

- 18 framebuffer checkpoints were retained and independently matched the nested frame manifest.
- First frame `installer-0000.ppm`: 720x400, SHA-256 `971ed044118a47ff79db01ef20294729426913a74c292486faa727714c995b60`.
- Final frame `installer-0017.ppm`: 1280x800, SHA-256 `447f4be762f3eee1c56e5b58a3675b99c76587c53dba269ae4b7f8ca1fff5217`.
- 18/18 HMP snapshots, from `04:55:33Z` through `05:12:25Z`, report `VM status: running` and all four configured virtual CPUs.
- 199 process observations span elapsed 0 through 1,012 seconds. Final QEMU observation: PID 9268, state `Sl`, CPU time `00:25:43`, 152% CPU, RSS 5,453,388 KiB.
- `evidence-finalization.txt`: `complete`.
- QCOW2 file: 3,948,347,392 bytes; SHA-256 `d69d5152df9581adacf2a18f9614ea0374ac27824f51712aeb857e620dc193d3`.
- Retained Linux `qemu-img check`: exit 0, no errors.
- Independent Windows QEMU 11.1.0 readback: qcow2, virtual size 34,359,738,368 bytes, cluster size 65,536, dirty flag false, corrupt false; `qemu-img check` exit 0 with no errors.
- Protected-one before/after SHA-256: `ba6170c17c667a8cfb9577349163cae578fd358b5da5d5be1859ee93da52bcae`.
- Protected-two before/after SHA-256: `e9c5cda034df938e8b4e37f8520424aa3ab36aa6078e2c1f12e85f996d5d3cc8`.
- Protected before/after manifests are byte-identical and `protected-diff.txt` is empty.

Checksum readback:

- Recursive manifest: 41 entries; all 41 independently recomputed and matched, with no retained file missing or unlisted.
- Recursive manifest SHA-256: `3b343a56a5fc156a0980c60a2c8cc87e6df64476594040066e0660bab1c440df`.
- Nested frame manifest: 18 entries; all 18 independently recomputed and matched.
- Nested frame manifest SHA-256: `3c954b9b15a9f7e7d8aa225398d0f98140978d9a29d8a4dc8e25741c6c27836a`.
- Evidence-format defect: the recursive manifest records absolute CI runner paths. Independent readback succeeded only after stripping the fixed authenticated `/offline-evidence/pass-1/` prefix. The hashes and file coverage are valid, but a successor should make manifest paths relative for portable direct verification.

## Defects

1. The production offline payload refuses an already-existing `iscsi.service` override during its temporary service-mask setup and exits 1. D2 did not determine from this text alone whether that existing override is the same safe `/dev/null` mask or a conflicting override; the next correction must inspect and distinguish those states fail closed.
2. The diagnostic recursive checksum manifest uses absolute runner paths rather than portable relative paths. Byte coverage and hashes verified after deterministic prefix normalization, but direct readback from a relocated artifact is unnecessarily awkward.
3. The payload failure prevents the installer from reaching subsequent install and first-boot verification. C1 therefore remains unverified.

## Blockers

- C1 remains blocked on a separately authorized minimal correction for the proven `iscsi.service` override collision, followed by two new clean, retry-disabled, no-NIC install/first-boot passes.
- OWNER-10 remains blocked on its separately authorized LINSTOR + DRBD 9 + DRBD Reactor + installed-but-disabled LINSTOR Gateway closure, kernel/Secure-Boot compatibility, offline Proxmox-plugin sidecar evidence, and subsequent offline validation.
- No live-system, physical-storage, credential, or external-infrastructure blocker was needed for D2.

## Next action

Authorize one narrow payload correction that treats an existing override as idempotent only when it is proven to be the exact intended `/dev/null` mask, continues to reject every other file/link target, and adds a regression for the Noble installer-provided `iscsi.service` state before any new diagnostic or two-pass run.
