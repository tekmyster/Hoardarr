# WO-APP-006-C1-F18 result

## Result

**FAIL — implementation is bounded and locally clean, but the required automatic Linux proof did not complete phases 12–15.**

F18 removed the three manager-dependent offline `systemctl is-active` query sites from `disable_unmasked_units`. Install-time activity is now recorded honestly and uniformly as `not-queried-offline` with numeric status `-1`; the JSON receipt adds `"activity_verification": "deferred-to-first-boot"`. The unchanged booted verifier remains the runtime-activity authority.

Both automatic Linux jobs passed phases 10 and 11, then failed identically at the unchanged phase-12 enablement assertion because `iscsid.service` remained `enabled`. The new deferred-activity validator was therefore not reached in the real Noble harness, and phases 12–15 are not verified. No correction or retry was attempted.

## Identity and scope

- Work order: 7,508 bytes; SHA-256 `1E488F0C0A9B071F39BAA7B0A6C5869F5DCCEEC04EF2909BD1FB5D7717C79F0B`.
- Starting local/origin head: `e29c7d791c4e3b42c2e0dcb2213f7d3a3708e750`.
- F17 implementation/handoff remained ancestors: `f34a7eb6be989c42193d35c0a040e8c864ae6b1b` / `9b60038b593d9ff64e94794c29185e7336a8f931`.
- Implementation commit: `01c7afa8923f4de6b27a8be28f1129db7884b1ae` (`fix(appliance): defer offline service activity checks`).
- Implementation parent: `e29c7d791c4e3b42c2e0dcb2213f7d3a3708e750`.
- Authorized implementation paths only:
  - `packaging/appliance/install-offline-payload.sh`: 59,771 bytes; SHA-256 `62077EF0E6F885CC13D11A882F674B906988ACDF60352B338F631494820C42CF`.
  - `tests/release/test_offline_appliance.py`: 235,340 bytes; SHA-256 `871EBF71C224AF35A92475F2BD447E13DE3A52894F60ED1E1F73797B48FAAC80`.
- Concurrent shared-worktree commits after the implementation were preserved. At handoff preparation, local and origin head were both `86bcb4c1f279cca123aeec9994968885d6ae0b3a`, and the F18 implementation remained an ancestor.

## Implemented contract

- Removed exactly three `SYSTEMD_OFFLINE=1 chroot "$target" systemctl is-active ...` sites:
  1. preserved exact `/dev/null` masks;
  2. exact package-backed iSCSI alias/canonical handling;
  3. ordinary denied units after final disable/guard classification.
- Added one bounded row sentinel for every denied unit:
  - activity state: `not-queried-offline`;
  - activity status: `-1`.
- Added top-level JSON authority: `activity_verification=deferred-to-first-boot`.
- The exact production validator rejects old `inactive/3`, `active/0`, missing rows, mixed rows, arbitrary state/status, unsafe enablement, unknown start boundaries, or denied-unit order/cardinality differences.
- The exact extracted production finalizer test uses a fail-if-called chroot manager stub and verifies no `is-active` call occurs.
- Preserved separate start-boundary values: `pre-existing-mask`, `disabled-canonical`, `disabled-unit`, and `condition-drop-in`.
- Preserved `systemctl --root ... is-enabled`, disable/readback, boot-symlink negatives, `policy-rc.d` status 101, mask/alias identity, recovery guards, retained-guard manifest, denied-unit ordering, and cleanup behavior.

## Immutable evidence

- `packaging/appliance/verify-offline-appliance.sh` remained byte-identical: 4,227 bytes; SHA-256 `F188D76E7C19BA38472A5125C68D53E428BCF095D36878AC688E56A93FC627AD`.
- Its booted real-manager `systemctl is-active` query and active-unit rejection remain present.
- Compared with F17 implementation `f34a7eb6be989c42193d35c0a040e8c864ae6b1b`, these source blocks were byte-identical:
  - F16 local-systemd marker oracle;
  - systemd source receipt;
  - systemd causal proof;
  - phase-10 offline nonactivation proof;
  - F17 phase-11 watchdog path-key lookup;
  - phase-14 peer path-key lookup;
  - 15-phase trace grammar.

## Local validation

