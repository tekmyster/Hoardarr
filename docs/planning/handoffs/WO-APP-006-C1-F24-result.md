# WO-APP-006-C1-F24 result

## Result

**HARNESS_ERROR**

The F24 representation retained the complete bounded output from the same one
production-shaped systemctl call. The two ordered stderr lines are:

1. `Synchronizing state of iscsid.service with SysV service script with /usr/lib/systemd/systemd-sysv-install.`
2. `Executing: /usr/lib/systemd/systemd-sysv-install --root=/ disable iscsid`

The second line identifies an attempted helper argv but does not explicitly
identify why the exact systemctl call returned status `1`. F24 therefore does
not infer unsupported arguments, helper behavior, or a product cause. Phase 12,
C1, and OWNER-10 remain failing.

## Ancestry and scope

- Work order: `WO-APP-006-C1-F24-complete-bounded-systemctl-output.md`, 5,921
  bytes, SHA-256
  `7F7E6395FCF77BA12416D8D642786294E558E08FE1CDDFF40CA591BE0F30D622`.
- Authority: `ACC-099 / DEC-2026-08-26-139`.
- Prior F23 implementation:
  `0c89d4ff29a85884472400285186f45f5f5ddccc`.
- Prior F23 handoff: `ffc411318c600e6fa7d6521457a7cbb2e6b9439e`.
- F24 implementation parent: `0cbb8f7b31fdecb8aaa83bb4aa0081abe474caef`.
- F24 implementation: `b4952b26e4a043ada4ef8da95f25204c5bd8480d`.
- Implementation scope: only `tests/release/test_offline_appliance.py`.
- F24 test file: 354,538 bytes, SHA-256
  `318F45AFBBE9193600D4498484AD15B53914BA2FD43C321B6256F3C669849BE1`;
  Git blob `4bfdb71a8a29a5b9950a3369bc5e1d2f900af1cb`.
- Locked payload remained SHA-256
  `62077EF0E6F885CC13D11A882F674B906988ACDF60352B338F631494820C42CF`.
- Locked verifier remained SHA-256
  `F188D76E7C19BA38472A5125C68D53E428BCF095D36878AC688E56A93FC627AD`.

## Implementation evidence

F24 retains every validated line in order only after the complete raw stream
passes the existing UTF-8, control-character, and secret-like-content gates.
Each stream remains capped at 8 KiB, with at most 64 lines and 512 UTF-8 bytes
per line. The receipt retains exact byte size, SHA-256, ordered lines, and the
trailing-LF state; reconstructing the line representation must reproduce the
validated bytes exactly.

The accepted F23 boundaries remain unchanged:

- exact derived replacement of one discarded-output redirection;
- one `SYSTEMD_OFFLINE=1 systemctl --root=/ disable iscsid.service` execution;
- fixed argv/order/status capture and no second systemctl or SysV cycle;
- separate confined root/root, mode-0600, regular, non-symlink, one-link stdout
  and stderr files;
- inode/device-pinned fixed-argv non-shell bounded privileged reader;
- no chmod/chown/metadata relaxation or fabricated evidence.

## Local validation

- Focused F19/F20/F21/F23/F24 and real-PCP set: 9 tests; 6 passed and 3
  expected Windows platform skips.
- Complete release suite: 77 passed, 10 expected platform skips, 61.426 s.
- Ruff format and check: pass.
- Python compilation: pass.
- `git diff --check`: pass.
- Pre-push staged scope: exactly `tests/release/test_offline_appliance.py`.
- Portable negatives cover total overflow, excessive per-line length, excessive
  line count, invalid UTF-8, disallowed controls, secret-like content, and the
  existing strict metadata/reader failures.

## Automatic evidence

### Original implementation-head CI

- Run `33036414651`, push event, exact head
  `b4952b26e4a043ada4ef8da95f25204c5bd8480d`.
- Terminal result: **cancelled by unrelated repository concurrency**.
- `release-bundle-systemd` job `98399997216` was canceled before authoritative
  F24 Linux evidence completed.
- No retry, rerun, cancellation, or replacement dispatch was performed by F24.

### Supplementary unchanged-blob CI authority

- Automatic run `33036440701`, exact head
  `d0c91516bdc61fa7b61dcf58cc9972cce87aa900`, terminal failure.
- F24 implementation `b4952b26e4a043ada4ef8da95f25204c5bd8480d`
  is an ancestor of that head.
- `tests/release/test_offline_appliance.py` is byte-identical at both heads:
  Git blob `4bfdb71a8a29a5b9950a3369bc5e1d2f900af1cb`.
- `release-bundle-systemd` job `98400077196`: 77 tests in 64.409 s, one expected
  phase-12 failure and one skip.
- Exact call count: `1`.
- Environment: `SYSTEMD_OFFLINE=1`.
- Exact call status: `1`.
- Stdout: 0 bytes, SHA-256
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`,
  ordered lines `[]`, trailing LF false.
- Stderr: 180 bytes, SHA-256
  `87a3ff2e005dc1d6cca3921ee289aacb4da813ec05682310bc1755edbf234659`,
  ordered lines exactly as quoted in Result, trailing LF true.
- The lines themselves do not state why status `1` occurred; classification is
  therefore `HARNESS_ERROR`, not `SYSTEMCTL_REJECTION_CAPTURED`.
- Separate F20 evidence remains `helper.invoked=false`, with no helper output or
  partial evidence. This is an unresolved distinction from the systemctl line
  that logs an attempted `systemd-sysv-install` argv. F24 does not reconcile or
  infer the cause of that distinction.

### Build appliance ISO

- Run `33036414657`, exact F24 head
  `b4952b26e4a043ada4ef8da95f25204c5bd8480d`, terminal **success**.
- Build job `98399957783`: success, 03:27:28Z-03:35:46Z.
- Visible interactive installer checkpoint: success.
- Manual-only offline-install job `98401242616`: skipped with zero steps, as
  expected for an automatic push workflow.
- No artifact was downloaded.

## Defects and blockers

- Complete output now proves the attempted helper argv, but neither retained
  line explicitly states the reason for the status-1 failure.
- The logged attempted helper argv and separate `helper.invoked=false` receipt
  remain unresolved. They must not be treated as proof that the helper ran or
  as proof of a specific failure cause.
- Phase 12, C1, and OWNER-10 remain failing.

## Prohibited-action readback

- Manual workflow dispatches: `0`.
- Retries/reruns/cancellations: `0`.
- Artifact downloads: `0`.
- Additional systemctl/SysV calls: `0`.
- Product/verifier/workflow/package/backend/frontend/storage/HA/website edits:
  `0`.
- Metadata relaxation, chmod/chown, fabricated evidence: `0`.
- Live host/VM/disk/service/credential/browser actions: `0`.
- Adjacent successor work begun: `0`.

## Next action

Only under separate authorization, add one narrowly bounded observation that
reconciles the logged attempted `systemd-sysv-install` argv with the existing
`helper.invoked=false` evidence and preserves the original single systemctl
cycle. Do not change the product finalizer until that contradiction is resolved.
