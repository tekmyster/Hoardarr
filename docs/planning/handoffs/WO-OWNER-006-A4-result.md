# WO-OWNER-006-A4 result — disposable real ZFS-zvol/LIO lifecycle

## Disposition

**IMPLEMENTATION PUSHED; SOLE AUTOMATIC RUN FAILED CLOSED — stopped for Supervisor QA.**

The first real Linux run reached the local CHAP initiator login, received a non-retryable iSCSI login failure, entered the exact cleanup trap, and then exhausted the job's 25-minute timeout before cleanup completed. The run was not dispatched, retried, rerun, cancelled, or otherwise modified by this Builder. No corrective commit was made after the terminal result.

## Authority, locked inputs, and ancestry

- Authority: ACC-094 / DEC-2026-08-26-134.
- A4 work order: 9,887 bytes; SHA-256 `376CC6C2DB183169E4000FBD634D9854AE109EA52ADD8840A5B353938CCFBDA2`.
- A1 implementation: `4e3635452ce7155f320e0626f553c761da482b7f`; tree `17d5571925865e97cb54b159887dddb267af2fd3`.
- A2 implementation/correction: `71f2a702716b2b2e59ee3dcd26ee8772766e38e8` / `8605d3f7772a6bb8da1fdbc91854d340b346e3e6`; trees `21eaba234f9d39e29ec7303e6a79090d6d09268a` / `389c55e9727f652b586b66d19f7e0db1a2c21916`.
- A3 implementation/handoff: `7b6d3809afe78cf77e3937e1b1fa27d04619f62a` / `a3571e6b72090eef1e6e0ecea2c1604f61fd9189`.
- A3 handoff reproduced as 7,152 bytes; SHA-256 `ECBEBCB98B5E5D9F11F20A967A41ABB92005012599632B60135F98CEF5BC3EBC`.
- Existing workflow before A4: 9,493 bytes; SHA-256 `26730D2E02B74AA1AEDB3E62776DF650CE37283205C0DE8DDB448B56A9ACDDE2`.
- Existing privileged reference script: 9,498 bytes; SHA-256 `5B0BC6AC85C2A4EC93234B1179B5745A2FD2C2622AE78554757A776285416D42`.
- Dispatch local/origin HEAD: `a3571e6b72090eef1e6e0ecea2c1604f61fd9189`. Concurrent unrelated documentation/release-test commits advanced the shared branch without overlapping the A4 paths.
- A4 implementation: `cf5ca41adc6e9d7345b532364b6154136f9b2ad9`; parent `f251ba3a6e6283d70c7ac33391110a6e48da3536`; tree `1ebb6f421f92f4fa66bf0d3199d77daf91ab0de9`.

All inherited App, Website, Supervisor, roadmap, frontend, telemetry, release-test, temporary, evidence, and planning changes were preserved and excluded.

## Implementation scope and identities

The single implementation commit contains exactly four authorized files, 734 insertions, and no production-code edit:

| File | Bytes | SHA-256 |
|---|---:|---|
| `.github/workflows/storage-integration.yml` | 11,647 | `4B1CBB76DF760FBD617CB67BEC4831386B7706E9F1C9A51466243A02843B7DC1` |
| `tests/integration/run-managed-zvol-lio-lifecycle.sh` | 16,101 | `22D68C16506957664FB6F7EAD58FD7814174AC3B18951D69A8A6A8F07371339E` |
| `tests/integration/managed_zvol_lio_lifecycle.py` | 9,438 | `B873DBB2A68E53CDA15D27D57BD8630A2EC7778C82C55E4963CB6C0D93374DBD` |
| `tests/integration/test_managed_zvol_lio_assets.py` | 3,656 | `16DC6FCFD479A7C4E1661B247282F27F8EB31250AE459168C59970D6751BE368` |

The workflow adds one `ubuntu-24.04` job, updates only the required connectivity/integration path filter, uses the locked backend environment, installs the bounded required packages, validates one sanitized JSON with `jq`, and uploads only that JSON. The script is executable mode `100755`.

The helper invokes the accepted production connectivity executor. It overrides only the injectable executor state-file constant, wraps production targetcli/state/readback calls for counters, and emits booleans, sanitized A2 evidence digests, and hashed test-only identities. It does not emit CHAP material, raw zvol/provider/stable identity, raw saveconfig, environment, or unrestricted command output.

## Local gates

