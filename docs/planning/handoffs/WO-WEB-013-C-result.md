# WO-WEB-013-C result

## Result

WO-WEB-013-C is **DNS-01 PRESENCE GATE FAILED / ROLLED BACK**. The complete active stream map and listener estate were inventoried, a source-safe original-client design was bounded, unused loopback ports were proven, all 109 explicit SNI names plus unknown/default behavior were captured, Cloudflare authority was revalidated, and immutable on-host/off-host backups with one exact inverse passed.

The one authorized Certbot issuance returned zero after approximately 5.4 seconds, but a persisted observer sampled both Cloudflare authoritative nameservers six times while Certbot was active and saw TXT count zero every time. The requested 45-second propagation interval was therefore never entered. The most likely explanation is reuse of the still-valid ACME DNS authorization from WO-WEB-013-B; this is an inference from the short completion and zero authoritative observations, not a claim from secret or TXT-value inspection. Because this order requires persisted authoritative TXT presence/count before validation, the gate failed even though Certbot returned success.

The exact inverse ran once immediately. It removed only the transient dev Certbot lineage and dedicated renewal credential, restored both accepted NGINX files byte-for-byte, confirmed challenge cleanup, validated NGINX, and reloaded without changing the main process identity. No loopback TLS listener or stream route was activated. Renewal, HTTPS route acceptance, browser QA and promotion were not started.

| Required gate | Result | Evidence |
|---|---|---|
| source-safe stream design | **PASS — design/inventory only** | Existing 110-entry map remains intact. Proposed isolated decision uses original stream `$remote_addr`, permits only loopback/RFC1918 dev SNI to `127.0.0.1:19643`, sends all other dev SNI to unused `127.0.0.1:19644` so the connection closes without a certificate/body, and delegates every non-dev SNI unchanged to the accepted map. It was not activated after the DNS gate failed. |
| loopback port selection | **PASS** | Ports 19643 and 19644 had no listener, no effective-config reference and each passed a loopback bind test. Final readback confirms neither is listening. |
| credential/authority and collision | **PASS** | Proven updater token remains active, distinct from the rejected credential, sees exactly one active `hoardarr.com` zone and advertises `#dns_records:edit`; public dev A/AAAA/CNAME and challenge TXT/CNAME were absent before issuance. |
| backups/inverse | **PASS** | Immutable affected-config/DNS/Certbot backup and byte-identical off-host readback completed before the first write. The exact inverse executed once with return code zero. |
| DNS-01 authoritative presence | **FAIL** | Six persisted in-flight samples from 0.1 through 5.4 seconds recorded TXT count 0 at both `dahlia.ns.cloudflare.com` and `greg.ns.cloudflare.com`; Certbot had exited by the final sample. No TXT value was read or persisted. |
| DNS-01 cleanup | **PASS** | Cloudflare API, both authorities, `1.1.1.1`, `8.8.8.8` and `9.9.9.9` all return no challenge TXT/CNAME after inverse; public dev A/AAAA/CNAME remain absent. |
| exact certificate | **NOT ACCEPTED / REMOVED** | Certbot returned zero for an exact ECDSA request containing only `dev.hoardarr.com`, but the mandatory live-challenge evidence failed and inverse removed live/archive/renewal state before certificate acceptance. |
| loopback backend and stream route | **NOT STARTED** | No staged or active change was made to either NGINX file. Ports 19643/19644 remain absent. |
| SNI/source matrix | **BASELINE PASS / POSTCHANGE NOT STARTED** | Private evidence records all explicit/default pre-change behavior. There is no post-change route because the earlier DNS gate stopped execution. |
| unrelated mappings/default | **PASS / UNCHANGED** | Active stream file remains exact SHA-256 `a75a5f0d5800bd60f86cb803e320a0843c276202734de40fced0591f2c13173c`; all 110 entries/default/backends are byte-restored. |
| renewal rehearsal | **NOT STARTED** | The authoritative presence gate failed before listener activation or renewal configuration. |
| routes/controls | **HTTP UNCHANGED / HTTPS NOT STARTED** | Accepted internal HTTP staging remains active with noindex/no-store. Trusted dev HTTPS was not activated. |
| 56 browser states | **NOT STARTED** | No trusted dev HTTPS endpoint existed after the mandatory inverse. No browser bypass or certificate exception was attempted. |
| owner artifact | **NOT CREATED** | `https://dev.hoardarr.com/` was not presented as approved. |
| production invariants | **PASS** | Production DNS/content/config/certificate/HSTS and NGINX/Apache identities match the locked baseline after inverse. |

