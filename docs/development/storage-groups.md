# Storage Groups and disk lifecycle

Storage Groups are stable, user-facing locations used by media applications. A group owns a
namespace such as `/srv/hoardarr/media`; disks and logical pools are replaceable backends of that
namespace. Kernel names such as `/dev/sdb` are observations and never backend identity.

The persistent lifecycle is:

`discovered → assigned → active → preferred_write → draining → verifying → read_only → retired`

The first production slices implement registration, assignment, activation, single preferred-write
placement, and immutable drain preflight. Moving a preference atomically demotes the previous
preferred backend. Drain execution, verification, retirement, reuse, and wipe states cannot be
entered through the direct transition API: a durable operation must own them so copy or
verification safety cannot be skipped.

## Drain preflight

`POST /api/v1/storage/groups/{group_id}/drain/preview` performs a read-only preflight and returns an
immutable SHA-256-bound plan. It does not move data. The planner binds the source and destinations
to stable backend identities and configured mount paths, rejects cross-group/duplicate/inactive
destinations, rejects path overlap, checks destination health, and uses filesystem facts to compare:

`source filesystem used bytes + configured reserve <= aggregate destination available bytes`

That value is deliberately conservative when a source mount contains files outside the selected
namespace. The UI shows the exact inputs rather than calling the value a file inventory.

Linux preflight also performs a bounded `/proc/*/fd` inspection for open files. Connected Servarr
applications contribute active-write state only when their stored provider state actually reports
it; missing provider data is a warning, not a fabricated zero. Active open handles, reported ARR
writes, unhealthy destinations, or insufficient capacity make the plan `ready: false`.

Verification modes are explicit: `fast` retains size/mtime methodology, `accurate` requires full
file hashes, and `paranoid` adds another full read pass. The checkpointed mover will implement these
methods under `DRAIN-04`, `DRAIN-07`, and `DRAIN-09`.

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
- `POST /api/v1/storage/groups/{group_id}/drain/preview`
- `GET /api/v1/storage/disks`
- `POST /api/v1/storage/disks/reconcile`

Reads require authentication. Mutations require the `operate` scope and normal browser Origin/CSRF
checks. Unsafe lifecycle skips return Problem Details with `durable_operation_required`.

The UI exposes **Preview drain** only when another active data/archive backend and a source mount
path exist. It labels the result as preflight-only. Apply remains unavailable until the durable,
checkpointed mover tracked by `DRAIN-02` through `DRAIN-12` is complete.
