# WO-WEB-013-B result

## Result

WO-WEB-013-B is **VALIDATION FAILED / ROLLED BACK** at the private TLS listener gate. The owner-identified Cloudflare updater credential was independently proven active, distinct from the rejected Certbot credential, able to see the exact authoritative `hoardarr.com` zone, and granted `#dns_records:edit`. One DNS-01 transaction issued an exact-name Let's Encrypt certificate for only `dev.hoardarr.com`, and the temporary challenge was removed.

The first active SNI readback then selected the unrelated self-signed `mail.invalid` default certificate instead of the new dev certificate. Read-only follow-up proved that public port 443 is owned by an NGINX `stream` SNI router. Its current map has explicit apex/www Hoardarr entries but no `dev.hoardarr.com` entry, so dev SNI follows the default `127.0.0.1:17443` backend. The recorded inverse was executed once. The accepted HTTP-only dev configuration is restored, the new certificate/archive/renewal credential are removed, DNS is clean, and all production/dev invariants match the predecessor baseline. No retry, renewal rehearsal, browser matrix, owner artifact or promotion was attempted.

| Required gate | Result | Evidence |
|---|---|---|
| script/credential authority | **PASS** | Existing updater script and root-only runtime mapping were inventoried without exposing or hashing the credential. Cloudflare returned active-token status, exactly one active `hoardarr.com` zone, record-read success and advertised `#dns_records:edit`; the credential is not byte-equivalent to the rejected Certbot credential. |
| collision/precondition inventory | **PASS** | Public dev A/AAAA/CNAME and challenge TXT/CNAME were absent; dev Certbot live/archive/renewal paths were absent; NTP was synchronized; Certbot/plugin/timer, listeners and accepted hashes were inventoried. |
| backups/inverse | **PASS** | Immutable on-host backup and byte-identical off-host readback were completed before the first write. The exact dev-only inverse was validated against the accepted pre-file and later executed successfully once. |
| DNS-01 lifecycle and cleanup | **PASS with evidence limitation** | Certbot completed exact-name DNS-01 issuance; its logs classified 7 DNS-challenge mentions, 2 cleanup mentions, 2 certificate-save mentions and 0 errors. A direct during-challenge snapshot from both authoritative nameservers was not retained because the bounded observer outlived its local harness timeout. Post-cleanup NXDOMAIN was independently proven at both Cloudflare authorities and `1.1.1.1`, `8.8.8.8`, and `9.9.9.9`; Cloudflare API counts are zero. |
| exact certificate | **PASS transiently; removed by inverse** | Issued certificate subject/SAN was only `dev.hoardarr.com`, issuer Let's Encrypt `YE1`, validity 2026-08-26 through 2026-11-24, SHA-256 fingerprint `4deecb2fc5531ac08cb16a4c1ccb5f33ad35701382799c7fe111487f69108aa8`. |
| private TLS/SNI | **FAIL** | The exact first loopback request returned curl verify result 18. OpenSSL SNI readback served SHA-256 `00058b68beb97893ce19dbe5511bc860a7cd6723fee344f836c0cc8a33ab1450`, subject/SAN `mail.invalid`, not the new dev certificate. |
| renewal rehearsal | **NOT STARTED** | Listener validation failed first; the stop condition required immediate inverse before a renewal dry run or hook installation. |
| production invariants | **PASS** | Production content/config/certificate/HSTS and NGINX/Apache service identities match the locked values after rollback. |
| routes/controls | **UNCHANGED / HTTPS NOT STARTED** | Accepted internal HTTP still returns the 5,982-byte document with exact noindex/no-store controls. Trusted HTTPS route acceptance could not start. |
| public/direct-origin denial | **PASS / UNCHANGED** | Public dev address records remain absent; the accepted HTTP private-source config and separate Apache dev vhost/root hashes are restored. No public dev listener remains. |
| 56 browser states | **NOT STARTED** | Trusted HTTPS failed before browser control. No HSTS bypass or certificate exception was used. |
| owner approval artifact | **NOT CREATED** | `https://dev.hoardarr.com/` was not safe to present as approved. |

## Evidence

