from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import hoardarr.api.routes.auth as auth_routes
from hoardarr import __version__
from hoardarr.api.app import create_app
from hoardarr.auth.service import create_initial_owner, issue_setup_token
from hoardarr.core.config import Settings
from hoardarr.core.secrets import SecretBox
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.db.models import (
    AuditEvent,
    AuthSession,
    ConnectivityService,
    HardwareSnapshot,
    IntegrationConnection,
    Operation,
    StorageEntity,
    User,
)
from hoardarr.operations.service import document_hash
from hoardarr.operations.worker import run_once
from hoardarr.storage.redundancy import register_single_path_storage
from hoardarr.storage.tiering import plan_transfer
from hoardarr.wizard.service import DEFAULT_LAYOUT


@pytest.fixture
def api_runtime(tmp_path: Path):  # type: ignore[no-untyped-def]
    database_path = tmp_path / "api.db"
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{database_path.as_posix()}",
        secret_key_file=tmp_path / "secret.key",
        secure_cookies=False,
        hardware_detector=tmp_path / "detect-hardware.py",
        snapraid_config_root=tmp_path / "snapraid",
    )
    upgrade_database(settings.database_url)
    secret_box = SecretBox.from_file(settings.secret_key_file, create=True)
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        setup_token = issue_setup_token(session)
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        yield client, app, setup_token, secret_box