## Evidence

The authoritative work order was read completely and matched required SHA-256 `cfca224a9dd315d28b67d26adbc170028c83a9bc2d3ca1089ecc6a4c941ef7ea`. WO-WEB-012/013/013-B, their handoffs, the complete target architecture and accepted endpoint/credential register were read before execution. The checkout was `rc/0.3.11-validation` at `99ad3ff86962a6c8924ae43063a9242d55e0148a` before this handoff; unrelated concurrent worktree changes were preserved.

Stream and listener baseline:

- Active stream file: `/etc/nginx/stream.d/vpn-sni-passthrough.conf`, 6,419 bytes, SHA-256 `a75a5f0d5800bd60f86cb803e320a0843c276202734de40fced0591f2c13173c`.
- The accepted map contains 110 entries: 109 explicit names plus default. Apex/www remain mapped to `127.0.0.1:18443`; default remains `127.0.0.1:17443`; public IPv4/IPv6 port 443 retains PROXY-protocol forwarding and `ssl_preread`.
- Chosen TLS backend: `127.0.0.1:19643`. Explicit reject target: `127.0.0.1:19644`, with no listener by design. Both ports were absent from `ss`, absent from `nginx -T`, passed bind probes, and remain absent after inverse.
- HTTP real-IP and stream SSL preread capabilities are present. A future approved backend would have to accept the existing stream PROXY header on the loopback listener while the decisive allow/reject remains in stream context using original `$remote_addr`; backend loopback identity alone was not treated as authorization.
- Private pre-change matrix: `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-C\WO-WEB-013-C-20260826T191555Z\pre-sni-matrix.json`, 30,582 bytes, SHA-256 `fcd64853ea05900e69db86fbef33b77948b2b6519804e0bd027048b044c7755a`. It contains 110 probes, 108 successful TLS observations and two expected non-TLS/failed observations, with mapped backend, leaf fingerprint, TLS class and response class but no secret values.

Authority/preconditions:

- Cloudflare token verification: HTTP 200, active; exact active zone count 1; permissions include `#dns_records:edit`, `#dns_records:read` and `#zone:read`; token differs byte-for-byte from the rejected credential. No value/hash was emitted.
- Certbot `4.0.0`; `python3-certbot-dns-cloudflare 4.0.0-1`; two existing ACME account registrations; `certbot.timer` active/enabled.
- Edge clock was NTP-synchronized. CAA count was zero through Cloudflare and Google DNS-over-HTTPS. Public dev A/AAAA/CNAME and challenge TXT/CNAME were absent at provider, authorities and public recursors before mutation.
- Accepted dev HTTP NGINX file SHA-256 `d937c3a13a0570dade04f5ee1140c8663e3a6ed2e65c3505879358e60f02eb82`; production NGINX file SHA-256 `367b11bb6182f2cc356efa0ac4e8e49049b3bb1a740c869e309f399c26e0bb1b`; `nginx -t` passed; NGINX main PID `4337`, active-enter monotonic `20208693`.

Backup/inverse:

- On-host root: `/var/backups/nginx/hoardarr-dev-stream-tls/WO-WEB-013-C-20260826T191555Z`.
- Immutable archive: `/var/backups/nginx/hoardarr-dev-stream-tls/WO-WEB-013-C-20260826T191555Z.tar.gz`, 4,249 bytes, SHA-256 `ec8907681d65b8211264230d5d3b49fe3672ba57e98b46feff214257440e22e8`; archive, directory and contents accepted `chattr +i`.
- Off-host readback: `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-C\WO-WEB-013-C-20260826T191555Z\WO-WEB-013-C-20260826T191555Z.tar.gz`, exact same bytes and SHA-256.
- Backup contains the exact accepted stream/dev files, non-secret DNS/Certbot/listener state, manifest, a dev-only inverse and an exact challenge cleanup helper. It contains no credential material.
- Inverse restores only the two accepted dev/stream files, validates and reloads NGINX, deletes only a `dev.hoardarr.com` Certbot lineage if present, removes only the dedicated dev credential, and deletes only exact `_acme-challenge.dev.hoardarr.com` TXT records after confirming the zone/name/type. It cannot touch production certificate/config or unrelated records.

