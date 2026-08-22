# Storage decommission

Decommission safely removes media or an existing storage system from service.
It separates data migration, verification, unmounting, sanitization, and
certification so the operator can choose the appropriate outcome without being
forced to wipe a device.

The primary workflow is:

```text
Select -> Migrate or skip -> Verify -> Quiesce -> Unmount
       -> Detach or sanitize -> Verify sanitization -> Certify
```

Skipping migration, choosing a destructive sanitization method, or accepting
incomplete verification requires an explicit recorded decision.

## 1. Select scope

The operator may decommission:

- one standalone disk;
- one member that a supported layout can safely remove;
- an entire pool, array, tier, or imported source group;
- a share or presentation path while preserving its backing storage; or
- a foreign system after a completed import or migration.

Hoardarr identifies every affected mount, share, application path, pool, array,
holder, and device. It blocks the plan if the scope is ambiguous or if removing
a member would violate a supported storage layout's safety rules.

## 2. Migrate data

The first operator choice is **Migrate data: yes or no**.

If migration is skipped, Hoardarr shows the detected data, estimated allocated
bytes, affected paths, and the consequence of continuing. Skipping migration
never implies that the source is empty.

If migration is selected, Hoardarr requires:

- a destination with sufficient usable and reserved capacity;
- a durable source-to-destination manifest;
- explicit conflict behavior;
- handling for files that change during migration;
- preservation policy for timestamps, ownership, permissions, ACLs, extended
  attributes, sparse files, and hardlinks;
- open-writer detection; and
- a selected verification policy.

The copy is resumable. The source remains authoritative until verification
passes and the operator approves the transition.

## Verification options

### Quick

- Relative path and file type.
- File size.
- Selected basic metadata.

This detects missing or obviously incomplete copies but does not prove identical
contents.

### Balanced

- Everything in Quick.
- Full-file fast checksum on source and destination.
- Selected metadata, ACL, extended-attribute, sparse-file, and hardlink checks.

CRC32 or CRC32C may be offered for accidental-corruption detection. The UI must
state that CRC is not a cryptographic integrity proof.

### Thorough

- Everything in Balanced.
- Full cryptographic digest of every source and destination file.
- Complete selected metadata comparison.
- Optional second read of failed or unstable files before final disposition.

### Custom

The operator selects algorithms, full or sampled reads, metadata fields,
hardlink behavior, retry policy, and allowed exceptions. The certificate records
the exact policy rather than describing a custom result as simply verified.

## Migration exceptions

Hoardarr pauses for explicit handling of:

- unreadable source files;
- destination write or capacity failures;
- path collisions and case-sensitivity conflicts;
- files that change while being copied;
- unsupported names or metadata;
- broken hardlinks or sparse-file expansion;
- unknown writers; and
- digest mismatches.

Source data is not automatically deleted after verification. Decommission moves
to quiescing and unmounting while the source remains recoverable.

## 3. Quiesce

Before unmounting, Hoardarr:

- pauses or disables affected Hoardarr-managed shares;
- identifies connected ARR applications and affected root or download paths;
- detects open files and reports the responsible processes;
- stops only explicitly managed consumers included in the approved plan;
- blocks new writes to the decommission scope;
- flushes pending filesystem writes;
- verifies migration has completed or records that it was skipped; and
- creates a final pre-unmount source manifest.

Hoardarr does not kill an unknown process or stop an unrelated service merely
to make unmounting succeed.

## 4. Unmount

Unmount is an independent mandatory gate before detach or sanitization.

Hoardarr checks:

- normal, bind, nested, and private-namespace mounts;
- swap use;
- device-mapper and LVM holders;
- MD RAID, ZFS, Btrfs, mergerFS, cache, and block-target membership;
- loop devices and open block-device handles;
- container or virtual-machine consumers;
- `/etc/fstab`, systemd mount units, udev rules, pool caches, and other
  Hoardarr-managed automatic activation; and
- boot, root, recovery, and active system dependencies.

