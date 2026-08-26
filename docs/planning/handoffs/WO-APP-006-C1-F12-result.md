# WO-APP-006-C1-F12 Result

## Result

FAIL at a later, newly reached phase. The authorized same-filesystem hard-link placement correction worked: both Linux jobs passed phases 05–09, including the hard-link negative, production guard preparation, PCP configuration, all denied presets, the confined `chmod` receipt checks, and the exact phase-10 `0644` pmcd guard assertion. Both jobs then failed identically at the final phase-10 assertion that the private manager-root directory remained empty.

No correction, retry, rerun, manual workflow, or artifact download was performed. F8, C1, and OWNER-10 remain FAIL.

## Exact commits and diff

- Required starting local/origin HEAD: `89326535039d90e1a5d67fb03e859c1deb89213c`.
- F11 handoff identity reverified: 5,862 bytes; SHA-256 `A08721AFBD3DE18D80E25602C6FBCCE3D9135C4F78AB22CA0133CE6FB73777D6`.
- F12 work-order identity reverified: 5,165 bytes; SHA-256 `79D0F037AFBD8CFD8554461264E24729762AD3C63705294256FB06DEA4F840BD`.
- Implementation commit: `3f456df86b2224ffbe71955a465b81c6419f28ad` (`test(appliance): keep hardlink negative on private mount`).
- Authorized diff: only `tests/release/test_offline_appliance.py`, 21 insertions and 4 deletions.
- The `$work/.hoardarr-recovery.OUT001` outside-root negative remains unchanged.
- The hard-link source is now the explicit private path `/etc/systemd/system/.hoardarr-hardlink-negative-source`, directly below the private bind root and outside every denied-unit `.d` directory.
- Source and shaped destination are required to have equal device and inode identities, link count 2 at both names, mode `0600` at both names, and zero delegation receipt. Both exact names are removed and read back absent.
- The F11 wrapper is byte-identical after newline normalization: 1,746 bytes; SHA-256 `CC0E926992EFC68A0FB72B0C19892D7B4DFE0C807FE9CD8A480B7371D37582D4`.
- No production payload, policy, package, workflow, appliance, or product file changed.

## Local QA

- `backend/.venv/Scripts/ruff.exe check tests/release/test_offline_appliance.py` — PASS.
- `backend/.venv/Scripts/ruff.exe format --check tests/release/test_offline_appliance.py` — PASS; one file already formatted.
- `python -m py_compile tests/release/test_offline_appliance.py` — PASS.
- Focused PCP/nonactivation/trace/real-PCP selection — 4 run, 2 PASS, 2 expected Windows platform skips, 0 failures, 0.044 seconds.
- `python -m unittest discover -s tests/release -p 'test_*.py' -v` — 61 PASS, 5 expected platform skips, 0 failures, 68.565 seconds.
- Full generated guest harness: 45,068 UTF-8 bytes; phases 05–15 each present exactly once; Git Bash `bash -n` PASS. Host-side trace setup retains phases 01–04.
- `git diff --check -- tests/release/test_offline_appliance.py` — PASS.
- Staged-path gate — exactly `tests/release/test_offline_appliance.py`.

## Automatic CI evidence

- Run `33017822672`, push event, exact head `3f456df86b2224ffbe71955a465b81c6419f28ad` — terminal FAILURE.
- `release-bundle-systemd` job `98340577530` — 61 tests, 1 failure, 55.279 seconds.
- `backend` job `98340577220` — 61 tests, 1 failure, 60.768 seconds.
- The two Linux failures are identical. Complete receipts prove phases 01–09 began and passed. The first terminal evidence is:
  - `HPCP|1|BEGIN|10-host-manager-isolation|status=-|line=-|function=-|label=host-manager-isolation`
  - `HPCP|1|EXIT|10-host-manager-isolation|status=1|line=934|function=main|label=host-manager-isolation`
- Generated line 934 is exactly:

  ```bash
  [[ -z "$(find "$work/run-systemd" -mindepth 1 -print -quit)" ]]
  ```

- This line follows the successful exact pmcd guard path/owner/inode/type/root-owner/`0644`/single-membership/content/condition checks and the expected false `systemd-analyze condition` result. It is the closing private-manager-root emptiness check.
- Successful CI jobs: `ubuntu-installer` `98340577435`, `frontend` `98340577481`, `minio-control-plane-backup` `98340577505`, `central-fleet-postgres` `98340577536`, and `installed-appliance-smoke` `98340577546`.
- CI artifacts were recorded by metadata only and not downloaded:
  - `controller-redundancy-browser-evidence`, ID `9625341151`, 8,006,435 bytes, digest `sha256:119469646f5f99506481d6737244821fcc991868d707700a4e2bac0370d181af`.
  - `minio-control-plane-backup-evidence`, ID `9625321227`, 1,254 bytes, digest `sha256:618d4518782e13002ac86d4843e40c9474dd1c700bb194082a79cc0a123d6a80`.

## Automatic appliance evidence

- Run `33017822671`, push event, exact head `3f456df86b2224ffbe71955a465b81c6419f28ad` — terminal SUCCESS.
- `build` job `98340508991` — SUCCESS, 2026-08-26 22:00:04Z through 22:08:32Z.
- Manual-only `offline-install` job `98342336158` — SKIPPED with zero execution time.
- Artifacts were recorded by metadata only and not downloaded:
  - `hoardarr-appliance`, ID `9625479024`, 4,408,887,413 bytes, digest `sha256:79b2df21f84a218d3381a571f53d3bacc3eb04d9ea5aa4278753ff8358b89f5f`.
  - `hoardarr-offline-install-inputs`, ID `9625482429`, 1,072,358,181 bytes, digest `sha256:230c202653bcf0795e06c03c094ca71c4a690feef52acdfc8e46c04e5b3a92c6`.

## Defects and blockers

- The F12 EXDEV defect is resolved and the accepted F11 wrapper boundary reached its intended executable evidence, including the exact `0644` guard mode.
- The complete F8 regression remains blocked because `$work/run-systemd` is nonempty at the closing phase-10 check after the structurally false `systemd-analyze condition` command. Current bounded evidence does not identify the entry, type, owner, or creating command; no inference is recorded as fact.
- Phases 11–15 were not reached. F8, C1, and OWNER-10 remain FAIL.

## Next action

Supervisor authorization is required for one narrow test-observability successor that records a bounded, non-secret listing and metadata for the unexpected exact entries under the private `$work/run-systemd` root at phase 10, while preserving the managerless oracle and original exit status. Do not change the wrapper, production code, or assertion until that evidence identifies the cause.
