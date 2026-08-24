# Remote and control-plane backups

Hoardarr's first remote-backup scope protects the Hoardarr control plane. It does not upload media libraries or present S3 as a live POSIX filesystem.

## Supported targets

The normalized S3 contract supports Amazon S3, MinIO, Cloudflare R2, Wasabi, Backblaze B2's S3 API, and explicitly configured generic S3-compatible endpoints. Provider credentials are encrypted with Hoardarr's local secret store. List and history APIs return only a short credential fingerprint; they never return the access key or secret.

Custom endpoints are resolved before use. Private, loopback, link-local, and other non-public destinations require explicit private-network approval. Plain HTTP and disabled TLS verification are permitted only for an explicitly approved private endpoint. This allows a home MinIO server without weakening the default for Internet targets.

## Connection proof

Saving a target does not make it ready. `Test connection` writes a target-specific marker, verifies its length and Hoardarr SHA-256 metadata, then removes that exact marker. A backup cannot start and an automatic schedule cannot be enabled until this succeeds.

Credentials can be replaced without returning either the old or new secret. Rotation is refused while the target has active work, invalidates the previous connection proof, and turns off automatic scheduling until a new marker test succeeds.

## Backup contents

A control-plane archive contains:

- a transactionally consistent SQLite backup;
- non-secret regular files below the configured Hoardarr configuration root;
- a manifest with version, file size, and SHA-256 evidence.

Files whose names indicate credentials, passwords, private keys, secrets, or tokens are excluded. Symlinks and oversized configuration files are also excluded. Secret-valued keys are removed from `hoardarr.env` while non-secret listener and appliance settings remain restorable.

The default SQLite copy is also made safe for a different installation key. User password verifiers, browser sessions, one-time setup claims, and API tokens are removed. ARR credentials, CHAP secrets, backup credentials, and webhook signing secrets are cleared; their non-secret endpoint/configuration rows remain disabled with `credentials_required` state so the owner can review and re-enter them. The manifest records the credential mode and row counts. This prevents a structurally valid fresh restore from containing permanently unreadable ciphertext, transferable authentication material, or live sessions.

The default Hoardarr secret-store key is not exported. The archive is not described as encrypted: operators must use provider-side encryption or an encrypted destination when encryption at rest is required.

An owner who needs an offline full-credential recovery artifact can create one only from the appliance console:

```console
printf '%s\n' "$HOARDARR_RECOVERY_PASSPHRASE" | sudo hoardarr export-control-plane \
  --output /secure-offline-media/hoardarr-control-plane.tar.gz \
  --encrypt-secrets
```

The passphrase must contain 16 to 1024 characters. It is read only from standard input, never accepted as a command-line argument, and never recorded in an API request, browser state, durable operation, or Activity event. Hoardarr protects the installation key with scrypt and AES-256-GCM. Authentication sessions, API tokens, setup claims, owners, and password verifiers are still removed. The encrypted artifact is sensitive recovery material and belongs on access-controlled offline storage; losing either it or its passphrase makes the encrypted credentials unrecoverable.

## Upload, recovery, and verification

Small archives use one bounded upload payload. Larger archives use S3 multipart upload with one part in memory at a time. The upload ID and completed part list are durable, so a recovered worker resumes from provider-reported completed parts instead of assuming local checkpoint state is authoritative. The optional MiB/s limit paces actual payload bytes.

The AWS SDK standard retry mode performs three bounded attempts for transient provider failures. A retryable failure then returns the same durable operation to its queue for at most three delayed attempts (5, 30, then 120 seconds), retaining its upload ID and provider-confirmed multipart parts. Worker interruption uses the same durable run and completed multipart state. A stale target fingerprint fails closed if configuration or credentials changed after queueing.

Hoardarr does not treat an ETag as a content checksum. It verifies object length and its independently recorded SHA-256 metadata, downloads the finished object, and recomputes the entire SHA-256 digest.

Automatic schedules use a bounded 1–720 hour interval. The worker checks due targets once per minute, creates at most one active job per target, and uses a time-bucketed idempotency key to prevent duplicate runs.

## Restore boundary

`Validate restore` downloads a successful remote archive into an isolated temporary directory, rejects unsafe archive paths and non-regular entries, enforces entry-count and expanded-size limits, checks the archive and manifest digests, and runs SQLite `PRAGMA integrity_check`. It never replaces the running database or configuration.

An administrator can apply a downloaded, credential-redacted archive only to an offline fresh appliance:

```console
sudo systemctl stop hoardarr-api hoardarr-worker hoardarr-account-executor \
  hoardarr-storage-executor hoardarr-storage-status
sudo hoardarr restore-control-plane \
  --archive /secure/path/hoardarr-control-plane.tar.gz \
  --sha256 <verified-64-character-digest> \
  --yes
sudo hoardarr-migrate
sudo systemctl start hoardarr-account-executor hoardarr-storage-executor \
  hoardarr-storage-status hoardarr-worker hoardarr-api
```

For an encrypted full-credential export, add `--passphrase-stdin` and pipe the passphrase to the same restore command. The command requires root, refuses to run while a Hoardarr service is active, requires an independent expected SHA-256 value, refuses an appliance that already has an owner, rejects archives whose credential mode is neither proven redaction nor the authenticated encrypted-key envelope, and checks bounded expanded size plus a 64 MiB staging safety margin before extraction. It atomically replaces the empty SQLite database and, for encrypted exports, the installation key; both are retained under the reported rollback path. A missing or incorrect passphrase fails before destination mutation. After migration, issue a new one-time setup link or create a new console owner before restarting normal access. Redacted archives require credential re-entry. Encrypted exports preserve credential ciphertext for review under the restored key. A hardware scan then reconciles the physical disk registry by stable identity rather than kernel path. Restore validation must not be presented as an applied restore.

## User interface and activity

Settings → Remote backups provides honest empty/loading/failure states, target creation, connection proof, automatic 24-hour scheduling, durable backup launch, history, and restore validation. Active work remains visible in Activity and survives browser closure. The browser never owns backup progress or history.

## Validation

- `backend/tests/test_backups.py` covers endpoint policy, filename and environment-key exclusion, database credential/session redaction, encrypted-key export, missing/wrong passphrases, credential recovery, re-entry states, rate pacing, connection proof, multipart checkpoints, full SHA-256 verification, corruption rejection, scheduler idempotency, transient-outage delayed resume, and failed-run reconciliation.
- `backend/tests/test_cli_backups.py` executes the console-only encrypted export and fresh restore using standard-input passphrases and proves the restored credential decrypts while the owner account does not transfer.
- backup-focused API tests cover authentication, scopes, redaction, credential rotation, idempotency, readiness gates, schedule persistence, and durable run creation.
- `frontend/src/components/RemoteBackupsPanel.test.tsx` covers empty states, secret removal from the DOM after creation and rotation, readiness gating, scheduling, and validation actions.
- the Playwright production-shell scenario exercises the visible create → prove connection → enable backup workflow.
- `tests/integration/minio_control_plane_backup.py` exercises the production boto3/worker path against a live disposable MinIO server in Ubuntu CI, downloads the verified object, applies it to a separate fresh SQLite/configuration root, proves authentication material is removed while non-secret target configuration survives in credential-reentry state, and emits sanitized evidence.
