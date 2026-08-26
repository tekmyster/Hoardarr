# WO-APP-006-C1-F14 Result

## Result

**FAIL / not accepted for the bounded causal-validation objective.** The test-only implementation reached the unchanged real phase-10 closing assertion in both Linux jobs, which means the source-identity checks and the separate positive/negative controls completed before the real F13 command created its marker. However, the new durable source receipt was malformed and rejected by the strict parser in both jobs. The causal receipt uses the same faulty separator construction and therefore is not independently reviewable either.

No correction, retry, manual run, or artifact download occurred. The existing real marker, exact condition command, closing emptiness assertion, original status, F9 trace, and all F10-F13 guards remain unchanged. F8, C1, and OWNER-10 remain **FAIL**.

## Commits and authorized diff

- Starting local/origin HEAD: `d0980ecb5eaed970893e51f9c62ccfc713518318`.
- Work-order identity: 7,394 bytes; SHA-256 `33921F0D0CE384E68AB4F66FFECE7553CBF0988EB4115E573EBE5135C83A5764`.
- F13 handoff identity: 7,285 bytes; SHA-256 `6A423D7913EE5DB551E47687D43349AA7666E90A7C316CE891FF4F9BF5DE9C5D`.
- Implementation commit: `8dde76dd984be9713b1693f772fa64041e7465f6` (`test(appliance): prove systemd marker causality`).
- Authorized implementation diff: only `tests/release/test_offline_appliance.py`, 571 insertions and 3 deletions.
- Committed test-file identity: 194,054 bytes; SHA-256 `CBDAE796CEF8180504C3D29B837BD9426CC3B7DC602EA25F00D948EB51CC5B8D`.
- No product payload, policy, package, workflow, installer input, wrapper, or other test file changed.

## Version-matched primary source

The Linux harness fail-closed gates require `/usr/bin/systemd-analyze` to be a root-owned regular non-symlink owned by binary package `systemd`, require its first `--version` line to agree with the installed package version, and accept only a `255.4-*` Noble package. The completed `release-bundle-systemd` package-install step independently records exact package version `255.4-1ubuntu8.17`. The backend harness passed the same executable/package/version gates, but its exact Ubuntu suffix was not durably recovered because receipt parsing failed.

Primary upstream identity:

- Repository: `https://github.com/systemd/systemd-stable`.
- Immutable release tag: `v255.4`; annotated tag object `4003dd6754e3446691402d3cc389fbfd4faccc90`; peeled commit `387a14a7b67b8b76adaed4175e14bb7e39b2f738`.
- `src/analyze/analyze-condition.c`, `verify_conditions` lines 68-114 at that revision; exact blob-byte SHA-256 `3f89216b21faa202099f290615cdd8ed4ee5f98a2f0094242d447670248a9b89`.
- `src/core/manager.c`, `manager_ready` lines 1891-1910 at that revision; exact blob-byte SHA-256 `58af3c261e43b6de343be931a46c049152eb57c856f24f81dd53bdd9abafa72e`.
- Immutable source URLs:
  - `https://raw.githubusercontent.com/systemd/systemd-stable/387a14a7b67b8b76adaed4175e14bb7e39b2f738/src/analyze/analyze-condition.c`
  - `https://raw.githubusercontent.com/systemd/systemd-stable/387a14a7b67b8b76adaed4175e14bb7e39b2f738/src/core/manager.c`

The source chain is exact: `verb_condition()` calls `verify_conditions()`; `verify_conditions()` creates a minimal test manager and calls `manager_startup()`; `manager_startup()` calls `manager_ready()`; and `manager_ready()` calls `touch_file("/run/systemd/systemd-units-load", false, ..., 0444)`. The executable control was designed to verify the resulting fresh-root object as a root-owned, zero-byte, non-symlink regular file with mode `0444`, link count 1, on the private root's filesystem.

## Executable control result

Both Linux executions reached generated line 1218, the unchanged final real-PCP emptiness assertion. The F14 insertion adds exactly 191 lines; the original F13 closing assertion shifted from line 1027 to 1218. Therefore every preceding fail-closed source and disposable-control assertion completed in both jobs:

- Negative control: fresh bind-backed private `/run/systemd`; empty before and after; no command executed; no endpoint or entry appeared; exact root removed.
- Positive control: separate fresh bind-backed private `/run/systemd`; empty and endpoint-free before; exact false command executed once with status 1; only `systemd-units-load` appeared; the shell required regular/non-symlink, root:root, `0444`, zero bytes, link count 1, same filesystem, no sockets/endpoints; exact marker/root removed.
- Underlying real PCP `/run/systemd` bind identity was restored before the unchanged F13 proof.
- The unchanged real condition then returned 1 and the unchanged closing assertion failed after its real `systemd-units-load` appeared.

This establishes execution ordering and that the controls themselves did not fail. It does **not** satisfy the required durable causal-receipt acceptance because neither new receipt was validly parsed.

## Exact receipt defect

The generator writes receipt rows with shell arguments such as:

