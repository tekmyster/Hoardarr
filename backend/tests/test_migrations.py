from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import op
from sqlalchemy import inspect

from hoardarr.cli import migrate_main
from hoardarr.db.engine import create_database_engine
from hoardarr.db.migrate import database_is_current, upgrade_database


@pytest.mark.parametrize(
    "starting_revision",
    ["0001_initial", "0002_plan_approvals", "0003_connectivity_services"],
)
def test_every_retained_schema_revision_upgrades_directly_to_head_and_preserves_data(
    tmp_path: Path,
    starting_revision: str,
) -> None:
    database = tmp_path / f"{starting_revision}.db"
    database_url = f"sqlite:///{database.as_posix()}"
    upgrade_database(database_url, starting_revision)
    timestamp = "2026-08-21 20:00:00"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users "
            "(id, username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
            ("user-1", "owner", "argon2-test-hash", 1, timestamp),
        )
        connection.execute(
            "INSERT INTO operations "
            "(id, kind, status, actor_type, actor_id, idempotency_key, request_sha256, "
            "request_json, cancel_requested, event_sequence, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "operation-1",
                "hardware.scan",
                "succeeded",
                "session",
                "user-1",
                "historical-operation",
                "0" * 64,
                "{}",
                0,
                0,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO integration_connections "
            "(id, adapter, name, expected_product, base_url, approved_ips_json, "
            "allow_localhost, api_key_ciphertext, verify_tls, status, capabilities_json, "
            "state_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "integration-1",
                "servarr",
                "Sonarr",
                "sonarr",
                "https://sonarr.test",
                '["192.0.2.20"]',
                0,
                b"encrypted-test-value",
                1,
                "connected",
                "[]",
                "{}",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO audit_events "
            "(actor_type, actor_id, action, outcome, correlation_id, details_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("session", "user-1", "historical.test", "succeeded", "correlation-1", "{}", timestamp),
        )

    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    assert database_is_current(engine, database_url)
    assert {
        "plan_approvals",
        "connectivity_services",
        "storage_groups",
        "physical_disks",
        "storage_backends",
        "storage_lifecycle_events",
        "storage_drain_jobs",
        "storage_drain_entries",
        "topology_expectations",
        "topology_drift_events",
    } <= set(inspect(engine).get_table_names())
    assert "not_before" in {column["name"] for column in inspect(engine).get_columns("operations")}
    assert {"expectation_id", "fingerprint", "state", "resolved_at"} <= {
        column["name"] for column in inspect(engine).get_columns("topology_drift_events")
    }
    engine.dispose()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT username FROM users").fetchone() == ("owner",)
        assert connection.execute("SELECT status FROM operations").fetchone() == ("succeeded",)
        assert connection.execute("SELECT name FROM integration_connections").fetchone() == (
            "Sonarr",
        )
        assert connection.execute("SELECT action FROM audit_events").fetchone() == (
            "historical.test",
        )


def test_migrate_creates_first_key_but_refuses_silent_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "hoardarr.db"
    key = tmp_path / "secret.key"
    database_url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("HOARDARR_DATABASE_URL", database_url)
    monkeypatch.setenv("HOARDARR_SECRET_KEY_FILE", str(key))
    monkeypatch.setenv("HOARDARR_ENVIRONMENT", "production")

    # A failed/early API start may leave an empty SQLite file. That is not an
    # established encrypted installation and must remain recoverable.
    database.touch()
    migrate_main()
    assert database.is_file()
    assert key.is_file()
    engine = create_database_engine(database_url)
    assert database_is_current(engine, database_url)
    engine.dispose()

    key.unlink()
    with pytest.raises(SystemExit, match="replacement encryption key"):
        migrate_main()
    assert not key.exists()


def test_migrate_retry_keeps_key_created_before_schema_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "retry.db"
    key = tmp_path / "retry.key"
    database_url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("HOARDARR_DATABASE_URL", database_url)
    monkeypatch.setenv("HOARDARR_SECRET_KEY_FILE", str(key))
    monkeypatch.setenv("HOARDARR_ENVIRONMENT", "production")

    def failed_upgrade(_database_url: str) -> None:
        raise RuntimeError("simulated migration interruption")

    monkeypatch.setattr("hoardarr.cli.upgrade_database", failed_upgrade)
    with pytest.raises(RuntimeError, match="simulated"):
        migrate_main()
    first_key = key.read_bytes()
    assert first_key
    assert not database.exists()

    monkeypatch.undo()
    monkeypatch.setenv("HOARDARR_DATABASE_URL", database_url)
    monkeypatch.setenv("HOARDARR_SECRET_KEY_FILE", str(key))
    monkeypatch.setenv("HOARDARR_ENVIRONMENT", "production")
    migrate_main()
    assert key.read_bytes() == first_key
    engine = create_database_engine(database_url)
    assert database_is_current(engine, database_url)
    engine.dispose()


def test_interrupted_sqlite_ddl_rolls_back_and_retries_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "interrupted.db"
    key = tmp_path / "interrupted.key"
    database_url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("HOARDARR_DATABASE_URL", database_url)
    monkeypatch.setenv("HOARDARR_SECRET_KEY_FILE", str(key))
    monkeypatch.setenv("HOARDARR_ENVIRONMENT", "production")

    real_create_table = op.create_table
    calls = 0

    def interrupted_create_table(*args: object, **kwargs: object) -> object:
        nonlocal calls
        result = real_create_table(*args, **kwargs)
        calls += 1
        if calls == 3:
            raise RuntimeError("simulated mid-DDL interruption")
        return result

    with monkeypatch.context() as interruption:
        interruption.setattr(op, "create_table", interrupted_create_table)
        with pytest.raises(RuntimeError, match="mid-DDL interruption"):
            migrate_main()

    first_key = key.read_bytes()
    assert first_key
    engine = create_database_engine(database_url)
    assert inspect(engine).get_table_names() == []
    engine.dispose()

    migrate_main()
    assert key.read_bytes() == first_key
    engine = create_database_engine(database_url)
    assert database_is_current(engine, database_url)
    assert "users" in inspect(engine).get_table_names()
    engine.dispose()
