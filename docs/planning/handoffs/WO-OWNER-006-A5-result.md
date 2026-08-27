# WO-OWNER-006-A5 result — bounded CHAP and cleanup diagnosis

## Disposition

**IMPLEMENTATION PUSHED ONCE; SOLE AUTOMATIC RUN FAILED CLOSED AS `HARNESS_ERROR`; NO LOGIN CAUSE OR CLEANUP RESULT ACCEPTED.**

The hosted lifecycle reached the single bounded login and returned status 19. The script then returned in about 34 seconds, but the non-root workflow validator could not read the root-owned mode-0600 sanitized receipt. Validation ended with `Permission denied`, upload was skipped, and no managed-zvol artifact exists. Under Supervisor QA this is an A5 receipt-permissions defect, not product-failure evidence and not accepted login-cause evidence. No retry or correction was attempted.

## Authority, locked inputs, and ancestry

- Authority: ACC-100 / DEC-2026-08-26-140.
- Work order: 8,143 bytes; SHA-256 `0260D47276FE8CC7AE0FDD8DA757CCC752E85F3F230CC0B73D0F712E68399747`.
- A1 implementation: `4e3635452ce7155f320e0626f553c761da482b7f`.
- A2 implementation/correction: `71f2a702716b2b2e59ee3dcd26ee8772766e38e8` / `8605d3f7772a6bb8da1fdbc91854d340b346e3e6`.
- A3 implementation: `7b6d3809afe78cf77e3937e1b1fa27d04619f62a`.
- A4 implementation/handoff: `cf5ca41adc6e9d7345b532364b6154136f9b2ad9` / `0cbb8f7b31fdecb8aaa83bb4aa0081abe474caef`.
- A4 automatic failure: run `33034885581`, job `98395337958`.
- A5 dispatch local/origin HEAD after the temporary coordination hold cleared: `ad89b157f4baf95f652f3d59d254d1d771ab8f8d`.
- A5 implementation: `7b9cb87f1790b7cc94da7e3dc884989853fca8d5`; parent `ad89b157f4baf95f652f3d59d254d1d771ab8f8d`; tree `7bb6bdfd852de244617785008c954fe17c03fe1f`.
- A5 implementation was pushed once to `origin/rc/0.3.11-validation`.
- A concurrent App-owned commit later advanced local HEAD without touching A5 paths. It is not part of the A5 implementation commit.

The committed `backend/src` tree at A5 dispatch was byte-identical to A4: Git tree `66c34759cc06bcdb05cbf3e75fdf95b0be9c1c5b`. The working and committed blobs for `connectivity/executor.py`, `lio_readback.py`, and `service.py` matched A4 exactly. Inherited dirty App, Website, Supervisor, roadmap, frontend, telemetry, temporary, evidence, release-test, and planning paths were preserved and excluded.

## Implementation scope and identities

The implementation commit contains exactly the four A5-authorized files, 1,495 insertions and 208 deletions; no production-code path changed:

| File | Bytes | SHA-256 |
|---|---:|---|
| `.github/workflows/storage-integration.yml` | 12,702 | `AA0DE64939E6E19ECE011B6B4129CACB60DD525E4156B46407D01B5B416F82A1` |
| `tests/integration/managed_zvol_lio_lifecycle.py` | 34,342 | `4BD30C13C13B8B7F53B736DCE8DF1491B136B851108838BBA322D00DA24F7079` |
| `tests/integration/run-managed-zvol-lio-lifecycle.sh` | 25,833 | `BA7B03364A387F2EBF0C7A40C52A8C73787590692759AF20A7DBB6123EC34F0D` |
| `tests/integration/test_managed_zvol_lio_assets.py` | 16,616 | `F26031C523A34C2E68E922302229B7356975B683CB9145C86C4299911E523AEF` |

The shell script remains executable mode `100755`. Staged scope was exactly four files, `backend/src` staged count was zero, staged diff check passed, and private-key/AWS-access-key/bearer/assigned-secret scan counters were all zero.

## Implemented contract

- Preserves A4's six test-created loops, one RAIDZ2 pool, one zvol, production managed-zvol LIO executor, and one local initiator.
- Re-runs production target readback before login and requires exact target, portal, ACL, CHAP equality, and managed backing binding.
- Resolves exactly one fixed-target/fixed-portal default initiator record and rejects zero, multiple, symlink, path escape, unsafe ownership/mode, non-regular, multi-link, oversized, invalid UTF-8, control-bearing, and duplicate relevant fields.
- Compares CHAP method, username, and in-memory fixture with constant-time equality and retains only booleans, safe lengths, test-only identity hashes, and the parity-object digest.
- Calls the fixed login command at exactly one source site and only after exact parity. There is no retry, reconnect, second portal, or post-remove login.
- Caps login and journal time and file size, validates UTF-8/control/secret conditions, and allows only ACL, authentication-method, credential, transport, generic-login, or unclassified bounded diagnostic labels.
- Runs a fixed 16-phase cleanup controller: unmount; logout; node delete; target delete; backstore delete; saveconfig; pool destroy; six individually guarded loop detaches; initiator restore; owned work-root removal; runner-marker removal.
- Every potentially blocking cleanup command has timeout and kill-after bounds. Declared total cleanup budget is 191 seconds, below five minutes. Cleanup probes treat timeouts/errors as unknown, never as absence, and the first failure is retained.
- Validates a schema-versioned receipt and writes it atomically before returning the original lifecycle failure status. The workflow validation/upload steps alone use `if: always()` and missing/invalid receipts fail closed.

