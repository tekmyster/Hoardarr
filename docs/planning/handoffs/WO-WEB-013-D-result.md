# WO-WEB-013-D result

## Result

WO-WEB-013-D is **BACKUP GATE FAILED / INVERSE COMPLETE / NO ISSUANCE**.

The work order passed its authority, predecessor, issuance-count/rate-risk, DNS collision, origin/edge identity, source-safe design, and full pre-SNI gates. Supervisor-requested investigation also conclusively bounded the four changed HTTP response tuples as response-layer volatility: their stream destinations, listener owners, TLS classes, leaf fingerprints, active route graph, upstream identities and NGINX service/config identities remained fixed. Those current tuples were persisted as the D response baseline.

The fresh backup gate then failed before archive creation. Its manifest command changed to the backup parent but supplied file paths relative to the child, so `sha256sum` could not resolve the files. The resulting manifest was empty, the archive/off-host copy did not exist, and immutability had not run. No production certificate order, DNS mutation, NGINX config replacement, dev TLS listener, or stream route had been attempted.

The dispatch requires a failed gate to execute the exact inverse and stop. The recorded inverse ran once, restored the already-unchanged accepted files, passed `nginx -t`, reloaded without restarting NGINX, confirmed DNS cleanup, and left all locked production/dev identities exact. No second backup attempt and no issuance attempt occurred.

| Required gate | Result | Evidence |
|---|---|---|
| order/predecessor authority | **PASS** | Work order length 8,986 bytes and SHA-256 `fe563e06096889fc607dcd8226eacd83d03347e5633c2863f9b231808fe13179`; predecessor handoff commit `d915d1e93f10160f45adba3ec71f1246d9a7ea35` and file SHA-256 `f645e91b096699cffdc3b7f048ee366bd53b5ad573156ee6bd7a46811b254b30`. |
| issuance-count/rate preflight | **PASS** | Two prior exact-name production issuances were reconciled. Current Certbot logs contain no 429, rate-limit or Retry-After evidence. One additional exact-set request would have been the third against Let's Encrypt's current limit of five exact-set certificates per seven days; it was not used. |
| account/plugin/clock/authority | **PASS** | One existing production ACME account; Certbot `4.0.0`; `python3-certbot-dns-cloudflare 4.0.0-1`; timer active/enabled; NTP synchronized; existing Cloudflare token active with exactly one active `hoardarr.com` zone. No account or credential was created. |
| DNS/collision preflight | **PASS** | Provider, both authorities and three public recursors had zero public dev A/AAAA/CNAME and zero challenge TXT/CNAME; all three internal resolvers retained `dev.hoardarr.com A 192.168.0.21`. |
| origin/dev/production invariants | **PASS** | Origin production/dev trees remain 13 files / 50,603 bytes / tree `79f4eaf953edb2ba119877b577daf4f342cb6c9f75b9a15bc49473b7e2e6008b`; Apache vhost/service identities match the accepted baseline. Edge production content/config/certificate/HSTS and NGINX service identity matched before the failed backup. |
| source-safe design/full pre-SNI | **PASS — no activation** | Active stream file stayed at 110 entries, 109 explicit plus default, with 108 TLS and two expected non-TLS observations. Ports 19643/19644 were unused. The accepted design remained original-`$remote_addr` private-source dev to loopback 19643, non-private dev to closed 19644, and unchanged delegation for every non-dev SNI. |
| four changed response tuples | **PASS — bounded response volatility** | Exact before/current tuples, route/leaf/config/service evidence and two-repeat current probes are recorded below. Routing/certificate/TLS semantics never changed. |
| fresh immutable on-host backup | **FAIL** | The root-only directory was created with correct pre-files/inverse/state, but its manifest is zero bytes because of the relative-path defect. No archive exists and `chattr +i` was not reached. |
| verified off-host backup | **FAIL / NOT CREATED** | Archive creation failed first; therefore no off-host archive/readback was attempted or claimed. |
| exact inverse | **PASS — executed once** | Return code 0; accepted stream/dev hashes restored, `nginx -t` passed, NGINX reloaded with unchanged main PID/start identity, dev lineage/credential remained absent and exact challenge cleanup returned clean. |
| production issuance/path classification | **NOT STARTED** | D consumed zero of the one permitted production issuance. There is no fresh-DNS or CA-reuse classification for D. |
| exact dev certificate | **NOT STARTED** | Dev live/archive/renewal paths remain absent. |
| loopback TLS/PROXY backend | **NOT STARTED** | 19643 remains unlistened; no candidate config was installed. |
| original-source stream activation | **NOT STARTED** | Stream file is byte-restored to the accepted pre-file; 19644 remains unlistened. |
| postchange SNI/source matrix | **NOT APPLICABLE** | No activation occurred. Post-inverse readback re-proved the unchanged 110-case baseline and adopted response tuples. |
| renewal rehearsal | **NOT STARTED** | Stopped at the earlier backup gate. |
| seven HTTPS routes/controls | **NOT STARTED** | Trusted dev HTTPS was not created. Accepted internal HTTP remains 200 with the exact noindex/no-store controls. |
| 56 browser states | **NOT STARTED** | The browser-control instructions were read, but the browser was not invoked because the production stop condition occurred first. No bypass or certificate exception was used. |
| owner artifact | **NOT CREATED** | There is no trusted dev HTTPS result to present. |
| final production invariants | **PASS** | Public apex is HTTP 200 / 5,982 bytes, leaf SHA-256 `5a2b6441409e9ac3f3ed928b147fab31b14c725a1723488771efdeb98aa4767d`, and exact HSTS `max-age=31536000; includeSubDomains; preload`; origin/edge hashes, DNS, ports and service identities remain accepted. |

