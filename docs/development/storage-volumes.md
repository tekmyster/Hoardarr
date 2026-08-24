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

`GET /api/v1/storage/volumes` returns the authenticated inventory. Registration is currently an
internal provider boundary; provider discovery owns creation and reconciliation. The later
create-operation tasks add provider mutation workflows.

Each inventory item contains a normalized capability matrix for size, allocation, filesystem,
file/block presentation, snapshots, quota, reservation, thin provisioning, clone, QoS, and
replication. `support` describes provider semantics; `availability` describes the current observed
runtime. A provider baseline never turns an unknown runtime into `available`, and a runtime probe
cannot enable a capability that is incompatible with that resource class. Provider observations
may include bounded constraints such as a maximum size or backing thin pool. Unsupported behavior
remains unsupported rather than being simulated.
