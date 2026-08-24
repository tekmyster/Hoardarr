# Storage import

Import answers: **What existing storage is present, and how can Hoardarr access
or adopt it without damaging it?**

The default import path is read-only. Hoardarr first fingerprints individual
devices, groups related members, explains its conclusions, and presents an
immutable import plan. It does not automatically assemble, replay, repair,
upgrade, or mount an unknown storage system.

## Current implementation boundary

`GET /api/v1/storage/foreign` performs the first non-mutating assessment from
the latest persisted hardware snapshot. It recognizes reported ext4, XFS,
Btrfs, NTFS, exFAT, Linux MD member, LVM physical-volume, and ZFS member
signatures. Matching MD/LVM/ZFS member UUIDs are grouped for review, but no
array, volume group, pool, or filesystem is activated. The assessment is bound
to the snapshot ID and digest and records `mutation_performed: false`.

Recognized inactive stacks can proceed to a provider metadata preview through
`POST /api/v1/storage/foreign/stack-preview`. The reviewed document is bound to
the same snapshot, every complete stable device identity, each source path, and
the exact member signature. Immediately before the preview, the storage
executor revalidates all identities, signatures, mounts, holders, and swap use.
It then invokes only a bounded provider read path:

- Linux MD: `mdadm --examine --export` on each member. Array UUID, geometry,
  expected member count, unique member UUIDs, and event counters are retained.
- LVM: `pvs` and `vgs` with `--readonly`, `--foreign`, and an exact `--devices`
  allowlist. No volume group or logical volume is activated.
- ZFS: offline `zdb -l` label reads. No pool import is attempted.

MD and LVM completeness is derived only when the provider reports the expected
number of unique members with one consistent stack identity. Offline ZFS labels
do not prove the expected topology or importability, so those fields remain
**Not reported**. An inactive stack preview never claims current health. The
API requires the `operate` scope, writes an audit record, returns no raw command
error or secret, and exposes no assemble, activate, import, or mount action.

Cached udev evidence is explicitly partial. It can identify a likely signature
but cannot prove that no other on-media signature exists. System storage,
mounted sources, unstable identities, already-managed devices, missing provider
tools, and stack-member uncertainty block automatic inspection. Filesystem and
volume metadata alone never assigns a Synology, QNAP, or other product origin.
Unraid is identified only when a persisted assignment export matches current
stable serial/WWN identity; otherwise origin remains **Not reported**.

### Unraid assignment evidence

Hoardarr includes `scripts/export-unraid-assignments.php`, a read-only exporter
intended to run on the old Unraid server while emhttp's cached assignment state
is available. It reads only `disks.ini`, `lsblk` identity, and the Unraid
version. It does not query SMART, mount a disk, or start, stop, or change the
array. The JSON contains at most 30 data/parity assignments and no credential
or hostname.

The Storage UI accepts that bounded JSON through
`POST /api/v1/storage/foreign/unraid/evidence`. The authenticated `operate`
endpoint validates slot/role consistency, rejects duplicate slots and stable
identities, persists a SHA-256 provenance record, audits replacement/removal,
and re-assesses the latest hardware snapshot. A current disk is an
**identified** Unraid data or parity member only when its reported stable
serial/WWN/EUI/NGUID agrees with exactly one assignment and any supplied
capacity is consistent. Same capacity or model never establishes identity.

Without assignment evidence:

- an independently readable supported filesystem is only a **possible data
  disk** because Unraid stores each array data disk as its own filesystem;
- a complete signature scan that finds no filesystem on a disk at least as
  large as the largest readable member may be shown as **suspected parity**;
  it may also be blank, unsupported, or damaged; and
- incomplete or conflicting evidence remains **unknown**.

Identified and suspected parity are deliberately separate. This workflow never
claims parity is valid or reusable and exposes no parity activation/reuse
operation. Official Unraid recovery guidance likewise treats the assignment
configuration as authoritative and a filesystem-free disk only as a way to
identify likely parity when configuration has been lost.

Standalone filesystems with one unambiguous source path can proceed to a real
read-only inspection. `POST /api/v1/storage/foreign/inspection/preview` binds an
immutable plan to the latest snapshot digest, stable device identity,
filesystem type and UUID, source partition, provider-specific no-recovery mount
options, and fixed inventory limits. `POST /api/v1/storage/foreign/inspection`
requires the exact phrase `INSPECT READ ONLY` and queues a durable
`storage.foreign.inspect` Activity operation.

The privileged storage executor then performs a fresh live device-identity and
activation check, resolves a persistent `/dev/disk/by-id` alias, runs
`wipefs --no-act --json`, and refuses changed or ambiguous signatures. It mounts
only beneath the root-owned `/mnt/hoardarr/imports` inspection root with `ro`,
`nodev`, `nosuid`, `noexec`, and the filesystem-specific replay/recovery guard:

- ext4: `noload`
- XFS: `norecovery`
- Btrfs: `nologreplay`
- NTFS/NTFS3/exFAT: read-only without an unsupported replay claim

