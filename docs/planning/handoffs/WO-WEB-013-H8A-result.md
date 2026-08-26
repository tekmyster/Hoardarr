# WO-WEB-013-H8A result

## Result

**FAIL — mandatory pre-write `production-content` gate stopped the one-session transaction; zero mutation.** H8A opened exactly one approved pinned NGINX01 session, passed the native/H5/H7/DDI/public/provider/config/listener/collision gates, then stopped during the fixed loopback origin/production HTTP read. The combined production assertion required all of: `HTTP/1.1 200 OK`, 5,982 body bytes, and SHA-256 `d92a4c5a6d6a30161239a14c235c36aeeb23beecec70ad92a9373f432dfa027d`. At least one field did not match. The controller intentionally retained no raw response and, because its sanitized HTTP object was assigned only after the combined assertion, did not retain the individual failing field. The session closed immediately; no second session or retry occurred.

One bounded unauthenticated public corroboration after the session closed returned status 200, exactly 5,982 bytes, exact accepted SHA-256 `d92a4c5a6d6a30161239a14c235c36aeeb23beecec70ad92a9373f432dfa027d`, content type `text/html`, and exact HSTS `max-age=31536000; includeSubDomains; preload`. This proves the public production body/HSTS baseline remained exact at handoff time, but it cannot safely identify which transient loopback tuple field caused the combined assertion. H8A therefore does not rebaseline or infer the cause.

No G2/H7 upload, remote staging root, backup path, credential, certificate request, DNS/config change, reload, restart, inverse, renewal, or browser action occurred. Production and internal dev state remain unchanged; trusted `https://dev.hoardarr.com/` TLS was not activated.

## Evidence

### Authority and protected inputs

- H8A order: 8,303 bytes / SHA-256 `24d0d3a8c7a2967e95540f11fd94f0eb037123202341ee5347f1265a8e7eb7df`.
- Original H8: 12,505 bytes / SHA-256 `873654784fd5239308672c3d613aa76e0a973882e57b2d89de96eaad669ac524`.
- H2: 15,594 bytes / SHA-256 `3f17c51c1e5902cc2eb9fe1c4e396dd13e524c991e2d3ace79eacf6b477944bc`.
- Corrected map: 26,905 bytes / SHA-256 `a44647ab82964d5052c196001f9ceeb5ae694377a6d39ff92ba20e54e01f7167`.
- H5/H6/H7 handoffs: 7,273 / `ac75a44403025371666e5c8706565f14c45e99d31ac47e383e2dd71deeba1dde`; 6,629 / `1b08daa1a16fde3d64a3747d16a8e8093a6cce5070d6e3617f528367cfad8790`; 9,649 / `22634595cdadfa05a4b54251314ce45124c7f4108cbcd9d4ba35c0c35fcac195`.
- Accepted G2 handoff: 9,052 bytes / SHA-256 `c861fec7355e5061f88d76c40cd8c879f790bb531e4afeccc9b6e4129b26d933`.
- Exact local G2 copy: 7 files / 53,166 bytes / tree `bb97958810c1cc3cd1785d27e42b1fcc527848e9e74d08e6ffc484cda4aa03e7`; all seven accepted per-file hashes reproduced.
- Exact local H7 copy: 4 files / 57,127 bytes / tree `faba67fd00baeab9cef86c26a5d4d4d5f3ea23233886c4f816859a2e3a6f0758`; all four accepted per-file hashes reproduced.
- `PYTHONDONTWRITEBYTECODE=1` and an external bytecode prefix were used. No generated cache exists in either copied bundle.
- Fresh protected root: `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-H8A\WO-WEB-013-H8A-20260826T233440Z`; inheritance disabled; full control only for owner, Administrators and SYSTEM.
- Pre-write plan records all budgets/counters at zero and every stop/rollback trigger before authentication.
- Pre-authentication repository snapshot: local/origin HEAD `89a9f563b4f941362b54dbf5454d6e4b702616eb`; 71 inherited dirty paths preserved without attribution. Pre-handoff local/origin HEAD advanced concurrently to `3672863eb620acfbf161536429d2ad4675bbbb9c`; 70 inherited dirty paths remained.

### Sole authenticated session

- Authenticated sessions/attempts: exactly `1/1`.
- Accepted identity: established NGINX01 profile; host `NGINX01`; effective read account root through approved password-backed sudo.
- Pinned ED25519 key SHA-256 hex: `37cab91dd39592c2feaa65283389427fb150c32ca9a1cb1b79412288adcf4161`.
- Session closed: `true`.
- Credential values remained memory-only and never entered argv, output, evidence, hashes, Git, or this handoff.

| Fixed call | Status | stdout | stderr | Cap |
|---|---:|---:|---:|---:|
| native identity | 0 | 494 | 0 | 32,768 |
| `nginx -T` | 0 | 268,968 | 314 | 2,097,152 |
| production leaves | 0 | 642 | 0 | 32,768 |
| filtered dev Certbot | 0 | 198 | 57 | 32,768 |
| filtered production Certbot | 0 | 546 | 57 | 32,768 |
| DDI primary / ddi01 / ddi02 | 0/0/0 | 591/591/591 | 0/0/0 | 32,768 each |
| five public DNS reads | all 0 | 389/387/372/372/372 | all 0 | 32,768 each |
| Cloudflare authority | 0 | 399 | 0 | 32,768 |
| active state | 0 | 1,273 | 0 | 65,536 |
| HTTP origin/production | 0 | 1,938 | 0 | 65,536 |

### Gate matrix

