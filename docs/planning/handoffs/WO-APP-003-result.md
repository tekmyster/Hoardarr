# WO-APP-003 Result

## Result

- **Disposition:** IMPLEMENTED AND SOFTWARE-VERIFIED; supervisor acceptance pending.
- **Dispatch baseline:** `rc/0.3.11-validation` at
  `423f94af63438a1ccac56f1bc84411e353905117`.
- **Implementation commit:** `18b6594137f4cf143f7b81e719c2d61c89f4dcba`
  (`Add fail-closed migration identity and session tooling`).
- **Supervisor correction commit:** `a15e5f4c7323f23a0ef7f54a1ae2c2e9872a5496`
  (`Harden identity migration service quiescence`). This extends the identity-migration offline
  gate to every accepted Hoardarr writer/observer service; it does not change the separately
  designed API-only session-revocation gate.
- Added the required local CLI contracts:

  ```text
  hoardarr migrate-hardware-identities --manifest <absolute.json> \
    --expected-database-sha256 <sha256> --dry-run
  hoardarr migrate-hardware-identities --manifest <absolute.json> \
    --expected-database-sha256 <sha256> --apply
  hoardarr auth revoke-all-sessions --reason migration-cutover \
    --expected-count <count> --json
  ```

- Hardware identity migration is a control-plane-only operation. It does not invoke a storage
  command, open a block device, change a filesystem, alter an array/pool, mount/unmount anything,
  or write an image.
- Session revocation is owned by the authentication service and uses the product audit path. It
  never returns tokens, hashes, CSRF material, password verifiers, or credentials.
- No WebUI/frontend, website/community, fleet/public analytics, virtualization, guest/appliance,
  network/DNS, KeePass, real storage, or protected host disk was changed.

### Ownership inventory

| Surface | Owner and change |
|---|---|
| `physical_disks` | `PhysicalDisk`; stable physical identity is rebound in place. Its durable row ID is preserved. |
| `physical_disk_identity_aliases` | New migration `0028`; one unique retired identity resolves to one durable `PhysicalDisk`. |
| disk discovery | `storage.groups.register_disk`; an observation using a retired alias resolves to the existing disk instead of creating a duplicate. |
| Storage Groups | `StorageGroup.policy_json`; exact physical/member references are reconciled, while group ID/name/namespace are preserved. |
| storage backends | `StorageBackend`; physical FK and backend ID are preserved; `disk:<identity>` and exact config references are rebound, including landing/member state. |
| logical storage | `StorageEntity`; logical stable identity, filesystem UUID, mountpoint, and ID are preserved. Exact member references and an observed presentation device path are reconciled. |
| managed volumes | `StorageVolume`; stable logical/provider IDs are preserved. Exact physical references and observed device paths are reconciled. |
| storage paths | `StoragePath`; logical/path IDs are preserved. Exact observed kernel-path/config references are reconciled only when the manifest supplies the complete old/new path pair. |
| telemetry/history | `MetricEntity` is rebound in place; `MetricSample`, `MetricRollup`, alert FKs, and entity UUID remain unchanged. Ingestion resolves retired aliases to the current entity, preventing a second drive series. |
| operations/alerts | Historical operation and alert rows are not rewritten or recreated. Their IDs/FKs remain unchanged. |
| active-use safety | `Operation` and `StorageDrainJob` are checked inside the protected transaction, along with registry safety metadata and backend drain state. |
| authentication | `auth.service.revoke_all_active_sessions`; uses the same strict expiry boundary as `principal_from_session`. |
| audit | `audit.service.record_audit`; creates `auth.sessions.revoke_all` with local-console actor and count/reason detail only. |
| CLI/transactions | `hoardarr.cli`; local-root and systemd quiescence checks, deterministic JSON, exit-code mapping, `BEGIN IMMEDIATE`, commit/rollback ownership. |

### Manifest schema

- Strict Pydantic schema, `schema_version = 1`, unknown fields forbidden at every level.
- 1–256 one-to-one mappings; duplicate old IDs, duplicate new IDs, chains, and cycles rejected.
- Input is an absolute, non-traversing, non-symlink `.json` regular file of 1 byte–1 MiB.
- JSON duplicate keys and a file-size/read race are rejected.
- Every mapping requires exact capacity, logical/physical sector geometry, and matching lowercase
  SHA-256 observations on source and converted target.
- ext4 requires matching filesystem UUIDs.
- ZFS requires matching pool GUID and matching optional dataset GUID.
- Linux MD requires matching array UUID, filesystem UUID, exact member count, and every member in
  that array must appear in the manifest.
