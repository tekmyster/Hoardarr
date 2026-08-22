# Hoardarr backend

The backend is the root host-management API and durable job worker for Hoardarr. It is
API-first: the web interface and external automation use the same versioned
routes and operation records.

This vertical slice supports authenticated administration, immutable hardware
snapshots, durable operations, first-run network planning, resumable wizard
sessions, immutable storage plans, plan-bound `I AGREE` approval, production
frontend serving, Servarr connection discovery, and managed SMB, NFS, iSCSI,
and FCoE connectivity. It does not expose an arbitrary command runner.

## Quick development checks

From the repository root:

```sh
make backend-sync
make backend-lint
make backend-test
make backend-build
```

Use `make verify` from the repository root for the combined backend lint/tests
and frontend tests/build.

`backend/uv.lock` is the dependency lock. Update it deliberately with
`make backend-lock`; normal sync, lint, and test commands refuse to rewrite it.

The preferred first-run command is `hoardarr setup`. The package also installs
`hoardarr-migrate`, `hoardarr-setup-token`, `hoardarr-api`, and
`hoardarr-worker` for service and compatibility workflows.

The complete local setup flow, service layout, route inventory, authentication
rules, and current limitations are documented in
[`../docs/development/backend.md`](../docs/development/backend.md).
