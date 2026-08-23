# Product and architecture reconciliation

This decision records how older design material is interpreted against the current 0.3.11 code.

## Product scope

Hoardarr is ARR-first home-media storage software. Guided mode explains media storage in normal
language; Advanced mode may expose SAS/SES, FC/FCoE/iSCSI, multipath, controller ownership, ZFS,
Linux MD, mergerFS, and SnapRAID. Those Advanced capabilities do not reposition the product as a
corporate SAN or compute-cluster manager. Single-writer storage safety remains mandatory.

## Deployment

The canonical appliance is Ubuntu, versioned wheel/frontend release bundles, systemd services,
forward Alembic migrations, atomic release switching, and QEMU/Linux validation. Docker may remain
a development tool, but older Compose language does not override the validated appliance model.

## Extensions

There is one extension model, not two marketplaces:

- Built-in, tightly coupled hardware/storage providers run in-process behind provider registries.
- Trusted local add-ons use signed Ed25519 manifests, declared compatibility/privileges, and the
  existing lifecycle.
- Third-party code needing isolation runs as a bounded systemd service with an explicit contract.
- Container-only extensions are not a second canonical runtime.

## Failover traceability

| Capability | Current state |
|---|---|
| Controller/path multipath failover | Implemented and verified in isolated Linux/QEMU tests |
| Controlled node storage-ownership handoff | Implemented and verified in the two-node isolated test |
| Automatic node/controller HA failover | Not implemented |
| Fencing and quorum protection | Not implemented |
| Replicated control-plane state and floating endpoint | Not implemented |

The first two never serve as evidence for the last three. Automatic HA cannot be enabled until
fencing/quorum prevents split-brain and the state-replication contract is proven.
