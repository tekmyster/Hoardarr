# WO-WEB-012 result

## Result

WO-WEB-012 is **PARTIAL / ACCEPTANCE BLOCKED**. The distinct internal-only HTTP staging path is installed and passing every non-browser gate at `http://dev.hoardarr.com/`, but it is not browser-acceptable: production sends `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`, so Chromium upgrades the internal HTTP URL to HTTPS before document load. The only available Hoardarr certificate has SANs `hoardarr.com` and `www.hoardarr.com`, not `dev.hoardarr.com`; its SHA-256 is `5a2b6441409e9ac3f3ed928b147fab31b14c725a1723488771efdeb98aa4767d` and it expires `2026-10-08 20:00:23 GMT`.

No TLS bypass, HSTS weakening, certificate-name mismatch, public DNS exposure, production promotion or rollback was attempted. This is a trusted-TLS acceptance blocker, not a credential blocker. Production apex/www remains the accepted WO-WEB-011 release.

| Gate | Result | Evidence |
|---|---|---|
| credential/pin inventory | PASS | Canonical All Servers/KeePass register and accepted per-server mappings were exhausted; Paramiko required accepted `known_hosts` identities with `RejectPolicy`, disabled key/agent discovery and kept credentials only in process memory. |
| public-DNS absence | PASS | Public Cloudflare authorities and public recursors returned no `dev` A/AAAA/CNAME before and after the internal write; no public record was created. A public Browser fetch could not resolve/open the name. |
| internal DNS | PASS | Exact split-horizon record `dev A 192.168.0.21`, TTL 300, read back from `192.168.1.10`, `10.81.60.226` and `10.81.60.227`; DNS PID stayed `1500`. |
| distinct origin | PASS | `/var/www/dev.hoardarr.com/public_html` is a separate directory, not a production symlink; it reproduces 13 files / 50,603 bytes / tree `79f4eaf953edb2ba119877b577daf4f342cb6c9f75b9a15bc49473b7e2e6008b`. |
| Apache | PASS | Separate enabled `/etc/apache2/sites-available/dev.hoardarr.com.conf`, SHA-256 `d033e1feedcdbd2ff5c20155ab0194d937306c4f0520b4f5d077ee53c1cb6bb4`; `apache2ctl configtest` passed. Apache main PID remained `1447135`, active-enter monotonic value `1254497584148`. |
| NGINX/private-source enforcement | PASS | Separate `/etc/nginx/conf.d/hoardarr-dev.conf`, SHA-256 `d937c3a13a0570dade04f5ee1140c8663e3a6ed2e65c3505879358e60f02eb82`; explicit loopback/RFC1918 allows followed by `deny all`; `nginx -t` passed. NGINX main PID remained `4337`, active-enter monotonic value `20208693`. |
| noindex/no-store | PASS | All 12 required HTTP readbacks returned exact `X-Robots-Tag: noindex, nofollow, noarchive` and `Cache-Control: no-store`; dev robots is exact `User-agent: *\nDisallow: /\n`, 26 bytes, SHA-256 `331ea9090db0c9f6f597bd9840fd5b171830f6e0b3ba1cb24dfa91f0c95aedc1`. |
| internal HTTP/TLS truth | HTTP PASS / TLS BLOCKED | HTTP routes correctly through NGINX to the separate Apache root. No approved certificate covers `dev`; the apex certificate was not reused. |
| public/direct-origin denial | PASS | Direct-origin Host probing was denied (403 before DNS and network timeout on final bounded retry). A forced public-destination Host probe timed out; public DNS remains absent. NGINX independently denies every non-loopback/non-RFC1918 source. |
| browser matrix | **FAIL** | `0/56` states reached document load. Chromium applied inherited HSTS and rejected HTTPS because no trusted `dev` certificate exists. The browser security policy also prohibited capturing/bypassing the interstitial, so there are no conforming 320/768/1440/1920 light/dark screenshots. |
| backups/inverse | PASS | All three pre-change backups were created before active changes, read back off-host and hash-matched; the exact inverse removes only the new internal record and disables/moves only the new dev vhost/config/root artifacts. |
| production invariants | PASS | Production content, Apache vhost, NGINX production file, corrected-SNI edge behavior, public routes, TLS and accepted backup/inverse generation all match the locked baseline. |
| WEB-11/WEB-12 scope | PARTIAL | Separate staging, exact content identity, atomic new-path publication, rollback evidence and static HTTP semantics passed. Browser-responsive/theme/keyboard evidence remains blocked before document load. Promotion remains unapproved. |

