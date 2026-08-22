# Disk quarantine and autoactivation

## Status

The host-bound quarantine preparer and typed privileged executor ship in
0.1.8. `hoardarr-storage-quarantine prepare --yes` performs a fail-closed
boot-chain inspection, atomically stages udev, mdadm, LVM, and multipath
policies, makes existing iSCSI nodes manual, regenerates initramfs, and writes a
checksum-verified attestation. It does not reboot. Layered or remote boot
storage is rejected until an explicit allowlist is available.

The executor refuses every plan until that attestation exists and still repeats
stable identity, active-use, holder, and selected-drive checks under per-drive
locks immediately before destructive stages.

## Why service suppression is not enough

`policy-rc.d` and systemd unit-state checks prevent many package maintainer
scripts from starting daemons. They do not suppress udev rules, initramfs
hooks, storage generators, or protocol-specific boot discovery. On Ubuntu
24.04, packages planned for the appliance can add persistent behavior such as:

- mdadm incremental assembly when an MD signature appears;
- LVM event-based PV scanning and VG/LV autoactivation;
- unconditional registration of devices carrying bcache signatures;
- multipath path claiming and map creation;
- login to iSCSI nodes marked for automatic startup; and
- NVMe-oF/NBFT autoconnection triggered by udev and systemd units.

Those actions may occur on a later cold boot, hotplug, controller rescan, or a
concurrent device event even if package installation itself appears inert. This
matters especially for old, repurposed, imported, or partially wiped drives:
stale metadata is still actionable metadata.

Hoardarr's default must therefore be **enumerated but not activated**. A disk is
not eligible for assembly, import, cache registration, multipath claiming, or
network-storage login until a reviewed lifecycle plan names its stable identity.

## Required boot-device discovery

Before changing packages or activation policy, the installer must derive the
complete recursive dependency chain for `/`, `/boot`, `/boot/efi`, and active
swap. It must resolve every layer, including:

- filesystem UUID and partition UUID;
- DM, LVM, MD RAID, multipath, and bcache parents;
- local NVMe/SATA/SAS paths and stable WWN/serial-based IDs;
- iSCSI session/node identity; and
- NVMe-oF, NBFT, Fibre Channel, and SAN path identity.

Kernel names such as `/dev/sda` are evidence, not identity. If any boot member
cannot be mapped unambiguously to stable identifiers, runtime installation must
stop before mutation. Existing activation configuration must be preserved and
reported; Hoardarr must never replace an unknown policy with a generic one.

## Deny-by-default controls

The installer must stage these controls atomically **before** installing the
corresponding packages. Each policy permits only members required by the boot
dependency graph. All other devices remain quarantined until an explicit intake
or import workflow authorizes them.

| Subsystem | Ubuntu 24.04 behavior to contain | Required initial policy |
| --- | --- | --- |
| MD RAID | `64-md-raid-assembly.rules` invokes `mdadm --incremental` | Write explicit `ARRAY` and stable `DEVICE` entries for boot MD members, followed by the effective first `AUTO -all` policy. Use `AUTO -all` alone when boot does not use MD. |
| LVM | `69-lvm.rules` runs event `pvscan` and may run `vgchange -aay` | Set `activation/auto_activation_volume_list` in `/etc/lvm/lvmlocal.conf` to boot VGs only, or `[]` when boot is not on LVM. Use a boot-PV allowlist through the LVM devices file or `devices/global_filter` for udev/system components. |
| bcache | `69-bcache.rules` registers every recognized bcache signature and has no native allowlist | When boot is not on bcache, mask the vendor rule with `/etc/udev/rules.d/69-bcache.rules -> /dev/null`. A bcache-root system requires a separately reviewed rule that registers only boot UUIDs. |
| multipath | udev rules and module loading can claim paths | Enforce `find_multipaths strict`; audit the WWIDs file and preserve only boot-SAN or explicitly approved WWIDs. A blacklist-all policy may use reviewed WWID exceptions. Never remove a SAN-root WWID. |
| iSCSI | automatic nodes are logged in by `open-iscsi.service`; udev may activate `iscsid` for an existing iSCSI path | Inspect active sessions and `/etc/iscsi/nodes`. Preserve required boot sessions, keep every unapproved node at manual startup, and keep service/socket activation off until configured. Reject an ambiguous iSCSI-root host. |
| NVMe-oF/NBFT/FC | `70-nvmf-autoconnect.rules` and `nvmf-*`/`nvmefc-*` units can connect automatically | Mask autoconnect rules and units unless the boot dependency graph proves that a reviewed remote controller/path is required. Preserve only those boot connections. |

The same-name `/etc/udev/rules.d` symlink-to-`/dev/null` mechanism is the
documented udev override for disabling a vendor rule. Managed files, symlinks,
and prior administrator configuration need checksummed crash-recovery records,
the same way the bootstrap protects `policy-rc.d`.

After policy and package changes, regenerate every affected initramfs. Report a
required reboot but never reboot automatically.

## Intake and temporary activation

Discovery may read identity, capacity, transport, SMART/NVMe health, enclosure
location, and on-disk signatures without activating a storage stack. An intake
workflow may then propose one narrowly scoped action:

1. identify the selected devices by serial/WWN plus current physical location;
2. revalidate identity immediately before execution;
3. show every metadata type and predicted activation;
4. add only the selected identities to a temporary or persistent allowlist;
5. perform the requested import, test, pool, wipe, or decommission operation;
6. audit the result; and
7. return unused devices to quarantine.

No provider or add-on may bypass this policy by invoking raw `mdadm`, `pvscan`,
`vgchange`, `bcache-register`, `multipath`, `iscsiadm`, or `nvme connect`
arguments.

## Release gates

Runtime `apply` may be enabled only after all of the following pass on Ubuntu
24.04:

1. Boot-graph fixtures cover plain partitions, hardware RAID virtual disks, MD,
   LVM, bcache, local NVMe, multipath/SAN, iSCSI root, and NVMe-oF/NBFT. An
   unsupported root topology fails closed.
2. Disposable disks carry valid and stale MD, LVM, bcache, filesystem, ZFS, and
   multipath signatures. Capture sector hashes and storage-stack state before
   testing.
3. Test a clean install, direct upgrade from each supported old policy schema,
   a no-change rerun, interrupted installation, and crash recovery.
4. Trigger add/change/remove udev events and controller rescans during and after
   installation. No unapproved array, VG/LV, bcache device, multipath map,
   iSCSI session, or NVMe-oF connection may appear.
5. Cold-boot and hotplug every fixture, then verify that boot storage still
   works, quarantined media sector hashes are unchanged, and no unapproved
   service or SysV runlevel link is active.
6. Verify generated initramfs contents and repeat the test with Secure Boot
   where supported.
7. Run the same suite on a disposable physical host with sacrificial disks and
   representative SAS expanders/controllers before enabling a production-host
   path.

Until these gates pass, physical hosts may receive only read-only hardware
detection and package plans. Data disks should remain detached while building
or installing the base image.
