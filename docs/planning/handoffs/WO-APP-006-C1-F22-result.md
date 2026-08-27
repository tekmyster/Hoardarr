# WO-APP-006-C1-F22 result

## Result

**FAIL — the Windows/POSIX boundary correction is locally verified and the appliance build passed, but the exact automatic CI run was canceled by a later concurrent push before a valid Linux F21 receipt survived. Classification is `INCONCLUSIVE`. F18, C1, and OWNER-10 remain FAIL.**

F22 preserved the complete uncommitted F21 draft and corrected only its test portability boundary. Windows now proves that its real `0666`-reported temporary file is rejected without output. Linux remains the sole executable authority for root/root, one-link, non-symlink, mode-0600 receipt generation. No production or verification requirement was weakened.

## Identity and ancestry

- Authority: ACC-092 / DEC-2026-08-26-132.
- Work order: 6,927 bytes; SHA-256 `A3E35C3A10D0CE330F5F3D588F24219F4C331F377BFC6C47F3ACBE522146AE28`.
- Locked F21 work order: 8,207 bytes; SHA-256 `9C0AD617080D2BE2FC24057E493538DD258B970A815684E1BF13C9133166E9AD`.
- Locked F21 safe-stop handoff: 5,795 bytes; SHA-256 `DE825B63800CD430011A1636EB1E59F358A83B58D07252D27F0FCF6BDA258848`.
- Locked uncommitted F21 draft: 329,490 bytes; SHA-256 `56A60DE21D3B51D1944BB11AC03F70E471DBA7580BB8FAAA01FB7D71DE16F463`.
- Initial observed local/origin head: `3d3ba51116c986122ca3582e78b4a5901ad69134` / same.
- Concurrent pre-commit parent: `fb54361c46843627463de01849449cfdae6a6a73`.
- Implementation commit: `a6ff03aa696cae67bc489e1a607fc580f1fc5023` (`test(appliance): preserve POSIX receipt boundary`).
- Implementation parent: `fb54361c46843627463de01849449cfdae6a6a73`.
- Authorized implementation scope: `tests/release/test_offline_appliance.py` only.
- Committed diff: 677 insertions, 23 deletions.
- Committed test file: 336,324 bytes; SHA-256 `CD2A062D77DB7C28DD45ED2787A03F537786573A9161AFD7354087B0B61F2378`.
- The unrelated untracked F21 safe-stop handoff and all inherited Storage, Website, Supervisor, frontend, telemetry, roadmap, temporary and evidence paths were preserved and excluded.

## Windows/POSIX correction

- `F21_CAPTURE_ERROR_SCRIPT` still requires all of:
  - regular and non-symlink stderr object;
  - `uid=0`, `gid=0`;
  - mode `0600`;
  - link count 1.
- `_validate_f21_capture_error` still requires emitted `stderr_uid=0`, `stderr_gid=0`, `stderr_mode="0600"`, fixed schema, bounded size, fixed class and hashes.
- Portable checks execute on Windows for fixed classification, cap, secret-like content, multiline framing, unknown stage/status, fixture escape, duplicate record, partial evidence, exact one-call source shape and success-path record absence.
- Exact Windows fixture observation after `Path.chmod(0o600)`: `stat.S_IMODE(st_mode) == 0o666`.
- The unchanged diagnostic rejects that fixture with exactly:

  ```text
  F21 capture stderr metadata invalid
  ```

- Windows rejection proves nonzero exit and absence of both `f21-capture-error.json` and its partial path.
- Nominal success and POSIX ownership/mode/symlink/link-count mutations are isolated only in `test_f21_posix_capture_error_receipt_is_bounded_and_fail_closed`, skipped on Windows with reason: `requires real POSIX root ownership, mode 0600, and link-count semantics`.
- That skip cannot activate on Linux because its predicate is exactly `sys.platform == "win32"`.
- The Windows-only rejection test skips elsewhere with reason: `Windows-specific 0666 metadata rejection boundary`.

## Immutable product and one-call boundary

- `packaging/appliance/install-offline-payload.sh`: unchanged at 59,771 bytes; SHA-256 `62077EF0E6F885CC13D11A882F674B906988ACDF60352B338F631494820C42CF`.
- `packaging/appliance/verify-offline-appliance.sh`: unchanged at 4,227 bytes; SHA-256 `F188D76E7C19BA38472A5125C68D53E428BCF095D36878AC688E56A93FC627AD`.
- Exact plain phase-12 `disable_unmasked_units` call count: 1.
- Retry, fallback, direct helper invocation, replacement systemctl and second cycle added: 0.
- F20 private mount/wrapper and F21 `invoked=true`/strict evidence-free `invoked=false` semantics remain present.

