# WO-INTAKE-001-A0 result — explicit durable drive-test action outcomes

Status: **COMPLETE — implementation pushed; synthetic-only; awaiting Supervisor QA**

## Disposition and repository identity

- Work order verified before implementation: 6,652 bytes; SHA-256 `1541250A9399B34EACE0687A46B71AC0DCE1F6BED22AE609FBBE28A38AC1E047`.
- Initial observed shared `HEAD`/origin: `2776b7055253566df45fca69295b24eeabd35cab`. Unrelated concurrent commits advanced both to `f34a7eb6be989c42193d35c0a040e8c864ae6b1b` before this implementation commit; those intervening commits did not change either A0-owned file.
- Implementation commit, pushed to `origin/rc/0.3.11-validation`: `937088b34ae83cfa48c962f721be994b25681100` (`fix(storage): persist explicit drive test outcomes`). Its parent is `f34a7eb6be989c42193d35c0a040e8c864ae6b1b`.
- Implementation commit contains exactly `backend/src/hoardarr/storage/executor.py` and `backend/tests/test_storage_executor.py`: 681 insertions, 9 deletions.
- This document is the only authorized second-commit path. Its final on-disk byte count/SHA-256 and the handoff commit are reported with the Supervisor response; a file cannot truthfully contain its own final digest.

## Boundary preserved

All execution and evidence were synthetic: fake runners, synthetic plan/inventory/result objects and pytest disposable roots only. No physical or virtual drive, Windows/Hyper-V host, VM, guest, block-device path, SMART interface, mount, pool, credential or live service was accessed.

The owner context did not alter A0: future Cisco SSD-240G V01 modules are a LAB ONLY / NOT RECOMMENDED FOR PRODUCTION, write-light/read-heavy case; intended active batches are 6–8 on the shared hub with untouched spares rotated for replacement/missing/reorder/bulk-intake cases. A0 creates no 36-member pool, mass-format behavior, device policy, discovery, admission rule or destructive authorization. The generic destructive-result normalization exists only as product schema and was exercised only through a fake runner.

Inherited dirty work was preserved and excluded. At the ownership boundary it included tracked changes under API automation/telemetry, storage drain/foreign, telemetry alerts, architecture/roadmap/gap documents, fleet/community specifications, frontend storage components, the gap-matrix script and `tests/release/test_offline_appliance.py`; untracked `.codex-temp/`, `.tmp-deploy-*`, `docs/design/`, planning ledgers/state/specifications/work orders and other teams' handoffs, credential-recovery scripts, `test-results/`, and `website/` also remained untouched. The A0-owned files were clean before A0 editing.

## Durable result contract

Every completed drive-test action now has exactly one bounded common-envelope record:

```json
{
  "schema_version": 1,
  "action_id": "<exact plan action ID>",
  "device_id": "<exact stable selected-device ID>",
  "type": "<exact drive test action type>",
  "outcome": "passed|failed|skipped|unsupported",
  "code": "<bounded deterministic machine code>",
  "evidence": { "kind": "<bounded evidence class>" }
}
```

The complete serialized record is capped at 16 KiB. Common identity, action/type, code, outcome and evidence shape are validated against the current plan and revalidated device before acceptance. Duplicate, unplanned, mismatched or altered records fail `needs_attention`.

Action evidence is exact:

- `drive.identity.verify`: `passed` / `identity_verified`; immutable-device-revalidation evidence binds the stable plan identity, current revalidation success, stable-identity fact, and only sanitized confidence/conflict facts actually exposed by inventory. It does not claim controller, hub or SMART facts.
- `drive.surface.read`: `passed` / `full_surface_read_completed` only after runner success; evidence is read-only, full-device intended coverage, exact positive revalidated `capacity_bytes`, `badblocks_-sv_full_device`, and command success.
- `drive.write_read.destructive`: `passed` / `destructive_write_read_completed` only after runner success; evidence is destructive-write/read mode, full-device intended coverage, exact positive revalidated `capacity_bytes`, `badblocks_-wsv_full_device`, and command success. This does not infer consent.
- `drive.smart.short|extended`: existing SMART outcome/code/message and passing timestamps/test kind are retained in the common envelope. Unavailable logging remains `skipped` / `smart_self_test_unavailable`, never PASS; evidence records SMART result kind, exact short/extended kind, and command-success truth.

