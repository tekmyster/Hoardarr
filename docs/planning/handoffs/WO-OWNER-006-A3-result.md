# WO-OWNER-006-A3 result — idempotent managed-LIO reconcile

## Disposition

Implemented and pushed the bounded A3 pre-mutation reconciliation contract. This is synthetic/local evidence only and is stopped for Supervisor QA. No live target, disk, zvol, host, VM, credential, network, restart-persistence, multipath, or HA action occurred.

## Authority and locked identities

- Authority: ACC-091 / DEC-2026-08-26-131.
- Work order: `WO-OWNER-006-A3-idempotent-managed-lio-reconcile.md`, 9,528 bytes, SHA-256 `1FBF2A91244C6E28AAA42AE34E848E156FC1772D17B56D21171D16437126B272`.
- A1 implementation/handoff: `4e3635452ce7155f320e0626f553c761da482b7f` / `aab2d1f3da222cd19e3f3dff29874003a9902413`.
- A2 implementation/parser correction/handoff update: `71f2a702716b2b2e59ee3dcd26ee8772766e38e8` / `8605d3f7772a6bb8da1fdbc91854d340b346e3e6` / `3d3ba51116c986122ca3582e78b4a5901ad69134`.
- Accepted A2 handoff: 11,228 bytes, SHA-256 `A5244D2DC0B68D45DC2E5CF0281885BF144A91253913D787B913DF4991C334BB`.
- Dispatch local/origin HEAD: `3d3ba51116c986122ca3582e78b4a5901ad69134`. Concurrent accepted unrelated commits `fb54361` and `a6ff03a` advanced the shared branch before this commit; neither overlapped the authorized A3 paths.
- A3 implementation: `7b6d3809afe78cf77e3937e1b1fa27d04619f62a`; parent `a6ff03aa696cae67bc489e1a607fc580f1fc5023`; tree `01eaedcce5cdac7fbb6c8e9fe939c08933d1804f`.
- Implementation scope: exactly `connectivity/executor.py`, `connectivity/lio_readback.py`, and `test_lio_readback.py`; 439 insertions and 90 deletions. It was pushed normally to `origin/rc/0.3.11-validation`.

All inherited tracked/untracked App, Website, Supervisor, roadmap, frontend, telemetry, release-test, temporary, evidence, and work-order paths were preserved and excluded from the implementation commit.

## Implemented contract

The pure classifier consumes one already-parsed, capped A2 saveconfig document and the exact in-memory service/config/secret expectation. It returns `exact_active` with the existing sanitized active evidence, returns `exact_absent` with the existing sanitized absence evidence, or raises the stable bounded `connectivity_lio_preflight_conflict` failure. The executor converts reader/classifier failures to `ExecutorFailure(needs_attention=true)`.

The preflight is invoked once, after config/hash validation and one executor-state load, only for a managed-zvol request with no row or an exactly identical row. A different existing row bypasses A3 and retains the pre-existing validated remove/apply/postcondition path. No preflight branch performs cleanup or repair.

| Stored row / live graph | State reads | Preflight reads | Targetcli calls | Postcondition reads | State writes | Cleanup calls | Result |
|---|---:|---:|---:|---:|---:|---:|---|
| No row / exact absent | 1 | 1 | 1 | 1 | 1 | 0 | Existing mutation path; active |
| No row / exact active | 1 | 1 | 0 | 0 | 1 | 0 | Active; `reconciled_existing=true` |
| No row / partial, conflicting, or unreadable | 1 | 1 | 0 | 0 | 0 | 0 | Needs attention |
| Identical row / exact active | 1 | 1 | 0 | 0 | 0 | 0 | Active; `already_active=true` |
| Identical row / exact absent | 1 | 1 | 0 | 0 | 0 | 0 | Needs attention; no reconstruction |
| Identical row / partial, conflicting, or unreadable | 1 | 1 | 0 | 0 | 0 | 0 | Needs attention |
| Different row / successful managed replacement | 1 | 0 | 2 | 1 | 1 | 0 | Existing update path |
| New apply / failed post-mutation proof | 1 | 1 | 2 total | 2 total | 0 | 1 | Existing one cleanup plus absence proof |

The last row's two targetcli calls are the one apply mutation and the one accepted same-service cleanup. A failed preflight always has targetcli/state-write/cleanup counters of zero.

## Exactness and negative evidence

The executable matrix proves target-only, backstore-only, duplicate identity, wrong device/plugin/fabric/TPG/LUN/portal/ACL/CHAP/safety facts, boolean-as-integer, wildcard-versus-explicit portal mismatch, duplicate JSON keys, collection caps, missing/unreadable/malformed/symlink/directory/oversized/deep-recursive/oversized-integer documents all fail closed. Both no-row and identical-row preflight states are covered. Unrelated valid objects are ignored by classification and are not included in results.

State-only recovery requires the complete selected block backstore, target, TPG, LUN, portal set, ACL set, CHAP equality booleans, and safety attributes to match the immutable incoming binding. Target IQN alone is never sufficient.

Returned evidence remains A2 schema version 1 and includes only state, service/target/backstore identifiers, plugin, redacted managed-volume suffix, exact TPG/LUN/portal/initiator facts, CHAP configured/equality booleans, safety attributes, and deterministic evidence digest. It excludes CHAP material, raw device/zvol path, provider resource, stable identity, complete saveconfig, and unrelated objects. Error text is bounded and excludes those values as well.

## Preserved A1/A2 boundaries

- The direct exact command-script regression passes unchanged, including block backstore creation, target/TPG/LUN/portal/ACL commands, and the accepted saveconfig behavior.
- Normal new apply remains preflight, one mutation, one postcondition read, then one state write.
- Failed post-mutation verification retains exactly one same-service cleanup followed by one absence proof; no removal retry was added.
- Removal verification and state ordering are unchanged.
- Managed backing-file allocation/unlink remain prohibited; no zvol or backing-data mutation was added.
- Destructive managed-backing rejection and legacy fileio, SMB, NFS, and FCoE paths are unchanged.

## QA evidence

- Focused A3/A2 reader/executor matrix: `132 passed`.
- Applicable A1/A2 connectivity, managed-zvol, API, worker, storage-volume, migration, and identity regressions: `283 passed, 1 skipped`; the skip is the expected Windows skip for descriptor-relative Linux file operations.
- Ruff format check: clean after formatting; Ruff check: all checks passed.
- Python compileall for the connectivity package and focused test: passed.
- `git diff --check` for authorized paths: clean.
- Authorized implementation diff: exactly three listed files.
- Added-line secret scan for private-key headers, access-key forms, and assigned API key/token/secret/password material: clean.
- Fake-only prohibited-action counters: real targetcli 0; `/dev`/disk 0; pool/zvol 0; backing allocation/delete 0; systemd 0; network 0; credential access 0; host/VM access 0; deployment 0.

## Deferred gates and proposed successor

A3 does not prove boot restoration, service ordering after restart, real rtslib persistence, a real initiator transaction, multipath, shared-controller behavior, fencing, quorum, or HA. Those remain separately authorized evidence gates.

Proposed narrow successor only: a fake/local restart-persistence state-machine contract that proves an exact managed target is revalidated after service restart before Hoardarr reports it active, without accessing a real target or broadening into initiator, multipath, or HA work.

Stopped for Supervisor QA. No successor was begun.
