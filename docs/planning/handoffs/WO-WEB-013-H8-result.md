# WO-WEB-013-H8 result

## Result

**FAIL — mandatory pre-authentication inverse-readiness gate stopped the transaction.** No authenticated session was opened and no live action occurred. The protected H8 root and exact frozen G/H7 copies were prepared, but the accepted G inverse cannot represent the two active NGINX files in one valid inverse payload: G requires one `config_root` and each `active_name` to be a single path component, while the active files have different parents.

The conflicting active paths are `/etc/nginx/stream.d/vpn-sni-passthrough.conf` and `/etc/nginx/conf.d/hoardarr-dev.conf`. Their common root is `/etc/nginx`, but the required names `stream.d/vpn-sni-passthrough.conf` and `conf.d/hoardarr-dev.conf` fail G's exact `_name_ok` contract. Using only the basenames would require two different `config_root` values, which G's fixed schema does not permit. Symlinks, helper edits, manual inverse logic, or an extra config indirection would violate H8. Because H8 requires a sealed, tested exact G+H7 inverse before any active write, proceeding into the one-session transaction would not be fail-safe.

Production and internal dev remain unchanged. Trusted TLS for `dev.hoardarr.com` was not activated.

## Evidence

- Work order: 12,505 bytes; SHA-256 `873654784FD5239308672C3D613AA76E0A973882E57B2D89DE96EAAD669AC524` — PASS.
- H2 order: 15,594 bytes; SHA-256 `3F17C51C1E5902CC2EB9FE1C4E396DD13E524C991E2D3ACE79EACF6B477944BC` — PASS.
- Corrected infrastructure map: 26,905 bytes; SHA-256 `A44647AB82964D5052C196001F9CEEB5AE694377A6D39FF92BA20E54E01F7167` — PASS.
- Exact protected G copy: 6 files / 31,215 bytes / tree SHA-256 `1810A43736B2006B8EB95FBD3D16166341474780E17AE0A4B0C43AAC101D11CC` — PASS.
- Exact protected H7 copy: 4 files / 57,127 bytes / tree SHA-256 `FABA67FD00BAEAB9CEF86C26A5D4D4D5F3EA23233886C4F816859A2E3A6F0758` — PASS.
- Protected evidence root: `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-H8\WO-WEB-013-H8-20260826T225531Z`; owner, Administrators and SYSTEM only — PASS.
- Pre-write plan recorded with session/backup/request/reload counters all zero — PASS.
- Local/origin HEAD at final readback: `daad0b85c76c25282e4d0917ad6421a133c6b8ed`; 70 inherited/concurrent dirty paths preserved and not attributed to H8.
- Deterministic G contract probe:
  - `_name_ok("stream.d/vpn-sni-passthrough.conf")` = `false`.
  - `_name_ok("conf.d/hoardarr-dev.conf")` = `false`.
  - `_name_ok("vpn-sni-passthrough.conf")` = `true`.
  - `_name_ok("hoardarr-dev.conf")` = `true`.
  - Parent equality for the two active paths = `false`.
- G `execute_inverse` schema accepts exactly one `config_root`, exactly two files, and single-component `active_name`/`backup_name` values. Therefore no valid payload maps both active paths without changing G or introducing an unauthorized live indirection.
- No secret, credential value, raw NGINX/Certbot/OpenSSL/PEM output, or private material was persisted or printed.

Gate matrix:

| Gate | Status | Evidence |
|---|---:|---|
| Normative input identity/readback | PASS | Exact identities above |
| Fresh protected H8 root | PASS | ACL-restricted root created |
| Exact G/H7 copy and identity | PASS | Exact file/byte/tree identities above |
| Pre-write plan and zero counters | PASS | Protected `pre-write-plan.json` |
| Exact sealed G+H7 inverse representability | **FAIL** | One-root/single-component G contract cannot address both active config parents |
| Authentication/pin/session | NOT RUN | Stopped before authentication |
| H7 empty/presence binding | NOT RUN | No session |
| H5 topology/lineage and all-block convergence | NOT RUN | No session |
| DDI/public DNS boundaries | NOT RUN | No session |
| 110-case mapping baseline | NOT RUN | No session |
| Active config/content/service/provider checks | NOT RUN | No session |
| Immutable on-host backup | NOT RUN | Backup attempts `0` |
| Off-host transfer/readback | NOT RUN | Transfers `0` |
| Certificate request | NOT RUN | Requests `0` |
| Challenge cleanup/certificate identity | NOT RUN | No request |
| Candidate config/PROXY/source/SNI activation | NOT RUN | Remote writes `0`; reloads `0` |
| Renewal | NOT RUN | No certificate |
| Seven-route acceptance | NOT RUN | No activation |
| 56-state browser matrix | NOT RUN | Browser work is post-activation only |
| Production/unrelated final readback | NOT RUN | No session; no live action |

Exact counters: authenticated sessions `0`; remote read calls `0`; remote writes/uploads `0`; backup attempts `0`; off-host transfers `0`; production certificate requests `0`; config writes `0`; reloads `0`; restarts `0`; inverse executions `0`; browser states `0`.

## Defects

- The accepted G inverse payload contract is structurally incompatible with the accepted two-file live layout. This is a transaction-design defect, not an access or credential blocker.
- The locally drafted controller is unexecuted candidate evidence only. It was not uploaded, authenticated, or used against a live service.
- Owner physical-iPhone verification remains pending because trusted dev TLS was not activated.

## Blockers

H8 cannot safely proceed until a separately reviewed successor supplies an accepted inverse mechanism that can atomically and fail-closed restore exactly these two files under different parent directories while retaining the G/H7 identity, deletion, ordering, redaction and confinement guarantees. H8 prohibits editing G, using a manual fallback, adding live indirection, or retrying under a broadened contract.

This is the first decisive technical boundary. It is not resolved by the available credentials or permissions.

## Next action

Supervisor QA should accept the zero-mutation stop and issue a narrow local-only correction/certification order for multi-parent inverse addressing. Do not resume the live transaction until that exact inverse contract is independently accepted. Do not promote, create a public dev record, modify the DDNS updater, change production, begin H9, or perform local-WebUI/VM work in this website task.
