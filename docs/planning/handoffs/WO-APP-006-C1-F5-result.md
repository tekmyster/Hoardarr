# WO-APP-006-C1-F5 result

## Result

- **Causal proof: PASS.** Both retained F4C clean-pass transcripts reached the verified signed local repository and an accepted 109-root solver plan, then the actual install exited `100` with `E: Internal Error, Pathname to install is not absolute 'coreutils_9.4-3ubuntu6.2_amd64.deb'`. The archive cache was empty. Ubuntu Noble's `apt-get(8)` documents `--no-download` as disabling package downloads and restricting APT to already-downloaded `.deb` files: <https://manpages.ubuntu.com/manpages/noble/man8/apt-get.8.html>.
- **Source correction: PASS.** The one production change removes `--no-download` only from the actual package-install invocation. It retains `--yes`, `--no-install-recommends`, the isolated signed `file:` source, all 109 exact roots, retry/proxy guards, service-start denial, storage guards, compatibility families, simulation, readback, audit, and final checks.
- **Executable production-argv regression: PASS.** The test extracts and executes the production fragment, verifies the exact argv and exact supplied roots, and rejects network-source, signed-by, signature, retry/proxy, root, service/storage-guard, and reintroduced-`--no-download` mutations.
- **Real local-file actual-install integration: PASS on Linux CI.** The disposable test creates and signs an ephemeral local repository, starts with an empty APT archive cache, configures no network source and false HTTP/HTTPS proxies, proves the obsolete `--no-download` invocation fails, executes the exact corrected production fragment, observes acquisition from the signed `file:` repository, and reads back `installed\t1.0\tall`. Cleanup purges the disposable test package.
- **Focused/full QA: PASS.** Four focused tests passed with the Linux-only test skipped on Windows; the complete release directory passed 55 tests with two expected platform skips. Bash syntax, Ruff check/format, Python compilation, YAML parsing, plan validation, and `git diff --check` passed.
- **Automatic workflows/build: PASS at final implementation head.** Push-triggered CI run `32977106512` passed all seven jobs. Push-triggered appliance run `32977106643` passed shared build job `98204404085`; manual-only `offline-install` job `98207598636` was skipped with zero steps.
- **C1: FAIL.** F5 does not execute or supersede the failed ordinary two-pass no-NIC installation gate.
- **OWNER-10: FAIL.** The deferred LINSTOR + DRBD 9 + DRBD Reactor + installed-but-disabled LINSTOR Gateway and offline Proxmox-plugin sidecar closure is not part of F5.

## Identities and scope

- Required starting local/origin head: `c7bfb10ab541d8c062ffd96f970a15ae83addb08`.
- Work-order SHA-256: `05eaac5ab87fe0fe288c1702429d127a7f40afb9c087a4e7047BEAA8E92DF480`.
- F4C handoff SHA-256: `99cfa7cedaafd16f610b02bf9abf2c3137629c9d749a4e2bb97b825bb1935040`.
- Production/test implementation commit: `8f7c608e994813271d5f5ced4fcee1a2c6cea037`.
- Narrow CI-test evidence correction commit: `aa523803da12636a28eb0a5a22681e5e7a2b101d`.
- Final implementation local/origin head before this handoff: `aa523803da12636a28eb0a5a22681e5e7a2b101d`.
- Handoff commit: the dedicated commit containing this document; its exact identity is reported in the terminal delivery because a Git commit cannot contain its own hash.

Only these implementation paths changed from the required starting head:

```text
packaging/appliance/install-offline-payload.sh
tests/appliance/test-local-file-apt-install.sh
tests/release/test_offline_appliance.py
```

Final file SHA-256 values:

```text
3434c06fa483167efcbb96ab7dbfa89ae2e9d55943f1c77bb35293c59a8c25bf  packaging/appliance/install-offline-payload.sh
0414af8969eeed0da0239f276483e06a0216f0a9694eb5fbe0be712885ff40e1  tests/appliance/test-local-file-apt-install.sh
4dfbb6ba43ab92650a9ebeb4f03fbc47cd4711b5c2119be8bdf51c9ddf36e43b  tests/release/test_offline_appliance.py
```

The inherited dirty/untracked worktree was preserved and excluded from both scoped implementation commits.

## Actual-install correction

Before:

```bash
chroot "$target" apt-get "${apt_options[@]}" \
  --yes --no-download --no-install-recommends install "${exact_roots[@]}"
```

After:

```bash
chroot "$target" apt-get "${apt_options[@]}" \
  --yes --no-install-recommends install "${exact_roots[@]}"
```

No update, simulation, final-check, source-list, package policy, package version, workflow, unattended-data, installer bound, or VM geometry was changed.

## Integration evidence

The executable Linux fixture uses:

