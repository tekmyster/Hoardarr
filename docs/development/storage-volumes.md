# Provider-backed storage volumes

Hoardarr stores a canonical logical-volume record between provider-managed storage and a
Storage Group. The record represents only semantics that the provider actually exposes; it does
not make every filesystem, ZFS dataset, LVM logical volume, or iSCSI LUN behave alike.

The stable identity is `provider:resource_type:provider_resource_id`. The provider resource ID is
authoritative and remains stable when a Linux kernel path changes. A current device path and
mountpoint are operational attributes, not identity inputs.

Supported initial resource classes are:

| Provider | Resource types | Presentation |
| --- | --- | --- |
| filesystem | filesystem | file |
| ZFS | dataset, zvol | file or block as appropriate |
| LVM | logical volume | file or block as appropriate |
| Linux MD | filesystem | file |
| iSCSI | LUN | block |

`GET /api/v1/storage/volumes` returns the authenticated inventory. Guided creation uses
`POST /api/v1/storage/volumes/preview` followed by `POST /api/v1/storage/volumes`. The apply call
requires the immutable preview hash and creates a durable `storage.volume.create` operation.
The worker registers a volume only after the provider resource has been created and read back.
`GET /api/v1/storage/volumes/{id}` returns one canonical item together with its bounded durable
operation history. The Storage page's **Manage** action shows provider identity, the observed
capability matrix, its observation timestamp, and that history; it never upgrades an unsupported
or unavailable provider capability into an editable setting.

The initial production mutation provider is ZFS. Guided users select a human purpose rather than
provider geometry:

- media, archive, and backup areas create datasets with `zstd`, `atime=off`, and 1 MiB records;
- download and general areas use the same compression and balanced 128 KiB records;
- VM storage creates a sparse zvol with an explicit size and 16 KiB block size.

Plans bind the ZFS pool GUID observed at preview. The executor re-reads that GUID before running a
fixed-argument `zfs create` command and verifies the created resource GUID afterward. A missing
tool, changed pool identity, unhealthy pool, insufficient reserve, malformed plan, command error,
or malformed provider output fails safely; none is converted into a successful inventory row.
The Storage page provides loading, empty, provider-unavailable, review, durable progress, failure,
and completed states, with the same operation visible in Activity.

Each inventory item contains a normalized capability matrix for size, allocation, filesystem,
file/block presentation, snapshots, quota, reservation, thin provisioning, clone, QoS, and
replication. `support` describes provider semantics; `availability` describes the current observed
runtime. A provider baseline never turns an unknown runtime into `available`, and a runtime probe
cannot enable a capability that is incompatible with that resource class. Provider observations
may include bounded constraints such as a maximum size or backing thin pool. Unsupported behavior
remains unsupported rather than being simulated.

Advanced creation for other provider geometries is tracked separately. The capability catalog is
not permission to offer a decorative control for an unavailable provider.

ZFS datasets and zvols expose provider-native lifecycle controls in **Manage** only when the live
provider observation reports them available. Snapshot operations create immutable plans bound to
the dataset/zvol GUID, execute as durable operations, and read the exact snapshot or clone back
before recording success. Manual create, restore, clone and delete use fixed argument vectors;
bounded automatic schedules retain only Hoardarr-owned snapshots. Dataset clones receive a safe
managed mount path, while zvol clones remain block devices.

Capacity controls use `POST /api/v1/storage/volumes/{id}/capacity/preview` followed by the durable
apply endpoint at the same path. For a dataset, `quota=0` means no maximum and `reservation=0`
means no reserved pool space; a nonzero reservation cannot exceed a nonzero quota. Hoardarr sends
both properties in one fixed-argument `zfs set` invocation. For a zvol, thin allocation maps to
`refreservation=none` and fully reserved allocation maps to `refreservation=auto`; zvol quota is
correctly reported as unsupported because its `volsize` is the size boundary. The executor checks
the provider GUID and resource type immediately before mutation, reads the numeric properties back,
and stores only verified limits. A quota may make future writes fail, and thin allocation may fail
future writes if the backing pool fills; the review UI states those consequences without implying
data deletion.

Advanced ZFS creation is exposed behind **Customize ZFS settings**. It changes the immutable
provider plan and resulting command, rather than merely saving UI preferences. Experts may choose
dataset versus zvol, a bounded ZFS compression algorithm, dataset record size and access-time
behavior, a safe storage mount path, or zvol block size and sparse allocation. The UI does not
offer LVM LV or iSCSI LUN creation yet because Hoardarr does not yet have those mutation providers;
their honest capability inventory is not treated as implementation.
