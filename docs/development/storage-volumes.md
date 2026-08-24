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
capability task adds honest per-provider snapshot, quota, clone, thin-provisioning, QoS, and
replication states. Unsupported behavior must remain unsupported rather than being simulated.
