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
- 32 MiB written once to the shared virtual LUN;
- one 4 MiB controlled write and a 12-second, 1 MiB/s, 95%-read mixed workload, with the
  mixed phase's full 12 MiB worst-case write volume reserved against both possible
  mergerFS destination members;
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

**TWO-NODE VIRTUAL STORAGE FAILOVER: VERIFIED IN ISOLATION.** Ubuntu workflow
`32599605672` completed in 6m04s. It used four test-created local virtual SSDs plus one shared
virtual LUN presented through two independent SCSI paths per node. Both nodes ran the installed
0.3.11 wheel, production frontend, API and durable worker under systemd.

The executed run recorded 75,497,472 bytes of conservatively planned payload against a 96 MiB
hard limit. Linux block counters observed 77,455,360 bytes including filesystem metadata. The
shared SHA-256 remained
`5230d111b27f7c6ff1434e545765d7e0ac9778ac373d2690e64d049fdbfe8dc9` across the controlled
Node A to Node B ownership handoff. The shared filesystem UUID
`a8aa442f-6e31-4446-8248-5ada9ac4a09f`, stable WWID `36000000000000011`, mapper and mount path
were unchanged.

The first successful graph-evidence run exposed and then verified a correction to automatic
history resolution: a fresh node now returns its bounded raw samples until an hourly rollup is
actually available. The post-correction screenshots contain 87 plotted commands in Node A's
largest path series and 92 on Node B, rather than empty chart shells. Both nodes rendered persisted
throughput, IOPS, latency and path-state changes collected while no browser was connected.

After 80 real controller-chart mount/unmount cycles per node, forced-GC heap growth after warm-up
was 813,699 bytes on Node A and 829,612 bytes on Node B, within the 16 MiB acceptance envelope.
Worker RSS changed from 97,460 to 101,808 KiB on Node A and 97,228 to 103,584 KiB on Node B;
both remained at two threads and below their unchanged 130,264/130,268 KiB high-water marks.

The artifact includes phase timestamps, fio JSON, Linux iostat output, full bounded telemetry,
events, hashes, write counters, worker memory, browser heap measurements, traces, and ten actual
production-UI screenshots. The graph workload issued 412,999,413,760 read bytes over repeated
read-heavy phases. Fio and Hoardarr are compared by time window with explicit semantics: fio is
workload-issued IO; Hoardarr host telemetry is Linux block-layer IO and can include mapper/path
layers, so phase direction, shape and order of magnitude—not byte equality—are the acceptance
basis.

The repository subsequently expanded the final profile to fail and recover both paths, restart
multipathd under IO, and record bounded mixed read/write activity. Final reconfirmation run IDs are
reported in the release validation result and task completion report.

**TWO-NODE FOUR-PHYSICAL-SSD VALIDATION: PENDING.** No repository or environment evidence marks
the four Cisco SSD-240G devices disposable. They were not repartitioned, formatted, mounted or
written by this profile. Physical temperature and endurance deltas are therefore Not reported,
not inferred from virtual devices.
