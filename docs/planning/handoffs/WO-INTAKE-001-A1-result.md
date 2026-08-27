# WO-INTAKE-001-A1 result — durable identity-bound drive disposition

Status: **COMPLETE — implementation pushed; synthetic SQLite only; awaiting Supervisor QA**

## Repository and ownership evidence

- A0 predecessor independently accepted as ACC-076 / DEC-2026-08-26-115: implementation `937088b34ae83cfa48c962f721be994b25681100`; handoff `7edbe931cc381141088c86b9660730a6973d54f2`.
- A1 work order verified before action: 10,746 bytes; SHA-256 `B5E96D60E7675429DE74F9719921A5ABD9AC48F2D574A48210C08B424A56F59A`.
- Initial observed shared `HEAD`/origin: `9b60038b593d9ff64e94794c29185e7336a8f931`. Unrelated concurrent commits advanced both to implementation parent `3952a99749eb8cfdc945ef7bb7e7ca92d2a920a5`; none changed an A1-owned path.
- Implementation commit pushed to `origin/rc/0.3.11-validation`: `64a399eaf4ab498f34e52708126b42220edeec44` (`feat(storage): persist drive intake dispositions`).
- Implementation commit contains exactly six authorized paths: 1,724 insertions, 8 deletions.
  - `backend/src/hoardarr/db/models.py`
  - `backend/src/hoardarr/db/migrations/versions/0030_drive_intake_dispositions.py`
  - `backend/src/hoardarr/storage/intake.py`
  - `backend/src/hoardarr/operations/worker.py`
  - `backend/src/hoardarr/api/routes/storage.py`
  - `backend/tests/test_storage_intake.py`
- This document is the only authorized second-commit path. Its final byte count/SHA-256 and commit are reported after finalization because a file cannot contain its own final digest truthfully.

Inherited tracked changes in API automation/telemetry, storage drain/foreign, telemetry alerts, architecture/roadmap/gap documents, fleet/community specifications, frontend storage components, the gap-matrix script and `tests/release/test_offline_appliance.py` were preserved and excluded. Existing untracked `.codex-temp/`, `.tmp-deploy-*`, design/planning/control documents, other teams' handoffs, recovery scripts, `test-results/`, and `website/` were also untouched. App/Website ownership boundaries were preserved.

## Schema and immutability contract

Migration `0030_drive_intake_dispositions` follows `0029_user_active_state` and creates one append-only `drive_intake_dispositions` history table. Each row binds:

- durable `PhysicalDisk` ID plus the exact stable identity observed by the plan;
- completed/evaluating `Operation`, immutable `Plan`, plan SHA-256 and wizard revision;
- exact plan-bound `HardwareSnapshot` ID/SHA-256 and selected-device binding SHA-256;
- canonical device fingerprint document/SHA-256 and completed executor-result SHA-256;
- policy name/version/SHA-256, intended-use scope, required action types and explicit A0 result/evidence references;
- normalized disposition, deterministic reason codes and completion/evaluation timestamps.

The database uniqueness constraint is `(operation_id, physical_disk_id, policy_sha256)`. Replay of the same immutable operation returns the existing row only when every persisted immutable field still matches; a distinct later operation appends a new row. The database check constraint permits exactly `PASS`, `FAIL`, `QUARANTINED`, `INCOMPLETE`, `UNSUPPORTED`, and `SOURCE_ONLY`. No overwrite-only field was added to `PhysicalDisk`; no service path updates historical rows.

The fingerprint includes stable identity and identity evidence/conflict facts, vendor/model/firmware revision, capacity, sector geometry, complete optional physical connection document (controller/HBA/enclosure/expander/hub/port/path facts when present), and plan-bound partition/filesystem/signature evidence. Optional connection facts remain explicitly absent when the hypervisor did not expose them. Ephemeral kernel path, device node, discovery index and Windows disk number are excluded from the fingerprint, so a hotplug reorder cannot manufacture identity drift.

## Exact binding and evaluation proof

Qualification never queries the newest snapshot. It resolves only `plan.document_json.storage.snapshot_binding.snapshot_id`, then independently proves:

1. operation request hash, plan ID/SHA and wizard revision match;
2. `document_hash(plan.document_json) == plan.sha256`;
3. the referenced snapshot exists, its persisted SHA matches the binding and its payload hashes to that SHA;
4. selected IDs are unique and exactly match the binding;
5. the selected-device list hashes to `device_binding_sha256`;
6. each selected observation exactly equals the normalized observation regenerated from that bound snapshot;
7. the stable identity resolves to one durable `PhysicalDisk` row;
8. result action IDs/device IDs/types and explicit A0 envelopes match the exact plan actions.