- a uniquely named disposable `Architecture: all` package at version `1.0`;
- an ephemeral GPG signing identity and exported keyring used by an exact `signed-by=` source;
- an isolated `file:` repository with `InRelease`, `Release`, and `Release.gpg`;
- `Dir::Etc::sourcelist` pointing only to that source and `Dir::Etc::sourceparts=-`;
- `Acquire::Retries=0`, `Acquire::http::Proxy=false`, and `Acquire::https::Proxy=false`;
- an initially empty disposable archive cache;
- a deliberate old-command execution that must return nonzero and contain the relative-path install error;
- the exact production install fragment, whose captured APT output must identify the fixture `file:` repository;
- `dpkg-query` and an installed marker for final package readback.

The first pushed CI run exposed one test-only defect: successful APT installation does not promise retention of the fetched `.deb` in the archive cache. CI run `32976142173` failed only the integration assertion in jobs `98201229731` and `98201230036`; its stderr showed package installation/post-install activity. Appliance run `32976142111` independently passed its build and boot checkpoint, with `offline-install` skipped. The follow-up commit removed only that obsolete post-install cache-retention assertion and instead proves acquisition from the captured corrected APT output. No production code changed in the follow-up.

## Validation

```text
backend/.venv/Scripts/python.exe -m unittest \
  tests.release.test_offline_appliance.OfflineApplianceTests.test_offline_installer_has_independent_service_and_storage_guards \
  tests.release.test_offline_appliance.OfflineApplianceTests.test_actual_install_argv_executes_exact_production_fragment \
  tests.release.test_offline_appliance.OfflineApplianceTests.test_actual_install_contract_rejects_safeguard_regressions \
  tests.release.test_offline_appliance.OfflineApplianceTests.test_signed_local_file_repository_actual_install
PASS: 4 executed; 1 expected Windows platform skip

backend/.venv/Scripts/python.exe -m unittest tests.release.test_offline_appliance
PASS: 27 executed; 1 expected platform skip

backend/.venv/Scripts/python.exe -m unittest discover -s tests/release -p 'test_*.py'
PASS: 55 executed; 2 expected platform skips

C:\Program Files\Git\bin\bash.exe -n tests/appliance/test-local-file-apt-install.sh
PASS

backend/.venv/Scripts/ruff.exe check tests/release/test_offline_appliance.py
PASS

backend/.venv/Scripts/ruff.exe format --check tests/release/test_offline_appliance.py
PASS

backend/.venv/Scripts/python.exe -m compileall -q tests/release/test_offline_appliance.py
PASS

uv run --with pyyaml python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/appliance.yml').read_text(encoding='utf-8'))"
PASS

git diff --check
PASS (line-ending notices only for inherited dirty files)
```

Generated-plan invariants at the final implementation head:

```text
candidates: 129
included product roots: 109
compatibility families: 2
systemd-noble members: 11
linux-meta-noble members: 3
systemd-noble accepted version: 255.4-1ubuntu8.17
linux-meta-noble accepted version: 6.8.0-138.138
```

## Automatic workflow evidence

Final implementation head `aa523803da12636a28eb0a5a22681e5e7a2b101d` triggered only automatic push workflows:

| Workflow | Run | Result | Evidence |
|---|---:|---|---|
| CI | `32977106512` | PASS | All seven jobs passed: installed-appliance-smoke `98204403639`, central-fleet-postgres `98204403643`, ubuntu-installer `98204403800`, minio-control-plane-backup `98204403848`, frontend `98204403851`, release-bundle-systemd `98204403880`, backend `98204403942`. The release job executed all 55 tests including the Linux APT integration. |
| Build appliance ISO | `32977106643` | PASS | Shared build job `98204404085` passed every step, including repository build/verification, ISO build, and visible installer checkpoint. `offline-install` job `98207598636` was skipped and has `steps: []`. |

No workflow was manually dispatched, retried, or rerun. No artifacts were downloaded.

Automatically retained metadata-only input artifact:

```text
id: 9610264160
name: hoardarr-offline-install-inputs
size: 1,072,360,496 bytes
digest: sha256:7e8ab4cc227f6d16d259433c5784778813afbf70928fca8d46177b2c28917837
created: 2026-08-26T14:04:56Z
expires: 2026-08-29T14:04:49Z
expired: false
run: 32977106643
head: aa523803da12636a28eb0a5a22681e5e7a2b101d
downloaded: no (GitHub API metadata readback only)
```

## Defects and blockers

- The first CI attempt's cache-retention assertion was invalid and was corrected without changing production behavior. This is fully closed at `aa523803...`.
- C1 remains failed because F5 did not execute the separately reviewed ordinary two-pass no-NIC install gate.
- OWNER-10 remains failed because its complete LINSTOR/DRBD/Reactor/Gateway and Proxmox-sidecar closure has not been implemented or validated.
- GitHub emitted only non-blocking warnings about Node.js 20 action deprecation and one inherited MinIO Go-cache miss; all final jobs passed.

## Next action

Supervisor QA should independently review the two scoped implementation commits and final automatic evidence. If accepted, the smallest successor is a separately authorized ordinary retry-disabled two-pass no-NIC C1 run using the newly retained inputs; it is not authorized by F5 itself. OWNER-10 remains a separate later gate.
