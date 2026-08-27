# WO-APP-006-C1-F25 result

## Result

**WRAPPER_ENTRY_GUARD_REJECTION — DIAGNOSTIC TRUTH PROVEN; F25 TEST SUITE
FAILED**

The same single production-shaped phase-12 cycle reached the private helper
wrapper with exact argv `--root=/`, `disable`, `iscsid`. The strict pre-guard
receipt records argc `3`; only `expected_argc=false` and `exact_vector=false`.
All six remaining predicates are true: `SYSTEMD_OFFLINE`, package-maintainer
package/name, private PATH, wrapper identity/mode, and copied-real-helper
identity/mode. The unchanged guard therefore returned `REJECTED`, and the
separate post-guard evidence remains `helper.invoked=false`.

This resolves F24's observation boundary: the wrapper was reached, and the
unchanged test-harness guard rejected the three-argument vector before the
copied real helper ran. It does not prove how the uninstrumented helper would
behave, does not authorize accepting `--root=/`, and does not correct the
product finalizer. Phase 12 still fails because `iscsid.service` remains
enabled. C1 and OWNER-10 remain failing.

F25 is not test-clean: its Linux adversarial regression contains a test-only
key mismatch, requesting `helper_identity_mode` where the exact receipt schema
uses `real_helper_identity_mode`. That defect is separate from the accepted
main diagnostic receipt.

## Ancestry and scope

- Work order: `WO-APP-006-C1-F25-helper-entry-guard-receipt.md`, 6,758 bytes,
  SHA-256
  `71FD9BED82EF9CCFC2FC5999C72BBF04E3E1C4B47FCDECB13CF0AFE08783CFED`.
- Authority: `ACC-102 / DEC-2026-08-26-142`.
- F24 implementation/handoff:
  `b4952b26e4a043ada4ef8da95f25204c5bd8480d` /
  `ad89b157f4baf95f652f3d59d254d1d771ab8f8d`.
- F24 locked test blob:
  `4bfdb71a8a29a5b9950a3369bc5e1d2f900af1cb`.
- F25 parent: `7b9cb87f1790b7cc94da7e3dc884989853fca8d5`.
- F25 implementation: `1194fb1b37fbb93eb8144dd1e6db20e340feb508`.
- Implementation scope: exactly one path,
  `tests/release/test_offline_appliance.py`.
- At final handoff readback, the shared local branch was
  `b88999cac8cdcc0f4637d56cba0ea4306f33a809`, after unrelated Storage and
  Website handoff-only child commits; origin remained at the exact F25
  implementation. F25 did not alter or include either concurrent path.
- F25 test blob: `26659f1507994b4887493a574abb7f855a2a8561`.
- F25 test file: 386,475 bytes, SHA-256
  `870D7A2C48B5AEEE12330323230C30D6F0B55D218933F36961D1F51199E0C6C2`.
- Locked payload remained SHA-256
  `62077EF0E6F885CC13D11A882F674B906988ACDF60352B338F631494820C42CF`.
- Locked verifier remained SHA-256
  `F188D76E7C19BA38472A5125C68D53E428BCF095D36878AC688E56A93FC627AD`.

## Accepted diagnostic evidence

The validated entry object reports:

- `entry_reached=true`;
- argc `3`;
- ordered argv classifications:
  1. position 0, `ALLOWLISTED`, value `--root=/`;
  2. position 1, `ALLOWLISTED`, value `disable`;
  3. position 2, `ALLOWLISTED`, value `iscsid`;
- `expected_argc=false`;
- `exact_vector=false`;
- `systemd_offline=true`;
- `dpkg_maintscripts_package=true`;
- `dpkg_maintscripts_name=true`;
- `exact_private_path=true`;
- `wrapper_identity_mode=true`;
- `real_helper_identity_mode=true`;
- `guard_outcome=REJECTED`;
- post-guard `helper.invoked=false`;
- classification `WRAPPER_ENTRY_GUARD_REJECTION`.

The correlated F24 systemctl evidence remains one call, status `1`, with
stderr 180 bytes and SHA-256
`87a3ff2e005dc1d6cca3921ee289aacb4da813ec05682310bc1755edbf234659`.
Its ordered second line reports the same attempted argv. Correlation occurred
only after both bounded receipts validated.

## Local validation

- Focused F19/F20/F21/F23/F24/F25 and real-PCP selection: 8 passed, 4 expected
  Windows/POSIX skips, 44 subtests, 0.84 s.
- Complete local release suite: 69 passed, 11 expected platform skips, 162
  subtests, 61.46 s.
- Final focused recheck after the nonfunctional lint cleanup: 4 passed, 2
  expected platform skips, 34 subtests, 0.24 s.
- Ruff format/check: pass.
- Python compilation: pass.
- `git diff --check`: pass.
- Pre-push staged scope: exactly
  `tests/release/test_offline_appliance.py`.

