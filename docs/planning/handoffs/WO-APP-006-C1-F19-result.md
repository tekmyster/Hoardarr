# WO-APP-006-C1-F19 result

## Result

**PASS for bounded diagnosis; primary cause remains `INCONCLUSIVE`. F18, C1, and OWNER-10 remain FAIL.**

F19 retained deterministic before/after unit, symlink, preset, mount, and command evidence around the one unchanged production phase-12 `disable_unmasked_units` call. Both automatic Linux jobs reproduced the same decisive behavior: the preceding `corosync.service` control disabled successfully, but `systemctl --root=/ disable iscsid.service` returned 1 and the immediately following `is-enabled` readback remained `enabled`/0. The original fail-closed assertion then returned status 1; the F18 deferred-activity validator and phases 13–15 were not reached.

The receipt cannot safely choose among a product finalizer defect, coupled SysV/unit behavior, or a synthetic-fixture collision. It does not retain disable stderr, `/etc/init.d/iscsid`, `/usr/lib/systemd/systemd-sysv-install`, package ownership for those objects, or the remainder of the target root. No cause or correction is guessed.

## Identity and scope

- Authority: ACC-082 / DEC-2026-08-26-122.
- Work order: 8,528 bytes; SHA-256 `342E504946210A6DD629028B383F5D935D8BD8BC8D8C7FB4BDB758479742F8AD`.
- Starting local/origin head observed before work: `c9bcde19a64316658389b3ed651636183bf12bb0`.
- Concurrent shared-worktree changes were preserved. Implementation parent: `ea365d27af039beb45c210a76d306590400bfae8`.
- Test-only implementation commit: `d5f7ca36a61ee5b75a0f0b63a14be5056d7ad7f8` (`test(appliance): diagnose offline iscsid enablement`).
- Authorized implementation path only: `tests/release/test_offline_appliance.py`, 270,637 bytes; SHA-256 `7025E4BB71E7061F51DBC1C8D00985E58DC69369EAACB4CAC308FCB788FEB63A`.
- Implementation diff: 842 insertions, 4 deletions; no product, verifier, workflow, package, policy, or other test path changed.

## Diagnostic contract

- Added schema-validated, bounded F19 before/after snapshots and a strictly parsed bounded xtrace.
- Preserved the original phase-12 call as one plain command. It was not placed in an `if`/`||` condition and was not repeated.
- Captured exact input hashes, systemctl identity/version, four bind-root identities, vendor unit metadata, bounded unit content in test output only, matching private `/etc/systemd/system` objects, presets/effective rules, phase-09 outcomes, before/after enablement, symlink delta, exact command/status sequence, and the first failure boundary.
- Rejects malformed schema, unsafe paths, traversal, duplicate/overflow entries, unconfined links, unsupported types, unexpected trace prefixes, oversized traces, inconsistent input hashes, or a second disable/preset cycle.
- The sanitized log excludes complete unit and preset contents; no manager activity query was added.
- F18 phase 10, phase 11, phase 14, and 15-phase grammar constants were checked byte-identical to the F18 test input.

## Immutable F18 evidence

- `packaging/appliance/install-offline-payload.sh`: unchanged at 59,771 bytes; SHA-256 `62077EF0E6F885CC13D11A882F674B906988ACDF60352B338F631494820C42CF`.
- `packaging/appliance/verify-offline-appliance.sh`: unchanged at 4,227 bytes; SHA-256 `F188D76E7C19BA38472A5125C68D53E428BCF095D36878AC688E56A93FC627AD`.
- Extracted `disable_unmasked_units` SHA-256 in both Linux receipts: `C068C3E081EBCBC9DB7D849B39A1803445ECCE2F2A1EEEA3EE11D08466F7B854`.
- No F18 product or first-boot verifier byte was changed.

## Local QA

- Focused F19/source-integrity tests: `2 passed, 1 skipped, 39 deselected, 8 subtests passed`.
- Complete `tests/release`: `62 passed, 8 skipped, 121 subtests passed` in 61.48 seconds.
- Ruff format check: PASS.
- Ruff check: PASS.
- Python compile: PASS.
- `git diff --check`: PASS.
- Exact one-file implementation scope review: PASS.
- Linux-only generated harness execution was skipped locally on Windows as expected; both automatic Ubuntu jobs parsed and executed it.