DNS observer and stop:

- Observer: `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-C\WO-WEB-013-C-20260826T191555Z\dns01-observer.json`, 3,082 bytes, SHA-256 `df656e1b227bc0e3ec42f0e6db1a3e3f8efb934e482dc826b25b1a40197b6137`.
- It explicitly records `values_persisted=false`. Six issuance samples observed counts only at both authorities; maximum count was zero at each. The cleanup sample recorded zero at both authorities and all three public recursors.
- The one exact Certbot command used the installed DNS Cloudflare plugin, production Let's Encrypt directory, ECDSA, only `-d dev.hoardarr.com`, the dedicated root-only credential and a 45-second propagation setting. Its return code was zero, but the authoritative-presence acceptance predicate failed. No Certbot stdout/stderr or credential was copied into evidence.
- Exact inverse return code: 0. No second issuance was attempted.

Final invariant readback:

- Stream SHA-256 `a75a5f0d5800bd60f86cb803e320a0843c276202734de40fced0591f2c13173c`; dev HTTP SHA-256 `d937c3a13a0570dade04f5ee1140c8663e3a6ed2e65c3505879358e60f02eb82`; production NGINX SHA-256 `367b11bb6182f2cc356efa0ac4e8e49049b3bb1a740c869e309f399c26e0bb1b`.
- Dev live/archive/renewal paths and `/root/.secrets/certbot/cloudflare-hoardarr-ddns.ini` are absent. Ports 19643/19644 have no listener. `nginx -t` passes; NGINX main PID/start identity is unchanged.
- Both Cloudflare authorities, `1.1.1.1`, `8.8.8.8`, `9.9.9.9`, and provider API report no challenge record and no public dev A/AAAA/CNAME.
- Production certificate SHA-256 `5a2b6441409e9ac3f3ed928b147fab31b14c725a1723488771efdeb98aa4767d`, SANs only apex/www. Public apex returns HTTP 200, 5,982 bytes and exact HSTS `max-age=31536000; includeSubDomains; preload`.
- Production/dev origin trees remain 13 files / 50,603 bytes / tree `79f4eaf953edb2ba119877b577daf4f342cb6c9f75b9a15bc49473b7e2e6008b`; preserved production inverse remains 13 files / 51,764 bytes / tree `2d1cdf160842c52c13e46aba595fb808bdfd82fe74cda2a22278d6459a150d92`.
- Production Apache vhost SHA-256 `0087dab83bece62ac78a88ec9a004ca90075c4481cecf94d9b31d66517f749f4`; dev Apache vhost SHA-256 `d033e1feedcdbd2ff5c20155ab0194d937306c4f0520b4f5d077ee53c1cb6bb4`; Apache main PID `1447135`, active-enter monotonic `1254497584148`.

## Defects

- The exact production ACME order completed without a newly observable DNS-01 TXT. Reusable authorization is the likely cause, so successful issuance alone cannot satisfy this work order's mandatory new-challenge presence artifact.
- The source-safe stream route, loopback TLS listener, renewal and browser gates remain unexecuted because the earlier DNS presence stop condition fired.

## Blockers

No Cloudflare credential, zone authority, cleanup, edge access, host-pin, port-collision or rollback blocker remains. The blocker is procedural/ACME-state-specific: a future attempt must guarantee a fresh Let's Encrypt DNS-01 authorization whose TXT presence can be observed at both authorities before validation. This order permits no second attempt.

Trusted internal dev TLS, safe renewal, the post-change SNI/source matrix, 56 browser states and owner approval remain incomplete. Production and accepted HTTP-only dev staging are healthy and unchanged.

## Next action

Supervisor QA of this failed/rolled-back result. If continued later, dispatch one separately authorized attempt only after selecting a standards-compliant method that guarantees a fresh DNS authorization (for example, an explicitly approved isolated Let's Encrypt account/order path or waiting until the reusable authorization expires). The continuation must repeat all collision/backups/inverse gates, preserve the exact source-safe stream design and perform no second attempt on mismatch. Do not promote dev or start another website item.
