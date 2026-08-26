# WO-APP-006-C1-F11 Result

## Result

FAIL. The confined `chmod` fixture boundary was implemented in the one authorized test file, but the first automatic Linux execution stopped earlier in phase 05 because the new hard-link negative attempted to cross the fixture bind-mount filesystem boundary. Per the work order, no correction, retry, rerun, manual workflow, or artifact download was performed.

F8, C1, and OWNER-10 remain FAIL. This result does not accept the service-policy proof or authorize an ordinary C1 run.

## Exact commits and authorized diff

- Required starting local/origin HEAD: `745bacd797f949e4dd55151ec84d2c579b27d0c4`.
- Implementation commit: `1a9bd98cf78bb2987845a13bafde992bfcfa3988` (`test(appliance): confine recovery guard chmod fixture`).
- Implementation diff: only `tests/release/test_offline_appliance.py`, 115 insertions and 1 deletion.
- The wrapper retained package-maintainer isolation and delegated only exact `0644 -- <one path>` requests after fixed-binary, argument-count, canonical root/parent/target, denied-unit mapping, production basename, regular-file, non-symlink, and link-count-one checks.
- A bounded receipt recorded unit, temporary basename, and mode. Count, uniqueness, identity, and receipt-hash stability checks were added.
- Alternate mode, missing `--`, extra operand, traversal, outside-root, wrong basename, wrong directory, symlink, hard-link, and unrelated package-path negatives were added.
- Production payload, policy, package roots/families, workflow, and appliance files were unchanged.

## Local QA

- Work-order identity: 5,613 bytes; SHA-256 `8BA1C8F72B8EB91DE7917C818E8CC0B7F704C6FB94377C93BD8DFA096056425E` — PASS.
- `backend/.venv/Scripts/ruff.exe check tests/release/test_offline_appliance.py` — PASS.
- `backend/.venv/Scripts/ruff.exe format --check tests/release/test_offline_appliance.py` — PASS (one file already formatted; cache-write warning only).
- `python -m py_compile tests/release/test_offline_appliance.py` — PASS.
- Focused PCP/nonactivation/trace/real-PCP selection — 4 run, 2 PASS, 2 expected Windows platform skips.
- `python -m unittest discover -s tests/release -p 'test_*.py' -v` — 61 PASS, 5 expected platform skips, 0 failures, 67.714 seconds.
- Generated fixture wrapper/setup segment parsed with Git Bash `bash -n` — PASS, 4,561 UTF-8 bytes. The complete generated Linux harness subsequently parsed and executed in both automatic Linux jobs through phase 05.
- `git diff --check -- tests/release/test_offline_appliance.py` — PASS.
- Staged-path gate before implementation commit — exactly `tests/release/test_offline_appliance.py`.

## Automatic CI and appliance evidence

### CI

- Run: `33016516689`, push event, exact head `1a9bd98cf78bb2987845a13bafde992bfcfa3988`.
- Terminal result: FAILURE.
- `release-bundle-systemd` job `98336032847`: FAILURE in complete release discovery; 61 tests, 1 failure, 57.959 seconds.
- `backend` job `98336032933`: FAILURE in complete release discovery; 61 tests, 1 failure, 56.285 seconds.
- Both failures were identical:
  - `HPCP|1|BEGIN|05-mount-namespace|status=-|line=-|function=-|label=mount-namespace`
  - `HPCP|1|EXIT|05-mount-namespace|status=1|line=771|function=main|label=mount-namespace`
  - decisive command error: hard-link creation from the namespace work directory into `/etc/systemd/system/corosync.service.d/.hoardarr-recovery.LNK001` failed with `Invalid cross-device link`.
- The trace proves phases 01–04 completed, phase 05 began, and the harness exited with the original status 1. It did not reach guard preparation or phase-10 mode validation.
- Other terminal CI jobs: `ubuntu-installer` PASS, `installed-appliance-smoke` PASS, `minio-control-plane-backup` PASS, `frontend` PASS, and `central-fleet-postgres` PASS.
- CI artifacts were not downloaded:
  - `controller-redundancy-browser-evidence`, ID `9624812107`, 8,006,461 bytes, digest `sha256:9721137b22ecc9624be58c8e99d740192285ff7ee9dadc3dadc893a3349c4e7c`.
  - `minio-control-plane-backup-evidence`, ID `9624784824`, 1,255 bytes, digest `sha256:b2e88eb263e2a5198a91c4d230e8c639e761a69ca451e2fdad3573fde06bbe9d`.

### Build appliance ISO

- Run: `33016516711`, push event, exact head `1a9bd98cf78bb2987845a13bafde992bfcfa3988`.
- Job: `build` / `98336032545`.
- Terminal result: SUCCESS.
- The visible interactive installer checkpoint completed successfully. The manual-only offline-install job was not invoked.
- Artifacts were recorded by metadata only and were not downloaded:
  - `hoardarr-appliance`, ID `9624966855`, 4,408,883,023 bytes, digest `sha256:92a0b650f03855e6a9810f307dc6daf3d7e13f5c38c24777c7539d78e45d3a11`.
  - `hoardarr-offline-install-inputs`, ID `9624969756`, 1,072,358,146 bytes, digest `sha256:31825cc7ec3683070b9122f3ecf756edf4d5fb2cd18921eb73cd4cece6627f0c`.

## Defects and blockers

- The hard-link negative created its source at `$work/.hoardarr-recovery.OUT001` and its destination under the separately bind-mounted `/etc/systemd/system`. Those paths are different mount/filesystem identities in the real Linux fixture, so `ln` correctly returned `EXDEV` before the wrapper was called.
- Consequently, the automatic Linux evidence does not yet prove the accepted wrapper boundary, the exact production `0644` delegation, the complete F9 15-phase trace, or the F10 phase-10 managerless oracle.
- No product defect was identified by this run. The failure is confined to the executable test fixture.

## Next action

Supervisor authorization is required for one narrow test-only successor: create the hard-link negative's source and destination on the same private `/etc/systemd/system` bind-backed filesystem while keeping the source outside the allowed denied-unit recovery directory, then run one fresh automatic CI/appliance pair. Do not alter the accepted wrapper predicate or production code.
