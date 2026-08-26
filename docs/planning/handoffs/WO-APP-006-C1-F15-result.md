# WO-APP-006-C1-F15 Result

## Result

**PASS for the bounded F15 receipt-encoding objective; Supervisor acceptance pending.** Both automatic Linux jobs executed the production source and causal receipt writers, accepted both receipts through the unchanged strict validators, printed identical validated diagnostics, and then failed only at the unchanged real F13 phase-10 closing assertion. The real command, marker, status, closing oracle, and expected overall CI failure remain unchanged.

F15 does not accept or correct the phase-10 oracle. F8, C1, and OWNER-10 remain **FAIL**. No successor, ordinary C1 run, product correction, or adjacent work was started.

## Identity and authorized diff

- Work order: 7,016 bytes; SHA-256 `5048CD9CD9909C87BA8B9D7DD67EF1E954791CE17ABBD4A0569F5BA73FB7BECB`.
- Starting local/origin HEAD: `604282f03fb4406a0d21cf7e3b10322ea629d6da`.
- F14 implementation: `8dde76dd984be9713b1693f772fa64041e7465f6`.
- F14 handoff: 9,630 bytes; SHA-256 `D0816D894670E1EE9E4FFB97DA938C130F87EEEB7B4ED4F0E40C3ED72A4DD652`; commit `604282f03fb4406a0d21cf7e3b10322ea629d6da`.
- F15 implementation commit: `daad0b85c76c25282e4d0917ad6421a133c6b8ed` (`test(appliance): encode systemd receipts with tabs`).
- Authorized implementation diff: only `tests/release/test_offline_appliance.py`, 140 insertions and 17 deletions.
- Committed test-file identity: 199,387 bytes; SHA-256 `00B9C7F00BAAFAF50F27F5B635DDD7F61E143D740C46B3D42C4D873AB09C84FD`.

The two row writers now use fixed field-safe `printf` format strings containing `\t`; every dynamic field remains a `%s` argument. There is no `%b`, `eval`, `echo -e`, constructed command, or environment serialization. Receipt schemas, row order, bounds, `.partial` to final rename, `sync`, validators, and path/type checks are unchanged.

## Unchanged boundaries

Source comparison is against starting commit `604282f03fb4406a0d21cf7e3b10322ea629d6da`, using LF-normalized exact source bytes outside the two writer groups:

- `PCP_SYSTEMD_SOURCE_RECEIPT` pre-writer: identical; SHA-256 `939AEFBA38E1B29E99A4D37B79868E71442536C305A6ABE64502F82373FAC8AB`.
- `PCP_SYSTEMD_SOURCE_RECEIPT` post-writer: identical; SHA-256 `0ED5632B0078C84B6042D76302DC507CB875B8E6BB3FE0ECD5C436EAABEC6EC5`.
- `PCP_SYSTEMD_CAUSAL_PROOF` pre-writer: identical; SHA-256 `2C0A2DDDC92BEAC1E3ABC77F44F9A0820BE865FFF61389AECBB457CD9736F085`.
- `PCP_SYSTEMD_CAUSAL_PROOF` post-writer: identical; SHA-256 `BEE37E7CF812A78A63EFDEE8AC2B1A2A9520B635DB858B31E5D60CC66F6D236C`.
- Complete `PCP_OFFLINE_NONACTIVATION_PROOF` real F13 block: identical, 2,259 bytes; SHA-256 `497272072C69B68F6EF3BB17D3D30AFA18FA760CCA501DC02820AD3AE7EEDA2C`.
- Exact real sequence count remains one: before receipt -> `systemd-analyze condition "ConditionPathExists=$expected_pmcd_condition"` -> captured status -> after receipt -> `$work/run-systemd` closing emptiness assertion.
- Immutable F14 source gates remain `systemd-stable` tag `v255.4`, peeled revision `387a14a7b67b8b76adaed4175e14bb7e39b2f738`, analyze source SHA-256 `3f89216b21faa202099f290615cdd8ed4ee5f98a2f0094242d447670248a9b89`, and manager source SHA-256 `58af3c261e43b6de343be931a46c049152eb57c856f24f81dd53bdd9abafa72e`.

