# WO-WEB-013-H5 result

## Result

**FAIL — exact read-only map completed through Read D; mandatory stop at Read E.** H5 used exactly one accepted pinned authenticated session and no retry. Reads A–D passed: native/service identity was exact, all apex/www NGINX blocks were structurally classified, the TLS-serving path converged on one canonical `hoardarr.com` lineage pair, referenced and served public leaf identities matched, and the native dev-filtered output matched frozen H3 `CERTBOT4_EMPTY` byte-for-byte.

Read E returned status zero within its 32 KiB cap, but its 546-byte stdout did not satisfy the frozen H3 exact one-candidate outer-frame grammar. The sole session closed immediately. Raw NGINX, Certbot, OpenSSL, DER/PEM and private-key output was neither persisted nor hashed. No second session, retry, mutation, H6, or adjacent work occurred.

## Evidence

### Local authority and evidence

| Gate | Result | Exact evidence |
|---|---|---|
| H5 work order | PASS | 9,576 bytes / SHA-256 `0862428f1b2e7ab431e75ca40057d2fd9cd8bdbce276522e90b932acb3e05da1` |
| H3 handoff | PASS | 7,228 bytes / SHA-256 `bd7517ef77edf789fb30c3bdf17d37c40fa96541af21a858f92499d641897053` |
| H4 handoff | PASS | 6,802 bytes / SHA-256 `d6cef759001bdd3b4e465eea03db9db9cb09a4f9256996a1f3ec12feabd14756` |
| corrected infrastructure map | PASS | 26,905 bytes / SHA-256 `a44647ab82964d5052c196001f9ceeb5ae694377a6d39ff92ba20e54e01f7167` |
| H4 evidence manifest | PASS | 1,660 bytes / SHA-256 `cb421bcc6ba2c3592fd45b9bf1711a78be766383739d58d804778b29a5abcd0a` |
| frozen H3 source/copy | PASS | 4 files / 51,788 bytes / tree SHA-256 `426e52dbeefb30a00d61152e54d5f95302f2843e52330ee29e799905f37fd0db`; all four accepted per-file hashes reproduced |
| repository snapshot | PASS | Before authentication, local and `origin/rc/0.3.11-validation` HEAD were both `dd943772e2d67c35328f44f737ed9306802f6c47`; 15 tracked dirty paths and 56 normal untracked entries were preserved without attribution. Before the H5 commit, concurrent repository work advanced both HEADs to `3a5996a3e697addbd8a4d29fb9117287dc8009e8`; H5 did not modify or attribute it. |
| protected H5 root | PASS | `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-H5\WO-WEB-013-H5-20260826T221816Z`; inherited ACLs disabled; full control only for owner, Administrators and SYSTEM |
| self-excluding manifest | PASS | Fresh readback matched 6 files / 58,979 bytes; `evidence-manifest.json` is 900 bytes / SHA-256 `01016078a224262c95881442c8ae70ea81a207e58ed9c1843aea4a305540a815` |

### Single-session reads

| Read | Result | Stdout | Stderr | Combined / cap | Safe result |
|---|---|---:|---:|---:|---|
| A — native identity | PASS | 74 B | 28 B | 102 / 32,768 B | `NGINX01`; authenticated account `tekmyster`, effective read account `root`; `/usr/sbin/nginx` 1.30.4; `/usr/bin/certbot` 4.0.0; NGINX PID `4337`, start monotonic `20200000` |
| B — `nginx -T` | PASS | 269,121 B | 314 B | 269,435 / 2,097,152 B | Complete in-memory structural parse; 2 relevant HTTP-context blocks classified; raw dump discarded |
| C — canonical paths and leaves | PASS | 3,588 B | 0 B | 3,588 / 32,768 B | One canonical live/archive pair; served/reference leaf fingerprint exact; transient certificate bytes discarded |
| D — dev filtered frame | PASS | 198 B | 57 B | 255 / 32,768 B | Status 0; byte-exact frozen H3 empty frame |
| E — derived-lineage frame | **FAIL — STOP** | 546 B | 57 B | 603 / 32,768 B | Status 0; frozen H3 exact one-candidate outer frame mismatch; raw output discarded and not hashed |

