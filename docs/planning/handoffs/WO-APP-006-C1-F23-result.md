# WO-APP-006-C1-F23 result

## Result

**HARNESS_ERROR**

F23 preserved and validated one exact production-shaped invocation of
`SYSTEMD_OFFLINE=1 systemctl --root=/ disable iscsid.service`. The invocation
returned status `1`, but the bounded diagnostic retained only the first stderr
line:

> Synchronizing state of iscsid.service with SysV service script with /usr/lib/systemd/systemd-sysv-install.

That line describes synchronization and does not identify the status-1
rejection. F23 therefore does **not** classify the systemctl rejection, phase
12, C1, or OWNER-10 as passing. It does not infer or quote the unretained
remainder.

## Ancestry and scope

- Work order: `WO-APP-006-C1-F23-systemctl-failure-receipt.md`, 6,698 bytes,
  SHA-256 `C99E9E04B57362398602EDAF9E2ED095F4910E0DC0500465F596813F4F95DF78`.
- Authority: `ACC-096 / DEC-2026-08-26-136`.
- Starting local/origin HEAD: `977f0fe246e67919ef922aa2c42e625a07566b76`.
- Implementation commit: `0c89d4ff29a85884472400285186f45f5f5ddccc`.
- Implementation scope: only `tests/release/test_offline_appliance.py`.
- Implemented test file: 353,098 bytes, SHA-256
  `41A059EEC187D14018B82313C6CD801D071DEB333087FBAD7FB39FD85A2752CA`.
- Locked product payload remained SHA-256
  `62077EF0E6F885CC13D11A882F674B906988ACDF60352B338F631494820C42CF`.
- Locked first-boot verifier remained SHA-256
  `F188D76E7C19BA38472A5125C68D53E428BCF095D36878AC688E56A93FC627AD`.

## Local evidence

- Focused F19/F20/F21/F23 plus real-PCP command: 9 tests, 6 passed and 3
  expected platform skips on Windows.
- Complete release suite: 77 passed, 10 expected platform skips, 62.519 s.
- Ruff format/check: pass.
- Python compile: pass.
- `git diff --check`: pass.
- Pre-push staged scope: exactly `tests/release/test_offline_appliance.py`.

The reader validates confinement, regular/non-symlink type, uid/gid `0`, mode
`0600`, and link count `1` before one fixed-argv, non-shell, passwordless-sudo
read. It returns at most 1,025 bytes for the 1,024-byte receipt boundary and
fails closed on nonzero status, overflow, unexpected stderr, metadata drift,
wrong path/type, hard link, or symlink. No chmod/chown/receipt replacement is
used to make root evidence readable.

The harness derives its instrumented finalizer from the exact locked production
function and makes exactly one redirection substitution. The command argv,
`SYSTEMD_OFFLINE=1`, ordering, and execution count remain unchanged. Stdout and
stderr are separate confined root/root mode-0600 one-link files with 8-KiB
caps, UTF-8/control/secret validation, size and SHA-256 evidence.

## Automatic evidence

### CI

- Run: `33035550999`, event `push`, exact head
  `0c89d4ff29a85884472400285186f45f5f5ddccc`.
- Terminal result: **failure**.
- Linux release job: `98397355848` (`release-bundle-systemd`), terminal
  failure; 77 tests in 66.274 s, one expected F23/phase-12 failure, one Linux
  skip.
- One exact disable call: `systemctl --root=/ disable iscsid.service`.
- Environment: `SYSTEMD_OFFLINE=1`.
- Call count: `1`.
- Status: `1`.
- Stdout: 0 bytes, SHA-256
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- Stderr: 180 bytes, SHA-256
  `87a3ff2e005dc1d6cca3921ee289aacb4da813ec05682310bc1755edbf234659`.
- Retained bounded first line: the synchronization line quoted in Result.
- Separate F20 proof: `helper.invoked=false`; no helper output or partial helper
  evidence existed and the private helper/mount identity remained exact.
- The independent backend job `98397355837` also failed outside F23 scope: 950
  passed and 2 failed in `backend/tests/test_lio_readback.py` (`deep_nesting`).
  F23 did not modify or correct backend code/tests.
- Other CI jobs: frontend, MinIO control-plane backup, central fleet Postgres,
  Ubuntu installer, and installed-appliance smoke passed.

### Build appliance ISO

- Run: `33035551028`, event `push`, exact head
  `0c89d4ff29a85884472400285186f45f5f5ddccc`.
- Build job: `98397355774`, **success**, 03:10:25Z-03:20:07Z.
- Visible interactive installer checkpoint: **success**,
  03:14:24Z-03:17:38Z.
- Appliance artifact upload and retained offline-install inputs: **success**.
- Manual-only `offline-install` job `98398855232`: skipped with zero steps, as
  expected for this automatic push workflow.
- No artifact was downloaded.

## Defects and blockers

- The current F23 bounded representation persists only the first validated
  stderr line. The retained line does not identify why the real systemctl call
  returned `1`; classification must remain `HARNESS_ERROR`.
- Phase 12 remains failing. C1 remains failing. OWNER-10 remains failing.
- The unrelated backend deep-nesting failures keep the aggregate CI run red,
  but they do not alter the F23 Linux evidence classification.

## Prohibited-action readback

- Manual workflow dispatches: `0`.
- Retries/reruns/cancellations: `0`.
- Artifact downloads: `0`.
- Additional systemctl or SysV cycles: `0`.
- Product/verifier/workflow/package/backend/frontend/storage/website/HA edits:
  `0`.
- Live host/VM/service/disk/credential/deployment/browser actions: `0`.
- Adjacent roadmap items begun: `0`.

## Next action

Separately authorize one narrow F24-style test-observability successor that
retains a bounded sanitized representation capable of identifying the decisive
stderr line(s) from the same single existing systemctl call. Do not change the
product finalizer or add another call until that evidence identifies the real
rejection.