`findmnt` must report the resulting mount as read-only before inventory begins.
The inventory is metadata-only, does not follow symlinks, and is bounded to
100,000 entries, 256 extension groups, and 100 reported read/stat errors. It
records file and directory counts, bytes, the largest file, timestamp extrema,
extension distribution, and case/Unicode collision counts. The executor always
attempts to detach the private root-owned mount in `finally`; detach failure is
`needs_attention`, success removes the temporary directory, and no fstab entry
or persistent adoption state is created. The report and real phase progress
remain in Activity after the mount is gone.

The latest successful bounded report is also joined back to its foreign
candidate on later assessments. The Storage UI therefore shows files, bytes,
largest file, extension distribution, collision counts, errors, filesystem,
and completion time after a browser or service restart; it does not depend on
browser memory. A report records the hardware-snapshot digest used by its
immutable plan. If discovery changes afterward, Hoardarr retains the report for
audit but labels it **refresh required** and will not treat it as current input
to migration planning. Candidate health remains **Not reported** because
filesystem signatures do not prove physical-drive or inactive-pool health.

Removable media and sources whose persisted connection evidence reports USB,
MMC/SD, or FireWire are additionally labelled **DISCOVERED EXTERNAL**. This is
an Archive Intake presentation state, not permission to write the device. The
same read-only/no-recovery inspection is required, and automatic mounting and
formatting remain disabled. Its durable preview includes the oldest and newest
reported file timestamps, extension distribution, read/stat errors, case and
Unicode collision counts, bounded top-level folder/file names, and counts of
permission conditions that deserve review (set-ID files, world-writable items,
and owner-unreadable files). These are evidence, not claims that the source is
unsafe. Selection/filter planning remains a separate approval step.

MD/LVM/ZFS member groups remain non-activating. Their metadata preview can
identify stack membership and provider-supported completeness, but Hoardarr
does not activate those stacks until a separate read-only assembly executor is
implemented and reviewed. Unraid independent data disks can be inspected one
at a time with persisted reports before copy selection. Multi-disk copy
selection, collision planning, verified intake, adoption, and other NAS-origin
adapters remain separate operations and are not implied by either preview.

## Principles

- Discover first; do not mount merely to determine what a device contains.
- Treat a collection of related disks as a candidate source system, not as
  unrelated standalone media.
- Report confidence and evidence for every detected platform, pool, array,
  filesystem, and member role.
- Default to read-only inspection under a private root-owned temporary path.
- Use filesystem-, pool-, and volume-manager-specific no-recovery behavior.
- Never assume that a generic read-only flag prevents journal replay or other
  metadata changes.
- Require all expected members, or an explicitly supported degraded-import
  policy, before assembling storage.
- Separate inspecting, copying, and adopting into distinct approvals.

## Discovery and fingerprinting

The fingerprinting phase records:

- partition tables and partitions;
- filesystem type, UUID, label, features, and dirty/recovery state;
- ZFS pool and vdev identifiers;
- Linux MD RAID metadata and member roles;
- LVM physical volumes, volume groups, and logical volumes;
- Btrfs filesystem and device membership;
- Unraid-related independent data-disk and pool evidence;
- Synology, QNAP, TrueNAS, and other NAS layout evidence where a reviewed
  adapter exists;
- Windows Storage Spaces and other recognized foreign-storage signatures;
- encryption signatures without requesting or storing keys during discovery;
- expected, present, missing, duplicate, foreign, and ambiguous members; and
- stable identity, controller, enclosure, bay, and current path for every disk.

The result is one or more candidate source groups. Each group includes a source
profile, confidence, supporting evidence, unresolved questions, and available
import modes.

Hoardarr must not guess that an unrecognized disk is parity, empty, or safe to
format. Unknown members remain unknown.

## Source profiles

Initial adapters should prioritize common media-hoarding sources:

- standalone ext4, XFS, Btrfs, NTFS, and exFAT disks;
- Unraid data disks and pools;

## Verified file migration into managed storage

After a complete, current read-only inventory, an independently readable data
filesystem can be copied into an active Storage Group backend. The migration
plan binds the hardware snapshot, reviewed stable device identity, filesystem
type and UUID, inventory operation and hash, destination backend identity,
filesystem device number, free space and reserve, verification mode, and
collision policy. A changed discovery snapshot, source signature, destination
identity, destination filesystem, or insufficient capacity fails closed.

The durable worker mounts the source at a private per-operation path with the
reviewed filesystem-specific read-only/no-recovery options. It then inventories
the source again, checkpoints every regular file, copies with descriptor-relative
no-follow operations and atomic no-replace publication, and verifies the result.
Accurate mode uses BLAKE3; fast mode verifies size and modified time. Pause,
resume, and stale-worker recovery continue from durable file checkpoints. ARR
activity pauses placement at a safe checkpoint rather than competing with an
active import or download.

Collision behavior is explicit:

- **Stop before replacing anything** refuses every existing destination path.
- **Reuse only identical files** hashes both files and reuses the destination
  only when its bytes, size, and preserved modified time match.

