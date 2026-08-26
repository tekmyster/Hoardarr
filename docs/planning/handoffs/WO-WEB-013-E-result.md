# WO-WEB-013-E result

## Result

WO-WEB-013-E is **PASS — ONE EXPOSED CREDENTIAL ROTATED / KEEPass RECONCILED / SITE UNCHANGED**.

The exposed credential was uniquely bound to the SOCFortress application Fernet key named `TOTP_ENCRYPTION_KEY`, logical replacement identifier `socfortress-totp-fernet-20260826T201231Z`. Its exact live consumer is `/opt/socfortress-waf/.env` on NGINX01; the value is injected into the `admin-api` and `postgres` Compose services. The authoritative KeePass entry remained the single entry at UUID `{5f088dab-c358-1a4b-a382-3a1dbedd5780}`.

The supervisor-added ciphertext lifecycle gate passed before mutation. The live database contains one user row and zero populated `totp_secret_encrypted` values. Four retained SQL-gzip backups contain four total user rows and zero protected TOTP values. No retained ciphertext required rewrap/re-encryption, and no historical object remains decryptable only by the exposed key.

Exactly one provider-supported Fernet replacement was generated. The root-owned `0600` consumer was staged and atomically replaced, both exact Compose consumers were recreated on unchanged images, application-native new-key encryption/decryption passed, and synthetic old-key ciphertext was rejected. The same KeePass UUID was atomically edited and reopened, with rotation metadata read back. The exposed value was removed from the redundant local NGINX credential mapping and all live/process consumers; the root-only rollback copy was then removed. No certificate, DNS, NGINX/Apache configuration, website content, route, or production/dev service was changed.

| Required gate | Result | Evidence |
|---|---|---|
| order/predecessor authority | **PASS** | Work order 6,077 bytes / SHA-256 `61149bc641c4462f115806b8d74a55153c766b7dd274216b7cb3e4763e371cab`; predecessor commit `534d2c7099c299aa03c083d0f521d00aa9bbead2`, handoff SHA-256 `a273a548afc263fe57070f42c960ee48dde2b596ef470eddb3f6b78cc9e8b9da`. |
| unique identity binding | **PASS** | One local source record, one exact remote consumer key, one KeePass path/UUID match, and one current-value match; no ambiguity or peer credential was touched. |
| secret-redacted preflight | **PASS** | Exact host, consumer owner/mode, two process consumers, images, native crypto capability and health were captured without emitting credential material. |
| retained-ciphertext lifecycle | **PASS** | Live protected objects `0`; retained backups `4`; retained protected objects `0`; rewrap required `false`. |
| least-privilege replacement | **PASS** | One Fernet key generated for the same exact application function and host scope; no account, provider role, zone, MFA, recovery or peer credential change. |
| staged consumer update | **PASS** | Root-only stage and rollback paths; `/opt/socfortress-waf/.env` atomically replaced and retained as root:root `0600`; rollback path absent after acceptance. |
| new validation | **PASS** | Both exact services are running/healthy on unchanged image IDs; `/health` and `/healthz` return 200; application-native replacement-key round trip passed. |
| KeePass UUID readback | **PASS** | Same UUID reopened; current value matches replacement and not old; non-secret rotation metadata read back; zero duplicate active entries. KeePass native history and a verified encrypted database backup were preserved. |
| old revocation/rejection | **PASS** | This is local Fernet material with no provider deletion API. Old value count is zero in the live file and both process environments; synthetic old-key ciphertext is rejected; retained old-decryptable ciphertext count is zero. |
| zero secret leakage | **PASS** | Exact in-memory comparison found zero old/new values in 10,410 repository worktree files, 17 final private-evidence files at scan time, and the exact local credential source. Remote adjacent files have zero old values; the new value appears only once in the root-only consumer and once in each expected process environment. Encrypted KeePass files contain no literal plaintext. |
| production/dev invariants | **PASS** | Pre/post invariant documents compare exactly after excluding observation UTC. NGINX/Apache file hashes, PIDs/start identities, ports, trees, public content/TLS/HSTS, and internal dev controls are unchanged. |