## Local QA

- Focused selection: `4 passed, 2 skipped, 40 deselected, 27 subtests passed` in 0.81 seconds.
- Focused skips:
  - `test_real_noble_pcp_postinst_presets_with_production_service_guard`: `requires Linux mounts`.
  - `test_f21_posix_capture_error_receipt_is_bounded_and_fail_closed`: `requires real POSIX root ownership, mode 0600, and link-count semantics`.
- Complete `tests/release`: `65 passed, 9 skipped, 145 subtests passed` in 67.86 seconds.
- Full-suite skips comprise the two above plus the seven pre-existing Linux/Ubuntu-only boundaries: Linux runtime mounts, Linux Bash condition/guard controls, local systemd-marker mounts, Linux recovery-guard controls, Linux chmod-fixture controls, signed local Linux APT, and Ubuntu 24.04/systemd/Python 3.12 installer-plan coverage.
- Ruff format check: PASS.
- Ruff check: PASS.
- Python compile: PASS.
- `git diff --check`: PASS.
- Exact staged implementation scope: one authorized test file.
- Added-line secret review: no credential, token, private key or secret value; matches were limited to the diagnostic's literal detector and explicit fake rejection strings.

## Automatic evidence

### CI — canceled before valid Linux receipt

- Run `33033961083`, push event, attempt 1, exact head `a6ff03aa696cae67bc489e1a607fc580f1fc5023`.
- Created `2026-08-27T02:39:41Z`; terminal `2026-08-27T02:41:44Z`; conclusion `cancelled`.
- A later concurrent push at `7b6d3809afe78cf77e3937e1b1fa27d04619f62a` created run `33034052710` at `2026-08-27T02:41:25Z` and superseded the F22 CI run through repository concurrency.
- `release-bundle-systemd`, job `98392543103`: canceled during the complete release unittest command at `2026-08-27T02:41:38Z`; it emitted no validated F21 receipt or helper classification.
- `backend`, job `98392543004`: failed in unrelated backend pytest before its release-test step, so it produced no second Linux F21 execution.
- `ubuntu-installer` and `central-fleet-postgres` passed. Other long-running jobs were interrupted by the same concurrency transition.
- Valid Linux F21 receipts: 0.
- Allowed classification: **`INCONCLUSIVE`**.

### Build appliance ISO — pass

- Run `33033961088`, push event, attempt 1, exact implementation head.
- Created `2026-08-27T02:39:41Z`; terminal `2026-08-27T02:48:12Z`; conclusion `success`.
- Build job `98392498950`: PASS in 8m27s, including signed offline repository, release bundle, ISO construction and visible installer checkpoint.
- Manual-only `offline-install`, job `98393865910`: SKIPPED with zero steps.
- Artifact metadata only; no download:
  - `hoardarr-appliance`: ID `9631450427`, 4,408,898,841 bytes, digest `sha256:fa970048896c67199b96434f671822d34d5214b72cc29c094c0b31ac16231d4c`.
  - `hoardarr-offline-install-inputs`: ID `9631452398`, 1,072,379,447 bytes, digest `sha256:cef2906fdbdda624e1c92d7b41fd1b96ae46692641f7bad41e7547567d77a93d`.

## Prohibited-action counters

- Product/verifier/workflow/package/policy edits: 0.
- Linux 0666 allowances or fabricated POSIX metadata: 0.
- Second systemd/SysV cycles: 0.
- Manual workflow dispatches: 0.
- Retries/reruns/cancellations initiated by F22: 0.
- Artifact downloads: 0.
- Ordinary/manual C1 runs: 0.
- Live host/VM/service/storage/credential/website/HA actions: 0.
- Adjacent tasks: 0.

## Defects / blockers

- Repository concurrency canceled the only exact CI run before either required Linux release authority completed.
- No validated Linux F21 receipt survived; helper invocation and the original F20 capture boundary remain unclassified.
- The unrelated backend failure prevented the backend job from reaching its release-test step.
- F18, C1, and OWNER-10 remain FAIL.

## Next action

Authorize only one narrow, quiescent automatic Linux evidence run of the already committed F22 test boundary, with no source correction, retry inside that work item, manual C1, or artifact download. It must require both Linux release executions at one exact head and stop with the first validated `HELPER_INVOKED`, `HELPER_NOT_INVOKED`, `HARNESS_ERROR`, or `INCONCLUSIVE` result. Do not change the product or infer a SysV cause from the canceled run.

