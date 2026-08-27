# WO-WEB-013-H8E result

## Result

**FAIL — the sole H8E session stopped during backup staging at `controller-PermissionError`; no backup, certificate, DNS/configuration change, reload, or activation occurred.** The exact H8D controller passed every mandatory read-only pre-write gate, including the corrected `HTTP/2 200 ` production oracle and the complete 110-case SNI matrix. It then created one bounded temporary upload tree and uploaded the exact G2/H7 files. Its next operation attempted to read the root-owned active stream configuration through the non-sudo SFTP channel and received `Permission denied`.

The session closed immediately. H8E did not open a second session, retry, guess credentials, or continue to the backup/request/configuration phases. Production apex/www and the existing internal dev HTTP stage were not changed. Trusted private `https://dev.hoardarr.com/` TLS remains inactive.

## Evidence

### Authority and exact frozen input

- Authority: ACC-079 / DEC-2026-08-26-118.
- H8E work order: 9,236 bytes / SHA-256 `4db5ad747f13ef726daa8dbcaf7f4c21c8141a32d09b174561f7ed8e86066389`.
- H8D handoff: commit `3952a99749eb8cfdc945ef7bb7e7ca92d2a920a5`; 8,488 bytes / `345fdc76b585cef816e9e35c75c7f1931f68d8f4560aeec86546e43243a99edf`.
- Exact copied H8D controller: 85,974 bytes / `a2e966648cdc8fbd24efbc8bc2e41b9cec7499708ef63a48f4ceefe59bfd98e1`.
- Frozen execution bundle source/copy: 13 files / 207,923 bytes / tree `2a19838f79a16d32ea6174ff5e198d4ee5d8c1fd2a4d979b6ee4734024bcb137`; post-run readback found zero mismatch.
- G2 remained 7 files / 53,166 bytes / tree `bb97958810c1cc3cd1785d27e42b1fcc527848e9e74d08e6ffc484cda4aa03e7`.
- H7 remained 4 files / 57,127 bytes / tree `faba67fd00baeab9cef86c26a5d4d4d5f3ea23233886c4f816859a2e3a6f0758`.
- All H8/H8A/H8B/H8C/H8D orders and handoffs, H2, the corrected infrastructure map, and H5–H7 handoffs were read completely and their recorded identities reproduced before authentication.
- Pre-authentication repository snapshot: branch `rc/0.3.11-validation`; local/upstream HEAD `6bede04469f84434a233a5b79888ea786bf667af`; 70 inherited/concurrent dirty paths preserved without attribution to H8E.

### Protected transaction and sole session

- Protected root: `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-H8E\WO-WEB-013-H8E-20260827T004803Z`.
- ACL inheritance is disabled; only `DESKTOP-6U8VLDH\dmessana`, Administrators and SYSTEM have full control.
- Transaction binding: fresh RID `WO-WEB-013-H8E-20260827T004803Z`; fresh intended remote root `/var/lib/hoardarr-dev-tls/WO-WEB-013-H8E-20260827T004803Z`; fresh intended backup child under `/var/backups/nginx/hoardarr-dev-stream-tls`; all counters zero before authentication.
- Authentication attempts/sessions: exactly `1/1`; accepted NGINX01 profile and established ED25519 pin; session closed `true`; retries and second sessions `0`.
- Credentials remained memory-only and were never printed, persisted, hashed, placed in argv, or included in evidence.

### Complete read-only pre-write gate matrix

| Gate | Result | Sanitized evidence |
|---|---:|---|
| native host/path/version/service/timer/NTP/account | PASS | NGINX01; NGINX 1.30.4; Certbot/plugin 4.0.0; timer/service active and enabled; one account |
| H5 all-block convergence | PASS | Related ordinals 1 and 5; all TLS use the canonical production fullchain/private-key pair |
| production reference/served leaf | PASS | Exact accepted apex/www leaf and canonical generation 3 |
| H7 dev-empty / production-presence | PASS | Exact 198-byte empty and 546-byte one-block native frames; production lineage/leaf match |
| all three internal DDI answers | PASS | Each returns only `dev.hoardarr.com A 192.168.0.21`; common SOA/source semantics, serial 9 |
| public privacy/challenge boundary | PASS | Zero dev A/AAAA/CNAME and challenge TXT/CNAME across both authorities and three recursors |
| Cloudflare authority/collision | PASS | Active exact-zone authority; zero candidate records; no value retained |
| active stream/dev/production identities | PASS | Exact accepted hashes and modes; 19643/19644 unused; `nginx -t` passed |
| public DDNS boundary | PASS | Exact records/helper identities; dev absent; updater inactive/disabled and untouched |
| candidate/concurrency/path collisions | PASS | Candidate/credential/fresh remote/backup paths absent; no same-scope process |
| seven internal dev HTTP routes | PASS | Exact accepted bodies/digests plus noindex/no-store headers |
| H8D production HTTP oracle | PASS | Curl `0`; exact retained `HTTP/2 200 `; 5,982 bytes; exact accepted body digest and HSTS |
| complete pre-write SNI matrix | PASS | 110/110 semantic equality; 108 expected TLS successes and 2 expected failures |