## Evidence

### Identity, lifecycle and rotation

- Checkout branch `rc/0.3.11-validation`; pre-handoff shared-checkout HEAD `ab615d3e4ce5b2671d7edfd201f9240eee0af769`. Concurrent App Builder and Supervisor worktree changes were preserved and are not attributed to this order.
- Exact credential: application Fernet encryption key, non-secret identifier `socfortress-totp-fernet-20260826T201231Z`, scope `SOCFortress admin-api TOTP secrets on NGINX01`.
- Consumer: `/opt/socfortress-waf/.env`, root:root `0600`, exactly one key record. Compose config has two `.env` consumers. No compose file contains the value or a direct key-value declaration.
- Before: `admin-api` image `sha256:7c4dbc127f8407b7e0476e03cdc278f5c6a24327f95ea69456e327df6479b4d9`; `postgres` image `sha256:01100f48660a46a6c3fffb71f672da75347bb12f7b264dee381cf0e985d9e63c`; both running/healthy and each held one old-key environment entry.
- After: `admin-api` container `9605b9dee75de22390444014048ac07f0467eaf57a9015489aa80d6cb0b8200e`; `postgres` container `a13cbed6d024d3f1c7c6917ef687a71ebc0e08e583f150079a3f382176d3c1da`; unchanged image IDs, running/healthy, one replacement entry and zero old entries each.
- Crypto proof used only synthetic in-memory plaintext/ciphertext. New-key round trip passed; old-key ciphertext rejection passed. No key, key hash, ciphertext or ciphertext-derived verifier was emitted or persisted.
- Lifecycle inventory: live `public.users` row count `1`, populated `totp_secret_encrypted` count `0`; four exact retained SQL-gzip backups, one row each, protected count `0` each. The application has encrypt/decrypt support but no supported MultiFernet/rotate/rewrap/re-encrypt path; because the bounded protected-object count is zero, no migration was necessary or attempted.
- KeePass entry: `All Servers/NGINX/SOCFortress/SOCFortress TOTP Encryption Key`, UUID `{5f088dab-c358-1a4b-a382-3a1dbedd5780}`, username `TOTP_ENCRYPTION_KEY`, URL `https://192.168.0.21:18444/`. The UUID/path remain unique and accessible.
- Verified encrypted KeePass backup: `C:\Users\dmessana\Desktop\all servers\All_Servers.kdbx.WO-WEB-013-E-20260826T201231Z.bak`, 84,037 bytes, SHA-256 `7bdc1f8d275505c5eb215905fff3a916f47b15afc205baa8c2207e943eb6354c`; byte-identical readback passed before the edit.

### Hoardarr invariants

- Edge files remained exact: stream `a75a5f0d5800bd60f86cb803e320a0843c276202734de40fced0591f2c13173c`; internal-dev NGINX `d937c3a13a0570dade04f5ee1140c8663e3a6ed2e65c3505879358e60f02eb82`; production NGINX `367b11bb6182f2cc356efa0ac4e8e49049b3bb1a740c869e309f399c26e0bb1b`. NGINX stayed PID `4337`, active-enter monotonic `20208693`.
- Origin files remained exact: production Apache vhost `0087dab83bece62ac78a88ec9a004ca90075c4481cecf94d9b31d66517f749f4`; internal-dev Apache vhost `d033e1feedcdbd2ff5c20155ab0194d937306c4f0520b4f5d077ee53c1cb6bb4`. Apache stayed PID `1447135`, active-enter monotonic `1254497584148`.
- Production and internal-dev trees remain 13 files / 50,603 bytes / tree SHA-256 `79f4eaf953edb2ba119877b577daf4f342cb6c9f75b9a15bc49473b7e2e6008b`.
- Public apex remains HTTP 200 / 5,982 bytes / body SHA-256 `d92a4c5a6d6a30161239a14c235c36aeeb23beecec70ad92a9373f432dfa027d`; leaf SHA-256 `5a2b6441409e9ac3f3ed928b147fab31b14c725a1723488771efdeb98aa4767d`; HSTS remains `max-age=31536000; includeSubDomains; preload`.
- Internal dev HTTP remains private-source denied from the edge host with 403, `X-Robots-Tag: noindex, nofollow, noarchive`, and `Cache-Control: no-store`. Ports 19643/19644 and all dev certificate/renewal/temporary credential paths remain absent.

