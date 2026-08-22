# Release-readiness checklist

- [x] No requirement remains `NOT IMPLEMENTED`; executable software paths are
  `VERIFIED` or `VERIFIED IN ISOLATION`, with physical/provider gates recorded
  separately.
- [x] Locked backend, frontend, accessibility, browser, migration, bootstrap,
  release, telemetry-soak and production-build checks pass on Windows and
  Ubuntu 24.04 CI.
- [x] The Ubuntu release bundle installs from its offline locked wheels, starts
  its systemd units, migrates a fresh database, serves the production frontend,
  passes health checks and reapplies idempotently.
- [x] Versioned release activation and tested rollback preserve configuration
  and database state.
- [x] Four newly created loop-backed ext4 devices pass the mergerFS persistent
  telemetry workload, including browser-disconnected collection, collector
  restart, rollups, writes-today and cleanup.
- [x] Hosted Ubuntu disposable loops pass ext4/POSIX ACL, MD RAID6/XFS, ZFS
  RAIDZ2/snapshot/scrub and SnapRAID sync/status/diff/check execution.
- [x] The appliance ISO builds from the pinned Ubuntu 24.04.4 image and boots in
  QEMU/TCG to the Ubuntu installer/autoinstall checkpoint.
- [x] Critical browser workflows and automated accessibility checks pass.
- [x] Standard and differential security scans were completed; actionable
  findings were corrected and regression-tested. Dependency, secret, static,
  malformed-input and query-bound checks were rerun on the corrected tree.
- [x] Hardware claims are limited to the evidence in
  `hardware-certification.md`; no physical Cisco SSD was modified.
- [ ] Matching physical controller, shelf, multipath, FC/FCoE and storage-stack
  certification is complete.
- [ ] The managed repository-wide Deep Security Scan release gate is executed
  or formally waived.
- [ ] A production release-signing public trust root and offline private key are
  provisioned by the release owner.

Current decision: **SOFTWARE-READY; PHYSICAL HARDWARE CERTIFICATION PENDING**.

The software release candidate has reproducible Ubuntu 24.04 CI evidence for
build, install, systemd activation, web health, isolated destructive storage,
persistent telemetry, release switching, rollback, appliance construction and
QEMU boot. Publication remains outside this validation task.

The application API and durable worker intentionally run as root in the current
appliance design. Narrow typed executors, private Unix sockets, path/device
revalidation and systemd restrictions reduce exposure, but a compromise of
those network-facing processes retains root-level blast radius. This is an
accepted product decision, not a resolved privilege-separation claim.

Formal release gate pending: managed Deep Security Scan could not execute
because the parent environment did not provide the required managed filesystem
permission profile. Exact error:

`Deep Scan cannot safely start a read-only worker: the parent must provide a managed filesystem permission profile.`

The scan workflow prohibited retry; no result was inferred and fallback review
is not represented as equivalent assurance.
