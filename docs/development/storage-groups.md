# Storage Groups and disk lifecycle

Storage Groups are stable, user-facing locations used by media applications. A group owns a
namespace such as `/srv/hoardarr/media`; disks and logical pools are replaceable backends of that
namespace. Kernel names such as `/dev/sdb` are observations and never backend identity.

The persistent lifecycle is:

`discovered → assigned → active → preferred_write → draining → verifying → read_only → retired`

The production workflow implements registration, assignment, activation, single preferred-write
placement, immutable drain preflight, atomic new-write exclusion, checkpointed movement,
verification, and retirement. Moving a preference atomically demotes the previous preferred
backend. Drain execution, verification, retirement, reuse, and wipe states cannot be entered through
the direct transition API: a durable operation must own them so copy or verification safety cannot
be skipped.

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

Verification modes are explicit: `fast` verifies size and mtime, `accurate` requires full SHA-256
source and destination hashes, and `paranoid` adds a second complete destination read and hash pass.
The durable per-file manifest records checkpoints so a worker restart resumes without treating
unverified files as complete.

An approved plan can include a durable scheduled start, a bounded copy-rate limit, and a maintenance
window. The worker does not claim a scheduled operation before `not_before`; the database index keeps
that queue lookup bounded. A maintenance window pauses at the next file/chunk checkpoint. Each
explicit resume receives a fresh window of the immutable duration, so an expired first window cannot
trap a resumable job in an immediate-pause loop. Copy limiting is applied to actual bytes written and
does not build an in-memory work queue.

Linux exact-mount sources can optionally be remounted read-only while inventory, copy, and
verification run. Hoardarr will not remount a parent filesystem or guess at mount ownership. It
verifies the kernel's observed flag after every remount, briefly restores write access only while
removing already verified source files, and restores read-only access in a `finally` boundary even
when finalization fails. The source stays read-only after retirement. Unsupported hosts and paths are
reported as unavailable rather than silently weakening the requested plan.

Before that mover copies its first byte, `begin_drain_placement` atomically changes the source to
`draining`, records the owning operation and immutable plan digest, mirrors the state to a physical
disk when present, and guarantees another backend is preferred for new files. Replaying the same
operation is idempotent; a different operation cannot adopt an in-progress source. This transaction
boundary is deliberately not exposed as a standalone UI action: starting it without the
checkpointed mover would leave an operator with placement changed but no evacuation work running.

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
- `POST /api/v1/storage/groups/{group_id}/drain`
- `GET /api/v1/operations/{operation_id}/progress`
- `POST /api/v1/operations/{operation_id}/pause`
- `POST /api/v1/operations/{operation_id}/resume`
- `GET /api/v1/storage/disks`
- `POST /api/v1/storage/disks/reconcile`

Reads require authentication. Mutations require the `operate` scope and normal browser Origin/CSRF
checks. Unsafe lifecycle skips return Problem Details with `durable_operation_required`.

The UI exposes **Preview drain** only when another active data/archive backend and a source mount
path exist. It shows the immutable-plan facts, requires exact `I AGREE` approval, starts the durable
operation, and displays real phase/file/byte/rate/ETA progress with pause/resume and final report.
The stable Storage Group namespace remains unchanged when the source backend is retired.

The isolated Linux proof uses two purpose-created loop-backed ext4 filesystems. It writes and hashes
four deterministic files, pauses and resumes the job, simulates a worker crash after inventory,
recovers the stale operation from its durable manifest, finishes verification, retires the source,
and confirms the namespace and all hashes are unchanged. The extended proof schedules the operation,
applies a 16 MiB/s bound, verifies the source mount is actually read-only during movement, and checks
the completed report. This proves the software workflow in isolation; it is not physical-disk
certification.