## Evidence

### Authority and preflight

- Checkout branch: `rc/0.3.11-validation`. The shared checkout moved concurrently to `c5323681a65889e788ac1237a48d2d95df21f79d` before this handoff; the inherited application/supervisor worktree was preserved and not attributed to this order.
- The accepted architecture, B/C work orders and handoffs, and accepted runtime/endpoint credential register were read before mutation.
- The B fresh issuance is represented by `letsencrypt.log.5`: exact-name success, challenge-performance and cleanup evidence at approximately `2026-08-26T18:54Z` UTC. The C short issuance is represented by `letsencrypt.log.2`: exact-name success with no challenge-performance or cleanup request at approximately `2026-08-26T19:20Z` UTC, consistent with the accepted CA-authorization-reuse finding. Neither log contains local 429/rate/Retry-After evidence.
- Current official Let's Encrypt policy permits five certificates for an exact identifier set per seven days, refilling one every 34 hours: `https://letsencrypt.org/docs/rate-limits/#new-certificates-per-exact-set-of-identifiers`. The authorized third request was below that known limit, but this order stopped before making it.
- Preflight edge hashes: stream `a75a5f0d5800bd60f86cb803e320a0843c276202734de40fced0591f2c13173c`; dev HTTP `d937c3a13a0570dade04f5ee1140c8663e3a6ed2e65c3505879358e60f02eb82`; production NGINX `367b11bb6182f2cc356efa0ac4e8e49049b3bb1a740c869e309f399c26e0bb1b`. NGINX main PID `4337`, active-enter monotonic `20208693`.
- Preflight origin hashes: production Apache vhost `0087dab83bece62ac78a88ec9a004ca90075c4481cecf94d9b31d66517f749f4`; dev Apache vhost `d033e1feedcdbd2ff5c20155ab0194d937306c4f0520b4f5d077ee53c1cb6bb4`; Apache PID `1447135`, active-enter monotonic `1254497584148`.

### Four response-drift tuples

The accepted C response and the first D observation differed only as follows:

| SNI | C response | First D response | Adopted D tuple | Fixed identity/cause evidence |
|---|---|---|---|---|
| `vpn.cptnyc.com` | `HTTP/1.1 404 Not found` | `HTTP/1.1 200 OK` | two consecutive `200 OK` | Exact map `127.0.0.1:17501`; NGINX listener forwards to `192.168.0.150:443`; TLS 1.2 and leaf `225b0dc6b5b34f7a07320a25d51532c5f0d1b0d14274386e8430dd432dc33807` unchanged; direct appliance read returned 200. This is upstream portal response state, not route/certificate/config drift. |
| `tekmyster.com` | `HTTP/1.1 200 OK` | response-line timeout on the initial three-second collector | two consecutive `200 OK` with eight-second bound | Exact map `127.0.0.1:18443`; same long-running `caddy-waf` container/image, route and upstreams; TLS 1.3 and leaf `42c6154c7999074de734bad44639db42dbd02c06da333cf9d5a51f428b3af30d` unchanged. The short collector timeout was transient and did not represent a routing failure. |
| `nvr.cptnyc.com` | `HTTP/1.1 503 Service Unavailable` | `HTTP/1.1 401 Unauthorized` | two consecutive `401 Unauthorized` | Exact map `127.0.0.1:18443`; same Caddy route to `172.17.0.1:9443`, NGINX route to `10.81.60.50:80`, TLS 1.3 and leaf `53049482c6c098d3864fc65962f67d4d466ab284bf8ca8673969d66a631586c1`. Direct Blue Iris 6.1 read now returns its expected 401 authentication challenge; prior 503 was upstream availability state. |
| `nvr.tekmyster.com` | `HTTP/1.1 503 Service Unavailable` | `HTTP/1.1 401 Unauthorized` | two consecutive `401 Unauthorized` | Exact map `127.0.0.1:18443`; same Caddy route to `172.17.0.1:9443`, NGINX route to `10.81.60.50:80`, TLS 1.3 and leaf `42c6154c7999074de734bad44639db42dbd02c06da333cf9d5a51f428b3af30d`. Direct Blue Iris 6.1 read now returns its expected 401 authentication challenge; prior 503 was upstream availability state. |

The active stream file/config and NGINX main identity did not change during these probes. The Caddy WAF container remained the same container/image and has run continuously since `2026-08-16T01:38:08Z`. Post-inverse, all 110 entries retained exact name/backend/TLS-version/TLS-class/leaf semantics and all four adopted response tuples matched.

### Backup failure and inverse

- Partial on-host directory: `/var/backups/nginx/hoardarr-dev-stream-tls/WO-WEB-013-D-20260826T193028Z`, root-only `0700`.
- Correct retained pre-files:
  - `vpn-sni-passthrough.conf.pre`: 6,419 bytes, SHA-256 `a75a5f0d5800bd60f86cb803e320a0843c276202734de40fced0591f2c13173c`.
  - `hoardarr-dev.conf.pre`: 1,334 bytes, SHA-256 `d937c3a13a0570dade04f5ee1140c8663e3a6ed2e65c3505879358e60f02eb82`.
  - `inverse.sh`: 1,125 bytes, SHA-256 `059d2269337b63c6d0756c9b8d79c20439b51c7aa225d0d4187eb28437819b84`.
  - `state.json`: 497 bytes, SHA-256 `94d9011e001584ee89c94c37032ba2c5f70f0f5e24738f6874f0dec1bdc66ebc`.
- Failed artifact: `manifest.sha256` is zero bytes. `/var/backups/nginx/hoardarr-dev-stream-tls/WO-WEB-013-D-20260826T193028Z.tar.gz` does not exist, no file in the partial directory has immutable `i`, and no off-host archive exists.
- Root cause: `find "$RID" -printf '%P'` stripped the child directory name while the following `sha256sum` still ran from the parent. The command failed closed before `tar`, `chattr`, transfer, certificate issuance or config staging.
- Exact inverse executed once with return code 0. Final stream/dev/production hashes match the locked values; NGINX remains active with main PID `4337` and active-enter monotonic `20208693`; 19643/19644 have zero listeners; dev live/archive/renewal/credential paths are absent.
- Final provider/authoritative/recursive public dev A/AAAA/CNAME and challenge TXT/CNAME counts are all zero/NXDOMAIN. Internal DNS remains unchanged.

### Private evidence

ACL-restricted evidence root:

`C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-D\WO-WEB-013-D-20260826T193028Z`

