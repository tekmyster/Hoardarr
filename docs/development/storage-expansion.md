# Storage expansion planning

The Storage page includes a read-only expansion assessment bound to the latest persisted hardware
snapshot and current Storage Groups. Loading or refreshing that assessment does not assign, reserve,
format, mount, or otherwise change a disk.

`GET /api/v1/storage/expansion` returns:

- currently unassigned registered disks and their stable identities;
- disks deliberately reserved for later, separated from executable candidates;
- current Storage Group namespace total/used/free capacity when its filesystem reports it;
- per-member utilization spread using distinct filesystem identities, explicitly presented as
  context rather than a defect;
- configured data/parity backend counts;
- health and presence blockers;
- existing-data state from partition, signature, and signature-scan evidence;
- detected mergerFS, SnapRAID, and ZFS capabilities;
- candidate plans with raw capacity, estimated usable capacity, calculation methodology,
  protection impact, future expansion implications, restrictions, and migration work.

Existing partitions, filesystem signatures, or incomplete signature scans cause an import-first
recommendation. Hoardarr does not produce a formatting recommendation by pretending an uncertain
disk is blank. Capacity is `Not calculated` when the source filesystem has not been inspected.

`system_disk` and `system_device` discovery evidence are absolute exclusions. Protected storage can
remain visible with its blocker so the operator understands why it is unavailable, but it never
produces an import, new-pool, tier, or expansion candidate. A registry entry absent from the latest
snapshot is likewise blocked rather than planned from a stale kernel path. The panel reloads its
assessment automatically after a successful hardware scan; manual refresh remains available for a
retry.

For a completely scanned blank disk, the planner can currently describe:

- an independent Storage Group;
- an additional mergerFS member when a mergerFS pool is actually detected;
- an explicit SnapRAID data-member role when the exact configuration is evidence-matched to the
  mergerFS branches, plus a separate parity-member choice when the disk is large enough;
- an SSD/NVMe download tier for an existing media Storage Group; and
- a matched two-disk ZFS mirror/new mirror vdev candidate with smallest-member capacity math.

An existing mergerFS candidate is emitted only when exactly one discovered instance can be tied to
the Storage Group by its namespace mount or an active backend branch. Merely detecting mergerFS
somewhere on the host is not sufficient. The candidate carries that instance ID and mountpoint into
the wizard; the normalized storage answer binds the candidate ID, selected disk identities, target,
and assessment snapshot digest into the immutable plan. A changed snapshot or changed target is
rejected instead of silently choosing the first pool.

The UI's action opens the existing Guided or Advanced storage wizard with the candidate disks
selected. The wizard remains the canonical persistent plan, identity revalidation, explicit
approval, durable operation, and execution boundary. The assessment itself is deliberately not a
second apply mechanism.

During existing mergerFS execution, the worker re-discovers the active instance and rejects branch
or identity drift before changing runtime membership. Its existing `/etc/fstab` entry is replaced in
the same atomic write that persists the new member filesystem entry, avoiding duplicate mergerFS
mount definitions. The runtime command has a rollback path if branch activation or verification
fails. This software path is covered with deterministic executor tests; disposable Linux execution
remains tracked separately from the implementation claim.

SnapRAID topology is read from a bounded local configuration parser. It exposes only data, parity,
and content paths plus a configuration digest; unrelated directives and exclusion patterns are not
returned through the inventory API. An expansion plan binds the exact `snapraid:<name>` identity,
configuration SHA-256, and the selected `data` or `parity` role. The worker refuses a changed
configuration. A data member is added to both mergerFS and SnapRAID; a parity member is mounted and
added only to SnapRAID, so it is never counted as usable media capacity. The updated configuration
is validated and synchronized with structured `snapraid -c ... status/sync` arguments; adding a
new parity level uses SnapRAID's required `--force-full sync` rather than treating an empty parity
file as an ordinary incremental sync. Validation,
runtime activation, or persistent-mount failures before synchronization restore the prior
configuration and runtime membership. Once synchronization starts, the expanded configuration and
mount are deliberately retained on failure so files written to a newly active data member cannot
be hidden by an unsafe rollback. The operation enters needs-attention state and explains that parity
is stale until a later sync succeeds; parity is never described as current merely because the disk
was added.

An operator may explicitly choose **Reserve for later** on a single candidate. That authenticated,
CSRF-protected API action changes only the durable physical-disk lifecycle state; it never opens the
device or writes storage metadata. Reserved disks are excluded from wizard assignment and expansion
candidates until **Release disk** is used. The transition is idempotent, audited, and independently
rejects protected system storage.

Current limitations are recorded in the unified roadmap: additional ZFS multi-vdev expansion
recommendations and the disposable-Linux expansion fault matrix remain in the EXPAND dependency
family.
