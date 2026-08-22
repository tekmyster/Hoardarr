# Backend development and deployment

The Hoardarr backend is a versioned, API-first control plane. The browser UI and
external clients use the same `/api/v1` resources. It currently inventories the
host, keeps durable operations and audit records, discovers Servarr state,
builds first-run network plans and immutable storage plans, and records an
explicit approval bound to the reviewed wizard revision, hardware snapshot,
device identities, and plan digest. It serves the production-built web
interface from the active release. It can provision the single guided media
application identity and execute complete individual-disk or new mergerFS
plans. It also manages SMB, NFS, iSCSI, and capability-gated FCoE connectivity.
It does **not** apply incomplete array/cache/encryption plans, networking, or
remote Servarr configuration.

## Components

| Component | Responsibility | Privilege |
| --- | --- | --- |
| `hoardarr-api` | HTTP validation, authentication, host discovery, planning, and queuing | Root host-management service |
| `hoardarr-worker` | Claims durable jobs and applies host storage/connectivity changes | Root host-management service |
| Worker-owned telemetry coordinator | Continuously collects, normalizes, persists, rolls up, retains, and evaluates alerts without any API or browser consumer | Same process lifetime as `hoardarr-worker`; read-only providers plus SQLite writes |
| `hoardarr-account-executor` | Creates or resets Hoardarr-managed non-login Linux and Samba media identities | Root, one typed operation over a local Unix socket |
| `hoardarr-storage-executor` | Revalidates and journals a closed set of storage actions after quarantine | Root, local Unix socket, no network |
| SQLite | Users, sessions, operations/events, snapshots, integrations, wizard sessions, plans, and audit events | `/var/lib/hoardarr`, SQLite WAL with full synchronization |
| Alembic | Forward database migrations after key preflight | Explicit `hoardarr-migrate` oneshot |
| Secret box | Encrypts integration API keys and CHAP credentials with an AES-GCM key kept outside the database | `0600` key owned by root |

The API never accepts a command line or shell fragment. It runs as root because
Hoardarr is a host-management appliance. The account executor
accepts only a validated username and password document from the exact local
`hoardarr` UID. It never returns or logs the password. Generated passwords are
returned once by the API and are not persisted by Hoardarr. Hardware detection
is a fixed-argument child process that reads sysfs
and the read-only udev database. It inventories visible disks, stable identity
when available, volatile kernel locators, geometry, partitions, recognized
signatures, connection metadata, storage controllers, transports, DMI platform
data, and tool recommendations. It never opens a block device.

Operations are the durability boundary for work outside the request. The API
commits a queued operation and its audit event; one worker claims it, records
ordered events, and stores either a sanitized result or a safe failure. Hardware
snapshots and wizard plans are immutable. Wizard answers use a revision and ETag
so stale clients cannot overwrite newer answers.

Telemetry deliberately remains inside the existing durable worker rather than
adding another daemon. The worker already has the required independent process
lifetime and failure isolation, so another process would duplicate scheduling
and SQLite writers without improving API/browser independence. The ownership
chain is:

```text
Linux and storage providers
  -> hoardarr-worker telemetry coordinator
  -> SQLite samples, rollups, retention, and alerts
  -> versioned API history queries
  -> bounded browser graphs
```

Stopping the API or closing every browser does not stop collection. Restarting
the worker creates an honest gap for the outage, then resumes against the same
database. Provider health records distinguish available and temporarily
unavailable sources with checked timestamps and sanitized reason codes. The
coordinator uses a fixed worker pool, one in-flight task per provider, timeouts,
capacity backpressure, short SQLite WAL transactions, and idempotent shutdown.
Its internal contract is the versioned telemetry catalog plus SQLAlchemy schema,
not a shell or undocumented shared-file protocol.

## Exact development commands

The backend requires Python 3.12-3.14 and `uv`. From the repository root:

```sh
make backend-sync       # install the project and all locked development groups
make backend-lint       # ruff check, without changing uv.lock
make backend-test       # pytest, without changing uv.lock
make backend-build      # build the wheel under backend/dist/
```

Run the complete backend/frontend gate from the repository root with:

```sh
make verify
```

On an Ubuntu 24.04 amd64/Python 3.12 build host, `make release-plan` previews
the versioned bundle path and `make release-build` builds the complete backend
plus prebuilt-frontend release.

