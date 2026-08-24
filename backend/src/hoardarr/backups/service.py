from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import os
import re
import shutil
import socket
import sqlite3
import tarfile
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlsplit

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.orm import Session, sessionmaker

from hoardarr import __version__
from hoardarr.core.config import Settings
from hoardarr.core.secrets import SecretBox, SecretStoreError
from hoardarr.db.engine import sqlite_database_path
from hoardarr.db.models import Operation, RemoteBackupRun, RemoteBackupTarget, utc_now
from hoardarr.operations.service import append_event

PROVIDERS = frozenset({"aws_s3", "minio", "cloudflare_r2", "wasabi", "backblaze_b2", "generic_s3"})
TARGET_RECORD_TYPE = "remote_backup_target"
MULTIPART_PART_BYTES = 8 * 1024 * 1024
MAX_CONFIG_FILE_BYTES = 1024 * 1024
MAX_RESTORE_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
EXCLUDED_NAME_FRAGMENTS = ("credential", "password", "private", "secret", "token")


class UploadRateLimiter:
    """Pace uploaded bytes while retaining at most one multipart payload in memory."""

    def __init__(
        self,
        limit_mib: int | None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.bytes_per_second = limit_mib * 1024 * 1024 if limit_mib is not None else None
        self.clock = clock
        self.sleeper = sleeper
        self.started_at = clock()
        self.transferred = 0

    def account(self, byte_count: int) -> None:
        if self.bytes_per_second is None or byte_count <= 0:
            return
        self.transferred += byte_count
        remaining = self.started_at + self.transferred / self.bytes_per_second - self.clock()
        if remaining > 0:
            self.sleeper(remaining)


class BackupError(RuntimeError):
    def __init__(self, code: str, safe_message: str, *, retryable: bool = False) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


class S3Client(Protocol):
    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def delete_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def create_multipart_upload(self, **kwargs: Any) -> dict[str, Any]: ...
    def upload_part(self, **kwargs: Any) -> dict[str, Any]: ...
    def list_parts(self, **kwargs: Any) -> dict[str, Any]: ...
    def complete_multipart_upload(self, **kwargs: Any) -> dict[str, Any]: ...
    def abort_multipart_upload(self, **kwargs: Any) -> dict[str, Any]: ...


def _is_forbidden_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not address.is_global


def validate_endpoint(
    provider: str,
    endpoint_url: str | None,
    *,
    allow_private_network: bool,
    allow_insecure_http: bool,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
) -> str | None:
    if provider not in PROVIDERS:
        raise BackupError("unsupported_backup_provider", "This S3 provider is not supported.")
    if endpoint_url is None:
        if provider != "aws_s3":
            raise BackupError("backup_endpoint_required", "This provider requires an endpoint URL.")
        return None
    value = endpoint_url.strip().rstrip("/")
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise BackupError(
            "backup_endpoint_invalid",
            "Use an HTTP or HTTPS S3 endpoint origin without credentials, a path, "
            "query, or fragment.",
        )
    if parts.scheme != "https" and not allow_insecure_http:
        raise BackupError(
            "backup_endpoint_requires_https",
            "HTTP endpoints require the explicit local-network HTTP option.",
        )
    try:
        addresses = {
            str(item[4][0])
            for item in resolver(
                parts.hostname,
                parts.port or (443 if parts.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        }
    except OSError as exc:
        raise BackupError(
            "backup_endpoint_unresolved",
            "The S3 endpoint hostname could not be resolved.",
            retryable=True,
        ) from exc
    if not addresses:
        raise BackupError(
            "backup_endpoint_unresolved", "The S3 endpoint returned no network address."
        )
    if not allow_private_network and any(_is_forbidden_address(item) for item in addresses):
        raise BackupError(
            "backup_private_endpoint_blocked",
            "Private, loopback, link-local, and reserved S3 endpoints require explicit "
            "local-network approval.",
        )
    return value


def normalize_prefix(value: str) -> str:
    cleaned = value.strip().strip("/")
    if not cleaned:
        return "hoardarr"
    path = PurePosixPath(cleaned)
    if path.is_absolute() or ".." in path.parts or any(ord(char) < 32 for char in cleaned):
        raise BackupError(
            "backup_prefix_invalid", "The backup prefix must be a relative S3 key prefix."
        )
    return path.as_posix()


def target_document(target: RemoteBackupTarget) -> dict[str, Any]:
    return {
        "id": target.id,
        "name": target.name,
        "provider": target.provider,
        "endpoint_url": target.endpoint_url,
        "region": target.region,
        "bucket": target.bucket,
        "prefix": target.prefix,
        "force_path_style": target.force_path_style,
        "verify_tls": target.verify_tls,
        "allow_private_network": target.allow_private_network,
        "allow_insecure_http": target.allow_insecure_http,
        "bandwidth_limit_mib": target.bandwidth_limit_mib,
        "schedule": target.schedule_json,
        "credential_fingerprint": target.credential_fingerprint,
        "status": target.status,
        "last_tested_at": target.last_tested_at,
        "last_success_at": target.last_success_at,
        "error": target.last_error_json,
        "enabled": target.enabled,
        "created_at": target.created_at,
        "updated_at": target.updated_at,
    }


def target_fingerprint(target: RemoteBackupTarget) -> str:
    document = {
        "id": target.id,
        "provider": target.provider,
        "endpoint_url": target.endpoint_url,
        "region": target.region,
        "bucket": target.bucket,
        "prefix": target.prefix,
        "force_path_style": target.force_path_style,
        "verify_tls": target.verify_tls,
        "allow_private_network": target.allow_private_network,
        "allow_insecure_http": target.allow_insecure_http,
        "credential_ciphertext_sha256": hashlib.sha256(bytes(target.secret_ciphertext)).hexdigest(),
        "enabled": target.enabled,
    }
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def run_document(run: RemoteBackupRun, operation: Operation | None = None) -> dict[str, Any]:
    return {
        "id": run.id,
        "target_id": run.target_id,
        "backup_kind": run.backup_kind,
        "object_key": run.object_key,
        "artifact_sha256": run.artifact_sha256,
        "artifact_size_bytes": run.artifact_size_bytes,
        "status": operation.status if operation is not None else run.status,
        "phase": run.phase,
        "report": run.report_json,
        "error": operation.error_json if operation is not None else None,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def encrypt_credentials(
    secret_box: SecretBox,
    target_id: str,
    *,
    access_key_id: str,
    secret_access_key: str,
    session_token: str | None,
) -> tuple[bytes, str]:
    document = json.dumps(
        {
            "access_key_id": access_key_id,
            "secret_access_key": secret_access_key,
            "session_token": session_token,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        secret_box.encrypt(TARGET_RECORD_TYPE, target_id, document),
        secret_box.fingerprint("backup_access_key", access_key_id)[:16],
    )


def decrypt_credentials(secret_box: SecretBox, target: RemoteBackupTarget) -> dict[str, str | None]:
    try:
        raw = secret_box.decrypt(TARGET_RECORD_TYPE, target.id, target.secret_ciphertext)
        document = json.loads(raw)
    except (SecretStoreError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BackupError(
            "backup_credentials_unavailable",
            "The saved backup credential could not be loaded. Re-enter it in Settings.",
        ) from exc
    if not isinstance(document, dict) or not all(
        isinstance(document.get(key), str) and document[key]
        for key in ("access_key_id", "secret_access_key")
    ):
        raise BackupError(
            "backup_credentials_unavailable", "The saved backup credential is invalid."
        )
    return {
        "access_key_id": str(document["access_key_id"]),
        "secret_access_key": str(document["secret_access_key"]),
        "session_token": (
            str(document["session_token"])
            if isinstance(document.get("session_token"), str) and document["session_token"]
            else None
        ),
    }


def create_s3_client(target: RemoteBackupTarget, secret_box: SecretBox) -> S3Client:
    endpoint = validate_endpoint(
        target.provider,
        target.endpoint_url,
        allow_private_network=target.allow_private_network,
        allow_insecure_http=target.allow_insecure_http,
    )
    credentials = decrypt_credentials(secret_box, target)
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=target.region,
        aws_access_key_id=credentials["access_key_id"],
        aws_secret_access_key=credentials["secret_access_key"],
        aws_session_token=credentials["session_token"],
        verify=target.verify_tls,
        config=Config(
            signature_version="s3v4",
            connect_timeout=10,
            read_timeout=60,
            max_pool_connections=2,
            retries={"max_attempts": 3, "mode": "standard"},
            s3={"addressing_style": "path" if target.force_path_style else "auto"},
        ),
    )


def _safe_s3_error(exc: Exception, action: str) -> BackupError:
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code") or "provider_error")[:64]
        if code in {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "ExpiredToken"}:
            return BackupError(
                "backup_authentication_failed", "The S3 provider rejected the backup credential."
            )
        if code in {"NoSuchBucket", "NotFound", "404"}:
            return BackupError("backup_bucket_not_found", "The configured S3 bucket was not found.")
    return BackupError(
        f"backup_{action}_failed",
        f"The S3 {action.replace('_', ' ')} could not be completed.",
        retryable=isinstance(exc, (BotoCoreError, OSError)),
    )


def test_target_connection(
    target: RemoteBackupTarget,
    secret_box: SecretBox,
    *,
    client_factory: Callable[[RemoteBackupTarget, SecretBox], S3Client] | None = None,
) -> dict[str, Any]:
    client = (client_factory or create_s3_client)(target, secret_box)
    marker = f"{target.prefix}/.hoardarr-connection-test/{target.id}"
    payload = b"hoardarr connection test\n"
    digest = hashlib.sha256(payload).hexdigest()
    try:
        client.put_object(
            Bucket=target.bucket,
            Key=marker,
            Body=payload,
            Metadata={"hoardarr-sha256": digest},
        )
        head = client.head_object(Bucket=target.bucket, Key=marker)
        if (
            int(head.get("ContentLength", -1)) != len(payload)
            or head.get("Metadata", {}).get("hoardarr-sha256") != digest
        ):
            raise BackupError(
                "backup_connection_verification_failed", "The S3 test object did not verify."
            )
    except BackupError:
        raise
    except Exception as exc:
        raise _safe_s3_error(exc, "connection_test") from exc
    finally:
        with suppress(Exception):
            client.delete_object(Bucket=target.bucket, Key=marker)
    return {"bucket": target.bucket, "prefix": target.prefix, "write_read_delete": "verified"}


def _include_configuration(configuration_root: Path) -> tuple[list[Path], list[str]]:
    included: list[Path] = []
    excluded: list[str] = []
    if not configuration_root.is_dir():
        return included, excluded
    for path in sorted(configuration_root.rglob("*")):
        relative = path.relative_to(configuration_root).as_posix()
        if path.is_symlink() or not path.is_file():
            if path.is_symlink():
                excluded.append(relative)
            continue
        lowered = path.name.casefold()
        if any(fragment in lowered for fragment in EXCLUDED_NAME_FRAGMENTS):
            excluded.append(relative)
            continue
        try:
            size = path.stat().st_size
        except OSError:
            excluded.append(relative)
            continue
        if size > MAX_CONFIG_FILE_BYTES:
            excluded.append(relative)
            continue
        included.append(path)
    return included, excluded


def _database_backup(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        result = destination_connection.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            raise BackupError(
                "backup_database_invalid", "The database backup failed its integrity check."
            )
    finally:
        destination_connection.close()
        source_connection.close()


def _sanitize_database_backup(destination: Path) -> dict[str, int]:
    """Remove live authentication state and make encrypted integrations re-keyable.

    A default control-plane archive deliberately excludes the installation SecretBox
    key.  Copying ciphertext without that key would leave a fresh restore looking
    configured while every credential is permanently unreadable.  Preserve the
    non-secret endpoint/configuration rows, but disable them and require the owner to
    enter new credentials after recovery.
    """

    connection = sqlite3.connect(destination)
    counts: dict[str, int] = {}
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

        def execute(name: str, statement: str) -> None:
            if name not in tables:
                return
            cursor = connection.execute(statement)
            counts[name] = max(0, int(cursor.rowcount))

        execute("auth_sessions", "DELETE FROM auth_sessions")
        execute("api_tokens", "DELETE FROM api_tokens")
        execute("setup_claims", "DELETE FROM setup_claims")
        execute("users", "DELETE FROM users")
        execute(
            "integration_connections",
            "UPDATE integration_connections "
            "SET api_key_ciphertext = X'', status = 'credentials_required'",
        )
        execute(
            "connectivity_services",
            "UPDATE connectivity_services "
            "SET secret_ciphertext = NULL, status = CASE "
            "WHEN secret_ciphertext IS NULL THEN status ELSE 'credentials_required' END",
        )
        execute(
            "remote_backup_targets",
            "UPDATE remote_backup_targets "
            "SET secret_ciphertext = X'', enabled = 0, status = 'credentials_required', "
            "schedule_json = '{\"enabled\":false}'",
        )
        execute(
            "webhook_endpoints",
            "UPDATE webhook_endpoints "
            "SET secret_ciphertext = X'', enabled = 0, status = 'credentials_required'",
        )
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise BackupError(
                "backup_database_invalid",
                "The credential-redacted database failed its integrity check.",
            )
    finally:
        connection.close()
    return counts


def _sanitized_environment_payload(path: Path) -> tuple[bytes, list[str]]:
    """Return a restorable env file without secret-valued settings."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise BackupError(
            "backup_configuration_invalid",
            "The Hoardarr environment file is not valid UTF-8.",
        ) from exc
    kept: list[str] = []
    removed: list[str] = []
    for line in lines:
        candidate = line.strip()
        if not candidate or candidate.startswith("#") or "=" not in candidate:
            kept.append(line)
            continue
        key = candidate.split("=", 1)[0].removeprefix("export ").strip()
        lowered = key.casefold()
        if any(fragment in lowered for fragment in EXCLUDED_NAME_FRAGMENTS):
            removed.append(key)
            continue
        kept.append(line)
    return "".join(kept).encode(), removed


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_control_plane_artifact(
    settings: Settings, operation_id: str
) -> tuple[Path, dict[str, Any]]:
    database = sqlite_database_path(settings.database_url)
    if database is None or not database.is_file():
        raise BackupError("backup_database_unavailable", "The Hoardarr database is unavailable.")
    root = settings.backup_artifact_root.resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    operation_root = (root / operation_id).resolve(strict=False)
    if operation_root.parent != root:
        raise BackupError("backup_path_invalid", "The backup staging path is invalid.")
    operation_root.mkdir(mode=0o700, exist_ok=True)
    artifact = operation_root / "hoardarr-control-plane.tar.gz"
    database_copy = operation_root / "hoardarr.db"
    _database_backup(database, database_copy)
    database_redaction = _sanitize_database_backup(database_copy)
    included, excluded = _include_configuration(settings.configuration_root)
    redacted_configuration: dict[str, list[str]] = {}
    configuration_payloads: dict[str, bytes] = {}
    for path in included:
        relative = path.relative_to(settings.configuration_root).as_posix()
        if relative == "hoardarr.env":
            payload, removed = _sanitized_environment_payload(path)
            configuration_payloads[relative] = payload
            redacted_configuration[relative] = removed
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    manifest = {
        "schema_version": 1,
        "kind": "hoardarr_control_plane",
        "hoardarr_version": __version__,
        "created_at": created_at,
        "database": {
            "path": "database/hoardarr.db",
            "sha256": _hash_file(database_copy),
            "credential_mode": "redacted_reentry_required",
            "redacted_rows": database_redaction,
        },
        "configuration": {
            "included": [
                path.relative_to(settings.configuration_root).as_posix() for path in included
            ],
            "excluded": excluded,
            "secrets_included": False,
            "redacted_keys": redacted_configuration,
        },
    }
    manifest_path = operation_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(manifest_path, 0o600)
    with tarfile.open(artifact, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        archive.add(manifest_path, arcname="manifest.json", recursive=False)
        archive.add(database_copy, arcname="database/hoardarr.db", recursive=False)
        for path in included:
            relative = path.relative_to(settings.configuration_root).as_posix()
            if relative in configuration_payloads:
                payload = configuration_payloads[relative]
                info = tarfile.TarInfo(name=f"configuration/{relative}")
                info.size = len(payload)
                info.mode = 0o600
                info.mtime = 0
                archive.addfile(info, io.BytesIO(payload))
            else:
                archive.add(path, arcname=f"configuration/{relative}", recursive=False)
    os.chmod(artifact, 0o600)
    return artifact, {
        **manifest,
        "artifact_sha256": _hash_file(artifact),
        "artifact_size_bytes": artifact.stat().st_size,
    }


def _read_parts(
    client: S3Client, target: RemoteBackupTarget, run: RemoteBackupRun
) -> list[dict[str, Any]]:
    if not run.upload_id or not run.object_key:
        return []
    response = client.list_parts(
        Bucket=target.bucket,
        Key=run.object_key,
        UploadId=run.upload_id,
        MaxParts=10_000,
    )
    return [
        {"PartNumber": int(item["PartNumber"]), "ETag": str(item["ETag"])}
        for item in response.get("Parts", [])
    ]


def _download_hash(client: S3Client, *, bucket: str, key: str) -> tuple[str, int]:
    response = client.get_object(Bucket=bucket, Key=key)
    stream = response["Body"]
    digest = hashlib.sha256()
    total = 0
    try:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            total += len(chunk)
    finally:
        stream.close()
    return digest.hexdigest(), total


def execute_control_plane_backup(
    session_factory: sessionmaker[Session],
    settings: Settings,
    secret_box: SecretBox,
    operation_id: str,
    *,
    client_factory: Callable[[RemoteBackupTarget, SecretBox], S3Client] | None = None,
) -> dict[str, Any]:
    effective_client_factory = client_factory or create_s3_client
    with session_factory() as session:
        run = session.get(RemoteBackupRun, operation_id)
        operation = session.get(Operation, operation_id)
        if run is None or operation is None:
            raise BackupError("backup_run_missing", "The durable backup run is missing.")
        target = session.get(RemoteBackupTarget, run.target_id)
        if target is None or not target.enabled:
            raise BackupError(
                "backup_target_unavailable", "The remote backup target is unavailable."
            )
        target_id = target.id
    artifact_root = settings.backup_artifact_root.resolve(strict=False) / operation_id
    artifact = artifact_root / "hoardarr-control-plane.tar.gz"
    with session_factory() as session, session.begin():
        run = session.get(RemoteBackupRun, operation_id)
        operation = session.get(Operation, operation_id)
        if run is None or operation is None:
            raise BackupError("backup_run_missing", "The durable backup run is missing.")
        run.status = "running"
        run.phase = "staging"
        run.started_at = run.started_at or utc_now()
        run.updated_at = utc_now()
        append_event(
            session, operation, "backup_staging", "Creating a consistent control-plane backup"
        )
    if not artifact.is_file():
        artifact, manifest = build_control_plane_artifact(settings, operation_id)
    else:
        manifest = {
            "artifact_sha256": _hash_file(artifact),
            "artifact_size_bytes": artifact.stat().st_size,
        }
    digest = str(manifest["artifact_sha256"])
    size = int(manifest["artifact_size_bytes"])
    with session_factory() as session, session.begin():
        run = session.get(RemoteBackupRun, operation_id)
        operation = session.get(Operation, operation_id)
        target = session.get(RemoteBackupTarget, target_id)
        if run is None or operation is None or target is None:
            raise BackupError("backup_run_missing", "The durable backup run is missing.")
        if run.artifact_sha256 and run.artifact_sha256 != digest:
            raise BackupError(
                "backup_staging_changed", "The staged backup changed during recovery."
            )
        run.artifact_sha256 = digest
        run.artifact_size_bytes = size
        run.object_key = (
            run.object_key
            or f"{target.prefix}/control-plane/"
            f"{operation.created_at:%Y/%m/%d}/{operation.id}.tar.gz"
        )
        run.phase = "uploading"
        run.updated_at = utc_now()
        append_event(
            session,
            operation,
            "backup_uploading",
            "Uploading the control-plane archive for remote checksum verification",
            {"bytes": size},
        )
        object_key = run.object_key
    with session_factory() as session:
        target = session.get(RemoteBackupTarget, target_id)
        run = session.get(RemoteBackupRun, operation_id)
        if target is None or run is None or object_key is None:
            raise BackupError("backup_run_missing", "The durable backup run is missing.")
        client = effective_client_factory(target, secret_box)
        bucket = target.bucket
        bandwidth_limit_mib = target.bandwidth_limit_mib
    metadata = {"hoardarr-sha256": digest, "hoardarr-kind": "control-plane"}
    limiter = UploadRateLimiter(bandwidth_limit_mib)
    try:
        if size < MULTIPART_PART_BYTES:
            with artifact.open("rb") as stream:
                payload = stream.read()
            limiter.account(len(payload))
            client.put_object(Bucket=bucket, Key=object_key, Body=payload, Metadata=metadata)
        else:
            with session_factory() as session, session.begin():
                run = session.get(RemoteBackupRun, operation_id)
                if run is None:
                    raise BackupError("backup_run_missing", "The durable backup run is missing.")
                if run.upload_id is None:
                    response = client.create_multipart_upload(
                        Bucket=bucket,
                        Key=object_key,
                        Metadata=metadata,
                    )
                    run.upload_id = str(response["UploadId"])
                    run.updated_at = utc_now()
                upload_id = run.upload_id
            with session_factory() as session:
                run = session.get(RemoteBackupRun, operation_id)
                target = session.get(RemoteBackupTarget, target_id)
                if run is None or target is None:
                    raise BackupError("backup_run_missing", "The durable backup run is missing.")
                parts = _read_parts(client, target, run)
            completed = {int(item["PartNumber"]): item for item in parts}
            with artifact.open("rb") as stream:
                part_number = 1
                while payload := stream.read(MULTIPART_PART_BYTES):
                    if part_number not in completed:
                        limiter.account(len(payload))
                        response = client.upload_part(
                            Bucket=bucket,
                            Key=object_key,
                            UploadId=upload_id,
                            PartNumber=part_number,
                            Body=payload,
                        )
                        completed[part_number] = {
                            "PartNumber": part_number,
                            "ETag": str(response["ETag"]),
                        }
                        with session_factory() as session, session.begin():
                            run = session.get(RemoteBackupRun, operation_id)
                            operation = session.get(Operation, operation_id)
                            if run is None or operation is None:
                                raise BackupError(
                                    "backup_run_missing", "The durable backup run is missing."
                                )
                            run.completed_parts_json = [completed[key] for key in sorted(completed)]
                            run.updated_at = utc_now()
                            operation.heartbeat_at = utc_now()
                    part_number += 1
            client.complete_multipart_upload(
                Bucket=bucket,
                Key=object_key,
                UploadId=upload_id,
                MultipartUpload={"Parts": [completed[key] for key in sorted(completed)]},
            )
        head = client.head_object(Bucket=bucket, Key=object_key)
        if (
            int(head.get("ContentLength", -1)) != size
            or head.get("Metadata", {}).get("hoardarr-sha256") != digest
        ):
            raise BackupError(
                "backup_remote_metadata_mismatch", "The uploaded backup metadata did not verify."
            )
        remote_digest, remote_size = _download_hash(client, bucket=bucket, key=object_key)
        if remote_digest != digest or remote_size != size:
            raise BackupError(
                "backup_remote_checksum_mismatch",
                "The uploaded backup failed full SHA-256 verification.",
            )
    except BackupError:
        raise
    except Exception as exc:
        raise _safe_s3_error(exc, "upload") from exc
    completed_at = utc_now()
    report = {
        "object_key": object_key,
        "artifact_sha256": digest,
        "artifact_size_bytes": size,
        "remote_verification": "full_sha256",
        "bandwidth_limit_mib": bandwidth_limit_mib,
        "secrets_included": False,
        "completed_at": completed_at.isoformat(),
    }
    with session_factory() as session, session.begin():
        run = session.get(RemoteBackupRun, operation_id)
        target = session.get(RemoteBackupTarget, target_id)
        if run is None or target is None:
            raise BackupError("backup_run_missing", "The durable backup run is missing.")
        run.status = "succeeded"
        run.phase = "completed"
        run.report_json = report
        run.completed_at = completed_at
        run.updated_at = completed_at
        target.status = "available"
        target.last_success_at = completed_at
        target.last_error_json = None
        target.updated_at = completed_at
    try:
        artifact.unlink(missing_ok=True)
        (artifact_root / "hoardarr.db").unlink(missing_ok=True)
        (artifact_root / "manifest.json").unlink(missing_ok=True)
        artifact_root.rmdir()
    except OSError:
        pass
    return report


def validate_remote_archive(
    target: RemoteBackupTarget,
    secret_box: SecretBox,
    *,
    object_key: str,
    expected_sha256: str,
    client_factory: Callable[[RemoteBackupTarget, SecretBox], S3Client] | None = None,
) -> dict[str, Any]:
    client = (client_factory or create_s3_client)(target, secret_box)
    digest, size = _download_hash(client, bucket=target.bucket, key=object_key)
    if digest != expected_sha256:
        raise BackupError(
            "backup_restore_checksum_mismatch",
            "The remote archive checksum does not match its recorded backup.",
        )
    with tempfile.TemporaryDirectory(prefix="hoardarr-restore-check-") as directory:
        archive_path = Path(directory) / "archive.tar.gz"
        response = client.get_object(Bucket=target.bucket, Key=object_key)
        stream = response["Body"]
        try:
            with archive_path.open("wb") as output:
                while chunk := stream.read(1024 * 1024):
                    output.write(chunk)
        finally:
            stream.close()
        extract_root = Path(directory) / "content"
        _extract_and_validate_archive(archive_path, extract_root)
    return {
        "object_key": object_key,
        "artifact_sha256": digest,
        "artifact_size_bytes": size,
        "database_integrity": "verified",
        "manifest_schema_version": 1,
        "restore_performed": False,
    }


def _extract_and_validate_archive(archive_path: Path, extract_root: Path) -> dict[str, Any]:
    extract_root.mkdir(mode=0o700)
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > 4096:
                raise BackupError(
                    "backup_restore_archive_invalid",
                    "The backup archive contains too many entries.",
                )
            total_size = 0
            for member in members:
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or member.issym()
                    or member.islnk()
                    or not (member.isfile() or member.isdir())
                ):
                    raise BackupError(
                        "backup_restore_archive_invalid",
                        "The backup archive contains an unsafe path.",
                    )
                total_size += max(0, int(member.size))
                if total_size > MAX_RESTORE_UNCOMPRESSED_BYTES:
                    raise BackupError(
                        "backup_restore_archive_too_large",
                        "The expanded backup exceeds the restore safety limit.",
                    )
            archive.extractall(extract_root, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise BackupError(
            "backup_restore_archive_invalid", "The control-plane archive could not be read."
        ) from exc
    manifest_path = extract_root / "manifest.json"
    database_path = extract_root / "database" / "hoardarr.db"
    if not manifest_path.is_file() or not database_path.is_file():
        raise BackupError(
            "backup_restore_archive_invalid", "The control-plane archive is incomplete."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError(
            "backup_restore_archive_invalid", "The control-plane manifest is invalid."
        ) from exc
    if manifest.get("kind") != "hoardarr_control_plane" or manifest.get("schema_version") != 1:
        raise BackupError(
            "backup_restore_archive_invalid", "The control-plane manifest is unsupported."
        )
    if _hash_file(database_path) != manifest.get("database", {}).get("sha256"):
        raise BackupError(
            "backup_restore_database_mismatch",
            "The restored database checksum does not match the manifest.",
        )
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise BackupError(
                "backup_restore_database_invalid",
                "The restored database failed its integrity check.",
            )
    finally:
        connection.close()
    return manifest


def _fresh_database(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if "users" not in tables:
                return True
            return connection.execute("SELECT count(*) FROM users").fetchone() == (0,)
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise BackupError(
            "backup_restore_destination_invalid",
            "The destination database could not be inspected safely.",
        ) from exc


def apply_fresh_control_plane_restore(
    settings: Settings,
    archive_path: Path,
    expected_sha256: str,
) -> dict[str, Any]:
    """Atomically apply a redacted archive to a fresh, offline appliance."""

    archive = archive_path.resolve(strict=False)
    if archive.is_symlink() or not archive.is_file():
        raise BackupError("backup_restore_archive_missing", "The restore archive is unavailable.")
    digest = expected_sha256.strip().casefold()
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None or _hash_file(archive) != digest:
        raise BackupError(
            "backup_restore_checksum_mismatch",
            "The local archive checksum does not match the supplied SHA-256 digest.",
        )
    destination_database = sqlite_database_path(settings.database_url)
    if destination_database is None:
        raise BackupError(
            "backup_restore_database_unsupported", "Fresh restore requires a SQLite database."
        )
    destination_database = destination_database.resolve(strict=False)
    if destination_database.is_symlink() or not _fresh_database(destination_database):
        raise BackupError(
            "backup_restore_destination_not_fresh",
            "Fresh restore refuses to replace an appliance that already has an owner account.",
        )
    destination_database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    configuration_root = settings.configuration_root.resolve(strict=False)
    if configuration_root.is_symlink():
        raise BackupError(
            "backup_restore_configuration_unsafe",
            "The configuration root cannot be a symbolic link.",
        )
    configuration_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    rollback_root = settings.backup_artifact_root.resolve(strict=False)
    rollback_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    rollback = rollback_root / f"restore-rollback-{digest[:12]}"
    if rollback.exists() or rollback.is_symlink():
        raise BackupError(
            "backup_restore_rollback_exists",
            "A rollback snapshot already exists for this archive.",
        )
    with tempfile.TemporaryDirectory(
        prefix="hoardarr-fresh-restore-", dir=destination_database.parent
    ) as directory:
        staging = Path(directory)
        extracted = staging / "archive"
        manifest = _extract_and_validate_archive(archive, extracted)
        if manifest.get("database", {}).get("credential_mode") != "redacted_reentry_required":
            raise BackupError(
                "backup_restore_credentials_unsafe",
                "This archive does not prove that installation-bound credentials were removed.",
            )
        rollback.mkdir(mode=0o700)
        source_database = extracted / "database" / "hoardarr.db"
        staged_database = staging / "hoardarr.db"
        shutil.copyfile(source_database, staged_database)
        os.chmod(staged_database, 0o600)
        existing_database = destination_database.exists()
        if existing_database:
            shutil.copy2(destination_database, rollback / "hoardarr.db")
        restored_configuration: list[str] = []
        configuration = extracted / "configuration"
        try:
            for source in sorted(configuration.rglob("*")) if configuration.is_dir() else []:
                if not source.is_file() or source.is_symlink():
                    continue
                relative = source.relative_to(configuration)
                candidate = configuration_root / relative
                if candidate.is_symlink():
                    raise BackupError(
                        "backup_restore_configuration_unsafe",
                        "A restored configuration target cannot be a symbolic link.",
                    )
                destination = candidate.resolve(strict=False)
                try:
                    destination.relative_to(configuration_root)
                except ValueError as exc:
                    raise BackupError(
                        "backup_restore_configuration_unsafe",
                        "A restored configuration path escapes the configuration root.",
                    ) from exc
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if destination.exists():
                    rollback_path = rollback / "configuration" / relative
                    rollback_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    shutil.copy2(destination, rollback_path)
                staged_config = staging / "configuration-stage" / relative
                staged_config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(source, staged_config)
                os.chmod(staged_config, 0o600)
                os.replace(staged_config, destination)
                restored_configuration.append(relative.as_posix())
            os.replace(staged_database, destination_database)
        except Exception:
            for relative in reversed(restored_configuration):
                destination = configuration_root / relative
                rollback_path = rollback / "configuration" / relative
                if rollback_path.is_file():
                    shutil.copy2(rollback_path, destination)
                else:
                    destination.unlink(missing_ok=True)
            if existing_database and (rollback / "hoardarr.db").is_file():
                shutil.copy2(rollback / "hoardarr.db", destination_database)
            raise
    return {
        "restore_performed": True,
        "artifact_sha256": digest,
        "source_version": manifest.get("hoardarr_version"),
        "credential_mode": "redacted_reentry_required",
        "configuration_files": restored_configuration,
        "rollback_path": str(rollback),
        "next_steps": [
            "run database migrations",
            "restart Hoardarr services",
            "create a new owner account",
            "re-enter integration credentials",
            "reconcile disks by stable identity",
        ],
    }
