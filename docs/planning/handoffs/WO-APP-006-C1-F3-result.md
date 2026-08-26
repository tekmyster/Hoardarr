# WO-APP-006-C1-F3 result

## Result

- **F3 declarative systemd-family closure: PASS (implementation and automatic build).** The profile-derived product-root set remains exactly 109 entries. One schema-validated compatibility family supplies the eleven proven systemd-family transition packages as exact candidate inputs without promoting the four compatibility-only packages to product roots. Automatic push run `32952291733` built and verified the signed repository, release bundle, ISO, and installer checkpoint successfully at commit `865d917c62f920b26a6427d5bf2661f51a85c76b`.
- **F3 retained-base simulation: FAIL / NOT EXECUTED.** The successful automatic push run did not create `hoardarr-offline-install-inputs`: that artifact and the offline-install job are explicitly gated to `workflow_dispatch`. F3 prohibits manual dispatch and prohibits downloading the appliance ISO artifact. Consequently there was no authorized corrected repository payload to transfer into a fresh F2-backed overlay, and no APT simulation was run.
- **C1 ordinary two-pass baseline: FAIL.** F3 authorized no ordinary two-pass run and does not supersede the accepted C1 failure.
- **OWNER-10: FAIL.** F3 does not implement or validate LINSTOR, DRBD 9, DRBD Reactor, LINSTOR Gateway, kernel/Secure-Boot integration, or the offline Proxmox-plugin sidecar.

No workflow was manually dispatched. No artifact was downloaded. No overlay was created or booted. No package install was performed. No live system, credential, network, pool, owner storage, protected media, or unrelated dirty worktree path was changed.

## Evidence

### Scope and commits

- Work order SHA-256: `1aafc8c221fcc183a1c45466c552516941f7130608909167b4969c825f5fbce1`.
- Required and observed starting local/origin HEAD: `93d648593c02ed1155cae536bb3de610b75873b7`.
- Initial F3 implementation commit: `34ed35a8dffd4413b72d07c5b0d6fd28d7d05645`.
- Architecture-aware verification correction: `865d917c62f920b26a6427d5bf2661f51a85c76b`.
- Local/origin implementation HEAD before this handoff: `865d917c62f920b26a6427d5bf2661f51a85c76b`.
- Authorized implementation files only:
  - `packaging/offline/package-policy.json`
  - `scripts/build-offline-apt-repository.py`
  - `tests/release/test_offline_appliance.py`

The first automatic run, `32951933104`, was canceled by Supervisor while repository construction was active after independent QA proved that `systemd-dev` is legitimately `Architecture: all`; no retry was made. The correction binds each family member to exactly one unambiguous package-manifest binary identity and permits only target-compatible `amd64` or `all`. Missing, duplicate, conflicting, and unsupported architectures fail closed.

### Policy and root identity

Policy schema remains `1`. Its sole compatibility family is:

```json
{
  "id": "systemd-noble",
  "version_policy": "single-candidate-version",
  "members": [
    "systemd",
    "systemd-sysv",
    "systemd-timesyncd",
    "systemd-resolved",
    "udev",
    "libudev1",
    "libsystemd0",
    "libsystemd-shared",
    "libpam-systemd",
    "libnss-systemd",
    "systemd-dev"
  ]
}
```

The family is passed to the empty-status APT closure as exact `package=version` inputs. The builder requires all eleven candidates to have one version, verifies every exact download, rejects duplicate binary identities, emits `evidence/compatibility-families.json`, and verifies declaration order, family/member schemas, manifest identities, compatible architecture, versions, and repository `Packages` entries. `root-package-versions.txt` is still generated only from the profile-derived roots.

- Starting root count: `109`.
- Final root count: `109`.
- Starting sorted-root identity SHA-256: `71f0bc60aa3dba665b25511479cce5b853e0ae25ccd3dd0eb264d5ead0f23170`.
- Final sorted-root identity SHA-256: `71f0bc60aa3dba665b25511479cce5b853e0ae25ccd3dd0eb264d5ead0f23170`.
- `git diff --exit-code HEAD -- packaging/packages`: clean before the final correction; no root/profile manifest was edited or committed by F3.

### Local validation

Final material-diff validation:

```text
backend\.venv\Scripts\python.exe -m unittest tests.release.test_offline_appliance tests.bootstrap.test_manifests
29 tests passed

backend\.venv\Scripts\ruff.exe check scripts/build-offline-apt-repository.py tests/release/test_offline_appliance.py
All checks passed
```