- Kernel paths are optional observations, never identities. If one is supplied, the old/new pair
  must be complete and the stored source path must match.

Sanitized accepted example:

```json
{
  "schema_version": 1,
  "mappings": [
    {
      "old_identity": "wwn:vmware-source-0001",
      "new_identity": "wwn:hyperv-target-0001",
      "evidence_type": "ext4",
      "source": {
        "capacity_bytes": 8589934592,
        "logical_sector_bytes": 512,
        "physical_sector_bytes": 4096,
        "content_sha256": "4e17f8fe9d0e0b42b7476e983a4cd231095c86df63ba90c37a167c43213f34a8",
        "filesystem_uuid": "a6c45fbf-fdd4-4b92-9e87-5c2fdbeb5ccb",
        "kernel_path": "/dev/sda"
      },
      "target": {
        "capacity_bytes": 8589934592,
        "logical_sector_bytes": 512,
        "physical_sector_bytes": 4096,
        "content_sha256": "4e17f8fe9d0e0b42b7476e983a4cd231095c86df63ba90c37a167c43213f34a8",
        "filesystem_uuid": "a6c45fbf-fdd4-4b92-9e87-5c2fdbeb5ccb",
        "kernel_path": "/dev/sdb",
        "serial": "sanitized-target-serial"
      }
    }
  ]
}
```

Rejection matrix (all have deterministic assertions):

| Condition | Result/code |
|---|---|
| unsupported version, unknown/duplicate JSON fields, malformed/oversized/unsafe file | `manifest_invalid`, `manifest_size_invalid`, or `manifest_path_unsafe` |
| duplicate source/target, chain/cycle, incomplete pair | strict schema rejection |
| capacity/sector/digest mismatch | strict schema rejection or `source_geometry_mismatch` |
| ext4 UUID, ZFS GUID, MD UUID/member-count mismatch | strict schema rejection or source evidence rejection |
| incomplete MD membership | strict schema rejection |
| missing/ambiguous managed source | `source_identity_unmatched` / `source_identity_ambiguous` |
| system/boot/protected source | `protected_disk` |
| mounted foreign source | `mounted_foreign_disk` |
| active operation/drain/use marker | `disk_active` |
| target physical/alias/backend/telemetry identity already owned | `target_identity_owned` or `target_telemetry_owned` |
| exact database digest mismatch or non-checkpointed WAL | `database_precondition_failed` or `database_wal_not_checkpointed` |
| predicate changes inside transaction | `transaction_state_drift` or the exact safety rejection |
| injected post-rebind failure | full transaction rollback |
| repeat of the same accepted manifest | deterministic `already_applied`, no duplicate alias/entity |

### CLI result and transaction contracts

Identity success JSON is bounded and sorted and contains:

```text
schema_version, status, manifest.{schema_version,sha256},
database_precondition.{expected_sha256,matched}, mapped_count,
already_applied_count, rejected_count, mappings[], preserved_logical_ids,
alias_changes[], remaining_unmatched_identities[]
```

Identity exit codes:

- `0`: accepted dry-run or committed apply.
- `2`: malformed bounded input/precondition value.
- `3`: safety, authority, manifest, ownership, or capability rejection.
- `4`: database digest/migration/state-drift precondition failure.
- `5`: transaction/readback failure.

Identity transaction boundary:

1. Require local root and all five accepted Hoardarr writers/observers to be inactive on systemd:
   `hoardarr-api.service`, `hoardarr-worker.service`, `hoardarr-storage-status.service`,
   `hoardarr-account-executor.service`, and `hoardarr-storage-executor.service`.
2. Require current migrations, a checkpointed SQLite main database, and exact file SHA-256.
3. `BEGIN IMMEDIATE` for apply.
4. Re-read every disk/evidence/ownership/active-use predicate.
5. Re-read predicates again after the testable validation boundary and compare the deterministic
   safety inventory.
6. Apply every mapping and alias in the same transaction; flush and read back the result.
7. Commit once; any exception rolls the whole operation back.

Dry-run opens no write transaction, explicitly rolls its read session back, and compares the
database SHA-256 again before returning. The deterministic fixture proves byte-for-byte equality.

An active identity-migration service gate returns exit `3` and this exact bounded result for each
of the five units; the active unit, manifest path, and supplied database digest are not echoed:

```json
{
  "error": {
    "code": "services_active",
    "message": "Stop all Hoardarr API, worker, storage-status, account-executor, and storage-executor services before hardware identity migration."
  },
  "mapped_count": 0,
  "rejected_count": 1,
  "schema_version": 1,
  "status": "rejected"
}
```

