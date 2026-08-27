# WO-APP-006-C1-F17 Result

## Result

**FAIL for F17 completion; the two authorized path-key corrections are proven, but both automatic Linux jobs agree on a later unchanged phase-12 failure. Supervisor acceptance pending.**

Both jobs preserve the accepted F16 phase-10 PASS and now pass corrected phase 11. They then fail identically in unchanged phase 12:

```text
HPCP|1|PASS|10-host-manager-isolation|status=-|line=-|function=-|label=host-manager-isolation
HPCP|1|BEGIN|11-interrupted-retention|status=-|line=-|function=-|label=interrupted-retention
HPCP|1|PASS|11-interrupted-retention|status=-|line=-|function=-|label=interrupted-retention
HPCP|1|BEGIN|12-final-disable-readback|status=-|line=-|function=-|label=final-disable-readback
HPCP|1|EXIT|12-final-disable-readback|status=1|line=1360|function=main|label=final-disable-readback
offline install denied unit is not inactive: corosync.service=Running in chroot, ignoring command 'is-active'
```

No phase-12 correction, retry, manual workflow, artifact download, or successor work was attempted. F8, C1, and OWNER-10 remain **FAIL**.

## Identity and scoped diff

- Work order: 4,688 bytes; SHA-256 `6D5A82B1BE6439A3F6A98F04D5B63823989CD197CE9F14BFFD4F0A9C6BFDE104`.
- Starting local/origin HEAD: `2776b7055253566df45fca69295b24eeabd35cab`.
- Accepted F16 implementation: `3672863eb620acfbf161536429d2ad4675bbbb9c`.
- Accepted F16 handoff: 9,821 bytes; SHA-256 `C18AFA23F1B2874A3CD0B228FED086D6D2C23F887E07109C38D4C77F738C322B`; commit `2776b7055253566df45fca69295b24eeabd35cab`.
- F17 implementation: `f34a7eb6be989c42193d35c0a040e8c864ae6b1b` (`test(appliance): resolve recovery guard condition paths`).
- Authorized implementation diff: only `tests/release/test_offline_appliance.py`, 186 insertions and 2 deletions.
- Committed test file: 228,211 bytes; SHA-256 `197E29BE98617D3AD4693E54870A93AC17BE3DD073EBAB8A3B24DAC22D706CEB`.

The two corrected lookups are:

1. Phase 11 resolves `watchdog.service` through `recovery_guard_paths_by_unit`, then requires the exact path's owner, tracked inode, regular non-symlink identity, present path-keyed condition entry, and nonempty condition before calling the unchanged condition-command shape with `$watchdog_condition`.
2. Phase 14 uses the already-resolved `peer_guard`, requires it still equals the `zfs.target` unit mapping and retains the expected owner, inode, file identity, present path-keyed condition entry, and nonempty value before using `$peer_condition`.

There is no default that turns a missing condition into success. Direct `recovery_guard_condition_paths[watchdog.service]` and `[zfs.target]` indexing is rejected by the generated-harness contract.

## Preserved boundaries

- Exact accepted F16 phase 10: byte-identical; 18,940 bytes; SHA-256 `62C65876780CB0E8C94C3034661A2CEAE22EE966ED64B15840CB563A431875F0`.
- Unchanged phases 12–13 source block: byte-identical to F16; 2,849 bytes; SHA-256 `A0AEE239B99B25AE6A9AB99A036E13EF853C26CA06FFC82F8A135373D712191E`.
- Unchanged phase-15 source block: byte-identical to F16; 270 bytes; SHA-256 `D29AF4D67B8A0DDAEE7BA5EFB01193EE25009E4656D917DC90D34152768CDCA2`.
- Phase 14 differs only in the authorized path-key resolution/assertions and use of `$peer_condition`.
- Product payload `packaging/appliance/install-offline-payload.sh` is unchanged; its unit/path key domains were already correct.

## Map-domain coverage

The structural contract requires each complete lookup block exactly once, requires each resolved condition use exactly once after its checks, and rejects either direct unit-name condition-map key.

