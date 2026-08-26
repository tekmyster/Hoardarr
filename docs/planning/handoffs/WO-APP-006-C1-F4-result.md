# WO-APP-006-C1-F4 result

## Result

- **Authoritative metadata proof: PASS.** The official Ubuntu Noble amd64 archive metadata had one common candidate, `6.8.0-138.138`, for `linux-generic`, `linux-image-generic`, and `linux-headers-generic`. All three are `Architecture: amd64`, source `linux-meta`; `linux-generic` declares exact `=` dependencies on both siblings at that same version.
- **Implementation: PASS.** A declarative `linux-meta-noble` compatibility family now closes and verifies the three version-coupled binary identities without adding product roots. The existing `systemd-noble` family and all package/service/product profiles remain intact.
- **Focused/full QA: PASS.** Focused F4 tests, the complete offline-appliance test file, and the complete release test directory passed. Ruff, Ruff format, Python compilation, policy JSON parsing, plan generation, and diff validation passed.
- **Automatic build: PASS.** Push-triggered run `32962854775`, exact source commit `226a7c25c5eda353cc85b18e638a1c58962e0f54`, completed successfully. Build job `98158833897` passed all steps.
- **Retained artifact: PASS.** `hoardarr-offline-install-inputs` artifact `9604813277` exists with exact API readback below. It was not downloaded.
- **F4: PASS.** The source/build/artifact gates authorized by F4 are satisfied.
- **C1: FAIL.** F4 did not authorize and did not execute a retained-base solver rerun or ordinary two-pass installation.
- **OWNER-10: FAIL.** No LINSTOR, DRBD 9, DRBD Reactor, LINSTOR Gateway, kernel/Secure-Boot, or Proxmox-sidecar work was performed.

## Evidence

### Authority and source state

- Work order: `docs/planning/work-orders/WO-APP-006-C1-F4.md`.
- Required/observed work-order SHA-256: `f8e5fd973ede3cae807f98b3d87baa28c514feac8917bbbb8e87fb53a79bc1ac`.
- Required/observed starting local and origin HEAD: `b937fe6a65c47d048ed4c9dc21f1bb7e1da414b3`.
- Required/observed F3H handoff SHA-256: `87fe935aaef5098caa18c1e63fb595cff6f8c9d9867c04b3bbd7a6f3c5e8ff7a`.
- Implementation commit: `226a7c25c5eda353cc85b18e638a1c58962e0f54` (`Close Noble linux-meta offline compatibility family`).
- Implementation commit was pushed once; local and `origin/rc/0.3.11-validation` both read `226a7c25c5eda353cc85b18e638a1c58962e0f54` before the handoff commit.

The implementation commit contains only:

```text
17   0  packaging/offline/package-policy.json
184 37  scripts/build-offline-apt-repository.py
346 55  tests/release/test_offline_appliance.py
```

Post-implementation file identities:

```text
packaging/offline/package-policy.json
  a12136ea35c83afe3335964b6fda03de5a11850d62263d689e7deae22157a221
scripts/build-offline-apt-repository.py
  0923858f33fb79ed3d9cc4ebbbf215c336d44b392b07578b4cedf8bdd9ca89c4
tests/release/test_offline_appliance.py
  b8b1167d672d41f979baeb4f4caebd601846511702d55ee26f5d7563b4211e2
```

All unrelated inherited dirty/untracked files were preserved and excluded from both commits.

### Pre-change authoritative Noble metadata

The metadata gate used the official amd64 `Packages.xz` indexes served over HTTPS from:

```text
https://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64/Packages.xz
https://archive.ubuntu.com/ubuntu/dists/noble-updates/main/binary-amd64/Packages.xz
https://security.ubuntu.com/ubuntu/dists/noble-security/main/binary-amd64/Packages.xz
```

Retained source/index identities:

