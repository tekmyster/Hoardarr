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

This initial adoption is **non-destructive and configuration-preserving**, but
it is not advertised as seamless. A filesystem already mounted from a raw SCSI
path cannot generally have that open block-device reference replaced with a new
Device Mapper device in place. Hoardarr therefore reports **Brief maintenance
required**, coordinates its managed SMB/NFS services, unmounts, verifies the
map, and remounts the same filesystem at the same paths. Existing online maps
support path addition, replacement, failover and settings changes without this
initial adoption window. Providers that cannot prove safe automatic adoption
report **Unsupported automatic conversion**.

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
failback to Linux multipath/provider defaults. Expert mode can apply a supported
per-WWID path grouping policy, selector, failback mode and no-path behavior to a
new or existing map. Hoardarr validates the generated configuration with
`multipath -t` and reloads `multipathd`; the UI never offers a value outside the
backend allow-list. Hoardarr does not impose one policy
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

## Product interface

Storage cards and Overview show **Single path**, **Fully redundant**, **Reduced
redundancy**, **Failed over**, or **Offline** without requiring a user to open
settings. Selecting an Overview item opens that exact logical storage object's
Advanced view. The view contains:

- summary KPIs for healthy, active and failed paths, failovers today, last
  failover, degraded duration, aggregate throughput, IOPS and latency;
- an explicit controller/path topology and per-path controller, HBA, target,
  H:C:T:L, WWID, ALUA/optimized and speed facts;
- bounded 24-hour path graphs for `io.read.bytes_per_second`,
  `io.write.bytes_per_second`, `io.read.iops`, `io.write.iops`,
  `io.read.latency`, `io.write.latency`, `storage.paths.healthy`, and
  `storage.paths.failed`;
- durable failover/path-loss/recovery annotations and an Events timeline;
- Add, Replace, Remove, and Configure workflows with immutable review and real
  Activity progress.

The graph API is capped at 240 points per path/metric request, requests are
cancelled when the tab or storage changes, and polling stops when the view is
unmounted or the document is hidden. The browser is only a viewer: path samples
and controller events are collected and persisted by the backend worker.

## Telemetry continuity

Capacity and block-I/O readings are rebound from the current presentation device
to the durable `logical_storage` telemetry entity. Counter-source changes and
resets use persisted offsets, so changing from a direct path to a mapper does
not create a second storage history or reset `Writes today`. Path transitions
and failovers are durable `StorageRedundancyEvent` records and are recorded
separately from the continuous logical-storage performance series. Physical path
counters are rebound to stable `storage_path` entities, so a kernel device rename
does not create a new path history.

## Validation boundary

Unit, API, executor, browser, accessibility, and provider tests cover identity, false matches,
geometry drift, mount and UUID preservation, replacement ordering, removal,
path failure/recovery, restart reconciliation, rollback, managed SMB/NFS service
coordination, settings-to-generated-config behavior, persistent events, graph
annotations, and telemetry identity.
The Linux integration workflow creates one disposable LIO-backed LUN exposed
through multiple iSCSI portals, introduces paths after initial filesystem use,
runs continuous I/O while paths fail and recover, restarts `multipathd`, and
verifies hashes and identities throughout.

That isolated test proves the generic Linux Device Mapper lifecycle. Exact
NetApp, Dell EMC, HPE, dual-domain SAS, FC, and FCoE controller/firmware behavior
still requires matching physical hardware certification.

The separate [two-node storage validation](../validation/two-node-storage-failover.md)
boots two real Ubuntu/systemd Hoardarr VMs with two virtual SSDs per node. Its
controlled single-writer storage handoff is not presented as automatic clustered HA.
