# WO-APP-006-C1-F8 result

## Result

**FAIL — not accepted.** The source implementation and Windows-host release checks are complete, and the automatic appliance build succeeds, but the required executable Linux PCP/service-guard regression still exits `1` without a decisive captured assertion. The work-order stop condition therefore applies. No ordinary/manual offline-install workflow was dispatched.

- F8 work-order SHA-256: `3224ef568f0b4adb3f919c39b6d1668e8f7ec9957e2a77587fafafe16eef5dbd`
- Implementation commits: `c5323681a65889e788ac1237a48d2d95df21f79d`, `ab615d3e4ce5b2671d7edfd201f9240eee0af769`
- Test-only dependency correction: `769227493872d8f694deb0e9f5455051b4095c63`
- Branch: `rc/0.3.11-validation`
- C1: **FAIL**
- OWNER-10: **FAIL**

## Evidence

### Implemented source behavior

- Newly absent denied-unit paths are no longer replaced with temporary `/dev/null` masks during package configuration.
- Exact pre-existing literal masks and the accepted package-backed `iscsi.service` alias remain identity checked and protected.
- `policy-rc.d` remains an exact exit-101 package-start guard, and `SYSTEMD_OFFLINE=1` remains in force for target systemd operations.
- All denied units receive an install/recovery drop-in whose condition is the structurally non-authorizing per-unit `/dev/null/hoardarr-offline-service-guard/<unit>` descendant.
- Successful finalization queries actual enablement and actual target activity. Disabled/not-found units lose only their own temporary guard. Static/indirect/generated/transient units retain their exact guard.
- Retained success-state guards are recorded in `service-retained-guards.json` with unit, canonical path, inode, SHA-256, state, reason and condition. The only supported later action is exact path/inode/hash-verified per-unit guard removal. The manifest is included in the install evidence `SHA256SUMS`.
- The former marker namespace is rejected during setup, guard validation/retention, final readback and retained-manifest production. It cannot authorize the `/dev/null` descendant condition.
- Failure after the package transaction retains the start-denial boundary and refuses a successful recovery receipt when guard validation is uncertain.

### Local checks at the final source state

- `bash -n packaging/appliance/install-offline-payload.sh`: PASS.
- `ruff check tests/release/test_offline_appliance.py`: PASS.
- `ruff format --check tests/release/test_offline_appliance.py`: PASS.
- `python -m py_compile tests/release/test_offline_appliance.py`: PASS.
- `git diff --check`: PASS.
- Focused service/mask/alias/static-contract tests: 3 PASS, 1 Linux-only SKIP.
- Complete release suite: 58 executed, 54 PASS, 4 platform SKIP.
- Offline package plan: PASS; 109 roots and the accepted `systemd-noble` and `linux-meta-noble` compatibility families remain unchanged.

### Automatic run history

1. Commit `c5323681a65889e788ac1237a48d2d95df21f79d`
   - CI `33007384698`: FAIL. Both Linux release-suite jobs completed the executable assertions but TemporaryDirectory cleanup could not remove root-owned `namespace/run-systemd`.
   - Build appliance ISO `33007384763`: PASS; `offline-install` SKIPPED with zero steps.
2. Commit `ab615d3e4ce5b2671d7edfd201f9240eee0af769`
   - CI `33010034428`: FAIL. The Linux harness omitted extracted production helper `entry_is_root_owned`.
   - Build appliance ISO `33010034427`: PASS; `offline-install` SKIPPED with zero steps.
3. Final commit `769227493872d8f694deb0e9f5455051b4095c63`
   - CI `33010987788`: FAIL. `release-bundle-systemd` and `backend` each report only `AssertionError: 1 != 0 :` for `test_real_noble_pcp_postinst_presets_with_production_service_guard`; 58 tests ran and the single PCP harness test failed. No stdout/stderr identifies which in-harness assertion returned nonzero.
   - Build appliance ISO `33010987765`: PASS.
   - `build` job: PASS.
   - `offline-install` job: SKIPPED with zero steps.
   - `hoardarr-appliance`: artifact ID `9622816662`, size `4,408,883,985`, digest `sha256:574dce645f5a130eeadaa803c7b7a276a00b94b60a4d2dfc88371dfbd3edbce8`.
   - `hoardarr-offline-install-inputs`: artifact ID `9622821624`, size `1,072,360,532`, digest `sha256:494f7d6e13e82d4c3cf2eb289ab20b55f542f651f36f50f1714a69d62e47177c`.
   - Artifact metadata was read through the API only; neither artifact was downloaded.

## Defects

- The required real Noble PCP private-mount-namespace regression is not diagnosable at the final commit because the inner Bash harness returns `1` with empty stdout/stderr. Consequently the following F8 acceptance claims remain unverified in executed Linux evidence: exact corrected PCP post-install completion, zero preset errors across all denied units, final static-guard manifest production, and per-unit retained-peer isolation.
- This is an unclassified executable-test failure. It is not evidence that the product assertions passed, and it is not safe to infer the failing assertion.

## Blockers

- F8 acceptance is blocked by the missing stage-specific evidence from the Linux PCP harness. The work order prohibits another speculative correction or retry after an unclassified automatic test failure.
- C1 and OWNER-10 remain independently blocked and were not advanced by F8.

## Next action

Authorize one bounded test-observability follow-up that adds durable, stage-specific markers and fail-trap output inside the existing disposable PCP harness, then uses fresh automatic CI to identify the exact first failing assertion. It must not change production service policy unless that retained evidence proves a product defect. No manual two-pass run should occur before F8 is accepted.