## Local gates

- Focused A4/A5 and A1-A3 regressions: **201 passed, 1 expected Windows skip**. The skip is the existing descriptor-relative POSIX test; A5's 34 focused asset/guard/schema/adversarial cases all passed.
- Adversarial coverage includes zero/multiple/symlink/escaped/unsafe node records; method/user/value parity mismatch; one-login ordering; secret/control/overflow diagnostics; distinct ACL/auth-method/credential/transport labels; all five top-level classifications; atomic receipt; cleanup timeout/order/incomplete/tamper; and absent-receipt workflow behavior.
- Ruff format/check: passed.
- Python compilation: passed.
- Git Bash syntax: passed using the installed Git Bash. WSL was unavailable and was not installed.
- Workflow-equivalent `jq -e`: success fixture passed, failure fixture passed, tampered login-count fixture failed as required.
- `git diff --check`: passed; executable mode `100755` confirmed.
- Local real disk, `/dev`, ZFS, LIO, iSCSI, host/VM, credential, and network-storage actions by this Builder: **0**.

## Sole automatic run

- Workflow: `Isolated storage integration`.
- Run: `33037604676`; event `push`; exact head `7b9cb87f1790b7cc94da7e3dc884989853fca8d5`.
- URL: `https://github.com/tekmyster/Hoardarr/actions/runs/33037604676`.
- Run created `2026-08-27T03:51:18Z`; terminal update `2026-08-27T03:52:40Z`; conclusion `failure`.
- Managed job: `98403699404`, `managed-zvol-lio-lifecycle`; started `2026-08-27T03:51:21Z`; completed `2026-08-27T03:52:20Z`; conclusion `failure`.
- Checkout, locked environment setup, and disposable ZFS/LIO/initiator package installation passed.
- Lifecycle step started `2026-08-27T03:51:44Z`, completed `2026-08-27T03:52:19Z`, and returned exit 19. Its observed duration was approximately 35 seconds, so A4's 25-minute cleanup hang did not recur.
- Receipt validation started and completed `2026-08-27T03:52:19Z`. The receipt existed, but `jq` could not open `dist/validation/managed-zvol-lio-lifecycle.json`: `Permission denied`; validator exit 2.
- Upload was skipped by the strict validation gate. Repository artifact metadata listed four artifacts from unrelated existing jobs and no `managed-zvol-lio-lifecycle-evidence` artifact. No artifact was downloaded.
- Builder workflow actions: implementation pushes 1; automatic runs observed 1; manual dispatch 0; retry 0; rerun 0; cancel 0; artifact download 0.

## Evidence truth table

| Gate | Result | Evidence boundary |
|---|---|---|
| A4 root/GitHub/marker/safe-root and six-loop guards | REACHED | Ordered control flow reached login |
| Six loops / one RAIDZ2 / one zvol / production initial apply | REACHED | Ordered control flow reached login |
| Exact production target readback immediately before login | PASS | Login is unreachable unless it passes |
| Exactly one safe initiator record | PASS | Login is unreachable unless parity is exact |
| CHAP method/user/value equality booleans | PASS | Login is unreachable unless all are true; values were not emitted |
| Login attempts | EXACTLY 1 | One syntactic login site plus parity-gated control flow |
| Login status | 19 | Hosted lifecycle exit and failed-step log |
| Login cause | **NOT ACCEPTED** | rc 19 alone proves no cause; receipt artifact unavailable |
| Sanitized diagnostic class | **UNKNOWN** | Receipt unreadable to validator and not uploaded |
| Cleanup controller invoked and returned boundedly | YES | Receipt existed after lifecycle returned in about 35 seconds |
| Cleanup phase/status/postcondition table | **UNKNOWN** | Receipt artifact unavailable |
| Cleanup complete/incomplete | **NOT PROVEN** | A bounded command emitted a kill indication, but exact phase/result cannot be recovered safely from retained evidence |
| Downstream by-path/I/O/idempotence/recovery/restart/remove/retention | **NOT PROVEN** | Login returned 19 before these gates |
| Managed evidence artifact | ABSENT | Upload skipped after permission-denied validation |

The run is classified as **A5 `HARNESS_ERROR` — receipt permissions defect**. It is not evidence of a product defect, an authentication cause, successful cleanup, or A4/OWNER-06 completion.

## Prohibited-action counters and safety disposition

| Action | Builder-local | Hosted A5 scope |
|---|---:|---:|
| Physical media / local disks / host or VM access | 0 | 0 |
| Manual workflow dispatch/retry/rerun/cancel | 0 | 0 |
| Login retry/reconnect/second portal | 0 | 0 |
| Multipath / A-B controller / HA / cluster work | 0 | 0 |
| Production-code edits | 0 | 0 |
| Credential/raw saveconfig/raw node-record retention | 0 | 0 |
| Artifact downloads | 0 | 0 |

## Next smallest candidate

One separately authorized A5 correction only: make the already-sanitized atomic receipt readable by the same-job non-root validator/uploader while preserving mode-0600 confinement for raw login/journal/node material, then add an exact permissions regression. The narrow candidate is a deliberate readable mode for the validated sanitized receipt (or an equivalently bounded same-job handoff), with no CHAP/login behavior, production code, topology, cleanup ordering, timeout, or diagnosis changes. A new automatic run would require fresh one-shot authority. No authentication fix or cleanup-phase inference should precede recovery of a valid retained receipt.

Stopped for Supervisor QA. No correction or successor was begun.
