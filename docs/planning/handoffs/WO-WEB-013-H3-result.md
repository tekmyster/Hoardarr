# WO-WEB-013-H3 result

## Result

**PASS — exact Certbot 4 filtered-inventory grammar certified locally; no live use.** A fresh H3 copy of the accepted H1 bundle was revised only in `adapter.py` and `tests.py`. The exact H1 state example/schema remain byte-identical. Static/native-fixture gates passed, the four files were frozen, and two separate clean official roots then passed 16/16 with identical result bytes and no correction between runs.

H3 performed no authentication, socket/network call, DNS/HTTP/Cloudflare/Certbot/SSH/browser action, upload, backup, certificate request, NGINX/Apache/service action, or production/dev mutation.

| Gate | Result |
|---|---|
| authority/work-order/native-fixture identities | **PASS** |
| exact H1 source and pre-edit H3 copy | **PASS** |
| edit boundary (`adapter.py`, `tests.py` only) | **PASS** |
| exact native absence grammar | **PASS** |
| exact legacy absence marker | **PASS** |
| exact one-candidate filtered grammar | **PASS** |
| malformed/ambiguous output rejection with zero deletion | **PASS** |
| inherited H1 DNS/state/service/order/recovery/redaction/confinement coverage | **PASS** |
| static/import/schema/native-fixture checks | **PASS** |
| official clean pass 1 | **PASS — 16/16** |
| official clean pass 2 | **PASS — 16/16** |
| frozen-byte readback and secret-sentinel scan | **PASS** |

## Evidence

- H3 work order: 8,600 bytes / SHA-256 `e8dbf38fcf28b47648e2595eeed82b5c80cc371b7cbe9dfae18a55237aff8b66`.
- H2 native fixture: 1,657 bytes / SHA-256 `fbfee14175fd470ac4730a19cc77df6a125bf13bf498de1d6280bdf3ef92f569`.
- H2 result: 7,166 bytes / SHA-256 `5bb819270703fec978fe49d7f39f3d625dd5f2a824b5c9e5e8d7fe06290c5a83`; commit `21faf0e02e3dd1f329d06e1345f9150e4e37e5cf`.
- H1 source and one H3 pre-edit copy both reproduced 4 files / 48,730 bytes / tree SHA-256 `a3b2be01af3048fdc2d77c1c403b1a323e326661839d3b998fdd62ceab962a9b` before either authorized edit.
- Repository state before editing: `rc/0.3.11-validation`; local and origin HEAD both `89326535039d90e1a5d67fb03e859c1deb89213c`; 70 inherited dirty paths captured and preserved without attribution to H3.
- Protected root: `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-H3\WO-WEB-013-H3-20260826T215437Z`. ACL readback grants full control only to the owner, Administrators and SYSTEM. The evidence manifest excludes itself and records 13 files / 62,341 bytes; `evidence-manifest.json` is 2,204 bytes / SHA-256 `4a860e060106ce15ef5bd2b20fbac1432b7c5a2f7cdd4280a71d37eb45089bcd`.

### Exact certified parser grammar

- Absence is accepted only for exact raw bytes `No certificates found.\n` or the retained Certbot 4 filtered zero-match frame.
- The Certbot 4 frame has exactly one leading LF, an exact 79-character `- `-pattern separator, LF, exact header `Found the following matching certs:`, two LFs, the identical separator, and exactly one trailing LF.
- Presence uses the same exact prefix/suffix and exactly seven ordered, LF-terminated lines with exact indentation and one nonempty value each: `Certificate Name`, `Serial Number`, `Key Type`, `Domains`, `Expiry Date`, `Certificate Path`, and `Private Key Path`.
- Only the exact sealed candidate name and fullchain path are accepted; the fixed OpenSSL argv computes the leaf fingerprint, which must equal the sealed fingerprint before deletion. Domains and private-key text are not deletion authority and are never reflected.
- No `.strip()` or whitespace/CRLF normalization classifies inventory output. The old synthetic `Found the following certs:` presence and empty grammars are deliberately rejected rather than retained for backward compatibility.
- Empty, missing/duplicated LF, CRLF, shortened/lengthened/altered/nonidentical separators, missing/altered/multiple/old headers, missing/duplicate/unknown/empty labels, unrelated name/path, wrong fingerprint, production target/path, two blocks, prefix/suffix text, oversized output and nonzero status all fail with the same generic inventory/process error. Every inventory negative proved zero `certbot delete` calls.

