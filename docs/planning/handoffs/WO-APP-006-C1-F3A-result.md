# WO-APP-006-C1-F3A result

## Result

- **Automatic offline-input artifact retention: PASS.** Automatic push run `32953633660` completed successfully at exact commit `87043c98a35c231288ef40a99620bd80a067c751`, uploaded exactly one `hoardarr-offline-install-inputs` artifact, and left the `offline-install` job skipped.
- **F3: PENDING.** F3A only produces the missing artifact. It did not download it or resume the retained-base solver preflight.
- **C1: FAIL.** No ordinary two-pass workflow was dispatched or run.
- **OWNER-10: FAIL.** No LINSTOR, DRBD 9, DRBD Reactor, LINSTOR Gateway, kernel/Secure-Boot, or Proxmox-sidecar work was performed.

No artifact was downloaded. No workflow was manually dispatched. No offline-install pass ran. No live system, VM, network, credential, pool, protected disk, package policy, repository builder, payload, installer, ISO input, or unrelated dirty worktree path was changed.

## Evidence

### Identity and scope

- Work order SHA-256: `5dc7b29f70635fd7de51993dd12c50267053a322a9d821e7318274b8046479c1`.
- Work order's implementation baseline: `865d917c62f920b26a6427d5bf2661f51a85c76b`.
- Observed starting local/origin HEAD: `2e8cd852ea381527a781886efd8ba9e561c33825`, consisting of the same implementation baseline plus the required F3 handoff evidence commit. No product/workflow change existed between those commits.
- F3A implementation commit: `87043c98a35c231288ef40a99620bd80a067c751`.
- Authorized implementation files only:
  - `.github/workflows/appliance.yml`
  - `tests/release/test_offline_appliance.py`

The only workflow behavior change removes the dispatch-only `if` from the existing `Retain offline install inputs for no-network validation` build step. It preserves:

- artifact name `hoardarr-offline-install-inputs`;
- exactly `dist/hoardarr-release.tar.gz` and `dist/offline-repository` as paths;
- compression level `0`;
- retention period `3` days;
- all workflow inputs, ISO behavior, concurrency, and manual validation modes;
- the job-level `offline-install` gate `if: github.event_name == 'workflow_dispatch'`.

### Local validation

```text
backend\.venv\Scripts\python.exe -m unittest tests.release.test_offline_appliance tests.bootstrap.test_manifests
29 tests passed

backend\.venv\Scripts\ruff.exe check tests/release/test_offline_appliance.py
All checks passed
```

Additional validation:

- `uv run --with pyyaml==6.0.2 python -` parsed `.github/workflows/appliance.yml` and asserted:
  - the retention step exists exactly once and has no event gate;
  - the artifact paths remain exactly the release archive and offline repository;
  - `offline-install` remains strictly `workflow_dispatch`-only.
- `git diff --check`: passed.
- Source review confirmed no automatic pass matrix or offline-install execution path was introduced.

### Automatic run

- Workflow: `Build appliance ISO`.
- Event: `push` (automatic).
- Run: `32953633660`.
- Head SHA: `87043c98a35c231288ef40a99620bd80a067c751`.
- Build job: `98130395836`.
- Build job start: `2026-08-26T09:33:29Z`.
- Build job completion: `2026-08-26T09:41:47Z`.
- Build conclusion: `success`.
- Signed repository build/verification: success.
- Release bundle: success.
- ISO construction: success.
- Interactive installer checkpoint: success.
- `Retain offline install inputs for no-network validation`: success, `2026-08-26T09:41:38Z` to `09:41:44Z`.
- `offline-install` job: `98132662989`, conclusion `skipped`, zero steps.

The run produced exactly two artifacts:

1. Required F3 input artifact:
   - ID: `9601372214`.
   - Name: `hoardarr-offline-install-inputs`.
   - Size: `1,054,964,607` bytes.
   - Digest: `sha256:87c90870111cb81cb2aefc262874de188a50c662cbe4c0d03fe42780db42929a`.
   - Created/updated: `2026-08-26T09:41:44Z`.
   - Expiry: `2026-08-29T09:41:39Z`.
   - Workflow head SHA: `87043c98a35c231288ef40a99620bd80a067c751`.

2. Existing appliance artifact:
   - ID: `9601368977`.
   - Name: `hoardarr-appliance`.
   - Size: `4,391,495,310` bytes.
   - Digest: `sha256:3593c038a5943a7c2332f8625ed04d03334436cb5f78bd21e2519823b464e8ab`.

Metadata was queried through the GitHub Actions API. Neither artifact was downloaded, opened, or extracted.

## Defects

- No F3A defect remains in the authorized retention behavior.
- F3's independent artifact readback and retained-base simulation remain deliberately unexecuted pending a separate authorization.
- C1 and OWNER-10 remain failed.

## Blockers

- F3 remains pending until Supervisor authorizes the single artifact download and retained-base no-network solver preflight.
- C1 remains FAIL; F3A authorizes no two-pass run.
- OWNER-10 remains FAIL independently.

## Next action

Authorize resumption of F3 at its existing artifact/preflight gate: download artifact `9601372214` exactly once, independently verify its digest and repository evidence, then run only the prescribed fresh F2-backed, `-nic none` APT simulation. Do not dispatch a workflow or perform an actual install.