def _claim_owner(client: TestClient, setup_token: str) -> str:
    response = client.post(
        "/api/v1/setup/claim",
        headers={"Origin": "http://testserver"},
        json={
            "token": setup_token,
            "username": "owner",
            "password": "a-long-unique-test-password",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["user"]["username"] == "owner"
    assert "hoardarr_session" in client.cookies
    assert client.cookies.get("hoardarr_csrf") == response.json()["csrf_token"]
    return response.json()["csrf_token"]


def _state_headers(csrf: str, **extra: str) -> dict[str, str]:
    return {"Origin": "http://testserver", "X-CSRF-Token": csrf, **extra}


def test_authenticated_read_only_settings_requests_do_not_require_csrf_origin(
    api_runtime: Any,
) -> None:
    client, _app, setup_token, _secret_box = api_runtime
    _claim_owner(client, setup_token)
    client.headers.pop("Origin", None)
    assert client.get("/api/v1/updates/status").status_code == 200
    response = client.get("/api/v1/addons")
    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_storage_group_api_preserves_identity_and_guards_lifecycle(api_runtime: Any) -> None:
    client, _app, setup_token, _secret_box = api_runtime
    assert client.get("/api/v1/storage/groups").status_code == 401
    csrf = _claim_owner(client, setup_token)
    headers = _state_headers(csrf)

    reconciled = client.post(
        "/api/v1/storage/disks/reconcile",
        headers=headers,
        json={
            "items": [
                {
                    "stable_identity": "wwn:5000c500feed0001",
                    "kernel_path": "/dev/sdb",
                    "serial": "SANITIZED-0001",
                    "model": "Media Disk",
                    "capacity_bytes": 8_000_000_000_000,
                    "health_state": "healthy",
                }
            ]
        },
    )
    assert reconciled.status_code == 200, reconciled.text
    disk_id = reconciled.json()["items"][0]["id"]

    created = client.post(
        "/api/v1/storage/groups",
        headers=headers,
        json={
            "name": "Media",
            "namespace_path": "/srv/hoardarr/media",
            "purpose": "media",
        },
    )
    assert created.status_code == 201, created.text
    group_id = created.json()["item"]["id"]
    assigned = client.post(
        f"/api/v1/storage/groups/{group_id}/backends",
        headers=headers,
        json={"physical_disk_id": disk_id, "role": "data"},
    )
    assert assigned.status_code == 201, assigned.text
    backend_id = assigned.json()["item"]["backends"][0]["id"]

    for state in ("active", "preferred_write"):
        response = client.post(
            f"/api/v1/storage/groups/{group_id}/backends/{backend_id}/transition",
            headers=headers,
            json={"target_state": state},
        )
        assert response.status_code == 200, response.text
    guarded = client.post(
        f"/api/v1/storage/groups/{group_id}/backends/{backend_id}/transition",
        headers=headers,
        json={"target_state": "draining"},
    )
    assert guarded.status_code == 422
    assert guarded.json()["code"] == "durable_operation_required"

    document = client.get("/api/v1/storage/groups").json()["items"][0]
    assert document["namespace_path"] == "/srv/hoardarr/media"
    assert document["backends"][0]["stable_identity"] == "disk:wwn:5000c500feed0001"
    assert document["backends"][0]["lifecycle_state"] == "preferred_write"


def test_device_maintenance_preview_apply_and_worker_are_bound(api_runtime: Any) -> None:
    client, app, setup_token, secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    disk = {
        "id": "wwn:maintenance-test",
        "stable_identity": True,
        "system_device": False,
        "selectable": True,
        "kernel_path": "/dev/sdz",
        "vendor": "TEST",
        "model": "DISK",
        "identity": {"serial": "SERIAL", "wwn": "maintenance-test", "eui64": None, "nguid": None},
        "capacity_bytes": 1_000_000_000,
        "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
        "partitions": [],
        "maintenance_capabilities": {},
    }
    hardware = {
        "schema_version": 1,
        "source": {"kind": "sysfs"},
        "controllers": [],
        "disks": [disk],
    }
    with app.state.session_factory() as session, session.begin():
        scan = Operation(
            kind="hardware.scan",
            status="succeeded",
            actor_type="user",
            actor_id="00000000-0000-0000-0000-000000000001",
            request_sha256=document_hash({}),
            request_json={},
        )
        session.add(scan)
        session.flush()
        session.add(
            HardwareSnapshot(
                operation_id=scan.id,
                detector_schema_version=1,
                source="sysfs",
                payload_json=hardware,
                sha256=document_hash(hardware),
            )
        )
    preview = client.post(
        "/api/v1/storage/maintenance/preview",
        headers=_state_headers(csrf),
        json={"device_id": disk["id"], "action": "wipe", "method": "quick"},
    )
    assert preview.status_code == 200, preview.text
    body = {
        "plan": preview.json()["plan"],
        "plan_sha256": preview.json()["plan_sha256"],
        "confirmation": "I AGREE",
    }
    assert (
        client.post(
            "/api/v1/storage/maintenance",
            headers={"Origin": "http://testserver", "Idempotency-Key": "maintenance-no-csrf"},
            json=body,
        ).status_code
        == 403
    )
    headers = _state_headers(csrf, **{"Idempotency-Key": "maintenance-test-one"})
    accepted = client.post("/api/v1/storage/maintenance", headers=headers, json=body)
    replay = client.post("/api/v1/storage/maintenance", headers=headers, json=body)
    assert accepted.status_code == replay.status_code == 202
    assert replay.json()["replayed"] is True
    operation_id = accepted.json()["operation"]["id"]

    def maintenance_applier(_socket: object, **values: object) -> dict[str, object]:
        assert values["plan_sha256"] == body["plan_sha256"]
        assert values["confirmation_sha256"] == document_hash({"confirmation": "I AGREE"})
        return {
            "operation_id": operation_id,
            "action": "wipe",
            "device_id": disk["id"],
            "completed_actions": ["maintenance:1"],
        }

    assert run_once(
        session_factory=app.state.session_factory,
        settings=app.state.settings,
        secret_box=secret_box,
        maintenance_applier=maintenance_applier,
    )
    completed = client.get(f"/api/v1/operations/{operation_id}")
    assert completed.json()["status"] == "succeeded"


def test_tier_transfer_preview_apply_is_authenticated_and_idempotent(
    api_runtime: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _app, setup_token, _secret_box = api_runtime
    request = {
        "workload": "torrent",
        "source": "/data/downloads/release.mkv",
        "destination": "/data/media/Movies/release.mkv",
        "method": "copy",
    }
    assert client.post("/api/v1/storage/transfers/preview", json=request).status_code == 401
    csrf = _claim_owner(client, setup_token)
    plan = plan_transfer(
        {
            **request,
            "source_identity": "dev:11",
            "destination_identity": "dev:22",
            "same_filesystem": False,
            "required_bytes": 4096,
        }
    ).document()
    monkeypatch.setattr("hoardarr.api.routes.storage._transfer_plan", lambda _payload: plan)
    preview = client.post("/api/v1/storage/transfers/preview", json=request)
    assert preview.status_code == 200, preview.text
    body = {
        "plan": preview.json()["plan"],
        "plan_sha256": preview.json()["plan_sha256"],
        "confirmation": "APPLY",
    }
    headers = _state_headers(csrf, **{"Idempotency-Key": "transfer-apply-one"})
    first = client.post("/api/v1/storage/transfers", headers=headers, json=body)
    second = client.post("/api/v1/storage/transfers", headers=headers, json=body)
    assert first.status_code == second.status_code == 202
    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True
    changed = client.post(
        "/api/v1/storage/transfers",
        headers=headers,
        json={**body, "plan": {**plan, "required_bytes": 1}},
    )
    assert changed.status_code == 409
    with _app.state.session_factory() as session, session.begin():
        retained = session.get(Operation, first.json()["operation"]["id"])
        assert retained is not None
        retained.status = "succeeded"
        retained.result_json = {"state": "retained", "source": plan["source"]}
    cleanup_headers = _state_headers(csrf, **{"Idempotency-Key": "transfer-cleanup-one"})
    cleanup = client.post(
        f"/api/v1/storage/transfers/{first.json()['operation']['id']}/cleanup",
        headers=cleanup_headers,
        json={"confirmation": "APPLY"},
    )
    assert cleanup.status_code == 202, cleanup.text
    assert cleanup.json()["operation"]["kind"] == "storage.transfer.cleanup"


def test_snapraid_replacement_is_immutable_reserved_and_executed(api_runtime: Any) -> None:
    client, app, setup_token, secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    disk = {
        "id": "wwn:snapraid-replacement",
        "stable_identity": True,
        "system_device": False,
        "selectable": True,
        "read_only": False,
        "kernel_path": "/dev/sdz",
        "vendor": "TEST",
        "model": "DISK",
        "identity": {
            "serial": "REPLACEMENT",
            "wwn": "snapraid-replacement",
            "eui64": None,
            "nguid": None,
        },
        "capacity_bytes": 1_000_000_000,
        "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
        "partitions": [],
    }
    hardware = {"schema_version": 1, "source": {"kind": "test"}, "disks": [disk]}
    app.state.settings.snapraid_config_root.mkdir()
    (app.state.settings.snapraid_config_root / "media.conf").write_text(
        "parity /mnt/parity/snapraid.parity\ndata d1 /mnt/old\n",
        encoding="utf-8",
    )
    with app.state.session_factory() as session, session.begin():
        scan = Operation(
            kind="hardware.scan",
            status="succeeded",
            actor_type="user",
            actor_id="00000000-0000-0000-0000-000000000001",
            request_sha256=document_hash({}),
            request_json={},
        )
        session.add(scan)
        session.flush()
        session.add(
            HardwareSnapshot(
                operation_id=scan.id,
                detector_schema_version=1,
                source="test",
                payload_json=hardware,
                sha256=document_hash(hardware),
            )
        )
    preview = client.post(
        "/api/v1/storage/snapraid/replacements/preview",
        headers=_state_headers(csrf),
        json={
            "pool_name": "media",
            "data_name": "d1",
            "replacement_device_id": disk["id"],
            "filesystem": "ext4",
        },
    )
    assert preview.status_code == 200, preview.text
    body = {
        "plan": preview.json()["plan"],
        "plan_sha256": preview.json()["plan_sha256"],
        "confirmation": "I AGREE",
    }
    headers = _state_headers(csrf, **{"Idempotency-Key": "snapraid-replace-one"})
    accepted = client.post("/api/v1/storage/snapraid/replacements", headers=headers, json=body)
    assert accepted.status_code == 202, accepted.text
    assert (
        client.post(
            "/api/v1/storage/snapraid/replacements",
            headers=headers,
            json={**body, "plan_sha256": "0" * 64},
        ).status_code
        == 409
    )
    operation_id = accepted.json()["operation"]["id"]

    def applier(_socket: object, **values: object) -> dict[str, object]:
        assert values["plan_sha256"] == body["plan_sha256"]
        return {"operation_id": operation_id, "parity_state": "current"}

    assert run_once(
        session_factory=app.state.session_factory,
        settings=app.state.settings,
        secret_box=secret_box,
        snapraid_replacement_applier=applier,
    )
    assert client.get(f"/api/v1/operations/{operation_id}").json()["status"] == "succeeded"


def test_servarr_preview_apply_runs_product_adapter_without_secret_leak(api_runtime: Any) -> None:
    client, app, setup_token, secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    connection_id = "radarr-write-test"
    api_key = "radarr-key-never-persist-in-operations"
    with app.state.session_factory() as session, session.begin():
        session.add(
            IntegrationConnection(
                id=connection_id,
                name="Radarr",
                expected_product="radarr",
                base_url="http://127.0.0.1:7878",
                approved_ips_json=["127.0.0.1"],
                allow_localhost=True,
                api_key_ciphertext=secret_box.encrypt(
                    "integration_connection", connection_id, api_key
                ),
                verify_tls=False,
                status="connected",
                discovered_product="radarr",
                product_version="5.0.0",
            )
        )
    proposed = {"root_folders": [{"path": "/data/media/Movies"}]}
    preview = client.post(
        f"/api/v1/integrations/{connection_id}/changes/preview",
        headers=_state_headers(csrf),
        json=proposed,
    )
    assert preview.status_code == 200, preview.text
    body = {
        "plan": preview.json()["plan"],
        "plan_sha256": preview.json()["plan_sha256"],
        "confirmation": "APPLY",
    }
    applied = client.post(
        f"/api/v1/integrations/{connection_id}/changes/apply",
        headers=_state_headers(csrf, **{"Idempotency-Key": "radarr-write-one"}),
        json=body,
    )
    assert applied.status_code == 202, applied.text

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Api-Key"] == api_key
        if request.method == "GET" and request.url.path.endswith("/rootfolder"):
            return httpx.Response(200, json=[])
        if request.method == "POST" and request.url.path.endswith("/rootfolder"):
            return httpx.Response(201, json={"id": 9, **json.loads(request.content)})
        return httpx.Response(404)

    assert run_once(
        session_factory=app.state.session_factory,
        settings=app.state.settings,
        secret_box=secret_box,
        worker_id="servarr-write-worker",
        servarr_transport=httpx.MockTransport(handler),
    )
    with app.state.session_factory() as session:
        operation = session.get(Operation, applied.json()["operation"]["id"])
        connection = session.get(IntegrationConnection, connection_id)
        assert operation is not None and operation.status == "succeeded"
        assert (
            connection is not None and connection.state_json["last_apply"]["state"] == "completed"
        )
        assert api_key not in json.dumps(operation.request_json)
        assert api_key not in json.dumps(operation.result_json)


def test_openapi_contract_has_versioned_product_groups(api_runtime: Any) -> None:
    client, _app, _setup_token, _secret_box = api_runtime
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    document = response.json()
    assert document["info"]["version"] == __version__
    paths = set(document["paths"])
    for group in (
        "setup",
        "auth",
        "system",
        "onboarding",
        "hardware",
        "storage",
        "wizards",
        "operations",
        "connectivity",
        "networking",
        "accounts",
        "integrations",
    ):
        assert any(path.startswith(f"/api/v1/{group}") for path in paths), group


def test_problem_response_carries_matching_correlation_id(api_runtime: Any) -> None:
    client, _app, _setup_token, _secret_box = api_runtime
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["x-request-id"] == response.json()["request_id"]
    assert response.json()["instance"] == "/api/v1/auth/me"


def test_api_rejects_late_cancellation_of_host_mutation(api_runtime: Any) -> None:
    client, app, setup_token, _secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    with app.state.session_factory() as session, session.begin():
        owner = session.scalar(select(User).where(User.username == "owner"))
        assert owner is not None
        operation = Operation(
            kind="storage.apply",
            status="running",
            actor_type="session",
            actor_id=owner.id,
            idempotency_key="late-cancel-test",
            request_sha256="0" * 64,
            request_json={},
            lease_owner="worker-one",
        )
        session.add(operation)
        session.flush()
        operation_id = operation.id

    response = client.post(
        f"/api/v1/operations/{operation_id}/cancel",
        headers=_state_headers(csrf),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "operation_not_cancellable"
    with app.state.session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        assert operation.status == "running"
        assert operation.cancel_requested is False


def test_connectivity_service_create_apply_and_remove(api_runtime: Any) -> None:
    client, app, setup_token, secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    created = client.post(
        "/api/v1/connectivity",
        headers=_state_headers(csrf, **{"Idempotency-Key": "connectivity-create"}),
        json={
            "protocol": "smb",
            "name": "media",
            "path": "/data/media",
            "read_only": False,
            "browseable": True,
            "valid_users": ["media"],
            "write_users": ["media"],
            "read_users": [],
        },
    )
    assert created.status_code == 202, created.text
    service_id = created.json()["service"]["id"]
    operation_id = created.json()["operation"]["id"]
    calls: list[dict[str, Any]] = []

    def apply_connectivity(_socket: object, **values: Any) -> dict[str, Any]:
        calls.append(values)
        return {
            "operation_id": values["operation_id"],
            "service_id": values["service_id"],
            "protocol": "smb",
            "state": "active",
        }

    assert run_once(
        session_factory=app.state.session_factory,
        settings=app.state.settings,
        secret_box=secret_box,
        connectivity_applier=apply_connectivity,
    )
    operation = client.get(f"/api/v1/operations/{operation_id}")
    assert operation.status_code == 200
    assert operation.json()["status"] == "succeeded"
    listed = client.get("/api/v1/connectivity")
    assert listed.json()["items"][0]["status"] == "active"
    assert calls[0]["config"]["path"] == "/data/media"
    assert calls[0]["config"]["write_users"] == ["media"]

    removed = client.request(
        "DELETE",
        f"/api/v1/connectivity/{service_id}",
        headers=_state_headers(csrf, **{"Idempotency-Key": "connectivity-remove"}),
        json={"confirmation": "I AGREE", "delete_backing_data": False},
    )
    assert removed.status_code == 202, removed.text

    def remove_connectivity(_socket: object, **values: Any) -> dict[str, Any]:
        return {
            "operation_id": values["operation_id"],
            "service_id": values["service_id"],
            "protocol": "smb",
            "state": "removed",
        }

    assert run_once(
        session_factory=app.state.session_factory,
        settings=app.state.settings,
        secret_box=secret_box,
        connectivity_remover=remove_connectivity,
    )
    with app.state.session_factory() as session:
        assert session.get(ConnectivityService, service_id) is None


def test_setup_accepts_a_one_character_password(api_runtime: Any) -> None:
    client, _app, setup_token, _secret_box = api_runtime
    response = client.post(
        "/api/v1/setup/claim",
        headers={"Origin": "http://testserver"},
        json={"token": setup_token, "username": "owner", "password": "x"},
    )

    assert response.status_code == 201, response.text
    client.cookies.clear()
    login = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "http://testserver"},
        json={"username": "owner", "password": "x"},
    )
    assert login.status_code == 200, login.text


def test_login_session_is_durable_before_browser_cookie_is_published(
    api_runtime: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, app, setup_token, _secret_box = api_runtime
    _claim_owner(client, setup_token)
    client.cookies.clear()
    original_set_cookie = auth_routes._set_session_cookie
    observed_session_counts: list[int] = []

    def assert_durable_session(*args: Any, **kwargs: Any) -> None:
        with app.state.session_factory() as observer:
            observed_session_counts.append(len(observer.scalars(select(AuthSession)).all()))
        original_set_cookie(*args, **kwargs)

    monkeypatch.setattr(auth_routes, "_set_session_cookie", assert_durable_session)
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "http://testserver"},
        json={"username": "owner", "password": "a-long-unique-test-password"},
    )

    assert response.status_code == 200, response.text
    assert observed_session_counts == [2]
    assert client.get("/api/v1/auth/me").status_code == 200


def test_trusted_local_setup_can_create_owner_without_a_site_code(api_runtime: Any) -> None:
    client, app, _setup_token, _secret_box = api_runtime
    with app.state.session_factory() as session, session.begin():
        owner = create_initial_owner(session, username="admin", password="x")
        assert owner.username == "admin"

    status = client.get("/api/v1/setup/status")
    assert status.status_code == 200
    assert status.json()["configured"] is True
    login = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "http://testserver"},
        json={"username": "admin", "password": "x"},
    )
    assert login.status_code == 200, login.text