```text
noble Packages.xz       2a6a199e1031a5c279cb346646d594993f35b1c03dd4a82aaa0323980dd92451
noble Packages          8f6f71ae839c8cba390a7643fcbbdacddb0bc7d12c1583a2dd80a1f8443a30e5
updates Packages.xz     82aa274192eb0d271d828c64563056f7105563726755c19fe0b94de955dddf37
updates Packages        4987518abfbeaa0fc75cc512d8dabb32e1044fc1a322785ac64d5680e002befa
security Packages.xz    c4ffce44904e99d06b2b971938e45e023fa02527d617803942f1187ba4c889f5
security Packages       dd4a4a19c1e006a678ecdfcadb89e2d5d4f9910068186b360e69ebaf20975eab
```

The normalized three-suite metadata record SHA-256 is `3951475a089ad462bbd5c505bba1b1467b0cfcf9572a13c185c56809c3f2f85c`.

The Noble updates/security candidate records were exact:

```text
Package: linux-generic
Version: 6.8.0-138.138
Architecture: amd64
Source: linux-meta
Depends: linux-image-generic (= 6.8.0-138.138), linux-headers-generic (= 6.8.0-138.138)

Package: linux-image-generic
Version: 6.8.0-138.138
Architecture: amd64
Source: linux-meta
Depends: linux-image-6.8.0-138-generic, linux-modules-extra-6.8.0-138-generic, linux-firmware, intel-microcode, amd64-microcode

Package: linux-headers-generic
Version: 6.8.0-138.138
Architecture: amd64
Source: linux-meta
Depends: linux-headers-6.8.0-138-generic
```

The base Noble index also showed all three at `6.8.0-31.31`, with the same exact sibling relationship. The correction does not embed either observed numeric version; normal APT candidate resolution supplies the version.

### Family and dependency semantics

Policy now declares exactly two compatibility families:

1. Existing `systemd-noble`, unchanged: eleven members, `single-candidate-version`.
2. New `linux-meta-noble`: exactly `linux-generic`, `linux-image-generic`, and `linux-headers-generic`, `single-candidate-version`; declarative exact dependencies require `linux-generic` to depend on both sibling members.

Build and verify paths now fail closed on:

- unsafe or malformed family dependency declarations;
- a dependency source/target outside its family;
- differing candidate versions;
- missing or duplicate family binary identities;
- architecture other than target-compatible `amd64` or `all`;
- wrong downloaded/manifest/index version;
- missing, non-exact, wrong-version, alternative-only, malformed, or repeated required dependency clauses;
- package-manifest dependency drift during repository verification.

Alternatives are deliberately not treated as unconditional exact dependencies. Whitespace, multiarch qualifiers, architecture restrictions, and build-profile suffixes are parsed without weakening the exact `=` requirement. Family evidence remains schema-versioned, deterministic, and name/version-only; dependency proof is retained in and revalidated from the package manifest's declared control metadata.

### Root/profile invariants

- Product roots: exactly `109`, unchanged.
- Compatibility-matrix candidates: exactly `129`, unchanged.
- Package profile files changed: `0`.
- Product roots intersecting `linux-meta-noble`: exactly `{linux-image-generic}`.
- `linux-generic` and `linux-headers-generic` are compatibility closure only, not product roots or feature claims.
- Existing service enable/deny policy, package dispositions, profile membership, workflow, payload, unattended data, and appliance geometry changed: `0`.

### Local validation

Focused F4 behavior:

```text
python -m unittest \
  tests.release.test_offline_appliance.OfflineApplianceTests.test_compatibility_families_are_explicit_without_changing_product_roots \
  tests.release.test_offline_appliance.OfflineApplianceTests.test_compatibility_family_schema_rejects_unsafe_and_duplicate_values \
  tests.release.test_offline_appliance.OfflineApplianceTests.test_exact_dependency_parser_is_whitespace_and_alternative_safe \
  tests.release.test_offline_appliance.OfflineApplianceTests.test_linux_meta_dependency_validation_requires_exact_sibling_versions
PASS: 4/4
```