### Private evidence

ACL-restricted evidence root:

`C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-E\WO-WEB-013-E-20260826T195901Z`

The directory grants full control only to the owner, local Administrators and SYSTEM. Its final manifest records 19 helper/evidence files / 98,399 bytes; `evidence-manifest.json` is 3,312 bytes with SHA-256 `8d3cfe2541ed91e683aa31658e8860b41511031eac46a878c077ed6ec98fc921`.

Key evidence identities:

- `identity-preflight.json`: 2,205 bytes, SHA-256 `f36c1d6af43032fe3c1774ee628d7381e8b4ad238ef7374d6288aba3bab3f247`.
- `lifecycle-preflight.json`: 3,418 bytes, SHA-256 `e98a698aa5862774cbb484d97c243ab39fa4fe1c8dc563b4b1af05c53be37b07`.
- `backup-ciphertext-inventory.json`: 1,341 bytes, SHA-256 `1d4bd4f571ffdc7e258286e07f2f32c77333fdcd42583ce82d9401335d047b8f`.
- `consumer-structure.json`: 931 bytes, SHA-256 `7a15326ed69cd524373d9baf7e178dbc861c299df7fea6f9faee3ced01304540`.
- `runtime-preflight.json`: 1,790 bytes, SHA-256 `486dc5da15b7097003d919791729f0daf616a2490042d7ef07c7b6b59c0b21ba`.
- `rotation-result.json`: 3,723 bytes, SHA-256 `74a0d514fbf8c0c408246898c532dfa8944c5881745f57501dc5aed018965d53`.
- `secret-scan.json`: 2,927 bytes, SHA-256 `c1c3f48673d0172dfcae6d09d8a848d134603db61ceebb75a807fdd839f08ff0`.
- `site-invariants-pre.json`: 2,790 bytes, SHA-256 `39481e2339caf952c8e8d45ea77d740a8929b0f0b4fd089d830ceabc76e97cb2`.
- `site-invariants-post.json`: 2,790 bytes, SHA-256 `95ed6aca2c478e5a7fde9e70205728758f5d8f58948255222a43f7c52be08b3f`. Its observation timestamp differs, while the complete invariant payload is exact.

## Defects

- No retained protected TOTP ciphertext exists, so a rewrap/sample-restore migration was neither required nor performed. This result must not be generalized to a future rotation after encrypted TOTP secrets are populated; that future lifecycle would require a supported migration path before revocation.
- Two initial local leakage-scan executions timed out because their file traversal included Git object storage and then loaded the 10,410-file worktree serially. Neither attempt changed any system or emitted a secret. The corrected mandated pass used fixed-string patterns over stdin with path-only output, excluded `.git`, covered tracked/untracked worktree files, and passed.
- `postgres` receives the same key through the existing shared Compose environment even though application use was proven only in `admin-api`. Removing that redundant injection would be a separate application/Compose design change and was not authorized here.

## Blockers

No blocker remains for Supervisor acceptance of WO-WEB-013-E. The one exposed credential is no longer present in plaintext sources or live consumers, the replacement is recoverable through the authoritative KeePass UUID, and old-key rejection is proven without stranded ciphertext.

Trusted TLS for internal `dev.hoardarr.com` remains rejected from the predecessor workstream. This credential-containment result does not authorize or complete certificate issuance, DNS mutation, stream activation, renewal, browser QA, or promotion.

## Next action

Supervisor QA of this credential-containment handoff. Any trusted-dev-TLS continuation requires a separately dispatched bounded order; do not resume certificate issuance or start adjacent website work from this result.