- `bash -n packaging/appliance/install-offline-payload.sh`: PASS.
- Focused service-guard/PCP/path-key/activity tests: `4 passed, 2 skipped, 35 deselected, 7 subtests passed`.
- Complete release suite: `61 passed, 8 skipped, 118 subtests passed` in 62.10 seconds.
- `ruff format --check tests/release/test_offline_appliance.py`: PASS.
- `ruff check tests/release/test_offline_appliance.py`: PASS.
- `python -m py_compile tests/release/test_offline_appliance.py`: PASS.
- `git diff --check` for both authorized files: PASS.
- The Linux-only generated PCP harness could not execute locally on Windows; both automatic Ubuntu jobs parsed and ran it through the unchanged phase-12 boundary, providing its Bash/execution evidence.

## Automatic evidence

### CI — FAIL

- Run: `33027187964`, push event, attempt 1, head `01c7afa8923f4de6b27a8be28f1129db7884b1ae`.
- Created `2026-08-27T00:32:40Z`; terminal `2026-08-27T00:35:38Z`.
- `release-bundle-systemd`, job `98371200040`: FAIL.
- `backend`, job `98371200077`: FAIL.
- The other five jobs passed: `minio-control-plane-backup`, `installed-appliance-smoke`, `central-fleet-postgres`, `frontend`, and `ubuntu-installer`.
- Both Linux release jobs reported the same 69-test result with one failure and the same bounded trace:
  - phases 01–11: PASS;
  - phase 12 begins;
  - exact first message: `offline install denied unit remains enabled: iscsid.service=enabled`;
  - trace terminal: `HPCP|1|EXIT|12-final-disable-readback|status=1|line=1337|function=main|label=final-disable-readback`;
  - phases 13–15: not reached.
- The accepted phase-10 manager-root/source/causal receipts and phase-11 path-key proof completed before this failure in both jobs.
- CI artifact metadata only; neither artifact was downloaded:
  - `controller-redundancy-browser-evidence`: ID `9628895898`, 8,005,757 bytes, digest `sha256:7be63ca719546951d8c4e5c9a34179a68272fc90712705fb15e333901304b368`.
  - `minio-control-plane-backup-evidence`: ID `9628883631`, 1,255 bytes, digest `sha256:ffed4657a78bbb8dd311c5c33f3ef06c46a74d3189f28c5abc05d4fa805ecd0c`.

### Build appliance ISO — PASS

- Run: `33027187943`, push event, attempt 1, head `01c7afa8923f4de6b27a8be28f1129db7884b1ae`.
- Created `2026-08-27T00:32:40Z`; terminal `2026-08-27T00:42:16Z`.
- Build job `98371200005`: PASS, including signed offline repository, release bundle, ISO build, and visible installer checkpoint.
- Manual-only `offline-install` job `98372876799`: SKIPPED with zero steps.
- Artifact metadata only; neither artifact was downloaded:
  - `hoardarr-appliance`: ID `9629029160`, 4,408,880,488 bytes, digest `sha256:3bbb8d0367f2dabc1e22648fb43c79cc4d44e2432f2de32bd758b3843a91b644`.
  - `hoardarr-offline-install-inputs`: ID `9629032865`, 1,072,363,749 bytes, digest `sha256:d5a63df5a1dd84601f15dcb39f3e40ca38d13d5166f4a43d25565df423085d08`.

## Defect / blocker

The manager-dependent activity-query defect is removed, but F18 acceptance remains blocked by a distinct unchanged phase-12 enablement state: the real Noble harness observes `iscsid.service=enabled` after `disable_unmasked_units`. Because the fail-closed enablement assertion precedes receipt validation, the automatic evidence cannot prove the new sentinel matrix or phases 12–15. This run does not establish why the iSCSI unit remains enabled and makes no correction claim.

## Prohibited-action counters

- Manual workflow dispatches: 0.
- Retries/reruns: 0.
- Cancellations: 0.
- Artifact downloads: 0.
- Ordinary C1 runs: 0.
- Live host/VM/service/storage/credential/website actions: 0.
- HA-11 or adjacent roadmap work: 0.
- Changes to `verify-offline-appliance.sh`, workflows, packages, denied-unit policy, or unrelated product paths: 0.

## Next action

Authorize one separate, narrow diagnosis of why the exact phase-12 Noble fixture still reports `iscsid.service=enabled` after final disable. Preserve F18's deferred-activity implementation and all existing guards; do not reinterpret this enablement failure as runtime activity and do not retry the same automatic pair without an evidence-backed correction.
