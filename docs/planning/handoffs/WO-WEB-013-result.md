# WO-WEB-013 result

## Result

WO-WEB-013 is **PRECONDITION FAILED / EXTERNAL BLOCKED** at the first decisive authority boundary. Public DNS is authoritatively hosted by Cloudflare and the approved edge has Certbot 4.0.0 with the `dns-cloudflare` plugin, an existing ACME account and root-only runtime credential wiring. However, the only approved Cloudflare credential found in the complete All Servers/KeePass and per-server inventory is byte-equivalent to the edge runtime credential and Cloudflare rejects it with HTTP 401. No second scoped token, provider API credential or approved replacement mapping exists.

Execution stopped before backups, TXT creation, ACME issuance, certificate/key creation, renewal metadata, NGINX configuration, reload or browser activity. No DNS, certificate, production, dev, KeePass or application state changed.

| Required gate | Result | Evidence |
|---|---|---|
| credential/authority inventory | **FAIL** | Cloudflare authority and approved credential path were identified, but the sole approved credential returns HTTP 401 and cannot prove zone-control authority. |
| pre-change collision gates | PASS | Public dev A/AAAA/CNAME absent; challenge TXT/CNAME absent; dev certificate/live/archive/renewal paths absent; edge clock NTP-synchronized; no conflicting dev certificate automation found. |
| backups/inverse | NOT STARTED | The order requires a valid authority credential before writes/backups for the issuance transaction. No affected state was changed, so the pre-WO state is already the exact inverse. Existing WO-WEB-012 backups remain untouched. |
| DNS-01 challenge lifecycle | NOT STARTED | No TXT was created; authoritative and recursive absence was re-proven after stopping. |
| exact-name certificate | NOT STARTED | `/etc/letsencrypt/live/dev.hoardarr.com`, archive and renewal metadata remain absent. |
| private-source TLS | NOT STARTED | The accepted HTTP-only dev NGINX block remains SHA-256 `d937c3a13a0570dade04f5ee1140c8663e3a6ed2e65c3505879358e60f02eb82`; no 443 dev listener exists. |
| renewal rehearsal | NOT STARTED | A credential rejected by the provider cannot safely create/clean a staging challenge; no dry run was claimed. |
| internal routes/controls | UNCHANGED | Internal `dev A 192.168.0.21` remains on the accepted primary and both secondaries; separate dev tree remains exact `79f4eaf...`. |
| public/direct-origin denial | PASS / UNCHANGED | Public address records remain absent; the existing private-source NGINX and separate-origin restrictions were not modified. |
| 56-state browser QA | NOT STARTED | Trusted TLS precondition failed. No bypass, mismatched certificate, HSTS weakening or alternate browser surface was attempted. |
| owner approval artifact | NOT CREATED | `https://dev.hoardarr.com/` cannot be presented as trusted until the certificate gate is cleared. |
| production invariants | PASS | Production origin tree/config, edge production config, public content, HSTS, certificate and service identities match the accepted baseline. |

## Evidence

The authoritative order `docs/planning/work-orders/WO-WEB-013-dev-trusted-tls.md` was read completely and matched required SHA-256 `b22b3d44bb874fad4bc4151dfa7efed2bb3342868b08c53e1a269bbef38a8793`. WO-WEB-012, its accepted handoff, the complete target architecture and accepted runtime/endpoint register were read before authority checks.

Credential discovery was secret-safe in the repository and handoff: the canonical KeePass database was opened only in process memory; vault titles, URL hosts and attribute names were exhaustively inspected; all approved per-server `.env` files were checked by key name; no secret value was written to Git or an evidence bundle. The only Cloudflare credential-bearing approved entry was `All Servers/openradius.tekmyster.com/OpenRADIUS Let's Encrypt Radius Certificate`. Its stored `dns_cloudflare_ini` material is the same credential material installed at `/root/.secrets/certbot/cloudflare.ini` on the accepted pinned edge. The file is `root:root` mode `0600`. Both validation paths resolve to the same rejected credential; the provider response is HTTP 401. This is not an SSH/KeePass access blocker.

Read-only edge inventory:

- Host `NGINX01`; accepted pinned SSH mapping; clock `2026-08-26T15:43:26Z`, `NTPSynchronized=yes`.
- Certbot `4.0.0`; `python3-certbot-dns-cloudflare 4.0.0-1`; plugins include `dns-cloudflare` and `nginx`.
- Existing production Hoardarr renewal uses ACME account `9a6533f34bd9ff59fd951010553caf03`, authenticator `dns-cloudflare`, server `https://acme-v02.api.letsencrypt.org/directory`, RSA key type and the same root-only credentials path.
- `certbot.timer` is active/enabled. The dev live/archive/renewal paths are all absent.
- No already approved internal-CA path or alternate public-DNS API credential was found.

Public DNS collision readback was run against both authoritative nameservers and public recursors `1.1.1.1`, `8.8.8.8` and `9.9.9.9`:

- Authorities: `dahlia.ns.cloudflare.com`, `greg.ns.cloudflare.com`.
- Apex CAA: absent.
- `dev.hoardarr.com` A/AAAA/CNAME: absent everywhere.
- `_acme-challenge.dev.hoardarr.com` TXT/CNAME: absent everywhere.

Final invariant readback after the stop:

- Production origin `/var/www/hoardarr.com/public_html`: tree `79f4eaf953edb2ba119877b577daf4f342cb6c9f75b9a15bc49473b7e2e6008b`.
- Dev origin `/var/www/dev.hoardarr.com/public_html`: same exact tree `79f4eaf953edb2ba119877b577daf4f342cb6c9f75b9a15bc49473b7e2e6008b`.
- Preserved production inverse: tree `2d1cdf160842c52c13e46aba595fb808bdfd82fe74cda2a22278d6459a150d92`.
- Production Apache vhost SHA-256 `0087dab83bece62ac78a88ec9a004ca90075c4481cecf94d9b31d66517f749f4`; dev Apache vhost SHA-256 `d033e1feedcdbd2ff5c20155ab0194d937306c4f0520b4f5d077ee53c1cb6bb4`.
- Apache remains active with main PID `1447135`, active-enter monotonic `1254497584148`; config test passed.
- Production NGINX file SHA-256 `367b11bb6182f2cc356efa0ac4e8e49049b3bb1a740c869e309f399c26e0bb1b`; dev HTTP file SHA-256 `d937c3a13a0570dade04f5ee1140c8663e3a6ed2e65c3505879358e60f02eb82`.
- NGINX remains active with main PID `4337`, active-enter monotonic `20208693`; config test passed.
- Public root remains 5,982 bytes, SHA-256 `d92a4c5a6d6a30161239a14c235c36aeeb23beecec70ad92a9373f432dfa027d`.
- Production certificate SHA-256 remains `5a2b6441409e9ac3f3ed928b147fab31b14c725a1723488771efdeb98aa4767d`, SANs only `hoardarr.com` and `www.hoardarr.com`.
- HSTS remains exact `max-age=31536000; includeSubDomains; preload`.
- Internal DNS still answers `dev.hoardarr.com A 192.168.0.21` from `192.168.1.10`, `10.81.60.226` and `10.81.60.227`.

The shared checkout was `rc/0.3.11-validation` at `b5947d7f6b7b8164c00c6f6b880b976e1d54d237` before this dedicated handoff commit. Concurrent unrelated application/supervisor changes were preserved.

## Defects

- The accepted Cloudflare DNS credential is rejected by the provider with HTTP 401. Existing Certbot renewal files reference that same runtime credential path, so its operational repair should consider all current DNS-Cloudflare renewals rather than silently replacing it only for dev.
- No least-privilege `hoardarr.com`-scoped replacement token is present in the approved register.
- Trusted dev TLS, safe renewal and the browser/owner-approval gates therefore remain unproven.

## Blockers

The exact access item required is an owner-approved, active Cloudflare API token with at least Zone:Read and DNS:Edit for the authoritative `hoardarr.com` zone, preferably restricted to that zone, plus an approved root-readable runtime mapping for unattended Certbot renewal. The token must be verified without printing it before WO-WEB-013 resumes.

This is an external provider-credential blocker. It does not authorize creating a broad token, changing the vault, repairing unrelated renewal automation, using the Cloudflare dashboard interactively, or substituting another validation method.

## Next action

Supervisor QA of this blocked handoff. Resume this same work order only after supplying/approving the exact scoped Cloudflare credential mapping. On resume, restart at the full pre-change collision and production-invariant gates, then follow the work order's backup, one DNS-01 issuance, TXT cleanup, private TLS, renewal rehearsal, 56-state browser and owner-approval sequence. Do not begin another website item or promote dev.