## Automatic evidence

### CI — expected diagnostic FAIL

- Run `33030497420`, attempt 1, push event, exact head `d5f7ca36a61ee5b75a0f0b63a14be5056d7ad7f8`.
- Created `2026-08-27T01:33:56Z`; terminal `2026-08-27T01:37:19Z`; conclusion `failure`.
- Backend job `98381653444`: FAIL only in the release suite; `70 tests`, one failure, 61.569 seconds.
- Release-bundle-systemd job `98381653539`: FAIL only in the same release test; `70 tests`, one failure, 56.381 seconds.
- Other five CI jobs passed: installed-appliance-smoke, minio-control-plane-backup, central-fleet-postgres, frontend, and ubuntu-installer.
- Both Linux jobs reported the same first failure:
  - `offline install denied unit remains enabled: iscsid.service=enabled`
  - `HPCP|1|EXIT|12-final-disable-readback|status=1|line=1377|function=main|label=final-disable-readback`
  - phases 01–11 passed; phase 12 began; phases 13–15 and the deferred-activity validator were not reached.
- CI artifact metadata only; no artifact was downloaded:
  - `controller-redundancy-browser-evidence`: ID `9630093130`, 8,006,494 bytes, digest `sha256:fdc34a346bdd257c02f91039698e6910efd325e03d8ef7cb136f0cb5a7554219`.
  - `minio-control-plane-backup-evidence`: ID `9630070788`, 1,253 bytes, digest `sha256:69941d101224ce54ab4ef1b91e3eebff5b0c7eddc43c72d4e22c5c24d4aa1b6e`.

### Build appliance ISO — PASS

- Run `33030497492`, push event, exact head `d5f7ca36a61ee5b75a0f0b63a14be5056d7ad7f8`.
- Created `2026-08-27T01:33:56Z`; terminal `2026-08-27T01:42:28Z`; conclusion `success`.
- Build job `98381653669`: PASS, including signed repository, release bundle, ISO build, and visible installer checkpoint.
- Manual-only offline-install job `98383090331`: SKIPPED with zero steps.
- Artifact metadata only; neither artifact was downloaded:
  - `hoardarr-appliance`: ID `9630189757`, 4,408,897,519 bytes, digest `sha256:5896b6a699681707c021e724b6cd698de35a80adbac6db232eaec6888465cb73`.
  - `hoardarr-offline-install-inputs`: ID `9630191922`, 1,072,371,316 bytes, digest `sha256:4dd2f3623f8b07bff36c4055cd419a16700c589891cc89003bf884fb4bde3055`.

## Sanitized two-job receipt

The complete bounded JSON and xtrace appeared in each Linux job log. Receipt hashes differ as expected because each disposable namespace has distinct path and mount identities, while all semantic fields agree.

| Evidence | Backend `98381653444` | Release `98381653539` |
| --- | --- | --- |
| Before snapshot SHA-256 | `2D227D63318EA9C178E27041B2CAA26983FE1A5838829CD5AD501421A8AF36EE` | `C1E137B1024E8857DE3828C5CFE28BA2EF6EB4700FE62353059B732B7BBC6B66` |
| After snapshot SHA-256 | `6640509C2BF58EA37A61C76AC900F5896C53671D0C34F7F9B056061207D6C53E` | `DE53213FB05A06B6B8557901F9DC693D5DE644B5B8615472AE08598BB17F78F1` |
| Command trace | 10,913 bytes; `9853E9485E10FF7356EE419D7124FA826ED07149EFA92427E8FD4E21517CB736` | 10,913 bytes; `4372693353D0CDF202363BF1EA68CA2C5997E6FE58F3E4C38149D72977851227` |

Agreed observations:

