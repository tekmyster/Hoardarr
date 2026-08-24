# Remote and control-plane backups

Hoardarr's first remote-backup scope protects the Hoardarr control plane. It does not upload media libraries or present S3 as a live POSIX filesystem.

## Supported targets

The normalized S3 contract supports Amazon S3, MinIO, Cloudflare R2, Wasabi, Backblaze B2's S3 API, and explicitly configured generic S3-compatible endpoints. Provider credentials are encrypted with Hoardarr's local secret store. List and history APIs return only a short credential fingerprint; they never return the access key or secret.

Custom endpoints are resolved before use. Private, loopback, link-local, and other non-public destinations require explicit private-network approval. Plain HTTP and disabled TLS verification are permitted only for an explicitly approved private endpoint. This allows a home MinIO server without weakening the default for Internet targets.

## Connection proof

Saving a target does not make it ready. `Test connection` writes a target-specific marker, verifies its length and Hoardarr SHA-256 metadata, then removes that exact marker. A backup cannot start and an automatic schedule cannot be enabled until this succeeds.

## Backup contents

A control-plane archive contains:

- a transactionally consistent SQLite backup;
- non-secret regular files below the configured Hoardarr configuration root;
- a manifest with version, file size, and SHA-256 evidence.

Files whose names indicate credentials, passwords, private keys, secrets, or tokens are excluded. Symlinks and oversized configuration files are also excluded. API credentials and the Hoardarr secret-store key are not exported. The archive is not described as encrypted: operators must use provider-side encryption or an encrypted destination when encryption at rest is required. A future optional encrypted-secrets export remains a separate roadmap item.

## Upload, recovery, and verification

Small archives use one bounded upload payload. Larger archives use S3 multipart upload with one part in memory at a time. The upload ID and completed part list are durable, so a recovered worker resumes from provider-reported completed parts instead of assuming local checkpoint state is authoritative. The optional MiB/s limit paces actual payload bytes.

The AWS SDK standard retry mode performs three bounded attempts for transient provider failures. Worker interruption returns a control-plane backup to the durable queue; completed multipart state is retained. A stale target fingerprint fails closed if configuration or credentials changed after queueing.

Hoardarr does not treat an ETag as a content checksum. It verifies object length and its independently recorded SHA-256 metadata, downloads the finished object, and recomputes the entire SHA-256 digest.

Automatic schedules use a bounded 1–720 hour interval. The worker checks due targets once per minute, creates at most one active job per target, and uses a time-bucketed idempotency key to prevent duplicate runs.

## Restore boundary

`Validate restore` downloads a successful remote archive into an isolated temporary directory, rejects unsafe archive paths and non-regular entries, checks the archive and manifest digests, and runs SQLite `PRAGMA integrity_check`. It never replaces the running database or configuration. Applying a validated archive to a fresh appliance, optional encrypted secret export, and stable-disk reconciliation remain distinct work; validation must not be presented as a completed restore.

## User interface and activity

Settings → Remote backups provides honest empty/loading/failure states, target creation, connection proof, automatic 24-hour scheduling, durable backup launch, history, and restore validation. Active work remains visible in Activity and survives browser closure. The browser never owns backup progress or history.

## Validation

- `backend/tests/test_backups.py` covers endpoint policy, secret exclusion, rate pacing, connection proof, multipart checkpoints, full SHA-256 verification, corruption rejection, scheduler idempotency, and failed-run reconciliation.
- backup-focused API tests cover authentication, scopes, redaction, idempotency, readiness gates, schedule persistence, and durable run creation.
- `frontend/src/components/RemoteBackupsPanel.test.tsx` covers empty states, secret removal from the DOM, readiness gating, scheduling, and validation actions.
- the Playwright production-shell scenario exercises the visible create → prove connection → enable backup workflow.
- `tests/integration/minio_control_plane_backup.py` exercises the production boto3/worker path against a live disposable MinIO server in Ubuntu CI and emits sanitized evidence.
