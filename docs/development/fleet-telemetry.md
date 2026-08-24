# Fleet telemetry architecture

Hoardarr fleet telemetry has two deliberately separate lifetimes.

The appliance worker collects eligible product and hardware observations, writes them to a
bounded SQLite outbound queue, and delivers them independently of the API and browser. The
`hoardarr-fleet-ingestion` process is the hoardarr.com receiver. It uses a separate database and
is not started on a normal appliance.

## Privacy levels

Level 0 is the required anonymous installation heartbeat. Level 1 hardware/product telemetry is
enabled by default and can be disabled. Level 2 enhanced diagnostics and Level 3 content
diagnostics require progressively stronger explicit consent. Passwords, API keys, session tokens,
CHAP secrets, SNMP communities, signing keys, encryption keys, and file contents are rejected by
the client allowlist and sanitization boundary.

The installation identifier is a stored random UUID. Drive identifiers are deterministic,
versioned pseudonyms based on WWN/NAA, NGUID, EUI-64, or a normalized serial/vendor/model fallback.
They are not cryptographic anonymity and must not be published at installation scope.

## Transport and retry

Registration issues a unique random credential once. The server stores it using record-bound
authenticated encryption; the client does the same in its appliance secret store. Batches use
canonical JSON, SHA-256 record and batch digests, UUID batch identifiers, monotonic per-installation
sequence numbers, and HMAC-SHA-256 authentication. The server rejects malformed, oversized,
unsupported, unauthenticated, and replayed batches.

Only HTTPS endpoints pass appliance configuration validation. TLS verification cannot be disabled.
Transient failures retain records with bounded exponential backoff and jitter. Permanent schema or
body errors enter a bounded dead-letter state. Records are removed only after the server explicitly
acknowledges their IDs.

## Central service

Central deployment assets live under `packaging/hoardarr-com/`; they are deliberately excluded
from the home-appliance release bundle. The receiver has its own runtime, account, database and
deployment lifecycle, so installing an appliance can never accidentally start the hoardarr.com
ingestion service.

Run the receiver behind the hoardarr.com TLS reverse proxy. Required environment variables are:

- `HOARDARR_FLEET_DATABASE_URL` — central SQLAlchemy database URL; this is distinct from appliance
  SQLite.
- `HOARDARR_FLEET_SECRET_KEY_FILE` — root path for the service-owned 0600 AES-GCM key.
- `HOARDARR_FLEET_ADMIN_TOKEN` — at least 32 characters; staging bootstrap credential only until
  the website account boundary is connected.
- `HOARDARR_FLEET_BIND_HOST` and `HOARDARR_FLEET_BIND_PORT` — default to `127.0.0.1:8091`.

The service exposes `/healthz`, versioned registration/batch ingestion, and a bounded aggregate
admin summary. Raw installation records and public aggregate statistics remain separate. Source IP
is not added to telemetry analytics.

The normalized receiver schema contains installations, heartbeats, version observations, hardware
snapshots, drives, drive observations, lifecycle events, explicit controller, storage-layout,
application, capacity and feature-usage observations, geographic settings, batches, and
deduplicated records. SQLite is supported only for isolated tests and staging smoke checks; the
production hoardarr.com deployment must use its managed server database and normal backup policy.

Schema version 1 is the initial supported wire format. Application version and telemetry schema are
stored independently, so an older supported Hoardarr build is not rejected merely for being old.
The receiver rejects unknown schema versions with a permanent Problem Details response rather than
guessing field meaning. Before version 1 is deprecated, a new receiver parser/normalizer and
compatibility fixtures must ship first, followed by a published support window; removal is a later
server migration, never an implicit direct-to-latest client requirement.

Central retention is type-specific and bounded per transaction. Defaults keep raw accepted records
for 90 days, explicitly opted-in Level 2/3 raw records for 30 days, heartbeats/version observations
for 400 days, hardware/category observations for 730 days, and drive observations/lifecycle events
for 3,650 days. Every value has server-side minimum and maximum bounds. Cleanup runs at most hourly
during ingestion, deletes no more than the configured batch size per table, removes empty old batch
envelopes after their records, and publishes the last cleanup time through the authenticated admin
summary. Lifecycle retention is intentionally longer; access-log/source-IP retention belongs to the
TLS proxy and must remain separately bounded.

## Operational behavior

The systemd unit runs as `hoardarr-fleet`, listens only on loopback by default, uses a dedicated
state directory, and has no storage-management privileges. Invalid client data never becomes a
local appliance metric. An administrator controlling an appliance can fabricate its telemetry;
authentication provides transit integrity, replay resistance, and installation attribution—not
trusted hardware attestation.

The central database has its own Alembic history under
`hoardarr/fleet/migrations`; it is intentionally independent from the appliance database. The
systemd unit executes `hoardarr-fleet-ingestion migrate` before `serve`. The receiver refuses to
start against an empty or stale schema, so a deployment cannot silently create a partial schema
from whichever model definitions happen to be installed. Migration execution must remain a
single deployment step; concurrent service instances may start only after it completes.

Production central deployments use PostgreSQL through the `fleet-central` package extra. The
example environment and fail-closed installer live under `packaging/hoardarr-com/`. The installer
rejects SQLite URLs and unchanged credential placeholders, installs into an isolated central venv,
and runs the versioned migration before systemd starts the receiver. Appliance releases do not
install this extra or service. CI applies the central schema to a real disposable PostgreSQL server,
starts the receiver, registers an installation and queries its authenticated aggregate summary.
