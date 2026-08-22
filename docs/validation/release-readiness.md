# Release-readiness checklist

- [x] No requirement remains `NOT IMPLEMENTED`; all executable software paths
  are `VERIFIED` or `VERIFIED IN ISOLATION`, with environment/hardware gates
  recorded separately.
- [x] Both clean validation passes have no failures or unexplained skips; the
  two environment-specific skips are recorded.
- [x] Production backend wheel and frontend builds pass from locked dependencies.
- [x] Empty-database and every repository-supported migration revision direct to
  current pass.
- [ ] Release bundle install, health check, atomic switch, and rollback pass in Ubuntu 24.04.
- [ ] Disposable-disk destructive safety tests pass on newly created devices.
- [x] Critical browser pairing, navigation, Advanced controls, durable apply
  reload, one-time credential, theme and navigation paths pass; automated
  accessibility auditing passes across primary pages.
- [ ] Security fallback checks pass and the managed Deep Security Scan gate is resolved or explicitly waived by release policy.
- [x] Documentation matches the tested implementation boundary.
- [x] Hardware claims are limited to the evidence in `hardware-certification.md`.
- [x] No known data-loss, authentication, authorization, secret, migration, or
  durability defect remains in the software and isolated paths exercised here.

Current decision: **NOT RELEASE-READY**.

The decision is driven by missing Ubuntu/Linux execution evidence for the
release bundle, systemd services, disposable block-device harness and appliance
boot, plus the unresolved managed scan gate and physical hardware certification.
It is not driven by remaining `NOT IMPLEMENTED` rows; that count is zero.

Ubuntu 24.04 CI definitions now include release-bundle construction, offline
hashed wheel installation from both generated lock manifests,
`systemd-analyze verify`, a QEMU serial-console appliance
boot checkpoint, live `/proc`/`/sys` telemetry, and a four-loop mergerFS
persistent-telemetry workload. Workflow YAML and shell syntax pass local static
validation. The worktree has not been committed or pushed, so GitHub has no
execution result for these new jobs; their state is **CI IMPLEMENTED — EXECUTION
RESULT PENDING**, not verified.

Formal release gate pending: managed Deep Security Scan could not execute
because the parent environment did not provide the required managed filesystem
permission profile.
