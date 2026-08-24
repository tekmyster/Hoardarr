from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import hoardarr.api.routes.auth as auth_routes
import hoardarr.api.routes.storage as storage_routes
import hoardarr.storage.drain as drain_service
import hoardarr.storage.groups as group_service
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
    ForeignMigrationJob,
    HAConfiguration,
    HardwareSnapshot,
    IntegrationConnection,
    MetricEntity,
    MetricSample,
    Operation,
    PhysicalDisk,
    RemoteBackupRun,
    RemoteBackupTarget,
    StorageBackend,
    StorageController,
    StorageEntity,
    StorageGroup,
    StoragePath,
    StorageVolume,
    StorageVolumeSnapshot,
    User,
)
from hoardarr.hardware.topology_expectations import reconcile_topology_snapshot
from hoardarr.operations.service import document_hash
from hoardarr.operations.worker import refresh_media_libraries, run_once
from hoardarr.storage.groups import register_disk
from hoardarr.storage.redundancy import register_single_path_storage
from hoardarr.storage.tiering import plan_transfer
from hoardarr.storage.volumes import register_volume
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


def test_storage_volume_inventory_requires_authentication_and_returns_provider_identity(
    api_runtime: Any,
) -> None:
    client, app, setup_token, _secret_box = api_runtime
    assert client.get("/api/v1/storage/volumes").status_code == 401
    _claim_owner(client, setup_token)
    factory = app.state.session_factory
    with factory() as session, session.begin():
        volume, _created = register_volume(
            session,
            {
                "provider": "filesystem",
                "resource_type": "filesystem",
                "provider_resource_id": "uuid-media",
                "name": "Media filesystem",
                "presentation": "file",
                "mountpoint": "/srv/media",
                "device_path": "/dev/mapper/media",
                "filesystem_type": "xfs",
                "filesystem_uuid": "uuid-media",
                "size_bytes": 8_000_000_000,
                "allocated_bytes": 2_000_000_000,
                "lifecycle_state": "active",
                "config": {},
            },
        )
        session.add(
            Operation(
                kind="storage.volume.create",
                status="succeeded",
                actor_type="user",
                actor_id="owner",
                resource_type="storage_volume",
                resource_id=volume.stable_identity,
                request_sha256="a" * 64,
                request_json={"name": "Media filesystem"},
                result_json={"volume_id": volume.id},
            )
        )

    response = client.get("/api/v1/storage/volumes")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["stable_identity"] == "filesystem:filesystem:uuid-media"
    assert item["mountpoint"] == "/srv/media"
    assert item["presentation"] == "file"
    assert item["capabilities"]["size"]["availability"] == "available"
    assert item["capabilities"]["snapshot"]["support"] == "unsupported"

    detail = client.get(f"/api/v1/storage/volumes/{item['id']}")
    assert detail.status_code == 200
    assert detail.json()["item"] == item
    assert detail.json()["operations"][0]["kind"] == "storage.volume.create"
    assert detail.json()["operations"][0]["resource"]["id"] == item["stable_identity"]
    missing = client.get("/api/v1/storage/volumes/00000000-0000-4000-8000-000000000000")
    assert missing.status_code == 404
    assert missing.json()["code"] == "volume_not_found"


