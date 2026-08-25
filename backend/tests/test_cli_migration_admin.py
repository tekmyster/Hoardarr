from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

import hoardarr.cli as cli
from hoardarr.core.config import Settings
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.db.models import AuthSession, PhysicalDisk, User, utc_now
from hoardarr.migration_identity import database_sha256
from test_migration_identity import manifest_document, seed_ext4, write_manifest


def settings_for(root: Path) -> Settings:
    database = root / "hoardarr.db"
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{database.as_posix()}",
        secret_key_file=root / "secret.key",
        secure_cookies=False,
    )
    upgrade_database(settings.database_url)
    return settings


def test_identity_cli_dry_run_apply_and_local_root_enforcement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = settings_for(tmp_path)
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    seed_ext4(factory)
    engine.dispose()
    path, _manifest, _digest = write_manifest(tmp_path, manifest_document())
    database = tmp_path / "hoardarr.db"
    expected = database_sha256(database)
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_is_root", lambda: False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hoardarr",
            "migrate-hardware-identities",
            "--manifest",
            str(path.resolve()),
            "--expected-database-sha256",
            expected,
            "--dry-run",
        ],
    )
    with pytest.raises(SystemExit) as denied:
        cli.main()
    assert denied.value.code == 3
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "local_root_required"

    monkeypatch.setattr(cli, "_is_root", lambda: True)
    cli.main()
    dry = json.loads(capsys.readouterr().out)
    assert dry["status"] == "ready" and dry["mapped_count"] == 1
    assert database_sha256(database) == expected

    monkeypatch.setattr(sys, "argv", [*sys.argv[:-1], "--apply"])
    cli.main()
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "applied"
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    with factory() as session:
        assert session.get(PhysicalDisk, "disk-id").stable_identity == (  # type: ignore[union-attr]
            "wwn:hyperv-target-0001"
        )
    engine.dispose()


@pytest.mark.parametrize(
    "active_unit",
    [
        "hoardarr-api.service",
        "hoardarr-worker.service",
        "hoardarr-storage-status.service",
        "hoardarr-account-executor.service",
        "hoardarr-storage-executor.service",
    ],
)
def test_identity_cli_rejects_each_active_writer_before_database_access(
    active_unit: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected_units = (
        "hoardarr-api.service",
        "hoardarr-worker.service",
        "hoardarr-storage-status.service",
        "hoardarr-account-executor.service",
        "hoardarr-storage-executor.service",
    )
    observed_units: list[tuple[str, ...]] = []

    def active_units(units: tuple[str, ...]) -> list[str]:
        observed_units.append(units)
        return [active_unit]

    def database_access_forbidden() -> Settings:
        raise AssertionError("service gate must reject before database configuration is loaded")

    manifest = tmp_path / "private-identity-map.json"
    monkeypatch.setattr(cli, "_is_root", lambda: True)
    monkeypatch.setattr(cli, "_active_units", active_units)
    monkeypatch.setattr(cli, "Settings", database_access_forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hoardarr",
            "migrate-hardware-identities",
            "--manifest",
            str(manifest),
            "--expected-database-sha256",
            "a" * 64,
            "--dry-run",
        ],
    )

    with pytest.raises(SystemExit) as denied:
        cli.main()

    assert denied.value.code == 3
    assert observed_units == [expected_units]
    output = capsys.readouterr().out
    expected_output = {
        "schema_version": 1,
        "status": "rejected",
        "error": {
            "code": "services_active",
            "message": (
                "Stop all Hoardarr API, worker, storage-status, account-executor, and "
                "storage-executor services before hardware identity migration."
            ),
        },
        "mapped_count": 0,
        "rejected_count": 1,
    }
    assert json.loads(output) == expected_output
    assert output == json.dumps(expected_output, sort_keys=True) + "\n"
    assert active_unit not in output
    assert str(manifest) not in output
    assert "a" * 64 not in output


def test_session_cli_json_contract_count_precondition_and_redaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = settings_for(tmp_path)
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        session.add(User(id="owner", username="owner", password_hash="password-verifier"))
        session.flush()
        for index in range(2):
            session.add(
                AuthSession(
                    id=f"session-{index}",
                    user_id="owner",
                    token_hash=f"{index + 1:064x}",
                    csrf_hash=f"{index + 101:064x}",
                    expires_at=utc_now() + timedelta(hours=1),
                )
            )
    engine.dispose()
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_is_root", lambda: True)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hoardarr",
            "auth",
            "revoke-all-sessions",
            "--reason",
            "migration-cutover",
            "--expected-count",
            "1",
            "--json",
        ],
    )
    with pytest.raises(SystemExit) as mismatch:
        cli.main()
    assert mismatch.value.code == 4
    rejected_output = capsys.readouterr().out
    rejected = json.loads(rejected_output)
    assert rejected["observed_count"] == 2 and rejected["revoked_count"] == 0
    assert "token_hash" not in rejected_output and "csrf" not in rejected_output

    second_args = list(sys.argv)
    second_args[6] = "2"
    monkeypatch.setattr(sys, "argv", second_args)
    cli.main()
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["status"] == "succeeded"
    assert result["revoked_count"] == 2 and result["remaining_active_count"] == 0
    assert "password-verifier" not in output and "token_hash" not in output and "csrf" not in output
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    with factory() as session:
        assert list(session.scalars(select(AuthSession))) == []
        assert session.get(User, "owner").password_hash == "password-verifier"  # type: ignore[union-attr]
    engine.dispose()


def test_session_cli_rejects_non_root_without_database_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = settings_for(tmp_path)
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_is_root", lambda: False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hoardarr",
            "auth",
            "revoke-all-sessions",
            "--reason",
            "migration-cutover",
            "--expected-count",
            "0",
            "--json",
        ],
    )
    with pytest.raises(SystemExit) as denied:
        cli.main()
    assert denied.value.code == 3
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "local_root_required"
