# WO-APP-006-C1-F9 result

## Result

**PASS — PCP harness failure observability only.** The unchanged real Noble PCP harness now retains a strict ordered phase trace, preserves its original exit status, and reports the same first failing phase in both automatic Linux jobs. No production installer, service-policy, package, workflow, or application source was changed.

- Work-order length: `5582` bytes.
- Work-order SHA-256: `DB1907A8DA7F3F50009682EB43B8328E4DD3D88F360D9BAA0C2A854482486DDE`.
- Starting local/origin HEAD: `6f08466a665de9f039a55a7a064837349780f29f`.
- Observability implementation commit: `2379d072730231633fa9fef66ddfa03b06fbcddd`.
- Authorized implementation file: `tests/release/test_offline_appliance.py` only.
- F8 product policy: **FAIL / not accepted**.
- C1: **FAIL**.
- OWNER-10: **FAIL**.

## Evidence

### Trace and parser contract

- Fifteen fixed phases cover fixture creation, package download/hash/extraction, private mount setup, old-failure reproduction, guard preparation, real PCP configuration, all-denied presets, host-manager isolation, interrupted retention, final disable/readback, retained-manifest validation, peer isolation, and fixture-cleanup handoff.
- The Bash harness uses `set -Eeuo pipefail`, ordered `BEGIN`/`PASS` markers, and one bounded `EXIT` receipt. The receipt contains only the fixed phase/label, numeric status, bounded Bash line, and sanitized function name; it never records the dynamic command, environment, package contents, or arbitrary output.
- The trace is a regular file directly below the exact purpose-created temporary root and outside the namespace mount tree. Python validates that location, restores ownership only below the exact validated namespace path, then reads and validates the trace before evaluating subprocess success.
- The parser rejects missing phases, duplicate markers, out-of-order markers, unknown phases, multiple terminal receipts, malformed status, malformed line, oversized lines, environment-like extra fields, and traces outside the fixture root.
- A dedicated Bash regression exits `73`; the process status and parsed terminal status both remain exactly `73`. A direct Git Bash execution of that regression also passed locally.

### Local QA

- `backend/.venv/Scripts/ruff.exe check tests/release/test_offline_appliance.py`: PASS.
- `backend/.venv/Scripts/ruff.exe format --check tests/release/test_offline_appliance.py`: PASS.
- `python -m py_compile tests/release/test_offline_appliance.py`: PASS.
- `git diff --check -- tests/release/test_offline_appliance.py`: PASS.
- Focused service-guard/PCP/parser set: `7` run, `5` PASS, `2` expected Linux-only skips.
- Complete release discovery, `python -m unittest discover -s tests/release -p 'test_*.py'`: `60` run, `55` PASS, `5` expected platform skips.

### Automatic CI evidence

- CI run: `33013346546`, automatic `push`, exact head `2379d072730231633fa9fef66ddfa03b06fbcddd`, terminal **FAILURE**.
- `backend` job `98325048381`: `60` release tests, one failure in the real PCP harness.
- `release-bundle-systemd` job `98325048728`: `60` release tests, the same one failure.
- `frontend`, `ubuntu-installer`, `minio-control-plane-backup`, `central-fleet-postgres`, and `installed-appliance-smoke`: PASS.
- Both failing jobs retained the identical first receipt:

  ```text
  HPCP|1|BEGIN|10-host-manager-isolation|status=-|line=-|function=-|label=host-manager-isolation
  HPCP|1|EXIT|10-host-manager-isolation|status=1|line=776|function=main|label=host-manager-isolation
  ```

- Read-only reconstruction of the generated 901-line harness maps line `776` exactly to the existing assertion:

  ```bash
  [[ "$pcp_active_state" == inactive && "$pcp_active_status" -eq 3 ]]
  ```

- Phases `01` through `09` completed with ordered `BEGIN`/`PASS` markers in both jobs. Phase `10-host-manager-isolation` began and failed before its `PASS`. There was no variance between the two Linux jobs.
- The trace intentionally does not expose the dynamic `pcp_active_state` content; F9 establishes the failing assertion without leaking arbitrary command output.

### Automatic appliance evidence

- Build appliance ISO run: `33013346538`, automatic `push`, exact head `2379d072730231633fa9fef66ddfa03b06fbcddd`, terminal **SUCCESS**.
- `build` job `98325048488`: PASS.
- `offline-install` job `98327644675`: SKIPPED with exactly zero steps.
- `hoardarr-appliance`: artifact ID `9623723213`, size `4,408,884,473` bytes, digest `sha256:7d05ac0089329eb8821704e282e8c4c34b36133701073feab357d3cd14d34503`.
- `hoardarr-offline-install-inputs`: artifact ID `9623730073`, size `1,072,358,186` bytes, digest `sha256:5dba452dfda640a97af7a01a163e82e378d0a096a1803c77d6208316af0e55a4`.
- Artifact metadata was inspected through the API only. No artifact was downloaded.

## Defects

- The real Noble PCP harness still fails the target activity assertion in phase `10-host-manager-isolation`. F9 does not determine or alter the observed state/status values because recording dynamic command output was prohibited.
- F8's required executable service-policy evidence is therefore still incomplete. The successful appliance build is not product-policy acceptance.

## Blockers

- F8 remains blocked on a separately authorized diagnosis/correction of why the target `pmcd.service` activity readback does not equal the accepted `inactive` / status `3` contract after the real package configure path.
- C1 and OWNER-10 remain independently blocked and were not advanced.

## Next action

Authorize one narrow successor to reproduce the phase-10 target activity readback under the same private namespace and determine whether the defect is in the production target-state query or the executable fixture. Preserve the now-accepted trace contract, and do not broaden into an ordinary two-pass run or clustered-storage packages until the PCP service-policy regression passes.