| Gate | Status | Safe evidence |
|---|---:|---|
| native host/path/version/service/timer/NTP/account | PASS | NGINX 1.30.4; Certbot/plugin 4.0.0; timer and NGINX active/enabled; one account |
| H5 all-block convergence | PASS | Two related blocks: ordinal 1 HTTP-only and ordinal 5 TLS-serving; one canonical production fullchain/private-key pair |
| production reference/served leaf | PASS | Exact fingerprint `5a2b6441409e9ac3f3ed928b147fab31b14c725a1723488771efdeb98aa4767d`; SANs apex/www; RSA |
| H7 dev-empty frame | PASS | Exact native empty, 198 stdout bytes |
| H7 production-presence frame | PASS | Exact one block, 546 stdout bytes, lineage/public-leaf match |
| DDI primary/secondary semantics | PASS | All three return exactly `dev.hoardarr.com A 192.168.0.21`; common SOA mname and serial 9 |
| public privacy/challenge boundary | PASS | Zero dev A/AAAA/CNAME and challenge TXT/CNAME at both authorities and three recursors |
| Cloudflare authority/collision | PASS | Active credential, one exact zone, zero candidate records; no value retained |
| active config identities | PASS | Stream `a75a5f0…173c`; dev `d937c3a1…eb82`; production `367b11bb…bb1b` |
| public DDNS invariants | PASS | Records `8dd80531…1a79`; helper `668a5bb…9e67`; dev absent; timer inactive/disabled and untouched |
| candidate/listener/path/collision/configtest | PASS | Candidate/credential paths absent; 19643/19644 unused; no same-scope transaction; `nginx -t` passed; fresh H8A remote paths absent |
| seven dev HTTP route bodies/headers | PASS by fail-order | Every per-route content/header assertion completed before the production assertion |
| production loopback HTTP combined tuple | **FAIL — STOP** | Status/bytes/hash combined assertion failed; individual field not retained; raw response discarded |
| public production corroboration | PASS | 200; 5,982 bytes; exact body SHA; exact HSTS |
| pre-write 110-case matrix | NOT RUN | Stop occurred immediately before matrix call |
| G2/H7 remote staging and readback | NOT RUN | Uploads/writes `0` |
| G2 immutable on-host backup | NOT RUN | Backup attempts `0` |
| off-host transfer/extraction | NOT RUN | Transfers `0` |
| G2 two-root inverse seal/dry validation | NOT RUN | No remote staging/backup |
| dedicated credential | NOT RUN | Credential writes `0` |
| certificate request/challenge/certificate | NOT RUN | Requests `0` |
| coupled two-file stage/commit/re-entry | NOT RUN | Config writes `0` |
| candidate/installed `nginx -t` and reload | NOT RUN | Forward reloads `0`; restarts `0` |
| PROXY/source/listener and post 110 cases | NOT RUN | No activation |
| renewal | NOT RUN | Rehearsals `0` |
| seven HTTPS routes | NOT RUN | No certificate/activation |
| 56-state browser matrix | NOT RUN | Browser states `0` |
| inverse/rollback | NOT REQUIRED | Mutation state `none`; inverse invocations/reloads `0/0` |

Exact counters: authentication attempts `1`; authenticated sessions `1`; remote writes/uploads `0/0`; backup attempts `0`; off-host transfers `0`; production certificate requests `0`; renewal rehearsals `0`; forward reloads `0`; inverse invocations/reloads `0/0`; restarts `0`; browser states `0`; retries `0`.

Transaction receipt: 10,753 bytes / SHA-256 `cdc399e52d6e19f662ed9a62562663bb541833deae89f0debf9c9e5d4f77f6f4`. Public corroboration receipt: 243 bytes / SHA-256 `f05584dc497e38d04d00e3447ef08946adf29ef30258ab1fe94ba9c72096ea49`. Private self-excluding evidence manifest records 17 files / 410,548 bytes / tree `e832dae3fe28c858387fe1b7c0553402e37efe705e7fec6e58aa0aa09758ff89`; the 2,547-byte manifest SHA-256 is `bce41d7117caf9c395ba0ae9c0288805ea81ad17c72ae8b4fa322306b35928da`.

## Defects

- The controller's production tuple assertion conflates protocol/status-line, byte count and body hash into one stop label and persisted its sanitized HTTP object only after that assertion. Consequently the exact differing loopback field is unavailable even though the raw call completed status zero within cap. This is an evidence/controller defect and must not be silently rebaselined.
- The separate public read proves production body/HSTS identity but is not equivalent to the loopback SNI path and cannot override the failed gate.
- Trusted dev TLS, renewal and browser QA remain incomplete because the correct pre-write stop occurred first.

## Blockers

H8A is consumed: its single session was used and no second session/retry is authorized. The backup and certificate-request budgets remain unused, but cannot be used under H8A.

This is not an authentication, credential, permission, DNS, certificate-authority, G2, H7, public-exposure, or production-body blocker. The narrow blocker is exact safe classification of the loopback production HTTP tuple under a field-preserving assertion.

## Next action

Supervisor QA should accept the zero-mutation stop and independently review the protected receipt/ACL/manifest. Any successor should first correct the controller locally so it records the safe status protocol/version, byte count and body hash before separately asserting each field, while retaining raw-response nonpersistence. A new bounded live order may then perform one fresh read-only binding and, only if every original gate passes, consume the still-unused backup/request/reload transaction. Do not retry H8A, promote, add public dev records, touch the DDNS updater, change production, begin H9, or perform adjacent website/appliance/VM work.
