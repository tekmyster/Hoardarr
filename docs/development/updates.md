# Updates and migrations

An update from any supported Hoardarr release to the newest release is one
operator action. The updater must not require the user to install intermediate
releases manually. Internally, the newest release may execute multiple retained
schema migrations or reboot into a newly staged system image.

## Current implementation boundary

The package-mode release bundle implements the first direct-to-latest building
block today. Each bundle is a complete, versioned application release with a
locked Python wheelhouse and prebuilt frontend. The installer stages it beside
the active release, switches the `current` link only after the new environment
imports successfully, and runs every retained Alembic revision through `head`
before starting the API or worker. It does not require the immediately previous
application bundle.

The package-mode updater now retrieves bounded metadata and artifacts only from
approved HTTPS GitHub origins, verifies an independently provisioned Ed25519
trust root and artifact digest, checks compatibility, free space, add-ons and
active storage operations, backs up state, stages a complete release, executes
retained migrations, switches the active release atomically, runs health checks
and rolls back on failure. Each transition is represented by a durable
operation. The Settings UI performs checks and displays the same real operation
state; it does not simulate progress.

Supported starting schemas are those covered by the retained migration fixtures.
RAUC system-image slot management and post-reboot confirmation remain outside
the package-mode implementation. Production deployments must provision the
public verification key; no production private signing key is part of Hoardarr.

## Desired-state releases

Every release carries a complete desired-state manifest rather than a patch
that assumes the immediately preceding version. It identifies:

- appliance and application versions;
- supported Ubuntu base and architectures;
- required system packages and minimum compatible versions;
- configuration and database schema versions;
- supported hardware-provider and add-on API levels;
- cryptographic digests, size, release channel, and rollback compatibility.

The host bootstrap reconciles its selected Ubuntu packages with current APT
candidates: missing and older packages are planned and simulated, held packages
block with an explicit explanation, and downgrades are rejected. This covers
host dependencies; it is not a substitute for application migrations.

## Direct-to-latest migration

The latest release retains a migration graph from every still-supported schema
version. Given the installed state, the updater computes the complete ordered
path locally, proves that no step is missing, estimates temporary space, and
backs up application data and configuration before changing anything. Each
migration is idempotent, declares its input/output schema, has a postcondition,
and can resume safely after interruption.

When a historical transition truly needs old code, the required migration
helper is embedded and signed inside the newest release. The user still starts
one update; Hoardarr does not ask them to locate and install a chain of old
releases.

## Delivery and trust

The web UI checks a small signed channel manifest, displays release notes and
preflight results, and submits an update job to the privileged backend. It does
not run a shell command or install directly from a Git branch. GitHub Releases
may host immutable artifacts, but Hoardarr verifies an embedded project signing
key, artifact digest, target architecture, release metadata, and anti-rollback
policy before staging them. Offline USB/file updates use the same verification
path.

System-image releases are staged into an inactive RAUC slot where the target
hardware permits it. The bootloader marks the new slot tentative, health checks
confirm the API, database, storage discovery, and required mounts, and only then
is it committed. A failed boot or health check returns to the previous slot.
Application data and storage pools remain outside the replaceable system slot.

Package-mode development updates use the same plan, signature, dependency, and
migration rules even when no system-image slot is involved.

## Safety and availability

Before staging, the updater checks free space, package/database health, pending
storage jobs, pool degradation, active wipes or rebuilds, release compatibility,
and whether rollback data can be written. It never imports, exports, upgrades,
or rewrites a storage pool merely because the application is updating.

The plan distinguishes:

- read-only checks;
- downloads and staging;
- service interruption;
- reboot requirements;
- configuration/database migrations;
- actions that prevent rollback.

Secrets are not copied into reports. Logs use stable operation and migration
IDs so support can identify the failed step without exposing credentials.

## Add-ons

Add-ons declare compatible Hoardarr API and schema ranges. The updater checks
all installed add-ons before staging. An incompatible optional add-on can be
disabled with explicit approval; it cannot silently block the appliance from
booting or execute an unreviewed migration as root.

## Release gates

Every release is tested from clean installation and from fixtures representing
each oldest supported application/configuration schema directly to the new
release. Required gates include interrupted download, interrupted migration,
full disk, held dependency, offline update, bad signature, failed health check,
rollback, repeated update, and post-reboot validation. A release cannot claim
direct-to-latest support for a starting schema until that path passes.
