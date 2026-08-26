# WO-WEB-013-G result

## Result

WO-WEB-013-G is **PASS — LOCAL SECRET-FREE HELPER CERTIFIED / NO LIVE OR NETWORK ACCESS**.

The retained six-file helper bundle separates the Python driver, remote backup shell entrypoint, inverse shell entrypoint, fixed allowlist, structured payload schema and local tests. The exact bundle passed Python parsing/import, shell parsing, JSON/schema structure, encoding/newline, output confinement, deterministic child-rooted backup/archive/readback, adversarial negatives, inverse mocks and structured-transport gates. Two final runs from separate clean disposable roots passed 16/16 with retries disabled and produced identical result bytes, manifest identity and archive identity.

G did not open SSH, perform DNS resolution, access a browser or live service, request a certificate, run a service/configuration command, or change production/dev state. D and F evidence remain exact. The bundle is evidence for Supervisor review only; it is not authority for a live attempt.

| Required gate | Result | Evidence |
|---|---|---|
| authority and predecessor | **PASS** | Work order 5,689 bytes / SHA-256 `896a9d9a1977d9dcba1ce293e1d46662019cbb4ca0818f036d8c0acf2849414c`; F handoff SHA-256 `27ed15364084806f43a6ef5fc63cc033697e90b084f91183742df7c8269bac4a`. |
| output confinement | **PASS** | Canonical root guard runs before each helper write and rejects overlap/escape. Read-only negative probes rejected the exact D root, F root, repository, Desktop root and filesystem root without creating probe files. Declared output/state roots must preexist. |
| syntax/static/import | **PASS** | `py_compile` passed for both Python files; Git Bash 5.3.15 `bash -n` passed for both shell files; PowerShell parser is not applicable because the bundle has no PowerShell payload. Driver AST import changed zero retained bytes, imported zero network modules and launched no process. |
| encoding/schema/modes | **PASS** | All six files decode as UTF-8, have no BOM or CRLF and end in LF. Both JSON documents parse; runtime exact-key validation and the retained schema require retries `0`. Bundle manifest explicitly represents executable files as `0500` and data files as `0400`. |
| fixed child allowlist | **PASS** | Seven representative pre/inverse/state names; manifest calculated from inside the exact child, temporarily written, verified, atomically published and verified again. Missing, extra, duplicate, absolute, traversal, nested, zero-byte, symlink and unsafe owner/mode cases reject before mutation-ready. |
| archive/extraction | **PASS** | Deterministic USTAR+gzip archive created from the parent with sorted exact members, fixed metadata and gzip mtime zero; exact members were listed, manually extracted beneath a second guarded root and manifest-verified. |
| copy/readback | **PASS** | Local off-host analogue is byte-identical to the parent archive, extracted beneath a separate disposable root and manifest-verified. Partial-copy evidence differs and cannot satisfy identity. |
| adversarial negatives | **PASS** | Tampered retained file, tampered manifest, wrong child, parent-relative name, unsafe metadata, partial copy and extra archive member all reject or fail verification. No negative returned `mutation-ready`. |
| inverse mocks | **PASS** | Exactly two baseline files restore only from accepted candidate/baseline identities. Mocked action order is config-test, reload, exact certificate deletion, exact DNS deletion, config-test, readback. Second application is idempotent; wildcard name, missing state root and identity drift reject with zero mock actions on drift. No live command ran. |
| transport and secret handling | **PASS** | Canonical UTF-8 JSON roundtrip preserved spaces, Unicode, shell metacharacters and newline-like data. Fixed argv uses `shell=false`, retries `0` and an FD number only; non-placeholder sensitive data rejects. Static scan found no live hostname, IP, username, secret path/value, private key, token or embedded shell body. |
| clean pass 1 | **PASS** | Final certification root `pass-4-root`: 16/16, zero failures/errors/skips, retries `0`; result SHA-256 `647edce74bf56c73bde835755a7ffe13aa03d9aa76eb7b20ef9f80128ff7424d`. |
| clean pass 2 | **PASS** | Final certification root `pass-5-root`: 16/16, zero failures/errors/skips, retries `0`; result SHA-256 `647edce74bf56c73bde835755a7ffe13aa03d9aa76eb7b20ef9f80128ff7424d`. |
| deterministic identities | **PASS** | Both final runs produced manifest SHA-256 `641336721b4e2c7e532b916fae6e023c70740bb88e6a51bdb74f4e88398dec71` and 641-byte archive SHA-256 `ff790f463c71fb8278f38772a71f0697346af288c0a9173ecde88eaeafe54f92`. |
| D/F preservation | **PASS** | D `evidence-manifest.json` remains `c9069dce684ba2bd2c1b3dee74d8236abd88ee55e57d188d6eaba8422d373fa8`; F remains `f017abd32f143937c02e4b02de13a648780938228989f0e51dd3d231677c344e`. |
| prohibited-boundary invariant | **PASS** | Zero network, socket, SSH, browser, NGINX, Apache, Certbot, Cloudflare, service, KeePass or live-system call; zero certificate request; zero live path or repository implementation mutation. |