- Effective systemctl: `/usr/bin/systemctl`, SHA-256 `E0D3D0E9444DA1B2B58C792C3F5028B69F049B77D5CA17B3EC0D09F89117225B`, `systemd 255 (255.4-1ubuntu8.17)`.
- `/usr/lib/systemd/system`, `/etc/systemd/system`, `/var/lib/systemd`, and `/run/systemd` each resolved to its exact disposable bind source; every `bind_identity_matches` value was true.
- Phase-09 preset returned status 0 in order for `corosync.service`, `iscsid.service`, `iscsid.socket`, `iscsi.service`, and `open-iscsi.service`.
- The visible preset file was `/usr/lib/systemd/system-preset/90-systemd.preset`, 1,458 bytes, SHA-256 `A69E1D1E1D05FA0A8AEBB431B9AFEB980C7545877F7603972785EF9B8484FE2F`; all five effective rules were bounded as default `enable`.
- `corosync.service`, `iscsid.service`, `iscsi.service`, and `open-iscsi.service` were synthetic regular root-owned 0644 files, each 138 bytes with identical SHA-256 `D54E3C3E43095991C2D4C2E3DB1A83D7DAEA775987FC08C003757BBC8A960CF3`.
- `iscsid.socket` was a regular root-owned 0644 file, 150 bytes, SHA-256 `7C1052DE4A8AA4B12EF699699666C5D7C392BA653A7F5C934DD047C0C86A7731`.
- Before the production call, all five units were `enabled` with status 0.
- After the one production call, `corosync.service` was `disabled`/1, while all four iSCSI-family units remained `enabled`/0.
- The only observed symlink removal was `multi-user.target.wants/corosync.service`; no entry was added. The four iSCSI-family wants symlinks remained.
- The decisive xtrace sequence was:
  1. `systemctl --root=/ disable iscsid.service`
  2. `disable_status=1`
  3. `systemctl --root=/ is-enabled iscsid.service`
  4. `enabled_state=enabled` and status 0
  5. `return 1`
- Failure snapshot: status 1, generated line 1377, function `main`, command `return 1`.

## Classification

**Primary cause: `INCONCLUSIVE`.**

The evidence proves an exact, name-specific boundary: an otherwise byte-identical synthetic `corosync.service` control disables and loses its wants symlink, whereas `iscsid.service` disable returns 1 and its private wants symlink remains. It also proves the four systemd roots observed by F19 are the intended fixture bind roots. It does not prove why systemctl returned 1.

Alternatives that cannot safely be promoted to the primary cause:

- **Product finalizer failure:** not established. The exact production function correctly rejects the still-enabled unit, while the underlying systemctl mutation fails only at `iscsid.service`; the receipt lacks the cause of that command failure.
- **Coupled unit/alias/`Also=` behavior:** not established or excluded. The captured vendor objects are regular synthetic units and four service files are byte-identical, but F19 does not capture every target-root relationship or disable stderr.
- **Synthetic harness relationship / host-visible SysV collision:** plausible, not proven. The fixture binds the four systemd roots, but F19 does not capture `/etc/init.d/iscsid`, `systemd-sysv-install`, their package identities, or the rest of `/`. Those uncaptured objects could influence this special unit name.
- **Unintended observed systemd root/path:** rejected for the four captured roots; exact bind identities matched. This does not establish isolation of uncaptured SysV compatibility paths.

Smallest missing observation: in a separately authorized test-only successor, retain bounded metadata and package ownership for exact `/etc/init.d/iscsid` and `/usr/lib/systemd/systemd-sysv-install`, plus bounded stderr/exit classification from the same single production disable call, while preserving the one-cycle rule and all F18 safety assertions. That is sufficient to decide whether the status 1 comes from host-visible SysV compatibility handling or from the private unit relationship. Do not alter the product until that distinction is proven.

## Prohibited-action counters

- Product/verifier/workflow/package/policy edits: 0.
- Second preset/disable cycles: 0.
- Manager activity queries: 0.
- Manual workflow dispatches: 0.
- Retries/reruns/cancellations: 0.
- Artifact downloads: 0.
- Ordinary C1 runs: 0.
- Live host/VM/service/storage/credential/website actions: 0.
- HA-11 or adjacent work: 0.

## Defects / blockers

- Phase 12 remains FAIL at `iscsid.service=enabled` after the exact disable command returns 1.
- The F18 deferred-activity receipt validator and phases 13–15 remain unverified in the real Noble harness.
- F19 cannot attribute the disable failure without the bounded SysV compatibility/stderr observation above.
- C1 and OWNER-10 remain FAIL.

## Next action

Authorize only the bounded test-harness successor described in Classification. It should observe, not correct: capture exact SysV compatibility identities/package ownership and bounded stderr around the same one production call, classify the status-1 source, then stop. Do not change product code, weaken enablement policy, repeat the transaction, or run ordinary C1 until that evidence supports a minimal correction.
