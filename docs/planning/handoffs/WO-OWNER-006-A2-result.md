# WO-OWNER-006-A2 result — exact standalone LIO managed-zvol readback

Status: **CORRECTED AND PUSHED — awaiting repeat Supervisor QA**

Authority: ACC-087 / DEC-2026-08-26-127

Implementation commit: `71f2a702716b2b2e59ee3dcd26ee8772766e38e8`

Implementation tree: `21eaba234f9d39e29ec7303e6a79090d6d09268a`

Parser correction commit: `8605d3f7772a6bb8da1fdbc91854d340b346e3e6`

Parser correction tree: `389c55e9727f652b586b66d19f7e0db1a2c21916`

## Supervisor correction

Supervisor QA reproduced two under-4-MiB parser escapes in the original A2 result: an unrelated 5,000-level JSON array raised raw `RecursionError`, and an unrelated 5,000-digit JSON integer raised raw `ValueError` from Python's integer-conversion limit. Correction `8605d3f` changes only the JSON exception boundary so `RecursionError` and `ValueError` become the existing bounded `LioReadbackError`; the prior explicit `LioReadbackError` branch remains first and unchanged.

Both exact inputs now have direct-reader and executor regressions. Direct reads return the generic bounded message and stable readback code. At executor level, each performs exactly two fake targetcli calls (the original apply plus the single allowed cleanup), returns `needs_attention=true`, and writes state 0 times. No success schema, targetcli script, executor ordering, cleanup policy or other production path changed. A concurrent non-overlapping H8I handoff commit `58a8db5` became the correction parent and was not edited or staged by A2.

## Certified inputs and ancestry

The complete order reproduced as 12,386 bytes / SHA-256 `808FD38183C7551FA319226A119B02933DB5EB66091D595BD75E4A6FCB1B30D0` before editing. A1's order reproduced as 11,573 bytes / `14079F69E103CDB68BA21F064C3761998797AA940CD7EAF0BDF018CAF55FD628`; its accepted handoff reproduced as 8,668 bytes / `44097C1F1CF9FC147A5009E0707A1A668D58593F86AE908A5B485940AC2BD263`. The A1 implementation, complete current executor and focused tests, OWNER-06/OWNER-07 and HA-01/HA-02/HA-10 rows, and DEC-2026-08-26-120/127 were read before edits.

Dispatch local/origin HEAD was `aab2d1f3da222cd19e3f3dff29874003a9902413`. During A2, concurrent non-overlapping commits `1fcbf7c`, `66ea854` and `265be8f` advanced the shared branch; the A2 implementation parent is therefore `265be8fa8026a5a05a1012daf1dfb1f629ae0793`. After push, local and origin both resolved to the implementation commit.

The inherited tracked baseline was preserved: `backend/src/hoardarr/api/routes/automation.py`, `backend/src/hoardarr/api/routes/telemetry.py`, `backend/src/hoardarr/storage/drain_worker.py`, `backend/src/hoardarr/storage/foreign.py`, `backend/src/hoardarr/telemetry/alerts.py`, `docs/development/architecture-reconciliation.md`, `docs/planning/execution-gap-matrix.csv`, `docs/planning/execution-gap-matrix.md`, `docs/planning/specs/community-profiles-leaderboards.md`, `docs/planning/specs/fleet-telemetry-hardware-lifecycle.md`, `docs/planning/unified-product-roadmap.md`, `frontend/src/components/StorageExpansionPanel.test.tsx`, `frontend/src/components/StorageVolumesPanel.tsx`, `scripts/build-execution-gap-matrix.py`, and App-owned `tests/release/test_offline_appliance.py`. The last path was committed independently during A2 and was never edited or staged here.

The inherited untracked baseline was also preserved: `.codex-temp/`; `.tmp-deploy-33f8d2f/`, `.tmp-deploy-62c4613/`, `.tmp-deploy-66246ac/`, `.tmp-deploy-732f377/`, `.tmp-deploy-81a9dd2/`, `.tmp-deploy-876fdae/`, `.tmp-deploy-aeb836e/`, `.tmp-deploy-d6697d3/`; `docs/design/`; `docs/planning/acceptance-ledger.md`, `architecture/`, `blockers.md`, `chat-registry.md`, `decision-log.md`, `design/`, `fleet-contract-gap-analysis.md`, `kpi-ui-reconciliation.md`, `supervisor-state.md`, and `work-orders/`; `docs/planning/handoffs/README.md` and the pre-existing APP-001/002, KPIUI-001/002/003, LAB-001/002/004/005, UX-001/002, WEB-001/003/004/005/006/007/008/009/010/011 and WEB-013-NOTES handoffs; the pre-existing gated-intake, KPI/WebUI, local-user, offline-appliance, community-add-on, three-host-cluster, UX and fleet-contract specs; `scripts/invoke-hoardarr-build-credential-recovery.ps1`, `scripts/reconcile-hoardarr-keepass.ps1`, `scripts/validate-hoardarr-static.py`; `test-results/`; and `website/`.

## Implemented reader and bounds

One isolated pure module reads the existing rtslib-fb path `/etc/rtslib-fb-target/saveconfig.json` through an injectable constant. It performs no shell, targetcli, systemd, device, network, credential or database call.

- Maximum file size/read: 4 MiB, with a one-byte overflow sentinel.
- File identity: `lstat`, symlink/non-regular rejection, size check, no-follow open where supported, `fstat`, named/opened device+inode equality, and a second size/read cap.
- JSON: UTF-8 object only, duplicate-key rejection at every nesting level, required `storage_objects` and `targets` lists.
- Collection cap: 256 entries for top-level storage objects/targets and every selected target's TPG, LUN, portal and ACL collection.
- Missing, unreadable, symlink, directory/non-regular, oversized, malformed, duplicate-key, wrong top-level, type-confused, ambiguous and over-bound input becomes a bounded `ExecutorFailure` with `needs_attention=true`.

