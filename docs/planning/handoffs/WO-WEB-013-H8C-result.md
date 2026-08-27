# WO-WEB-013-H8C result

## Result

**FAIL — the exact field-preserving production HTTP oracle stopped the sole session before every write at `production-http-status-format`; zero mutation.** The retained sanctioned production tuple is:

- remote curl exit status: `0`;
- protocol/status field: 11 printable ASCII bytes, escaped as `HTTP/2 200\u0020`;
- body length: `5,982` bytes;
- body SHA-256: `d92a4c5a6d6a30161239a14c235c36aeeb23beecec70ad92a9373f432dfa027d`;
- HSTS: `strict-transport-security: max-age=31536000; includeSubDomains; preload`.

The exact H8B parser accepts only the fixed `HTTP/<digit>.<digit> <three digits> ...` line grammar before comparing the accepted `HTTP/1.1 200 OK` baseline. The observed `HTTP/2 200 ` field therefore failed format validation. H8C does not rebaseline, infer equivalence, waive the mismatch, or retry.

Exactly one approved pinned NGINX01 session was opened and closed. No remote write/upload, backup attempt, off-host transfer, credential creation, certificate request, DNS/configuration change, reload, restart, inverse, renewal or browser state occurred. Production and the existing private HTTP dev stage remain unchanged; trusted `https://dev.hoardarr.com/` TLS remains inactive.

## Evidence

### Authority and exact local binding

- H8C order: 8,216 bytes / SHA-256 `1b61389f9746c43e6f0adf6aabbabd401f71254bf513088e3ef00a93808b7fae`.
- Accepted H8B handoff: commit `5d5a449e89fc4018fc58471790b4c1364cb31f80`; 8,853 bytes / SHA-256 `53460e8029a052a2883de06657ce1d2ef84e724b9cb7a189c6077404d546979d`.
- Exact H8B controller source/copy: 85,122 bytes / SHA-256 `e193b4ba2616cb6ad881c0a6c43bdb03e5915905af88dfc2b85f4a2e20a8582a`.
- Exact execution bundle source/copy: 13 files / 203,319 bytes / tree `0f9a1e7dfaf82a788bcf3906e641b6f56c4077bad966fcd32fc60bafe022bd37`; zero source/copy/post-run mismatch.
- Exact G2: 7 files / 53,166 bytes / tree `bb97958810c1cc3cd1785d27e42b1fcc527848e9e74d08e6ffc484cda4aa03e7`.
- Exact H7: 4 files / 57,127 bytes / tree `faba67fd00baeab9cef86c26a5d4d4d5f3ea23233886c4f816859a2e3a6f0758`.
- Fresh protected root: `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-H8C\WO-WEB-013-H8C-20260827T001605Z`. ACL inheritance is disabled; only `DESKTOP-6U8VLDH\dmessana`, Administrators and SYSTEM have full control.
- `PYTHONDONTWRITEBYTECODE=1` was used; no generated cache exists in the frozen bundle.
- Pre-write plan: 3,192 bytes / SHA-256 `2b8d22d54a8a051f98b33201844d8375bef10826a531978c28e677521f5b57bd`; all counters and one-shot limits were durably recorded before authentication.
- The exact frozen controller contains its predecessor H8B RID/path constants. A separate 3,152-byte transaction-instance binder, SHA-256 `2173551c1bc8213f0ed033c78faf4a6f52d90efb392f13255c7c30c694d48f67`, verified the controller hash and rebound only the fresh local root, receipt, RID, remote sibling, backup sibling and exact copied G2/H7 paths in memory. It changed no controller/G2/H7 byte or baseline. Its pre-authentication check proved the fresh receipt absent and all counters zero.
- Pre-authentication repository snapshot: branch `rc/0.3.11-validation`; local/upstream HEAD `9b60038b593d9ff64e94794c29185e7336a8f931`; 70 inherited/concurrent dirty paths preserved without attribution.

### Sole session and bounded calls

- Authentication attempts/sessions: exactly `1/1`; session closed `true`.
- Accepted host/account path: established NGINX01 profile with approved password-backed sudo; credential material remained memory-only.
- Pinned ED25519 host-key SHA-256 hex: `37cab91dd39592c2feaa65283389427fb150c32ca9a1cb1b79412288adcf4161`.
- Controller runtime was approximately 177 seconds. No second session or out-of-band live probe was opened.

| Fixed call | Status | stdout | stderr | Cap |
|---|---:|---:|---:|---:|
| native identity | 0 | 494 | 0 | 32,768 |
| `nginx -T` | 0 | 268,986 | 314 | 2,097,152 |
| production leaves | 0 | 642 | 0 | 32,768 |
| filtered dev Certbot | 0 | 198 | 57 | 32,768 |
| filtered production Certbot | 0 | 546 | 57 | 32,768 |
| DDI primary / ddi01 / ddi02 | 0/0/0 | 591/591/591 | 0/0/0 | 32,768 each |
| five public DNS reads | all 0 | 389/387/372/372/372 | all 0 | 32,768 each |
| Cloudflare authority | 0 | 399 | 0 | 32,768 |
| active state | 0 | 1,273 | 0 | 65,536 |
| HTTP origin/production | 0 | 1,938 | 0 | 65,536 |

Raw NGINX, Certbot, OpenSSL, PEM/private-key, provider and HTTP response data were transient. The retained evidence contains only the controller's bounded safe structures and call byte/status metadata.

### Pre-write gate matrix

