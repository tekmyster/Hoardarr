# WO-WEB-013-H8B result

## Result

**PASS — one field-preserving production HTTP oracle bundle is locally certified.** The exact H8A controller was copied into a fresh protected H8B root and corrected only at the production loopback HTTP evidence seam. The copied controller now validates and durably publishes the bounded sanitized seven-route/production object before evaluating content baselines, then checks curl status, HTTP protocol/status line, body length, body SHA-256 and HSTS independently.

The frozen output is **13 files / 203,319 bytes / deterministic tree SHA-256 `0f9a1e7dfaf82a788bcf3906e641b6f56c4077bad966fcd32fc60bafe022bd37`**. Both clean certifications passed H8B 29/29, G2 28/28, G2 adversarial 8/8 and H7 17/17 with byte-identical receipts.

This was local-only. Authentication, network access, live reads/commands, uploads, backups, certificate requests, DNS/configuration/service changes, inverse calls, reloads, restarts and browser states were all zero. H8A was not retried and no live state changed.

## Evidence

### Authority and accepted inputs

- Work order: 6,278 bytes / SHA-256 `b026de2286e056c4140b8fac4782a4476947da62fb81efb4b49060cdc367146c`.
- Accepted H8A handoff: commit `bb851c16559e870369bff790b8019f2153059b44`; 10,063 bytes / SHA-256 `0c1fe84d176d9569fa6a10be78533bb0f96d6ed83bd8d203214e796afabec317`.
- Accepted H8A controller input: 78,885 bytes / SHA-256 `097c732abb7957930f31d07bda933564e28184fd538d0641af92c283aeab0927`.
- H8A evidence readback: all 17 entries / 410,548 bytes reopened with zero mismatch; accepted tree `e832dae3fe28c858387fe1b7c0553402e37efe705e7fec6e58aa0aa09758ff89`; accepted transaction receipt `cdc399e52d6e19f662ed9a62562663bb541833deae89f0debf9c9e5d4f77f6f4`.
- Unchanged G2 copy: 7 files / 53,166 bytes / tree `bb97958810c1cc3cd1785d27e42b1fcc527848e9e74d08e6ffc484cda4aa03e7`.
- Unchanged H7 copy: 4 files / 57,127 bytes / tree `faba67fd00baeab9cef86c26a5d4d4d5f3ea23233886c4f816859a2e3a6f0758`.
- Pre-work repository readback: branch `rc/0.3.11-validation`; local/upstream HEAD `2776b7055253566df45fca69295b24eeabd35cab`; 73 inherited/concurrent dirty paths preserved without attribution to H8B.

### Protected root and frozen identity

- Protected root: `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-H8B\WO-WEB-013-H8B-20260826T235530Z`.
- ACL inheritance is disabled. Only `DESKTOP-6U8VLDH\dmessana`, Administrators and SYSTEM have full control.
- Corrected controller: 85,122 bytes / SHA-256 `e193b4ba2616cb6ad881c0a6c43bdb03e5915905af88dfc2b85f4a2e20a8582a`.
- Local certification tests: 7,904 bytes / SHA-256 `43fd701b63a8ec9a68177ac3c51fd6056da9af5ee07f0cf236e43a32f89ad5b4`.
- Bundle manifest: 2,624 bytes / SHA-256 `35f8276a958154f1c05f477b95a494c1b2766acfa99f62890ce53a3c649ac307`.
- Bundle-tree algorithm: SHA-256 over sorted UTF-8 LF records `sha256  decimal-bytes  intended-octal-mode  relative-path\n`.
- Certification receipt: 3,443 bytes / SHA-256 `f5818453d2847f291dff71facb85c7e32272e36e47ced670459f5e3ea617cd80`.
- Private evidence manifest excludes itself and records 83 files / 403,764 bytes / evidence tree `8606c14fe4277d6eeddf860851b971a0cc559e275e6203c8dd1b0340719d835e`. The 15,653-byte manifest SHA-256 is `643f2c6fdffad9cb4cd45c3ea212539faa28704b422abf8380f9de58ecc4f8cf`; fresh readback found zero mismatch.
- `PYTHONDONTWRITEBYTECODE=1` was set. No `__pycache__` exists in the frozen root.

### Exact correction boundary

