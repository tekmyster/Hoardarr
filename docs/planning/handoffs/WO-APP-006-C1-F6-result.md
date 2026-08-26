# WO-APP-006-C1-F6 result

## Result

- **Retained F5A failure reproduction: PASS.** Both retained ordinary-pass serial captures again show 283 acquisitions from `file:/opt/hoardarr/offline-repository`, zero HTTP/HTTPS references, zero former relative-path acquisition errors, 82 `/proc/ is not mounted` diagnostics, the first `pcp` configuration failure, the later `linux-image-6.8.0-138-generic` failure, exact payload status `100`, and complete failure capture.
- **F6 implementation: IMPLEMENTED, NOT ACCEPTED.** Commit `cd074fd9296235c68dcb6c61725b87f38d1ccd03` adds a private mount-namespace transition and bounded `/proc`, `/sys`, `/dev`, `/dev/pts`, and `/run` target-chroot lifecycle. It does not change package roots, compatibility families, repository policy, networking, QEMU, workflow, unattended data, or application code.
- **Path/mount preflight: FAIL for acceptance.** The implementation validates real directories beneath the target, rejects symlinks/special files/unexpected mounts from `/proc/self/mountinfo`, records created mount identity, and has negative coverage. The automatic Linux run exposed one real fail-closed defect in the first bind-failure path, so the complete gate is not accepted.
- **Propagation containment: IMPLEMENTED; NOT ACCEPTED as a complete lifecycle.** The transition no longer trusts caller-controlled environment sentinels: it clears them, carries the parent mount namespace through an inherited descriptor, requires the child namespace identity to differ, and rejects shared/master/propagate-from root propagation. The complete executable lifecycle nevertheless fails later at bind-failure injection.
- **Exact runtime availability: IMPLEMENTED; NOT ACCEPTED.** Disposable-package/post-install and kernel-hook-equivalent probes cover all five paths, but the single Linux lifecycle test is terminal failure and cannot be reported as an overall pass.
- **Success cleanup: IMPLEMENTED; NOT ACCEPTED.** The intended successful path performs exact reverse, non-lazy unmounts and preserves a five-row cleanup receipt; empty-tracker and repeated `cleanup_guard 0` calls are idempotent. The encompassing Linux test failed.
- **Partial/failure/signal cleanup: FAIL.** CI proved that an injected failure of the first `mount --bind` can fall through when `prepare_runtime_mounts` is called in an OR-list, after which `/proc` is incorrectly treated as tracked and cleanup reports `tracked offline runtime mount disappeared`. The function depends on ambient Bash `errexit` instead of explicitly checking the mutation command.
- **Original-status preservation: IMPLEMENTED; NOT ACCEPTED.** Tests cover work failure, signal mapping, and cleanup-status aggregation, but the complete Linux harness failed before acceptance.
- **Service/storage/offline invariants: PASS at source/build scope.** Existing signed-by local-only APT, 109 roots, both compatibility families, service-start denial, mask/alias lifecycle, final inactive-unit checks, and MD/multipath/LVM activation guards were not altered. No fresh ordinary installation was authorized, so runtime installation acceptance remains pending.
- **Local focused QA: PASS.** Three focused Windows-executable/static release tests passed; Bash syntax, Ruff check/format, Python compile, YAML parse, package plan checks, and `git diff --check` passed.
- **Local full release QA: PASS with platform boundary.** `python -m unittest discover -s tests/release -p 'test_*.py' -v` ran 57 tests successfully with 3 expected platform skips, including the Linux-mount integration on Windows.
- **Automatic CI: FAIL.** Run `32990166031` at exact head `cd074fd...` completed failure. Jobs `release-bundle-systemd` (`98245609173`) and `backend` (`98245609395`) independently failed the same Linux lifecycle test; the other five jobs passed.
- **Automatic appliance shared build: PASS.** Run `32990165940`, job `98245608218`, completed successfully at the exact implementation head.
- **Manual-only offline-install skip: PASS.** Job `98252511262` was skipped and has zero steps. No manual workflow was dispatched.
- **Artifact metadata: PASS, metadata only.** Artifact identities were read from the API and neither artifact was downloaded.
- **Source scope: PASS.** The implementation commit changes only the production offline payload and the two smallest directly coupled test files. Inherited dirty/untracked files remain preserved.
- **C1: FAIL.** F6 did not pass automatic CI and no fresh ordinary two-pass installation was authorized.
- **OWNER-10: FAIL.** The separate LINSTOR, DRBD 9, DRBD Reactor, installed-but-disabled LINSTOR Gateway, kernel/Secure-Boot, and offline Proxmox-plugin sidecar gates remain unresolved.