The CLI test replaces `Settings()` with a fail-fast sentinel and proves this rejection happens
before database configuration or access. It also asserts that the probed tuple is exactly the
five-unit set above. The API-only session gate remains `hoardarr-api.service` and was not widened.

Session success JSON is:

```text
schema_version, status, expected_count, observed_count, revoked_count,
remaining_active_count, reason, audit_event_id
```

Session rejection JSON adds `error.{code,message}`, sets `revoked_count = 0`, and never echoes an
invalid/untrusted reason. Exit codes use the same `0/2/3/4/5` meanings above. The operation:

1. Requires the local CLI, root authority, `--json`, a bounded sanitized reason, exact count, and
   inactive `hoardarr-api.service` on systemd.
2. Starts `BEGIN IMMEDIATE`.
3. Counts active records using `expires_at > utc_now()`, matching authentication validity.
4. Rechecks the count, deletes exactly that active set, records a sanitized product audit event,
   and reads back zero active sessions in the same transaction.
5. Rolls back deletion and audit together on count drift or injected database/audit failure.
6. A zero/zero invocation is a deterministic audited success.

Required operator sequence (documented only; not executed):

```text
stop/quiesce Hoardarr API
checkpoint and hash the offline SQLite database
run revoke-all-sessions with the exact observed count
require remaining_active_count == 0
create the exact-state archive
```

## Evidence

### Disposable fixture readback

- Before: `PhysicalDisk.id = disk-id`, identity `wwn:vmware-source-0001`, kernel path `/dev/sda`.
- After: the same `disk-id`, identity `wwn:hyperv-target-0001`, kernel path `/dev/sdb`.
- Preserved exactly: `StorageGroup.id = group-id`, `StorageBackend.id = backend-id`,
  `StorageEntity.id = entity-id`, mount `/srv/media`, filesystem UUID, `StorageVolume.id = volume-id`,
  `MetricEntity.id = metric-id`, and historical `Operation.id = historical-operation`.
- Metric sample and hourly-rollup rows retain `entity_id = metric-id`; the alert retains that same
  FK. No history row is copied.
- One retired alias is stored. Repeating apply returns `mapped_count = 0`,
  `already_applied_count = 1`, and leaves exactly one alias and one metric entity.
- Ingesting simultaneous old-alias and new-identity observations creates one sample and reports one
  duplicate. Rediscovering the old alias returns the existing physical disk and creates no row.
- mergerFS/member references, landing backend identity, group policy, managed-volume config, and
  observed device path are read back with the new physical identity/path.
- Drift injection (`active_use`) and a post-rebind exception both leave the original physical,
  backend, entity, telemetry, and alias readback unchanged.
- Session tests revoke three active sessions belonging to two users while retaining the expired
  session, both users, usernames, password hashes, administrator roles, API token/hash/scopes, and
  prior audit event. Audit-failure and count-drift injection leave all active sessions present.

### Executed checks

All commands ran from `backend` unless stated otherwise.

| Command/check | Result |
|---|---|
| focused identity/session/CLI suite | `30 passed` before final integration refinements |
| original final focused ownership/regression suite (`test_migration_identity`, `test_session_admin`, `test_cli_migration_admin`, `test_storage_groups`, `test_enterprise_telemetry`, `test_migrations`) | `94 passed` |
| supervisor-correction CLI suite (`test_cli_migration_admin`) | `8 passed`; five parametrized active-unit cases all executed |
| supervisor-correction focused ownership/regression suite (same six files) | `99 passed`, 1 existing dependency deprecation warning, 15.45 s |
| `python -m ruff check src tests` | passed |
| `python -m compileall -q src tests` | passed |
| scoped mypy 1.17.1 (`--follow-imports=skip --disable-error-code=attr-defined`) on corrected CLI/test paths | passed, no issues |
| final complete backend pass 1, fresh pytest process | `679 passed, 13 skipped`, 1 dependency deprecation warning, 63.54 s |
| final complete backend pass 2, fresh pytest process | `679 passed, 13 skipped`, 1 dependency deprecation warning, 64.76 s |
| supervisor-correction complete backend pass, fresh pytest process | `684 passed, 13 skipped`, 1 dependency deprecation warning, 65.45 s |
| migration upgrade suite | included in both full passes; new alias table asserted at head |
| supervisor-correction `uv build --wheel` | passed; `hoardarr-0.3.11-py3-none-any.whl`, 533,303 bytes |
| supervisor-correction isolated install/console smoke | passed; direct installed-CLI readback shows both command families |