- The source-only H8A→H8B controller diff is 179 insertions / 10 deletions. Apart from the H8B protected-root/RID/receipt-schema identity update, it adds one top-level function, `publish_and_assert_http`, and replaces the original eight-line combined HTTP assertion block with one call to that function.
- The function uses two nested local helpers so no generic/global command or output surface was added. It accepts only exact top-level, production and seven-route key sets; exact types; bounded integers; bounded printable header/status strings; and lowercase 64-hex digests.
- It reconstructs a fresh sanctioned object containing only the seven route status/content/header tuples and production `status/http/bytes/sha256/hsts` tuple. Extra keys, body bytes, arbitrary stdout/stderr, cookies and unsafe header content are discarded rather than retained.
- `receipt["preflight"]["http"]` is written and `save()` completes before any baseline comparison. Each fail path then writes the exact deterministic stop code and saves again before raising.
- The original seven route content/header baselines are unchanged. The fixed 65,536-byte call cap, 150-second timeout, all earlier preflights, the following 110-case gate, G2/H7 interfaces, mutation sequencing, inverse, renewal, HTTPS/browser gates and one-session/one-backup/one-request/one-reload budgets remain in place.

### Distinct stop-code matrix

| Injected condition | Exact stop code |
|---|---|
| nonzero curl | `production-curl-status` |
| wrong HTTP line | `production-http-status-line` |
| missing HTTP line | `production-http-status-missing` |
| malformed HTTP line | `production-http-status-format` |
| wrong body length | `production-body-length` |
| wrong body digest | `production-body-sha256` |
| missing body digest | `production-body-sha256-missing` |
| malformed/uppercase digest | `production-body-sha256-format` |
| wrong HSTS | `production-hsts` |
| missing HSTS | `production-hsts-missing` |
| malformed HSTS | `production-hsts-format` |
| absent/extra structural key | `http-oracle-schema` |
| wrong type | `http-oracle-type` |
| negative/oversized numeric value | `http-oracle-bounds` |
| bounded parser field overflow | `http-oracle-overflow` |

The matrix also includes the exact valid tuple, top-level and production extras, Boolean/integer ambiguity, missing route, extra route key, unchanged route-content/header failure behavior and route overflow. All 29 cases verify that the durable receipt has the complete sanctioned tuple shape, exact stop code, zero mutation counters and no raw body/stdout/stderr or hostile sentinel.

### Two clean passes and static gates

| Gate | Pass 1 | Pass 2 | Deterministic identity |
|---|---:|---:|---|
| H8B oracle matrix | 29/29 | 29/29 | suite receipt `d635d5dba05689e2e5e15b197d2c83b53e5e3141d79d88796e87aeb22872cc79` |
| G2 primary | 28/28 | 28/28 | output `3783518ee1d26a2faca63ffd1534d029d0d80224a80e47b35a4bb2e1a22f8270` |
| G2 independent adversarial | 8/8 | 8/8 | output `c091038fcc991ba3fc6af9d499397888569a9f2acb86b45e80b4f044c6545b82` |
| H7 complete suite | 17/17 | 17/17 | output `b109cf17d4c1ea95b121909253bfdcb5bdc5bbd08c929c940739359ddc01d70b` |

- Both passes used separate new ACL-protected source copies and disposable state roots. Every one of the 13 copied source files matched the frozen identity before execution.
- The H8B suite installs an audit hook that rejects socket/network and subprocess events; its real copied oracle is exercised directly with injected dictionaries only.
- Python compile passed for all retained Python files; both shell entrypoints passed `bash -n`; all JSON parsed; all retained text is UTF-8 without BOM, CRLF or missing final LF.
- The exact G2 archive identity remains 641 bytes / `ff790f463c71fb8278f38772a71f0697346af288c0a9173ecde88eaeafe54f92`; its inner manifest remains `641336721b4e2c7e532b916fae6e023c70740bb88e6a51bdb74f4e88398dec71`.
- The retained-evidence secret scan found no PEM/private-key block, literal Bearer value or literal Cloudflare token assignment. Synthetic hostile values exist only in the local negative-test source and are proven absent from every retained result/receipt.
- Prohibited-action counters: authentication `0`; network `0`; live commands/reads `0`; remote writes/uploads `0`; backup attempts `0`; certificate requests `0`; DNS/config changes `0`; reloads/restarts `0`; inverse calls `0`; browser states `0`; retries `0`.

## Defects

No defect remains in the bounded H8B local oracle certification. H8A's live trusted-private-dev-TLS transaction remains incomplete because this order explicitly prohibited authentication and live use; H8B does not claim the production loopback tuple now matches the accepted baseline.

## Blockers

There is no blocker to Supervisor review of this exact frozen H8B identity. Live use remains outside H8B authority and requires a separately dispatched transaction that freshly binds these exact bytes and all prior live invariants.

## Next action

Supervisor QA should independently reproduce the 13-file tree, protected ACL, one-function seam, receipt-before-assertion behavior, 29-case matrix, unchanged G2/H7 trees, two clean-pass identities, secret scan and zero-live counters. After acceptance, only a separately ordered live successor may use the frozen controller; do not infer authorization to retry H8A or begin adjacent work.
