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

Cached udev evidence is explicitly partial. It can identify a likely signature
but cannot prove that no other on-media signature exists. System storage,
mounted sources, unstable identities, already-managed devices, missing provider
tools, and stack-member uncertainty block automatic inspection. Filesystem and
volume metadata alone never assigns an Unraid, Synology, QNAP, or other product
origin; the UI shows **Not reported** until a reviewed adapter has stronger
evidence.

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

MD/LVM/ZFS member groups remain assessment-only. Hoardarr does not activate
those stacks until each provider has a reviewed no-activation completeness and
health preview followed by its own read-only assembly executor. Copy intake,
adoption, Unraid classification, and NAS-origin adapters remain later tasks and
are not implied by standalone inspection.

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
