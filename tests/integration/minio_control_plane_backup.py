"""Exercise Hoardarr's real control-plane backup path against a live MinIO server."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import boto3
from botocore.client import Config

from hoardarr.auth.service import Principal
from hoardarr.backups.service import (
    apply_fresh_control_plane_restore,
    encrypt_credentials,
    target_fingerprint,
    validate_remote_archive,
)
from hoardarr.backups.service import (
    test_target_connection as verify_target_connection,
)
from hoardarr.core.config import Settings
from hoardarr.core.secrets import SecretBox
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.db.models import Operation, RemoteBackupRun, RemoteBackupTarget, User
from hoardarr.operations.service import create_operation
from hoardarr.operations.worker import run_once


def main() -> int:
    endpoint = os.environ.get("HOARDARR_MINIO_ENDPOINT", "http://127.0.0.1:9000")
    access_key = os.environ["MINIO_ROOT_USER"]
    secret_key = os.environ["MINIO_ROOT_PASSWORD"]
    bucket = "hoardarr-integration"
    output = Path(
        os.environ.get("HOARDARR_BACKUP_EVIDENCE", "dist/validation/minio-backup.json")
    )
    with tempfile.TemporaryDirectory(prefix="hoardarr-minio-") as temporary:
        root = Path(temporary)
        settings = Settings(
            environment="test",
            database_url=f"sqlite:///{(root / 'hoardarr.db').as_posix()}",
            secret_key_file=root / "secret.key",
            secure_cookies=False,
            backup_artifact_root=root / "artifacts",
            configuration_root=root / "config",
        )
        settings.configuration_root.mkdir()
        (settings.configuration_root / "hoardarr.env").write_text(
            "HOARDARR_ENVIRONMENT=integration\n", encoding="utf-8"
        )
        upgrade_database(settings.database_url)
        secret_box = SecretBox.from_file(settings.secret_key_file, create=True)
        factory = create_session_factory(create_database_engine(settings.database_url))
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name="us-east-1",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            verify=False,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        s3.create_bucket(Bucket=bucket)
        target = RemoteBackupTarget(
            id="minio-integration",
            name="Disposable MinIO",
            provider="minio",
            endpoint_url=endpoint,
            region="us-east-1",
            bucket=bucket,
            prefix="integration",
            force_path_style=True,
            verify_tls=False,
            allow_private_network=True,
            allow_insecure_http=True,
            bandwidth_limit_mib=None,
            schedule_json={"enabled": False},
            secret_ciphertext=b"placeholder",
            credential_fingerprint="placeholder",
            status="not_tested",
            created_by="integration",
        )
        target.secret_ciphertext, target.credential_fingerprint = encrypt_credentials(
            secret_box,
            target.id,
            access_key_id=access_key,
            secret_access_key=secret_key,
            session_token=None,
        )
        connection = verify_target_connection(target, secret_box)
        target.status = "available"
        principal = Principal(
            user_id="integration",
            username="integration",
            is_admin=True,
            auth_type="test",
            scopes=frozenset({"read", "operate", "admin"}),
        )
        with factory() as session, session.begin():
            session.add(
                User(
                    id="integration-owner",
                    username="integration-owner",
                    password_hash="integration-password-hash",
                )
            )
            session.flush()
            session.add(target)
            session.flush()
            operation, created = create_operation(
                session,
                kind="backup.control_plane",
                principal=principal,
                request={
                    "target_id": target.id,
                    "target_fingerprint": target_fingerprint(target),
                    "backup_kind": "control_plane",
                    "secrets_included": False,
                },
                idempotency_key="minio-integration",
                resource_type="remote_backup_target",
                resource_id=target.id,
            )
            assert created
            session.add(
                RemoteBackupRun(
                    id=operation.id,
                    target_id=target.id,
                    backup_kind="control_plane",
                )
            )
            operation_id = operation.id
        assert run_once(
            session_factory=factory,
            settings=settings,
            secret_box=secret_box,
            worker_id="minio-integration-worker",
        )
        with factory() as session:
            run = session.get(RemoteBackupRun, operation_id)
            stored_target = session.get(RemoteBackupTarget, target.id)
            assert run is not None and stored_target is not None
            validation = validate_remote_archive(
                stored_target,
                secret_box,
                object_key=str(run.object_key),
                expected_sha256=str(run.artifact_sha256),
            )
            operation = session.get(Operation, operation_id)
            assert operation is not None and operation.status == "succeeded"
            report = operation.result_json
            archive_path = root / "downloaded-control-plane.tar.gz"
            remote = s3.get_object(Bucket=bucket, Key=str(run.object_key))["Body"]
            try:
                archive_path.write_bytes(remote.read())
            finally:
                remote.close()
            fresh_root = root / "fresh-appliance"
            fresh_root.mkdir()
            fresh_settings = Settings(
                environment="test",
                database_url=f"sqlite:///{(fresh_root / 'hoardarr.db').as_posix()}",
                secret_key_file=fresh_root / "secret.key",
                secure_cookies=False,
                backup_artifact_root=fresh_root / "artifacts",
                configuration_root=fresh_root / "config",
            )
            fresh_settings.configuration_root.mkdir()
            (fresh_settings.configuration_root / "hoardarr.env").write_text(
                "HOARDARR_ENVIRONMENT=test\n", encoding="utf-8"
            )
            upgrade_database(fresh_settings.database_url)
            SecretBox.from_file(fresh_settings.secret_key_file, create=True)
            fresh_restore = apply_fresh_control_plane_restore(
                fresh_settings, archive_path, str(run.artifact_sha256)
            )
        restored_factory = create_session_factory(
            create_database_engine(fresh_settings.database_url)
        )
        with restored_factory() as restored_session:
            restored_target = restored_session.get(RemoteBackupTarget, target.id)
            assert restored_session.get(User, "integration-owner") is None
            assert restored_target is not None
            assert restored_target.status == "credentials_required"
            assert restored_target.enabled is False
        evidence = {
            "provider": "live MinIO",
            "endpoint": endpoint,
            "connection": connection,
            "backup": report,
            "restore_validation": validation,
            "fresh_appliance_restore": fresh_restore,
            "operation_id": operation_id,
            "credentials_recorded": False,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
