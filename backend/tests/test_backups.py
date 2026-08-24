from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from collections import namedtuple
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

import hoardarr.backups.service as backup_service
import hoardarr.operations.worker as worker_service
from hoardarr.auth.service import Principal
from hoardarr.backups.scheduler import queue_due_control_plane_backups
from hoardarr.backups.service import (
    BackupError,
    UploadRateLimiter,
    apply_fresh_control_plane_restore,
    build_control_plane_artifact,
    encrypt_credentials,
    execute_control_plane_backup,
    target_fingerprint,
    validate_endpoint,
    validate_remote_archive,
)
from hoardarr.backups.service import (
    test_target_connection as verify_target_connection,
)
from hoardarr.core.config import Settings
from hoardarr.core.secrets import SecretBox
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.db.models import (
    ApiToken,
    AuthSession,
    ConnectivityService,
    IntegrationConnection,
    Operation,
    RemoteBackupRun,
    RemoteBackupTarget,
    User,
    WebhookEndpoint,
)
from hoardarr.operations.service import create_operation, mark_failed_resource


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, dict[str, str]]] = {}
        self.uploads: dict[str, dict[int, bytes]] = {}
        self.upload_metadata: dict[str, tuple[str, str, dict[str, str]]] = {}
        self.deleted: list[tuple[str, str]] = []

    @staticmethod
    def _bytes(value: Any) -> bytes:
        if isinstance(value, bytes):
            return value
        return value.read()

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = (
            self._bytes(kwargs["Body"]),
            dict(kwargs.get("Metadata", {})),
        )
        return {"ETag": '"put"'}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        payload, metadata = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        return {"ContentLength": len(payload), "Metadata": metadata}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        payload, _metadata = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        return {"Body": io.BytesIO(payload)}

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        key = (kwargs["Bucket"], kwargs["Key"])
        self.deleted.append(key)
        self.objects.pop(key, None)
        return {}

    def create_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
        upload_id = f"upload-{len(self.uploads) + 1}"
        self.uploads[upload_id] = {}
        self.upload_metadata[upload_id] = (
            kwargs["Bucket"],
            kwargs["Key"],
            dict(kwargs.get("Metadata", {})),
        )
        return {"UploadId": upload_id}

    def upload_part(self, **kwargs: Any) -> dict[str, Any]:
        payload = self._bytes(kwargs["Body"])
        self.uploads[kwargs["UploadId"]][int(kwargs["PartNumber"])] = payload
        return {"ETag": f'"part-{kwargs["PartNumber"]}"'}

    def list_parts(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "Parts": [
                {"PartNumber": number, "ETag": f'"part-{number}"'}
                for number in sorted(self.uploads[kwargs["UploadId"]])
            ]
        }

    def complete_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
        upload_id = kwargs["UploadId"]
        bucket, key, metadata = self.upload_metadata[upload_id]
        payload = b"".join(
            self.uploads[upload_id][number] for number in sorted(self.uploads[upload_id])
        )
        self.objects[(bucket, key)] = (payload, metadata)
        return {"ETag": '"complete"'}

    def abort_multipart_upload(self, **_kwargs: Any) -> dict[str, Any]:
        return {}


def _settings(tmp_path: Path) -> Settings:
    database = tmp_path / "hoardarr.db"
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{database.as_posix()}",
        secret_key_file=tmp_path / "secret.key",
        secure_cookies=False,
        backup_artifact_root=tmp_path / "backups",
        configuration_root=tmp_path / "config",
    )
    upgrade_database(settings.database_url)
    return settings


def _target(secret_box: SecretBox) -> RemoteBackupTarget:
    target = RemoteBackupTarget(
        id="backup-target",
        name="Test MinIO",
        provider="minio",
        endpoint_url="https://127.0.0.1:9000",
        region="us-east-1",
        bucket="hoardarr-backups",
        prefix="test-host",
        force_path_style=True,
        verify_tls=True,
        allow_private_network=True,
        allow_insecure_http=False,
        schedule_json={"enabled": False},
        secret_ciphertext=b"placeholder",
        credential_fingerprint="placeholder",
        status="available",
        created_by="owner",
    )
    target.secret_ciphertext, target.credential_fingerprint = encrypt_credentials(
        secret_box,
        target.id,
        access_key_id="test-access-key",
        secret_access_key="test-secret-key",
        session_token=None,
    )
    return target