Fixed call evidence: 17 calls, all status zero and under cap. `nginx -T` was 269,300 / 2,097,152 bytes; the HTTP oracle was 1,938 / 65,536 bytes; the SNI matrix was 25,407 / 262,144 bytes. Raw NGINX, Certbot, OpenSSL, certificate/private-key, provider and HTTP response data were transient and not retained.

### Exact stop and bounded staging residue

- The controller entered `stage_and_backup`, opened the existing authenticated SFTP channel, proved the fresh temporary path absent, and created `/tmp/WO-WEB-013-H8E-20260827T004803Z-upload` plus `G2`, `H7`, `source`, and `state` subdirectories at mode `0700`.
- It successfully uploaded the exact seven G2 and four H7 files at mode `0600`: 11 files / 110,293 bytes. The `source` and `state` directories remained empty because the failure occurred before prestate construction.
- It then attempted `sftp.file('/etc/nginx/stream.d/vpn-sni-passthrough.conf', 'rb')` as the authenticated non-root SFTP account. The file is root-owned and the operation returned `PermissionError: [Errno 13] Permission denied`. The dev config read was not attempted.
- The subsequent sudo installation into the fresh root-owned remote transaction path was never reached. The temporary upload tree is outside active NGINX/configuration/certificate paths and contains only already-public, secret-free frozen helper source, but it remains as bounded unverified residue because H8E prohibits a second session or cleanup retry.
- Controller receipt: 13,715 bytes / SHA-256 `8baac5027405990627befaa3fda07286dc8528adeb3ad26596f740c5c74ffbc1`; result `FAIL`; phase `controller`; stop `controller-PermissionError`; session closed `true`; mutation state `none` in the controller's active-production sense.
- Sanitized stop reconciliation: 1,897 bytes / SHA-256 `d01f6224f8a2d0922c966a1f79e5ff298734594441937b675405003ac38fc169`.
- Instrumentation defect: the controller receipt reports `remote_writes: 0` because that counter is incremented only after both active SFTP reads. The actual bounded staging write event is **1** and is reported here; it created five directories and uploaded eleven files before the failed read.

### Unused mutation budgets and state

| Action | Count / state |
|---|---:|
| backup attempts / off-host transfers | `0 / 0` |
| dedicated credential writes | `0` |
| certificate requests | `0` |
| DNS mutations | `0` |
| active NGINX configuration mutations | `0` |
| forward reloads / restarts | `0 / 0` |
| inverse invocations / inverse reloads | `0 / 0` |
| renewal rehearsals | `0` |
| browser states | `0` |
| promotion | `0` |

No inverse was invoked: no backup or sealed inverse existed, and no certificate, DNS, active configuration, service, or production mutation occurred. Browser-control was not invoked because activation and the browser boundary were not reached. The physical-iPhone trusted load remains pending.

### Evidence integrity

- Self-excluding protected evidence manifest: 20 files / 353,347 bytes / tree `c27bdc22135099a3bd4684a92daf656a2f83353b3106c1dffe57cf9fa866bba6`.
- Manifest: 3,456 bytes / SHA-256 `585ba3e272cd80124a5fd158491b500e86cc21bef19f29b6a5925941e1e3f8c2`; fresh readback found zero missing, byte, or hash mismatches.
- A local pre-authentication `py_compile` created two self-generated `__pycache__` files before no-bytecode execution was enforced. They are preserved and included in the evidence manifest but excluded from the exact 13-file execution-bundle tree. One compiled source contains the literal Certbot INI key name from the frozen controller, but no credential value; retained JSON/evidence secret-pattern hits were zero.

## Defects

- The exact H8D controller uploads the helper bundles through non-root SFTP, then tries to read root-owned active configuration through that same non-sudo SFTP channel. The accepted account can perform password-backed sudo through command execution but cannot read those files via SFTP, so the transaction cannot reach its backup gate.
- The controller increments `remote_writes` after the failing reads rather than immediately after creating/uploading the temporary tree. Its receipt therefore undercounts this bounded staging mutation.
- The failure path does not remove the user-owned temporary upload tree. Under H8E's no-second-session/no-retry rule, that residue cannot be read back or removed in this transaction.
- Trusted dev TLS, renewal, 56-state browser QA and physical-iPhone approval remain incomplete.

## Blockers

H8E is consumed: its one authenticated session was used and closed. No retry or second session is authorized even though the backup, request, and reload budgets remain unused.

The narrow technical blocker is the controller's privilege/order seam before backup. A separately reviewed local correction must obtain both active root-owned config bytes through a fixed bounded password-backed-sudo read before any remote staging write, count staging mutations immediately, and provide an exact fail-closed cleanup/readback contract for the temporary tree. It must retain the accepted H8D status oracle, G2/H7 bytes, all preflight baselines, one-session caps, and no-secret guarantees.

## Next action

Supervisor QA should independently verify the protected ACL, exact 13-file tree, sole-session receipt, all passing pre-write gates, exact permission stop, actual-versus-recorded staging-write reconciliation, bounded temporary residue, unused backup/request/reload budgets, evidence manifest and unchanged production state. Do not resume or retry H8E, reconnect for cleanup, activate dev TLS, invoke browser QA, promote, add public dev records, touch the DDNS updater, begin H9, or start adjacent work without a new exact order.
