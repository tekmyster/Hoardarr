# WO-APP-006 result

## Result

**BASELINE ARTIFACT VERIFIED; TWO-PASS OFFLINE INSTALL NOT VERIFIED.**

The pre-LINSTOR baseline now builds a complete signed/indexed Ubuntu 24.04 amd64 ISO-local APT repository, exact dependency closure, release bundle, evidence corpus, and bootable 4.15 GiB appliance ISO. The repository and release manifests were independently read back from the downloaded CI artifact. No live system, owner data, credential, pool, network, physical disk, or protected disk was mutated, and nothing was deployed.

The authoritative manual run built both CI-only no-network ISOs, but the harness was not executed because `tests/appliance/run-offline-iso-pass.sh` lacks an executable Git mode. Both jobs failed before QEMU installation or protected-media testing with exit `126` / `Permission denied`.

This evidence predates the owner-selected **LINSTOR + DRBD 9 + DRBD Reactor** three-host clustered-storage requirement added to the normative contract while run `32918144601` was already in flight. It contains no claimed LINSTOR/DRBD/Reactor package, kernel-module, Secure Boot/module-signing, or Proxmox-sidecar closure and therefore **cannot close the superseding OWNER-10 contract**.

Scoped implementation commits:

- `c0aa02a7de4acd9879a223f221eb8d67a5927820` — offline package/repository/install payload and no-network harness
- `0061c3c91f96d90054f312b7b2ec6fb3fb84f67e` — Noble `dstat` reconciliation to maintained `pcp`; release evidence
- `68b1bebcf2b1faa90d21d95ee69e405265568c89` — Noble generic-kernel payload reconciliation
- `9a13360dc6d9ba5b4d309c9b311a99bd1fb3473a` — field-name parsing of authoritative Debian control metadata
- `5ff65d186802cc14cb446c73ab592d11b7567639` — keep ephemeral CI evidence outside clean release source
- `ae3a567111536733e219da70857c5e31f2ac773b` — omit only absent platform-optional npm packages from installed-payload evidence
- `f2e3790f69e29e25172eab6394883f806c520542` — safe cleanup of read-only extracted ISO directories
- `d6fe6d040713744c5dba8cea999863fcdd5602a3` — restore retained offline input artifact under exact `dist/` paths

## Evidence

### Owner intake and disposition

- Workbook: `C:\Users\dmessana\Documents\Codex\Projects\Web\Hoardarr\Linux Packages needed.xlsx`
- Workbook SHA-256: `438991f1a7def5de709beea6337780baf50e2fd5e50f3a9229ef858d8186ed4c`
- Preserved intake: `Sheet1!4:50`, 47 package-candidate rows
- Pre-LINSTOR candidate dispositions: 76 included/installed; 33 included/feature-disabled; 15 checksum-pinned manual sidecars; 5 not supported
- Approved root packages: 109
- Resolved closure: 524 exact packages, architectures `all` and `amd64`, zero missing roots
- SnapRAID: `12.3-1/amd64`
- Repository CycloneDX components: 524
- Debian copyright/license files: 524

### Focused local validation

Final material-diff command:

```text
python -m unittest tests.bootstrap.test_manifests tests.release.test_release_bundle tests.release.test_appliance_assets tests.release.test_offline_appliance
python -m ruff check scripts/build-offline-apt-repository.py scripts/build-release-bundle.py tests/release/test_offline_appliance.py tests/release/test_release_bundle.py
```

Result: **47 passed, 1 platform-specific skip; Ruff passed**. The suite grew from the original 45 tests by two deterministic regression cases. Local `bash -n` was unavailable because this Windows host has no installed WSL distribution; Linux CI executed the appliance shell build successfully.

### Resolver/build progression

- `32914394902`: failed closed — Noble `dstat` is virtual; corrected to official `pcp` provider.
- `32914671117`: failed closed — no Noble `linux-modules-extra-generic`; corrected to `linux-image-generic`, whose closure includes matching versioned extra modules.
- `32914872158`: failed closed — labeled Debian control fields were parsed positionally; corrected to RFC822 field-name parsing.
- `32915143480`: signed repository/closure passed; release clean-source gate found workflow-created root evidence files.
- `32915330016`: repository/closure passed; release evidence rejected absent AIX optional npm package.
- `32915638020`: repository, closure, release and ISO write passed; purpose-created extracted tree cleanup failed on read-only directory modes.
- `32916046025`: **successful** normal build in 9m53s at `f2e3790`; repository, release, ISO composition, visible installer boot checkpoint and artifact upload all passed.
- `32917415162`: manual build passed; both no-NIC jobs failed before ISO build because retained inputs were restored outside `dist/`.
- `32918145045`: automatic push run intentionally cancelled by concurrency when the corrected manual run started.
- `32918144601`: build and retained-input stages passed; both no-NIC ISO builds passed; both harness executions failed before QEMU with non-executable script exit 126.

### Downloaded artifact readback

Successful baseline run `32916046025`:

- artifact ID: `9588405303`
- artifact name: `hoardarr-appliance`
- artifact API digest: `sha256:57312c454136838a28d41bae288f9aa16ba2a6978ffe2f0ee381fffc163577b5`
- downloaded evidence directory: `C:\Users\dmessana\AppData\Local\Temp\hoardarr-wo-app-006-run-32916046025`
- ISO bytes: `4,460,380,160`
- recorded and independently calculated ISO SHA-256: `bd8394c9ee2030bd68f32639475cd47b9aef652b4be906102effb49cb702bbce`
- ISO tree entries: 1,931
- repository extracted from ISO to: `C:\Users\dmessana\AppData\Local\Temp\hoardarr-wo-app-006-iso-32916046025\hoardarr\offline-repository`
- repository `SHA256SUMS` tree verification: passed
- workflow repository signature verification: passed via `gpgv`
- CI-only signing fingerprint: `E23FC9033465C4CB37ED7ED06C783B35B96493E5` (ephemeral CI evidence identity, not a production key)
- release extracted to: `C:\Users\dmessana\AppData\Local\Temp\hoardarr-wo-app-006-release-32916046025`
- release `SHA256SUMS`: 291 files, all independently verified
- release provenance source commit: `f2e3790f69e29e25172eab6394883f806c520542`
- locked input digests: `backend/uv.lock` `2361e9ff0458c625c592abbcf298c389f492492665b7dc9a0e8fe93ec9801c82`; `frontend/package-lock.json` `8ead8f624780787669d84c6364fef687d06538765fff5b009caaed59e3554511`

Manual run `32918144601` artifacts:

- `hoardarr-appliance`: ID `9589046742`, digest `sha256:e7a6a8696aba5f18dace8c2c2ac53906d08c9dd23626595219c467c25b44cccb`
- `hoardarr-offline-install-inputs`: ID `9589050641`, digest `sha256:167d3db37097b921b6e5fc492965fde0bf645f512f1bb957741340fd5107fc13`
- `hoardarr-offline-pass-1`: ID `9589097995`, digest `sha256:2777b59fb99930f29e94ff4ff89f5319106cab508fcc6f7d611c0ee48c6f3aef`
- `hoardarr-offline-pass-2`: ID `9589082080`, digest `sha256:fbf3915bf3c6c6822fd342babaa81ff907b70d3ebfbebb756335b4b61cd815b3`
- downloaded pass evidence: `C:\Users\dmessana\AppData\Local\Temp\hoardarr-wo-app-006-manual-32918144601`
- pass-1 CI-only ISO SHA-256: `85fe611545aa782c97db9d7371b823174cb8c082cc145a670fa2a1ddfd00f20a`
- pass-2 CI-only ISO SHA-256: `15cd5a7be3c87bb340871d54df3fbe0b78087deb047fe0a7387a8a74742c5c27`
- both tree manifests: 1,931 entries; identical tree-manifest SHA-256 `d678a56dad91a073a6e486adc3868c645605c25a71b45e5d1946966ca5916a1d`
- no offline install evidence exists because neither harness started

### Implemented baseline safety/ordering

- Autoinstall uses `fallback: offline-install`, `geoip: false`, and installs the verified local package payload before the Hoardarr release.
- The retained repository is copied to `/opt/hoardarr/offline-repository` for offline repair/activation.
- External APT sources are recoverably disabled and the installed source is `file:` only.
- Package maintainer scripts run behind `policy-rc.d` and temporary systemd masks.
- MD autoassembly is denied, multipath is strict/blacklist-all, and LVM discovery is reject-all during package installation.
- The transaction is simulated first and rejects removal/downgrade; install uses `--no-download --no-install-recommends`.
- Package/version/architecture and service-policy readbacks are produced; denied optional/mutation-capable units remain disabled until configured.
- Production user-data keeps explicit OS-disk selection; test-only user-data binds an exact disposable serial and the harness defines two read-only protected marker disks.

## Defects

1. `tests/appliance/run-offline-iso-pass.sh` is not executable in the Git checkout. Both no-NIC jobs failed with exit `126` before QEMU; no installation, package readback, service readback, protected-disk hash comparison, WebUI readiness, or first-boot measurement was executed.
2. The pre-LINSTOR ISO/repository is not a valid closure of the now-superseding clustered-storage contract. LINSTOR, DRBD 9, DRBD Reactor, required kernel/module/Secure-Boot handling and Proxmox-sidecar disposition remain unevidenced.
3. The CI repository uses an explicit ephemeral test signing identity. A production repository signing key remains an external release input and was not invented or embedded.

## Blockers

- Supervisor explicitly froze package changes and reruns after the LINSTOR/DRBD/Reactor requirement arrived, requiring this baseline to stop for QA.
- Full WO-APP-006/OWNER-10 acceptance remains blocked until a successor incorporates the new clustered-storage closure and the executable harness correction, then executes two clean no-NIC installations and the remaining offline provider/upgrade/rollback gates.

## Next action

Supervisor accepts this pre-change baseline and issues the narrow successor authorization to reconcile LINSTOR + DRBD 9 + DRBD Reactor (including kernel, Secure Boot/module signing and Proxmox sidecar disposition), restore the harness executable Git mode, and run the superseding two-pass no-network validation exactly once.