```text
'PACKAGE\tsystemd\t...'
```

under `printf '%s\n'`. `%s` does not interpret backslash escapes in its argument, so the file contains literal backslash-plus-`t` bytes instead of tab separators. `_validate_systemd_source_receipt()` correctly rejects the first package row as `systemd package identity is malformed`. The causal receipt is constructed the same way and was not reached because source validation fails first. Local parser tests used real tab characters and therefore did not exercise the generator-to-parser byte boundary.

Consequences:

- Exact per-job executable SHA-256 and complete `systemd-analyze --version` output hash are not durably recoverable from the terminal logs.
- Intended positive/negative receipt records cannot be reported as validated receipts.
- The source and control assertions did execute, but F14's independently reviewable evidence contract is unmet.

## Local QA

- Ruff format: PASS; final check reports already formatted.
- Ruff check: PASS.
- Python compile: PASS.
- Focused manager/source/causal/PCP contract tests: 4 PASS.
- Complete `tests/release` discovery: 64 PASS, 5 expected platform skips, 0 failures, 64.210 seconds.
- Generated F14 phase-10 shell: 17,949 UTF-8 bytes; Git Bash `bash -n` PASS.
- Parser negatives cover wrong/missing package, package version, executable path/hash, upstream revision, source path/function/hash, marker presence/type/size/mode/owner/link/filesystem, extra entry, endpoint state, command/status, negative-control drift, cleanup drift, unknown/control/unbounded data, and outside-root receipt paths.
- `git diff --check`: PASS.
- Staged-path gate: exactly `tests/release/test_offline_appliance.py`.

## Automatic CI evidence

- CI run `33020796180`, push event, exact head `8dde76dd984be9713b1693f772fa64041e7465f6`: terminal **FAILURE**, 2026-08-26T22:47:11Z through 22:49:49Z.
- `release-bundle-systemd` job `98350402713`: **FAILURE**, 64 tests, one failure, 60.291 seconds.
- `backend` job `98350402566`: **FAILURE**, 64 tests, one failure, 60.015 seconds.
- Both jobs report the same bounded trace:

```text
HPCP|1|BEGIN|10-host-manager-isolation|status=-|line=-|function=-|label=host-manager-isolation
HPCP|1|EXIT|10-host-manager-isolation|status=1|line=1218|function=main|label=host-manager-isolation
```

- Both jobs then reject the source receipt with exactly `systemd package identity is malformed`.
- Other CI jobs passed: `central-fleet-postgres` `98350402407`; `minio-control-plane-backup` `98350402575`; `ubuntu-installer` `98350402686`; `frontend` `98350402710`; `installed-appliance-smoke` `98350402735`.
- CI artifact metadata only; nothing downloaded:
  - `controller-redundancy-browser-evidence`: ID `9626490650`, 8,006,174 bytes, digest `sha256:ea8e59aa4cdc14df670026b4e6af116c12a5d3f0c2b8b9c57e4c688cdbe57c66`.
  - `minio-control-plane-backup-evidence`: ID `9626485430`, 1,256 bytes, digest `sha256:bf89f112ccc55bf5669eff88225a24a47edfbe07fda901f819b2ec6d79cf76c1`.

## Automatic appliance evidence

- Appliance run `33020796196`, push event, exact head `8dde76dd984be9713b1693f772fa64041e7465f6`: terminal **SUCCESS**, 2026-08-26T22:47:11Z through 22:55:22Z.
- `build` job `98350402314`: **SUCCESS**, 2026-08-26T22:47:14Z through 22:55:21Z.
- Manual-only `offline-install` job `98352157942`: **SKIPPED**, zero executed steps.
- Artifact metadata only; nothing downloaded:
  - `hoardarr-appliance`: ID `9626619106`, 4,408,880,454 bytes, digest `sha256:1f1b82a33c37b4e4ccdcb679e94adee33ae43f7b82ebbdcb4f8d37869e6fb8bf`.
  - `hoardarr-offline-install-inputs`: ID `9626621785`, 1,072,358,187 bytes, digest `sha256:d9b29229c8a4fb6207e3589e95e57324139cede4a8843a26c21ef10efca20d58`.

## Prohibited-action counters

- Manual workflow dispatches: 0.
- Workflow retries/reruns: 0.
- Artifact downloads: 0.
- Product/payload/policy/package/workflow edits: 0.
- Real-marker allowlist/removal/precreation/normalization: 0.
- Dependency or `strace` installs/downloads: 0.
- Live-system, VM, storage, cluster, website, credential, or protected-media actions: 0.

## Defect, blocker, and next action

F14 is blocked by one deterministic test-evidence encoding defect: the shell generator emits literal `\t` characters while the strict parsers require tab-delimited receipts. The smallest separately authorized successor should change only the receipt writer to emit real tab bytes (or an equivalently exact encoding), add an executable generator-to-parser byte test for both receipts, and inspect one fresh automatic pair. It must preserve the source/control logic, real F13 marker and command, final emptiness assertion, and original status exactly. Do not alter the oracle in that correction.