### Frozen bundle

Bundle identity: 4 files / 51,788 bytes / deterministic tree SHA-256 `426e52dbeefb30a00d61152e54d5f95302f2843e52330ee29e799905f37fd0db`. Tree records are sorted `sha256  bytes  intended-mode  filename` with LF. `bundle-manifest.json` SHA-256 is `22f56fc44979fadd8a4d8c6ad6cfa0bd0c6250e0f52b1f8bcd27846157bb7c60`.

| File | Status | Bytes | SHA-256 | Mode |
|---|---|---:|---|---:|
| `adapter.py` | changed H3 copy | 25,568 | `6c1323d368ea5f78c6f1ea5586673f1afebf756bba92806b7553472960367c49` | `0500` |
| `tests.py` | changed H3 copy | 23,750 | `b2e8ce7f98447cb6568d84be3e136be2318631ea8874458bccbcae05eecad61d` | `0500` |
| `state.example.json` | unchanged from H1 | 1,403 | `78640e7bcea46271e3302aef1ed458efbaaa3869d0861a511d54e4f8f1c0d930` | `0400` |
| `state.schema.json` | unchanged from H1 | 1,067 | `7011bd205700284f3511cecceb7a7718a2553582a441d674ac8cbd8329f08bc3` | `0400` |

- Static receipt SHA-256 `051e769b8047e6ea95a00e4733571f208f2cd96b6b9d75a6a9f5846dc8344504`: AST/import without effects, canonical schema/example, exact unchanged state hashes, UTF-8/no-BOM/LF, prohibited-surface checks, zero network calls, separator length 79, and byte-for-byte equality between the H2 native frame and H3 parser constant all passed.
- Official pass 1 and pass 2 each ran 16 tests with zero failures/errors/skips/retries. Their result files are byte-identical at SHA-256 `27f898dc2decc6d191cb9f5a72073710111a1cb83693a526679337e5d57ea39b`; both emitted the same restored/idempotent second-run receipt.
- Post-pass readback reproduced every frozen bundle hash. The complete synthetic sensitive sentinel does not occur in retained H3 evidence. `negative-summary.json` SHA-256 is `79c0a12fb83532bbf7644398ac3fe69b9215e146112dce5923cbdd7572402d6b`.
- One development run passed 16/16 before freeze but is explicitly non-certifying and its result was discarded. Only the unchanged post-freeze official runs are certification evidence.

## Defects

No defect remains in the exact frozen H3 bundle under the authorized local injected-fixture scope. H3 does not prove the exact post-issuance one-candidate frame emitted by live Certbot 4.0.0; the strict candidate grammar is based on the accepted filtered frame plus the documented seven-label block.

## Blockers

There is no blocker to Supervisor review of this local certification. Upload or live inverse use remains unauthorized. Before any later request or configuration write, a separately authorized live preflight must bind the exact one-candidate Certbot 4 filtered framing to these frozen parser bytes without weakening the grammar or consuming the production request merely to manufacture a fixture.

## Next action

Supervisor QA should independently reproduce the changed/unchanged hashes, native-fixture equality, strict grammar negatives, frozen tree, two identical 16/16 results, ACL and evidence manifest. If accepted, issue a separate backup-first live order that binds the exact candidate-frame behavior before any certificate request/configuration write. Do not upload/use H3, authenticate, resume H2, or begin adjacent work under this order.
