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
snapshots, drives, drive observations, lifecycle events, category observations for controllers,
storage layouts, applications, capacity and feature usage, geographic settings, batches, and
deduplicated records. SQLite is supported only for isolated tests and staging smoke checks; the
production hoardarr.com deployment must use its managed server database and normal backup policy.

## Operational behavior

The systemd unit runs as `hoardarr-fleet`, listens only on loopback by default, uses a dedicated
state directory, and has no storage-management privileges. Invalid client data never becomes a
local appliance metric. An administrator controlling an appliance can fabricate its telemetry;
authentication provides transit integrity, replay resistance, and installation attribution—not
trusted hardware attestation.
