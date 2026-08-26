# WO-WEB-013-H4 result

## Result

**FAIL — mandatory pre-write stop; live state unchanged.** H4 reproduced the required local work-order and exact G/H3 identities, created a fresh protected evidence root, and authenticated only with the approved pinned NGINX01 mapping. The fixed second read-only binding probe exceeded its explicit 200 KiB output cap. Per the Supervisor stop instruction, that failure closes H4 before staging, backup, credential creation, certificate issuance, DNS/configuration changes, reload, or browser QA.

A third narrowed read-only session had already failed and ended before the Supervisor no-third-session message was delivered to this task. It used the same approved profile and exact accepted host pin, made no mutation, made no Certbot inventory call or certificate request, and stopped because an in-memory production-block selector returned two matches rather than exactly one. No authenticated action occurred after either Supervisor stop message. This extra session is recorded as a procedure defect and is not treated as accepted binding evidence.

## Evidence

| Gate | Result | Exact non-secret evidence |
|---|---|---|
| H4 order | PASS | 10,257 bytes; SHA-256 `c3139f5074dca56c4726e9eb6dcb57401ff88349915ffb852457bbd95a70f457` |
| H2 order | PASS | 15,594 bytes; SHA-256 `3f17c51c1e5902cc2eb9fe1c4e396dd13e524c991e2d3ace79eacf6b477944bc` |
| corrected map | PASS | 26,905 bytes; SHA-256 `a44647ab82964d5052c196001f9ceeb5ae694377a6d39ff92ba20e54e01f7167` |
| H2 result | PASS | 7,166 bytes; SHA-256 `5bb819270703fec978fe49d7f39f3d625dd5f2a824b5c9e5e8d7fe06290c5a83` |
| H3 result | PASS | 7,228 bytes; SHA-256 `bd7517ef77edf789fb30c3bdf17d37c40fa96541af21a858f92499d641897053` |
| exact G source/copy | PASS | 6 files / 31,215 bytes / tree SHA-256 `1810a43736b2006b8eb95fbd3d16166341474780e17ae0a4b0c43aac101d11cc`; all six accepted file hashes reproduced |
| exact H3 source/copy | PASS | 4 files / 51,788 bytes / tree SHA-256 `426e52dbeefb30a00d61152e54d5f95302f2843e52330ee29e799905f37fd0db`; all four accepted file hashes reproduced; H1 was not copied or used |
| protected H4 root | PASS | `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-H4\WO-WEB-013-H4-20260826T220454Z`; inherited ACLs disabled; full control only for owner, Administrators and SYSTEM |
| repository pre-auth snapshot | PASS | local and `origin/rc/0.3.11-validation` HEAD both `3f456df86b2224ffbe71955a465b81c6419f28ad`; 14 tracked dirty paths plus 56 normal untracked entries captured and preserved without attribution |
| pinned authentication | PASS for identity only | All three read-only sessions authenticated to the accepted host/account mapping; ED25519 host-key SHA-256 hex `37cab91dd39592c2feaa65283389427fb150c32ca9a1cb1b79412288adcf4161`; secrets stayed memory-only and were not emitted, hashed, or persisted |
| first read-only probe | FAIL, retained | Local shell formatting consumed the package-version format string; `dpkg-query` returned status 2. Authentication and pin succeeded; zero mutation. |
| fixed second binding probe | **FAIL — decisive stop** | A bounded read-only input exceeded the explicit 200 KiB cap. The binding probe stopped before filtered Certbot inventory and before every write. |
| third narrowed read-only probe | FAIL, nonconforming chronology | Already completed before the Supervisor no-third-session message arrived. The production selector found 2 candidate server blocks instead of exactly 1, then stopped before either filtered Certbot inventory. Zero mutation. |
| exact dev zero-match binding | NOT RUN | Zero filtered Certbot inventory calls across all sessions. No raw candidate stdout, private-key text, or candidate-frame hash was persisted. |
| production one-candidate frame | NOT RUN | Selection never reached the filtered production-lineage command. |
| G+H3 live copy / on-host backup / off-host transfer / inverse seal | NOT RUN | Remote file writes `0`; backup attempts `0`; no remote H4 path was created. |
| certificate/DNS/config/service transaction | NOT RUN | Credential-file writes `0`; certificate requests `0`; DNS mutations `0`; configuration mutations `0`; reloads/restarts `0`. The still-unused request remains unused. |
| DDI/public/110/renewal/routes/56-state QA | NOT RUN | Downstream gates were correctly closed by the pre-write failure. |
| bounded public unchanged-state readback | PASS | `https://hoardarr.com/` returned 200 and 5,982 bytes; SHA-256 `d92a4c5a6d6a30161239a14c235c36aeeb23beecec70ad92a9373f432dfa027d`, byte-identical to the accepted local apex file. Direct public query to `1.1.1.1` returned zero dev A records. |

The fresh root retains `prewrite-gates.md`, `binding-stop.json`, and the exact locked G/H3 copies. `binding-stop.json` contains only non-secret counts, stop causes, and public identities; it records three authenticated sessions, zero filtered Certbot inventories, zero writes, zero backup attempts, zero requests, and zero service actions. The self-excluding evidence manifest readback matches exactly 12 files / 88,595 bytes; `evidence-manifest.json` is 1,660 bytes / SHA-256 `cb421bcc6ba2c3592fd45b9bf1711a78be766383739d58d804778b29a5abcd0a`.

## Defects

- The decisive H4 defect is the fixed second probe's 200 KiB output-cap failure. The order and Supervisor direction require stopping on that binding failure rather than changing caps or narrowing the query within H4.
- The first probe had a local shell-format construction defect and is not native-binding evidence.
- A third read-only session was started and completed before the no-third-session message was delivered. Although it was mutation-free and stopped before Certbot inventory, it exceeded the accepted two-session procedure and is therefore nonconforming evidence.
- The third probe also showed that the in-memory selector `server_name` condition was insufficiently exact: it matched two production-related blocks. H4 does not investigate or correct that selector.

## Blockers

- Exact H3 dev zero-match and one-candidate Certbot 4 framing remain unbound to live native output.
- Because the mandatory binding gate failed, no H2/H4 backup, inverse, one-request, trusted TLS activation, renewal, route, or browser gate is authorized as complete.
- There is no credential blocker and no live-state recovery blocker: no mutation occurred.

## Next action

Supervisor QA should verify the protected H4 root, exact G/H3 copies, three-session chronology, zero-write/request counters, and unchanged public readback. Any successor must explicitly authorize a fresh read-only binding design that removes the ambiguous production-block selection and defines an accepted bounded source before a new authenticated attempt. H4 must not be resumed, retried, or promoted under this order.
