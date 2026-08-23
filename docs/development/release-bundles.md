# Release bundles

Hoardarr's first deployment format is a versioned Ubuntu release bundle. It
contains the Hoardarr wheel, every locked Python runtime dependency as a wheel,
the production-built web interface, the host dependency reconciler, declared
Ubuntu package profiles, the hardware detector and manifests, systemd units,
the default environment file, operational documentation, and a SHA-256
manifest.

This is deliberately narrower than the eventual bootable appliance image.  It
does not contain Ubuntu package payloads, and the application release installer
does not install storage packages, drivers, controller utilities, or the
operating system. The signed appliance image and future update orchestrator
must reconcile the bundled desired-state profiles before activating a release.
That reconciliation remains fail-closed until the disk-quarantine gates are
implemented. Prepare the host with the `build-host` bootstrap profile before
building, and provide Python 3.12 plus `python3-venv` on an installation target.

## Supported target

The current bundle target is fixed to:

- Ubuntu 24.04 amd64
- CPython 3.12
- systemd as PID 1

The builder must run on that same target.  This makes binary-wheel selection
predictable and prevents a successful build from quietly containing artifacts
for the build operator's Windows or macOS workstation.

The build host also needs the pinned Node.js 24.18 toolchain and `npm`. Node is
used only to produce static frontend assets; it is not required on the
installation target. Python dependencies are locked by `backend/uv.lock`, and
frontend dependencies are locked by `frontend/package-lock.json`.

## Build

From the repository root on the Ubuntu build host, the recommended flow is:

```sh
make verify
make release-plan
make release-build
```

The direct equivalents for the last two commands are
`python3 scripts/build-release-bundle.py plan` and
`python3 scripts/build-release-bundle.py build`.

`plan` is read-only and prints the exact versioned output path.  `build`:

1. asserts that `backend/uv.lock` is unchanged while exporting exact, hashed
   production requirements;
2. installs the exact frontend dependency graph with `npm ci` and builds the
   static web interface;
3. downloads binary wheels only (source distributions are rejected);
4. builds the Hoardarr wheel;
5. copies an explicit allowlist of release assets; and
6. writes `SHA256SUMS` over every bundle file.

The default output is `dist/releases/hoardarr-<version>-<lock-id>-ubuntu24.04-amd64-cp312`.
The lock ID covers both the backend and frontend lock files.
An existing destination is never overwritten; remove or archive it deliberately
before rebuilding the same release.

The build needs network access to the configured Python and npm registries
unless their artifacts are already available through the configured caches.
Installation does not need network access.

## Inspect and install

Copy the complete versioned directory to the target.  Do not copy only its
wheelhouse.  From the bundle root:

```sh
./scripts/install.sh plan
sudo ./scripts/install.sh apply --yes
```

Both modes verify that:

- every file is listed exactly once in `SHA256SUMS`;
- every listed digest matches;
- there are no unmanifested files, symbolic links, absolute paths, or `..`
  path components;
- release metadata and the host OS, architecture, and Python version agree;
- expected units, locks, assets, wheels, and `frontend/index.html` are present.

`plan` performs no writes and does not require root.  `apply --yes` is the only
mutating mode.

## Installation and upgrade behavior

Each bundle is installed under `/usr/lib/hoardarr/releases/<release-id>`.  A new
virtual environment is built in a private staging directory using only
`--no-index`, the bundled wheelhouse, and hashed requirements.  The active
`/usr/lib/hoardarr/current` link changes only after that environment imports
successfully. The prebuilt frontend is staged beside that environment and is
served by the API from the active release. Old version directories are retained
for diagnosis and manual rollback; the installer performs no broad cleanup.

The dependency reconciler, package profiles, detector, and hardware manifests
are co-versioned as `scripts/`, `packaging/packages/`, and
`packaging/hardware/` beneath that release root. These relative layouts are
part of the release contract; active compatibility links under
`/usr/lib/hoardarr` move with `current`.

The service account is a locked, non-login system account with no storage-device
groups. The separate root media-account executor exposes one typed operation
over a `root:hoardarr` Unix socket; it cannot access storage devices or the
network. Code and release assets are owned by root and are not writable by the
runtime account. Hoardarr state remains in `/var/lib/hoardarr`.

Legacy development benches that already use `hoardarr` as their interactive
administrator login may apply a release with the explicit
`--preserve-existing-login-account` option. The installer validates that the
account is non-root and owns the matching primary group, leaves its login and
supplementary groups unchanged, and emits a visible warning. Fresh appliances
must not use this compatibility option; they retain the locked system-account
default.

On first install, `/etc/hoardarr/hoardarr.env` comes from the bundle and binds
the API to `127.0.0.1`.  On every later install, an existing environment file is
preserved byte-for-byte.  Review an existing file yourself if it was previously
configured to listen on another interface.

The installer stages first, then stops the API and worker, reloads/enables the
units, and restarts the migration unit.  API and worker start only after the
migration succeeds.  A migration or runtime startup failure leaves both runtime
services stopped so an operator can inspect the journal.  The installer never
issues an owner setup token.

### Direct-to-latest package behavior

The bundle is complete rather than an incremental patch. An operator may
install the newest bundle directly over any release whose database schema is
still supported by the migration chain shipped in that newest bundle; no
intermediate application bundle is required. `hoardarr-migrate` upgrades to the
current Alembic head before either long-running service starts.

That guarantee is limited to starting schemas covered by committed migration
fixtures and release tests. The current installer is a manually supplied,
trusted-channel package-mode installer; it does not yet implement the web UI
update button, signed channel metadata, GitHub release download, RAUC image
switching, or automated rollback described in the update design.

Useful diagnostics are:

```sh
systemctl status hoardarr-migrate.service hoardarr-api.service hoardarr-worker.service
journalctl -u hoardarr-migrate.service -u hoardarr-api.service -u hoardarr-worker.service
```

## Integrity boundary

`SHA256SUMS` detects corruption and accidental mixing of files.  It does not
authenticate who produced a release: someone who can replace both a bundle and
its manifest can create new matching hashes.  Until signed update metadata is
implemented, obtain bundles through a trusted channel and compare the manifest
digest with a separately published value before running the root installer.

The bundle covers the application runtime and prebuilt web assets only. A fully
offline bare-host install will also need a signed Ubuntu package repository or
appliance image; that is a separate packaging layer.
