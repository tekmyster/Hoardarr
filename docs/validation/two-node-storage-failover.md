# Two-node storage failover validation

This profile separates two storage behaviors that must not be confused:

- **Controller/path failover:** one logical LUN remains online through another path.
- **Storage-node ownership handoff:** one node unmounts a shared ext4 filesystem before the peer
  mounts it. This is a controlled single-writer transition, not automatic clustered HA.

## Disposable topology

The repository-controlled job in `.github/workflows/two-node-storage.yml` boots two Ubuntu 24.04
QEMU virtual machines. Each runs the Hoardarr API and durable worker as real systemd services and
receives two test-created 768 MiB virtual SSDs. A separate test-created 768 MiB shared LUN is
presented through two SCSI controllers to each node for DM-Multipath and controlled ownership tests.

No physical disk is selected by this profile. The four Cisco SSDs remain outside its scope unless
their exact stable identities are separately designated disposable.

## SSD-safe write budget

The default hard limits are:

- 96 MiB planned payload writes across the complete test;
- 64 MiB planned payload writes to any one virtual device;
- 9 MiB written once to each local virtual SSD;
- 36 MiB written once to the shared virtual LUN;
- repeated stress and soak phases are read-only.

The harness calculates the plan before booting the workload and exits before IO if either limit is
exceeded. It records Linux block-layer write-counter deltas separately because filesystem metadata
means observed OS writes can be larger than payload bytes.

## Workload and evidence

`tests/integration/run-two-node-storage-graph-stress.sh` records UTC phase timestamps for idle,
sequential read, random read, limited write, mixed-size read, path failure, path recovery, API-down
workload, node ownership handoff, post-handoff read soak, worker restart, and final idle.

The evidence artifact contains stable identities, fio results, persistent samples and rollups,
events, Linux reference output, worker memory observations, hashes, write counts, and both serial
console logs.

Telemetry is collected by each node's durable worker. No browser is connected while the workload,
path failure, API outage, recovery, or ownership handoff occurs. Historical evidence is exported
afterward from persistent storage.

## Product boundary

The profile validates controlled ownership movement between two real Hoardarr nodes. Hoardarr does
not currently claim automatic two-node fencing, quorum, or clustered filesystem semantics. Shared
ext4 is mounted by exactly one node at a time. Automatic storage-node HA requires a provider with a
safe ownership and fencing contract; controller/path failover remains independent and online after
DM-Multipath is active.

## Result

CI execution evidence is recorded in `docs/validation/validation-results.md` after the workflow has
run. Physical four-SSD validation remains a separate certification item.