def test_endpoint_policy_blocks_private_destinations_without_explicit_approval() -> None:
    def resolver(*_args, **_kwargs):
        return [(None, None, None, None, ("127.0.0.1", 9000))]

    with pytest.raises(BackupError, match="explicit local-network approval"):
        validate_endpoint(
            "minio",
            "https://minio.test:9000",
            allow_private_network=False,
            allow_insecure_http=False,
            resolver=resolver,
        )
    assert (
        validate_endpoint(
            "minio",
            "https://minio.test:9000",
            allow_private_network=True,
            allow_insecure_http=False,
            resolver=resolver,
        )
        == "https://minio.test:9000"
    )


def test_upload_rate_limiter_paces_payload_bytes_with_a_bounded_part_buffer() -> None:
    now = [10.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = UploadRateLimiter(1, clock=lambda: now[0], sleeper=sleep)
    limiter.account(512 * 1024)
    limiter.account(512 * 1024)
    assert sleeps == [0.5, 0.5]
    assert limiter.transferred == 1024 * 1024


def test_connection_test_writes_verifies_and_removes_only_its_marker(tmp_path: Path) -> None:
    secret_box = SecretBox.from_file(tmp_path / "secret.key", create=True)
    target = _target(secret_box)
    fake = FakeS3()
    result = verify_target_connection(target, secret_box, client_factory=lambda *_args: fake)
    assert result["write_read_delete"] == "verified"
    assert fake.objects == {}
    assert fake.deleted == [
        (target.bucket, f"{target.prefix}/.hoardarr-connection-test/{target.id}")
    ]


def test_control_plane_artifact_excludes_secret_named_files_and_symlinks(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.configuration_root.mkdir()
    (settings.configuration_root / "hoardarr.env").write_text("SAFE=value\n", encoding="utf-8")
    (settings.configuration_root / "api-secret.txt").write_text("do-not-export", encoding="utf-8")
    with suppress(OSError):
        (settings.configuration_root / "linked.conf").symlink_to(
            settings.configuration_root / "hoardarr.env"
        )
    artifact, report = build_control_plane_artifact(settings, "artifact-test")
    assert report["configuration"]["secrets_included"] is False
    with tarfile.open(artifact, "r:gz") as archive:
        names = set(archive.getnames())
        assert "configuration/hoardarr.env" in names
        assert "configuration/api-secret.txt" not in names
        assert "configuration/linked.conf" not in names
        manifest = json.load(archive.extractfile("manifest.json"))  # type: ignore[arg-type]
        assert manifest["database"]["sha256"]


def test_default_artifact_removes_live_auth_and_requires_credential_reentry(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.configuration_root.mkdir()
    (settings.configuration_root / "hoardarr.env").write_text(
        "HOARDARR_BIND_HOST=127.0.0.1\n"
        "HOARDARR_SETUP_TOKEN=must-not-leave-this-host\n",
        encoding="utf-8",
    )
    secret_box = SecretBox.from_file(settings.secret_key_file, create=True)
    factory = create_session_factory(create_database_engine(settings.database_url))
    with factory() as session, session.begin():
        session.add(User(id="owner", username="owner", password_hash="password-hash"))
        session.flush()
        session.add(
            AuthSession(
                id="session",
                user_id="owner",
                token_hash="session-hash",
                csrf_hash="csrf-hash",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        session.add(
            ApiToken(
                id="api-token",
                user_id="owner",
                name="automation",
                token_hash="api-token-hash",
                scopes_json=["read"],
            )
        )
        session.add(
            IntegrationConnection(
                id="sonarr",
                name="Sonarr",
                expected_product="sonarr",
                base_url="http://sonarr.local:8989",
                api_key_ciphertext=b"encrypted-api-key",
            )
        )
        session.add(
            ConnectivityService(
                id="iscsi",
                protocol="iscsi",
                name="media-lun",
                config_json={"target": "iqn.example:media"},
                config_sha256="a" * 64,
                secret_ciphertext=b"encrypted-chap-password",
            )
        )
        session.add(_target(secret_box))
        session.add(
            WebhookEndpoint(
                id="webhook",
                name="Home automation",
                url="http://automation.local/events",
                secret_ciphertext=b"encrypted-signing-secret",
                secret_fingerprint="fingerprint",
                created_by="owner",
            )
        )

    artifact, report = build_control_plane_artifact(settings, "redacted-artifact")
    assert report["database"]["credential_mode"] == "redacted_reentry_required"
    assert report["database"]["redacted_rows"]["auth_sessions"] == 1
    assert report["database"]["redacted_rows"]["api_tokens"] == 1
    assert report["database"]["redacted_rows"]["users"] == 1
    assert report["configuration"]["redacted_keys"] == {
        "hoardarr.env": ["HOARDARR_SETUP_TOKEN"]
    }

    extract_root = tmp_path / "restored"
    with tarfile.open(artifact, "r:gz") as archive:
        archive.extractall(extract_root, filter="data")
    restored_env = (extract_root / "configuration" / "hoardarr.env").read_text()
    assert restored_env == "HOARDARR_BIND_HOST=127.0.0.1\n"
    assert "must-not-leave-this-host" not in artifact.read_bytes().decode(
        "latin-1", errors="ignore"
    )

    database = sqlite3.connect(extract_root / "database" / "hoardarr.db")
    try:
        assert database.execute("SELECT count(*) FROM auth_sessions").fetchone() == (0,)
        assert database.execute("SELECT count(*) FROM api_tokens").fetchone() == (0,)
        assert database.execute("SELECT count(*) FROM users").fetchone() == (0,)
        assert database.execute(
            "SELECT status, length(api_key_ciphertext) FROM integration_connections"
        ).fetchone() == ("credentials_required", 0)
        assert database.execute(
            "SELECT status, secret_ciphertext FROM connectivity_services"
        ).fetchone() == ("credentials_required", None)
        assert database.execute(
            "SELECT enabled, status, length(secret_ciphertext) FROM remote_backup_targets"
        ).fetchone() == (0, "credentials_required", 0)
        assert database.execute(
            "SELECT enabled, status, length(secret_ciphertext) FROM webhook_endpoints"
        ).fetchone() == (0, "credentials_required", 0)
    finally:
        database.close()


def test_fresh_restore_applies_atomically_and_retains_rollback(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = _settings(source_root)
    source.configuration_root.mkdir()
    (source.configuration_root / "hoardarr.env").write_text(
        "HOARDARR_BIND_HOST=10.0.0.10\nHOARDARR_SETUP_TOKEN=remove-me\n",
        encoding="utf-8",
    )
    with (
        create_session_factory(create_database_engine(source.database_url))() as session,
        session.begin(),
    ):
        session.add(User(id="owner", username="owner", password_hash="restored-hash"))
    artifact, artifact_report = build_control_plane_artifact(source, "fresh-restore-source")

    target_root = tmp_path / "target"
    target_root.mkdir()
    target = _settings(target_root)
    target.configuration_root.mkdir()
    (target.configuration_root / "hoardarr.env").write_text(
        "HOARDARR_BIND_HOST=127.0.0.1\n",
        encoding="utf-8",
    )
    SecretBox.from_file(target.secret_key_file, create=True)
    report = apply_fresh_control_plane_restore(
        target, artifact, artifact_report["artifact_sha256"]
    )

    assert report["restore_performed"] is True
    assert report["credential_mode"] == "redacted_reentry_required"
    assert report["configuration_files"] == ["hoardarr.env"]
    assert (target.configuration_root / "hoardarr.env").read_text() == (
        "HOARDARR_BIND_HOST=10.0.0.10\n"
    )
    restored_database = sqlite3.connect(target_root / "hoardarr.db")
    try:
        assert restored_database.execute("SELECT count(*) FROM users").fetchone() == (0,)
    finally:
        restored_database.close()
    rollback = Path(report["rollback_path"])
    assert (rollback / "hoardarr.db").is_file()
    assert (rollback / "configuration" / "hoardarr.env").read_text() == (
        "HOARDARR_BIND_HOST=127.0.0.1\n"
    )
    with pytest.raises(BackupError, match="rollback snapshot already exists"):
        apply_fresh_control_plane_restore(
            target, artifact, artifact_report["artifact_sha256"]
        )
    with pytest.raises(BackupError, match="checksum"):
        apply_fresh_control_plane_restore(target, artifact, "0" * 64)

    occupied_root = tmp_path / "occupied"
    occupied_root.mkdir()
    occupied = _settings(occupied_root)
    with (
        create_session_factory(create_database_engine(occupied.database_url))() as session,
        session.begin(),
    ):
        session.add(User(id="existing", username="existing", password_hash="hash"))
    with pytest.raises(BackupError, match="already has an owner"):
        apply_fresh_control_plane_restore(
            occupied, artifact, artifact_report["artifact_sha256"]
        )


def test_fresh_restore_rolls_configuration_back_when_database_switch_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = _settings(source_root)
    source.configuration_root.mkdir()
    (source.configuration_root / "hoardarr.env").write_text(
        "HOARDARR_BIND_HOST=10.0.0.20\n", encoding="utf-8"
    )
    artifact, artifact_report = build_control_plane_artifact(source, "rollback-source")

    target_root = tmp_path / "target"
    target_root.mkdir()
    target = _settings(target_root)
    target.configuration_root.mkdir()
    original_configuration = "HOARDARR_BIND_HOST=127.0.0.1\n"
    (target.configuration_root / "hoardarr.env").write_text(
        original_configuration, encoding="utf-8"
    )
    destination_database = target_root / "hoardarr.db"
    original_replace = backup_service.os.replace

    def fail_database_switch(source_path: Path, destination_path: Path) -> None:
        if Path(destination_path) == destination_database:
            raise OSError("injected database switch failure")
        original_replace(source_path, destination_path)

    monkeypatch.setattr(backup_service.os, "replace", fail_database_switch)
    with pytest.raises(OSError, match="injected database switch failure"):
        apply_fresh_control_plane_restore(
            target, artifact, artifact_report["artifact_sha256"]
        )
    assert (target.configuration_root / "hoardarr.env").read_text() == (
        original_configuration
    )
    database = sqlite3.connect(destination_database)
    try:
        assert database.execute("SELECT count(*) FROM users").fetchone() == (0,)
        assert database.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        database.close()


def test_fresh_restore_fails_before_mutation_when_staging_space_is_insufficient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = _settings(source_root)
    source.configuration_root.mkdir()
    (source.configuration_root / "hoardarr.env").write_text(
        "HOARDARR_ENVIRONMENT=test\n", encoding="utf-8"
    )
    artifact, artifact_report = build_control_plane_artifact(source, "space-source")

    target_root = tmp_path / "target"
    target_root.mkdir()
    target = _settings(target_root)
    target.configuration_root.mkdir()
    original = "HOARDARR_BIND_HOST=127.0.0.1\n"
    (target.configuration_root / "hoardarr.env").write_text(original, encoding="utf-8")
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(backup_service.shutil, "disk_usage", lambda _path: usage(1, 1, 0))

    with pytest.raises(BackupError, match="enough free space"):
        apply_fresh_control_plane_restore(
            target, artifact, artifact_report["artifact_sha256"]
        )
    assert (target.configuration_root / "hoardarr.env").read_text() == original
    assert not any(target.backup_artifact_root.glob("restore-rollback-*"))


def test_durable_control_plane_backup_multipart_upload_and_restore_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    settings.configuration_root.mkdir()
    (settings.configuration_root / "hoardarr.env").write_text("SAFE=value\n", encoding="utf-8")
    secret_box = SecretBox.from_file(settings.secret_key_file, create=True)
    factory = create_session_factory(create_database_engine(settings.database_url))
    principal = Principal(
        user_id="owner",
        username="owner",
        is_admin=True,
        auth_type="session",
        scopes=frozenset({"read", "operate", "admin"}),
        session_id="session",
    )
    with factory() as session, session.begin():
        target = _target(secret_box)
        session.add(target)
        operation, created = create_operation(
            session,
            kind="backup.control_plane",
            principal=principal,
            request={"target_id": target.id},
            idempotency_key="backup-run-test",
            resource_type="remote_backup_target",
            resource_id=target.id,
        )
        assert created is True
        operation.status = "running"
        session.add(
            RemoteBackupRun(id=operation.id, target_id=target.id, backup_kind="control_plane")
        )
        operation_id = operation.id
    fake = FakeS3()
    monkeypatch.setattr(backup_service, "MULTIPART_PART_BYTES", 1024)
    report = execute_control_plane_backup(
        factory,
        settings,
        secret_box,
        operation_id,
        client_factory=lambda *_args: fake,
    )
    assert report["remote_verification"] == "full_sha256"
    assert fake.uploads and len(next(iter(fake.uploads.values()))) > 1
    with factory() as session:
        run = session.get(RemoteBackupRun, operation_id)
        target = session.get(RemoteBackupTarget, "backup-target")
        assert run is not None and run.status == "succeeded"
        assert target is not None and target.last_success_at is not None
        validation = validate_remote_archive(
            target,
            secret_box,
            object_key=str(run.object_key),
            expected_sha256=str(run.artifact_sha256),
            client_factory=lambda *_args: fake,
        )
    assert validation["database_integrity"] == "verified"
    assert validation["restore_performed"] is False
    with pytest.raises(BackupError, match="checksum"):
        validate_remote_archive(
            target,
            secret_box,
            object_key=str(run.object_key),
            expected_sha256="0" * 64,
            client_factory=lambda *_args: fake,
        )


def test_backup_failure_updates_durable_run_and_target_health(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    secret_box = SecretBox.from_file(settings.secret_key_file, create=True)
    factory = create_session_factory(create_database_engine(settings.database_url))
    principal = Principal(
        user_id="owner",
        username="owner",
        is_admin=True,
        auth_type="session",
        scopes=frozenset({"read", "operate", "admin"}),
        session_id="browser",
    )
    with factory() as session, session.begin():
        target = _target(secret_box)
        session.add(target)
        operation, _created = create_operation(
            session,
            kind="backup.control_plane",
            principal=principal,
            request={"target_id": target.id},
            idempotency_key="failure-key",
            resource_type="remote_backup_target",
            resource_id=target.id,
        )
        session.add(
            RemoteBackupRun(id=operation.id, target_id=target.id, backup_kind="control_plane")
        )
        session.flush()
        mark_failed_resource(session, operation, "backup_upload_failed")
        operation_id = operation.id
    with factory() as session:
        run = session.get(RemoteBackupRun, operation_id)
        target = session.get(RemoteBackupTarget, "backup-target")
        assert run is not None and run.status == "failed" and run.phase == "failed"
        assert run.completed_at is not None
        assert target is not None and target.status == "error"
        assert target.last_error_json == {"code": "backup_upload_failed"}


def test_scheduler_queues_one_due_backup_and_never_duplicates_active_work(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    secret_box = SecretBox.from_file(settings.secret_key_file, create=True)
    factory = create_session_factory(create_database_engine(settings.database_url))
    now = datetime(2026, 8, 23, 12, tzinfo=UTC)
    with factory() as session, session.begin():
        target = _target(secret_box)
        target.schedule_json = {"enabled": True, "interval_hours": 24}
        target.updated_at = now - timedelta(hours=25)
        session.add(target)
    with factory() as session, session.begin():
        assert queue_due_control_plane_backups(session, now=now) == 1
        assert queue_due_control_plane_backups(session, now=now) == 0
    with factory() as session:
        runs = list(session.scalars(select(RemoteBackupRun)))
        assert len(runs) == 1
        operation = session.get(Operation, runs[0].id)
        assert operation is not None
        assert operation.request_json["scheduled"] is True


def test_worker_retries_a_temporary_provider_outage_then_resumes_same_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    settings.configuration_root.mkdir()
    (settings.configuration_root / "hoardarr.env").write_text(
        "HOARDARR_ENVIRONMENT=test\n", encoding="utf-8"
    )
    secret_box = SecretBox.from_file(settings.secret_key_file, create=True)
    factory = create_session_factory(create_database_engine(settings.database_url))
    principal = Principal(
        user_id="owner",
        username="owner",
        is_admin=True,
        auth_type="session",
        scopes=frozenset({"read", "operate", "admin"}),
        session_id="browser",
    )
    with factory() as session, session.begin():
        target = _target(secret_box)
        session.add(target)
        session.flush()
        operation, _created = create_operation(
            session,
            kind="backup.control_plane",
            principal=principal,
            request={
                "target_id": target.id,
                "target_fingerprint": target_fingerprint(target),
                "backup_kind": "control_plane",
                "secrets_included": False,
            },
            idempotency_key="provider-outage",
            resource_type="remote_backup_target",
            resource_id=target.id,
        )
        session.add(
            RemoteBackupRun(id=operation.id, target_id=target.id, backup_kind="control_plane")
        )
        operation_id = operation.id
    fake = FakeS3()
    calls = 0

    def flaky_backup(*args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise BackupError(
                "backup_provider_unavailable",
                "The S3 service is temporarily unavailable.",
                retryable=True,
            )
        return execute_control_plane_backup(*args, client_factory=lambda *_args: fake)

    monkeypatch.setattr(worker_service, "execute_control_plane_backup", flaky_backup)
    assert worker_service.run_once(
        session_factory=factory,
        settings=settings,
        secret_box=secret_box,
        worker_id="backup-outage-worker",
    )
    with factory() as session, session.begin():
        operation = session.get(Operation, operation_id)
        run = session.get(RemoteBackupRun, operation_id)
        assert operation is not None and run is not None
        assert operation.status == "queued"
        assert operation.error_json["retry_attempt"] == 1
        assert run.status == "queued"
        operation.not_before = datetime.now(UTC) - timedelta(seconds=1)
    assert worker_service.run_once(
        session_factory=factory,
        settings=settings,
        secret_box=secret_box,
        worker_id="backup-outage-worker",
    )
    with factory() as session:
        operation = session.get(Operation, operation_id)
        run = session.get(RemoteBackupRun, operation_id)
        assert operation is not None and operation.status == "succeeded"
        assert run is not None and run.status == "succeeded"
        assert run.artifact_sha256
        assert calls == 2