Built-wheel evidence (temporary validation artifact, not committed):

```text
path: C:\Users\dmessana\AppData\Local\Temp\hoardarr-wo-app-003-b971405a59104f0298b3288f0c109247\hoardarr-0.3.11-py3-none-any.whl
sha256: 4ba6d1d3c94a27133db79cc94142786d63fe0514ba6e128c3b029309632f2f54
```

Supervisor-correction built-wheel and scoped-source evidence:

```text
wheel path: C:\Users\dmessana\AppData\Local\Temp\hoardarr-wo-app-003-followup-6f50d97eec6a4af586d9c666921aef20\hoardarr-0.3.11-py3-none-any.whl
wheel sha256: 3483db0d867e5f46b34089484ab41b964fb6a659cda0d5dd55a5a5f3d3f8a2de
backend/src/hoardarr/cli.py sha256: 007d4053e5ebaea773d79b7bf4d4e295f2b8c66696f64d328aa1c77fc0ac53a1
backend/tests/test_cli_migration_admin.py sha256: 9c0402cbc8528249713c85d565da54f0b564bcedbcc316224370a12097883e98
```

The 13 full-suite skips are pre-existing Linux descriptor/ownership/mount-path tests. Every new
WO-APP-003 safety assertion executed on Windows against real SQLite transactions; none is skipped.
An initial full-suite launch was deliberately terminated by an accidentally supplied one-second
shell timeout and produced no valid test result. It was replaced by the two clean complete passes
above; there was no test discrepancy.

During correction validation, an initial isolated `uvx mypy` invocation did not include project
dependencies and reported import-not-found errors only. It was repeated with the editable project
and pytest dependencies and passed. The first PowerShell wheel-smoke assertion used array
`-notmatch` incorrectly after the wheel installed successfully; direct invocation of the installed
CLI then proved both help contracts. Neither harness correction changed product code.

Files in implementation commit:

```text
backend/src/hoardarr/auth/service.py
backend/src/hoardarr/cli.py
backend/src/hoardarr/db/migrations/versions/0028_physical_disk_identity_aliases.py
backend/src/hoardarr/db/models.py
backend/src/hoardarr/migration_identity.py
backend/src/hoardarr/storage/groups.py
backend/src/hoardarr/telemetry/store.py
backend/tests/test_cli_migration_admin.py
backend/tests/test_migration_identity.py
backend/tests/test_migrations.py
backend/tests/test_session_admin.py
```

Files in supervisor correction commit:

```text
backend/src/hoardarr/cli.py
backend/tests/test_cli_migration_admin.py
```

## Defects

- No known WO-APP-003 functional defect remains.
- The repository does not configure mypy as a supported all-source gate. An exploratory broad
  mypy run found pre-existing annotations in `telemetry/store.py` and `storage/groups.py`; it also
  found the introduced tuple-variable inference seam, which was corrected. The bounded new
  migration/auth/CLI ownership surface then passed the scoped mypy check. Ruff and runtime suites
  cover the two small alias integrations in the existing store/group modules.
- The full suite reports the existing Starlette/httpx deprecation warning; this work order neither
  introduces nor changes it.

## Blockers

- Supervisor acceptance is pending. Completion of this tooling closes only the two product-tooling
  portions of `BLK-006`.
- `LAB-10`, VMware/Hyper-V mutation, source shutdown, image conversion, target attachment,
  identity-bearing restore, cutover, networking, DNS/DHCP, HA, observation, and retirement remain
  outside this work order and were not started.
- Owner decisions and the separate mutation authorization identified by `WO-LAB-001` remain
  external gates.
- Protected host disks 2–5 and the four Cisco/pass-through disks remain untouched.

Final repository state at handoff writing:

- Branch: `rc/0.3.11-validation`.
- Scoped implementation: committed cleanly at
  `18b6594137f4cf143f7b81e719c2d61c89f4dcba`.
- Scoped supervisor correction: committed cleanly at
  `a15e5f4c7323f23a0ef7f54a1ae2c2e9872a5496`.
- The inherited dirty worktree remains present and unmodified outside the scoped paths. This
  handoff is the only new post-implementation work-order artifact.

## Next action

Supervisor should independently review commits
`18b6594137f4cf143f7b81e719c2d61c89f4dcba` and
`a15e5f4c7323f23a0ef7f54a1ae2c2e9872a5496`, reproduce the focused/full checks, inspect the strict
manifest and JSON contracts including the five-service offline gate, and accept or reject
WO-APP-003. Do not execute either command against an appliance and do not begin LAB-10 without a
separately authorized mutation work order.