The source is never deleted, adopted, added to automatic mounts, or changed to
read-write. Unraid parity is not file content and is refused by this workflow;
Hoardarr does not claim parity validity or reuse. The final Activity report
records copied, reused, and verified counts, bytes, destination, verification
method, source-retention state, and the explicit absence of parity reuse.

Archive intake can copy everything, an explicit set of top-level folders/files,
or a bounded custom filter made from extensions and relative include/exclude
patterns. Selection paths are normalized, traversal/absolute paths and control
characters are rejected, and at most 64 values are accepted per filter field.
For a partial selection, the reviewed inventory is displayed as a full-source
capacity upper bound. The worker rebuilds the complete source inventory,
verifies it still matches the reviewed count and bytes, creates checkpoints
only for matching regular files, then proves the exact selected bytes plus the
reserve fit before copying begins. An empty selection fails safely. The final
report records the immutable selection alongside the verified manifest totals.
- ZFS pools from TrueNAS or another Linux/Unix host;
- Linux MD RAID and LVM stacks used by common NAS distributions;
- Synology and QNAP layouts where the complete stack can be identified safely;
  and
- unknown Linux or foreign media that can only be imaged or inspected
  manually.

An adapter defines its signature rules, member grouping, safe inspection
procedure, supported degraded states, mount behavior, blockers, and adoption
capability. Unsupported profiles may still produce useful evidence but cannot
reach execution.

## Import modes

### Inspect read-only

Make files available for browsing and planning without adopting the source.

- Assemble only through a reviewed source adapter.
- Disable recovery, replay, repair, upgrade, scrub, and write-intent behavior.
- Use a private mount namespace and Hoardarr-controlled temporary paths.
- Do not add persistent boot-time activation.
- Verify after mounting that every source device remains read-only and that no
  unexpected holder or writer appeared.
- Record any limitation that prevents Hoardarr from proving a zero-write path.

If the adapter cannot prove a safe read-only procedure, inspection is blocked
and the operator is offered imaging or manual recovery guidance instead.

### Copy into Hoardarr

Leave the source storage unchanged and migrate selected data to managed
storage.

- Run read-only inspection first.
- Preflight destination capacity and path conflicts.
- Use a durable migration manifest.
- Preserve selected timestamps, permissions, ACLs, extended attributes,
  sparse-file properties, and hardlinks when supported.
- Verify using the operator-selected policy.
- Never remove source data as an automatic consequence of import.

Migration and verification share the mechanisms defined in
[decommission.md](decommission.md), but import does not proceed into unmount or
sanitization unless the operator starts a separate decommission workflow.

### Adopt in place

Continue using the existing storage structure as Hoardarr-managed storage.

Adoption is available only to source profiles with a reviewed persistent import
and recovery model. It requires an additional plan that explains all changes
from temporary read-only inspection to normal managed operation.

## ZFS adoption

ZFS is the primary first-class adopt-in-place case. Hoardarr should allow a
healthy, compatible pool moved from another server to be imported and continue
operating with its existing pool and dataset identities.

The workflow is:

1. Discover pool and vdev metadata without importing the pool.
2. Match every available vdev to stable physical identities.
3. Report pool GUID, name, feature compatibility, health, host history, missing
   or faulted members, encryption state, and dataset inventory.
4. Perform a temporary no-mount, read-only inspection with no persistent cache
   or automatic activation.
5. Present datasets, mountpoints, quotas, snapshots, properties, and any
   compatibility or name conflicts.
6. Let the operator choose read-only inspection, data copy, or adoption.
7. For adoption, create a separate persistent-import plan covering mountpoint
   policy, cache/activation behavior, monitoring, scrub policy, and recovery.
8. Revalidate the complete vdev set and approval immediately before changing
   the pool from inspection to managed operation.

Hoardarr must not automatically force an import, clear a host-safety condition,
upgrade pool features, change dataset properties, or start a scrub during
discovery.

## Import assessment

Each candidate group receives one of these states:

- `ready-read-only`: a reviewed adapter can inspect it without writes;
- `ready-copy`: files can be read and copied into managed storage;
- `ready-adopt`: the source supports a complete reviewed adoption path;
- `degraded-review`: an adapter recognizes the source, but members or health
  require operator review;
- `blocked`: a known safety or compatibility condition prevents execution; or
- `unknown`: evidence is insufficient to classify the storage safely.

The interface must show why each mode is or is not available.

## Workflow states

```text
discovered -> fingerprinted -> grouped -> reviewed
           -> inspecting-read-only
           -> ready-copy -> copying -> verified
           -> ready-adopt -> adoption-approved -> adopted
           -> degraded-review | blocked | unknown | needs-attention
```

Any device-identity change, newer conflicting hardware snapshot, changed member
set, or detected writer invalidates the import approval.

## Audit and evidence

Import records the candidate-group manifest, member identities, raw signatures,
adapter/version, commands or library operations, mount namespace, mount options,
before/after block-device state, bytes read, migration manifest, verification
result, and all operator decisions. Sensitive keys and unrelated file contents
are excluded.