| Gate | Status | Sanitized evidence |
|---|---:|---|
| native host/path/version/service/timer/NTP/account | PASS | NGINX01; NGINX 1.30.4; Certbot/plugin 4.0.0; one account; active/enabled timer and service |
| all-block H5 convergence | PASS | Related ordinals 1 and 5; HTTP-only plus TLS-serving; all TLS references converge on the canonical production pair |
| production reference/served leaf | PASS | Exact accepted fingerprint, apex/www SAN set, canonical generation 3 and metadata |
| H7 dev-empty | PASS | Exact native empty; 198 stdout bytes |
| H7 production-presence | PASS | Exact native one-block frame; 546 stdout bytes; lineage/leaf match |
| three direct DDI answers | PASS | Each returns only `dev.hoardarr.com A 192.168.0.21`; common authoritative SOA semantics and serial 9 |
| public privacy/challenge boundary | PASS | Zero dev A/AAAA/CNAME and zero challenge TXT/CNAME across both authorities and three recursors |
| Cloudflare authority/collision | PASS | Active exact-zone authority; zero candidate records; no credential value retained |
| active stream/dev/production config identities | PASS | Exact accepted hashes `a75a5f0…173c`, `d937c3a1…eb82`, `367b11bb…bb1b` |
| public DDNS boundary | PASS | Exact accepted records/helper hashes; dev absent; updater timer inactive/disabled and untouched |
| candidate/listener/config/concurrency gates | PASS | Candidate and credential paths absent; 19643/19644 unused; `nginx -t` passed; no same-scope process; fresh remote/backup siblings absent |
| seven dev HTTP route bodies/headers | PASS | All seven exact body length/digest tuples plus noindex/no-store headers reproduced |
| H8B production tuple persistence | PASS | Complete sanctioned tuple saved before assertions |
| production curl status | PASS | `0` |
| production protocol/status grammar | **FAIL — STOP** | Observed `HTTP/2 200\u0020`; exact stop `production-http-status-format` |
| production body length/digest/HSTS comparisons | NOT REACHED | Safe observed values are retained and equal accepted values, but fail ordering stopped before comparisons |
| pre-write 110-case matrix | NOT RUN | Fixed order places it immediately after the HTTP oracle |
| remote G2/H7 upload/readback | NOT RUN | Writes/uploads `0` |
| immutable on-host/off-host backup | NOT RUN | Attempts/transfers `0/0` |
| G2 two-parent inverse seal | NOT RUN | No staging/backup |
| credential/request/certificate/challenge | NOT RUN | Writes/requests `0/0` |
| coupled config install/reload | NOT RUN | Config writes/reloads/restarts `0/0/0` |
| post-activation PROXY/DNS/110/production/renewal/routes | NOT RUN | No activation |
| browser-control 56-state gate | NOT RUN | Browser states `0`; browser skill was not invoked because the post-activation boundary was not reached |
| inverse/rollback | NOT REQUIRED | Mutation state `none`; inverse invocations/reloads `0/0` |

### Receipt, evidence and counters

- Transaction receipt: 13,335 bytes / SHA-256 `b0c56ee6b7571a95e3040d1356d7d2b2922d30313d070b8033b75800ebfb9717`; result `FAIL`; phase `preflight`; stop gate `production-http-status-format`; session closed `true`; mutation state `none`.
- Self-excluding evidence manifest: 17 files / 225,622 bytes / tree `ff0723b76b7c54ec840a655986d86eb3db9c2861f654ca4ce2639a89f33ef2fc`. The 2,934-byte manifest SHA-256 is `42d406be17327776c726d7d35c164a28f57318f1e6d62df6de6cd9dc789897b9`; fresh per-file readback found zero mismatch.
- Retained-evidence secret scan found no PEM/private-key block, literal Bearer credential or literal Cloudflare token assignment.
- Exact counters: authentication attempts `1`; authenticated sessions `1`; remote writes `0`; backup attempts `0`; off-host transfers `0`; production certificate requests `0`; renewal rehearsals `0`; forward reloads `0`; inverse invocations/reloads `0/0`; restarts `0`; browser states `0`; retries `0`.

## Defects

- The exact accepted H8B production status-line grammar accepts an HTTP version only in dotted form (`HTTP/x.y`). The current fixed TLS curl evidence emits the bounded field `HTTP/2 200 `, so H8B stops at format validation even though curl status, body length, body digest and HSTS in the same sanctioned tuple reproduce their accepted values.
- H8C cannot determine under this order whether a later controller should pin curl to HTTP/1.1, accept a precisely specified HTTP/2 curl header frame, or change another part of the oracle. Any such choice changes the accepted controller/baseline and requires separate local certification; H8C does not make it.
- Trusted private dev TLS, renewal and browser QA remain incomplete because the mandatory pre-write stop occurred first. Physical-iPhone trusted verification remains pending.

## Blockers

H8C is consumed: its one authenticated session was used and no retry or second session is authorized. Backup, request and reload budgets remain unused but cannot be used under H8C.

This is not an access, credential, DNS, provider-authority, G2, H7, content-body, HSTS or production-mutation blocker. The narrow blocker is the exact accepted production protocol/status grammar versus the observed sanitized `HTTP/2 200\u0020` field.

## Next action

Supervisor QA should verify the protected ACL, exact frozen bundle, transaction-instance binding, receipt-before-assertion tuple, stop code, sole-session closure, call counts/caps, evidence manifest and all zero-mutation counters. Any successor must be separately ordered and locally certify an exact protocol/status oracle decision before another live attempt. Do not retry H8C, rebaseline this field, promote dev, add public dev records, touch the public DDNS updater, change production, begin H9 or start adjacent work.
