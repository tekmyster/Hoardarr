# WO-WEB-013-H6 result

## Result

**PASS — native Certbot 4.0.0 production frame mapped with one session and one call; zero live mutation.** H6 opened exactly one accepted pinned NGINX01 session, executed exactly one 32,768-byte-capped `/usr/bin/certbot certificates --cert-name hoardarr.com` program call, closed the session before parsing, and never reopened it.

The 546-byte stdout passed byte safety, deterministic tokenization, the exact seven-label/one-block constraint, and all H5 identity comparisons. The native frame differs from frozen H3 in one structural location: native output places `Certificate Name` immediately after the header, while H3 `CERTBOT4_PREFIX` requires an additional blank line after the header. H6 did not edit, upload, or execute H3 and retained no raw output or unredacted label value.

## Evidence

### Authority, protected inputs and manifest

| Gate | Result | Exact evidence |
|---|---|---|
| H6 work order | PASS | 6,922 bytes / SHA-256 `c837a645f495e56dd0e5821277f203b4578cf0a463b000f3a552435740b273ad` |
| H5 handoff | PASS | 7,273 bytes / SHA-256 `ac75a44403025371666e5c8706565f14c45e99d31ac47e383e2dd71deeba1dde`; accepted commit `5548c4afd794510cfbad6397f36570cb2bff9178` |
| H5 sanitized receipt source/copy | PASS | 4,273 bytes / SHA-256 `6b8d0c04e1f998d14095926aed151693166bd4b62ef077827d6698e63b4bd303` |
| frozen H3 source/copy | PASS | 4 files / 51,788 bytes / tree SHA-256 `426e52dbeefb30a00d61152e54d5f95302f2843e52330ee29e799905f37fd0db`; every accepted per-file hash reproduced |
| repository snapshot | PASS | Before authentication, local and `origin/rc/0.3.11-validation` HEAD both `5548c4afd794510cfbad6397f36570cb2bff9178`; 14 tracked dirty paths and 56 normal untracked entries preserved without attribution. Before the H6 commit, concurrent repository work advanced both HEADs to `788363c67c81b7b22732b325ec7504dfce578572`; H6 did not modify or attribute it. |
| protected H6 root | PASS | `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-H6\WO-WEB-013-H6-20260826T222906Z`; inherited ACLs disabled; full control only for owner, Administrators and SYSTEM |
| sanitized template | PASS | `native-production-frame-template.json`: 4,249 bytes / SHA-256 `8aaf3991d367d76366e36dc495dc7603b95227aa50d79dc768251f933e952104`; JSON readback passed and unredacted identity/private-material pattern scan returned zero hits |
| self-excluding evidence manifest | PASS | Fresh readback matched 7 files / 63,038 bytes; `evidence-manifest.json` is 1,046 bytes / SHA-256 `519b73110a55768ca6b98f63dca92a4f24ea5372cbc1027c64dc8e39233d231b` |

### Exactly one session and call

- Authenticated sessions: **1**.
- Filtered Certbot calls: **1**.
- Accepted identity: `tekmyster@192.168.0.21`; `ssh-ed25519`; host-key SHA-256 hex `37cab91dd39592c2feaa65283389427fb150c32ca9a1cb1b79412288adcf4161`.
- Program argv: `/usr/bin/certbot certificates --cert-name hoardarr.com`; status `0`.
- Stdout: `546` bytes; stderr: `57` bytes; combined: `603 / 32,768` bytes.
- The session closed immediately after the call and before byte parsing. No H5 NGINX, leaf, dev-empty, or other Certbot read was repeated.

### Deterministic value-redacted grammar

The complete frame has `11` lines, `11` LF bytes, exactly one leading LF, and a final LF. Its only blank line is line `0`.

| Line | Bytes | Token | Safe structure |
|---:|---:|---|---|
| 0 | 0 | blank | sole leading blank |
| 1 | 79 | separator | exact spaced-hyphen separator |
| 2 | 35 | header | `Found the following matching certs:` |
| 3 | 32 | label | indent 2; `Certificate Name`; colon 18; delimiter `: `; value 12 bytes |
| 4 | 54 | label | indent 4; `Serial Number`; colon 17; delimiter `: `; value 35 bytes |
| 5 | 17 | label | indent 4; `Key Type`; colon 12; delimiter `: `; value 3 bytes |
| 6 | 42 | label | indent 4; `Domains`; colon 11; delimiter `: `; value 29 bytes |
| 7 | 59 | label | indent 4; `Expiry Date`; colon 15; delimiter `: `; value 42 bytes |
| 8 | 70 | label | indent 4; `Certificate Path`; colon 20; delimiter `: `; value 48 bytes |
| 9 | 68 | label | indent 4; `Private Key Path`; colon 20; delimiter `: `; value 46 bytes |
| 10 | 79 | separator | byte-identical to line 1 |

- Separator indices: `[1, 10]`; both are 79 bytes and identical.
- Header index: `2`; first label index: `3`. Blank lines between header and first label: **none**.
- Last label index: `9`; closing separator index: `10`. Blank lines between them: **none**.
- Ordered labels exactly match the required seven names; inferred blocks: `1`.
- Unknown lines/tokens: `0`; duplicate labels: `0`; extra/missing labels: `0`; extra blocks: `0`; trailing bytes: `0`.
- Every transient value was nonempty, printable ASCII, control-safe, and replaced by `<VALUE>` in retained evidence.

### H5 identity comparisons

| Comparison | Result |
|---|---|
| lineage name | PASS |
| public serial | PASS |
| key type | PASS |
| domain set | PASS |
| expiry/not-after | PASS |
| fullchain path | PASS |
| private-key path | PASS |
| referenced/public leaf binding | PASS |

Structural acceptance and identity acceptance are both `true`. No newly observed value was retained.

### Zero-mutation counters

| Counter | Value |
|---|---:|
| remote writes / uploads | 0 / 0 |
| backup attempts | 0 |
| remote credential-file reads / credential writes | 0 / 0 |
| certificate requests / deletes / renewals | 0 / 0 / 0 |
| DNS / configuration / service changes | 0 / 0 / 0 |
| reloads / restarts | 0 / 0 |
| browser runs / public-DNS actions / promotions | 0 / 0 / 0 |

## Defects

- Frozen H3's production-presence prefix is not byte-compatible with the observed native Certbot 4.0.0 frame. H3 requires a blank line after the header; native production output has the first label immediately after the header.
- H6 is mapping evidence only. It does not correct or certify a revised parser and grants no live inverse or mutation authority.

## Blockers

- A separately authorized local-only parser correction/certification must consume this sanitized template and preserve the already accepted empty-frame grammar and all fail-closed negatives before any future live transaction.
- There is no live recovery blocker: H6 made no mutation, and the unused backup/certificate-request attempts remain unused.

## Next action

Supervisor QA should reproduce the H6 ACL, copied input identities, template bytes/hash, per-line grammar, all eight match booleans, self-excluding manifest, exact one-session/one-call counters, and every zero-mutation counter. Do not resume H6, authenticate again, edit H3 under this order, or begin H7/adjacent work.