## Evidence

The authoritative work order `docs/planning/work-orders/WO-WEB-012-dev-stage.md` was read completely and independently matched required SHA-256 `45e235c7877dffc6aab6a0e0b83877ff7175993d1dd6b5e8492fe0281cdba567`. The accepted source was independently reproduced before upload and on the final dev origin:

- 13 files; 50,603 bytes; deterministic tree SHA-256 `79f4eaf953edb2ba119877b577daf4f342cb6c9f75b9a15bc49473b7e2e6008b`.
- Candidate manifest SHA-256 `5c649ad755241f6798be4805ecd8963d9562314c6d3ce3b166526f354779698c`.
- Canonical private evidence directory: `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-012\WO-WEB-012-20260826T151939Z`.
- Internal HTTP result table SHA-256 `5fc5a990af349c9c6e89b37b44a9be06cafec1f87a0459043ad5eb1b672bb868`.
- Final production-invariant report SHA-256 `74eee9d96c379c5b356e22b0c308d7f4aad75b86c329c836c7641d5881e846b8`.

Pre-change backups and verified off-host copies:

| Plane | Immutable/on-host path | Off-host readback | SHA-256 |
|---|---|---|---|
| origin | `/var/backups/dev.hoardarr.com/WO-WEB-012-20260826T151939Z.tar.gz` | `...\WO-WEB-012-20260826T151939Z-origin.tar.gz` under the private evidence directory | `0924c73314f89670f39d4f261938e0f6a605c248b940602ae4e3b5653cea2f01` |
| edge | `/var/backups/nginx/hoardarr-dev/WO-WEB-012-20260826T151939Z.tar.gz` | `...\WO-WEB-012-20260826T151939Z-edge.tar.gz` | `5fb9679a6a448f592a8733b5fe178539a42cc9b1c4868c6eb28ce908829f5b38` |
| DNS | `C:\ProgramData\Hoardarr\backups\WO-WEB-012\WO-WEB-012-20260826T151939Z\pre-dns.json` | `...\WO-WEB-012-20260826T151939Z-dns-pre.json` | `ccbdad94fa6dfe46e04c91c30ef8fe0af62c9a315863c7fb0da8db42cce722f2` |

The effective route is internal DNS `dev.hoardarr.com -> 192.168.0.21`, separate NGINX HTTP vhost, proxy to `192.168.0.200:80` with Host `dev.hoardarr.com`, then separate Apache vhost/root. NGINX does not forward client-address headers on this dev proxy, allowing Apache to require the accepted edge address while NGINX performs the client-source allow/deny decision. `/assets/`, `/.git/config` and `/server-status` returned 403; `/development` canonically redirects to `/development/` and the indexed route returns 200.

The initial pre-DNS staged check correctly found that NGINX loopback was missing from the explicit private allowlist. Only `127.0.0.1` and `::1` were added to the separate dev block; `nginx -t`, reload and the complete staged readback then passed. This correction did not touch a production file.

Production final readback:

- `/var/www/hoardarr.com/public_html`: 13 files / 50,603 bytes / tree `79f4eaf953edb2ba119877b577daf4f342cb6c9f75b9a15bc49473b7e2e6008b`.
- Preserved production inverse `/var/www/hoardarr.com/release-WO-WEB-010-20260826T003301Z`: tree `2d1cdf160842c52c13e46aba595fb808bdfd82fe74cda2a22278d6459a150d92`.
- Production Apache vhost SHA-256 remained `0087dab83bece62ac78a88ec9a004ca90075c4481cecf94d9b31d66517f749f4`.
- Production NGINX file SHA-256 remained `367b11bb6182f2cc356efa0ac4e8e49049b3bb1a740c869e309f399c26e0bb1b`.
- Origin, corrected-SNI edge and public readbacks passed for all 12 resources. Public/www root remained 5,982 bytes, SHA-256 `d92a4c5a6d6a30161239a14c235c36aeeb23beecec70ad92a9373f432dfa027d`.
- Accepted production `webroot.tar` remained SHA-256 `f283ed4bc929dc0795c91bc535844793c5ae582dd7e64c4d9060c31e673e4be4`; its off-host copy matched.

The exact inverse, recorded before active writes, is: remove only internal `dev A 192.168.0.21`; move the separate NGINX dev file into its timestamped rollback evidence path, run `nginx -t`, reload; disable the separate Apache dev site, move its config and `/var/www/dev.hoardarr.com` into the timestamped rollback evidence path, run `apache2ctl configtest`, reload. No production content/config generation participates in the inverse.

The shared checkout was `rc/0.3.11-validation` at `27f82fe201361d8c57307d971111faef3b8950f0` at final documentation. Concurrent unrelated application/supervisor changes were present and were preserved. This work order changes no repository implementation path; only this handoff is authorized for commit.

## Defects

- **Acceptance blocker:** production HSTS applies to `dev.hoardarr.com` through `includeSubDomains`; HTTP therefore cannot be the browser approval surface after a client has learned the apex policy.
- The current certificate is valid only for `hoardarr.com` and `www.hoardarr.com`. Presenting it for `dev.hoardarr.com` would be a hostname mismatch and was not done.
- Browser QA, keyboard/focus observation and screenshots are unavailable because document load is blocked before the responsive/theme matrix begins. This is not a static-content defect and not a browser-harness exception to waive.
- No already trusted internal CA issuance/enrollment path was demonstrated in the approved certificate inventory or credential register. That option therefore cannot be claimed available.
- HTTP-only staging remains active for protocol-level/internal checks, but it is unaccepted as an owner approval URL until trusted TLS exists.

## Blockers

Trusted TLS for `dev.hoardarr.com` is required before the browser matrix can run. There is no credential, host-pin, DNS-authority, edge or origin access blocker. No blocker exists to Supervisor review of the passing infrastructure evidence, but the live dev slice must not be accepted for browser approval or promotion in its current HTTP-only state.

## Next action

Supervisor QA of this bounded result, followed only by a separately authorized trusted-TLS remediation:

1. **Preferred — public-trust DNS-01 certificate for `dev.hoardarr.com`.** Keep public A/AAAA/CNAME absent. Prove an approved DNS-01 account/token and least-privilege zone scope, back up certificate/renewal and affected edge config, create only the temporary `_acme-challenge.dev.hoardarr.com` TXT needed for validation, issue a certificate whose SAN explicitly includes `dev.hoardarr.com`, remove/read back the temporary TXT, add a separate private-source-restricted dev TLS listener, validate renewal, then rerun internal/public-negative and complete 7-route × 4-width × 2-theme browser QA. Re-read all production DNS/TLS/content/config/service invariants before and after.
2. **Alternative — already trusted internal CA.** Use only if a later inventory proves an internal CA is already trusted by every intended approval client and has an approved issuance/renewal/revocation path. No such path is currently demonstrated.
3. **Not acceptable:** browser interstitial bypass, HSTS weakening/removal, mismatched apex certificate reuse, self-signed/untrusted TLS, public A/AAAA exposure or direct-origin exposure.

Do not remediate, promote dev, or begin another website item without a new Supervisor-approved order.
