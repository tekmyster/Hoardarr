# WO-APP-006-C1-F20 result

## Result

**FAIL — the bounded F20 implementation is locally clean, but the automatic run did not retain a valid SysV-helper receipt. Classification remains `INCONCLUSIVE`. F18, C1, and OWNER-10 remain FAIL.**

F20 added private SysV containment and a one-invocation observation wrapper around the real copied `systemd-sysv-install` helper without changing the product, verifier, policy, workflow, packages, or original phase-12 production call. Both automatic Linux jobs reached the same Python assertion: the shared F19/F20 post-failure capture status was `1` instead of `0`. The logs do not retain the internal F20 snapshot error, helper invocation receipt, helper stdout/stderr/status, or the phase trace. Therefore this run cannot prove whether the helper was invoked and cannot select a SysV or product cause.

No correction, retry, rerun, manual dispatch, cancellation, or artifact download was performed.

## Identity and scope

- Authority: ACC-085 / DEC-2026-08-26-125.
- Work order: 10,127 bytes; SHA-256 `A078D2988BD2C05DE47A3CAB4F16EA54715168F359FFE4476136D6CD380A60CA`.
- Accepted F19 implementation/handoff: `d5f7ca36a61ee5b75a0f0b63a14be5056d7ad7f8` / `053ceb793b299910a880455e89eeb2bc3086d760`.
- Accepted F19 handoff: 11,850 bytes; SHA-256 `C22791E8886C3804467740792A43B08D271F7DD0518896B925BED77A532A0B1F`.
- Starting local/origin head: `aab2d1f3da222cd19e3f3dff29874003a9902413`.
- Implementation commit: `1fcbf7ccd9a0548b3083c99eb603d45d1d16c7b2` (`test(appliance): diagnose SysV disable helper`).
- Implementation parent: `aab2d1f3da222cd19e3f3dff29874003a9902413`.
- Authorized implementation path only: `tests/release/test_offline_appliance.py`, 307,415 bytes; SHA-256 `92B0715CC07952BE97A2E8F4C6C7ACAF78D1C5D626366CED44AF215F27FA4913`.
- Implementation diff: 768 insertions, 1 deletion.
- A concurrent Website handoff later advanced local/origin to `66ea8542d8f4341b116fd41f23c6af42e1ac4752`; the F20 implementation remains its direct ancestor and all unrelated dirty/untracked paths were preserved.

## Implemented diagnostic boundary

- Computes a bounded host-side manifest before and after the disposable namespace for:
  - `/etc/init.d/iscsid`;
  - `/usr/lib/systemd/systemd-sysv-install`;
  - `/usr/sbin/update-rc.d`;
  - `/usr/sbin/invoke-rc.d`;
  - matching `iscsid`/`open-iscsi` entries in `/etc/rc0.d` through `/etc/rc6.d` and `/etc/rcS.d`.
- Requires byte-for-byte semantic equality of the host before/after manifests.
- Copies the exact observed init-script and matching rc entries into fixed private fixture roots, then bind-mounts private `/etc/init.d` and all eight rc directories inside the already private mount namespace.
- Copies the resolved real `systemd-sysv-install` bytes with mode/ownership/timestamps preserved and hashes them before placing a private wrapper at the exact helper path.
- The wrapper:
  - accepts only exact argv `disable iscsid`;
  - validates the fixed diagnostic environment values without serializing arbitrary environment values;
  - invokes the copied real helper once with unchanged argv/stdin/environment;
  - never calls `systemctl` or updates SysV state itself;
  - preserves the real helper status;
  - caps stdout/stderr at 32 KiB each;
  - requires fixed root-owned mode-0600 evidence and rejects a second invocation.
- Adds strict snapshot validation for object/package/version identities, private bind identities, rc/generator manifests, copied helper identity, argv/environment/status, output hashes/content, traversal/overflow/duplicate/secret-like evidence, and near-miss mutations.
- Keeps exactly one plain `disable_unmasked_units` command in phase 12. No `if`, `||`, retry, replacement, or second cycle was introduced.

## Immutable product boundary

- `packaging/appliance/install-offline-payload.sh`: unchanged at 59,771 bytes; SHA-256 `62077EF0E6F885CC13D11A882F674B906988ACDF60352B338F631494820C42CF`.
- `packaging/appliance/verify-offline-appliance.sh`: unchanged at 4,227 bytes; SHA-256 `F188D76E7C19BA38472A5125C68D53E428BCF095D36878AC688E56A93FC627AD`.
- No product, verifier, workflow, package, package-policy, installer, or other test file changed.

## Local QA

- Focused F19/F20/real-Noble scope: `2 passed, 1 skipped, 40 deselected, 8 subtests passed`.
- Complete `tests/release`: `63 passed, 8 skipped, 126 subtests passed` in 61.21 seconds.
- Ruff format check: PASS.
- Ruff check: PASS.
- Python compile: PASS.
- `git diff --check`: PASS.
- Exact staged scope: one authorized test file.
- Added-line secret review: no credential, token, private key, or secret value was added; only the validator's literal detection expressions matched the scan terms.
- The Linux-only private-mount harness was skipped locally on Windows as expected.

## Automatic evidence

### CI — CANCELLED overall; both Linux diagnostic jobs agree on capture failure