Only dependency updates should run:

```sh
make backend-lock
```

For a local HTTP instance on Linux, use a private temporary state directory:

```sh
cd backend
install -d -m 0700 /tmp/hoardarr-dev
export HOARDARR_ENVIRONMENT=development
export HOARDARR_DATABASE_URL=sqlite:////tmp/hoardarr-dev/hoardarr.db
export HOARDARR_SECRET_KEY_FILE=/tmp/hoardarr-dev/secret.key
export HOARDARR_HARDWARE_DETECTOR="$(pwd)/../scripts/detect-hardware.py"
export HOARDARR_BIND_HOST=127.0.0.1
export HOARDARR_BIND_PORT=7877
export HOARDARR_SECURE_COOKIES=false
export HOARDARR_ALLOWED_ORIGINS='["http://127.0.0.1:7877"]'
uv run --locked hoardarr-migrate
uv run --locked hoardarr-setup-token --ttl 900
```

Copy the printed one-time token, then run the API and worker in separate shells
with the same environment:

```sh
uv run --locked hoardarr-api
uv run --locked hoardarr-worker
```

`uv run --locked hoardarr-worker --once` claims at most one queued operation,
which is useful for deterministic integration tests. API documentation is at
`http://127.0.0.1:7877/api/docs`; liveness and migration readiness are
`/health/live` and `/health/ready`. Prometheus-format process metrics are at
`/metrics`.

## Initial owner setup

On a new installation, `hoardarr-migrate` creates and fsyncs the encryption key
**before** Alembic initializes the schema. If the first schema migration is
interrupted, retrying migration reuses that same key. An empty SQLite file with
no tables is still treated as a new installation.

If any database schema already exists while the key is missing,
`hoardarr-migrate` refuses to manufacture a replacement: restore the original
key or use an explicit recovery procedure. Production API and worker processes
never create the key. The normal appliance flow creates a single-use browser
pairing link:

```sh
sudo -u hoardarr /usr/lib/hoardarr/venv/bin/hoardarr setup
```

Open the printed link. Its URL fragment carries the server code into the Web UI,
which removes the fragment immediately and never renders the code in a field.
The user creates the first account in that paired browser. To create the owner
entirely at the server console instead, run:

```sh
sudo -u hoardarr /usr/lib/hoardarr/venv/bin/hoardarr setup --console
```

Use `--site-url https://hoardarr.example.test` when the automatically derived
address is not the address users browse. A plain server code is printed for
recovery, but the Web UI deliberately has no manual code field. The legacy
compatible command is:

```sh
sudo -u hoardarr /usr/lib/hoardarr/venv/bin/hoardarr-setup-token --ttl 900
```

The command stores only a hash and prints the claim secret once. Its lifetime is
60-3600 seconds, with 900 seconds as the default. Submit it to
`POST /api/v1/setup/claim` with the desired owner username and password. The
claim is atomic and single-use. `GET /api/v1/setup/status` reports only whether
setup is configured and whether a usable claim exists.

A successful claim or login sets an HttpOnly, SameSite=Strict session cookie
and returns a CSRF token in the JSON response. Production uses the Secure
`__Host-hoardarr_session` cookie. Every cookie-authenticated state-changing
request must send both an allowed `Origin` and `X-CSRF-Token`. Configure
`HOARDARR_ALLOWED_ORIGINS` with the public HTTPS origin when a reverse proxy
terminates TLS; do not rely on the backend's HTTP scheme in that topology.

App API keys are created once through the Settings page or
`/api/v1/auth/tokens`; the secret is shown once and stored only as a hash. They
begin with `hak_` and are structurally separate from the single-use `hsetup_`
pairing credential. Send one as `Authorization: Bearer hak_...`. API-key
requests do not use browser CSRF checks, but
authorization still enforces the token's `read`, `operate`, or `admin` scopes.
Possessing an administrator's token does not confer administrator authority
unless that token itself includes the `admin` scope.

Authentication rate limiting normally keys login/setup attempts to the TCP
peer. `HOARDARR_TRUSTED_PROXY_ADDRESSES` is a JSON list of exact proxy IP
addresses, not CIDR networks. Only when the immediate peer is in that list does
Hoardarr inspect `X-Forwarded-For`; it walks the last 20 entries right-to-left,
skips configured proxy hops, and uses the first non-proxy IP. Invalid or
overlong headers fall back to the TCP peer. A proxy must replace or safely
append this header. Use `[]` when clients connect directly, because trusting a
non-proxy lets that peer choose its rate-limit identity.

