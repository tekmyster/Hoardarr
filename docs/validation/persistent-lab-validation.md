# Persistent A/B lab validation

Initial appliance validation was completed 2026-08-24 against commit `055dff81c460`.
The live-product/storage validation below was repeated against commit `077cdaa531b2` and
application version 0.3.11 Beta 1.

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

At initial validation time, the DHCP observations were:

- Hoardarr-A: `10.81.200.120`, UI `http://10.81.200.120:7877`
- Hoardarr-B: `10.81.200.140`, UI `http://10.81.200.140:7877`

Addresses may change. Node identity is the VM and installed hostname, not the observed DHCP address.

On 2026-08-24 the current observations were Hoardarr-A `10.81.200.114` and Hoardarr-B
`10.81.200.140`. Both nodes were upgraded from the same CI-produced archive
`hoardarr-0.3.11-077cdaa531b2-ubuntu24.04-amd64-cp312.tar.gz` (32,232,743 bytes,
SHA-256 `741c4c6cb7bd7bdbda0cc4f7969781d6148c4adfb5d89a67b473876e05cea37e`). GitHub
Actions run `32781979719` passed its backend, frontend, browser, accessibility, release-bundle,
installed-appliance, migration, systemd, fleet/PostgreSQL, and MinIO recovery jobs. Both installed
release symlinks resolved to `0.3.11-077cdaa531b2`; API and worker units were active and
`/health/ready` returned ready after startup.

## Storage identity and UI evidence

VMware `disk.EnableUUID` is enabled on both powered-off VMs. After reboot, Linux exposed a distinct
WWN and `/dev/disk/by-id` entry for every OS and data VMDK. Hoardarr-A read-only discovery reported
exactly four eligible data disks, 47.24 GB raw capacity, stable VPD-backed identities, and correctly
excluded the 24-GiB system disk. The real product UI showed direct-attached disks and PVSCSI
topology without fake enclosures, bay mappings, health, pools, shares, or configured redundancy.

Separate administrator accounts were paired through each real UI. Browser inspection confirmed the
production navigation, first-run Guided/Advanced wizard, honest empty states, and live Storage page.
Audit credentials are stored only in local DPAPI-encrypted files outside the repository.

## Live managed-storage and Storage Group evidence

Hoardarr-A contains a real, product-created mergerFS media path that was initially backed by three
purpose-created 12-GiB VMDKs and was later expanded through the product to the purpose-created
8-GiB VMDK. The product discovery retained one logical storage object:

- Hoardarr `StorageEntity`: `91710909-1cef-4e4e-ab82-b5d34bee5c93`
- stable identity: `mergerfs:9ad4fa497d85f983`
- provider/kind: `mergerfs`
- live mount: `/data`
- presentation source: `/mnt/hoardarr/media`

The real 0.3.11 browser UI was used—not an API shortcut or fixture—to create `Media Library` at
the stable namespace `/data`, attach that exact logical storage, review its mounted identity,
activate it, and make it preferred for new files. Persisted readback showed group
`e1ed4dc7-d5d1-486e-b22b-daa78bdbeffb`, backend
`5f31b341-1922-477c-a30d-3cf107a0c927`, the unchanged StorageEntity ID above, and lifecycle state
`preferred_write`. After the expansion all four member identities were `managed_member`. The
24-GiB system VMDK remained excluded from expansion in the UI, and none of the protected Cisco
SSDs was attached or modified.

The UI also showed the real Activity history, live mergerFS pool/share discovery, virtual PVSCSI
topology, expansion candidates, and honest `Not reported` health/bay fields. This establishes a
visible beta Storage Group and pool on A. It does not claim SnapRAID, ZFS, Linux MD, a landing tier,
shared multipath, HA peer handoff, or physical-hardware validation.

After all Hoardarr-A audit browser tabs were closed, the durable worker continued collection with
no UI metric consumer. Over a 39-second observation the persisted sample count advanced from
411,781 (latest observation `2026-08-24T22:05:47.827522Z`) to 413,328 (latest observation
`2026-08-24T22:06:27.023606Z`). This is direct live-lab evidence that collection/persistence is not
owned by a graph subscription. It is not a substitute for the longer browser-disconnect workload
and retention scenarios already tracked by their dedicated validation tasks.

## Live mergerFS expansion evidence

On 2026-08-24 the authenticated production UI analyzed the unassigned 8-GiB VMDK
`wwn:naa.6000c29019336c157c2f94e3aae65c53`, showed its real capacity and absence of detected
signatures, recommended adding it to `Media Library`, disclosed that mergerFS alone provides no
drive-failure protection, and required the immutable destructive review. Plan
`f274ca5b-74b9-4342-9b53-d8d7b0d5502a` bound candidate `9a476d082bd022f5ce6173fe`, the hardware
snapshot, target Storage Group, and exact disk identity. Durable operation
`b0c54ce7-69ea-4d77-b4b2-45bc8121509a` performed the full-surface read, GPT creation, ext4 format,
managed mount, mergerFS activation, persistent fstab update, quarantine release, directory/share
verification, and completed at 100% (11/11 steps).

Restart recovery found three real implementation defects and corrected them without rebuilding or
copying user data:

- mergerFS runtime branch updates now set the exact reviewed list, so retry is idempotent and an
  interrupted older executor cannot append a member twice;
- the storage executor systemd sandbox permits local `AF_NETLINK`, which `udevadm trigger --settle`
  requires, while continuing to prohibit IP networking;
- expansion registration resolves the Storage Group's existing logical entity rather than hashing
  its `/data` bind presentation as a second pool. Startup reconciliation merged 245 short-lived
  alias samples into the canonical telemetry history and removed the orphan alias.

The final runtime and persistent mergerFS lists contain the fourth member exactly once. Usable pool
capacity changed from 37,714,550,784 to 46,062,751,744 bytes. The pre-expansion validation file
remained at `/data/.hoardarr-validation/pre-expansion.bin` with unchanged SHA-256
`2daeb1f36095b44b318410b3f4e8b5d989dcc7bb023d1426c492dab0a3053e74`. Its ext4 member UUID is
`4282a58c-cdc0-4069-ba8d-636e46d33cda`. The SMB share remained named `data` with path `/data`.
StorageEntity `91710909-1cef-4e4e-ab82-b5d34bee5c93`, Storage Group
`e1ed4dc7-d5d1-486e-b22b-daa78bdbeffb`, backend
`5f31b341-1922-477c-a30d-3cf107a0c927`, stable identity `mergerfs:9ad4fa497d85f983`, namespace
`/data`, and `preferred_write` state were unchanged. The canonical entity now records all four
member identities, exact 46,062,751,744-byte capacity, one logical metric entity, and the merged
pre/post-expansion history.

## Remaining boundary

This evidence verifies the persistent appliance nodes, stable virtual-disk discovery, and one real
Hoardarr-managed mergerFS/Storage Group workflow on A. `LAB-03` remains in progress until the other
required real virtual providers and tiers are created. `LAB-04` remains in progress until tiers,
peers, failover history, and their graphs are also visible without fixtures.