## Exact postcondition truth table

| Selected fact | Required apply state | Removal state |
|---|---|---|
| Backstore | exactly one matching deterministic name, plugin `block`, exact bound device | exact name absent |
| Target | exactly one matching IQN, fabric `iscsi` | exact IQN absent |
| TPG | exactly one, integer tag 1 | not inspected after proven target absence |
| LUN | exactly one, integer LUN 0, maps `/backstores/block/<exact-name>` | not applicable |
| Portals | exact normalized address set, port 3260, no duplicates; wildcard and explicit sets are not interchangeable | not applicable |
| ACLs | exact initiator set, no missing/extra/duplicate identity | not applicable |
| CHAP disabled | no unilateral or mutual credential material on selected ACLs | not applicable |
| CHAP enabled | every selected ACL's user and value equals in-memory expected data; mutual CHAP absent | not applicable |
| Safety attributes | integer `generate_node_acls=0`, `demo_mode_write_protect=1` | not applicable |
| Unrelated valid objects | ignored, never emitted or cleaned | ignored |

Missing, duplicate, wrong-plugin, wrong-device, wrong-fabric, target collision, TPG/tag disagreement, LUN mismatch/extra mapping, portal/ACL disagreement, CHAP disagreement and safety-attribute absence/disagreement all fail closed. Removal fails if either selected identity remains; there is no removal retry.

## Sanitized evidence

Successful apply returns `readback` schema version 1 with: state; service ID; target IQN; deterministic backstore name; `block` plugin; device-equality boolean; redacted volume-ID suffix; TPG tag; LUN index; sorted public portal and initiator identities; `chap_configured`, user-equality and secret-equality booleans; the two safety attributes; and `evidence_sha256`. Successful absence evidence contains schema version, absent state, service/target/backstore identities, target/backstore absence booleans, and digest.

The evidence digest is canonical SHA-256 over exactly the sanitized document excluding `evidence_sha256`. Evidence and safe errors contain no raw zvol/provider/stable identity, raw saveconfig, unrelated facts, command output, environment value, CHAP value, CHAP hash, length or prefix.

## Mutation, cleanup and state ordering

The A1 `_targetcli` mutation function is byte-identical and still appends exactly `saveconfig\nexit\n`; the managed apply and remove command lists are unchanged. A2 only reads after that existing call.

- Successful apply: one targetcli call -> one exact readback -> one Hoardarr state write -> active result.
- Apply-readback failure: one apply call -> one failed readback -> exactly one same-service LIO cleanup call -> one absence read. Proven cleanup returns the original bounded readback failure; uncertain cleanup/absence returns `connectivity_lio_readback_cleanup_uncertain`. State writes: 0.
- Successful removal: one remove call -> one absence read -> one state write -> removed result with `backing_data_deleted=false`.
- Removal-readback failure: one remove call -> one failed absence read -> no retry and 0 state writes. A disposable prior state file remained byte-identical.
- `delete_backing_data=true`: targetcli 0, readback 0, state read 0, state write 0.

The ten executor-level file/structure/parser reader negatives each made exactly two fake targetcli calls (apply plus the one allowed cleanup), one failed apply read, one failed absence read, and 0 state writes. The explicit apply mismatch and uncertain-cleanup cases each made two fake calls and 0 state writes. Successful apply/removal each made one fake mutation and one state write. Failed removal made one fake mutation, 0 retries and 0 state writes. Across every managed negative, backing-file allocation, truncate/fallocate, chmod/chown, unlink, format, mount, ZFS create/destroy and system-service commands were all 0.

## QA and scope proof

- Complete focused A2/A1 connectivity, API, worker, storage-volume and migration set: **207 passed, 1 expected Windows/POSIX descriptor skip**.
- Fresh final parser/readback run after formatting and digest assertions: **56 passed**.
- Correction-focused connectivity matrix: **95 passed, 1 expected Windows/POSIX descriptor skip**; exact reproduced-case selection: **4 passed**.
- The corrected matrix includes 11 direct file/parser-reader negatives, 10 executor reader/state-write negatives, 22 selected-graph mutations, both wildcard mismatch directions, 4 CHAP disagreement cases, 3 removal-presence cases, proven and uncertain apply cleanup, successful/failed removal ordering, prior-state preservation and destructive-delete zero calls.
- Ruff format/check on all four changed files: pass.
- Python compile: pass.
- `git diff --check`: pass.
- Added-line/source scan: private-key 0, AWS key 0, bearer 0, assigned-secret 0. Production parser raw-zvol literals: 0. Two raw zvol strings occur only in deliberate disposable parser fixtures, and fixture/output assertions prove they never enter evidence or error text.
- Implementation scope: exactly `backend/src/hoardarr/connectivity/executor.py`, new pure `backend/src/hoardarr/connectivity/lio_readback.py`, focused `backend/tests/test_lio_readback.py`, and the minimal A1 managed-executor fixture adjustment. No API, service normalization, model, migration, worker, intake/wizard, frontend, release/appliance, website, HA/cluster, roadmap or Supervisor file is present.

A2 used only disposable JSON, fake targetcli and fake state fixtures. It did not access a USB/SAS/NVMe disk, Windows/Hyper-V host, VM/guest, `/dev`, live targetcli/rtslib/systemd/network, credential store, pool, zvol, target or deployment.

Deferred gates remain explicit: interrupted-operation idempotent resume, boot/restart restoration, real target deployment/readback, initiator multipath, shared-ext4 ownership, ALUA/SCSI-3 PR, dual controllers, fencing, quorum and automatic failover. Stop at A2 for Supervisor QA; no successor is authorized.