The plan unmounts leaf paths before parents, removes or disables only
Hoardarr-managed activation entries, and then proves that the device is no
longer mounted, held, shared, or scheduled for automatic reactivation.

Failure to prove a clean unmount stops the workflow. Forced unmount is not a
silent fallback; if it is ever supported, it is a separate high-risk action
with its own explanation and approval.

The operator may finish here with **Detach only**, leaving all data intact.

## 5. Choose decommission method

Available methods depend on the media type, controller path, passthrough
capability, encryption state, and device-reported sanitize support.

### Preserve data

- Unmount and detach only.
- Export or deactivate the pool/array using its native safe procedure.
- Preserve partition tables, filesystems, and data.

### Quick reset

- Remove selected signatures or create a new empty filesystem.
- Intended for reuse, not secure sanitization.
- Clearly labelled **Quick format is not secure erasure**.

### Host overwrite

- One-pass zero overwrite.
- One-pass random overwrite.
- Operator-selected multi-pattern overwrite where justified.
- Full post-write verification or a documented verification sample.

Hoardarr records the accessible range written and must not imply that hidden,
remapped, overprovisioned, or controller-inaccessible areas were overwritten.

### Device-native sanitization

- ATA security erase or sanitize when safely supported.
- NVMe format, sanitize, or cryptographic erase when safely supported.
- SCSI sanitize when safely supported.
- Cryptographic erase only when Hoardarr can establish the relevant encryption
  and key-destruction conditions.

Commands are exposed only through typed, device-specific adapters. Hoardarr
must verify that the controller or USB bridge actually passes the selected
operation to the intended media.

### Standards-oriented outcome

The interface may organize supported methods into Clear, Purge, or Destroy
outcomes. The plan records the exact device capability, method, execution, and
verification evidence. It must not claim standards compliance from a generic
label alone.

### Physical destruction

Hoardarr can record an operator-attested destruction event, identifiers,
method, witnesses, and attachments. Software cannot independently prove a
physical event, so the certificate marks it as operator-attested.

## 6. Sanitization verification

Verification is method-specific and may include:

- device sanitize status and completion evidence;
- rereading selected or complete accessible ranges;
- proving prior partition, filesystem, pool, and volume signatures are absent;
- verifying cryptographic-key destruction evidence;
- confirming that the original serial and stable identity still match; and
- recording inaccessible areas or controller limitations.

An interrupted or ambiguous destructive operation finishes as
`needs-attention`, never as passed or safely reusable.

## Certificate of Data Burial

After a completed decommission, Hoardarr generates a downloadable pirate-themed
PDF titled **Hoardarr Certificate of Data Burial** and a machine-readable JSON
evidence document.

The certificate includes:

- certificate, operation, plan, and evidence-manifest identities;
- operator and optional witness identities;
- start and completion timestamps;
- device model, serial, WWN, capacity, controller, enclosure, and bay;
- source pool, array, filesystem, or import-group identity;
- whether migration was performed or skipped;
- migration totals, exceptions, and verification method;
- unmount and automatic-reactivation checks;
- selected decommission and sanitization method;
- tool/provider and Hoardarr versions;
- sanitization verification result;
- limitations, warnings, inaccessible regions, or operator attestations; and
- a SHA-256 digest of the accompanying JSON evidence manifest.

The visual certificate may use a Hoardarr/pirate seal and language such as:

> These bits have been sent to Davy Jones' locker.

The serious disclaimer remains visible: the certificate records what Hoardarr
executed and observed. It is not a legal guarantee and does not conceal
incomplete, failed, or operator-attested evidence.

## Workflow states

```text
draft -> reviewed
      -> migration-skipped | migrating -> migration-verifying -> migrated
      -> quiescing -> unmounting -> unmounted
      -> detached
      -> sanitizing -> sanitize-verifying -> sanitized
      -> certified
      -> blocked | failed | cancelled | needs-attention
```

Approvals are invalidated by a changed device identity, source membership,
destination, migration policy, verification policy, decommission method, or
newer conflicting hardware snapshot.

