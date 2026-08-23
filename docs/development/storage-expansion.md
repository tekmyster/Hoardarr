# Storage expansion planning

The Storage page includes a read-only expansion assessment bound to the latest persisted hardware
snapshot and current Storage Groups. It does not assign, reserve, format, mount, or otherwise change
a disk.

`GET /api/v1/storage/expansion` returns:

- currently unassigned registered disks and their stable identities;
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
- the SnapRAID resynchronization/parity-size consequence when SnapRAID is actually detected;
- an SSD/NVMe download tier for an existing media Storage Group; and
- a matched two-disk ZFS mirror/new mirror vdev candidate with smallest-member capacity math.

The UI's action opens the existing Guided or Advanced storage wizard with the candidate disks
selected. The wizard remains the canonical persistent plan, identity revalidation, explicit
approval, durable operation, and execution boundary. The assessment itself is deliberately not a
second apply mechanism.

Current limitations are recorded in the unified roadmap: richer current-state capacity/forecast
analysis, additional ZFS vdev geometries, explicit SnapRAID parity expansion selection, reserve-disk
persistence, and end-to-end immutable apply evidence remain in the EXPAND dependency family.
