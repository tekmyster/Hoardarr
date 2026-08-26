# WO-APP-006-C1-D1 result

## Result

- **Diagnostic observability: PASS.** The single authorized diagnostic run retained enough independent evidence to identify the last installer stage, the failure transition, and the post-failure wait state.
- **C1 two-pass offline installation: FAIL.** This work order ran only diagnostic `pass-1`; it is explicitly ineligible for two-pass acceptance.
- **OWNER-10 superseding closure: FAIL.** No LINSTOR, DRBD 9, DRBD Reactor, LINSTOR Gateway, kernel/Secure-Boot, or offline Proxmox-plugin sidecar work was included or tested.
- Implementation commit: `b4ea673088fe514b080440ee9a7d4747f9558d3b` (`Add bounded offline installer diagnostics`), based on required baseline `9205fc36a9a3c9d00a8d4d9c18b8705720446c23`.
- The default dispatch remains a two-pass matrix and retains legacy artifact names `hoardarr-offline-pass-1` and `hoardarr-offline-pass-2`. Explicit `diagnostic-pass-1` creates only pass-1 and names its artifact `hoardarr-offline-diagnostic-pass-1`.

## Evidence

### Implementation and pre-dispatch validation

Changed in the implementation commit only:

- `.github/workflows/appliance.yml`
- `tests/appliance/run-offline-iso-pass.sh`
- `tests/release/test_offline_appliance.py`

Executed locally before commit:

```text
uv run python -m unittest tests.bootstrap.test_manifests tests.release.test_release_bundle tests.release.test_appliance_assets tests.release.test_offline_appliance
Ran 47 tests ... OK (skipped=1)

uv run python -m ruff check scripts/build-offline-apt-repository.py scripts/build-release-bundle.py tests/release/test_offline_appliance.py tests/release/test_release_bundle.py
All checks passed!
```

Additional local readback:

- `git diff --check`: passed for all three scoped files.
- PyYAML parsed `.github/workflows/appliance.yml` successfully.
- Both embedded Python blocks in `run-offline-iso-pass.sh` compiled successfully.
- Local `bash -n` was unavailable because this Windows host has no installed WSL distribution. The exact diagnostic shell path subsequently executed on Ubuntu 24.04 CI through postmortem finalization.

The push-triggered ordinary build was allowed to finish before dispatch:

- Run: `32924803720`
- Head: `b4ea673088fe514b080440ee9a7d4747f9558d3b`
- Result: `success`
- Build job: `98045433209`, `success`
- Offline-install job: skipped as required for a push event.

### Exact diagnostic dispatch

- Workflow run: `32925360941`
- URL: `https://github.com/tekmyster/Hoardarr/actions/runs/32925360941`
- Event: `workflow_dispatch`
- Exact head: `b4ea673088fe514b080440ee9a7d4747f9558d3b`
- Input `offline_validation_mode`: `diagnostic-pass-1`
- Input `base_iso_url`: `https://releases.ubuntu.com/noble/ubuntu-24.04.4-live-server-amd64.iso`
- Input `base_iso_sha256`: `e907d92eeec9df64163a7e454cbc8d7755e8ddc7ed42f99dbc80c40f1a138433`
- Generated CI validation ISO SHA-256: `cacdbe609c28d31cbe9d8691372e92ca44a12adb3d62e746036df5b2d7e30676`
- Generated ISO tree-manifest file SHA-256: `ed8e746086ff71e15d3d0208118133b6339ea7fc32c49365c48cfa808191989f`
- Build job: `98047067068`, `success` in 9m09s.
- Only offline job: `98048714897`, `offline-install (pass-1)`, `failure` in 48m54s.
- No pass-2 job was created. No retry or second dispatch was made.
- Installer step: `03:20:17Z` to `04:05:25Z`, failed as the expected bounded diagnostic classification.
- Artifact upload step: `success`, `04:05:25Z` to `04:07:06Z`.

Artifact:

- ID: `9592523630`
- Name: `hoardarr-offline-diagnostic-pass-1`
- API-reported archive digest: `sha256:0c209a2f30f4f719e1279f380b8a07e21ecffc4394de0c541b84ab6d7c65acd6`
- Compressed size: `2,398,124,119` bytes
- Retention expiry: `2026-09-09T04:05:26Z`
- Independent extracted readback root: `C:\Users\dmessana\Documents\troubleshooting\Hoardarr\.codex-temp\d1-32925360941`

### Exit, accelerator, network, and process evidence

Retained `run.json` reports:

- Schema: 2
- Pass: `pass-1`
- Validation mode: `diagnostic-pass-1`
- Acceptance eligible: `false`
- Network device: `absent (-nic none)`
- Accelerator: `tcg`
- KVM available: `false`
- Installer bound: 2,700 seconds
- First-boot bound: 900 seconds
- Classification: `installer_timeout`
- Bounded runner exit: `124`
- QEMU exit: `null` because the bounded runner terminated QEMU at timeout
- Installer start: `2026-08-26T03:20:17+00:00`
- Installer finish after bounded termination/cleanup: `2026-08-26T04:05:21+00:00`
- Recorded elapsed including termination cleanup: 2,704 seconds
- First boot: `not_started`

QEMU stderr is one line: `qemu-system-x86_64: terminating on signal 15 from pid 9296 (timeout)`. The CI log independently records diagnostic mode `true`, the pinned URL/hash, and the bounded failure message at `04:05:25Z`.

Process identity and liveness:

- Bounded runner PID: 9296
- Observed QEMU child PID: 9299; child discovery: `true`
- `installer-process.tsv`: 530 timestamped observations, elapsed 0 through 2,699 seconds, all for PID 9299.
- Observed process states: `Dl`, `Rl`, and `Sl`.
- QEMU CPU time at the visible installer error (`03:43:45Z`): `00:36:07`; RSS 5,449,148 KiB.
- QEMU CPU time at the last checkpoint (`04:05:10Z`): `00:36:38`; RSS 5,452,456 KiB.
- The process remained live after the installer entered its error prompt. The small CPU-time increase after that point is consistent with a running but largely idle guest; the framebuffer provides the authoritative wait-state evidence.

HMP evidence:

- 45/45 timestamped monitor snapshots report `VM status: running`.
- Every snapshot reports all four configured virtual CPUs.
- First HMP snapshot: `03:20:17Z`; last: `04:05:10Z`.
- HMP was isolated on a Unix-domain socket. No NIC, TCP monitor, guest API, or guest cooperation was used.

### Last proven installer stage and framebuffer progression

All 45 PPM frames were retained and hashed; 18 distinct byte hashes show real progress before the final wait state.

Key checkpoints:

| Frame | Timestamp | SHA-256 | Dimensions | Proven state |
|---|---|---|---|---|
| `installer-0000.ppm` | `03:20:17Z` | `971ed044118a47ff79db01ef20294729426913a74c292486faa727714c995b60` | 720x400 | Ubuntu installer GRUB menu |
| `installer-0001.ppm` | `03:21:19Z` | `cbfba1884bd643ee682e07141263dea3577b181cc5e4f1b3149097f40724a7ad` | 1280x800 | Kernel/system startup output |
| `installer-0022.ppm` | `03:42:44Z` | `0265acb45f1ec1057b513941e4f18b3d37e81827f94d7127ee6cb1c8364f53cd` | 1280x800 | Subiquity late-command starts `/cdrom/hoardarr/install-offline-payload.sh /target /cdrom/hoardarr/offline-repository` |
| `installer-0023.ppm` | `03:43:45Z` | `342cd1984050b59c762c2db37e775d28933a34b16f9cee91e6011eb86a2672a0` | 1280x800 | That command returned non-zero exit status 1; Subiquity wrote an install-fail report and displayed `An error occurred. Press enter to start a shell` |
| `installer-0044.ppm` | `04:05:10Z` | `342cd1984050b59c762c2db37e775d28933a34b16f9cee91e6011eb86a2672a0` | 1280x800 | Same error prompt remained visible immediately before timeout |

Intermediate hashes and frames show storage configuration, target extraction, package installation, bootloader/configuration, OpenSSH installation, and entry into Hoardarr's late commands. The alternating final hashes (`342cd...` and `34ac99...`) represent cursor/prompt rendering; they do not show further installer-stage progress.

This distinguishes the previous ambiguity: the guest was neither merely too slow nor non-progressing. It reached the Hoardarr offline-payload late command, that command failed, and the unattended installer waited at an interactive error prompt until the unchanged timeout.

### Serial, QCOW2, protected media, and checksum readback

- `installer-serial.log`: 0 bytes. Serial remains non-observable, as expected before installed-system serial configuration.
- `evidence-finalization.txt`: `complete`.
- Recursive `SHA256SUMS`: 64 entries independently recomputed; 64 matched, 0 failed, 0 listed files missing, and 0 artifact files unlisted.
- Nested frame `SHA256SUMS`: 45 entries independently recomputed; all 45 matched.
- QCOW2 file length: `4,033,413,120` bytes.
- QCOW2 SHA-256: `7a6c0446b3ee0f235d844610dad7cfd0a533ab31524a06c36c7f13ce28be0b98`.
- Retained Linux `qemu-img check`: exit 0, `No errors were found on the image.`
- Independent host readback used official winget package metadata to download and extract QEMU 11.1.0 without installing it. Windows `qemu-img version 11.1.0 (v11.1.0-12130-ge470268ff4)` reported format `qcow2`, virtual size `34,359,738,368`, cluster size 65,536, dirty flag false, corrupt false, and `qemu-img check` exit 0 with no errors.
- Protected-one before/after SHA-256: `ba6170c17c667a8cfb9577349163cae578fd358b5da5d5be1859ee93da52bcae`.
- Protected-two before/after SHA-256: `e9c5cda034df938e8b4e37f8520424aa3ab36aa6078e2c1f12e85f996d5d3cc8`.
- Protected before/after manifests are byte-identical; `protected-diff.txt` is empty.

## Defects

1. The CI-only offline installer invokes `install-offline-payload.sh`, and that command returns exit status 1 during Subiquity late commands. The retained framebuffer proves the failing command but does not retain that script's preceding stdout/stderr or its exact internal failing assertion.
2. Installer serial output remains unavailable (0 bytes). Framebuffer/HMP now compensates for stage observability, but it does not expose scrolled-off command detail.
3. The run correctly never reached first boot, so service/readiness/package readback remains unexecuted.

## Blockers

- C1 remains blocked on identifying and correcting the exact internal offline-payload failure, followed by two new clean, retry-disabled, no-NIC install/first-boot passes.
- OWNER-10 remains blocked on the separately authorized complete LINSTOR + DRBD 9 + DRBD Reactor + installed-but-disabled LINSTOR Gateway closure, including kernel/Secure-Boot compatibility and offline Proxmox-plugin sidecar evidence, followed by its own offline validation.
- No physical or live-system blocker was encountered or needed for this diagnostic.

## Next action

Authorize one bounded D2 diagnostic change to the **CI-only** `tests/appliance/offline-user-data`: invoke the same offline-payload command through a fail-closed wrapper that preserves its exact exit status while teeing complete stdout/stderr to `/target/var/log/hoardarr-offline-payload.log` and the visible installer console, then retain that target log from the stopped QCOW2 in failure finalization. Review that evidence before changing production installer/package inputs or authorizing another two-pass run.
