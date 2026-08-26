# WO-APP-006-C1-F16 Result

## Result

**FAIL for F16 completion; bounded oracle correction is proven through phase 10, but both automatic Linux jobs agree on a later unchanged phase-11 failure. Supervisor acceptance pending.**

The exact local `systemd-units-load` marker oracle accepted the real phase-10 evidence, removed exactly that one disposable marker, restored the private manager root to empty, and emitted `HPCP|1|PASS|10-host-manager-isolation`. Both Linux executions then entered unchanged phase 11 and failed identically:

```text
HPCP|1|BEGIN|11-interrupted-retention|status=-|line=-|function=-|label=interrupted-retention
HPCP|1|EXIT|11-interrupted-retention|status=1|line=1|function=main|label=interrupted-retention
recovery_guard_condition_paths[watchdog.service]: unbound variable
```

Per the mandatory stop boundary, no phase-11 correction, retry, manual workflow, artifact download, or successor work was attempted. F8, C1, and OWNER-10 remain **FAIL**.

## Identity and authorized change

- Work order: 7,863 bytes; SHA-256 `49C6CE80914D273828A491E8D3A750DC622AD259EA1430E3D96C389E4DF0E589`.
- Accepted F15 implementation: `daad0b85c76c25282e4d0917ad6421a133c6b8ed`.
- Accepted F15 handoff: 10,196 bytes; SHA-256 `C0D27FE5FFE364DE3C1BD7BFAAAAF4EA9F7EEB3D9E526FE094A757C6FFD692CD`; commit/baseline `3fead48c61e390a59298b3e150f6cbc8d780dc6e`.
- Starting local/origin identity was the required `3fead48c61e390a59298b3e150f6cbc8d780dc6e`. Unrelated shared-worktree commits advanced the branch to `89a9f563b4f941362b54dbf5454d6e4b702616eb` before the scoped commit; they were preserved and not modified by this work order.
- F16 implementation commit: `3672863eb620acfbf161536429d2ad4675bbbb9c` (`test(appliance): validate local systemd marker oracle`).
- Authorized implementation diff: only `tests/release/test_offline_appliance.py`, 484 insertions and 4 deletions.
- Committed test file: 219,934 bytes; SHA-256 `4168F2300B63E92EBB1FC2D296B714FC204E42AF7702CD5233AE519E3D02446A`.

The named `validate_and_remove_local_systemd_marker()` oracle runs only after the immutable after-receipt is published. It proves exact private bind identity and mount ID, sole top-level marker path, no deeper entry/socket, regular non-symlink type, mode `0444`, UID/GID `0/0`, zero size/content, link count 1, same private-root device and stable inode/metadata. It hashes and syncs the receipt, removes only `/run/systemd/systemd-units-load` using one fixed non-wildcard `/usr/bin/rm -- "$marker"`, syncs the private root, proves it empty, and proves the receipt is byte-identical afterward.

## Preserved boundaries

Comparison against F15 implementation `daad0b85c76c25282e4d0917ad6421a133c6b8ed`:

- Executed `PCP_SYSTEMD_SOURCE_RECEIPT`: byte-identical; 3,861 bytes; SHA-256 `A8B4DB78232F5F28B9B4BBF3B869A2286B0FD70DF5E07047CBFA7B31C93F865B`.
- Executed `PCP_SYSTEMD_CAUSAL_PROOF`: byte-identical; 7,723 bytes; SHA-256 `AF78C056977F0721A14888E4B318F9F20A84A9AD3A67E87D6910B02C805CA748`.
- Exact real before -> condition -> status -> after command sequence: byte-identical; 268 bytes; SHA-256 `B40812555F395967B1960F6DE201BAF9DF9F63D02342E491C7F0997C505EF9B2`.
- Raw phases 11–15 source block: byte-identical; 5,651 bytes; SHA-256 `BB4E4CF5CB2824CBCEDF5AE3998111CE2BE2E6AAB4E3FCB68EE7FFDDEF46729E`.
- The real condition command remains present once, remains redirected exactly as before, and retains status 1.
- F9 trace, F10 managerless structural proof, F11 wrapper, F12 hard-link placement, F13 receipt writers/parsers, and F14/F15 source/causal/byte gates remain intact.

## Three-source proof and durable receipt

Both automatic Linux jobs agree on all accepted evidence before cleanup:

1. Manager receipt before the real command: 25 bytes; SHA-256 `D0ECE910486B0AF43B06FA5F47123DAB870B6149F27710E4202C5AE56F852FE4`; no entries.
2. Manager receipt after the real command: 65 bytes; SHA-256 `BF9DEC7FD997520CEC13F37C2708D47157F07315EF29C22110CACFE7BAF5E310`; exact contents:

   ```text
   HMROOT|1|after|status=1
   ENTRY⇥systemd-units-load⇥regular⇥444⇥0⇥0
   ```

   `⇥` renders an actual tab. The receipt remains unchanged after cleanup.
3. Source receipt identifies Ubuntu `systemd` `255.4-1ubuntu8.17` amd64, executable SHA-256 `233e1dddc9f2a0cf7a2558a8948ce74252cc74a1c8b93d0a7a88953622064adc`, upstream `systemd-stable` `v255.4` revision `387a14a7b67b8b76adaed4175e14bb7e39b2f738`, source hashes `3f89216b21faa202099f290615cdd8ed4ee5f98a2f0094242d447670248a9b89` and `58af3c261e43b6de343be931a46c049152eb57c856f24f81dd53bdd9abafa72e`, and the `verb_condition>verify_conditions>manager_startup>manager_ready>touch_file` chain.
4. Causal receipt proves the no-command control stays empty; the exact condition command returns 1, creates exactly the zero-byte mode-444 root-owned marker on the private filesystem, makes no manager endpoint, and permits exact cleanup.

