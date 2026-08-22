# Hoardarr host bootstrap

`scripts/bootstrap.py` installs the Ubuntu image-build machine and produces
read-only plans for a future Hoardarr storage appliance. It is designed for
repeatable reconciliation. The package lists, tool versions, hardware rules,
and vendor downloads are repository data rather than values hidden in the
installer.

The supported base is Ubuntu Server 24.04 on amd64 or arm64. A runtime profile
requires a full physical machine or a normal VM; WSL and containers are rejected.
The current build-host profile is safe for the Ubuntu packaging VM.

## Workflow

Run these in order from the repository root:

```sh
python3 scripts/bootstrap.py check
python3 scripts/bootstrap.py plan --json
sudo python3 scripts/bootstrap.py apply --yes
python3 scripts/bootstrap.py validate --json
```

The omitted profile is `build-host`. The equivalent Make targets are
`bootstrap-check`, `bootstrap-plan`, `bootstrap-apply`, and
`bootstrap-validate`. Set `PROFILE=build-host` explicitly in automation.

`check` performs the Ubuntu, architecture, systemd, free-space, package-manager,
clock, DNS, and TLS preflight. `plan` additionally resolves every APT candidate
and simulates the exact package transaction. Neither action changes the host.
Build-host `apply` takes an exclusive Hoardarr lock, installs missing selected
packages, upgrades older selected packages to their current APT candidates,
installs pinned tools, and then validates the result. Runtime `apply` is blocked
as described below. Apply refreshes APT indexes by default, never downgrades or
removes a package, and never clears an APT hold. A hold blocks only a selected
package that needs installation or upgrade. `validate` is read-only and does not
require network access.

Every apply writes `/var/log/hoardarr/bootstrap-report.json`. Use
`--report PATH` to choose another destination, `--report -` for JSON on stdout,
or `--json` to print JSON as well as writing the normal apply report. An offline
build can use `--skip-network-check --no-apt-update` only after its package
indexes and download cache have been deliberately populated.

An index refresh is reported but is not a persistent `changes` entry, so an
already-reconciled second apply can have an empty change list. The report keeps
installed and candidate versions, selected upgrades, holds, the complete
simulated `Inst` set, and the actual dpkg delta. A changed package outside every
simulation or any removed package fails the transaction audit.

Apply and validate report `/var/run/reboot-required` and, when present, the
package names in `/var/run/reboot-required.pkgs`. The bootstrap never reboots
the host. The build profile also selects boot-image packages by architecture:
amd64 gets BIOS/isolinux and amd64 UEFI components, while arm64 gets only arm64
UEFI components.

## Profiles and safety boundary

Runtime-profile installation is deliberately blocked in the current milestone.
Storage packages can install udev and boot-time autoactivation behavior even
when service starts are suppressed. Runtime `check` and `plan` remain available
for review, but `apply` is build-host-only until the deny-by-default
[disk-quarantine design](disk-quarantine.md) is implemented and passes its
reboot and hotplug gates.

The profiles are:

| Profile | Purpose | Manifest |
| --- | --- | --- |
| `build-host` | compiler, Debian/image tooling, QEMU, RAUC, Node, pnpm, uv | `packaging/packages/build-host.txt` |
| `appliance-core` | disk discovery, health, filesystems, ZFS, SnapRAID, mergerfs | `packaging/packages/appliance-core.txt` |
| `storage-protocols` | iSCSI, NFS, and SMB | `packaging/packages/storage-services.txt` |
| `tiered-storage` | file-aware SSD landing/mover tools; advanced block-cache inspection | `packaging/packages/tiered-storage.txt` |
| `advanced-cluster` | controller/service HA components | `packaging/packages/advanced-ha.txt` |
| `advanced-fcoe` | FCoE and DCB user space | `packaging/packages/advanced-fcoe.txt` |

Repeat `--profile` to combine profiles. `--profile all` expands to every listed
profile.
Every profile other than `build-host` requires `--confirm-runtime-host`, even for
a read-only plan or validation. For example:

```sh
python3 scripts/bootstrap.py plan \
  --profile appliance-core \
  --profile storage-protocols \
  --profile tiered-storage \
  --confirm-runtime-host \
  --json
```

Installing packages is not permission to touch data. The current executable
boundary is fail-closed: because runtime `apply` is rejected, the bootstrap
cannot install packages that partition, format, wipe, import, assemble, mount,
export, or target storage media. Build-host APT transactions use a temporary
`policy-rc.d` that returns 101. A pre-existing regular file or symlink is recorded
with its content/target, ownership, mode, and timestamps, then restored with
crash recovery. Recovery proceeds only when the current path is still the exact
Hoardarr guard or already matches the recorded original; an administrator's
intervening change is preserved and recovery fails for manual review.

The code also keeps a machine-bound, persistent runtime-unit baseline as future
defense in depth. It discovers native units and SysV init scripts across every
simulated dependency, audits active/enabled state in both directions, and tracks
SysV boot links separately from generated/alias unit metadata. A mismatch aborts
before package mutation. `--refresh-runtime-baseline` is accepted only on an
explicitly confirmed runtime apply and is the sole way to accept intentional
drift; validation never starts a stopped service to repair state. This unit
tracking still cannot contain udev or initramfs autoactivation and is not the
authorization to lift the runtime block. The complete
[disk-quarantine design](disk-quarantine.md) must be implemented and tested
first.

The default tier model is file-aware: SSD landing paths and a controlled mover
can be reasoned about, audited, paused, and recovered without placing opaque
block-cache metadata in front of a filesystem. `bcache-tools` and device-mapper
thin-provisioning utilities are installed only as advanced building blocks. The
bootstrap does not create a bcache device, thin pool, migration job, or timer.