The executable Bash test runs the exact lookup blocks for both `watchdog.service` and `zfs.target`. The valid path-key domain reaches its condition sentinel. Each of these mutations exits nonzero before that sentinel and emits no sentinel output:

- condition stored under the unit-name key;
- missing unit mapping;
- missing condition mapping;
- present but empty condition;
- unexpected path owner;
- mapped path disagreement;
- tracked inode disagreement.

The executable matrix passed directly under Git Bash locally and ran normally in both automatic Ubuntu jobs.

## Local validation

- Focused phase-10/path-domain tests: 2 passed, 2 explicit Windows platform skips, 2 subtests passed.
- Direct Git Bash execution of the Linux-decorated path-domain test: **PASS**.
- Complete `tests/release`: 60 passed, 8 explicit platform skips, 113 subtests passed, 0 failures, 62.26 seconds on the final diff.
- Ruff format/check: **PASS**.
- Python compile: **PASS**.
- Generated path-domain Bash syntax: **PASS**.
- Immutable phase-10/phases-12–13/phase-15 comparisons: **PASS**.
- `git diff --check`: **PASS**.
- Staged implementation path was exactly `tests/release/test_offline_appliance.py`.

## Automatic CI evidence

- CI run `33025668472`, push event, exact head `f34a7eb6be989c42193d35c0a040e8c864ae6b1b`: terminal **FAILURE**, 2026-08-27T00:06:01Z through 00:08:54Z.
- `release-bundle-systemd` job `98366295886`: **FAILURE**; 68 tests, one failure, 63.463 seconds; phase 10 PASS, phase 11 PASS, phase 12 exact failure above.
- `backend` job `98366295912`: **FAILURE**; 68 release tests, one failure, 57.454 seconds; identical phase/status/generated-line/failure message.
- Successful jobs: `installed-appliance-smoke` `98366295593`; `minio-control-plane-backup` `98366295818`; `frontend` `98366295850`; `central-fleet-postgres` `98366295870`; `ubuntu-installer` `98366295927`.
- Artifact metadata only, no downloads: `controller-redundancy-browser-evidence` ID `9628336917`, 8,014,249 bytes, digest `sha256:559c284241835358666d0e02c639d1ed2f9fa1d9b8afa5f26f7867ee0c48245a`; `minio-control-plane-backup-evidence` ID `9628327446`, 1,252 bytes, digest `sha256:a279ed3d388794b2cdfde3164c0cc197e7ccd21f3d0f9675a08a80ef9433cf72`.

## Automatic appliance evidence

- Appliance run `33025668488`, push event, exact head `f34a7eb6be989c42193d35c0a040e8c864ae6b1b`: terminal **SUCCESS**, 2026-08-27T00:06:01Z through 00:13:25Z.
- `build` job `98366256981`: **SUCCESS**.
- Manual-only `offline-install` job `98367693788`: **SKIPPED**, zero executed steps.
- Artifact metadata only, no downloads: `hoardarr-appliance` ID `9628431059`, 4,408,881,539 bytes, digest `sha256:4a8fca7af52dfd84a6a2237e32d8fa0d0755f3b00d935ce2ded6bdd72d80670d`; `hoardarr-offline-install-inputs` ID `9628435396`, 1,072,358,152 bytes, digest `sha256:3ef970f4df5af58945cde13519b0a9abedc7c3c5364b8d586cc906c14e4d730a`.

## Prohibited-action counters

- Manual workflow dispatches: 0.
- Retries/reruns/cancellations: 0/0/0.
- Artifact downloads: 0.
- Product/payload/package/workflow/wrapper or phase-10 changes: 0.
- Live host/VM/storage/cluster/website/credential/protected-media actions: 0.
- Ordinary C1, HA-11, or adjacent work started: 0.

## Defect, blocker, and next action

F17's two path-key corrections are proven: both automatic Linux jobs pass phases 10 and 11. F17 cannot be accepted as complete because both then fail in unchanged phase 12 while querying `is-active` against the deliberately managerless chroot. No product defect is established by this harness result.

The only next action is Supervisor QA. Any phase-12 oracle correction requires a separately authorized successor.
