from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from sqlalchemy import select

import hoardarr.cli as cli
from hoardarr.backups.service import decrypt_credentials, encrypt_credentials
from hoardarr.core.config import Settings
from hoardarr.core.secrets import SecretBox
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.db.models import RemoteBackupTarget, User


def _settings(root: Path) -> Settings:
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{(root / 'hoardarr.db').as_posix()}",
        secret_key_file=root / "secret.key",
        secure_cookies=False,
        backup_artifact_root=root / "backups",
        configuration_root=root / "config",
    )
    upgrade_database(settings.database_url)
    settings.configuration_root.mkdir()
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


def test_console_encrypted_export_and_fresh_restore_use_stdin_passphrase(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = _settings(source_root)
    source_secret_box = SecretBox.from_file(source.secret_key_file, create=True)
    source_factory = create_session_factory(create_database_engine(source.database_url))
    with source_factory() as session, session.begin():
        session.add(User(id="owner", username="owner", password_hash="verifier"))
        session.add(_target(source_secret_box))

    archive = tmp_path / "encrypted-control-plane.tar.gz"
    monkeypatch.setattr(cli, "Settings", lambda: source)
    monkeypatch.setattr(sys, "stdin", io.StringIO("correct horse battery staple\n"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hoardarr",
            "export-control-plane",
            "--output",
            str(archive),
            "--encrypt-secrets",
        ],
    )
    cli.main()
    export_report = json.loads(capsys.readouterr().out)
    assert archive.is_file()
    assert export_report["database"]["credential_mode"] == "encrypted_secret_key"

    target_root = tmp_path / "target"
    target_root.mkdir()
    target = _settings(target_root)
    SecretBox.from_file(target.secret_key_file, create=True)
    monkeypatch.setattr(cli, "Settings", lambda: target)
    monkeypatch.setattr(sys, "stdin", io.StringIO("correct horse battery staple\n"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hoardarr",
            "restore-control-plane",
            "--archive",
            str(archive),
            "--sha256",
            export_report["artifact_sha256"],
            "--passphrase-stdin",
            "--yes",
        ],
    )
    cli.main()
    restore_report = json.loads(capsys.readouterr().out)
    assert restore_report["credential_mode"] == "encrypted_secret_key"

    restored_box = SecretBox.from_file(target.secret_key_file, create=False)
    restored_factory = create_session_factory(create_database_engine(target.database_url))
    with restored_factory() as session:
        assert session.scalar(select(User)) is None
        restored_target = session.get(RemoteBackupTarget, "backup-target")
        assert restored_target is not None
        assert decrypt_credentials(restored_box, restored_target)["access_key_id"] == (
            "test-access-key"
        )