Complete applicable suites and static gates:

```text
python -m unittest tests.release.test_offline_appliance
PASS: 24/24

python -m unittest discover -s tests/release -p 'test_*.py'
PASS: 52/52 executed; 1 platform-specific skip

backend/.venv/Scripts/ruff.exe check \
  scripts/build-offline-apt-repository.py \
  tests/release/test_offline_appliance.py
PASS

backend/.venv/Scripts/ruff.exe format --check \
  scripts/build-offline-apt-repository.py \
  tests/release/test_offline_appliance.py
PASS: 2 files already formatted

python -m py_compile \
  scripts/build-offline-apt-repository.py \
  tests/release/test_offline_appliance.py
PASS

python -m json.tool packaging/offline/package-policy.json
PASS

python scripts/build-offline-apt-repository.py plan
PASS

git diff --check -- \
  packaging/offline/package-policy.json \
  scripts/build-offline-apt-repository.py \
  tests/release/test_offline_appliance.py
PASS
```

The first complete offline-appliance run exposed one systemd-only mock whose synthetic control record omitted `Depends`; the adapter was corrected to supply an empty string to dependency-free families. The complete suite was rerun clean after that final material change.

### Automatic GitHub Actions evidence

- Workflow: `Build appliance ISO` / `.github/workflows/appliance.yml`.
- Run: `32962854775`.
- URL: `https://github.com/tekmyster/Hoardarr/actions/runs/32962854775`.
- Trigger: `push` (no manual dispatch).
- Attempt: `1`.
- Exact head SHA: `226a7c25c5eda353cc85b18e638a1c58962e0f54`.
- Build job: `98158833897`, `success`, `2026-08-26T11:20:52Z` through `2026-08-26T11:29:53Z`.
- `Build and verify complete signed offline APT repository`: `success`, `11:21:31Z` through `11:22:59Z`.
- Release bundle, deterministic archive, ISO construction, and visible interactive-installer checkpoint: all `success`.
- `Retain offline install inputs for no-network validation`: `success`, `11:29:44Z` through `11:29:51Z`.
- Offline-install job: `98161163806`, conclusion `skipped`, `steps: []` (zero steps), proving it remained manual-only for the push.

Exact retained-input artifact API readback:

```text
id: 9604813277
name: hoardarr-offline-install-inputs
size_in_bytes: 1072360483
digest: sha256:a238d7d859a1686b8e9723e7bafc1495e9654e7311d0463ab5804dadb5777fe8
created_at: 2026-08-26T11:29:51Z
expires_at: 2026-08-29T11:29:45Z
expired: false
head_sha: 226a7c25c5eda353cc85b18e638a1c58962e0f54
```

The artifact was not downloaded. The `hoardarr-appliance` artifact was also not downloaded. No manual workflow was dispatched.

## Defects

- No F4 source/build/artifact defect remains within the authorized scope.
- The exact retained-base transaction has not been rerun with this repository, so F4 does not prove that the prior `linux-generic` removal is eliminated in the real retained guest.
- C1 and OWNER-10 remain failed.

## Blockers

- **C1:** blocked pending a separately authorized exact retained-base solver preflight using artifact `9604813277`, followed by any separately authorized ordinary two-pass no-NIC gate only if the solver is removal-free.
- **OWNER-10:** independently blocked on the deferred LINSTOR + DRBD 9 + DRBD Reactor + installed-disabled LINSTOR Gateway closure, kernel/Secure-Boot compatibility, and offline Proxmox-plugin sidecar requirements.

## Next action

Authorize one bounded retained-base solver preflight that downloads artifact `9604813277` at most once, verifies its API digest and signed/indexed repository, and runs exactly one unchanged 109-root no-install-recommends simulation against a fresh F2-backed offline overlay. Require status zero with no removals, purges, or downgrades and explicit preservation of `linux-generic`; do not begin C1 two-pass or OWNER-10 work unless that gate passes.