Authenticated session count: **exactly 1**. Accepted ED25519 pin SHA-256 hex: `37cab91dd39592c2feaa65283389427fb150c32ca9a1cb1b79412288adcf4161`. The approved local authentication profile was consumed in memory; remote credential-file reads were zero and no secret value appeared in argv, output, evidence, hashes, Git, or this handoff.

### Production lineage map

| Source / ordinal | Exact relevant names | Listen role | Classification | Certificate pair |
|---|---|---|---|---|
| `/etc/nginx/conf.d/reverseproxy.conf` / 1 | `hoardarr.com`, `www.hoardarr.com` | `80` | HTTP-only | none |
| `/etc/nginx/conf.d/reverseproxy.conf` / 5 | `hoardarr.com`, `www.hoardarr.com` | `127.0.0.1:8443 ssl`; `127.0.0.1:9443 ssl`; `172.17.0.1:9443 ssl` | TLS-serving | `/etc/letsencrypt/live/hoardarr.com/fullchain.pem`; `/etc/letsencrypt/live/hoardarr.com/privkey.pem` |

- Related blocks: `2`; HTTP-only: `1`; TLS-serving: `1`.
- All TLS-serving blocks converge on the single live pair above.
- Resolved archive targets are `fullchain3.pem` and `privkey3.pem` under `/etc/letsencrypt/archive/hoardarr.com/`; generation `3` matches.
- Exact lineage `hoardarr.com` was derived only from the converged live fullchain path.
- Referenced and currently served public leaf are identical: SHA-256 fingerprint `5a2b6441409e9ac3f3ed928b147fab31b14c725a1723488771efdeb98aa4767d`; subject `CN=hoardarr.com`; SANs `hoardarr.com`, `www.hoardarr.com`; serial `5497efc6c756145731640e086c854124d0f`; validity `2026-07-10T20:00:24Z` through `2026-10-08T20:00:23Z`; RSA public key.
- The production Certbot candidate leaf was **not bound** because its native frame failed before label values could be accepted under H3.

### Grammar receipts

- Dev empty: 198 stdout bytes; exactly one leading LF; identical 79-character separators; exact header `Found the following matching certs:`; zero blocks; one final LF; byte-for-byte H3 match **PASS**.
- Production candidate: 546 stdout bytes; status zero; exact H3 one-candidate outer-frame match **FAIL**. Per fail-closed policy, no label value, candidate identity, path, or leaf was accepted or retained.

### Zero-mutation counters

| Counter | Value |
|---|---:|
| authenticated sessions | 1 |
| remote writes / uploads | 0 / 0 |
| backup attempts | 0 |
| remote credential-file reads / credential writes | 0 / 0 |
| certificate requests / deletes / renewals | 0 / 0 / 0 |
| DNS changes / configuration changes | 0 / 0 |
| reloads / restarts | 0 / 0 |
| browser runs / promotions | 0 / 0 |

## Defects

- Native Certbot 4.0.0 production-lineage stdout does not match frozen H3's exact one-candidate outer framing, despite status zero and bounded output. H5 intentionally does not disclose or persist the differing raw bytes and does not broaden or edit H3.
- Because Read E failed, exact agreement among the NGINX/reference/public leaf and the Certbot production candidate remains unproven, even though the NGINX/reference/public portion is exact.

## Blockers

- H5 cannot be accepted as a complete native one-candidate grammar binding. The mandatory Read E gate failed.
- No live mutation or recovery blocker exists. The unused backup and certificate-request attempts remain unused.

## Next action

Supervisor QA should reproduce the protected-root ACL, H3 copy/tree, sanitized session receipt, manifest, exact read byte counts, two-block classification, canonical lineage/leaf map, exact dev-empty match, Read E stop, session count, and zero-mutation counters. Any successor must separately authorize a local-only review of the native one-candidate framing discrepancy or a newly specified parser contract; H5 must not be retried, resumed, or used to begin H6.
