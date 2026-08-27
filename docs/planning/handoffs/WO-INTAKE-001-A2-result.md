# WO-INTAKE-001-A2 result — current identity-bound PASS admission gate

Status: **COMPLETE — implementation pushed; synthetic SQLite only; awaiting Supervisor QA**

## Authority, baseline and ownership

- Authority: ACC-080 / DEC-2026-08-26-119.
- Work order verified before action: 8,744 bytes; SHA-256 `E86F7DE09425F563AB77C7813A26DB04F050C56BB423AED0494F6633ACF5483C`.
- Accepted A1 input: implementation `64a399eaf4ab498f34e52708126b42220edeec44`; handoff `86bcb4c1f279cca123aeec9994968885d6ae0b3a`; handoff 9,803 bytes / SHA-256 `101FF26EFA3915D53F3A979682AD0B66DC226705711D18549AFCE662B87C13EB`.
- Initial shared branch `HEAD` and branch origin were `6bede04469f84434a233a5b79888ea786bf667af`. Concurrent H8E documentation advanced them to implementation parent `626ec1da07f0e481fcf8a0c6f224ce6cdfba022f`; it changed no A2-owned path.
- Implementation commit pushed to `origin/rc/0.3.11-validation`: `e3ae8082faf5f6e16676a033621618373f708ca8` (`feat(storage): enforce current drive intake admission`).
- Implementation commit contains exactly seven authorized paths: 874 insertions, 12 deletions:
  - `backend/src/hoardarr/storage/intake.py`
  - `backend/src/hoardarr/wizard/service.py`
  - `backend/src/hoardarr/api/routes/wizard.py`
  - `backend/src/hoardarr/operations/worker.py`
  - `backend/tests/test_storage_intake.py`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_api.py`
- This document is the sole authorized second-commit path. Its final commit, byte count and SHA-256 are reported after commit because a file cannot truthfully contain its own final commit/digest.

Inherited tracked changes in automation/telemetry routes, storage drain/foreign, telemetry alerts, architecture/roadmap/gap/specification documents, frontend storage components and the gap-matrix script were preserved and excluded. Existing untracked temporary deployment roots, design/planning/control documents, other teams' handoffs, recovery scripts, test results and website content were also preserved and excluded. App, Website and Supervisor ownership boundaries were not crossed.

## Central admission contract

`hoardarr.storage.intake.evaluate_storage_admission` is the single read-only semantic authority. It returns `admitted`, an explicit test-topology `qualification_exempt`, bounded redacted blockers, and one derived `allowed` decision. It inserts, updates and deletes no row.

| Proposed state | Central result |
|---|---|
| No storage document | blocked with existing `storage_selection_required` behavior |
| `storage.topology == "test"` | allowed only by explicit qualification exemption; `admitted == false`; no PASS implied |
| Non-test plan with one exact current destination PASS per selected device | admitted |
| Missing durable disk or zero/multiple identity matches | blocked |
| No disposition history | blocked as not tested |
| Newest `SOURCE_ONLY` | blocked; never destination admission |
| Newest `FAIL`, `QUARANTINED`, `INCOMPLETE` or `UNSUPPORTED` | blocked; older PASS is not searched |
| Newest PASS with policy, identity, plan, snapshot, device, result, fingerprint or current-hardware mismatch | blocked |
| Malformed/tampered plan or A1 history | blocked with stable safe code/message |

Per disk, history selection is deterministic by `evaluated_at DESC, id DESC`. The evaluator never searches backward for a convenient PASS. The latest hardware observation is deterministic by `captured_at DESC, id DESC`; missing/duplicate identity, invalid payload/hash or fingerprint drift blocks.

## Exact policy, fingerprint and immutable-history proof

For each selected non-test device, the evaluator reuses A1's canonical binding and fingerprint functions to prove:

1. the plan-bound snapshot exists and its stored payload hash equals both the snapshot and binding hashes;
2. selected identities are unique, preserve exact order, equal the selected-device binding, and hash to `device_binding_sha256`;
3. the plan-selected device document exactly regenerates from the bound observation;
4. the stable identity resolves to exactly one durable `PhysicalDisk`;
5. intended use is exactly `destination`;
6. current policy name/version, ordered required action set and policy hash exactly match the newest record;
7. current plan-bound canonical fingerprint equals the persisted fingerprint;
8. persisted fingerprint JSON hashes to its stored fingerprint hash;
9. the historical row's test-only Plan, Operation, exact bound HardwareSnapshot, operation request/result hashes, device binding and evaluating operation all remain present and internally exact;
10. rerunning A1's deterministic evaluator over that immutable historical evidence reproduces disposition, reasons, required tests, bounded test results, fingerprint and policy hash exactly;
11. the newest hardware observation contains one matching identity and regenerates the same fingerprint.

Firmware, capacity, logical/physical geometry, controller, hub, port and filesystem/signature changes block. Kernel path/discovery reorder remains excluded from the fingerprint and does not block. A newer exact observation remains admissible.

## Three no-bypass boundaries

1. **Plan review:** `_build_plan_document` invokes the central evaluator after `build_storage_plan` and schema/capability blockers, appends non-duplicate admission blockers, then derives final `apply_available`. Test topology remains reviewable/applyable without history.
2. **Authenticated queue:** `POST /api/v1/wizards/{wizard_id}/apply` verifies immutable plan integrity, recomputes central admission from the stored plan and current database, and returns HTTP 409 `storage_drive_admission_blocked` with central safe errors before creating or replaying `storage.apply`. Stored `apply_available=true` and empty blockers cannot bypass it.
3. **Worker pre-executor:** `_execute_storage` completes immutable plan and approval validation, recomputes central admission inside the database transaction, and raises stable `WorkFailure` code `storage_drive_admission_blocked` with `needs_attention=true` before copying the plan or calling `storage_applier`. Resume uses the same path.

Synthetic direct-API rejection left the operation count unchanged. Synthetic worker rejection, including a stored resume request plus manipulated apply flags/empty blockers, produced exactly zero fake-applier calls. The broader authenticated API/worker flow first observed the plan-review blocker, persisted one synthetic exact A1 PASS, added a newer exact synthetic snapshot, refreshed the immutable plan, then passed queue and worker revalidation.

## Synthetic QA

All tests used synthetic plans/snapshots/results, fake appliers and disposable SQLite databases.

- Combined affected command covered `test_storage_intake.py`, `test_wizard_service.py`, `test_worker.py`, full `test_api.py`, `test_migrations.py`, `test_migration_identity.py` and `test_cli_migration_admin.py`: **163 passed**, one non-failing pre-existing Starlette TestClient deprecation warning.
- Focused negatives cover no history; every non-PASS value; `SOURCE_ONLY`; later non-PASS supersession; wrong policy; selected-device/device-hash/snapshot-hash/durable-disk/source-result tamper; historical raw-record mutation; firmware/capacity/geometry/controller/hub/port/signature drift; current identity absence/duplication; exact newer observation; kernel-path reorder; direct authenticated API; stored flags; resume; and zero applier calls.
- Ruff check on all seven changed Python paths: PASS.
- Python compile on all seven changed Python paths: PASS.
- `git diff --check` on all implementation paths: PASS.
- Added-line secret-value scan: 0 matches.
- Ruff format check passes for the three files without inherited formatting debt. It reports only pre-existing unrelated formatting in `operations/worker.py`, `wizard/service.py`, `test_api.py` and `test_worker.py`; each exact implementation-parent version independently returns the same formatter failure. A2-added regions were formatted, and unrelated lines were preserved.

## Prohibited-action counters

| Action class | Count |
|---|---:|
| Attached USB/raw disk or hardware-identity access | 0 |
| Windows/Hyper-V/host/VM/guest access | 0 |
| Credential or live-service access | 0 |
| Storage executor or host command invocation | 0 |
| Real SMART/mount/import/format/pool/ZFS/RAID/iSCSI action | 0 |
| Manual PASS, warning dismissal or override | 0 |
| UI, cluster, HA, deployment or adjacent-roadmap work | 0 |
| Model, migration, executor/client/command or frontend edit | 0 |

## Supervisor gate

Reproduce the seven-file implementation diff, central truth table, deterministic newest-row rule, immutable A1 replay proof, exact current-hardware fingerprint checks, all three call sites, HTTP 409/no-operation negative, resume/zero-applier negative, 163-test command, Ruff/compile/diff checks and added-line secret scan. A2 stops here. No attached USB SSD, host, VM, guest, credential, live service, storage device or storage command was accessed; admission UI, import, format, pool/ZFS/RAID/iSCSI, cluster and deployment remain separately unauthorized.