## Journal ordering and replay

Before A0, identity/surface/destructive execution could publish a completed checkpoint with no result; SMART appended its result in memory separately and checkpointed afterward.

After A0, action start/progress may be journaled normally, but successful completion uses one `atomic_json` replacement containing both the validated result and its completed-action checkpoint. There is no intermediate durable state with a new checkpoint and no result. Final test results emit records unchanged in plan-action order.

| Observed state/event | Disposition | PASS/checkpoint publication |
|---|---|---|
| Fresh action succeeds | Validate result; atomically add result + checkpoint | Exactly one |
| Valid result + checkpoint on resume | Revalidate and replay idempotently | Existing pair retained; no rerun/duplicate |
| Result without checkpoint | Remove orphan atomically, then re-execute only under existing semantics | No inferred PASS; one pair only after success |
| Checkpoint without valid result | `test_action_result_missing`, `needs_attention` | None synthesized |
| Invalid/missing capacity | `test_action_capacity_invalid`, `needs_attention`, before runner | None |
| Identity drift | Existing revalidation failure | None |
| Runner nonzero/exception, interruption or cancellation | Propagate failure | None |
| Duplicate checkpoint/result, wrong ID/schema or altered evidence | Fail closed (`test_action_checkpoint_invalid` or `test_action_result_invalid`) | None |

## Command, timeout and safety preservation

No executor command, timeout, approval, revalidation, progress, retry or path-safety behavior was broadened.

- Surface read remains `[_tool("badblocks"), "-sv", os.fspath(device)]`, timeout `604800` seconds.
- Generic destructive write/read remains `[_tool("badblocks"), "-wsv", os.fspath(device)]`, timeout `1209600` seconds.
- Identity verification adds no command and uses the existing selected immutable-device revalidation.
- SMART remains the existing `_run_smart_test`: `smartctl -l selftest`, then `smartctl -t short|long`, polling `smartctl -c`, final `smartctl -l selftest`; each smartctl call retains 120 seconds, overall short/long ceilings retain 3,600 / 1,123,200 seconds, and the pre-existing permissive bridge retry is unchanged.
- The focused test captured the two badblocks argv arrays and exact timeouts through a fake runner. No subprocess or storage tool ran.

## QA evidence

- `python -m pytest -q tests/test_storage_executor.py`: **88 passed, 1 skipped** in the final run. The skip is the pre-existing Windows-host skip for POSIX ownership/mode enforcement.
- Focused result tests cover identity, surface, destructive, SMART short/extended, skipped SMART, exact capacity/command profiles, atomic publication, deterministic order, replay, orphan handling, legacy checkpoint rejection, invalid capacity, identity drift, nonzero/interruption/cancellation, duplicate/mismatched/tampered results and final round trip.
- `python -m ruff check src/hoardarr/storage/executor.py tests/test_storage_executor.py`: **PASS**.
- Ruff formatting was applied to the A0 additions; unrelated pre-existing formatting changes were reverted to preserve the collision boundary.
- `python -m compileall -q src/hoardarr/storage/executor.py tests/test_storage_executor.py`: **PASS**.
- `git diff --check` on both implementation files: **PASS**.
- Implementation diff credential-value pattern scan (case-insensitive): **0 matches**.

## Prohibited-action counters

| Action class | Count |
|---|---:|
| Physical/virtual drive or raw identity access | 0 |
| Windows/Hyper-V/VM/guest access | 0 |
| Real `badblocks`, SMART or block command invocation | 0 |
| Mount/filesystem/pool/iSCSI operation | 0 |
| Credential or live-service access | 0 |
| API/UI/admission/PASS-policy/A1 implementation | 0 |
| Deployment or destructive consent inference | 0 |

## Supervisor gate

A0 closes only the explicit executor evidence-schema gap that stopped A1. Admission/disposition persistence, lab policy, UI/API, pool/import/ZFS/iSCSI and any physical testing remain separately unauthorized. Supervisor QA should reproduce the implementation commit tree, rerun the focused executor suite, inspect the atomic journal write and fail-closed replay cases, verify this one-file handoff commit, and confirm the final handoff identity reported out of band.