- Focused guard/workflow/schema tests: **12 passed**.
- Guards cover non-root, absent GitHub Actions context, absent marker, unsafe cleanup roots, wrong loop counts, non-loop candidates, foreign backing paths, and unresolved loop facts.
- Git Bash syntax: passed.
- Ruff format/check for both Python assets: passed.
- Python compile: passed.
- Synthetic bounded evidence passed the workflow-equivalent `jq -e` predicate.
- Destructive-target review: every target/delete/destroy/detach/unmount/remove operation is bound to a fixed test identity, exact owned loop, exact unique pool, or marker-proven `mktemp` root. No broad device glob exists.
- `git diff --check`: passed.
- Staged scope: exactly the four authorized files; executable mode verified.
- Source and staged added-line secret scans: clean. The CHAP fixture is deterministically derived in-process from a fixed synthetic service identity and is neither logged nor persisted in evidence.
- Local real disk, `/dev`, host/VM, target, ZFS, iSCSI, credential, and network-storage actions: **0**.

An initial static-test assertion incorrectly scanned later pre-existing workflow jobs and reported 11 passed / 1 failed. Supervisor coordination explicitly authorized continued local A4 work; the assertion was narrowed to the new job block, after which the final 12/12 local matrix passed. No implementation push occurred before the final clean matrix.

## Sole automatic run

- Workflow: `Isolated storage integration`.
- Run: `33034885581`; event `push`; exact head `cf5ca41adc6e9d7345b532364b6154136f9b2ad9`.
- URL: `https://github.com/tekmyster/Hoardarr/actions/runs/33034885581`.
- Created/started: `2026-08-27T02:57:33Z`; terminal update: `2026-08-27T03:22:54Z`.
- Overall conclusion: `cancelled` due the managed job timeout.
- Managed job: `98395337958`, `managed-zvol-lio-lifecycle`, started `02:57:37Z`, completed `03:22:53Z`, conclusion `cancelled`.
- Checkout, locked environment sync, and package installation passed.
- The real lifecycle step failed at script line 202 at `02:58:06Z`: `iscsiadm` returned rc 19, a non-retryable initiator login failure. The bounded log contained no CHAP value.
- The error trap fired immediately. Cleanup then failed to terminate before the hard timeout; the runner cancelled the step at `03:22:50Z`.
- Evidence validation and managed artifact upload were skipped. No managed-zvol evidence artifact exists. Four artifacts from unrelated existing jobs were listed but not downloaded.
- Other automatic jobs: storage-group drain, mergerFS telemetry, controller redundancy, and extended storage stacks succeeded; the manual-only self-hosted job was skipped.
- Automatic workflow actions by this Builder: dispatch 0, retry 0, rerun 0, cancel 0. Exactly one implementation push and one resulting automatic run occurred.

## Proven and unproven lifecycle facts

Because the script is `set -euo pipefail` and the first reported failure is line 202, control-flow evidence proves the preceding ordered gates completed:

- root/GitHub Actions/marker and exact safe-root guards;
- six uniquely owned sparse-file loop devices with no selected mount, holder, signature, or existing ZFS membership;
- one six-member RAIDZ2 pool, ONLINE health, one RAIDZ2 vdev, ashift 12, and one real fixed-size `/dev/zvol` block device;
- initial production executor apply, one targetcli mutation, sanitized A2 active block/LUN/portal/ACL/CHAP-equality readback, state write, unchanged pre-I/O zvol size/allocation, and an independent production-reader digest match;
- initiator identity installation, service restart, discovery, and node CHAP configuration commands.

The login itself did **not** succeed. Therefore A4 does not prove by-path resolution, filesystem creation, bounded I/O, data hash, idempotent apply, lost-state recovery, target persistence restart, removal/absence, destructive-delete rejection, backing retention, or final cleanup. No success evidence JSON exists, so none of those later booleans or counters are claimed.

The exact CHAP login mismatch cause is not proven from retained logs. The cleanup stall location is also not proven because cleanup emitted no phase markers and the job was cancelled before returning. These are two separate defects: an initiator/target authentication interoperability failure and an unbounded cleanup command path.

## Safety and deferred gates

The only real storage objects were GitHub-hosted disposable sparse files, six loops, one uniquely named RAIDZ2 pool/zvol, and one unique standalone LIO target. No physical SAS/NVMe/USB media, local Supervisor host, Windows/Hyper-V, VM/guest, Proxmox, cloud credential, network storage, multipath, A/B controller, shared filesystem, fencing, quorum, or HA action occurred.

No retry is authorized. Proposed narrow successor only: a bounded A4 correction that first captures sanitized target/initiator CHAP-mode equality booleans before login and adds per-cleanup-command timeouts/phase evidence, while retaining the exact same single-host disposable topology and production executor boundary. It must receive separate authority and one fresh automatic-run allowance.

Stopped for Supervisor QA. No successor was begun.
