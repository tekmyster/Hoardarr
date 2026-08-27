# WO-OWNER-006-A1 result — managed zvol iSCSI binding

Status: **IMPLEMENTED AND PUSHED — awaiting Supervisor QA**

Authority: ACC-083 / DEC-2026-08-26-123

Implementation commit: `4e3635452ce7155f320e0626f553c761da482b7f`

Implementation tree: `17d5571925865e97cb54b159887dddb267af2fd3`

## Scope and baseline

The certified order was read completely before editing and reproduced as 11,573 bytes / SHA-256 `14079F69E103CDB68BA21F064C3761998797AA940CD7EAF0BDF018CAF55FD628`. The accepted A2 order reproduced as 8,744 bytes / `E86F7DE09425F563AB77C7813A26DB04F050C56BB423AED0494F6633ACF5483C`; its accepted handoff reproduced as 9,126 bytes / `6A120CF99667F73F1B111977FF16132154542D9EBC7685017F4F3550D96FFD0F`. The complete current connectivity API, schema, normalizer, executor, worker seams and focused tests were read, together with the OWNER-05/OWNER-06 roadmap rows and DEC-2026-08-26-120/123. At closure, the concurrent roadmap and decision-log snapshots were respectively 159,340 bytes / `9156816607E71A04F9A0D5038AB6501FB4B09208D75F1ABCDBDDCFF8F991B25B` and 161,898 bytes / `39238E1E4A411321D116C0FABDF4669D0497FC910D340D93EC1FD8576B5E5FB6`.

Dispatch local/origin HEAD was `ea365d27af039beb45c210a76d306590400bfae8`. Concurrent App-owned F19 commits `d5f7ca36a61ee5b75a0f0b63a14be5056d7ad7f8` and `053ceb793b299910a880455e89eeb2bc3086d760` advanced the shared branch before this implementation commit; neither overlapped A1. The implementation parent was therefore `053ceb793b299910a880455e89eeb2bc3086d760`. After push, local and `origin/rc/0.3.11-validation` both resolved to the implementation commit.

Inherited tracked changes were preserved in:

- `backend/src/hoardarr/api/routes/automation.py`, `backend/src/hoardarr/api/routes/telemetry.py`, `backend/src/hoardarr/storage/drain_worker.py`, `backend/src/hoardarr/storage/foreign.py`, `backend/src/hoardarr/telemetry/alerts.py`;
- `docs/development/architecture-reconciliation.md`, both execution-gap-matrix files, the community-profile and fleet-telemetry specs, and `docs/planning/unified-product-roadmap.md`;
- `frontend/src/components/StorageExpansionPanel.test.tsx`, `frontend/src/components/StorageVolumesPanel.tsx`, and `scripts/build-execution-gap-matrix.py`.

Inherited untracked roots/files remained untouched: `.codex-temp/`; the eight `.tmp-deploy-*` roots present at closure; `docs/design/`; `docs/planning/{acceptance-ledger.md,architecture/,blockers.md,chat-registry.md,decision-log.md,design/,fleet-contract-gap-analysis.md,kpi-ui-reconciliation.md,supervisor-state.md,work-orders/}`; all pre-existing untracked handoffs; the pre-existing planning specs; `scripts/invoke-hoardarr-build-credential-recovery.ps1`, `scripts/reconcile-hoardarr-keepass.ps1`, `scripts/validate-hoardarr-static.py`; `test-results/`; and `website/`.

## Implemented contract

No model or migration was added. `ConnectivityService.config_json` now carries the immutable managed binding while `StorageVolume` remains the canonical current row.

The request/binding truth table is:

| Request/current state | Result |
|---|---|
| non-iSCSI plus `storage_volume_id` | reject before operation creation |
| iSCSI managed ID plus caller path or size | reject before operation creation |
| exact active `zfs` / `zvol` / `block` volume, supported+available size and block capability, canonical identity/path | resolve and persist managed binding |
| dataset, snapshot, filesystem, external LUN, non-ZFS, inactive/deleting/failed, absent, malformed, unsupported or unavailable row | deterministic fail closed |
| exact managed binding on update | allowed |
| managed volume replacement, managed-to-fileio, or fileio-to-managed update | `connectivity_recreate_required` |
| legacy fileio create/update | existing normalized keys, hash semantics and command behavior retained |
| managed remove with `delete_backing_data=true` | reject before executor; no operation/executor mutation |
| managed remove with data retention | remove LIO objects only; `backing_data_deleted=false` |