## Evidence

### Authority and source identity

- Work-order SHA-256: `6141a79612ea61e3751ba056c9b25f4308e61325d63b708526d004d7878a6a7f`.
- F5A handoff SHA-256: `491cfe633c7fc0e3ab85cd42d497189bb99bbc09c4bbb2aa0be3cd4415e3c4be`.
- Required and observed starting local/origin head: `1b8e634a6327bcccc269058e30eddb6c4373903f`.
- Implementation commit: `cd074fd9296235c68dcb6c61725b87f38d1ccd03` (`fix: prepare isolated target chroot runtime mounts`).
- Implementation diff:
  - `packaging/appliance/install-offline-payload.sh`: modified.
  - `tests/appliance/test-target-chroot-runtime-mounts.sh`: added, Git mode `100755`.
  - `tests/release/test_offline_appliance.py`: modified.
  - Total: 913 insertions, 10 deletions.

The design uses the kernel's per-process mount namespace and `/proc/self/mountinfo`, rather than human-formatted `mount` output. The isolation and identity assumptions follow the Linux mount-namespace and mountinfo contracts: [mount_namespaces(7)](https://man7.org/linux/man-pages/man7/mount_namespaces.7.html), [proc_pid_mountinfo(5)](https://man7.org/linux/man-pages/man5/proc_pid_mountinfo.5.html), and [mount(8)](https://man7.org/linux/man-pages/man8/mount.8.html).

### Retained failure evidence

The unchanged retained F5A evidence establishes the pre-correction product failure on both independent passes:

| Evidence | Pass 1 | Pass 2 |
|---|---:|---:|
| local `file:` acquisitions | 283 | 283 |
| HTTP/HTTPS references | 0 | 0 |
| former relative-path errors | 0 | 0 |
| `/proc/ is not mounted` | 82 | 82 |
| first `pcp` configuration failure | 1 | 1 |
| later kernel-image configuration failure | 1 | 1 |
| exact payload exit | 100 | 100 |

This establishes why target-chroot runtime preparation is needed without rerunning or mutating the retained images.

### Runtime-mount lifecycle implemented

The scoped implementation:

1. Rejects the internal re-exec argument on ordinary caller entry, clears the former environment sentinels, opens the parent mount namespace as an inherited descriptor, and re-execs through `unshare --mount --propagation private`.
2. Requires the inherited parent namespace identity to differ from `/proc/self/ns/mnt` and rejects propagation tags on the child root mount.
3. Restricts sources/destinations to `/proc`, `/sys`, `/dev`, `/dev/pts`, and `/run`; validates each destination's real path/type and pre-existing mount state from mountinfo before mutation.
4. Performs a nonrecursive bind, records a verifiable created mount immediately, makes it private, re-reads exact identity, and verifies source/root/type/propagation before any chroot operation.
5. Keeps the runtime context across update, simulation, actual install, dpkg/package checks, and final unit disable/readback.
6. Unmounts only exact recorded mount IDs in reverse order with ordinary `umount`, verifies absence, writes a durable cleanup receipt, and makes later empty cleanup a non-truncating no-op.
7. Integrates runtime and service-mask cleanup into the existing single EXIT/signal lifecycle.

The executable test includes the Supervisor-requested seams: initial record/ID capture, `--make-private`, post-private record/identity validation, all five partial-bind positions, work failure, TERM, cleanup failure, symlink/non-directory/pre-existing mount rejection, propagation containment, cleanup-receipt preservation, and caller-preseeded sentinel rejection. It also builds a disposable local package whose post-install action reads all five runtime paths.

### Local validation

The following scoped checks completed successfully before the implementation commit:

```text
backend/.venv/Scripts/python.exe -m unittest discover -s tests/release -p 'test_*.py' -v
Result: 57 tests passed; 3 expected platform skips.

backend/.venv/Scripts/ruff.exe check tests/release/test_offline_appliance.py
Result: PASS.

backend/.venv/Scripts/ruff.exe format --check tests/release/test_offline_appliance.py
Result: PASS.

bash -n packaging/appliance/install-offline-payload.sh tests/appliance/test-target-chroot-runtime-mounts.sh tests/appliance/test-local-file-apt-install.sh
Result: PASS.

python -m compileall tests/release/test_offline_appliance.py
Result: PASS.

uv run --with pyyaml python -c <workflow YAML parse check>
Result: PASS.

git diff --check
Result: PASS; only inherited line-ending warnings were emitted.
```

