# WO-WEB-013-G2 result

## Result

**PASS — exact local G2 multi-parent inverse bundle certified.** G2 represents the accepted H8 layout as two canonical active-root IDs with one single-component file per root: `stream` → `/etc/nginx/stream.d` → `vpn-sni-passthrough.conf`, and `dev` → `/etc/nginx/conf.d` → `hoardarr-dev.conf`. It preserves G's bounded FD transport, backup implementation, fixed action order, exact deletion authority, confinement, identity checks, redacted/bounded result, and zero-retry contract.

All validation for both roots, both active targets, both backups, all four accepted hashes, both sibling temporary paths, state root and forbidden boundaries completes before the first possible active-parent write. Each baseline is staged and fsynced in its own active parent, hash-verified, then committed with a same-directory atomic replace. No adapter action can run until both active files reread at their exact baseline hashes. An injected partial commit invokes zero adapter actions, safely removes retained staging files, writes a bounded recovery receipt, and a zero-retry idempotent re-entry reaches both baselines before the fixed action sequence.

The exact frozen G2 identity is 7 files / 53,166 bytes / deterministic tree SHA-256 `bb97958810c1cc3cd1785d27e42b1fcc527848e9e74d08e6ffc484cda4aa03e7`. Two separate official clean roots each passed 28/28 primary tests and 8/8 independently implemented adversarial tests with byte-identical 387-byte receipts at SHA-256 `e26063778f73a2b5ecbee105451ba811fd0233b6a50645c2bcf3a71b0718c226`.

G2 performed no authentication, network access, live command, DNS/certificate/config/service action, reload, browser work, public-site work, appliance-WebUI work, or VM work. H8 was not resumed.

## Evidence

- Work order: 7,699 bytes / SHA-256 `3434e52c994d1c6d3084caa0da1b1015be0e4e0bb4a8e05d8dceb59c50b3e554`.
- Protected root: `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-G2\WO-WEB-013-G2-20260826T231733Z`; inheritance removed; only the owner, Administrators and SYSTEM have full control.
- Frozen accepted G allowlist: 6 files / 31,215 bytes / tree `1810a43736b2006b8eb95fbd3d16166341474780e17ae0a4b0c43aac101d11cc`.
- Frozen accepted H7 allowlist: 4 files / 57,127 bytes / tree `faba67fd00baeab9cef86c26a5d4d4d5f3ea23233886c4f816859a2e3a6f0758`.
- The H8 G source's self-created `__pycache__` remains in place as a generated artifact and is explicitly excluded from the six-file accepted-source identity. All six accepted source files and their protected G2 copy reproduce the accepted byte and tree identities exactly. `PYTHONDONTWRITEBYTECODE=1` was applied to all subsequent imports and tests; no cache exists inside G2 candidate/frozen bundles.
- H8 result prerequisite: 6,088 bytes / SHA-256 `6bb3e0fba26fef76d2984ab7e448eea9616dd07f0e9825cee08bce27a6ed7e9d`; zero live counters.
- Repository readback immediately before handoff: local/origin `rc/0.3.11-validation` HEAD `3fead48c61e390a59298b3e150f6cbc8d780dc6e`; 71 inherited/concurrent dirty paths preserved without attribution.

Bundle tree algorithm: SHA-256 over sorted UTF-8 LF records `sha256`, two spaces, decimal bytes, two spaces, intended octal mode, two spaces, filename, newline.

| Frozen G2 file | Bytes | Intended mode | SHA-256 |
|---|---:|---:|---|
| `adversarial_tests.py` | 6,598 | `0500` | `f4a2946fe0b9e1ea752484a49594c98fb045b68efcbd283c4f7333aa22f69dd0` |
| `allowlist.json` | 552 | `0400` | `d4ea935c90f9043d7e9602b549439d8c889f8b4e8ba1de0d634198c1517e652e` |
| `driver.py` | 23,152 | `0500` | `3985dd272c40b8781aab81bae891726c306bb123dcd93038ecac631e6466f5b5` |
| `inverse.sh` | 245 | `0500` | `89412258b1f5532baa147e1b254780899c61751d9a085e0ccf11bc85fe43539f` |
| `payload.schema.json` | 760 | `0400` | `47529e469a94195b5363dcbf0933bb529666e5b8a3f05709ad67ad560dc82e55` |
| `remote-backup.sh` | 244 | `0500` | `d6c0f6279f8d7f15ea8f6cb95df5e684e5ffb79fda8049d862ad802374c159d8` |
| `tests.py` | 21,615 | `0500` | `3863b4c48c6b9bf2b5cc760ee5fb9691416aef3c89d49a86f72aff6b92429616` |

Exact G→G2 source disposition:

- Unchanged byte-for-byte: `inverse.sh`, `remote-backup.sh`.
- Version-only fixed-allowlist metadata change: `allowlist.json`.
- Multi-parent schema cardinality declaration: `payload.schema.json`.
- Multi-parent inverse validation/stage/commit/recovery implementation: `driver.py`.
- Extended original suite: `tests.py`.
- New independent public-interface/adversarial implementation: `adversarial_tests.py`.
- Frozen G and H7 sources were not edited.

Certification gates:

| Gate | Status | Evidence |
|---|---:|---|
| exact inputs and protected root | PASS | Identities and ACL above |
| Python compile and AST parse | PASS | All three Python scripts; bytecode redirected outside bundle |
| shell parse | PASS | Git Bash `bash -n` for both shell entrypoints |
| JSON/schema structure | PASS | Both JSON files parsed; exact two-root/two-file declarations checked; runtime exact-key/schema negatives passed |
| encoding/newlines | PASS | UTF-8, no BOM/CRLF, final LF for all seven files |
| no-live/no-secret static audit | PASS | No network/SSH/browser/live-service modules or action commands; no private material; only synthetic negative field names |
| exact H8 two-parent representation | PASS | Root IDs, canonical parents and exact basenames asserted |
| complete preflight before either write | PASS | Second-target/backup drift leaves both candidates byte-identical and calls zero actions |
| stage/fsync/hash/commit/readback | PASS | Both baselines verify before first adapter call |
| candidates/baselines/mixed/idempotent | PASS | All four input combinations accepted only at sealed identities |
| staging/first commit/second commit failures | PASS | Zero adapter action, safe temp cleanup, machine-readable recovery, deterministic re-entry |
| staged/committed hash drift | PASS | Fails closed before adapter action |
| action failure boundaries | PASS | Each of six boundaries invoked only the exact prefix once; no hidden retry/reorder |
| roots/path/type/symlink/cardinality negatives | PASS | Relative, noncanonical, duplicate, nested, unused, extra, traversal, separator, dot, glob, shell-text, symlink, nonregular, duplicate/swapped and temp-collision cases reject |
| forbidden/identity/schema/secret negatives | PASS | Root overlap, backup/candidate/baseline drift, key additions/removals, nonzero retry, wildcard authority and sensitive payload cases reject |
| FD transport/action authority/output | PASS | Canonical FD-only JSON, `shell=False`, exact H7 argv shape, exact dev certificate/challenge names, fixed six actions, bounded non-secret result |
| official clean pass 1 | PASS | 28/28 primary + 8/8 independent; receipt SHA above |
| official clean pass 2 | PASS | 28/28 primary + 8/8 independent; byte-identical receipt |
| frozen G/H7 post-test identity | PASS | Accepted allowlisted trees reproduce exactly |
| zero-live boundary | PASS | All counters below remain zero |

The unchanged G backup tests retain deterministic 641-byte archive SHA-256 `ff790f463c71fb8278f38772a71f0697346af288c0a9173ecde88eaeafe54f92` and manifest SHA-256 `641336721b4e2c7e532b916fae6e023c70740bb88e6a51bdb74f4e88398dec71` in both official passes.

Private certification receipt: 2,970 bytes / SHA-256 `5a56a13476935874d0e298ca725c3e4cda569fa52cc1e6c300806d37d689c11e`. Private evidence manifest excludes itself and records 2,465 files / 537,008 bytes / tree SHA-256 `7b5686fee72824aa4de9984b2c0012f67818af80a1055e6c26065dcd786ad190`; the 404,045-byte manifest SHA-256 is `304e6a5cd51a11a59f5c099bec06ce1eca5910396cb5795d20743aed9a8119b8`.

Exact counters: authenticated sessions `0`; network calls `0`; live commands `0`; remote writes/uploads `0`; backup attempts `0`; certificate requests `0`; DNS/config changes `0`; reloads `0`; restarts `0`; browser states `0`; test retries `0`.

## Defects

- No G2 certification defect remains in the bounded two-parent inverse contract.
- The optional Python `jsonschema` package is not installed. This did not weaken acceptance: JSON parsing, exact schema declaration checks, runtime exact-key validation, and schema-addition/removal/cardinality adversarial cases all passed twice.
- G2 proves transaction safety locally with injected filesystem and adapter behavior. It has not been uploaded or used live, as required by this work order.

## Blockers

No blocker remains to Supervisor review of this exact frozen G2 identity. This result does not itself authorize a live transaction or H8 resumption.

## Next action

Supervisor may independently reproduce the exact seven-file identity and both 36-test clean passes. If accepted, a separately dispatched live successor may bind only tree SHA-256 `bb97958810c1cc3cd1785d27e42b1fcc527848e9e74d08e6ffc484cda4aa03e7` with the already accepted H7 identity and reissue the H8 transaction under its original one-session/backup/request/reload/inverse limits. Do not resume H8, authenticate, or perform adjacent work without that dispatch.
