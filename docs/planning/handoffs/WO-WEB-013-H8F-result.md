# Result

PASS — WO-WEB-013-H8F is complete as a local-only certification under ACC-081 / DEC-2026-08-26-121. The protected candidate reads both privileged NGINX prestate files through one fixed, bounded, password-backed-sudo read before opening SFTP; accounts for the user staging root immediately when its creation succeeds; and permits same-session cleanup only after an exact, shallow, allowlisted lstat/readback. Any identity, type, owner, mode, size, hash, child-set, inspection, or removal ambiguity fails closed with residue recorded as present-or-unknown.

No live-service authentication or network access, H8E reconnect or residue inspection/cleanup, remote read/write, backup, certificate request, DNS action, NGINX action, reload/restart, browser action, promotion, or product/site edit occurred. All associated certification counters are zero. H8E remains stopped and was not resumed. The required Git push is limited to this handoff after evidence closure and is not a live-service action.

# Evidence

- Work order: 9,444 bytes; SHA-256 `d684092fb013ccc9ced42b2d7478b9c92dc5ec05b4e8022299f242759c8fda6d`.
- Protected root: `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-H8F\WO-WEB-013-H8F-20260827T011000Z`. ACL inheritance is disabled; only `dmessana`, `Administrators`, and `SYSTEM` have full control.
- Exact H8D/G2/H7 input: 13 files / 207,923 bytes / tree SHA-256 `2a19838f79a16d32ea6174ff5e198d4ee5d8c1fd2a4d979b6ee4734024bcb137`; input-manifest SHA-256 `b3397fe892011e4b354efc65fccb4770841ebcccd829f3b88baed9001766e1d4`.
- Frozen H8F bundle: 13 files / 233,685 bytes / tree SHA-256 `99a94bcbeee5bf4ff24f74b055e073d47a78d291aa58927c5138451f578a748b`; bundle-manifest 2,625 bytes / SHA-256 `84802d2f0078df4eb255bae7e6ccfe9eb660feae49ad7191a5621a4b19e94e16`.
- Only copied `h8b_controller.py` and copied `tests.py` changed. Controller: 96,089 bytes / SHA-256 `9100ce25b175a609bfd0d81e9302bc6b1f1590150a17c5377da52e8a9df207f7` (diff `+240/-23`). Tests: 27,303 bytes / SHA-256 `e3c3614227a675fdbc80007cd7ad7e3ffb5980f083b2d086a7dcc9325f576012` (diff `+293/-3`).
- G2 remains exact: 7 files / 53,166 bytes / tree SHA-256 `bb97958810c1cc3cd1785d27e42b1fcc527848e9e74d08e6ffc484cda4aa03e7`. H7 remains exact: 4 files / 57,127 bytes / tree SHA-256 `faba67fd00baeab9cef86c26a5d4d4d5f3ea23233886c4f816859a2e3a6f0758`.
- The accepted H8D production HTTP status oracle is byte-identical: extracted helper 753 bytes / SHA-256 `f2cb7d2749cfa51d72e41180ffbd242aaf5a55a14ced9d5bd8c0b99a3678cf5c`; exactly two call sites remain.
- Privileged prestate is one fixed 32,768-byte-capped sudo call covering only `/etc/nginx/stream.d/vpn-sni-passthrough.conf` and `/etc/nginx/conf.d/hoardarr-dev.conf`. The parser requires exact two-record schema, path, regular-file/no-symlink type, root ownership, accepted mode, bounded size, lowercase SHA-256, strict base64, and byte/hash agreement. Raw/base64 file content is not retained in receipts.
- The negative prestate matrix covers nonzero status, cap overflow, malformed/extra schema, missing record, wrong path, symlink, non-root owner, wrong mode, wrong size/hash, and invalid base64. Every case proves zero SFTP opens and zero writes.
- Staging accounting records the first successful temporary-root mkdir immediately (`remote_writes=1`, `staging_write_events=1`, residue=`present`) before lstat or later writes. Failures at the next mkdir, first upload, middle upload, and final upload each preserve the exact successful prefix and invoke the same-session exact allowlisted cleanup.
- Cleanup validates the entire shallow tree before deletion and then removes exact files and exact directories deepest-first. Unexpected child, symlink, mode drift, hash drift, inspection failure, and removal failure all refuse broad deletion and leave explicit present-or-unknown residue.
- H8E residue reconciliation is disabled by default. Fake-transport eligibility accepts only the exact expected `5 directories / 11 files / 110,293 bytes` shape; mode, size/hash, link, extra, missing, owner, and directory-mode near misses refuse cleanup. No live H8E path was contacted.
- Designated clean pass 1 and pass 2 each independently reproduced source tree `99a94bcbeee5bf4ff24f74b055e073d47a78d291aa58927c5138451f578a748b` and passed: H8F/H8D `84/84` (45 inherited H8D + 32 H8F + 7 post-activation helper cases), G2 `28/28`, G2 adversarial `8/8`, and H7 `17/17`. Both H8F suite receipts are 8,436 bytes / SHA-256 `3e8ebadf7a403016cff3395d3a33bdbde6a3dd823cccec797047e42ae8f70a3a`; all reported retry counters are zero.
- Static gates pass: Python AST/compile without effects, JSON parsing, UTF-8/no-BOM/LF/final-LF, Git Bash `bash -n` for both shell files, no generated `__pycache__`, fixed-call/source-boundary assertions, and retained-output secret/no-live audits. `PYTHONDONTWRITEBYTECODE=1` and `python -B` were used.
- Self-excluding protected evidence manifest: 16 files / 241,000 bytes / tree SHA-256 `ba224fad61a6739ba97242f9df71d8954de2381317c6de8a53ff3f74bf4d91e6`; manifest 2,439 bytes / SHA-256 `946c59c53032e9cddfc22e0cf8ef1c58f06779d989e55f635f0222043ea543eb`. Certification receipt: 2,065 bytes / SHA-256 `44ef2d50ab799c0bef35f8160a8d6fac489b592561d848a6e9fed0b6f7cf44aa`.
- Repository baseline before this handoff was local/upstream `c9bcde19ef8c3143cdd07aa0bb0f0460b4595a43`; 71 inherited/concurrent dirty paths were preserved and not attributed to H8F.

# Defects

- One retained pre-designation local invocation supplied two forbidden roots to G2 although its accepted contract requires at least five. That invocation correctly failed `test_16_real_boundary_names_rejected_without_write`. No bundle byte changed and no live budget was consumed. The two designated clean passes used five disposable forbidden roots and passed unchanged.
- The default Windows `bash.exe` is the WSL launcher and no WSL distribution is installed; the required syntax checks passed with the installed Git Bash executable. This is a harness-path limitation, not a bundle defect.
- No unresolved H8F source or test defect remains.

# Blockers

None to Supervisor acceptance of this local certification. H8F does not authorize live use, authentication, H8E residue handling, or continuation of the private-dev TLS transaction, so it makes no live-readiness or deployment claim.

# Next action

Supervisor independently verifies the protected bundle/evidence identities, two clean matrices, and this handoff. Any live successor must be separately ordered and must bind the exact accepted bytes; do not infer authority to reconnect, inspect or clean H8E residue, resume H8E, or begin adjacent website work from this result.
