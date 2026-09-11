<p align="center">
  <img src="website/dev.hoardarr.com/assets/hoardarr-wordmark.png" width="560" alt="Hoardarr">
</p>

<h1 align="center">Hoardarr</h1>

<p align="center"><strong>ARR-first storage lifecycle management for real homelabs.</strong></p>

Hoardarr is a free storage control plane for media enthusiasts, data hoarders, and homelab operators. It combines a guided, goal-first WebUI with access to the meaningful layers underneath: disks, paths, vdevs, pools, datasets, zvols, shares, LUNs, telemetry, import, recovery, and eventually multi-host availability.

The design target is simple:

- approachable enough for someone building their first media server;
- fast and opinionated enough for a typical ARR stack;
- deep enough for an experienced storage engineer to inspect and tune;
- one configuration and execution engine underneath both Standard and Advanced modes;
- every feature available to the community.

> **Alpha ISO:** coming soon. Please follow this repository for release announcements and published checksums.

## Product principles

1. **ARR community first.** Media libraries, downloads, stable application paths, gradual disk growth, and practical homelab hardware lead the design.
2. **Wizard ease by default.** Detect what can be known, recommend sensible defaults, and ask targeted questions about choices that shape the result.
3. **Advanced depth on demand.** Expose supported storage-layer controls through the same configuration engine and planning workflow.
4. **Fast defaults.** Quick formatting and fast tests are recommended, with full validation available when desired.
5. **Real readback.** Hoardarr confirms each applied setting by inspecting the resulting state.

## What Hoardarr manages

Hoardarr models storage as a related stack:

```text
physical device
  -> path / partition / preparation
    -> redundancy and vdev topology
      -> pool
        -> dataset, filesystem, or zvol
          -> SMB/NFS share or iSCSI LUN
            -> stable application-visible storage
```

The backend provides detection, capabilities, recommendations, plan generation, validation, execution, and readback. The WebUI presents and edits that shared model.

## Capability overview

### Storage creation and lifecycle

- ZFS RAIDZ1, RAIDZ2, and RAIDZ3 guided pool creation
- topology and usable-capacity review before creation
- dedicated system-disk separation
- dataset and zvol-backed storage planning
- scrub, degradation, replacement, resilver, export, and recovery workflows
- SnapRAID and mergerfs lifecycle work for heterogeneous media storage
- stable paths for ARR applications while disks are added, drained, or retired

### Existing storage intake

- metadata-first discovery of existing disks, pools, arrays, and filesystems
- guided import with topology and identity preview
- manual intake fallback when auto-detection is incomplete
- permission policy on import: preserve, remap identities, or reset
- optional drive testing for previously validated arrays
- imported data, ACL, xattr, ownership, and topology continuity checks

### File and block access

- SMB shares for Windows and mixed clients
- NFS exports for Linux, Proxmox, and ARR workloads
- zvol-backed iSCSI targets and LUNs
- initiator mapping and reconnect persistence
- real write/read/hash/delete data-path tests
- Quick format as the recommended option where a new client filesystem is required
- multipath and HA block-storage work in active development

### Observability

- pool and device state
- bandwidth and read/write throughput
- IOPS and latency
- SMART, NVMe, SAS, controller, path, multipath, thermal, and endurance expansion
- lifecycle-operation progress and auditable plan/readback records
- planned top-level, per-user dashboard builder with persistent Grafana-style panels
- graph expansion/compression state that persists across page refreshes
- planned Cisco-style show-tech diagnostic bundle with user-controlled encrypted support packaging

### Availability roadmap

- standalone mode with no heartbeat or quorum requirement
- guided two-node active/passive and active/active modes
- dedicated heartbeat/synchronization link plus shared-storage witness records
- monotonic ownership epochs, peer acknowledgements, fencing, and exactly one writable owner
- optional external witness
- ongoing local ZFS replication with lag and lineage reporting
- multipathed shared SAS/NVMe and iSCSI presentation continuity
- three-plus-node cluster mode built on a proven cluster foundation

HA development is focused on end-to-end failover behavior, continuous service, and clear data ownership.

## Standard and Advanced modes

**Standard mode** starts from the user's goal—media library, downloads/scratch, general files, VM storage, database, backup/archive, mixed use—and derives the relevant topology and tuning from detected hardware.

**Advanced mode** reveals the same plan layer by layer, including supported disk/path facts, vdev layout, pool properties, datasets, zvol geometry, shares, LUNs, multipath, replication, and HA state. The interface clearly identifies detected, derived, recommended, inherited, overridden, immutable, and higher-impact values.

Advanced mode adds deeper control while retaining the shared plan and validation workflow.

## First public Alpha ISO target

The first Rufus-ready image is being prepared around a complete standalone storage-server path:

- Ubuntu 24.04 LTS amd64 appliance installation
- guided system-disk selection and protection
- first-run Hoardarr WebUI
- hardware and existing-storage discovery
- Standard and Advanced configuration views
- ZFS RAIDZ1/2/3 pool creation
- existing ZFS pool discovery and import
- SMB and NFS file presentation
- iSCSI block presentation
- users, groups, and imported-permission handling
- live bandwidth, IOPS, latency, capacity, and health views
- scrub, degraded-pool, replacement, resilver, export, and recovery flows
- offline-friendly release artifact and future update/rollback path

Please follow this repository for the Alpha release announcement and published checksums.

## Development site

- Preview: [dev.hoardarr.com](https://dev.hoardarr.com)
- Production: [hoardarr.com](https://hoardarr.com)

## Architecture

Hoardarr orchestrates proven Linux facilities instead of reimplementing them:

- ZFS for resilient pools, datasets, zvols, scrub, snapshot, and replication primitives
- SnapRAID for parity-oriented heterogeneous media storage
- mergerfs for stable pooled namespaces
- Samba, NFS, and LIO for file and block presentation
- SMART/NVMe/system telemetry for device and path health
- systemd-managed services for appliance operation
- FastAPI/Python backend and a modern WebUI

Hoardarr presents storage settings as an understandable, connected system with complete planning, execution, and readback.

## Project status

Hoardarr is moving toward its first public Alpha. Standalone storage creation, import, file and block data paths, telemetry, and recovery have working proof points. Installer finalization, broader hardware intake, multipath, HA, replication, dashboards, and additional lifecycle coverage are in active development.

## Contributing

The project welcomes focused issues, reproducible hardware observations, UX feedback, and test results from ARR/community storage environments. Proposals are easiest to integrate when they build on the detection -> recommendation -> plan -> validation -> execution -> readback architecture.