This limited use of `X-Forwarded-For` does not enable Uvicorn proxy-header
processing and does not change request scheme, Host, cookie, CSRF, or origin
decisions. The supplied default trusts only `127.0.0.1` and `::1` for a
same-host reverse proxy.

## API route groups

All normal responses are JSON. Errors use Problem Details-style documents and
include `X-Request-ID` correlation identifiers.

| Prefix | Purpose |
| --- | --- |
| `/health/live`, `/health/ready` | Public process liveness and migration readiness |
| `/metrics` | Public Prometheus metrics; keep loopback-only or restrict at the reverse proxy |
| `/api/v1/setup` | Public setup status and one-time owner claim |
| `/api/v1/auth` | Login/logout, current principal, and PAT lifecycle |
| `/api/v1/system` | Authenticated version, environment, and capability status |
| `/api/v1/onboarding` | First-run state, network-interface discovery, and non-mutating network plans |
| `/api/v1/operations` | Durable operation list/detail/events and cancellation |
| `/api/v1/hardware` | Queue read-only scans and read immutable snapshots |
| `/api/v1/integrations` | Resolve, create, list, inspect, and refresh Servarr connections |
| `/api/v1/wizards` | Create/resume/cancel storage sessions, generate immutable plans, and record plan-bound approval |
| `/api/v1/storage` | Inventory, telemetry, topology, pools, and active reservations |
| `/api/v1/connectivity` | Capability-gated SMB, NFS, iSCSI, and FCoE management |
| `/api/v1/networking` | Host networking plans, apply/confirm/rollback, and status |
| `/api/v1/accounts` | Typed media-service account creation and one-time credentials |
| `/api/docs`, `/api/openapi.json` | Interactive API documentation and schema |

Requests that enqueue work require an `Idempotency-Key` containing 8-128 safe
characters. Replaying an identical request returns the original operation;
reusing the key for different input is a conflict. Integration credentials are
write-only, encrypted at rest, redacted from operation records, and never
returned by read routes.

Cancellation is truthful rather than optimistic. A queued operation can be
cancelled before mutation starts. Once a storage or connectivity mutation is
running, the API rejects cancellation because interrupting a filesystem,
target, or share transition could leave the host in an uncertain state. A late
cancellation request is recorded as too late and the executor's actual final
result wins. After a worker restart, durable storage journals are reconciled
before stale-operation recovery.

Every request body is buffered and bounded before FastAPI validation. Both an
oversized `Content-Length` and a chunked/streamed body crossing
`HOARDARR_MAX_REQUEST_BODY_BYTES` receive a Problem Details `413` response. The
default is 1 MiB; accepted configuration is 16 KiB through 16 MiB. Keep the cap
small because buffering is intentional.

`HOARDARR_AUTHENTICATION_CONCURRENCY` limits simultaneous Argon2 login password
verifications to protect CPU and memory. The default is 2 and the accepted
range is 1-8; an attempt above the active limit receives `429
authentication_busy` with `Retry-After: 1`. It does not change PAT validation
or the separate per-account/per-client attempt limits.

## First-run network inventory contract

`GET /api/v1/onboarding` returns defaults in the same nested `server`,
`network`, `ntp`, and `discovery` shape accepted by the network-plan endpoint.
After discovery, the client fills `network.interface_ids` from the exact
interface identifiers it presents to the administrator.

`GET /api/v1/onboarding/network/interfaces` reads NIC facts without invoking a
shell or external executable. `speed_mbps` is the current positive value from
sysfs. `model` is an explicit sysfs model or a name from the local udev hardware
database. Either value is JSON `null` when the host cannot prove it;
`unknown_fields` lists those facts and `fact_sources` records the source of each
known value. Hoardarr never turns a PCI ID, interface name, or link duration
into a guessed model or speed.

## Servarr discovery matrix