Focused static/executable checks for the runtime contract, the preseeded-sentinel launcher, and directly coupled service/alias behavior passed locally. The full mount lifecycle is Linux-only and was skipped on Windows; it is therefore the automatic Linux result below—not the local static result—that controls acceptance.

### Automatic CI result

Run `32990166031`:

- Event: `push`.
- Exact head: `cd074fd9296235c68dcb6c61725b87f38d1ccd03`.
- Created: `2026-08-26T16:45:04Z` after the GitHub Actions major-outage delay.
- Completed: `2026-08-26T16:48:18Z`.
- Conclusion: **failure**.
- URL: `https://github.com/tekmyster/Hoardarr/actions/runs/32990166031`.

Both failing jobs ran 57 release tests and failed the same one assertion:

```text
FAIL: test_target_runtime_mount_lifecycle_and_package_postinst
AssertionError: 1 != 0 : tracked offline runtime mount disappeared: .../bind-failure-1/proc
Ran 57 tests
FAILED (failures=1)
```

| Job | ID | Conclusion |
|---|---:|---|
| release-bundle-systemd | `98245609173` | failure |
| backend | `98245609395` | failure |
| installed-appliance-smoke | `98245609331` | success |
| central-fleet-postgres | `98245609387` | success |
| ubuntu-installer | `98245609424` | success |
| frontend | `98245609476` | success |
| minio-control-plane-backup | `98245609509` | success |

Root cause: the test invokes `prepare_runtime_mounts` in an OR-list to assert failure. Bash disables `errexit` in that function context. The injected `mount --bind` returns `71`, but the unchecked command falls through into mount tracking. Cleanup then correctly notices that the supposedly tracked mount does not exist. Relying on caller `set -e` is not a fail-closed command contract.

### Automatic appliance result and artifacts

Run `32990165940`:

- Event: `push`.
- Exact head: `cd074fd9296235c68dcb6c61725b87f38d1ccd03`.
- Created: `2026-08-26T16:45:04Z`.
- Completed: `2026-08-26T17:07:13Z`.
- Conclusion: **success**.
- Build job `98245608218`: success (`16:45:07Z` to `17:07:12Z`).
- Offline-install job `98252511262`: skipped, zero steps.
- URL: `https://github.com/tekmyster/Hoardarr/actions/runs/32990165940`.

| Artifact | ID | API bytes | API digest | Created / expires | Disposition |
|---|---:|---:|---|---|---|
| `hoardarr-offline-install-inputs` | `9615063634` | `1,072,357,750` | `sha256:6b42f39788968a1e7bdae5f5efeaddee230a3f53c7823e721ee8e94e28b38fa2` | `2026-08-26T17:07:08Z` / `2026-08-29T17:06:57Z` | metadata only; not downloaded |
| `hoardarr-appliance` | `9615056823` | `4,408,881,954` | `sha256:3a5645c555aefac56b70316e28e39dd180bb3055c4076d7a817b4eda8a3bf1b4` | `2026-08-26T17:06:56Z` / `2026-11-24T16:45:04Z` | metadata only; not downloaded |

The appliance shared build passing proves that the signed repository, release bundle, ISO construction, installer boot checkpoint, and artifact-retention paths still build. It does not override the failed Linux lifecycle test or prove an ordinary offline installation.

## Defects

The sole newly proven implementation defect is reliance on ambient Bash `errexit` for mutation commands inside `prepare_runtime_mounts`. A shell function can execute with `errexit` disabled when called from conditional/OR-list contexts. Consequently, a failed bind may fall through and be treated as created. This violates F6's fail-closed partial-setup requirement even though the current production top-level call is plain under `set -e`.

No correction, retry, rerun, manual dispatch, artifact download, or adjacent work was performed after this automatic failure.

## Blockers

- **F6 acceptance:** blocked by the explicit-command-check defect above.
- **C1:** remains failed until the narrow correction passes automatic Linux CI and a separately authorized fresh ordinary two-pass installation passes.
- **OWNER-10:** remains independently failed pending the complete offline LINSTOR/DRBD 9/DRBD Reactor/installed-but-disabled LINSTOR Gateway, kernel/Secure-Boot, and Proxmox-plugin sidecar gates.

## Next action

Authorize one narrow F6 follow-up that changes every mutating command in `prepare_runtime_mounts` from ambient-`errexit` reliance to explicit checked failure handling, while preserving the mount-ID, reverse-cleanup, no-lazy-unmount, namespace, source, package, service, and storage safeguards. The executable Linux test must prove each injected mutation failure returns fail-closed and leaves no mount, including invocation from an OR-list. Then run focused/full local QA and automatic push workflows only. Do not authorize a two-pass C1 run until that correction is accepted.