## Evidence

- Checkout: `rc/0.3.11-validation`; shared-checkout HEAD before handoff `6f08466a665de9f039a55a7a064837349780f29f`. Concurrent App Builder/Supervisor changes were preserved and are not attributed to G.
- ACL-restricted private root: `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-G\WO-WEB-013-G-20260826T204406Z`. Readback grants full control only to the owner, local Administrators and SYSTEM.
- Exact bundle: 6 files / 31,215 bytes / deterministic tree SHA-256 `1810a43736b2006b8eb95fbd3d16166341474780e17ae0a4b0c43aac101d11cc`.
- Bundle tree algorithm: SHA-256 of sorted UTF-8 LF records `sha256`, two spaces, decimal bytes, two spaces, intended octal mode, two spaces, filename, newline.

| Bundle file | Bytes | Intended mode | SHA-256 |
|---|---:|---:|---|
| `allowlist.json` | 551 | `0400` | `918dc9ee34dd9b94f4a0df488cd6017b21f7e57bf20e9fe5ee83f495ce468b29` |
| `driver.py` | 17,308 | `0500` | `d1c2223dbc811cbdd3d13263fdf24b11379a217d6d21e872ae890a72faff5f71` |
| `inverse.sh` | 245 | `0500` | `89412258b1f5532baa147e1b254780899c61751d9a085e0ccf11bc85fe43539f` |
| `payload.schema.json` | 589 | `0400` | `4b482b83baa5f2456ad2cc2c3891c403740fa9d00fb24c9eeba187485c433290` |
| `remote-backup.sh` | 244 | `0500` | `d6c0f6279f8d7f15ea8f6cb95df5e684e5ffb79fda8049d862ad802374c159d8` |
| `tests.py` | 12,278 | `0500` | `294cf08ad567ed19d1189e86712e4fa3cfd44fb537a1e0cc593aebb831ff45b9` |

- `static-result.json`: 1,324 bytes / SHA-256 `30fb4b40b51d5f44bb89d1c8177d94394b8cb17d14fa6f973df72b66ec31259f`.
- Final pass result files are byte-identical, each SHA-256 `647edce74bf56c73bde835755a7ffe13aa03d9aa76eb7b20ef9f80128ff7424d`.
- Private evidence manifest excludes itself and records 1,244 files / 2,236,829 bytes / tree SHA-256 `746f1d192dd2961c2c0858d79217395a516a027b0a3cc4f372a6400fd0fad39e`; `evidence-manifest.json` is 202,792 bytes / SHA-256 `711644f9483cfba345b71ac2252673f7bd6fffa9f6bb2077c5d324c7756cce2c`.
- Only this handoff is an authorized repository write. The helper bundle and all test evidence remain outside Git.

## Defects

- No defect remains in the exact retained six-file bundle under G's local certification scope.
- A retained pre-certification development run used an earlier test-fixture revision and reported two Windows read-only fixture errors plus one over-specific expected error string. It made no network/live call and did not certify the retained bytes. The fixtures were corrected before the two designated clean certification runs; the failed disposable evidence was preserved rather than edited or represented as a passing run.
- The helper has not been exercised on a live POSIX host. G expressly provides no authority to infer live backup, issuance, inverse or activation success from local mocks.

## Blockers

There is no blocker to Supervisor acceptance of the exact local helper bundle. Live backup, certificate issuance and trusted private dev activation remain outside G and require a new bounded work order. G consumed zero production certificate requests.

## Next action

Supervisor QA of the exact bundle identities, final two-pass evidence and this handoff. Do not open SSH, use the helper live, request a certificate, modify NGINX/Apache/DNS/content, run browser QA, promote dev or begin a successor item under WO-WEB-013-G.