A missing/wrong bound snapshot fails even if a newer valid snapshot exists. A newer unbound snapshot can only make a historical assessment explicitly stale during readback; it cannot supply evidence for or create a PASS.

| Evidence state | Result |
|---|---|
| Stable non-conflicting destination, complete no-signature scan, every required explicit action passed with exact evidence | scoped `PASS` / `policy_requirements_satisfied` |
| Existing-data read-only source scope, non-destructive policy complete | `SOURCE_ONLY` / `source_policy_requirements_satisfied` |
| Required skipped or unsupported action | `UNSUPPORTED`; never PASS |
| Missing result/checkpoint, ambiguous outcome/evidence, incomplete signature state or undetermined intent | `INCOMPLETE`; never PASS |
| Explicit failed media/test result | `FAIL` |
| Identity instability/conflict, destination signature, destructive source policy, failure notice or blocker | `QUARANTINED` |
| Duplicate/unplanned result, duplicate identity, plan/snapshot/device/fingerprint/result topology tamper | no row; explicit evaluation/reconciliation error |
| Non-test layout/import/format result | no disposition row |

PASS remains scoped to the recorded plan-bound policy and intended use. It is not a pool-admission decision and does not claim production safety. DEC-113's later 18-device/three-host sequence remains context only; A1 implements no host topology, replication, quorum, fencing, failover, iSCSI, ZFS or pool behavior.

## Worker and reconciliation seams

At successful `StorageExecution` finalization, A1 evaluation/persistence runs after existing immutable-plan checks and before `complete_operation`, within the same database transaction. Binding failure marks the operation `needs_attention` with a stable safe code; it does not infer success or mark the wizard applied.

Startup `reconcile_completed_storage_state` replays succeeded test-only operations through the same evaluator. A simulated host-success/database-interruption creates the missing row once; immediate replay creates zero rows; a later distinct operation appends history. Invalid immutable binding records one idempotent `drive_intake_reconciliation_deferred` operation event containing only the stable reason code. Reconciliation invokes no executor or storage command.

## Authenticated redacted GET API

- `GET /api/v1/storage/disks/{physical_disk_id}/intake-history`
- `GET /api/v1/storage/disks/{physical_disk_id}/intake-assessment`

Both require the existing read scope (or admin). Synthetic HTTP proof shows unauthenticated access returns 401, an operate-only token returns 403, and a read-scoped token succeeds. Responses expose durable database IDs, hashes, policy scope, normalized outcome/code/evidence hashes, reason codes, timestamps and explicit `current`/`stale` state. They do not expose stable identity, serial/WWN, kernel/by-id path or raw evidence. Empty history returns `NOT_TESTED` with reason `not_tested`, never a healthy result. No write/override endpoint exists.

## Synthetic QA

Final combined command covered `test_storage_intake.py`, `test_worker.py`, `test_migrations.py`, `test_migration_identity.py`, `test_cli_migration_admin.py`, and the full `test_api.py`:

- **131 passed** in the final run.
- One non-failing pre-existing Starlette TestClient deprecation warning; no test failure or skip.
- Focused tests prove migration upgrade/downgrade and constraints; PASS/SOURCE_ONLY; skip/unsupported/missing/duplicate/partial/failed/notice paths; unstable/conflicting/duplicate identity; destination signatures; exact/missing/wrong/newest-unbound snapshots; plan/result/device/fingerprint/topology tamper; deterministic reasons; finalization transaction; startup interruption/reconciliation/replay; later append; heterogeneous model/capacity; kernel-path reorder; redaction/auth; and `NOT_TESTED`.
- Ruff format applied to all new Python files; Ruff check on every changed Python path: PASS.
- Python compile on every changed Python path: PASS.
- `git diff --check` on every implementation path: PASS.
- Staged added-line credential-value pattern scan: 0 matches.

All tests used synthetic documents, fake observations and disposable SQLite databases. No test runner invoked a block-device, mount, SMART, wipe, format, pool, executor or host command.

## Prohibited-action counters

| Action class | Count |
|---|---:|
| Attached USB/raw device or hardware-identity access | 0 |
| Windows/Hyper-V/VM/guest access | 0 |
| Credential or live-service access | 0 |
| Real storage/SMART/mount/format/wipe command | 0 |
| Manual PASS/override or pool-admission gate | 0 |
| UI/import/ZFS/RAID/iSCSI/HA/deployment work | 0 |
| Frontend, executor-command or adjacent-roadmap edit | 0 |

## Supervisor gate

Reproduce the six-file implementation tree, migration upgrade/downgrade, 131-test command, exact snapshot-binding negative cases, transactional finalization, startup idempotency, API authorization/redaction and staged secret scan. A1 stops here: admission gating, UI, real drive intake, pool creation/import, formatting, ZFS/iSCSI and the later three-host topology remain separately unauthorized.