The Linux-only adversarial test was not exercised locally on Windows. Its
automatic Linux execution exposed the exact test-key defect described below;
no retry or correction was made in F25.

## Automatic CI evidence

- Automatic push run `33037899826`, exact head
  `1194fb1b37fbb93eb8144dd1e6db20e340feb508`.
- Run interval: 2026-08-27 03:57:06Z-03:59:55Z.
- Terminal result: **failure**.

Jobs:

- `release-bundle-systemd` job `98404640865`: **failure**,
  03:57:09Z-03:59:04Z.
- `frontend` job `98404640950`: success, 03:57:10Z-03:59:54Z.
- `backend` job `98404640962`: **failure**,
  03:57:10Z-03:58:33Z.
- `central-fleet-postgres` job `98404641001`: success,
  03:57:10Z-03:57:34Z.
- `ubuntu-installer` job `98404641027`: success,
  03:57:10Z-03:57:32Z.
- `minio-control-plane-backup` job `98404641030`: success,
  03:57:10Z-03:59:25Z.
- `installed-appliance-smoke` job `98404641083`: success,
  03:57:10Z-03:59:32Z.

### Release job results

The release job ran 80 tests in 66.160 s: one failure, one error, and one
skip.

1. **Accepted existing product/harness failure:**
   `test_real_noble_pcp_postinst_presets_with_production_service_guard`
   reached phase 12 and failed because
   `iscsid.service=enabled`. Its retained receipt is the accepted diagnostic
   truth above.
2. **F25 implementation/test defect:**
   `test_f25_entry_writer_is_exclusive_atomic_and_predicate_complete`
   raised `KeyError: 'helper_identity_mode'` in the identity=`helper` subtest.
   The receipt schema correctly names that predicate
   `real_helper_identity_mode`; the test constructed the wrong lookup key.

The F25 error does not invalidate the independently validated real-PCP entry
receipt, but it prevents F25 from being test-clean or accepted as complete.

### Unrelated backend baseline failures

The backend job completed with 950 passed, 2 failed, and 1 warning in 69.82 s.
Both failures are outside F25's one-file scope and concern the inherited LIO
readback deep-nesting behavior:

1. `test_saveconfig_reader_fails_closed[deep_nesting]` did not raise the
   expected `LioReadbackError`.
2. `test_reader_failure_after_targetcli_never_saves_executor_state[False-deep_nesting]`
   observed 3 reads rather than 1.

These backend failures are recorded separately and are not attributed to, or
blurred with, the F25 receipt/test defect.

## Build appliance ISO evidence

- Automatic run `33037899863`, exact head
  `1194fb1b37fbb93eb8144dd1e6db20e340feb508`.
- Run interval: 2026-08-27 03:57:06Z-04:06:39Z.
- Terminal result: **success**.
- Build job `98404640905`: success, 03:57:10Z-04:06:37Z.
- Signed offline repository build: success.
- Release bundle and appliance ISO construction: success.
- Visible interactive installer checkpoint: success,
  04:01:07Z-04:04:11Z.
- Appliance artifact upload: success, 04:04:11Z-04:06:20Z.
- Offline-install input retention: success, 04:06:20Z-04:06:33Z.
- Manual-only `offline-install` job `98406218786`: skipped with zero steps, as
  expected for this automatic push workflow.
- Artifact downloads: zero.

## Defects and blockers

- F25's Linux identity-mode adversarial test uses the wrong receipt key for
  the copied real helper. This is a one-file test defect.
- The accepted diagnostic proves the current private wrapper guard rejects the
  three-argument systemd invocation before the real helper runs.
- Whether the guard should accept and safely classify the exact `--root=/`
  prefix is a separate policy/design decision. F25 does not authorize that
  change.
- Phase 12, F8, C1, and OWNER-10 remain failing.
- The two LIO readback failures are unrelated inherited backend defects and
  require separate ownership.

## Prohibited-action readback

- Implementation pushes: 1 exact authorized push.
- Manual workflow dispatches: 0.
- Retries/reruns/cancellations: 0.
- Artifact downloads: 0.
- Product/verifier/workflow/package/backend/frontend/storage/HA/website edits:
  0.
- Additional systemctl/SysV cycles: 0.
- Live host/VM/disk/service/credential/browser actions: 0.
- F26 or adjacent successor work begun: 0.

## Next action

Under separate authorization, make the smallest one-file test correction in
`tests/release/test_offline_appliance.py`: map the `helper` identity subtest to
the existing exact schema key `real_helper_identity_mode` (while leaving the
`wrapper_identity_mode` case unchanged), then rerun the required focused and
automatic gates once. Do not combine that correction with a guard-policy
change.

Only after the test correction is independently accepted should a separately
authorized policy decision determine whether the private harness may accept
the exact leading `--root=/` argument and what executable safety evidence that
would require. No product finalizer change is justified by F25 alone.
