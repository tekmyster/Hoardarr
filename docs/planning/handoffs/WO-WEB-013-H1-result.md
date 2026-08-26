# WO-WEB-013-H1 result

## Result

**PASS — exact live-inverse adapter certified locally; not used live.** The retained secret-free adapter accepts G's fixed `adapter_argv + action` contract, fails closed on identity or target drift, and reproduced the restored synthetic end state twice from separate clean roots with retries disabled. H1 performed no authentication, credential access, network/API call, SSH, upload, service command, certificate/DNS operation, browser action, or production/dev mutation.

| Gate | Result |
|---|---|
| Authority and output confinement | PASS |
| G interface compatibility and fixed action order | PASS |
| Canonical state/schema/seal | PASS |
| Config test, reload, PID/start invariance | PASS |
| Exact candidate certificate deletion | PASS |
| Exact candidate Cloudflare TXT deletion | PASS |
| Readback and idempotent second application | PASS |
| Adversarial and partial-failure recovery | PASS |
| Secret handling and bounded output | PASS |
| Two clean deterministic passes | PASS |

## Evidence

- Authoritative work order: 9,130 bytes; SHA-256 `e67da9b7ed28e241ca59d7bc4324a9571ef9169c5086b91f9645d43dedfa9cf2`.
- Protected evidence root: `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-H1\WO-WEB-013-H1-20260826T211352Z`. ACL readback grants full control only to the owner, Administrators, and SYSTEM. Its manifest covers 11 files / 53,667 bytes; `evidence-manifest.json` SHA-256 `31deb35b3183269dc79cb7c2810f86d114eb6494788c0637f8beb42cacd06ffc`.
- Exact retained bundle: 4 files / 48,730 bytes / deterministic tree SHA-256 `a3b2be01af3048fdc2d77c1c403b1a323e326661839d3b998fdd62ceab962a9b`. Tree lines are sorted `sha256  bytes  filename` with LF. `bundle-manifest.json` SHA-256 is `21de54eb55573916643d9e407e94a1cc5e0beed362bc48337f12ba4f10f963db`.

| Bundle file | Bytes | SHA-256 | Intended mode |
|---|---:|---|---|
| `adapter.py` | 24,988 | `8a1850c1a87479d438c9d72b004906283833728139f25f11eb97dc1afbe5280e` | `0500` |
| `state.example.json` | 1,403 | `78640e7bcea46271e3302aef1ed458efbaaa3869d0861a511d54e4f8f1c0d930` | `0400` |
| `state.schema.json` | 1,067 | `7011bd205700284f3511cecceb7a7718a2553582a441d674ac8cbd8329f08bc3` | `0400` |
| `tests.py` | 21,272 | `567727b5fe34486eac01ace1eb8bb1d789dc837d32516f035fb80313b6958f9f` | `0500` |

- Static receipt SHA-256 `13f94ff1aca87616e50a4cd753703ae8bb865e423f0784f645c8b0fa8b606e3d`: Python compile/AST/import without effects, canonical example plus runtime schema, UTF-8/no-BOM/LF, unchanged hashes, zero executed network calls, and prohibited-surface checks all passed.
- Official pass 1 and pass 2 each ran 15/15 tests with zero failures/errors/skips/retries and emitted byte-identical result SHA-256 `278cd836d8f40628de3fc7d9f16f61e7b4a8ca2a63c41f7c9dba36b0f2d688aa`. Both completed two inverse applications and returned the same canonical receipt: restored configs, candidate certificate absent, exact DNS baseline, unchanged NGINX identity and production leaf, absent dev listeners, completed run 2.
- Native process surface is constructed internally and limited to exact shapes: `/usr/sbin/nginx -t`; `/usr/bin/systemctl show nginx --property=MainPID --property=ExecMainStartTimestampMonotonic --value`; `/usr/bin/systemctl reload nginx`; `/usr/bin/certbot certificates --cert-name EXACT`; `/usr/bin/certbot delete --non-interactive --cert-name EXACT`; `/usr/bin/openssl x509 -in EXACT_PATH -noout -fingerprint -sha256`; and exact-port-filtered `/usr/bin/ss -H -ltn sport = :PORT`. State cannot select flags, services, shells, interpreters, helpers, restart, or generic commands.
- Certbot absence requires the exact supported `No certificates found.` marker. Otherwise one complete recognized candidate block and exact path are mandatory. Empty, unrelated, altered-label, duplicate, oversized, nonzero, and ambiguous inventories fail closed.
- Cloudflare is locked to `https://api.cloudflare.com/client/v4`, exact 32-hex zone/record IDs, real v4 `result`/`result_info` and delete `result.id` shapes, one-page results, empty errors, and sealed TXT identity/content. The credential contract accepts exactly one `dns_cloudflare_api_token = VALUE` assignment in an owner-only Certbot INI; its value is never printed, hashed, placed in argv, receipt, progress, exception, or retained result.
- Adversarial coverage includes state/key/hash/canonical/BOM/symlink/owner/mode/path drift; forbidden output roots; command/path/service drift; every native process failure boundary; fresh full G-sequence recovery before and after side effects; PID/start drift; certificate name/path/fingerprint/duplicate/production targets; realistic Cloudflare extras/errors/pagination/wrong delete ID/record drift/partial deletion; bounded over-limit output; hostile multiline authorization/token/password/private-key material; exact action arity/order; and idempotent already-absent targets.
- The pre-certification review iterations and their results were discarded before the two official passes. The retained `negative-summary.json` records the six corrected contract classes without sensitive values. A repository-wide/private-evidence check found no complete synthetic sentinel value in retained evidence.
- Repository baseline immediately before this handoff was `2f60bfba9a3e8146de3eba418cf9b171bcd5547e`; concurrent App/Supervisor dirty-worktree changes were preserved and are not attributed to H1.

## Defects

No defect remains in the exact retained H1 bundle under the local mocked certification scope. H1 does not claim that native command paths, Certbot output, sealed live state, or the adapter's remote execution have been bound or proven on NGINX01; those are intentionally outside this order.

## Blockers

There is no blocker to Supervisor review of this local certification. The exact bundle remains unauthorized for upload or live use until a separate successor order binds these exact bytes and schema to verified live identities, paths, native tool behavior, and a sealed state root.

## Next action

Supervisor QA should independently reproduce the four bundle hashes/tree identity, static receipt, two identical 15/15 passes, ACL/evidence manifest, and G invocation/recovery semantics. If accepted, issue a separate narrowly bounded live order; do not use or modify the adapter under H1.
