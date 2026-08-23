# Storage Groups and disk lifecycle

Storage Groups are stable, user-facing locations used by media applications. A group owns a
namespace such as `/srv/hoardarr/media`; disks and logical pools are replaceable backends of that
namespace. Kernel names such as `/dev/sdb` are observations and never backend identity.

The persistent lifecycle is:

`discovered → assigned → active → preferred_write → draining → verifying → read_only → retired`

The first production slice implements registration, assignment, activation, and single preferred
write placement. Moving a preference atomically demotes the previous preferred backend. Drain,
verification, retirement, reuse, and wipe states are represented but cannot be entered through the
direct transition API: a durable operation must own them so copy or verification safety cannot be
skipped.

## Identity and namespace safety

- `PhysicalDisk.stable_identity` uses a discovery-supplied WWN, NAA, EUI, NGUID, or stable
  serial-derived identifier. `kernel_path` may change without creating a new disk.
- A backend derives its identity from a registered disk or an existing `StorageEntity`; clients do
  not supply a second, conflicting identity.
- Namespaces must be absolute, cannot be `/`, cannot contain traversal, and cannot contain control
  characters.
- One group has at most one `preferred_write` backend.
- Every implemented transition creates durable lifecycle history and an application audit event.

## Current API

- `GET/POST /api/v1/storage/groups`
- `POST /api/v1/storage/groups/{group_id}/backends`
- `POST /api/v1/storage/groups/{group_id}/backends/{backend_id}/transition`
- `GET /api/v1/storage/disks`
- `POST /api/v1/storage/disks/reconcile`

Reads require authentication. Mutations require the `operate` scope and normal browser Origin/CSRF
checks. Unsafe lifecycle skips return Problem Details with `durable_operation_required`.

The drain/evacuate engine and verification profiles remain tracked by `DRAIN-01` through
`DRAIN-12`. The UI does not advertise those actions as available yet.