## Generator-to-parser byte evidence

The new Linux Bash test extracts and executes both exact production writer groups in one fresh disposable root. It requires ASCII and LF framing, the exact row counts, at least one real tab in every data row, and zero literal byte pairs `0x5c 0x74`. Both generated files pass the existing strict validators. Replacing every real tab with the original literal backslash-plus-`t` form makes both validators reject.

Local direct Git Bash execution of the exact test body:

- Source: 782 bytes; 9 LF-terminated rows; 28 byte `0x09` separators; zero `0x5c 0x74` pairs; SHA-256 `ABC432022A494D059985F95DA22C9F31C118C70D45D41A9F6637077BB0B9E18F`.
- Causal: 342 bytes; 4 LF-terminated rows; 24 byte `0x09` separators; zero `0x5c 0x74` pairs; SHA-256 `1F5D64DA8D9D1E0BDDB3F8E157B4B8F9B91A734F012F931C487827C1475D64D2`.
- Positive contents were emitted by Bash from the extracted production groups, not synthesized by Python.
- The Windows discovery run reports this Linux-decorated test as an expected platform skip; its body was also directly executed with Git Bash and passed. Both automatic Ubuntu jobs executed it normally and passed it before reaching the real harness failure.

## Local gates

- Focused source/causal/parser/oracle tests: 3 pass, 1 expected Windows platform skip.
- Direct executable production-writer generator/parser test through Git Bash: **PASS**.
- Complete `tests/release` discovery: 65 tests, 59 pass, 6 documented platform skips, 0 failures, 63.182 seconds.
- Ruff check: **PASS**.
- Ruff format check: **PASS** after formatting the authorized file.
- Python compile: **PASS**.
- Generated phase-10 shell: 13,814 bytes; SHA-256 `D2B44F7C36164C0639CA7B376E61A988D8CD0AA55E55A17A6D2445A76755B958`; Git Bash `bash -n`: **PASS**.
- Complete generated-harness contract is included in the full release suite: **PASS** locally.
- `git diff --check`: **PASS**.
- Staged implementation path was exactly `tests/release/test_offline_appliance.py`.

## Automatic Linux receipt evidence

Both Linux jobs emitted and validated byte-identical receipts. The rendering below uses `⇥` only to make each actual `0x09` separator visible; the identities are for the original tab-delimited bytes.

Source receipt: 782 bytes; 9 LF rows; 28 tabs; zero literal backslash-`t` pairs; SHA-256 `145FEE0964D68877AD6D68BD07FA97F6DE28951EDA42F5E670315F76BC07CCBE`.

```text
HSOURCE|1
PACKAGE⇥systemd⇥255.4-1ubuntu8.17⇥amd64
VERSION⇥systemd 255 (255.4-1ubuntu8.17)⇥24e682030f54829600ce9c96ef9d8be4297eb4cd1b11eca8fa7f83c5c4595fc7
EXECUTABLE⇥/usr/bin/systemd-analyze⇥233e1dddc9f2a0cf7a2558a8948ce74252cc74a1c8b93d0a7a88953622064adc⇥755⇥0⇥0⇥203624⇥1⇥systemd
UPSTREAM⇥https://github.com/systemd/systemd-stable⇥v255.4⇥387a14a7b67b8b76adaed4175e14bb7e39b2f738
SOURCE⇥src/analyze/analyze-condition.c⇥verify_conditions:68-114⇥3f89216b21faa202099f290615cdd8ed4ee5f98a2f0094242d447670248a9b89
SOURCE⇥src/core/manager.c⇥manager_ready:1891-1910⇥58af3c261e43b6de343be931a46c049152eb57c856f24f81dd53bdd9abafa72e
CHAIN⇥verb_condition>verify_conditions>manager_startup>manager_ready>touch_file
MARKER⇥/run/systemd/systemd-units-load⇥regular⇥0444⇥zero-length⇥manager-ready
```

Causal receipt: 342 bytes; 4 LF rows; 24 tabs; zero literal backslash-`t` pairs; SHA-256 `1F5D64DA8D9D1E0BDDB3F8E157B4B8F9B91A734F012F931C487827C1475D64D2`.

