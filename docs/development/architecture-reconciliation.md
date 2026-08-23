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

### Canonical provider boundary

Provider API version `1` has two deliberately different trust boundaries:

| Provider category | Runtime | Allowed responsibility | Not allowed |
|---|---|---|---|
| Built-in hardware providers | Hoardarr process | Bounded read-only collection, parsing, normalization, capability detection | Arbitrary command strings, storage mutation, third-party module loading |
| Built-in storage providers | Hoardarr process plus the fixed storage executor contract | Pure planning/validation and calls to typed executor operations | Direct browser-supplied commands or bypassing identity/system-disk checks |
| Built-in ARR/media providers | Hoardarr process | Product-aware bounded HTTP adapters, preview, and approved supported writes | Generic proxying, arbitrary URLs after validation, secret logging |
| Signed local add-ons | Dedicated systemd service | Manifest-declared API, package, privilege, schema, UI and update compatibility | Loading third-party Python into the API/worker process |

The hardware registry publishes `api_version`, `execution_model`, and `trust`
for every built-in provider. `/api/v1/system/capabilities` publishes the same
boundary so installed releases can be audited. A new built-in provider requires
repository review, bounded input/output, timeout/error tests, and a current API
version. A third-party provider uses the one signed local add-on lifecycle; it
does not create another plugin marketplace or gain an in-process escape hatch.

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