Also passed:

- Python compilation of both touched Python files.
- PowerShell `ConvertFrom-Json` parsing of `package-policy.json`.
- Executed `plan` readback proving 109 unique product candidates, one family, and eleven members.
- `git diff --check`.
- Profile/root-manifest diff check.
- Non-alphabetical generation/verification (`udev:amd64`, `systemd-dev:all`).
- Controlled rejection of malformed family member evidence instead of raw `AttributeError`.
- Negative missing download, family version mismatch, duplicate binary identity, manifest omission, repository omission, duplicate/conflicting architecture, and unsupported architecture cases.

Bash/YAML files were not changed by F3. The applicable workflow YAML remained unchanged and the automatic push execution supplied its executable validation.

### Automatic build

- Workflow: `Build appliance ISO`.
- Event: `push` (automatic; not manually dispatched).
- Run: `32952291733`.
- Job: `98126177466` (`build`).
- Commit: `865d917c62f920b26a6427d5bf2661f51a85c76b`.
- Started: `2026-08-26T09:18:29Z`.
- Completed: `2026-08-26T09:27:46Z`.
- Conclusion: `success`.
- Signed repository build/verify step: `2026-08-26T09:19:11Z` to `09:20:22Z`, success.
- Release bundle: success.
- ISO build: success.
- Interactive installer checkpoint: success.
- Artifact upload: success.

The run emitted only:

- Artifact ID: `9600911059`.
- Name: `hoardarr-appliance`.
- Size: `4,391,496,073` bytes.
- Digest: `sha256:791a0a7772f01687cb452fec132349239d5b8019a17ef934460304d4a6a711d8`.
- Expiry: `2026-11-24T09:18:25Z`.

The artifact upload reported 539 files and includes ISO bytes plus selected repository evidence. It was **not downloaded**, because F3 expressly permits downloading only `hoardarr-offline-install-inputs` and prohibits downloading the appliance ISO. The job step `Retain offline install inputs for no-network validation` was skipped, and job `offline-install` was skipped, because `.github/workflows/appliance.yml` restricts both to `github.event_name == 'workflow_dispatch'`.

Therefore these required preflight values remain unavailable rather than inferred:

- offline-input artifact ID/digest/download path;
- independently read-back corrected repository package count/tree hash/package-manifest hash;
- independently read-back resolved family version/member identities;
- corrected repository transfer hash.

### Retained F2 source preservation

The retained source was read only after the artifact gate stopped execution:

- Path: `.codex-temp\f2-32943411481\pass-2\offline-evidence\pass-2\os.qcow2`.
- Size: `3,968,401,408` bytes.
- SHA-256: `2393e9b4c90aeaf56a580634b3538defd0c3af26fa30c3a3846654962e3ba60d` (matches locked F2/D3 evidence).
- Virtual size: `34,359,738,368` bytes.
- Format: QCOW2; dirty false; corrupt false.
- `qemu-img 11.1.0 check --output=json`: exit 0, `check-errors=0`.

Because no overlay or guest was created, the same readback is both the F3 before/after state. No QEMU argv existed, no NIC existed, and no protected media was attached.

## Defects

1. The push-triggered appliance build does not retain the `hoardarr-offline-install-inputs` artifact required by F3's local retained-base gate; that artifact is available only from a prohibited manual-dispatch path.
2. The successful repository build proves the implementation can construct and self-verify the corrected repository, but F3 lacks the authorized artifact needed for independent package-count/family-evidence readback and exact retained-base APT simulation.
3. C1 remains failed and OWNER-10 remains unimplemented.

## Blockers

- **F3 retained-base simulation:** blocked by the conflict between the mandated automatic-push run and the workflow's dispatch-only offline-input artifact. This cannot be resolved without either a separately authorized workflow change, permission to download the appliance artifact, or permission for one manual artifact-producing dispatch. F3 authorizes none of those actions.
- **C1:** remains FAIL; no two-pass run is authorized by F3.
- **OWNER-10:** remains FAIL independently.

## Next action

Authorize one bounded evidence-path correction that retains `hoardarr-offline-install-inputs` from the automatic push build without starting offline-install jobs, then rerun only the automatic build at that correction and resume F3 from the single artifact download and fresh F2-backed, `-nic none` simulation. Do not change the product roots, repository policy, package payload, installer, or C1 time bounds.
