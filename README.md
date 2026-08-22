# Hoardarr

Storage lifecycle management for the ARR ecosystem.

Hoardarr is a storage lifecycle management platform for homelab
operators and large media hoarders running the ARR stack. It provides a
unified control plane and web interface on top of proven Linux storage
tools such as mergerfs, ZFS, and SnapRAID.

## Website

https://hoardarr.com

The Hoardarr website hosts conceptual UI mockups that illustrate the
intended ARR-style interface and storage lifecycle workflows.

## Overview

Hoardarr allows operators to manage heterogeneous storage backends
including:

-   individual disks
-   SnapRAID parity-protected disk sets
-   ZFS pools
-   mixed storage backends
-   foreign storage systems


## Why Hoardarr Exists

Most NAS platforms assume:

-   clean initial architecture
-   static pools
-   planned hardware refresh cycles

Real homelab storage environments evolve gradually:

-   disks are acquired opportunistically
-   storage pools accumulate over time
-   RAID decisions age poorly
-   migrating arrays is difficult and disruptive
-   ARR tools require stable paths

Hoardarr provides lifecycle-aware storage management for these
environments.

## What Hoardarr Is

Hoardarr is an ARR-first storage control plane that allows operators to:

-   grow storage incrementally
-   mix ZFS and SnapRAID strategies
-   drain and retire aging disks safely
-   ingest foreign storage systems
-   maintain stable media paths
-   monitor disk health and parity freshness
-   integrate with homelab automation systems

Hoardarr treats storage as a lifecycle rather than a static pool.

## Core Architecture

Hoardarr orchestrates existing Linux storage tools:

-   **ZFS** for protected pools and scrubs
-   **SnapRAID** for parity protection
-   **mergerfs** for unified namespaces
-   **SMART telemetry** for disk health
-   lifecycle workflows for migration and retirement

## Primary Use Cases

Hoardarr is designed for operators who need to:

-   grow media storage gradually
-   maintain stable paths for ARR tools
-   migrate data from older disks
-   mix storage protection strategies
-   import data from legacy NAS systems
-   ingest external archive drives

## Safety Model

Hoardarr is deny-by-default around storage mutation. The implemented planner
binds a review to the exact wizard revision, hardware snapshot, stable device
identities, and plan digest. Any edit or newer hardware snapshot invalidates
that approval, and destructive plans require the exact phrase `I AGREE`.

Approval is not execution. Storage apply crosses a separate root service only
for complete individual-disk and new mergerFS plans after a host-bound drive
quarantine is prepared. That service accepts no commands or paths from the UI:
it verifies the immutable plan, approval, stable identities, boot/active-use
state, and per-drive locks before each destructive stage. API and worker
services run as root on the managed host. Network storage services are managed
through the same API and durable activity records.

## Observability

Hoardarr collects real host, network, block-I/O, capacity, pool, SMART/NVMe,
controller, enclosure, ZFS, Linux MD, multipath, and Fibre Channel readings when
the local platform exposes them. Normalized readings include stable entity
identity, timestamp, unit, source, collection interval, raw/derived status, and
an explicit quality value. Missing hardware data is **Not reported**, never
estimated or replaced with zero.

Recent history, essential health alerts, and current safety metrics are always
available. Signed, installation-bound capability entitlements gate extended
history, forecasts, percentiles, anomaly/correlation analysis, advanced alert
rules, reporting, and authenticated Prometheus export at the API—not merely in
the browser. See [telemetry and analytics](docs/development/telemetry.md), the
[machine-readable KPI catalog](docs/telemetry/metric-catalog.json), and the
[two-pass telemetry validation](docs/validation/telemetry-validation.md).

## Project Status

Hoardarr is in active beta development with a guarded storage vertical slice:

- an authenticated, API-first FastAPI control plane, durable worker, audit
  records, and forward Alembic migrations;
- read-only hardware discovery with stable identity, connection, sector-size,
  filesystem/signature, and health-provenance fields;
- guided first-run network planning, an ARR-style React storage wizard, and
  authenticated Overview, Storage, Storage Access, Networking, Activity,
  Health, and Settings pages;
- immutable storage plans and explicit approvals bound to the reviewed devices
  and hardware snapshot;
- a deny-by-default quarantine preparer and typed, journaled storage executor
  for individual disks, mergerFS, ZFS, Linux MD, SnapRAID, mixed protected
  pools, tier movement, secure wipe and capability-gated sector conversion,
  including durable restart reconciliation and truthful cancellation boundaries;
- product-aware ARR discovery and writes, granular managed-path ACLs, signed
  direct-to-latest updates and a conservative trusted/local add-on runtime; and
- a reproducible Ubuntu release bundle containing locked Python dependencies
  and the prebuilt web interface.

The wizard can discover, explain, plan, approve, execute, recover and report
supported storage plans. SMB, NFS, iSCSI, FCoE, host networking and destructive
storage operations have capability-gated typed management paths. Unsupported
utilities or hardware fail closed instead of producing a success state. The
bootable appliance pipeline is repository-controlled, but an actual ISO boot and
physical controller/shelf certification remain validation gates.

The validation status is maintained in
[docs/validation/validation-results.md](docs/validation/validation-results.md).
Architecture or fixture support is not a physical-hardware certification.

## Build and test

On the supported Ubuntu 24.04 build host, from the repository root:

```sh
make backend-sync
make verify
make release-plan
make release-build
```

`make verify` runs script syntax checks, backend lint/tests, and frontend
tests/build. `release-plan` is read-only. `release-build` creates a versioned
bundle and must run on the target Ubuntu 24.04 amd64/Python 3.12 platform; see
the release documentation for the complete trust and installation boundary.

## Development

- [Host bootstrap and package profiles](docs/development/bootstrap.md)
- [Disk quarantine and storage autoactivation](docs/development/disk-quarantine.md)
- [Hardware, controller, and enclosure support](docs/development/hardware-support.md)
- [Tiered storage and download workloads](docs/development/tiered-storage.md)
- [ARR folder and API integration](docs/development/arr-integration.md)
- [Backend API and deployment](docs/development/backend.md)
- [Versioned release bundles](docs/development/release-bundles.md)
- [Direct-to-latest updates and migrations](docs/development/updates.md)
- [Telemetry, analytics, retention, and entitlements](docs/development/telemetry.md)
- [Machine-readable metric catalog](docs/telemetry/metric-catalog.json)

## Vision

Hoardarr enables homelab operators to treat storage as an evolving
system rather than a fixed appliance.
