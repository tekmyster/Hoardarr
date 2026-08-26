# WO-APP-006-C1-F10 result

## Result

**FAIL — structural phase-10 proof did not complete.** The invalid manager-dependent `pmcd.service is-active` oracle was removed and replaced with offline structural checks, but both automatic Linux jobs stop identically at the exact recovery-guard mode assertion. The F10 stop condition applies; no correction, retry, manual run, or artifact download occurred.

- Work-order length: `6994` bytes.
- Work-order SHA-256: `7A2EF9FF9C9B6C9094335D007DB911F1E6EC8E196701CB12B73305208D2A0BE4`.
- Starting local/origin HEAD: `8a0235d5b242eca28c51ed411226f37f52a34de3`.
- F9 handoff length/SHA-256 before editing: `5896` bytes / `19DDA4F43208D132EB264E012FB253D248C1C6032233DA160076BDAC6F05D9D4`.
- Test-only implementation commit: `2f60bfba9a3e8146de3eba418cf9b171bcd5547e`.
- Authorized implementation file: `tests/release/test_offline_appliance.py` only.
- F8 product policy: **FAIL / not accepted**.
- C1: **FAIL**.
- OWNER-10: **FAIL**.

## Evidence

### Scope and structural replacement

- The accepted F9 15-phase trace grammar, bounded parser, EXIT receipt, original-status preservation, trace placement, namespace ownership cleanup, parser negatives, and phases 11–15 remain unchanged.
- The literal manager-dependent `systemctl is-active pmcd.service` query and its `pcp_active_state` / `pcp_active_status` oracle were removed.
- The generated phase-10 harness now checks, in order:
  - the private `$work/run-systemd` root is empty before and after the proof;
  - offline `pmcd.service` preset state is exactly `enabled`;
  - the installed `policy-rc.d` returns `101` for a post-configure `pmcd.service start` request;
  - all recovery guards validate through the extracted production helper;
  - the pmcd guard maps exactly to `/etc/systemd/system/pmcd.service.d/90-hoardarr-offline-recovery.conf`;
  - its owner mapping, tracked inode, regular-file type, root ownership, mode, content, and single-list membership are checked;
  - its exact condition is `/dev/null/hoardarr-offline-service-guard/pmcd.service`;
  - that path and the former marker namespace are absent, the condition is not under the former namespace, and `systemd-analyze condition` cannot evaluate it true.
- The generated harness is `929` lines. Read-only generation validation places phase 10 at lines `770`–`804`, passes the managerless structural contract, and passes Bash syntax validation.
- A direct regression rejects a reintroduced `systemctl is-active pmcd.service` query and rejects omission of the post-configure status-101 assertion.
- Commit `2f60bfba9a3e8146de3eba418cf9b171bcd5547e` changes only the authorized test file. No installer, service-policy, package, workflow, or application byte changed.

### Local QA

- `backend/.venv/Scripts/ruff.exe check tests/release/test_offline_appliance.py`: PASS.
- `backend/.venv/Scripts/ruff.exe format --check tests/release/test_offline_appliance.py`: PASS.
- `python -m py_compile tests/release/test_offline_appliance.py`: PASS.
- `git diff --check -- tests/release/test_offline_appliance.py`: PASS.
- Focused PCP/service-guard/trace set: `8` run, `6` PASS, `2` expected Linux-only skips.
- Complete release discovery, `python -m unittest discover -s tests/release -p 'test_*.py'`: `61` run, `56` PASS, `5` expected platform skips.
- Generated-harness contract readback: `929` lines, phase 10 lines `770`–`804`, PASS.
- Generated-harness Git Bash `-n`: PASS.

### Automatic CI evidence

- CI run `33015068336`: automatic `push`, exact head `2f60bfba9a3e8146de3eba418cf9b171bcd5547e`, terminal **FAILURE**.
- `backend` job `98331047258`: `61` release tests, one failure in the real PCP harness.
- `release-bundle-systemd` job `98331047554`: `61` release tests, the identical one failure.
- `frontend`, `central-fleet-postgres`, `installed-appliance-smoke`, `ubuntu-installer`, and `minio-control-plane-backup`: PASS.
- Both Linux jobs retain the same bounded first receipt:

  ```text
  HPCP|1|BEGIN|10-host-manager-isolation|status=-|line=-|function=-|label=host-manager-isolation
  HPCP|1|EXIT|10-host-manager-isolation|status=1|line=785|function=main|label=host-manager-isolation
  ```

- Read-only reconstruction maps generated line `785` exactly to:

  ```bash
  [[ "$(stat -c %a -- "$pmcd_guard")" == 644 ]]
  ```

- The identical line proves that the initial empty manager root, exact enabled preset, post-configure status `101`, production guard validation, exact pmcd path/mapping/inode, regular-file check, and root-ownership check on lines `770`–`784` all completed before the failure. The mode assertion did not pass, so the remaining condition and closing manager-root checks did not execute.
- The fixture places a no-op `chmod` wrapper first in `PATH` for package-script isolation, while the extracted production guard helper uses `chmod 0644`. That static execution seam is the smallest evidence-backed candidate for the failed fixture mode readback; F10 did not alter or test a correction.
- Phases 11–15 did not begin. No later product-policy claim is accepted.

### Automatic appliance evidence

- Build appliance ISO run `33015068339`: automatic `push`, exact head `2f60bfba9a3e8146de3eba418cf9b171bcd5547e`, terminal **SUCCESS**.
- `build` job `98331046974`: PASS.
- `offline-install` job `98333387327`: SKIPPED with exactly zero steps.
- `hoardarr-appliance`: artifact ID `9624399987`, size `4,408,883,410` bytes, digest `sha256:a4f7ba86c1bb7c3d464031609e2c77ed1c979dd510d974aaed190691c9e27042`.
- `hoardarr-offline-install-inputs`: artifact ID `9624405430`, size `1,072,360,510` bytes, digest `sha256:5024620650941f4341d7b064b443e69fdaf4f74d453061f7951bb4f5d133aa92`.
- Artifact metadata was inspected through the API only. Neither artifact was downloaded.

## Defects

- The executable fixture does not prove the required exact `0644` pmcd recovery-guard mode. Both Linux jobs fail at that assertion before the false-condition and closing no-manager-contact checks.
- The test fixture's broad no-op `chmod` wrapper may intercept the extracted production helper's guard-mode operation. This is a directly observed static seam, not accepted proof of the actual mode or a product defect.
- F8 service-policy evidence remains incomplete because phases 10–15 did not all pass.

## Blockers

- F10 acceptance is blocked by the identical phase-10 mode failure.
- F8, C1, and OWNER-10 remain FAIL and were not advanced to an ordinary install gate.

## Next action

Authorize one narrow fixture-only successor to make package-postinst command isolation coexist with the extracted production helper's real `chmod 0644` behavior, without weakening package-script isolation or guard metadata checks. Preserve the accepted F9 trace and F10 structural oracle, rerun one automatic evidence pair, and stop at the first retained failure. Do not run ordinary C1 or clustered-storage work first.