The directory grants full control only to the owner, local Administrators and SYSTEM. The final manifest records 20 evidence/helper files / 138,863 bytes; `evidence-manifest.json` is 3,375 bytes with SHA-256 `c9069dce684ba2bd2c1b3dee74d8236abd88ee55e57d188d6eaba8422d373fa8`. A value-level scan against the approved edge/origin credential sources found zero secret-value matches in the evidence directory.

Key evidence identities:

- `edge-preflight.json`: 15,643 bytes, SHA-256 `cd5a7327a08ba26d25d9c454cb12d0cc4955f704fcbb92f638fb3240a175d742`.
- `origin-preflight.json`: 1,160 bytes, SHA-256 `5aecf815ff13f6d4bd191c820585fb0b3b76afdbdb0b2a6b3c35f027b55acec3`.
- `dns-preflight.json`: 2,717 bytes, SHA-256 `77be32a4ca98f3cf4d89ad5f68e72860fc2a3cd78978c51d7e461f1da9bace71`.
- `pre-sni-matrix.json`: 31,404 bytes, SHA-256 `9be4b699d558282d240e1d693d0bd89b29f70a1f466eb09857fdc69825675b55`.
- `response-drift.json`: 10,439 bytes, SHA-256 `7bcf8713d4cfe5f499243b1741f2392b9222bdd08f6e7521351306236c58b954`.
- `backup-failure-readback.json`: 1,289 bytes, SHA-256 `6bb6cf396b3e31909c27957ce5c8f288a4abe2a71e4ce8d98f83c6da123e3a37`.
- `inverse-readback.json`: 550 bytes, SHA-256 `295aef31efec216d5f7208f157e987ce7eb96a342052b7029a424eb4629acef5`.
- `dns-post-inverse.json`: 2,717 bytes, SHA-256 `dd97aea4c7a568f70f2c6a30f3a675f8da6e39de0199c17e05aaaf0d63dd52cb`.
- `post-inverse-sni-matrix.json`: 31,379 bytes, SHA-256 `6bc9e0463bf805390240256c7497383e972f664b637064c1d99f3b2cf57b0c63`.
- `public-final-readback.json`: 495 bytes, SHA-256 `07ee88e6f87d3741339b3d8273ea74255283c7ecf1e1729e36209c7dc34630af`.

## Defects

- The D backup helper had a deterministic manifest working-directory defect. It created correct pre-files but could not produce a valid manifest/archive, so neither the immutable on-host nor verified off-host backup gate passed.
- A local key-name-only diagnostic used the wrong separator precedence for one mixed-delimiter line and emitted one secret value to transient task output. The value was not written to Git or the final private evidence, and the final evidence scan found zero secret-value matches. Treat that credential as exposed and rotate it only under separate credential authority; its value and hash are intentionally omitted here.
- An intermediate unaccepted drift probe broadly hashed files selected by content and included secret-bearing `.env` files. That intermediate evidence was overwritten; the final `response-drift.json` reads/hashes only the three exact active NGINX config files and contains no secret value/hash. No such hash is repeated here.

## Blockers

WO-WEB-013-D cannot be accepted as a trusted-dev-TLS activation. Its immutable on-host/off-host backup prerequisite failed, so the order correctly stopped before using its one authorized production issuance. Trusted dev TLS, source-safe stream activation, renewal rehearsal, route acceptance, the 56 browser states and owner artifact all remain incomplete.

Production and the accepted internal HTTP dev stage are healthy. No certificate/DNS/config/content blocker was introduced. The partial root-only backup directory is retained as failed-gate evidence; it is not a valid rollback package and must not be represented or reused as one.

## Next action

Supervisor QA of this failed/stopped result. Any continuation requires a new bounded work order. It should explicitly decide retention/removal of the partial D backup, use a corrected manifest rooted inside the backup directory, prove immutable on-host plus byte-identical off-host readback before any issuance, and separately authorize rotation of the credential exposed in transient task output. Do not resume issuance, activate the dev stream route, retry this order, promote dev, or start successor website work under this handoff.