The persisted envelope is strictly keyed and non-secret: discriminator `kind=managed_zvol`; durable volume ID; stable identity; provider; resource type; provider resource ID; canonical `/dev/zvol/<pool>/<zvol>` path; positive bounded byte size; and `binding_sha256`. The digest is canonical JSON over exactly these seven identity/data fields, excluding the discriminator and digest itself: `storage_volume_id`, `stable_identity`, `provider`, `resource_type`, `provider_resource_id`, `device_path`, `size_bytes`.

Read APIs copy and redact `stable_identity`, `provider_resource_id`, and `device_path`. CHAP plaintext remains solely on the existing secret path and is absent from the binding, configuration, API document, operation request/result and handoff.

## No-bypass and execution evidence

The API transaction calls the central resolver before create/update normalization. The worker, inside its immediate pre-executor database transaction, verifies both the operation-request hash and a fresh canonical hash of stored configuration, validates the stored binding/digest, reopens the exact `StorageVolume`, recomputes every binding field, and compares it to current state. The same `_execute_connectivity` path is used on a requeued/resume attempt.

Synthetic worker tests changed, one at a time: lifecycle, capability availability, stable identity, provider resource ID, canonical device path, byte size, and stored binding digest. Each first attempt and requeued resume failed with `needs_attention`; fake-applier calls were **0/14**. Safe operation errors contained no raw `tank/...` identity. Two caller path/size conflicts created **0** operations.

For managed backing, the fake targetcli script shape is:

```text
/backstores/block create hoardarr-zvol-<sha256(service-id)[0:24]> /dev/zvol/<pool>/<zvol>
/iscsi create <validated-target-iqn>
/iscsi/<target>/tpg1/luns create /backstores/block/<bounded-backstore>
/iscsi/<target>/tpg1 set attribute generate_node_acls=0 demo_mode_write_protect=1
<existing deterministic portal deletion/creation>
<existing validated initiator ACL and optional in-memory CHAP commands>
```

Removal uses `/iscsi delete <target>` and `/backstores/block delete <bounded-backstore>`. Failure after managed block-backstore setup attempts only that bounded LIO cleanup; cleanup uncertainty becomes `connectivity_iscsi_rollback_failed` with `needs_attention=true`. The backing zvol is never removed.

Legacy fileio remains the existing script shape and naming: `/backstores/fileio create hoardarr-<service-id-prefix> <managed-file-path>` and a LUN under `/backstores/fileio/...`; existing allocation, validation and optional file-deletion semantics are unchanged.

Fake prohibited-action counters for the managed branch were: backing-file allocator **0**, truncate/fallocate **0**, unlink **0**, chmod/chown **0**, format/mount **0**, ZFS create/destroy or other storage command **0**, and live targetcli/systemctl/network calls **0**. No exact live readback or persistence-after-restart claim is made.

## QA

- Focused connectivity/API/worker plus storage-volume and migration regressions: **151 passed, 1 expected Windows-only POSIX descriptor skip**.
- Ruff check on all eight changed Python files: pass.
- Ruff format check on the five cleanly isolated changed files: pass. Whole-file formatting of the shared `test_api.py` and `worker.py` was intentionally not applied because Ruff proposes unrelated pre-existing formatting outside A1; A1-added code passes Ruff check and is convention-formatted.
- Python compile of backend source and the new focused test: pass.
- `git diff --check`: pass.
- Added-line secret scan: private-key 0, AWS-access-key 0, bearer 0, assigned-secret 0. The generic 40-character candidate pass found only cited Git hashes plus four repository-path fragments; credential-form high-entropy tokens were 0.
- Staged implementation paths: exactly the five authorized production files and three directly focused test files; 819 insertions / 23 deletions. No model, migration, frontend, wizard/intake, installer/workflow/release, website, HA/cluster, or Supervisor-control file was included.

No USB/SAS/NVMe disk, Windows/Hyper-V host, VM/guest, `/dev` device, live LIO target, network, credential store, pool, zvol or deployment was accessed or changed. All storage rows, command scripts, executor results and databases used for proof were synthetic/fake/disposable.

Disposition: stop at A1 and await Supervisor QA. No target-controller, ALUA, multipath, clustering, fencing, quorum, failover, pool geometry, deployment or adjacent roadmap item is authorized or implied.