def test_media_account_can_use_provided_or_generated_password(
    api_runtime: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, app, setup_token, _secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    calls: list[dict[str, object]] = []

    def provision(_socket_path: Path, **values: object) -> dict[str, object]:
        calls.append(values)
        return {
            "username": values["username"],
            "created": len(calls) == 1,
            "password_updated": True,
            "smb_enabled": True,
            "shell_login": False,
        }

    monkeypatch.setattr("hoardarr.api.routes.accounts.provision_media_account", provision)
    provided = client.post(
        "/api/v1/accounts/media",
        headers=_state_headers(csrf),
        json={"username": "media", "credential_mode": "provide", "password": "x"},
    )
    assert provided.status_code == 201, provided.text
    assert provided.json()["credential"] == {
        "generated": False,
        "password": None,
        "display_once": False,
    }
    assert calls[0]["password"] == "x"

    generated = client.post(
        "/api/v1/accounts/media",
        headers=_state_headers(csrf),
        json={"username": "media", "credential_mode": "generate"},
    )
    assert generated.status_code == 201, generated.text
    generated_password = generated.json()["credential"]["password"]
    assert isinstance(generated_password, str) and len(generated_password) >= 24
    assert calls[1]["password"] == generated_password

    with app.state.session_factory() as session:
        audits = list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.action == "media_account.provision")
            )
        )
    assert len(audits) == 2
    audit_payload = json.dumps([audit.details_json for audit in audits])
    assert generated_password not in audit_payload
    assert '"x"' not in audit_payload


