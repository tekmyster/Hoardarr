# Persistent A/B lab validation

Validated 2026-08-24 against commit `055dff81c460` and application version 0.3.11 Beta 1.

## Built artifact

- GitHub Actions run: `32759644019` (successful)
- Artifact: `hoardarr-lab.iso`
- Size: 3,436,904,448 bytes
- SHA-256: `1820636da2466c127b2b3285db5b7d698879429194bf3f3a3f99730493af7ab6`
- The locally downloaded ISO matched its embedded checksum before installation.

## Installed nodes

Both `Hoardarr-A` and `Hoardarr-B` are persistent VMware appliances in the `Hoardarr Development`
folder. Each has 2 vCPUs, 2 GiB RAM, one 24-GiB thin OS VMDK, and four purpose-created thin data
VMDKs sized 12, 12, 12, and 8 GiB. No RDM or physical disk is attached. The four protected Cisco
SSDs were not modified or presented to either VM.

The unattended Ubuntu 24.04.4 installation reached the corrected deferred-service late command,
rebooted into the installed operating system, completed cloud-init, and enabled the migration, API,
worker, account-executor, storage-executor, and storage-status units. The migration unit completed
successfully; every long-running Hoardarr unit was active; both local and externally reachable
`/health/ready` requests returned `{"status":"ready"}`.

At validation time, the DHCP observations were:

- Hoardarr-A: `10.81.200.120`, UI `http://10.81.200.120:7877`
- Hoardarr-B: `10.81.200.140`, UI `http://10.81.200.140:7877`

Addresses may change. Node identity is the VM and installed hostname, not the observed DHCP address.

## Storage identity and UI evidence

VMware `disk.EnableUUID` is enabled on both powered-off VMs. After reboot, Linux exposed a distinct
WWN and `/dev/disk/by-id` entry for every OS and data VMDK. Hoardarr-A read-only discovery reported
exactly four eligible data disks, 47.24 GB raw capacity, stable VPD-backed identities, and correctly
excluded the 24-GiB system disk. The real product UI showed direct-attached disks and PVSCSI
topology without fake enclosures, bay mappings, health, pools, shares, or configured redundancy.

Separate administrator accounts were paired through each real UI. Browser inspection confirmed the
production navigation, first-run Guided/Advanced wizard, honest empty states, and live Storage page.
Audit credentials are stored only in local DPAPI-encrypted files outside the repository.

## Remaining boundary

This evidence verifies the persistent appliance nodes and virtual-disk discovery. It does not claim
that a mergerFS/SnapRAID, ZFS, MD, landing tier, or multipath construct is already active. Those are
tracked by `LAB-03` and must be created through Hoardarr-supported product paths before the visible
lab state is classified as configured storage.