def test_guided_volume_preview_uses_live_pool_identity_and_requires_operate_scope(
    api_runtime: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _app, setup_token, _secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    monkeypatch.setattr(
        storage_routes,
        "discover_storage_inventory",
        lambda: {
            "pools": {
                "items": [
                    {
                        "id": "zfs:tank",
                        "name": "tank",
                        "type": "ZFS",
                        "status": "online",
                        "pool_guid": "1234567890123456789",
                        "free_bytes": 100_000_000_000,
                        "degraded": False,
                    }
                ]
            }
        },
    )
    missing_csrf = client.post(
        "/api/v1/storage/volumes/preview",
        json={"name": "movies", "purpose": "media"},
    )
    assert missing_csrf.status_code == 403

    response = client.post(
        "/api/v1/storage/volumes/preview",
        headers=_state_headers(csrf),
        json={"name": "movies", "purpose": "media"},
    )
    assert response.status_code == 200
    plan = response.json()["plan"]
    assert plan["provider_resource_id"] == "tank/movies"
    assert plan["ready"] is True

    advanced = client.post(
        "/api/v1/storage/volumes/preview",
        headers=_state_headers(csrf),
        json={
            "name": "vm-fast",
            "purpose": "vm",
            "size_bytes": 30_000_000_000,
            "advanced": True,
            "resource_type": "zvol",
            "compression": "zstd-3",
            "volblocksize": "8K",
            "sparse": False,
        },
    )
    assert advanced.status_code == 200
    assert advanced.json()["plan"]["mode"] == "advanced"
    assert advanced.json()["plan"]["properties"] == {
        "compression": "zstd-3",
        "volblocksize": "8K",
        "sparse": False,
    }

    headers = _state_headers(csrf, **{"Idempotency-Key": "create-guided-volume"})
    created = client.post(
        "/api/v1/storage/volumes",
        headers=headers,
        json={"plan": plan, "plan_sha256": plan["plan_sha256"], "confirmation": "CREATE"},
    )
    assert created.status_code == 202
    assert created.json()["operation"]["kind"] == "storage.volume.create"
    replay = client.post(
        "/api/v1/storage/volumes",
        headers=headers,
        json={"plan": plan, "plan_sha256": plan["plan_sha256"], "confirmation": "CREATE"},
    )
    assert replay.status_code == 202
    assert replay.json()["replayed"] is True


def test_snapshot_api_enforces_capability_confirmation_schedule_and_identity(
    api_runtime: Any,
) -> None:
    client, app, setup_token, _secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    factory = app.state.session_factory
    with factory() as session, session.begin():
        volume, _created = register_volume(
            session,
            {
                "provider": "zfs",
                "resource_type": "dataset",
                "provider_resource_id": "tank/media",
                "name": "media",
                "presentation": "file",
                "mountpoint": "/srv/media",
                "filesystem_type": "zfs",
                "filesystem_uuid": "123456789",
                "lifecycle_state": "active",
                "config": {"provider_guid": "123456789"},
                "capabilities": {
                    "snapshot": {"support": "supported", "availability": "available"}
                },
            },
        )
        volume_id = volume.id

    assert (
        client.post(
            f"/api/v1/storage/volumes/{volume_id}/snapshots/preview",
            json={"action": "create", "snapshot_name": "before-upgrade"},
        ).status_code
        == 403
    )
    preview = client.post(
        f"/api/v1/storage/volumes/{volume_id}/snapshots/preview",
        headers=_state_headers(csrf),
        json={"action": "create", "snapshot_name": "before-upgrade"},
    )
    assert preview.status_code == 200, preview.text
    plan = preview.json()["plan"]
    assert plan["snapshot"]["provider_snapshot_id"] == "tank/media@before-upgrade"
    wrong = client.post(
        f"/api/v1/storage/volumes/{volume_id}/snapshots",
        headers=_state_headers(csrf, **{"Idempotency-Key": "snapshot-create"}),
        json={
            "plan": plan,
            "plan_sha256": plan["plan_sha256"],
            "confirmation": "wrong",
        },
    )
    assert wrong.status_code == 409
    created = client.post(
        f"/api/v1/storage/volumes/{volume_id}/snapshots",
        headers=_state_headers(csrf, **{"Idempotency-Key": "snapshot-create"}),
        json={
            "plan": plan,
            "plan_sha256": plan["plan_sha256"],
            "confirmation": "CREATE SNAPSHOT",
        },
    )
    assert created.status_code == 202
    assert created.json()["operation"]["kind"] == "storage.volume.snapshot"
    schedule = client.put(
        f"/api/v1/storage/volumes/{volume_id}/snapshot-schedule",
        headers=_state_headers(csrf),
        json={
            "enabled": True,
            "interval_hours": 24,
            "retention_count": 7,
            "prefix": "nightly",
        },
    )
    assert schedule.status_code == 200
    assert schedule.json()["schedule"]["retention_count"] == 7
    snapshots = client.get(f"/api/v1/storage/volumes/{volume_id}/snapshots")
    assert snapshots.status_code == 200
    assert snapshots.json()["items"] == []
    assert snapshots.json()["schedule"]["enabled"] is True

    with factory() as session, session.begin():
        snapshot = StorageVolumeSnapshot(
            volume_id=volume_id,
            provider_snapshot_id="tank/media@existing",
            snapshot_name="existing",
            provider_guid="987654321",
            state="available",
        )
        session.add(snapshot)
        session.flush()
        snapshot_id = snapshot.id
    restore = client.post(
        f"/api/v1/storage/volumes/{volume_id}/snapshots/preview",
        headers=_state_headers(csrf),
        json={"action": "restore", "snapshot_id": snapshot_id},
    )
    assert restore.status_code == 200
    assert restore.json()["plan"]["confirmation"] == "RESTORE SNAPSHOT"
    restore_plan = restore.json()["plan"]
    with factory() as session, session.begin():
        selected = session.get(StorageVolumeSnapshot, snapshot_id)
        assert selected is not None
        selected.state = "deleted"
    changed = client.post(
        f"/api/v1/storage/volumes/{volume_id}/snapshots",
        headers=_state_headers(csrf, **{"Idempotency-Key": "snapshot-restore-stale"}),
        json={
            "plan": restore_plan,
            "plan_sha256": restore_plan["plan_sha256"],
            "confirmation": "RESTORE SNAPSHOT",
        },
    )
    assert changed.status_code == 409
    assert changed.json()["code"] == "snapshot_identity_changed"


def test_capacity_api_enforces_capability_bounds_confirmation_and_identity(
    api_runtime: Any,
) -> None:
    client, app, setup_token, _secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    factory = app.state.session_factory
    with factory() as session, session.begin():
        volume, _created = register_volume(
            session,
            {
                "provider": "zfs",
                "resource_type": "dataset",
                "provider_resource_id": "tank/media",
                "name": "media",
                "presentation": "file",
                "mountpoint": "/srv/media",
                "filesystem_type": "zfs",
                "filesystem_uuid": "123456789",
                "lifecycle_state": "active",
                "config": {"provider_guid": "123456789"},
                "capabilities": {
                    "quota": {"support": "supported", "availability": "available"},
                    "reservation": {
                        "support": "supported",
                        "availability": "available",
                    },
                },
            },
        )
        volume_id = volume.id

    assert client.post(
        f"/api/v1/storage/volumes/{volume_id}/capacity/preview",
        json={"quota_bytes": 10_000, "reservation_bytes": 1_000},
    ).status_code == 403
    invalid = client.post(
        f"/api/v1/storage/volumes/{volume_id}/capacity/preview",
        headers=_state_headers(csrf),
        json={"quota_bytes": 1_000, "reservation_bytes": 10_000},
    )
    assert invalid.status_code == 409
    assert invalid.json()["code"] == "volume_capacity_invalid"

    preview = client.post(
        f"/api/v1/storage/volumes/{volume_id}/capacity/preview",
        headers=_state_headers(csrf),
        json={"quota_bytes": 20 * 1024**3, "reservation_bytes": 2 * 1024**3},
    )
    assert preview.status_code == 200, preview.text
    plan = preview.json()["plan"]
    assert plan["properties"] == {
        "quota": str(20 * 1024**3),
        "reservation": str(2 * 1024**3),
    }
    headers = _state_headers(csrf, **{"Idempotency-Key": "capacity-media"})
    accepted = client.post(
        f"/api/v1/storage/volumes/{volume_id}/capacity",
        headers=headers,
        json={
            "plan": plan,
            "plan_sha256": plan["plan_sha256"],
            "confirmation": "APPLY CAPACITY LIMITS",
        },
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["operation"]["kind"] == "storage.volume.capacity"
    replay = client.post(
        f"/api/v1/storage/volumes/{volume_id}/capacity",
        headers=headers,
        json={
            "plan": plan,
            "plan_sha256": plan["plan_sha256"],
            "confirmation": "APPLY CAPACITY LIMITS",
        },
    )
    assert replay.status_code == 202
    assert replay.json()["replayed"] is True

    with factory() as session, session.begin():
        current = session.get(StorageVolume, volume_id)
        assert current is not None
        current.config_json = {**current.config_json, "provider_guid": "987654321"}
    changed = client.post(
        f"/api/v1/storage/volumes/{volume_id}/capacity",
        headers=_state_headers(csrf, **{"Idempotency-Key": "capacity-stale"}),
        json={
            "plan": plan,
            "plan_sha256": plan["plan_sha256"],
            "confirmation": "APPLY CAPACITY LIMITS",
        },
    )
    assert changed.status_code == 409
    assert changed.json()["code"] == "volume_capacity_plan_changed"


def test_ha_peer_configuration_and_heartbeat_are_persistent_and_identity_bound(
    api_runtime: Any,
) -> None:
    client, app, setup_token, _secret_box = api_runtime
    assert client.get("/api/v1/ha").status_code == 401
    csrf = _claim_owner(client, setup_token)
    empty = client.get("/api/v1/ha")
    assert empty.status_code == 200
    assert empty.json()["configured"] is False

    configuration = {
        "local_node_id": "hoardarr-a",
        "local_name": "Hoardarr-A",
        "local_fqdn": "hoardarr-a.lab.example",
        "local_ip": "10.81.200.251",
        "local_role": "active",
        "peer_node_id": "hoardarr-b",
        "peer_name": "Hoardarr-B",
        "peer_fqdn": "hoardarr-b.lab.example",
        "peer_ip": "10.81.200.252",
        "peer_role": "passive",
        "service_ip": "10.81.200.253",
    }
    assert client.put("/api/v1/ha/configuration", json=configuration).status_code == 403
    configured = client.put(
        "/api/v1/ha/configuration", headers=_state_headers(csrf), json=configuration
    )
    assert configured.status_code == 200, configured.text
    assert configured.json()["maturity_level"] == "HA-3"
    assert configured.json()["peer"]["state"] == "unavailable"
    assert configured.json()["automatic_failover"] is False
    assert configured.json()["fencing_configured"] is False

    mismatch = client.post(
        "/api/v1/ha/heartbeat",
        headers=_state_headers(csrf),
        json={
            "node_id": "unknown-peer",
            "fqdn": "hoardarr-b.lab.example",
            "ip": "10.81.200.252",
            "role": "passive",
            "current_owner_node_id": "hoardarr-a",
            "synchronization_state": "in_sync",
            "failover_readiness": "ready",
            "storage_ownership": "standby",
        },
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == "ha_peer_identity_mismatch"

    heartbeat = client.post(
        "/api/v1/ha/heartbeat",
        headers=_state_headers(csrf),
        json={
            "node_id": "hoardarr-b",
            "fqdn": "hoardarr-b.lab.example",
            "ip": "10.81.200.252",
            "role": "passive",
            "current_owner_node_id": "hoardarr-a",
            "synchronization_state": "in_sync",
            "failover_readiness": "ready",
            "storage_ownership": "standby",
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["peer"]["reachable"] is True
    assert heartbeat.json()["synchronization_state"] == "in_sync"
    persisted = client.get("/api/v1/ha").json()
    assert persisted["current_owner_node_id"] == "hoardarr-a"
    assert {event["event_type"] for event in persisted["events"]} >= {
        "ha_configured",
        "peer_reachable",
    }
    with app.state.session_factory() as session, session.begin():
        item = session.scalar(select(HAConfiguration))
        assert item is not None
        item.peer_last_seen_at = datetime.now(UTC) - timedelta(minutes=2)
    stale = client.get("/api/v1/ha").json()
    assert stale["peer"]["reachable"] is False
    assert stale["peer"]["state"] == "stale"
    assert stale["failover_readiness"] == "unknown"


def test_expected_topology_api_persists_drift_history_and_requires_csrf(api_runtime: Any) -> None:
    client, app, setup_token, _secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    baseline_hardware = {
        "schema_version": 1,
        "source": {"kind": "fixture"},
        "controllers": [
            {
                "address": "0000:01:00.0",
                "bus_type": "pci",
                "provider": {"name": "LSI SAS3008"},
            }
        ],
        "disks": [
            {
                "id": "wwn:expected-drive",
                "kernel_path": "/dev/sdb",
                "model": "MEDIA",
                "identity": {"serial": "SANITIZED"},
                "connection": {
                    "controller_address": "0000:01:00.0",
                    "protocol": "sas",
                    "transport": "sas",
                    "path_id": "end_device-6:0:3",
                    "enclosure_id": "shelf-1",
                    "slot": "03",
                    "negotiated_speed_gbps": 12.0,
                },
            }
        ],
    }
    with app.state.session_factory() as session, session.begin():
        operation = Operation(
            kind="hardware.scan",
            status="succeeded",
            actor_type="system",
            actor_id="worker",
            request_sha256=document_hash({}),
            request_json={},
        )
        session.add(operation)
        session.flush()
        baseline = HardwareSnapshot(
            operation_id=operation.id,
            detector_schema_version=1,
            source="fixture",
            payload_json=baseline_hardware,
            sha256=document_hash(baseline_hardware),
        )
        session.add(baseline)
        session.flush()
        baseline_id = baseline.id

    missing_csrf = client.post(
        "/api/v1/hardware/topology/expectations",
        json={"snapshot_id": baseline_id, "name": "Media shelf", "confirmation": "SAVE"},
    )
    assert missing_csrf.status_code == 403
    saved = client.post(
        "/api/v1/hardware/topology/expectations",
        headers=_state_headers(csrf),
        json={"snapshot_id": baseline_id, "name": "Media shelf", "confirmation": "SAVE"},
    )
    assert saved.status_code == 201, saved.text
    expectation_id = saved.json()["expectation"]["id"]

    changed_hardware = {**baseline_hardware, "disks": []}
    with app.state.session_factory() as session, session.begin():
        operation = Operation(
            kind="hardware.scan",
            status="succeeded",
            actor_type="system",
            actor_id="worker",
            request_sha256=document_hash({"changed": True}),
            request_json={"changed": True},
        )
        session.add(operation)
        session.flush()
        changed = HardwareSnapshot(
            operation_id=operation.id,
            detector_schema_version=1,
            source="fixture",
            payload_json=changed_hardware,
            sha256=document_hash(changed_hardware),
        )
        session.add(changed)
        session.flush()
        assert reconcile_topology_snapshot(session, changed)["opened"] > 0

    status = client.get("/api/v1/hardware/topology/expectation")
    assert status.status_code == 200
    assert status.json()["expectation"]["id"] == expectation_id
    assert {item["kind"] for item in status.json()["active_drifts"]} >= {
        "missing_controller",
        "missing_drive",
    }
    removed = client.request(
        "DELETE",
        f"/api/v1/hardware/topology/expectations/{expectation_id}",
        headers=_state_headers(csrf),
        json={"confirmation": "REMOVE"},
    )
    assert removed.status_code == 200
    assert client.get("/api/v1/hardware/topology/expectation").json()["expectation"] is None


def test_locate_api_queues_real_activity_and_bounded_automatic_clear(api_runtime: Any) -> None:
    client, app, setup_token, secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    hardware = {
        "schema_version": 1,
        "source": {"kind": "fixture"},
        "controllers": [],
        "disks": [
            {
                "id": "wwn:locatable-drive",
                "stable_identity": True,
                "identity": {"serial": "SANITIZED", "wwn": "locatable-drive"},
                "connection": {
                    "enclosure_id": "enclosure-1",
                    "slot": "3",
                    "mapping_source": "sysfs enclosure_device",
                    "mapping_confidence": "high",
                },
            }
        ],
    }
    with app.state.session_factory() as session, session.begin():
        scan = Operation(
            kind="hardware.scan",
            status="succeeded",
            actor_type="system",
            actor_id="worker",
            request_sha256=document_hash({}),
            request_json={},
        )
        session.add(scan)
        session.flush()
        session.add(
            HardwareSnapshot(
                operation_id=scan.id,
                detector_schema_version=1,
                source="fixture",
                payload_json=hardware,
                sha256=document_hash(hardware),
            )
        )

    endpoint = "/api/v1/hardware/locate"
    body = {"device_id": "wwn:locatable-drive", "enabled": True, "duration_seconds": 30}
    missing_csrf = client.post(
        endpoint, json=body, headers={"Idempotency-Key": "locate-drive-0001"}
    )
    assert missing_csrf.status_code == 403
    queued = client.post(
        endpoint,
        json=body,
        headers=_state_headers(csrf, **{"Idempotency-Key": "locate-drive-0001"}),
    )
    assert queued.status_code == 202, queued.text
    operation_id = queued.json()["operation"]["id"]
    automatic_clear_id = queued.json()["automatic_clear"]["id"]
    assert queued.json()["automatic_clear"]["not_before"] is not None
    replay = client.post(
        endpoint,
        json=body,
        headers=_state_headers(csrf, **{"Idempotency-Key": "locate-drive-0001"}),
    )
    assert replay.status_code == 202
    assert replay.json()["replayed"] is True
    assert replay.json()["operation"]["id"] == operation_id
    assert replay.json()["automatic_clear"]["id"] == automatic_clear_id
    changed_duration = client.post(
        endpoint,
        json={**body, "duration_seconds": 60},
        headers=_state_headers(csrf, **{"Idempotency-Key": "locate-drive-0001"}),
    )
    assert changed_duration.status_code == 409
    assert changed_duration.json()["code"] == "idempotency_conflict"
    clear_cancel = client.post(
        f"/api/v1/operations/{automatic_clear_id}/cancel", headers=_state_headers(csrf)
    )
    assert clear_cancel.status_code == 409
    assert clear_cancel.json()["code"] == "operation_not_cancellable"

    calls: list[tuple[dict, dict, Path]] = []

    def locate_executor(plan: dict, current: dict, *, sysfs_root: Path) -> dict:
        calls.append((plan, current, sysfs_root))
        return {
            "device_id": plan["binding"]["device_id"],
            "enclosure_id": plan["binding"]["enclosure_id"],
            "slot": plan["binding"]["slot"],
            "enabled": plan["enabled"],
            "provider": "test",
            "verification": "command accepted after read-only slot query",
        }

    assert run_once(
        session_factory=app.state.session_factory,
        settings=app.state.settings,
        secret_box=secret_box,
        worker_id="locate-worker",
        locate_executor=locate_executor,
    )
    completed = client.get(f"/api/v1/operations/{operation_id}")
    assert completed.status_code == 200
    assert completed.json()["status"] == "succeeded"
    assert completed.json()["result"]["enabled"] is True
    assert len(calls) == 1


def test_topology_planning_api_keeps_declared_plans_separate_from_live_hardware(
    api_runtime: Any,
) -> None:
    client, _app, setup_token, _secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)

    templates = client.get("/api/v1/hardware/topology/plan-templates")
    assert templates.status_code == 200
    assert {item["id"] for item in templates.json()["items"]} >= {
        "generic-8-bay",
        "generic-dual-path-shelf",
    }
    missing_csrf = client.post(
        "/api/v1/hardware/topology/plans",
        json={"name": "Future media shelf", "template_id": "generic-dual-path-shelf"},
    )
    assert missing_csrf.status_code == 403
    created = client.post(
        "/api/v1/hardware/topology/plans",
        headers=_state_headers(csrf),
        json={"name": "Future media shelf", "template_id": "generic-dual-path-shelf"},
    )
    assert created.status_code == 201, created.text
    plan = created.json()["plan"]
    assert plan["revision"] == 0
    assert len(plan["plan"]["controllers"]) == 2
    assert plan["plan"]["enclosures"][0]["bay_count"] == 24

    document = plan["plan"]
    document["changes"] = [
        {
            "id": "add-bay-3",
            "kind": "disk_addition",
            "label": "Add 18 TB media disk",
            "enclosure_id": "shelf-1",
            "slot": 3,
            "capacity_bytes": 18_000_000_000_000,
            "stable_device_id": None,
        },
        {
            "id": "retire-old-disk",
            "kind": "disk_retirement",
            "label": "Retire old media disk",
            "enclosure_id": None,
            "slot": None,
            "capacity_bytes": None,
            "stable_device_id": "wwn:5000c500old",
        },
    ]
    updated = client.put(
        f"/api/v1/hardware/topology/plans/{plan['id']}",
        headers=_state_headers(csrf),
        json={"revision": 0, "name": plan["name"], "plan": document},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["plan"]["revision"] == 1
    assert len(updated.json()["plan"]["plan"]["changes"]) == 2

    stale = client.put(
        f"/api/v1/hardware/topology/plans/{plan['id']}",
        headers=_state_headers(csrf),
        json={"revision": 0, "name": plan["name"], "plan": document},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "topology_plan_revision_conflict"
    invalid = {**document, "changes": [{**document["changes"][0], "slot": 25}]}
    invalid_update = client.put(
        f"/api/v1/hardware/topology/plans/{plan['id']}",
        headers=_state_headers(csrf),
        json={"revision": 1, "name": plan["name"], "plan": invalid},
    )
    assert invalid_update.status_code == 422

    listed = client.get("/api/v1/hardware/topology/plans")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [plan["id"]]
    removed = client.request(
        "DELETE",
        f"/api/v1/hardware/topology/plans/{plan['id']}",
        headers=_state_headers(csrf),
        json={"confirmation": "REMOVE"},
    )
    assert removed.status_code == 200
    assert client.get("/api/v1/hardware/topology/plans").json()["items"] == []


def test_disk_reservation_api_is_guarded_persistent_and_idempotent(api_runtime: Any) -> None:
    client, app, setup_token, _secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    hardware = {
        "schema_version": 1,
        "source": {"kind": "fixture"},
        "controllers": [],
        "disks": [
            {
                "id": "wwn:future-disk",
                "stable_identity": True,
                "system_device": False,
                "kernel_path": "/dev/sdz",
                "partitions": [],
                "signatures": [],
                "signature_scan": {"status": "complete", "source": "wipefs"},
            },
            {
                "id": "wwn:system-disk",
                "stable_identity": True,
                "system_device": True,
                "kernel_path": "/dev/sda",
                "partitions": [],
                "signatures": [],
                "signature_scan": {"status": "complete", "source": "wipefs"},
            },
        ],
    }
    with app.state.session_factory() as session, session.begin():
        operation = Operation(
            kind="hardware.scan",
            status="succeeded",
            actor_type="system",
            actor_id="worker",
            request_sha256=document_hash({}),
            request_json={},
        )
        session.add(operation)
        session.flush()
        session.add(
            HardwareSnapshot(
                operation_id=operation.id,
                detector_schema_version=1,
                source="fixture",
                payload_json=hardware,
                sha256=document_hash(hardware),
            )
        )
        future, _ = register_disk(
            session,
            {
                "stable_identity": "wwn:future-disk",
                "kernel_path": "/dev/sdz",
                "capacity_bytes": 1_000_000_000,
                "health_state": "healthy",
            },
        )
        system, _ = register_disk(
            session,
            {
                "stable_identity": "wwn:system-disk",
                "kernel_path": "/dev/sda",
                "capacity_bytes": 1_000_000_000,
                "health_state": "healthy",
            },
        )

    endpoint = f"/api/v1/storage/disks/{future.id}/reservation"
    missing_csrf = client.post(endpoint, json={"action": "reserve"})
    assert missing_csrf.status_code == 403
    reserved = client.post(endpoint, headers=_state_headers(csrf), json={"action": "reserve"})
    replayed = client.post(endpoint, headers=_state_headers(csrf), json={"action": "reserve"})
    assert reserved.status_code == replayed.status_code == 200
    assert reserved.json()["item"]["lifecycle_state"] == "reserved"
    assessment = client.get("/api/v1/storage/expansion").json()
    assert [item["id"] for item in assessment["available_disks"]] == [system.id]
    assert assessment["available_disks"][0]["eligible"] is False
    assert [item["id"] for item in assessment["reserved_disks"]] == [future.id]
    blocked = client.post(
        f"/api/v1/storage/disks/{system.id}/reservation",
        headers=_state_headers(csrf),
        json={"action": "reserve"},
    )
    assert blocked.status_code == 422
    assert blocked.json()["type"].endswith("system_disk_protected")
    released = client.post(endpoint, headers=_state_headers(csrf), json={"action": "release"})
    assert released.status_code == 200
    assert released.json()["item"]["lifecycle_state"] == "discovered"


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


def test_storage_group_api_preserves_identity_and_guards_lifecycle(
    api_runtime: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
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
                },
                {
                    "stable_identity": "wwn:5000c500feed0002",
                    "kernel_path": "/dev/sdc",
                    "serial": "SANITIZED-0002",
                    "model": "Media Disk",
                    "capacity_bytes": 8_000_000_000_000,
                    "health_state": "healthy",
                },
            ]
        },
    )
    assert reconciled.status_code == 200, reconciled.text
    disks = {item["stable_identity"]: item["id"] for item in reconciled.json()["items"]}

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
        json={
            "physical_disk_id": disks["wwn:5000c500feed0001"],
            "namespace_path": "/srv/hoardarr/backends/source",
            "role": "data",
        },
    )
    assert assigned.status_code == 201, assigned.text
    backend_id = assigned.json()["item"]["backends"][0]["id"]
    destination = client.post(
        f"/api/v1/storage/groups/{group_id}/backends",
        headers=headers,
        json={
            "physical_disk_id": disks["wwn:5000c500feed0002"],
            "namespace_path": "/srv/hoardarr/backends/destination",
            "role": "data",
        },
    )
    assert destination.status_code == 201, destination.text
    destination_id = next(
        item["id"] for item in destination.json()["item"]["backends"] if item["id"] != backend_id
    )

    bypass = client.post(
        f"/api/v1/storage/groups/{group_id}/backends/{backend_id}/transition",
        headers=headers,
        json={"target_state": "active"},
    )
    assert bypass.status_code == 422
    assert bypass.json()["code"] == "activation_preflight_required"

    monkeypatch.setattr(
        group_service,
        "inspect_backend_activation",
        lambda path, **_kwargs: {
            "path": path,
            "filesystem_device": 101 if path.endswith("source") else 202,
            "mount_source": "/dev/sdb1" if path.endswith("source") else "/dev/sdc1",
            "exact_mount": True,
            "identity_match": True,
            "identity_basis": "API test mounted-source fixture",
            "total_bytes": 20_000,
            "free_bytes": 12_000 if path.endswith("source") else 19_000,
        },
    )
    for selected_backend in (backend_id, destination_id):
        preview = client.post(
            f"/api/v1/storage/groups/{group_id}/backends/{selected_backend}/activation/preview",
            headers=headers,
            json={},
        )
        assert preview.status_code == 200, preview.text
        plan = preview.json()["plan"]
        response = client.post(
            f"/api/v1/storage/groups/{group_id}/backends/{selected_backend}/activation",
            headers=headers,
            json={"plan_sha256": plan["plan_sha256"]},
        )
        assert response.status_code == 200, response.text
    for selected_backend, state in ((backend_id, "preferred_write"),):
        response = client.post(
            f"/api/v1/storage/groups/{group_id}/backends/{selected_backend}/transition",
            headers=headers,
            json={"target_state": state},
        )
        assert response.status_code == 200, response.text

    monkeypatch.setattr(
        drain_service,
        "inspect_filesystem",
        lambda path: drain_service.FilesystemFacts(
            path,
            101 if path.endswith("source") else 202,
            20_000,
            8_000 if path.endswith("source") else 1_000,
            12_000 if path.endswith("source") else 19_000,
        ),
    )
    monkeypatch.setattr(
        drain_service,
        "inspect_open_use",
        lambda _path: {"quality": "available", "open_handles": 0, "processes": []},
    )
    preview = client.post(
        f"/api/v1/storage/groups/{group_id}/drain/preview",
        headers=headers,
        json={
            "source_backend_id": backend_id,
            "destination_backend_ids": [destination_id],
            "verification_mode": "accurate",
            "reserve_bytes": 1_000,
        },
    )
    assert preview.status_code == 200, preview.text
    drain_plan = preview.json()["plan"]
    assert drain_plan["ready"] is True
    assert len(drain_plan["plan_sha256"]) == 64
    guarded = client.post(
        f"/api/v1/storage/groups/{group_id}/backends/{backend_id}/transition",
        headers=headers,
        json={"target_state": "draining"},
    )
    assert guarded.status_code == 422
    assert guarded.json()["code"] == "durable_operation_required"

    started = client.post(
        f"/api/v1/storage/groups/{group_id}/drain",
        headers=_state_headers(csrf, **{"Idempotency-Key": "storage-drain-0001"}),
        json={
            "plan": drain_plan,
            "plan_sha256": drain_plan["plan_sha256"],
            "confirmation": "I AGREE",
        },
    )
    assert started.status_code == 202, started.text
    drain_operation_id = started.json()["operation"]["id"]
    progress = client.get(f"/api/v1/operations/{drain_operation_id}/progress")
    assert progress.status_code == 200, progress.text
    assert progress.json()["phase"] == "preflight"
    assert progress.json()["files"] == {"total": 0, "copied": 0, "verified": 0}
    paused = client.post(
        f"/api/v1/operations/{drain_operation_id}/pause",
        headers=headers,
    )
    assert paused.status_code == 202, paused.text
    assert paused.json()["status"] == "paused"
    resumed = client.post(
        f"/api/v1/operations/{drain_operation_id}/resume",
        headers=headers,
    )
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["status"] == "queued"
    replayed = client.post(
        f"/api/v1/storage/groups/{group_id}/drain",
        headers=_state_headers(csrf, **{"Idempotency-Key": "storage-drain-0001"}),
        json={
            "plan": drain_plan,
            "plan_sha256": drain_plan["plan_sha256"],
            "confirmation": "I AGREE",
        },
    )
    assert replayed.status_code == 202
    assert replayed.json()["replayed"] is True
    assert replayed.json()["operation"]["id"] == drain_operation_id

    document = client.get("/api/v1/storage/groups").json()["items"][0]
    assert document["namespace_path"] == "/srv/hoardarr/media"
    source_document = next(item for item in document["backends"] if item["id"] == backend_id)
    assert source_document["stable_identity"] == "disk:wwn:5000c500feed0001"
    assert source_document["lifecycle_state"] == "preferred_write"


def test_retired_backend_release_api_requires_scope_csrf_and_exact_confirmation(
    api_runtime: Any,
) -> None:
    client, app, setup_token, _secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    headers = _state_headers(csrf)
    reconciled = client.post(
        "/api/v1/storage/disks/reconcile",
        headers=headers,
        json={"items": [{"stable_identity": "wwn:api-retired", "kernel_path": "/dev/sdz"}]},
    )
    disk_id = reconciled.json()["items"][0]["id"]
    created = client.post(
        "/api/v1/storage/groups",
        headers=headers,
        json={"name": "Reusable", "namespace_path": "/srv/hoardarr/reusable"},
    )
    group_id = created.json()["item"]["id"]
    assigned = client.post(
        f"/api/v1/storage/groups/{group_id}/backends",
        headers=headers,
        json={
            "physical_disk_id": disk_id,
            "namespace_path": "/srv/hoardarr/backends/retired",
            "role": "data",
        },
    )
    backend_id = assigned.json()["item"]["backends"][0]["id"]
    with app.state.session_factory() as session, session.begin():
        backend = session.get(StorageBackend, backend_id)
        disk = session.get(PhysicalDisk, disk_id)
        assert backend is not None and disk is not None
        backend.lifecycle_state = "retired"
        backend.config_json = {"drain": {"operation_id": "completed-drain", "phase": "retired"}}
        disk.lifecycle_state = "retired"

    endpoint = f"/api/v1/storage/groups/{group_id}/backends/{backend_id}/retirement"
    missing_csrf = client.post(
        endpoint, json={"action": "release_for_reuse", "confirmation": "RELEASE"}
    )
    assert missing_csrf.status_code == 403
    invalid = client.post(
        endpoint,
        headers=headers,
        json={"action": "release_for_reuse", "confirmation": "release"},
    )
    assert invalid.status_code == 422
    released = client.post(
        endpoint,
        headers=headers,
        json={
            "action": "release_for_reuse",
            "confirmation": "RELEASE",
            "reason": "verified lifecycle test",
        },
    )
    assert released.status_code == 200, released.text
    assert released.json()["item"]["backends"] == []
    assert released.json()["disk"]["lifecycle_state"] == "reuse_ready"
    replay = client.post(
        endpoint,
        headers=headers,
        json={"action": "release_for_reuse", "confirmation": "RELEASE"},
    )
    assert replay.status_code == 422


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
        json={"device_id": disk["id"], "action": "wipe", "method": "metadata_clear"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["plan"]["options"]["scope"] == "metadata_only"
    unsupported = client.post(
        "/api/v1/storage/maintenance/preview",
        headers=_state_headers(csrf),
        json={"device_id": disk["id"], "action": "wipe", "method": "nvme_sanitize"},
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["code"] == "maintenance_capability_unavailable"
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
    summary = client.get("/api/v1/storage/transfers/summary")
    assert summary.status_code == 200
    assert summary.json()["queue"]["queued_count"] == 1
    assert summary.json()["queue"]["queued_bytes"] == 4096
    assert summary.json()["queue"]["estimated_queued_seconds"] is None
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
        "signatures": [],
        "signature_scan": {"status": "complete"},
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
    assert preview.json()["plan"]["existing_data"] == {
        "detected": False,
        "partition_count": 0,
        "signature_types": [],
        "scan_status": "complete",
    }
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


def test_zfs_replacement_preview_is_bound_reserved_and_durable(
    api_runtime: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, app, setup_token, secret_box = api_runtime
    assert (
        client.post(
            "/api/v1/storage/arrays/replacements/preview",
            json={
                "target_id": "zfs:media",
                "old_member_path": "/dev/disk/by-id/scsi-old",
                "replacement_device_id": "wwn:zfs-replacement",
            },
        ).status_code
        == 401
    )
    csrf = _claim_owner(client, setup_token)
    disk = {
        "id": "wwn:zfs-replacement",
        "stable_identity": True,
        "system_device": False,
        "selectable": True,
        "read_only": False,
        "kernel_path": "/dev/sdz",
        "vendor": "TEST",
        "model": "DISPOSABLE",
        "identity": {
            "serial": "ZFS-REPLACE",
            "wwn": "zfs-replacement",
            "eui64": None,
            "nguid": None,
        },
        "capacity_bytes": 2_000_000_000,
        "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
        "partitions": [],
        "signatures": [],
        "signature_scan": {"status": "complete"},
    }
    hardware = {"schema_version": 1, "source": {"kind": "test"}, "disks": [disk]}
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
    monkeypatch.setattr(
        storage_routes,
        "discover_storage_inventory",
        lambda **_kwargs: {
            "pools": {
                "items": [
                    {
                        "id": "zfs:media",
                        "name": "media",
                        "pool_guid": "1234567890123456789",
                        "degraded": True,
                        "configuration": {
                            "quality": "available",
                            "vdev_type": "mirror",
                            "member_paths": [
                                "/dev/disk/by-id/scsi-old",
                                "/dev/disk/by-id/scsi-live",
                            ],
                            "member_capacities": {"/dev/disk/by-id/scsi-old": 1_000_000_000},
                            "config_sha256": "a" * 64,
                        },
                    }
                ]
            },
        },
    )
    preview = client.post(
        "/api/v1/storage/arrays/replacements/preview",
        headers=_state_headers(csrf),
        json={
            "target_id": "zfs:media",
            "old_member_path": "/dev/disk/by-id/scsi-old",
            "replacement_device_id": disk["id"],
        },
    )
    assert preview.status_code == 200, preview.text
    body = {
        "plan": preview.json()["plan"],
        "plan_sha256": preview.json()["plan_sha256"],
        "confirmation": "I AGREE",
    }
    headers = _state_headers(csrf, **{"Idempotency-Key": "zfs-replace-one"})
    accepted = client.post("/api/v1/storage/arrays/replacements", headers=headers, json=body)
    replayed = client.post("/api/v1/storage/arrays/replacements", headers=headers, json=body)
    assert accepted.status_code == replayed.status_code == 202
    assert replayed.json()["replayed"] is True
    operation_id = accepted.json()["operation"]["id"]

    def applier(_socket: object, **values: object) -> dict[str, object]:
        assert values["plan_sha256"] == body["plan_sha256"]
        return {"operation_id": operation_id, "state": "healthy"}

    assert run_once(
        session_factory=app.state.session_factory,
        settings=app.state.settings,
        secret_box=secret_box,
        array_replacement_applier=applier,
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

    capabilities = client.get("/api/v1/system/capabilities")
    assert capabilities.status_code == 200, capabilities.text
    assert capabilities.json()["provider_runtime"] == {
        "api_version": 1,
        "in_process": "built_in_only",
        "third_party": "signed_systemd_addon",
        "arbitrary_in_process_code": False,
    }

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
                    "nvme_crypto_erase": False,
                    "scsi_block_erase": False,
                    "scsi_crypto_erase": False,
                    "sector_format_passthrough": False,
                    "supported_logical_sector_bytes": [],
                    "source": "Not reported",
                    "smart_self_test": {
                        "status": "not_reported",
                        "short_minutes": None,
                        "extended_minutes": None,
                        "source": "Not reported",
                    },
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

    expansion = client.get("/api/v1/storage/expansion")
    assert expansion.status_code == 200, expansion.text
    assert expansion.json()["hardware_snapshot_id"] == snapshot_id
    assert expansion.json()["methodology"].startswith("Plans use the latest persisted hardware")
    assert expansion.json()["available_disks"][0]["existing_data"]["state"] == "unknown"
    assert expansion.json()["candidates"][0]["kind"] == "import_existing"

    foreign = client.get("/api/v1/storage/foreign")
    assert foreign.status_code == 200, foreign.text
    assert foreign.json()["snapshot"]["id"] == snapshot_id
    assert foreign.json()["policy"] == {
        "default_access": "read_only",
        "automatic_mount": False,
        "automatic_assembly": False,
        "mutation_performed": False,
    }
    assert len(foreign.json()["candidates"]) == 1
    assert foreign.json()["candidates"][0]["profile"] == "unraid_unknown"
    assert foreign.json()["candidates"][0]["unraid"]["classification"] == "unknown"
    assert foreign.json()["candidates"][0]["state"] == "blocked"
    assert foreign.json()["unrecognized_device_count"] == 1

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


def test_foreign_inspection_api_is_snapshot_bound_idempotent_and_durable(
    api_runtime: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, app, setup_token, secret_box = api_runtime
    assert (
        client.post(
            "/api/v1/storage/foreign/inspection/preview",
            json={"candidate_id": "foreign:0123456789abcdef01234567"},
        ).status_code
        == 401
    )
    csrf = _claim_owner(client, setup_token)
    hardware = {
        "schema_version": 1,
        "source": {"kind": "sysfs"},
        "disks": [
            {
                "id": "wwn:foreign-api",
                "stable_identity": True,
                "kernel_path": "/dev/sdz",
                "identity": {
                    "serial": "FOREIGN-API",
                    "wwn": "5000000000000002",
                    "eui64": None,
                    "nguid": None,
                },
                "vendor": "TEST",
                "model": "Archive",
                "capacity_bytes": 8_000_000_000,
                "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
                "system_disk": False,
                "read_only": False,
                "mountpoints": [],
                "partitions": [],
                "signatures": [
                    {
                        "type": "ext4",
                        "usage": "filesystem",
                        "uuid": "foreign-fs-api",
                        "label": "Archive",
                        "source": "wipefs",
                    }
                ],
                "signature_scan": {"status": "complete", "source": "wipefs", "reason": None},
            }
        ],
    }
    with app.state.session_factory() as session, session.begin():
        scan = Operation(
            kind="hardware.scan",
            status="succeeded",
            actor_type="system",
            actor_id="worker",
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

    assessment = client.get("/api/v1/storage/foreign")
    candidate = assessment.json()["candidates"][0]
    assert candidate["state"] == "ready"
    endpoint = "/api/v1/storage/foreign/inspection/preview"
    assert client.post(endpoint, json={"candidate_id": candidate["id"]}).status_code == 403
    preview = client.post(
        endpoint,
        headers=_state_headers(csrf),
        json={"candidate_id": candidate["id"]},
    )
    assert preview.status_code == 200, preview.text
    plan = preview.json()["plan"]
    assert plan["persistent_mount"] is False
    apply_headers = _state_headers(csrf, **{"Idempotency-Key": "foreign-inspection-api-0001"})
    wrong_confirmation = client.post(
        "/api/v1/storage/foreign/inspection",
        headers=apply_headers,
        json={
            "plan": plan,
            "plan_sha256": plan["plan_sha256"],
            "confirmation": "APPLY",
        },
    )
    assert wrong_confirmation.status_code == 422
    accepted = client.post(
        "/api/v1/storage/foreign/inspection",
        headers=apply_headers,
        json={
            "plan": plan,
            "plan_sha256": plan["plan_sha256"],
            "confirmation": "INSPECT READ ONLY",
        },
    )
    assert accepted.status_code == 202, accepted.text
    replay = client.post(
        "/api/v1/storage/foreign/inspection",
        headers=apply_headers,
        json={
            "plan": plan,
            "plan_sha256": plan["plan_sha256"],
            "confirmation": "INSPECT READ ONLY",
        },
    )
    assert replay.status_code == 202
    assert replay.json()["replayed"] is True
    assert replay.json()["operation"]["id"] == accepted.json()["operation"]["id"]

    def inspect_applier(*_args: object, **kwargs: object) -> dict[str, Any]:
        return {
            "operation_id": kwargs["operation_id"],
            "candidate_id": plan["candidate_id"],
            "access": "read_only",
            "persistent_mount": False,
            "mutation_performed": False,
            "inventory": {"file_count": 3, "total_bytes": 1024, "read_errors": []},
        }

    assert run_once(
        session_factory=app.state.session_factory,
        settings=app.state.settings,
        secret_box=secret_box,
        worker_id="foreign-inspection-api-worker",
        foreign_inspection_applier=inspect_applier,
    )
    completed = client.get(f"/api/v1/operations/{accepted.json()['operation']['id']}")
    assert completed.json()["status"] == "succeeded"
    assert completed.json()["result"]["inventory"]["file_count"] == 3
    with app.state.session_factory() as session:
        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "storage.foreign.inspect_read_only")
        )
        assert audit is not None

    destination_path = "/"
    with app.state.session_factory() as session, session.begin():
        group = StorageGroup(
            name="Media",
            namespace_path=destination_path,
            purpose="media",
        )
        session.add(group)
        session.flush()
        destination = StorageBackend(
            storage_group_id=group.id,
            stable_identity="managed:foreign-api-destination",
            namespace_path=destination_path,
            lifecycle_state="preferred_write",
        )
        session.add(destination)
        session.flush()
        destination_id = destination.id

    migration_preview_endpoint = "/api/v1/storage/foreign/migration/preview"
    migration_input = {
        "candidate_id": candidate["id"],
        "destination_backend_id": destination_id,
        "verification_mode": "accurate",
        "collision_policy": "stop",
        "reserve_bytes": 0,
        "selection": {
            "mode": "selected_folders",
            "include_paths": ["Movies"],
            "include_extensions": [],
            "include_globs": [],
            "exclude_globs": [],
        },
    }
    assert client.post(migration_preview_endpoint, json=migration_input).status_code == 403
    invalid_selection = client.post(
        migration_preview_endpoint,
        headers=_state_headers(csrf),
        json={
            **migration_input,
            "selection": {"mode": "selected_folders", "include_paths": ["../etc"]},
        },
    )
    assert invalid_selection.status_code == 422
    assert invalid_selection.json()["code"] == "foreign_selection_invalid"
    migration_preview = client.post(
        migration_preview_endpoint,
        headers=_state_headers(csrf),
        json=migration_input,
    )
    assert migration_preview.status_code == 200, migration_preview.text
    migration_plan = migration_preview.json()["plan"]
    assert migration_plan["source_retained"] is True
    assert migration_plan["parity_reuse_supported"] is False
    assert migration_plan["verification"]["algorithm"] == "blake3"
    assert migration_plan["selection"]["include_paths"] == ["Movies"]
    assert migration_plan["selection"]["exact_selected_bytes_at_review"] is None
    migration_headers = _state_headers(csrf, **{"Idempotency-Key": "foreign-migration-api-0001"})
    wrong_migration_confirmation = client.post(
        "/api/v1/storage/foreign/migration",
        headers=migration_headers,
        json={
            "plan": migration_plan,
            "plan_sha256": migration_plan["plan_sha256"],
            "confirmation": "APPLY",
        },
    )
    assert wrong_migration_confirmation.status_code == 422
    migration_accepted = client.post(
        "/api/v1/storage/foreign/migration",
        headers=migration_headers,
        json={
            "plan": migration_plan,
            "plan_sha256": migration_plan["plan_sha256"],
            "confirmation": "COPY AND VERIFY",
        },
    )
    assert migration_accepted.status_code == 202, migration_accepted.text
    migration_replay = client.post(
        "/api/v1/storage/foreign/migration",
        headers=migration_headers,
        json={
            "plan": migration_plan,
            "plan_sha256": migration_plan["plan_sha256"],
            "confirmation": "COPY AND VERIFY",
        },
    )
    assert migration_replay.status_code == 202
    assert migration_replay.json()["replayed"] is True
    migration_operation_id = migration_accepted.json()["operation"]["id"]

    def execute_migration(
        session_factory: Any, operation_id: str, queued_plan: dict[str, Any]
    ) -> dict[str, Any]:
        report = {
            "operation_id": operation_id,
            "candidate_id": queued_plan["candidate_id"],
            "destination_backend_id": queued_plan["destination"]["backend_id"],
            "destination_path": queued_plan["destination"]["path"],
            "files_total": 3,
            "files_copied": 3,
            "files_verified": 3,
            "files_reused": 0,
            "bytes_copied": 1024,
            "source_retained": True,
            "parity_reused": False,
        }
        with session_factory() as session, session.begin():
            job = session.get(ForeignMigrationJob, operation_id)
            assert job is not None
            job.status = "succeeded"
            job.phase = "completed"
            job.files_total = 3
            job.files_copied = 3
            job.files_verified = 3
            job.bytes_total = 1024
            job.bytes_copied = 1024
            job.report_json = report
        return report

    monkeypatch.setattr("hoardarr.operations.worker.execute_foreign_migration", execute_migration)
    assert run_once(
        session_factory=app.state.session_factory,
        settings=app.state.settings,
        secret_box=secret_box,
        worker_id="foreign-migration-api-worker",
    )
    completed_migration = client.get(f"/api/v1/operations/{migration_operation_id}")
    assert completed_migration.json()["status"] == "succeeded"
    assert completed_migration.json()["result"]["source_retained"] is True
    assert completed_migration.json()["result"]["parity_reused"] is False
    with app.state.session_factory() as session:
        migration_audit = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "storage.foreign.migrate_files")
        )
        assert migration_audit is not None


def test_foreign_stack_preview_is_authorized_audited_and_nonactivating(
    api_runtime: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, app, setup_token, _secret_box = api_runtime
    endpoint = "/api/v1/storage/foreign/stack-preview"
    candidate_id = "foreign:0123456789abcdef01234567"
    assert client.post(endpoint, json={"candidate_id": candidate_id}).status_code == 401
    csrf = _claim_owner(client, setup_token)
    monkeypatch.setattr("hoardarr.storage.foreign.shutil.which", lambda _name: "/usr/bin/mdadm")
    disks = []
    for index in range(2):
        disks.append(
            {
                "id": f"wwn:foreign-md-{index}",
                "stable_identity": True,
                "kernel_path": f"/dev/sd{chr(98 + index)}",
                "identity": {
                    "serial": f"FOREIGN-MD-{index}",
                    "wwn": f"500000000000001{index}",
                    "eui64": None,
                    "nguid": None,
                },
                "vendor": "TEST",
                "model": "MD member",
                "capacity_bytes": 8_000_000_000,
                "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
                "system_disk": False,
                "read_only": False,
                "mountpoints": [],
                "partitions": [],
                "signatures": [
                    {
                        "type": "linux_raid_member",
                        "usage": "raid",
                        "uuid": "md-array-api",
                        "label": None,
                        "source": "wipefs",
                    }
                ],
                "signature_scan": {"status": "complete", "source": "wipefs", "reason": None},
            }
        )
    hardware = {"schema_version": 1, "source": {"kind": "sysfs"}, "disks": disks}
    with app.state.session_factory() as session, session.begin():
        scan = Operation(
            kind="hardware.scan",
            status="succeeded",
            actor_type="system",
            actor_id="worker",
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
    assessment = client.get("/api/v1/storage/foreign").json()
    candidate = assessment["candidates"][0]
    captured: dict[str, Any] = {}

    def provider(_socket: object, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "candidate_id": kwargs["plan"]["candidate_id"],
            "provider": "linux_md",
            "identity": "md-array-api",
            "activation_performed": False,
            "mutation_performed": False,
            "members": [{"role": 0}, {"role": 1}],
            "completeness": {"quality": "available", "state": "complete"},
            "health": {"quality": "not_reported", "state": None},
            "mountability": {"quality": "derived", "state": "read_only_assembly_candidate"},
        }

    monkeypatch.setattr("hoardarr.api.routes.storage.preview_foreign_stack", provider)
    assert client.post(endpoint, json={"candidate_id": candidate["id"]}).status_code == 403
    response = client.post(
        endpoint,
        headers=_state_headers(csrf),
        json={"candidate_id": candidate["id"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["activation_performed"] is False
    assert response.json()["result"]["mutation_performed"] is False
    assert len(captured["plan"]["members"]) == 2
    assert captured["plan"]["activation_allowed"] is False
    with app.state.session_factory() as session:
        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "storage.foreign.preview_stack")
        )
        assert audit is not None


def test_unraid_assignment_evidence_is_persisted_audited_and_identity_bound(
    api_runtime: Any,
) -> None:
    client, app, setup_token, _secret_box = api_runtime
    endpoint = "/api/v1/storage/foreign/unraid/evidence"
    manifest = {
        "schema_version": 1,
        "source": "unraid_runtime_state",
        "captured_at": "2026-08-23T20:00:00Z",
        "unraid_version": "7.2.0",
        "assignments": [
            {
                "slot": "parity",
                "role": "parity",
                "serial": "UNRAID-PARITY",
                "wwn": "5000000000000099",
                "capacity_bytes": 8_000_000_000,
                "filesystem_type": None,
            }
        ],
    }
    assert client.post(endpoint, json=manifest).status_code == 401
    csrf = _claim_owner(client, setup_token)
    hardware = {
        "schema_version": 1,
        "source": {"kind": "sysfs"},
        "disks": [
            {
                "id": "wwn:unraid-parity",
                "stable_identity": True,
                "kernel_path": "/dev/sdz",
                "identity": {
                    "serial": "UNRAID-PARITY",
                    "wwn": "5000000000000099",
                    "eui64": None,
                    "nguid": None,
                },
                "vendor": "TEST",
                "model": "Unraid source",
                "capacity_bytes": 8_000_000_000,
                "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
                "system_disk": False,
                "read_only": False,
                "mountpoints": [],
                "partitions": [],
                "signatures": [],
                "signature_scan": {"status": "complete", "source": "wipefs", "reason": None},
            }
        ],
    }
    with app.state.session_factory() as session, session.begin():
        scan = Operation(
            kind="hardware.scan",
            status="succeeded",
            actor_type="system",
            actor_id="worker",
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

    assert client.post(endpoint, json=manifest).status_code == 403
    invalid = {**manifest, "assignments": [{**manifest["assignments"][0], "role": "data"}]}
    assert client.post(endpoint, headers=_state_headers(csrf), json=invalid).status_code == 422
    saved = client.post(endpoint, headers=_state_headers(csrf), json=manifest)
    assert saved.status_code == 201, saved.text
    assert saved.json()["item"]["matched_assignment_count"] == 1
    assessment = client.get("/api/v1/storage/foreign").json()
    assert assessment["candidates"][0]["profile_name"] == "Identified Unraid parity disk"
    assert assessment["candidates"][0]["unraid"]["classification"] == "identified"
    assert assessment["candidates"][0]["unraid"]["parity_reuse_supported"] is False
    removed = client.delete(endpoint, headers=_state_headers(csrf))
    assert removed.status_code == 200
    assert removed.json()["cleared"] == 1
    assert client.get("/api/v1/storage/foreign").json()["unraid_evidence"] is None
    with app.state.session_factory() as session:
        actions = set(
            session.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.action.like("storage.foreign.unraid_evidence.%")
                )
            )
        )
    assert actions == {
        "storage.foreign.unraid_evidence.save",
        "storage.foreign.unraid_evidence.remove",
    }


def test_nas_source_evidence_requires_matching_platform_marker_and_is_audited(
    api_runtime: Any,
) -> None:
    client, app, setup_token, _secret_box = api_runtime
    endpoint = "/api/v1/storage/foreign/nas/evidence"
    manifest = {
        "schema_version": 1,
        "source": "nas_runtime_state",
        "captured_at": "2026-08-23T20:00:00Z",
        "platform": "qnap",
        "platform_marker": "qnap_runtime",
        "product_version": "5.2.8",
        "members": [
            {
                "member": "disk1",
                "serial": "QNAP-DATA-1",
                "wwn": "5000000000000101",
                "capacity_bytes": 8_000_000_000,
            }
        ],
    }
    assert client.post(endpoint, json=manifest).status_code == 401
    csrf = _claim_owner(client, setup_token)
    invalid = {**manifest, "platform_marker": "synology_runtime"}
    assert client.post(endpoint, headers=_state_headers(csrf), json=invalid).status_code == 422
    invalid_hardware_text = {
        **manifest,
        "members": [{**manifest["members"][0], "serial": "QNAP-DATA-1\nforged"}],
    }
    assert (
        client.post(
            endpoint,
            headers=_state_headers(csrf),
            json=invalid_hardware_text,
        ).status_code
        == 422
    )
    with app.state.session_factory() as session, session.begin():
        scan = Operation(
            kind="hardware.scan",
            status="succeeded",
            actor_type="system",
            actor_id="worker",
            request_sha256=document_hash({}),
            request_json={},
        )
        session.add(scan)
        session.flush()
        hardware = {
            "schema_version": 1,
            "source": {"kind": "sysfs"},
            "disks": [
                {
                    "id": "wwn:qnap-data",
                    "stable_identity": True,
                    "kernel_path": "/dev/sdz",
                    "identity": {
                        "serial": "QNAP-DATA-1",
                        "wwn": "5000000000000101",
                        "eui64": None,
                        "nguid": None,
                    },
                    "vendor": "TEST",
                    "model": "QNAP source",
                    "capacity_bytes": 8_000_000_000,
                    "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
                    "system_disk": False,
                    "read_only": False,
                    "mountpoints": [],
                    "partitions": [],
                    "signatures": [
                        {
                            "type": "ext4",
                            "usage": "filesystem",
                            "uuid": "qnap-data-fs",
                            "source": "wipefs",
                        }
                    ],
                    "signature_scan": {"status": "complete", "source": "wipefs"},
                }
            ],
        }
        session.add(
            HardwareSnapshot(
                operation_id=scan.id,
                detector_schema_version=1,
                source="sysfs",
                payload_json=hardware,
                sha256=document_hash(hardware),
            )
        )

    saved = client.post(endpoint, headers=_state_headers(csrf), json=manifest)
    assert saved.status_code == 201, saved.text
    assert saved.json()["item"]["matched_member_count"] == 1
    assessment = client.get("/api/v1/storage/foreign").json()
    assert assessment["candidates"][0]["origin"]["name"] == "QNAP QTS / QuTS"
    removed = client.delete(endpoint, headers=_state_headers(csrf))
    assert removed.status_code == 200 and removed.json()["cleared"] == 1
    with app.state.session_factory() as session:
        actions = set(
            session.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.action.like("storage.foreign.nas_evidence.%")
                )
            )
        )
    assert actions == {
        "storage.foreign.nas_evidence.save",
        "storage.foreign.nas_evidence.remove",
    }


def test_remote_backup_target_api_encrypts_credentials_and_queues_durable_runs(
    api_runtime: Any,
) -> None:
    client, app, setup_token, _secret_box = api_runtime
    endpoint = "/api/v1/backups/targets"
    document = {
        "name": "Home MinIO",
        "provider": "minio",
        "endpoint_url": "https://127.0.0.1:9000",
        "region": "us-east-1",
        "bucket": "hoardarr-backups",
        "prefix": "server-a",
        "access_key_id": "backup-access-key",
        "secret_access_key": "backup-secret-value",
        "force_path_style": True,
        "allow_private_network": True,
    }
    assert client.post(endpoint, json=document).status_code == 401
    csrf = _claim_owner(client, setup_token)
    created = client.post(endpoint, headers=_state_headers(csrf), json=document)
    assert created.status_code == 201, created.text
    target = created.json()
    target_id = target["id"]
    assert target["status"] == "not_tested"
    assert "access_key" not in json.dumps(target).casefold()
    assert "secret" not in json.dumps(target).casefold()
    with app.state.session_factory() as session:
        stored = session.get(RemoteBackupTarget, target_id)
        assert stored is not None
        assert b"backup-secret-value" not in stored.secret_ciphertext
        assert stored.credential_fingerprint != "backup-access-key"

    tested = client.post(
        f"{endpoint}/{target_id}/test",
        headers=_state_headers(csrf, **{"Idempotency-Key": "backup-test-0001"}),
    )
    assert tested.status_code == 202, tested.text
    assert tested.json()["operation"]["kind"] == "backup.target.test"
    replay = client.post(
        f"{endpoint}/{target_id}/test",
        headers=_state_headers(csrf, **{"Idempotency-Key": "backup-test-0001"}),
    )
    assert replay.status_code == 202 and replay.json()["replayed"] is True

    schedule_blocked = client.put(
        f"{endpoint}/{target_id}/schedule",
        headers=_state_headers(csrf),
        json={"enabled": True, "interval_hours": 24},
    )
    assert schedule_blocked.status_code == 409

    blocked = client.post(
        f"{endpoint}/{target_id}/runs",
        headers=_state_headers(csrf, **{"Idempotency-Key": "backup-run-0001"}),
        json={"confirmation": "BACK UP HOARDARR"},
    )
    assert blocked.status_code == 409
    with app.state.session_factory() as session, session.begin():
        stored = session.get(RemoteBackupTarget, target_id)
        assert stored is not None
        stored.status = "available"
    scheduled = client.put(
        f"{endpoint}/{target_id}/schedule",
        headers=_state_headers(csrf),
        json={"enabled": True, "interval_hours": 24},
    )
    assert scheduled.status_code == 200, scheduled.text
    assert scheduled.json()["schedule"] == {"enabled": True, "interval_hours": 24}
    queued = client.post(
        f"{endpoint}/{target_id}/runs",
        headers=_state_headers(csrf, **{"Idempotency-Key": "backup-run-0001"}),
        json={"confirmation": "BACK UP HOARDARR"},
    )
    assert queued.status_code == 202, queued.text
    assert queued.json()["run"]["backup_kind"] == "control_plane"
    operation_id = queued.json()["operation"]["id"]
    with app.state.session_factory() as session:
        assert session.get(RemoteBackupRun, operation_id) is not None
        audit_details = [
            event.details_json
            for event in session.scalars(
                select(AuditEvent).where(AuditEvent.action.like("backup.%"))
            )
        ]
        assert "backup-secret-value" not in json.dumps(audit_details)


def test_remote_backup_target_rejects_unapproved_private_or_insecure_endpoints(
    api_runtime: Any,
) -> None:
    client, _app, setup_token, _secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    base = {
        "name": "Unsafe target",
        "provider": "generic_s3",
        "endpoint_url": "https://127.0.0.1:9000",
        "bucket": "hoardarr-backups",
        "access_key_id": "backup-access-key",
        "secret_access_key": "backup-secret-value",
    }
    private = client.post(
        "/api/v1/backups/targets",
        headers=_state_headers(csrf),
        json=base,
    )
    assert private.status_code == 422
    insecure = client.post(
        "/api/v1/backups/targets",
        headers=_state_headers(csrf),
        json={
            **base,
            "endpoint_url": "http://127.0.0.1:9000",
            "allow_private_network": True,
        },
    )
    assert insecure.status_code == 422


def test_remote_backup_credential_rotation_invalidates_connection_proof(
    api_runtime: Any,
) -> None:
    client, app, setup_token, _secret_box = api_runtime
    endpoint = "/api/v1/backups/targets"
    document = {
        "name": "Rotating MinIO",
        "provider": "minio",
        "endpoint_url": "https://127.0.0.1:9000",
        "bucket": "hoardarr-backups",
        "access_key_id": "original-access-key",
        "secret_access_key": "original-secret-key",
        "force_path_style": True,
        "allow_private_network": True,
    }
    csrf = _claim_owner(client, setup_token)
    created = client.post(endpoint, headers=_state_headers(csrf), json=document)
    assert created.status_code == 201, created.text
    target_id = created.json()["id"]
    with app.state.session_factory() as session, session.begin():
        target = session.get(RemoteBackupTarget, target_id)
        assert target is not None
        target.status = "available"
        target.last_tested_at = datetime.now(UTC)
        target.schedule_json = {"enabled": True, "interval_hours": 24}
        previous_fingerprint = target.credential_fingerprint

    payload = {
        "access_key_id": "replacement-access-key",
        "secret_access_key": "replacement-secret-key",
    }
    unauthorized = client.put(f"{endpoint}/{target_id}/credentials", json=payload)
    assert unauthorized.status_code == 403
    rotated = client.put(
        f"{endpoint}/{target_id}/credentials",
        headers=_state_headers(csrf),
        json=payload,
    )
    assert rotated.status_code == 200, rotated.text
    response = rotated.json()
    assert response["status"] == "not_tested"
    assert response["last_tested_at"] is None
    assert response["schedule"]["enabled"] is False
    assert response["credential_fingerprint"] != previous_fingerprint
    assert "replacement" not in rotated.text
    with app.state.session_factory() as session:
        target = session.get(RemoteBackupTarget, target_id)
        assert target is not None
        assert b"replacement-secret-key" not in target.secret_ciphertext
        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "backup.target.credentials.rotate")
        )
        assert audit is not None
        assert "replacement-secret-key" not in json.dumps(audit.details_json)


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

    with app.state.session_factory() as session, session.begin():
        controller = StorageController(
            id="11111111-1111-4111-8111-111111111111",
            stable_identity="pci:0000:03:00.0",
            provider="sas",
            model="Test HBA",
            state_json={"health": "healthy", "api_key": "must-not-export"},
        )
        logical = StorageEntity(
            id="22222222-2222-4222-8222-222222222222",
            name="Media storage",
            stable_identity="wwid:3600test",
            storage_kind="block",
            filesystem_uuid="33333333-3333-4333-8333-333333333333",
            mountpoint="/media",
            presentation_device="/dev/mapper/3600test",
            capacity_bytes=1_000_000,
            logical_sector_bytes=512,
            physical_sector_bytes=4096,
            topology_state="fully_redundant",
            provider="multipath",
            config_json={"secret": "must-not-export"},
        )
        session.add_all([controller, logical])
        session.flush()
        session.add(
            StoragePath(
                storage_entity_id=logical.id,
                controller_id=controller.id,
                stable_path_identity="scsi:1:0:0:1",
                kernel_path="/dev/sdb",
                logical_storage_identity=logical.stable_identity,
                protocol="sas",
                state="active",
                optimized=True,
                active=True,
                metadata_json={"api_key": "must-not-export"},
            )
        )

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
    home_assistant = client.get("/api/v1/integrations/home-assistant/summary", headers=pat_headers)
    assert home_assistant.status_code == 200, home_assistant.text
    summary = home_assistant.json()
    assert summary["schema_version"] == 1
    assert summary["source"] == "hoardarr_persisted_state"
    assert len(summary["jobs"]["recent"]) <= summary["jobs"]["limit"] == 25
    assert summary["topology"]["limits"] == {
        "logical_storage": 128,
        "controllers": 128,
        "paths": 512,
    }
    assert summary["topology"]["logical_storage"][0]["path_count"] == 1
    assert summary["topology"]["controllers"][0]["health"] == "healthy"
    assert summary["topology"]["paths"][0]["storage_entity_id"] == logical.id
    assert "servarr-api-key-that-must-never-leak" not in home_assistant.text
    assert "api_key" not in home_assistant.text
    assert "must-not-export" not in home_assistant.text
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


def test_media_library_connection_is_read_only_and_persists_sanitized_discovery(
    api_runtime: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, app, setup_token, secret_box = api_runtime
    csrf = _claim_owner(client, setup_token)
    credential = "plex-token-that-must-not-leak"
    created = client.post(
        "/api/v1/integrations",
        headers=_state_headers(csrf, **{"Idempotency-Key": "media-integration-0001"}),
        json={
            "name": "Plex",
            "product": "plex",
            "base_url": "http://127.0.0.1:32400",
            "api_key": credential,
            "verify_tls": True,
            "allow_localhost": True,
        },
    )
    assert created.status_code == 202, created.text
    assert created.json()["operation"]["kind"] == "media.discover"
    connection_id = created.json()["integration"]["id"]

    namespace = tmp_path / "media"
    movies = namespace / "Movies"
    movies.mkdir(parents=True)

    def discoverer(**kwargs: object) -> dict[str, Any]:
        assert kwargs["api_key"] == credential
        return {
            "product": "plex",
            "version": "1.42.0",
            "capabilities": ["media_libraries"],
            "state": {
                "status": {"app_name": "Plex", "version": "1.42.0"},
                "libraries": [
                    {
                        "id": "movies",
                        "name": "Movies",
                        "media_type": "movie",
                        "paths": [str(movies)],
                        "item_count": 4020,
                        "capacity_bytes": None,
                        "quality": "available",
                        "untrusted_extra": credential,
                    }
                ],
            },
        }

    monkeypatch.setattr("hoardarr.operations.worker.discover_media_server", discoverer)
    assert run_once(
        session_factory=app.state.session_factory,
        settings=app.state.settings,
        secret_box=secret_box,
        worker_id="media-api-worker",
    )
    connection = client.get(f"/api/v1/integrations/{connection_id}")
    assert connection.status_code == 200
    assert connection.json()["status"] == "connected"
    assert connection.json()["capabilities"] == ["media_libraries"]
    assert connection.json()["state"]["libraries"][0]["item_count"] == 4020
    assert (
        connection.json()["state"]["libraries"][0]["storage_mapping"]["quality"] == "not_reported"
    )
    assert "untrusted_extra" not in connection.text
    assert credential not in connection.text

    with app.state.session_factory() as session, session.begin():
        session.add(
            StorageGroup(
                name="Media",
                namespace_path=str(namespace),
                purpose="media",
                state="active",
            )
        )

    def refreshed_discovery(**kwargs: object) -> dict[str, Any]:
        result = discoverer(**kwargs)
        result["state"]["libraries"][0]["item_count"] = 4021
        return result

    assert (
        refresh_media_libraries(
            app.state.session_factory,
            app.state.settings,
            secret_box,
            discoverer=refreshed_discovery,
        )
        == 1
    )
    refreshed = client.get(f"/api/v1/integrations/{connection_id}")
    mapping = refreshed.json()["state"]["libraries"][0]["storage_mapping"]
    assert mapping["confidence"] == "high"
    assert mapping["storage_group_name"] == "Media"
    assert mapping["storage_capacity_bytes"] > 0
    with app.state.session_factory() as session:
        entity = session.scalar(
            select(MetricEntity).where(MetricEntity.stable_id == f"media:{connection_id}:movies")
        )
        assert entity is not None
        samples = list(
            session.scalars(
                select(MetricSample)
                .where(MetricSample.entity_id == entity.id)
                .order_by(MetricSample.observed_at)
            )
        )
        values = {sample.metric_id: sample.value for sample in samples}
        assert values["media.library.items"] == 4021
        assert values["media.library.storage.capacity"] > 0
        assert values["media.library.storage.free"] > 0