```text
HCAUSE|1
CONTROL⇥negative⇥command=none⇥status=-⇥before=0⇥after=0⇥manager_endpoints_before=0⇥manager_endpoints_after=0⇥cleanup=removed
CONTROL⇥positive⇥command=systemd-analyze-condition⇥status=1⇥before=0⇥after=1⇥manager_endpoints_before=0⇥manager_endpoints_after=0⇥cleanup=removed
MARKER⇥systemd-units-load⇥regular⇥444⇥0⇥0⇥0⇥1⇥same-filesystem
```

Both jobs also agree on the bounded real F13 observation: before receipt contains zero entries; after receipt contains exactly `systemd-units-load`, regular, mode `444`, UID/GID `0/0`.

## Automatic CI evidence

- CI run `33022175583`, push event, exact head `daad0b85c76c25282e4d0917ad6421a133c6b8ed`: terminal **FAILURE**, 2026-08-26T23:08:32Z through 23:11:46Z.
- `release-bundle-systemd` job `98354967645`: expected **FAILURE**, 65 tests / one failure, 58.894 seconds. It validated both receipts, then recorded `HPCP|1|EXIT|10-host-manager-isolation|status=1|line=1238|function=main|label=host-manager-isolation`.
- `backend` job `98354967779`: expected **FAILURE**, 65 tests / one failure, 60.493 seconds. It validated the same receipts and recorded the identical phase, status, generated line, function, and label.
- The failing assertion remains the original harness-result equality after the unchanged real `$work/run-systemd` closing emptiness assertion. This expected failure is not an F15 encoding defect.
- Other jobs passed: `installed-appliance-smoke` `98354967743`; `ubuntu-installer` `98354967771`; `central-fleet-postgres` `98354967798`; `frontend` `98354967836`; `minio-control-plane-backup` `98354967913`.
- Artifact metadata only; no downloads: `controller-redundancy-browser-evidence` ID `9627015518`, 8,006,465 bytes, digest `sha256:636021b758e548ea1f1fac9214ce4cfa8d5004aadac5f52a8e687f2059c99112`; `minio-control-plane-backup-evidence` ID `9627027762`, 1,253 bytes, digest `sha256:dee3e2fb2f5b8f24195b36fd91ddfc0fac0db4050b0682b4faa6eac6c2d279f9`.

## Automatic appliance evidence

- Appliance run `33022175562`, push event, exact head `daad0b85c76c25282e4d0917ad6421a133c6b8ed`: terminal **SUCCESS**, 2026-08-26T23:08:32Z through 23:17:26Z.
- `build` job `98354967424`: **SUCCESS**, 2026-08-26T23:08:35Z through 23:17:26Z.
- Manual-only `offline-install` job `98356937000`: **SKIPPED**, zero executed steps.
- Artifact metadata only; no downloads: `hoardarr-appliance` ID `9627163718`, 4,408,879,712 bytes, digest `sha256:0056d4eeb6405b8a673d31b5b1d804e95aa1b3877bbacd18e4d1cba9e53dbb4e`; `hoardarr-offline-install-inputs` ID `9627167430`, 1,072,358,208 bytes, digest `sha256:be3043160ab4f9aed31185ded86a1895506bcf7a0b1b23edcc0b9906f5e77a42`.

## Prohibited-action counters

- Manual workflow dispatches: 0.
- Workflow retries/reruns/cancellations: 0/0/0.
- Artifact downloads: 0.
- Oracle/condition/status/real-marker changes: 0.
- Product/payload/policy/package/workflow/wrapper/dependency changes: 0.
- Manager contacts or live host/VM/storage/cluster/website/credential/protected-media actions: 0.
- Adjacent or successor work items started: 0.

## Defects, blockers, and next action

F15 has no remaining receipt-encoding defect. The source and causal diagnostics are now durable, strict-parser accepted, identical across both Linux jobs, and independently byte-addressed above.

The unchanged real phase-10 emptiness oracle remains rejected and outside F15 authority. The only next action is Supervisor QA of this handoff and evidence. Any oracle interpretation or correction requires a separately authorized successor. C1 and OWNER-10 remain **FAIL**.