def test_media_account_rejects_missing_or_line_based_password(api_runtime: Any) -> None:
    client, _app, setup_token, _secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    for password in (None, "line one\nline two"):
        payload = {"username": "media", "credential_mode": "provide"}
        if password is not None:
            payload["password"] = password
        response = client.post(
            "/api/v1/accounts/media",
            headers=_state_headers(csrf),
            json=payload,
        )
        assert response.status_code == 422, response.text


def test_remember_me_controls_cookie_and_server_session_lifetime(api_runtime: Any) -> None:
    client, _app, setup_token, _secret_box = api_runtime
    _claim_owner(client, setup_token)
    client.cookies.clear()

    session_only = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "http://testserver"},
        json={"username": "owner", "password": "a-long-unique-test-password"},
    )
    assert session_only.status_code == 200, session_only.text
    assert "max-age" not in session_only.headers["set-cookie"].lower()

    client.cookies.clear()
    remembered = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "http://testserver"},
        json={
            "username": "owner",
            "password": "a-long-unique-test-password",
            "remember_me": True,
        },
    )
    assert remembered.status_code == 200, remembered.text
    assert "Max-Age=2592000" in remembered.headers["set-cookie"]


def test_existing_session_restores_csrf_after_page_refresh(api_runtime: Any) -> None:
    client, _app, setup_token, _secret_box = api_runtime
    original_csrf = _claim_owner(client, setup_token)
    client.cookies.delete("hoardarr_csrf")

    restored = client.get("/api/v1/auth/me")

    assert restored.status_code == 200, restored.text
    restored_csrf = restored.json()["csrf_token"]
    assert restored_csrf.startswith("hc_")
    assert restored_csrf != original_csrf
    assert client.cookies.get("hoardarr_csrf") == restored_csrf
    assert restored.headers["cache-control"] == "no-store"

    stale_logout = client.post(
        "/api/v1/auth/logout",
        headers=_state_headers(original_csrf),
    )
    assert stale_logout.status_code == 403

    logout = client.post(
        "/api/v1/auth/logout",
        headers=_state_headers(restored_csrf),
    )
    assert logout.status_code == 204
    assert "hoardarr_session" not in client.cookies
    assert "hoardarr_csrf" not in client.cookies