Runtime profiles should first be exercised in a disposable Ubuntu 24.04 VM with
sacrificial virtual disks after disk quarantine is implemented. For now, the
physical storage host may receive only a read-only plan and reviewed JSON report;
runtime apply is intentionally unavailable.

## Hardware-aware package selection

Unless `--hardware none` is given, the bootstrap invokes:

```sh
python3 scripts/detect-hardware.py --format json
```

For fixture tests it adds `--fixture PATH`. Runtime profiles require a working
detector. Its `recommendations.packages` values are validated as Debian package
names, deduplicated, added to the selected manifests, resolved through APT, and
shown under `packages.hardware_added` in the report. Provider matches, warnings,
and proprietary tool recommendations remain in the hardware report so an
operator can see why a package was selected.

This makes controller support capability-based: inbox drivers and open-source
tools are selected for the detected PCI driver/subsystem rather than merely
because the chassis has a Dell, HPE, Oracle, or Supermicro badge. They remain a
plan until the runtime apply safety gate is implemented.

## Public vendor controller tools

Proprietary tools are never scraped from a “latest” page and no installer script
is piped to a shell. `packaging/hardware/vendor-tools.json` is the only download
authority. A tool is eligible for automated installation only when all of these
are true:

- the hardware detector recommends its exact catalog ID;
- `install_method` is `official-public-fetch`;
- architecture and Ubuntu 24.04 match;
- the URL is HTTPS and the artifact has a pinned SHA-256;
- `archive_type` is `deb`, `tar-deb`, or `zip-deb` (archives require one exact
  `deb_member`);
- the extracted Debian package name and version exactly match the catalog;
- its license is explicitly accepted on the command line.

Catalog conflict groups are resolved before downloading anything. In
particular, Broadcom and HPE StorCLI builds that own the same `storcli` package
and executable cannot be selected together; the operator must first verify
which controller-specific build applies.

Plan first. The report shows `install_method`, version, license link, landing
page, selected artifact, and whether it is safely available. Vendor tools are
part of runtime profiles, so installation is intentionally unavailable in this
milestone. After disk quarantine is implemented, the intended explicit flow is:

```sh
sudo python3 scripts/bootstrap.py apply \
  --profile appliance-core \
  --confirm-runtime-host \
  --include-vendor-tools \
  --accept-vendor-license broadcom-storcli \
  --yes
```

Repeat `--accept-vendor-license TOOL_ID` for each recommended proprietary tool.
The command above currently fails before mutation because all runtime apply is
blocked; it documents the future license boundary rather than a bypass.
When a detected tool has only a manual or unsupported entry, installation also
fails closed unless the future operator explicitly supplies
`--allow-missing-vendor-tools`; the omission remains visible in the report.
An unavailable or manual catalog entry remains a reported manual action; the
bootstrap will not bypass a login, click-through agreement, or missing artifact.
An artifact documented by its vendor only for an older Ubuntu release is also
reported but not installed on 24.04; a chassis or `Architecture: all` label is
not treated as proof of operating-system compatibility.
Catalog `http_headers` may contain only curated `User-Agent`, `Referer`, and
`Accept` values, which accommodates official download servers without accepting
arbitrary headers from command-line input.

Downloaded artifacts are cached below `/var/cache/hoardarr/downloads`, verified
before extraction, and installed only as Debian packages. A receipt below
`/var/lib/hoardarr-bootstrap/vendor` records the artifact digest and installed
dpkg package/version. An exact installed version is skipped on subsequent runs.
Validation rechecks dpkg state and, when the catalog supplies one, executes only
a constrained read-only version command.

## Pinned build toolchains

Node, Corepack, pnpm, and uv versions and integrity values live in
`packaging/packages/versions.env`. Downloads use exact release URLs and are
verified before extraction. Nothing uses `curl | sh`, and npm lifecycle scripts
are disabled while installing Corepack and pnpm from integrity-pinned tarballs.

Versioned directories live below `/opt/hoardarr/toolchains`; managed links live
only in `/opt/hoardarr/toolchains/bin`. The bootstrap will not overwrite a
regular file there and will not replace unrelated files in `/usr/bin` or
`/usr/local/bin`. `/etc/profile.d/hoardarr-build-tools.sh` adds the managed bin
directory to future login shells. Project dependencies must still be installed
as the ordinary build user, never by running `pnpm` or `uv` as root.

To update a tool, change its version and digest together, then test a clean
install, an upgrade, a second no-change apply, and `validate` before merging.
This host bootstrap reconciles each current declared dependency directly; it
does not require stepping through historical installer versions. Application
data/schema migrations are a separate Hoardarr update-system responsibility and
are not implied by this package bootstrap.

## Verification checklist

For the build VM:

1. Save the JSON from `plan`.
2. Run `apply` once and verify its report lists expected changes.
3. Run the identical `apply` again; `changes` should be empty.
4. Run `validate`; package, command, exact toolchain, pkg-config, `dpkg --audit`,
   and simulated `apt-get check` gates must pass.
5. Reboot the disposable VM and run `validate` again.

Future runtime image testing adds hardware fixtures, an Ubuntu 24.04 disposable
VM, sacrificial virtual disks, and the complete reboot/hotplug suite in
[disk-quarantine.md](disk-quarantine.md). Destructive disk workflows belong to
separate, explicitly confirmed Hoardarr tests; they are intentionally absent
from the current build-host milestone.