The authoritative work order was read completely and matched required SHA-256 `a32bf9075317dc2562f914724fbcff05b37d279f847ad377fe33026c5bb3dd49`. WO-WEB-012 and its accepted handoff, WO-WEB-013 and its rejected handoff, the complete target architecture, and the accepted endpoint/credential register were read before execution. The checkout was `rc/0.3.11-validation` at `ca373c6663a8560e90861bc46f88f52b8f428bb3` before this handoff; unrelated concurrent changes were preserved.

Cloudflare path inventory:

- `/usr/local/sbin/cloudflare-ddns-update.py`: root:root `0755`, 6,843 bytes, SHA-256 `668a5bbdde97ff1f06dd48eae76431c23b06c20b7078c555ee671a58d72c9e67`; no embedded credential literal found.
- Runtime credential: `/etc/cloudflare-ddns/cloudflare-ddns.env`, root:root `0600`, 93 bytes; only key names were inventoried. No credential value or credential hash was emitted.
- Record configuration: `/etc/cloudflare-ddns/records.json`, root:root `0600`, SHA-256 `8dd805319d4c5c0749e6e96a06f901e7ccca803d62d8f1acfe022ba690fc1a79`. The updater is an IPv4 DDNS helper: it reads only configured A-record names and uses GET plus record-specific PUT/POST. It does not implement TXT cleanup, so the proven credential was passed through the installed Certbot `dns-cloudflare` plugin for the exact challenge instead of executing the updater.
- Calling unit SHA-256 `6b20f10d42b26a43274b5757fd1be56837b89af0d292de51d02b59e44fa2f799`; timer SHA-256 `f7b4b5be96c651b8fc677f2449f09e9196cb3e6b06f5b7067628bad7cdd9fda9`. The timer was installed but inactive/disabled and its log was empty at inventory time; this did not affect the independently proven API authority.
- Certbot `4.0.0`; `python3-certbot-dns-cloudflare 4.0.0-1`; `certbot.timer` active/enabled. The new renewal metadata used existing ACME account `9a6533f34bd9ff59fd951010553caf03`, ECDSA, DNS Cloudflare, the production Let's Encrypt directory and a 45-second propagation window.

Backup and inverse:

- On-host root: `/var/backups/nginx/hoardarr-dev-tls/WO-WEB-013-B-20260826T185319Z`.
- Immutable archive: `/var/backups/nginx/hoardarr-dev-tls/WO-WEB-013-B-20260826T185319Z.tar.gz`, 1,509 bytes, SHA-256 `f9fe73ae466bfbd0d40caa8100c5abc6f7dcda3cf1f149c2ead6f6e8098f2c71`; directory, files and archive all accepted `chattr +i`.
- Off-host readback: `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-B\WO-WEB-013-B-20260826T185319Z\WO-WEB-013-B-20260826T185319Z.tar.gz`, exact same 1,509 bytes and SHA-256.
- The inverse restores only the accepted dev NGINX pre-file, runs `nginx -t`, reloads NGINX, deletes only the `dev.hoardarr.com` Certbot lineage if present, removes only the dedicated dev renewal credential, and re-tests NGINX. It contains no production config/certificate path. Execution returned zero.

Issuance/listener evidence:

- The dedicated renewal credential existed only as root:root `0600`; it was required because the updater environment format is not a Certbot credentials INI. It was removed by the inverse and never copied off-host or into Git.
- Temporary certificate paths were `/etc/letsencrypt/live/dev.hoardarr.com`, `/etc/letsencrypt/archive/dev.hoardarr.com`, and `/etc/letsencrypt/renewal/dev.hoardarr.com.conf`; all are absent after inverse.
- Candidate dev NGINX file: 3,138 bytes, SHA-256 `b03111c2771fd3d804e61844736a7538a5c77fb7ec42c641ad4a7ced679054b1`. Same-filesystem staging/readback passed, the active readback matched, and `nginx -t` passed before reload. This transient candidate is unaccepted and no longer active.
- Reload kept NGINX main PID `4337` and active-enter monotonic `20208693`, but the first SNI acceptance returned the wrong certificate and triggered rollback.
- Decisive routing evidence: `/etc/nginx/stream.d/vpn-sni-passthrough.conf`, 6,419 bytes, SHA-256 `a75a5f0d5800bd60f86cb803e320a0843c276202734de40fced0591f2c13173c`, maps `hoardarr.com` and `www.hoardarr.com` to `127.0.0.1:18443`, defaults unmatched SNI to `127.0.0.1:17443`, listens on public port 443 and proxies by `$ssl_preread_server_name`. No `dev.hoardarr.com` entry exists.