## Executable oracle coverage

The production oracle is extracted unchanged into a root private-mount-namespace test. The accepted case requires the exact marker and completes with one cleanup. Twenty negative cases require their exact fail-closed status and prove no implicit substitution:

- missing, wrong-name, extra, and deeper-path entries;
- directory, symlink, socket, FIFO, nonzero content, wrong mode, wrong UID, wrong GID, hard-link count, and wrong device;
- manager endpoint and private-bind identity drift;
- exact removal failure, receipt drift, residual entry after cleanup, and a second removal attempt.

The test also proves exact after-receipt-before-cleanup ordering, cleanup-before-empty-root ordering, no wildcard/recursive removal, and a single successful cleanup counter. Expected Linux completion text is `local_systemd_marker_oracle_valid=1 negatives=20 cleanup_count=1`; successful unittest execution captures that output. Structural mutations that move cleanup before the receipt or move the restored-empty assertion before cleanup are rejected.

## Local gates

- Focused manager/source/causal/oracle tests: 3 passed, 2 explicit Windows platform skips, 51 subtests passed.
- Complete `tests/release`: 59 passed, 7 explicit platform skips, 111 subtests passed, 0 failures, 63.55 seconds on the final material diff.
- Ruff format check: **PASS**.
- Ruff check: **PASS**.
- Python compile: **PASS**.
- Generated phase-10 shell: 18,960 bytes; SHA-256 `1B5AA5A708614844E22CE81FD696551866A4B22FE4B077D89BFAD070EE99E5F6`; Git Bash `bash -n`: **PASS**.
- Complete generated-harness contract: **PASS** locally where platform-independent; root private-mount execution explicitly skipped on Windows and executed by both automatic Ubuntu jobs.
- F15 generator-to-parser receipt-byte coverage: **PASS** and unchanged.
- Immutable-boundary comparison and `git diff --check`: **PASS**.
- Staged implementation path was exactly `tests/release/test_offline_appliance.py`.

## Automatic CI evidence

- CI run `33024134887`, push event, exact head `3672863eb620acfbf161536429d2ad4675bbbb9c`: terminal **FAILURE**, 2026-08-26T23:39:14Z through 23:41:50Z.
- `release-bundle-systemd` job `98361362709`: **FAILURE**; phase 10 PASS, then exact unchanged phase-11 unbound-variable failure above; 66 tests, one failure, 57.346 seconds.
- `backend` job `98361362695`: **FAILURE**; identical phase-10 PASS and phase-11 unbound-variable failure.
- Successful companion jobs: `ubuntu-installer` `98361362558`; `frontend` `98361362663`; `installed-appliance-smoke` `98361362760`; `minio-control-plane-backup` `98361362787`; `central-fleet-postgres` `98361362790`.
- Metadata only, no downloads: `controller-redundancy-browser-evidence` ID `9627765199`, 8,014,213 bytes, digest `sha256:b369f787b475de8b7c0f13257871603f1dc21cae5f64f417dc2a04bf41389992`; `minio-control-plane-backup-evidence` ID `9627753724`, 1,255 bytes, digest `sha256:d1dcfbc84c5b8e377bb6eaefb9db1ab763ffbe765628ee3e7855356feb1c5de5`.

The two required Linux jobs therefore agree: the F16 phase-10 marker oracle succeeds, and a later byte-preserved phase-11 path fails before phases 12–15.

## Automatic appliance evidence

- Appliance run `33024134827`, push event, exact head `3672863eb620acfbf161536429d2ad4675bbbb9c`: terminal **SUCCESS**, 2026-08-26T23:39:14Z through 23:47:59Z.
- `build` job `98361362195`: **SUCCESS**; offline repository, release bundle, ISO creation, and interactive boot checkpoint all passed.
- Manual-only `offline-install` job `98362922812`: **SKIPPED**, zero executed steps.
- Metadata only, no downloads: `hoardarr-appliance` ID `9627894848`, 4,408,884,967 bytes, digest `sha256:fbc479b98685b8333827fc48c110703e04bf715e6b4d8fd7da89db9378f0ff11`; `hoardarr-offline-install-inputs` ID `9627897667`, 1,072,358,173 bytes, digest `sha256:aaf532b54cdb177511994d69cecc1d3299a4441ecd0063efae784ffc294b66fb`.

## Prohibited-action counters

- Manual workflow dispatches: 0.
- Workflow retries/reruns/cancellations: 0/0/0.
- Artifact downloads: 0.
- Product/payload/policy/package/workflow/wrapper changes: 0.
- Condition command/status changes: 0.
- Host manager contacts or live host/VM/storage/cluster/website/credential/protected-media actions: 0.
- Wildcard/recursive marker deletions: 0.
- Adjacent or successor work items started: 0.

## Defects, blockers, and next action

The bounded F16 oracle itself is proven in both automatic Linux jobs: phase 10 passes and the exact receipt remains durable. F16 cannot be accepted as complete because both jobs fail immediately afterward in unchanged phase 11 on the same unset associative-array entry for `watchdog.service`; phases 12–15 are not reached.

The only next action is Supervisor QA of this failed handoff. Any phase-11 investigation or correction requires a separately authorized successor. No correction is included or proposed beyond recording the exact failure boundary.