- Run `33032130187`, attempt 1, push event, exact head `1fcbf7ccd9a0548b3083c99eb603d45d1d16c7b2`.
- Created `2026-08-27T02:04:30Z`; terminal `2026-08-27T02:07:20Z`; overall conclusion `cancelled`.
- `release-bundle-systemd`, job `98386793157`: terminal FAIL after 71 tests in 65.753 seconds.
- `backend`, job `98386792995`: its release step produced the same failure after 71 tests in 62.738 seconds; the job then concluded `cancelled` when a higher-priority same-ref CI request appeared.
- Exact common failure at `tests/release/test_offline_appliance.py:5075`:
  - expected capture status `0\n`;
  - observed capture status `1\n`.
- The assertion is the first surfaced F20 failure in both Linux jobs. Neither log contains a validated F20 receipt, helper invocation, helper output, or retained phase trace.
- `ubuntu-installer`, `central-fleet-postgres`, `frontend`, and `minio-control-plane-backup` passed.
- `installed-appliance-smoke` was independently canceled by the later higher-priority same-ref request after its idempotent-reapply step was interrupted; this is not F20 diagnostic evidence.
- The higher-priority request was automatic CI run `33032275620` at later Website handoff head `66ea8542d8f4341b116fd41f23c6af42e1ac4752`. F20 did not create, retry, or cancel that run.
- CI artifact metadata only; no artifact was downloaded:
  - `controller-redundancy-browser-evidence`: ID `9630669641`, 8,006,092 bytes, digest `sha256:bfbf7fe0f61f7c1c9bb09d8c7d6b2a40680390fce21afc41b6dc6abf27b5a515`.
  - `minio-control-plane-backup-evidence`: ID `9630666656`, 1,256 bytes, digest `sha256:4573aac4e28379754dc1b6f5721c9eaf230ea38bc88643c0eda168c81dac4931`.

### Build appliance ISO — PASS

- Run `33032130209`, attempt 1, push event, exact head `1fcbf7ccd9a0548b3083c99eb603d45d1d16c7b2`.
- Created `2026-08-27T02:04:30Z`; terminal `2026-08-27T02:13:11Z`; conclusion `success`.
- Build job `98386793421`: PASS, including signed repository, release bundle, ISO construction, and visible installer checkpoint.
- Manual-only offline-install job `98388264745`: SKIPPED with zero steps.
- Artifact metadata only; neither artifact was downloaded:
  - `hoardarr-appliance`: ID `9630792543`, 4,408,896,665 bytes, digest `sha256:051f27ca0f54eeea8eb5124baea8aada35e055ad491b7ca31ce02884ea17bcc2`.
  - `hoardarr-offline-install-inputs`: ID `9630795025`, 1,072,373,928 bytes, digest `sha256:b491351161b6d6dd3fa64732b616204448c07f9c6ba7500e7c26a7450000b9f2`.

## Required evidence disposition

- One original phase-12 production call in source: **structurally proven**.
- One original phase-12 production call executed automatically: **not independently proven by the retained log**.
- Host manifest unchanged: **the test reached and passed the host before/after equality assertions before failing at capture-status readback, but the manifest/hash was not emitted and is therefore not independently reviewable**.
- Private SysV mount identities: **not retained in a validated receipt**.
- Package/object identities: **not retained in a validated receipt**.
- Helper invoked: **unknown**.
- Helper argv/status/stdout/stderr: **unknown**.
- Private rc-link before/after delta: **unknown**.
- Two-job agreement: **both jobs agree only on capture status 1 at the same assertion**.

## Classification

**`INCONCLUSIVE`.**

The automatic evidence does not establish any of the allowed primary causes. It cannot prove that host-visible SysV state escaped, that the real helper was invoked and returned an error, that systemctl skipped the helper, or that a private unit/product-finalizer relationship caused the status. Promoting any of those alternatives would be a guess.

The immediate bounded defect is diagnostic observability: the shared capture status reports that the F20 post-failure snapshot failed, but the exact snapshot error is not retained in the assertion output. In addition, the current after-snapshot schema requires a complete invocation evidence set and cannot represent the work order's valid `helper not invoked` outcome. Either boundary could produce status 1; this run does not distinguish them.

## Prohibited-action counters

- Product/verifier/workflow/package/policy edits: 0.
- APT/package downloads added by F20: 0.
- Systemctl replacements: 0.
- Second preset/disable cycles: 0.
- Assertion weakening: 0.
- Manual workflow dispatches: 0.
- Retries/reruns: 0.
- Cancellations initiated by F20: 0.
- Artifact downloads: 0.
- Ordinary C1 runs: 0.
- Live host/VM/service/storage/credential/website/HA actions: 0.
- F21 or adjacent work: 0.

## Defects / blockers

- No valid F20 helper receipt survived either Linux job.
- The exact F20 snapshot failure message was not surfaced.
- Helper invocation/status/output and private mount/package/SysV deltas remain unknown.
- Concurrent same-ref CI canceled two non-diagnostic jobs after both Linux release steps had already produced the common F20 failure.
- F18, C1, and OWNER-10 remain FAIL.

## Next action

Authorize only a narrow test-harness observability correction. It should preserve the same private mounts, wrapper, one production call, and all product bytes; retain the exact F20 snapshot stderr/failure classification outside the private mount roots; and allow an exact `invoked=false` after receipt only when every invocation/output/status/repeat file is absent. Then run one fresh automatic pair and stop. Do not change the product or interpret this failed capture as SysV evidence.