Final cleanup and invariant readback:

- Cloudflare API: public dev A/AAAA/CNAME = 0; challenge TXT/CNAME = 0.
- `dahlia.ns.cloudflare.com`, `greg.ns.cloudflare.com`, `1.1.1.1`, `8.8.8.8` and `9.9.9.9`: challenge TXT NXDOMAIN after cleanup.
- Internal DNS remains `dev.hoardarr.com A 192.168.0.21` on `192.168.1.10`, `10.81.60.226` and `10.81.60.227`.
- Restored dev NGINX SHA-256 `d937c3a13a0570dade04f5ee1140c8663e3a6ed2e65c3505879358e60f02eb82`; production NGINX SHA-256 `367b11bb6182f2cc356efa0ac4e8e49049b3bb1a740c869e309f399c26e0bb1b`; `nginx -t` passes; main PID/start identity unchanged.
- Production certificate SHA-256 `5a2b6441409e9ac3f3ed928b147fab31b14c725a1723488771efdeb98aa4767d`, SANs only apex/www. Corrected-SNI loopback returns HTTP 200, verify result 0 and 5,982 bytes. Public apex/www each return HTTP 200 with verify result 0 and 5,982 bytes. HSTS remains exact `max-age=31536000; includeSubDomains; preload`.
- Production and dev origin trees remain 13 files / 50,603 bytes / tree `79f4eaf953edb2ba119877b577daf4f342cb6c9f75b9a15bc49473b7e2e6008b`; preserved production inverse remains 13 files / 51,764 bytes / tree `2d1cdf160842c52c13e46aba595fb808bdfd82fe74cda2a22278d6459a150d92`.
- Production Apache vhost SHA-256 `0087dab83bece62ac78a88ec9a004ca90075c4481cecf94d9b31d66517f749f4`; dev Apache vhost SHA-256 `d033e1feedcdbd2ff5c20155ab0194d937306c4f0520b4f5d077ee53c1cb6bb4`; Apache main PID `1447135`, active-enter monotonic `1254497584148`.

## Defects

- The accepted edge architecture has a public `stream` TLS router in front of HTTP NGINX TLS virtual hosts. A new HTTP-context `listen 443 ssl` block does not become the selected public SNI endpoint. `dev.hoardarr.com` requires an isolated loopback TLS listener plus an exact stream-map entry; neither is present in the accepted baseline.
- The during-challenge observer did not persist its direct authoritative TXT-presence sample before its local harness timeout. Exact issuance and final cleanup are proven, but that requested point-in-time artifact is absent.
- The installed DDNS timer is disabled and has no execution history, although its distinct credential is active and has the required Cloudflare authority.

## Blockers

No credential, DNS-authority, certificate-issuance, cleanup or production-integrity blocker remains. The exact blocker is the unapproved/unproven public TLS routing change needed in `/etc/nginx/stream.d/vpn-sni-passthrough.conf`: a dedicated loopback dev TLS backend and exact `dev.hoardarr.com` stream-map entry, with backups, automatic inverse and unrelated-SNI controls. The current order stopped on the first SNI validation failure and does not permit a second attempt or an improvised stream-layer change.

Trusted internal `https://dev.hoardarr.com/`, safe renewal, the 56-state browser matrix and owner approval remain incomplete until that routing boundary is separately authorized and rehearsed.

## Next action

Supervisor QA of this failed/rolled-back handoff. If the owner chooses to continue, issue a new bounded continuation that explicitly covers the existing NGINX stream SNI map and one isolated loopback TLS port, requires exact current stream-file hash/inverse and all unrelated-SNI controls, then repeats one certificate/renewal/listener attempt and the 56-state browser gate. Do not promote dev or start another website item.