Hoardarr sends the API credential only in the `X-Api-Key` header. A connection
retains a user-supplied reverse-proxy base path. Before each request, its host is
resolved again and every address must remain in both the administrator-approved
IP set and `HOARDARR_INTEGRATION_ALLOWED_NETWORKS`. Requests connect to a pinned
address, retain the original HTTP Host/TLS name, do not follow redirects, ignore
proxy environment variables, use bounded timeouts, and cap response bodies.

| Product | Fixed API prefix | Current level | Discovered capabilities |
| --- | --- | --- | --- |
| Sonarr | `/api/v3` | Supported | Root folders, remote-path mappings, download clients/schema |
| Radarr | `/api/v3` | Supported | Root folders, remote-path mappings, download clients/schema |
| Lidarr | `/api/v1` | Supported with explicit profile choices | Root folders, remote-path mappings, download clients/schema |
| Readarr | `/api/v1` | Legacy, opt-in | Root folders, remote-path mappings, download clients/schema |
| Whisparr v2 | `/api/v3` | Experimental, opt-in | Root folders, remote-path mappings, download clients/schema |
| Prowlarr | `/api/v1` | Discovery only | System identity/version; no storage roots or mappings |

The worker validates `/system/status` against the selected product and records
only the capabilities that the remote instance actually exposes. Current
integration support is read-only: the wizard may preview root-folder and
remote-path-mapping actions, but no adapter write is executed.

## Production layout and service order

The supplied packaging files use this contract:

| Path | Contents/ownership |
| --- | --- |
| `/usr/lib/hoardarr/venv` | Read-only application virtual environment and console scripts |
| `/usr/lib/hoardarr/frontend` | Production-built static web interface from the active release |
| `/usr/lib/hoardarr/scripts/detect-hardware.py` | Read-only detector installed with the release |
| `/usr/lib/hoardarr/packaging/hardware` | Provider and vendor-tool registries used by the detector |
| `/usr/share/doc/hoardarr/backend.md` | Installed copy of this service/deployment reference |
| `/etc/hoardarr/hoardarr.env` | Root-owned environment, recommended `root:hoardarr` mode `0640` |
| `/var/lib/hoardarr/hoardarr.db` | Database and WAL files in root-owned state directory mode `0700` |
| `/var/lib/hoardarr/secret.key` | Root-owned encryption key mode `0600` |

Install the units from `packaging/systemd/`, install the sample environment from
`packaging/config/hoardarr.env`, and create a locked system account with no
interactive shell. The units order both long-running services after
`hoardarr-migrate.service`:

```sh
systemctl enable hoardarr-migrate.service hoardarr-account-executor.service hoardarr-storage-executor.service hoardarr-api.service hoardarr-worker.service
systemctl start hoardarr-account-executor.service hoardarr-storage-executor.service hoardarr-api.service hoardarr-worker.service
```

On upgrade, use the versioned release installer to stage the wheel, locked
dependencies, detector data, and prebuilt frontend, then restart
`hoardarr-migrate.service` before either long-running service. Do not bypass a
failed migration. The API should remain bound to
loopback behind a hardened HTTPS reverse proxy; the included Uvicorn launcher
does not enable general forwarded-header processing. Its narrowly scoped
authentication client-identity handling is described above.

## Deliberate limitations

- Storage apply is available only for one individual disk or creation of a new
  mergerFS instance when the plan is complete. The executor requires the
  host-bound quarantine attestation, repeats stable identity and active-use
  checks, uses per-drive locks, and journals uncertain outcomes.
- API, migration, and worker services run as root on the managed host.
- Hardware snapshots include visible disk identity and any enclosure/slot link
  exported by sysfs. Raw SMART/NVMe lifetime health and complete stale-signature
  scans still require a future narrowly typed privileged reader. The detector
  reports those metrics as `Not reported` instead of substituting attachment uptime
  or a translated OS counter.
- Servarr support performs bounded discovery only. It does not create roots,
  mappings, clients, categories, or download paths.
- Plans expose exact capability blockers; incomplete plans have
  `apply_available=false` and cannot reach the executor.
- SQLite is intentionally single-host. Run one API process and one worker; do
  not place its database on NFS/SMB or share it between appliances.
- Add-ons, storage-controller failover, ZFS/RAID/SnapRAID execution,
  existing-mergerFS expansion, tier migration, and
  standalone destructive wipe flows remain future typed capabilities.

These restrictions keep the first backend useful for inventory, review, and UI
integration without putting attached storage at risk.
