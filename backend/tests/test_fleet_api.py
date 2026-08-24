from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hoardarr.api.app import create_app
from hoardarr.auth.service import issue_setup_token
from hoardarr.core.config import Settings
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database


def _runtime(tmp_path: Path) -> tuple[TestClient, str]:
    database = tmp_path / "fleet.db"
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<!doctype html><title>Hoardarr</title>")
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{database.as_posix()}",
        secret_key_file=tmp_path / "secret.key",
        secure_cookies=False,
        frontend_dir=frontend,
        fleet_telemetry_endpoint="https://fleet.test/api/telemetry/v1",
    )
    upgrade_database(settings.database_url)
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        token = issue_setup_token(session)
    return TestClient(create_app(settings), base_url="http://testserver"), token


def _claim(client: TestClient, token: str) -> str:
    response = client.post(
        "/api/v1/setup/claim",
        headers={"Origin": "http://testserver"},
        json={
            "token": token,
            "username": "owner",
            "password": "a-long-unique-test-password",
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["csrf_token"])


def test_fleet_settings_require_auth_and_explain_required_heartbeat(tmp_path: Path) -> None:
    client, token = _runtime(tmp_path)
    with client:
        assert client.get("/api/v1/fleet-telemetry/settings").status_code == 401
        _claim(client, token)
        document = client.get("/api/v1/fleet-telemetry/settings").json()
        assert document["anonymous_heartbeat"] == {"required": True, "enabled": True}
        assert document["hardware_enabled"] is True
        assert document["enhanced_enabled"] is False
        assert document["content_enabled"] is False
        assert document["endpoint"].startswith("https://")


def test_privacy_hierarchy_location_queue_and_reset_are_enforced(tmp_path: Path) -> None:
    client, token = _runtime(tmp_path)
    with client:
        csrf = _claim(client, token)
        headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf}
        invalid = client.put(
            "/api/v1/fleet-telemetry/settings",
            headers=headers,
            json={
                "hardware_enabled": False,
                "enhanced_enabled": True,
                "content_enabled": False,
                "country_code": "US",
                "timezone": "America/New_York",
            },
        )
        assert invalid.status_code == 422
        saved = client.put(
            "/api/v1/fleet-telemetry/settings",
            headers=headers,
            json={
                "hardware_enabled": True,
                "enhanced_enabled": False,
                "content_enabled": False,
                "country_code": "CA",
                "timezone": "America/Toronto",
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["location_detection_method"] == "manual"
        assert saved.json()["location_confirmed"] is True
        queued = client.post("/api/v1/fleet-telemetry/send-now", headers=headers)
        assert queued.status_code == 200
        assert queued.json()["queued_records"] >= 1
        exact = client.get("/api/v1/fleet-telemetry/pending").json()
        assert exact["items"][0]["payload"]["installation_id"]
        old_identity = saved.json()["installation_id"]
        wrong = client.post(
            "/api/v1/fleet-telemetry/reset-identity",
            headers=headers,
            json={"confirmation": "RESET"},
        )
        assert wrong.status_code == 422
        reset = client.post(
            "/api/v1/fleet-telemetry/reset-identity",
            headers=headers,
            json={"confirmation": "RESET TELEMETRY IDENTITY"},
        )
        assert reset.status_code == 200
        assert reset.json()["installation_id"] != old_identity
        assert reset.json()["queued_records"] == 0