def test_authenticated_hardware_worker_and_wizard_flow(
    api_runtime: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, app, setup_token, secret_box = api_runtime
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    csrf = _claim_owner(client, setup_token)

    overview = client.get("/api/v1/system/overview")
    assert overview.status_code == 200, overview.text
    overview_document = overview.json()
    assert overview_document["source"] == "live"
    assert overview_document["system"]["hostname"]
    assert overview_document["system"]["memory"]["total_bytes"] > 0
    assert overview_document["storage"]["snapshot"] is None
    assert overview_document["storage"]["drive_count"] is None
    assert overview_document["storage"]["pools"] == {
        "status": "not_configured",
        "items": [],
    }

    resources = client.get("/api/v1/system/resources")
    assert resources.status_code == 200, resources.text
    resource_document = resources.json()
    assert resource_document["source"] == "live"
    assert 0 <= resource_document["cpu"]["used_percent"] <= 100
    assert resource_document["memory"]["total_bytes"] > 0
    assert 0 <= resource_document["memory"]["used_percent"] <= 100
    assert isinstance(resource_document["network"]["interfaces"], list)
    for interface in resource_document["network"]["interfaces"]:
        assert set(interface) == {"name", "up", "bytes_received", "bytes_sent"}
    system_volume = resource_document["storage"]["system_volume"]
    if system_volume is not None:
        assert system_volume["total_bytes"] > 0
        assert 0 <= system_volume["used_percent"] <= 100

    setup_retries = [
        client.post(
            "/api/v1/setup/claim",
            headers={"Origin": "http://testserver"},
            json={
                "token": "hsetup_this-is-not-the-valid-claim-token",
                "username": "owner2",
                "password": "another-long-test-password",
            },
        ).status_code
        for _index in range(6)
    ]
    assert setup_retries[:4] == [409, 409, 409, 409]
    assert setup_retries[4:] == [429, 429]
    with app.state.session_factory() as session:
        assert (
            session.scalar(
                select(AuditEvent).where(
                    AuditEvent.action == "setup.claim",
                    AuditEvent.outcome == "rejected",
                )
            )
            is None
        )

    failed_login = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "http://testserver"},
        json={"username": "owner", "password": "this-password-is-wrong"},
    )
    assert failed_login.status_code == 401
    with app.state.session_factory() as session:
        rejected_audits = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "auth.login",
                    AuditEvent.outcome == "rejected",
                )
            )
        )
        assert len(rejected_audits) == 1

    assert client.get("/api/v1/auth/me").status_code == 200
    mergerfs = client.get("/api/v1/storage/mergerfs")
    assert mergerfs.status_code == 200, mergerfs.text
    assert mergerfs.json()["status"] in {
        "configured",
        "available_not_configured",
        "unavailable",
    }
    assert isinstance(mergerfs.json()["items"], list)
    rejected = client.post(
        "/api/v1/hardware/scans",
        headers={"Idempotency-Key": "hardware-test-0001"},
    )
    assert rejected.status_code == 403
    assert rejected.json()["code"] == "origin_required"

    scan = client.post(
        "/api/v1/hardware/scans",
        headers=_state_headers(csrf, **{"Idempotency-Key": "hardware-test-0001"}),
    )
    assert scan.status_code == 202, scan.text
    operation_id = scan.json()["operation"]["id"]
    replay = client.post(
        "/api/v1/hardware/scans",
        headers=_state_headers(csrf, **{"Idempotency-Key": "hardware-test-0001"}),
    )
    assert replay.status_code == 202
    assert replay.json()["replayed"] is True
    assert replay.json()["operation"]["id"] == operation_id

    payload = {
        "schema_version": 1,
        "source": {"kind": "sysfs"},
        "platform": {"manufacturer": "Oracle", "product": "storage-host"},
        "controllers": [],
        "disks": [
            {
                "id": "serial:cisco:ssd-240g:stp26501raw",
                "stable_identity": True,
                "kernel_name": "sdb",
                "kernel_path": "/dev/sdb",
                "identity": {
                    "serial": "STP26501RAW",
                    "wwn": None,
                    "eui64": None,
                    "nguid": None,
                },
                "vendor": "CISCO",
                "model": "SSD-240G V01",
                "capacity_bytes": 240_057_409_536,
                "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
                "read_only": False,
                "connection": {"transport": "usb", "protocol": "uas"},
                "partitions": [],
                "signatures": [],
                "maintenance_capabilities": {
                    "ata_secure_erase": False,
                    "nvme_block_erase": False,
                    "sector_format_passthrough": False,
                    "supported_logical_sector_bytes": [],
                    "source": "Not reported",
                },
            }
        ],
    }

    def detector(*_args: object, **_kwargs: object) -> tuple[dict[str, Any], str]:
        return payload, document_hash(payload)

    assert run_once(
        session_factory=app.state.session_factory,
        settings=app.state.settings,
        secret_box=secret_box,
        worker_id="api-test-worker",
        detector_runner=detector,
    )
    completed = client.get(f"/api/v1/operations/{operation_id}")
    assert completed.json()["status"] == "succeeded"
    snapshot_id = completed.json()["result"]["snapshot_id"]
    snapshot = client.get(f"/api/v1/hardware/snapshots/{snapshot_id}")
    assert snapshot.json()["hardware"] == payload
    latest_snapshot = client.get("/api/v1/hardware/snapshots/latest")
    assert latest_snapshot.status_code == 200
    assert latest_snapshot.json()["id"] == snapshot_id
    assert latest_snapshot.json()["hardware"] == payload

    wizard = client.post(
        "/api/v1/wizards",
        headers=_state_headers(csrf),
        json={"mode": "simple", "hardware_snapshot_id": snapshot_id},
    )
    assert wizard.status_code == 201, wizard.text
    wizard_id = wizard.json()["id"]
    assert wizard.headers["etag"].endswith('revision-0"')
    storage = client.put(
        f"/api/v1/wizards/{wizard_id}/steps/storage",
        headers=_state_headers(csrf),
        json={
            "revision": 0,
            "answers": {
                "selected_device_ids": ["serial:cisco:ssd-240g:stp26501raw"],
                "purpose": "media",
                "preserve_data": False,
                "portable_systems": ["windows"],
                "snapshots": False,
                "encryption": "none",
            },
        },
    )
    assert storage.status_code == 200, storage.text
    layout = client.put(
        f"/api/v1/wizards/{wizard_id}/steps/layout",
        headers=_state_headers(csrf),
        json={"revision": 1, "answers": DEFAULT_LAYOUT},
    )
    assert layout.status_code == 200, layout.text
    applications = client.put(
        f"/api/v1/wizards/{wizard_id}/steps/applications",
        headers=_state_headers(csrf),
        json={"revision": 2, "answers": {}},
    )
    assert applications.status_code == 200, applications.text
    plan = client.post(
        f"/api/v1/wizards/{wizard_id}/plan",
        headers=_state_headers(csrf),
        json={"revision": 3},
    )
    assert plan.status_code == 201, plan.text
    document = plan.json()["plan"]["document"]
    assert document["layout"] == DEFAULT_LAYOUT
    assert document["apply_available"] is True
    assert document["blockers"] == []
    consent_required = client.post(
        f"/api/v1/wizards/{wizard_id}/apply",
        headers=_state_headers(csrf, **{"Idempotency-Key": "storage-apply-consent"}),
    )
    assert consent_required.status_code == 409
    assert consent_required.json()["code"] == "destructive_consent_required"

    approval = client.post(
        f"/api/v1/wizards/{wizard_id}/plan/approve",
        headers=_state_headers(csrf),
        json={
            "revision": 3,
            "plan_sha256": plan.json()["plan"]["sha256"],
            "hardware_snapshot_sha256": document["storage"]["snapshot_binding"]["snapshot_sha256"],
            "selected_device_ids": document["storage"]["snapshot_binding"]["selected_device_ids"],
            "confirmation": "I AGREE",
        },
    )
    assert approval.status_code == 201, approval.text
    assert approval.json()["status"]["valid"] is True
    blocked = client.post(
        f"/api/v1/wizards/{wizard_id}/apply",
        headers=_state_headers(csrf, **{"Idempotency-Key": "storage-apply-0001"}),
    )
    assert blocked.status_code == 202
    assert blocked.json()["operation"]["kind"] == "storage.apply"
    storage_operation_id = blocked.json()["operation"]["id"]
    monkeypatch.setattr(
        "hoardarr.api.routes.operations.storage_operation_status",
        lambda *_args, **_kwargs: {
            "operation_id": storage_operation_id,
            "state": "running",
            "phase": "Checking and preparing drives",
            "completed_steps": 1,
            "total_steps": 5,
            "percent": 20,
            "completed_actions": ["identity"],
            "current_action": {"id": "format", "type": "filesystem.create"},
            "updated_at": 1.0,
        },
    )
    progress = client.get(f"/api/v1/operations/{storage_operation_id}/progress")
    assert progress.status_code == 200
    assert progress.json()["percent"] == 20

    def storage_applier(
        _socket_path: object,
        *,
        operation_id: str,
        plan_sha256: str,
        document: dict[str, Any],
        approval: dict[str, Any] | None,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        assert operation_id == storage_operation_id
        assert plan_sha256 == plan.json()["plan"]["sha256"]
        assert document["apply_available"] is True
        assert approval is not None
        assert approval["confirmation_phrase"] == "I AGREE"
        assert timeout_seconds >= 60
        return {
            "operation_id": operation_id,
            "topology": "individual",
            "selected_device_ids": ["serial:cisco:ssd-240g:stp26501raw"],
            "mountpoint": "/data",
            "completed_actions": [],
            "replayed": False,
        }

    assert run_once(
        session_factory=app.state.session_factory,
        settings=app.state.settings,
        secret_box=secret_box,
        worker_id="storage-api-test-worker",
        storage_applier=storage_applier,
    )
    applied = client.get(f"/api/v1/operations/{storage_operation_id}")
    assert applied.json()["status"] == "succeeded"
    assert client.get(f"/api/v1/wizards/{wizard_id}").json()["status"] == "applied"
    completed_wizard = client.post(
        f"/api/v1/wizards/{wizard_id}/complete", headers=_state_headers(csrf)
    )
    assert completed_wizard.status_code == 200, completed_wizard.text
    assert completed_wizard.json()["status"] == "completed"
    replayed_completion = client.post(
        f"/api/v1/wizards/{wizard_id}/complete", headers=_state_headers(csrf)
    )
    assert replayed_completion.status_code == 200
    assert replayed_completion.json()["status"] == "completed"
    with app.state.session_factory() as session:
        assert session.scalar(select(AuditEvent).where(AuditEvent.action == "wizard.plan.approve"))
        assert session.scalar(select(AuditEvent).where(AuditEvent.action == "wizard.complete"))


def test_storage_step_api_rejects_read_only_device(api_runtime: Any) -> None:
    client, app, setup_token, _secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    hardware = {
        "schema_version": 1,
        "source": {"kind": "sysfs"},
        "controllers": [],
        "disks": [
            {
                "id": "serial:readonly:test-drive",
                "stable_identity": True,
                "kernel_name": "sdc",
                "kernel_path": "/dev/sdc",
                "identity": {
                    "serial": "READONLY",
                    "wwn": None,
                    "eui64": None,
                    "nguid": None,
                },
                "vendor": "TEST",
                "model": "READ ONLY",
                "capacity_bytes": 1_000_000_000,
                "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
                "read_only": True,
                "connection": {"transport": "sas", "protocol": "sas"},
                "partitions": [],
                "signature_scan": {
                    "status": "complete",
                    "reason": "Test scan completed.",
                    "source": "test",
                },
                "signatures": [],
            }
        ],
    }
    with app.state.session_factory() as session, session.begin():
        operation = Operation(
            kind="hardware.scan",
            status="succeeded",
            actor_type="user",
            actor_id="00000000-0000-0000-0000-000000000001",
            request_sha256=document_hash({}),
            request_json={},
        )
        session.add(operation)
        session.flush()
        snapshot = HardwareSnapshot(
            operation_id=operation.id,
            detector_schema_version=1,
            source="sysfs",
            payload_json=hardware,
            sha256=document_hash(hardware),
            captured_at=datetime.now(UTC),
        )
        session.add(snapshot)
        session.flush()
        snapshot_id = snapshot.id

    wizard = client.post(
        "/api/v1/wizards",
        headers=_state_headers(csrf),
        json={"mode": "guided", "hardware_snapshot_id": snapshot_id},
    )
    assert wizard.status_code == 201, wizard.text
    rejected = client.put(
        f"/api/v1/wizards/{wizard.json()['id']}/steps/storage",
        headers=_state_headers(csrf),
        json={
            "revision": 0,
            "answers": {
                "selected_device_ids": ["serial:readonly:test-drive"],
                "topology": "individual",
                "purpose": "media",
                "preserve_data": True,
                "portable_systems": ["windows"],
                "snapshots": False,
                "encryption": "none",
            },
        },
    )

    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["code"] == "wizard_validation_failed"
    assert rejected.json()["errors"] == [
        {
            "field": "storage.selected_device_ids[0]",
            "message": (
                "drive is read-only; this workflow cannot guarantee a no-write import/share, so "
                "the device cannot be selected"
            ),
        }
    ]


def test_servarr_secret_is_encrypted_and_pat_scopes_are_enforced(api_runtime: Any) -> None:
    client, app, setup_token, secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    api_key = "servarr-api-key-that-must-never-leak"
    created = client.post(
        "/api/v1/integrations",
        headers=_state_headers(csrf, **{"Idempotency-Key": "integration-test-0001"}),
        json={
            "name": "Sonarr",
            "product": "sonarr",
            "base_url": "http://127.0.0.1:8989/sonarr",
            "api_key": api_key,
            "verify_tls": True,
            "allow_localhost": True,
        },
    )
    assert created.status_code == 202, created.text
    assert api_key not in created.text
    connection_id = created.json()["integration"]["id"]
    operation_id = created.json()["operation"]["id"]

    def discoverer(**kwargs: object) -> dict[str, Any]:
        assert kwargs["api_key"] == api_key
        return {
            "product": "sonarr",
            "version": "4.0.0",
            "api_prefix": "/api/v3",
            "support_level": "supported",
            "capabilities": ["root_folders", "remote_path_mappings"],
            "state": {
                "status": {"app_name": "Sonarr", "version": "4.0.0"},
                "root_folders": [],
                "remote_path_mappings": [],
            },
        }

    assert run_once(
        session_factory=app.state.session_factory,
        settings=app.state.settings,
        secret_box=secret_box,
        worker_id="api-test-worker",
        servarr_discoverer=discoverer,
    )
    assert client.get(f"/api/v1/operations/{operation_id}").json()["status"] == "succeeded"
    connection_response = client.get(f"/api/v1/integrations/{connection_id}")
    assert connection_response.json()["status"] == "connected"
    assert api_key not in connection_response.text

    refresh = client.post(
        f"/api/v1/integrations/{connection_id}/refresh",
        headers=_state_headers(csrf, **{"Idempotency-Key": "integration-refresh-0001"}),
    )
    assert refresh.status_code == 202, refresh.text
    refresh_operation_id = refresh.json()["operation"]["id"]
    # Refresh work is represented by its operation; it must not hide the last
    # known connected state while queued.
    assert client.get(f"/api/v1/integrations/{connection_id}").json()["status"] == "connected"
    cancelled_refresh = client.post(
        f"/api/v1/operations/{refresh_operation_id}/cancel",
        headers=_state_headers(csrf),
    )
    assert cancelled_refresh.json()["status"] == "cancelled"
    assert client.get(f"/api/v1/integrations/{connection_id}").json()["status"] == "connected"

    cancelled_create = client.post(
        "/api/v1/integrations",
        headers=_state_headers(csrf, **{"Idempotency-Key": "integration-cancel-0001"}),
        json={
            "name": "Radarr cancelled during setup",
            "product": "radarr",
            "base_url": "http://127.0.0.1:7878",
            "api_key": "another-secret-api-key",
            "allow_localhost": True,
        },
    )
    assert cancelled_create.status_code == 202
    cancelled_connection_id = cancelled_create.json()["integration"]["id"]
    cancelled_operation_id = cancelled_create.json()["operation"]["id"]
    cancellation = client.post(
        f"/api/v1/operations/{cancelled_operation_id}/cancel",
        headers=_state_headers(csrf),
    )
    assert cancellation.json()["status"] == "cancelled"
    cancelled_connection = client.get(f"/api/v1/integrations/{cancelled_connection_id}").json()
    assert cancelled_connection["status"] == "cancelled"
    assert cancelled_connection["state"]["last_error"]["code"] == "operation_cancelled"

    with app.state.session_factory() as session:
        connection = session.get(IntegrationConnection, connection_id)
        operation = session.get(Operation, operation_id)
        assert connection is not None and bytes(connection.api_key_ciphertext) != api_key.encode()
        assert operation is not None
        assert api_key not in json.dumps(operation.request_json)
        assert api_key not in json.dumps(connection.state_json)

    token_response = client.post(
        "/api/v1/auth/tokens",
        headers=_state_headers(csrf),
        json={
            "name": "read-only integration",
            "scopes": ["read"],
            "expires_at": "2099-01-01T12:00:00+05:00",
        },
    )
    assert token_response.status_code == 201, token_response.text
    pat = token_response.json()["secret"]
    assert pat.startswith("hak_")
    assert not pat.startswith("hsetup_")
    normalized_expiry = datetime.fromisoformat(
        token_response.json()["token"]["expires_at"].replace("Z", "+00:00")
    )
    assert normalized_expiry.utcoffset().total_seconds() == 0
    assert normalized_expiry.hour == 7
    pat_headers = {"Authorization": f"Bearer {pat}"}
    assert client.get("/api/v1/integrations", headers=pat_headers).status_code == 200
    forbidden = client.post(
        "/api/v1/hardware/scans",
        headers={**pat_headers, "Idempotency-Key": "pat-hardware-0001"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "insufficient_scope"

    with app.state.session_factory() as session:
        assert list(session.scalars(select(Operation)))


def test_request_cap_and_unexpected_errors_keep_safe_headers(api_runtime: Any) -> None:
    client, app, _setup_token, _secret_box = api_runtime
    maximum = app.state.settings.max_request_body_bytes
    oversized = client.post(
        "/api/v1/auth/login",
        headers={"Content-Type": "application/json"},
        content=b"x" * (maximum + 1),
    )
    assert oversized.status_code == 413
    assert oversized.json()["code"] == "request_too_large"
    assert oversized.headers["x-request-id"]
    assert oversized.headers["cache-control"] == "no-store"

    def chunks():  # type: ignore[no-untyped-def]
        for _index in range(17):
            yield b"x" * (maximum // 16)

    chunked = client.post(
        "/api/v1/auth/login",
        headers={"Content-Type": "application/json", "Transfer-Encoding": "chunked"},
        content=chunks(),
    )
    assert chunked.status_code == 413
    assert chunked.json()["code"] == "request_too_large"

    def explode() -> None:
        raise RuntimeError("detail-that-must-not-be-returned")

    app.add_api_route("/_test/unexpected", explode, methods=["GET"])
    failed = client.get("/_test/unexpected")
    assert failed.status_code == 500
    assert failed.json()["code"] == "internal_error"
    assert "detail-that-must-not-be-returned" not in failed.text
    assert failed.headers["x-request-id"]
    assert failed.headers["cache-control"] == "no-store"
    assert failed.headers["x-content-type-options"] == "nosniff"

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "hoardarr_api_requests_total" in metrics.text


def test_authentication_work_is_concurrency_bounded(
    api_runtime: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, app, setup_token, _secret_box = api_runtime
    _claim_owner(client, setup_token)
    release = threading.Event()
    two_active = threading.Event()
    lock = threading.Lock()
    active = 0

    def bounded_auth(session: Any, _username: str, _password: str) -> User:
        nonlocal active
        with lock:
            active += 1
            if active == 2:
                two_active.set()
        assert release.wait(timeout=5)
        user = session.scalar(select(User).where(User.username == "owner"))
        assert user is not None
        return user

    monkeypatch.setattr("hoardarr.api.routes.auth.authenticate_password", bounded_auth)
    request = {
        "headers": {"Origin": "http://testserver"},
        "json": {"username": "owner", "password": "a-long-unique-test-password"},
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(client.post, "/api/v1/auth/login", **request)
        second = executor.submit(client.post, "/api/v1/auth/login", **request)
        assert two_active.wait(timeout=5)
        busy = client.post("/api/v1/auth/login", **request)
        assert busy.status_code == 429
        assert busy.json()["code"] == "authentication_busy"
        release.set()
        assert first.result(timeout=5).status_code == 200
        assert second.result(timeout=5).status_code == 200
    assert app.state.authentication_slots.acquire(blocking=False)
    app.state.authentication_slots.release()


def test_controller_redundancy_api_preserves_logical_storage_and_is_idempotent(
    api_runtime: Any,
) -> None:
    client, app, setup_token, _secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)

    def path(controller: str, kernel_path: str) -> dict[str, object]:
        return {
            "id": f"wwn:naa.600a098000api:{controller}",
            "stable_identity": True,
            "system_device": False,
            "selectable": True,
            "kernel_path": kernel_path,
            "identity": {
                "serial": "ARRAY-LUN-7",
                "wwn": "naa.600a098000api",
                "eui64": None,
                "nguid": None,
            },
            "capacity_bytes": 8_000_000_000_000,
            "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
            "connection": {
                "protocol": "fc",
                "controller_address": controller,
                "target_port_wwn": f"50:00:{controller}",
            },
            "partitions": [],
        }

    first = path("hba-a", "/dev/sdb")
    second = path("hba-b", "/dev/sdc")
    hardware = {"schema_version": 1, "source": {"kind": "sysfs"}, "disks": [first, second]}
    with app.state.session_factory() as session, session.begin():
        scan = Operation(
            kind="hardware.scan",
            status="succeeded",
            actor_type="user",
            actor_id="00000000-0000-0000-0000-000000000001",
            request_sha256=document_hash({}),
            request_json={},
        )
        session.add(scan)
        session.flush()
        session.add(
            HardwareSnapshot(
                operation_id=scan.id,
                detector_schema_version=1,
                source="sysfs",
                payload_json=hardware,
                sha256=document_hash(hardware),
            )
        )
        storage = register_single_path_storage(
            session,
            name="MediaPool",
            device=first,
            mountpoint="/media",
            presentation_device="/dev/sdb",
            filesystem_uuid="11111111-1111-4111-8111-111111111111",
        )
        storage.config_json = {
            **storage.config_json,
            "node_name": "Node A",
            "storage_scope": "external_shared",
            "ownership_mode": "controlled_single_writer",
            "ownership_state": "serving",
            "peer_node": "Node B",
        }
        storage_id = storage.id

    inventory = client.get("/api/v1/storage/logical")
    assert inventory.status_code == 200
    assert inventory.json()["items"][0]["id"] == storage_id
    assert inventory.json()["items"][0]["mountpoint"] == "/media"
    assert inventory.json()["items"][0]["node_name"] == "Node A"
    assert inventory.json()["items"][0]["storage_scope"] == "external_shared"
    assert inventory.json()["items"][0]["ownership_mode"] == "controlled_single_writer"
    assert inventory.json()["items"][0]["ownership_state"] == "serving"
    assert inventory.json()["items"][0]["peer_node"] == "Node B"
    assert inventory.json()["items"][0]["redundancy_summary"] == {
        "healthy_paths": 1,
        "active_paths": 1,
        "failed_paths": 0,
        "failovers_today": 0,
        "last_failover": None,
        "time_degraded_seconds": 0,
    }
    event_history = client.get(f"/api/v1/storage/logical/{storage_id}/redundancy/events")
    assert event_history.status_code == 200
    assert event_history.json() == {"items": []}

    assert (
        client.post(
            "/api/v1/storage/redundancy/preview",
            headers={"Origin": "http://testserver"},
            json={"storage_entity_id": storage_id, "action": "add"},
        ).status_code
        == 403
    )
    preview = client.post(
        "/api/v1/storage/redundancy/preview",
        headers=_state_headers(csrf),
        json={"storage_entity_id": storage_id, "action": "add"},
    )
    assert preview.status_code == 200, preview.text
    plan = preview.json()["plan"]
    assert plan["destructive"] is False
    assert plan["format"] is False
    assert plan["before"]["mountpoint"] == plan["after"]["mountpoint"] == "/media"
    assert plan["before"]["filesystem_uuid"] == plan["after"]["filesystem_uuid"]
    assert plan["transition"]["mode"] == "brief_maintenance_required"
    assert plan["settings"]["path_grouping_policy"] == "group_by_prio"

    body = {
        "plan": plan,
        "plan_sha256": preview.json()["plan_sha256"],
        "confirmation": "APPLY",
    }
    headers = _state_headers(csrf, **{"Idempotency-Key": "redundancy-api-one"})
    accepted = client.post("/api/v1/storage/redundancy", headers=headers, json=body)
    replay = client.post("/api/v1/storage/redundancy", headers=headers, json=body)
    assert accepted.status_code == replay.status_code == 202
    assert replay.json()["replayed"] is True

    with app.state.session_factory() as session:
        stored = session.get(StorageEntity, storage_id)
        assert stored is not None
        assert stored.mountpoint == "/media"
        assert stored.filesystem_uuid == "11111111-1111-4111-8111-111111111111"
