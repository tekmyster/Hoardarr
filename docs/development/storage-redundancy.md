# Storage controller and path redundancy

Storage redundancy is an Advanced storage lifecycle operation. It changes how
Linux reaches an existing logical device; it does not recreate the filesystem,
pool, shares, media folders, ACLs, or application paths.

## Identity model

Hoardarr persists three separate records:

- `StorageEntity` is the logical storage, keyed by an authoritative SCSI
  WWID/NAA or NVMe NGUID/EUI-64.
- `StorageController` is a controller, HBA, initiator, or provider endpoint.
- `StoragePath` connects one controller to one `StorageEntity`.

Kernel names such as `/dev/sdb` are observations only. Capacity, model, or
matching enclosure positions are never sufficient proof that two paths reach
the same storage. If the provider cannot report a stable logical identifier,
automatic consolidation fails closed.

The durable Hoardarr storage ID, filesystem UUID, public mount path, and
telemetry entity ID belong to `StorageEntity`. Adding, replacing, failing, or
removing a path cannot change those identities.

## Single path to redundant access

New or imported single-path storage records its logical identity during normal
creation. When a later scan reports exactly one new controller path with the
same identity and geometry, Advanced storage offers **Add redundant path**.

The immutable plan records:

- the logical storage and filesystem UUID;
- original and proposed paths and controllers;
- the reviewed hardware snapshot;
- capacity and logical/physical sector geometry;
- the existing device mount and public application mount;
- the selected provider policy.

Immediately before execution, the privileged executor repeats the identity and
geometry checks. It then creates and verifies a Device Mapper Multipath map,
checks the filesystem UUID through the map, and performs the smallest controlled
mount transition required. It does not call `mkfs`, partitioning tools, or a
data-copy command. If the new bind mount fails, it restores the original direct
mount; an unsuccessful rollback becomes a durable needs-attention operation.

The public mount (for example `/media`) stays fixed. SMB/NFS definitions and
Plex/ARR paths therefore continue to reference the same location.

## Lifecycle operations

- **Add redundant path** converts verified single-path storage to a multipath
  presentation.
- **Replace controller path** adds and verifies the replacement before removing
  the stale path. An existing map stays mounted.
- **Remove redundant path** warns that protection is reduced. Removing one of
  several paths keeps the map online; reducing the last map to one path performs
  a controlled return to the remaining direct device.
- Existing multipath storage is imported as one logical storage object with
  several paths, not several independent disks.

These operations are idempotent durable jobs. A changed plan, hardware
snapshot, logical identity, capacity, sector geometry, or filesystem UUID is
rejected before mutation.

## Provider behavior

Recommended mode defers grouping, ALUA priority, path selection, failover, and
failback to Linux multipath/provider defaults. Expert mode can request a
supported policy for a newly created map. Hoardarr does not impose one policy
across active/passive, active/active, dual-domain SAS, Fibre Channel, iSCSI, or
FCoE providers.

Provider state is normalized into these user-facing states:

- **Single path** — online without controller redundancy.
- **Fully redundant** — expected paths are ready.
- **Reduced redundancy** — online, but a path is missing or failed.
- **Failed over** — an alternate path is serving the logical storage.
- **No path** — the logical storage is unavailable.

Advanced details retain controller identity, target/initiator information,
protocol, current kernel path, optimized state, active state, and reported link
speed. Unreported fields remain `Not reported`.

## Telemetry continuity

Capacity and block-I/O readings are rebound from the current presentation device
to the durable `logical_storage` telemetry entity. Counter-source changes and
resets use persisted offsets, so changing from a direct path to a mapper does
not create a second storage history or reset `Writes today`. Path transitions
and failovers are recorded separately.

## Validation boundary

Unit, API, executor, browser, and provider tests cover identity, false matches,
geometry drift, mount and UUID preservation, replacement ordering, removal,
path failure/recovery, restart reconciliation, rollback, and telemetry identity.
The Linux integration workflow creates one disposable LIO-backed LUN exposed
through multiple iSCSI portals, introduces paths after initial filesystem use,
runs continuous I/O while paths fail and recover, restarts `multipathd`, and
verifies hashes and identities throughout.

That isolated test proves the generic Linux Device Mapper lifecycle. Exact
NetApp, Dell EMC, HPE, dual-domain SAS, FC, and FCoE controller/firmware behavior
still requires matching physical hardware certification.
