from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from hoardarr.core.config import Settings
from hoardarr.core.secrets import SecretBox
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.models import (
    AuditEvent,
    Base,
    HardwareSnapshot,
    IntegrationConnection,
    Operation,
    OperationEvent,
    PhysicalDisk,
    Plan,
    WizardSession,
    utc_now,
)
from hoardarr.hardware.maintenance import enrich_maintenance_capabilities
from hoardarr.hardware.service import HardwareScanError
from hoardarr.integrations.servarr import ServarrError
from hoardarr.operations.service import (
    OperationConflict,
    append_event,
    document_hash,
    request_cancellation,
)
from hoardarr.operations.worker import (
    INTEGRATION_AAD_RECORD_TYPE,
    recover_abandoned_operations,
    refresh_servarr_activity,
    run_once,
)
from hoardarr.storage.tiering import plan_transfer


def _runtime(tmp_path: Path):
    database_path = tmp_path / "worker.db"
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{database_path.as_posix()}",
        secret_key_file=tmp_path / "secret.key",
        secure_cookies=False,
        hardware_detector=tmp_path / "detect-hardware.py",
    )
    engine = create_database_engine(settings.database_url)
    Base.metadata.create_all(engine)
    return settings, create_session_factory(engine)


def _enqueue(
    session_factory: Any,
    *,
    kind: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    request: dict[str, Any] | None = None,
) -> str:
    with session_factory() as session, session.begin():
        operation = Operation(
            kind=kind,
            actor_type="api_token",
            actor_id="test-user",
            resource_type=resource_type,
            resource_id=resource_id,
            idempotency_key=f"test-{kind}-{resource_id}",
            request_sha256=document_hash(request or {}),
            request_json=request or {},
        )
        session.add(operation)
        session.flush()
        append_event(session, operation, "queued", "Operation queued")
        return operation.id


def test_running_host_mutation_cannot_be_recorded_as_cancelled(tmp_path: Path) -> None:
    _settings, session_factory = _runtime(tmp_path)
    for kind in ("storage.apply", "storage.transfer", "connectivity.apply", "connectivity.remove"):
        operation_id = _enqueue(session_factory, kind=kind)
        with session_factory() as session, session.begin():
            operation = session.get(Operation, operation_id)
            assert operation is not None
            operation.status = "running"
            operation.lease_owner = "worker-one"
            operation.heartbeat_at = utc_now()

        with session_factory() as session, session.begin():
            operation = session.get(Operation, operation_id)
            assert operation is not None
            with pytest.raises(OperationConflict, match="cannot be cancelled"):
                request_cancellation(session, operation)

        with session_factory() as session:
            operation = session.get(Operation, operation_id)
            assert operation is not None
            assert operation.status == "running"
            assert operation.cancel_requested is False


def test_hardware_scan_creates_an_immutable_snapshot(tmp_path: Path) -> None:
    settings, session_factory = _runtime(tmp_path)
    operation_id = _enqueue(session_factory, kind="hardware.scan")
    payload = {
        "schema_version": 1,
        "source": {"kind": "sysfs"},
        "controllers": [],
        "disks": [
            {
                "id": "wwn:5000c500feed0001",
                "stable_identity": True,
                "kernel_path": "/dev/sdb",
                "identity": {"serial": "SANITIZED-0001", "wwn": "5000c500feed0001"},
                "vendor": "EXAMPLE",
                "model": "MEDIA-HDD",
                "capacity_bytes": 8_000_000_000_000,
                "rotational": True,
                "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
                "connection": {"transport": "sas", "protocol": "sas", "slot": 3},
            }
        ],
    }

    def detector(*_args: object, **_kwargs: object) -> tuple[dict[str, Any], str]:
        # A second writer succeeding here proves the claim transaction was committed
        # before the external detector ran.
        with session_factory() as session, session.begin():
            session.add(
                AuditEvent(
                    actor_type="system",
                    actor_id="worker-test",
                    action="test.detector",
                    outcome="succeeded",
                    correlation_id=operation_id,
                )
            )
        return payload, document_hash(payload)

    assert run_once(
        session_factory=session_factory,
        settings=settings,
        secret_box=SecretBox(b"h" * 32),
        worker_id="worker-one",
        detector_runner=detector,
    )

    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        snapshot = session.scalar(
            select(HardwareSnapshot).where(HardwareSnapshot.operation_id == operation_id)
        )
        assert operation is not None and operation.status == "succeeded"
        assert snapshot is not None
        expected_payload = enrich_maintenance_capabilities(deepcopy(payload))
        assert snapshot.payload_json == expected_payload
        assert snapshot.sha256 == document_hash(expected_payload)
        disk = session.scalar(select(PhysicalDisk))
        assert disk is not None
        assert disk.stable_identity == "wwn:5000c500feed0001"
        assert disk.kernel_path == "/dev/sdb"
        assert disk.media_type == "hdd"
        assert operation.result_json == {
            "snapshot_id": snapshot.id,
            "sha256": snapshot.sha256,
            "schema_version": 1,
            "source": "sysfs",
            "disk_registry": {"observed": 1, "created": 1, "updated": 0, "skipped": 0},
        }
    assert not run_once(
        session_factory=session_factory,
        settings=settings,
        secret_box=SecretBox(b"h" * 32),
        worker_id="worker-one",
        detector_runner=detector,
    )


def test_hardware_scan_logs_bounded_detector_reason_but_keeps_api_error_generic(
    tmp_path: Path, caplog: Any
) -> None:
    settings, session_factory = _runtime(tmp_path)
    operation_id = _enqueue(session_factory, kind="hardware.scan")

    def detector(*_args: object, **_kwargs: object) -> tuple[dict[str, Any], str]:
        raise HardwareScanError("hardware detector is unavailable: /missing/detector.py")

    with caplog.at_level(logging.ERROR):
        assert run_once(
            session_factory=session_factory,
            settings=settings,
            secret_box=SecretBox(b"h" * 32),
            worker_id="worker-one",
            detector_runner=detector,
        )

    assert "hardware detector is unavailable: /missing/detector.py" in caplog.text
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None
        assert operation.status == "failed"
        assert operation.error_json == {
            "code": "hardware_scan_failed",
            "message": "Hardware scan could not be completed",
        }


def test_servarr_discovery_decrypts_then_persists_only_allowlisted_state(
    tmp_path: Path,
) -> None:
    settings, session_factory = _runtime(tmp_path)
    secret_box = SecretBox(b"s" * 32)
    connection_id = "servarr-connection"
    api_key = "do-not-persist-this-plaintext"
    with session_factory() as session, session.begin():
        session.add(
            IntegrationConnection(
                id=connection_id,
                name="Sonarr",
                expected_product="sonarr",
                base_url="http://10.0.0.20:8989",
                approved_ips_json=["10.0.0.20"],
                api_key_ciphertext=secret_box.encrypt(
                    INTEGRATION_AAD_RECORD_TYPE,
                    connection_id,
                    api_key,
                ),
                verify_tls=False,
            )
        )
    operation_id = _enqueue(
        session_factory,
        kind="servarr.discover",
        resource_type="integration_connection",
        resource_id=connection_id,
    )

    def discoverer(**kwargs: object) -> dict[str, Any]:
        assert kwargs["api_key"] == api_key
        # This write also verifies discovery is outside the claim transaction.
        with session_factory() as session, session.begin():
            session.add(
                AuditEvent(
                    actor_type="system",
                    actor_id="worker-test",
                    action="test.discovery",
                    outcome="succeeded",
                    correlation_id=operation_id,
                )
            )
        return {
            "product": "sonarr",
            "version": "4.0.0",
            "capabilities": ["root_folders", "activity", "not-a-real-capability"],
            "api_key": api_key,
            "state": {
                "status": {"app_name": "Sonarr", "secret": api_key},
                "root_folders": [
                    {"id": 1, "path": "/data/media/tv", "free_space": 123, "secret": api_key}
                ],
                "download_clients": [
                    {
                        "id": 2,
                        "name": "Downloader",
                        "implementation": "SABnzbd",
                        "config_contract": "SabnzbdSettings",
                        "enabled": True,
                        "password": api_key,
                    }
                ],
                "activity": {
                    "quality": "available",
                    "reported_items": 3,
                    "total_items": 3,
                    "active_writes": 2,
                    "downloading": 1,
                    "importing": 1,
                    "pending": 1,
                    "stalled": 0,
                    "title": api_key,
                },
            },
        }

    assert run_once(
        session_factory=session_factory,
        settings=settings,
        secret_box=secret_box,
        worker_id="worker-one",
        servarr_discoverer=discoverer,
    )

    with session_factory() as session:
        connection = session.get(IntegrationConnection, connection_id)
        operation = session.get(Operation, operation_id)
        events = list(
            session.scalars(
                select(OperationEvent).where(OperationEvent.operation_id == operation_id)
            )
        )
        assert connection is not None and connection.status == "connected"
        assert connection.capabilities_json == ["activity", "root_folders"]
        assert connection.state_json["status"] == {"app_name": "Sonarr"}
        assert connection.state_json["root_folders"] == [
            {"id": 1, "path": "/data/media/tv", "free_space": 123}
        ]
        assert "password" not in connection.state_json["download_clients"][0]
        assert connection.state_json["active_writes"] == 2
        assert "title" not in connection.state_json["activity"]
        assert operation is not None and operation.status == "succeeded"
        durable_output = json.dumps(
            {
                "connection_state": connection.state_json,
                "operation_result": operation.result_json,
                "events": [event.data_json for event in events],
            }
        )
        assert api_key not in durable_output


def test_worker_refreshes_bounded_servarr_activity_without_api_consumers(tmp_path: Path) -> None:
    settings, session_factory = _runtime(tmp_path)
    secret_box = SecretBox(b"s" * 32)
    connection_id = "servarr-activity"
    with session_factory() as session, session.begin():
        session.add(
            IntegrationConnection(
                id=connection_id,
                name="Radarr",
                expected_product="radarr",
                discovered_product="radarr",
                base_url="http://10.0.0.21:7878",
                approved_ips_json=["10.0.0.21"],
                api_key_ciphertext=secret_box.encrypt(
                    INTEGRATION_AAD_RECORD_TYPE, connection_id, "activity-secret"
                ),
                verify_tls=False,
                status="connected",
                state_json={"status": {"app_name": "Radarr"}},
            )
        )

    def activity(**kwargs: object) -> dict[str, Any]:
        assert kwargs["api_key"] == "activity-secret"
        return {
            "product": "radarr",
            "activity": {
                "quality": "available",
                "reported_items": 3,
                "total_items": 3,
                "active_writes": 2,
                "downloading": 1,
                "importing": 1,
                "renaming": 1,
                "moving": 1,
                "importing_commands": 1,
                "commands_reported": 3,
                "pending": 1,
                "stalled": 0,
                "untrusted_title": "not persisted",
            },
        }

    assert refresh_servarr_activity(
        session_factory, settings, secret_box, discoverer=activity
    ) == 1
    with session_factory() as session:
        connection = session.get(IntegrationConnection, connection_id)
        assert connection is not None
        assert connection.state_json["active_writes"] == 2
        assert connection.state_json["activity"]["downloading"] == 1
        assert connection.state_json["activity"]["renaming"] == 1
        assert "untrusted_title" not in connection.state_json["activity"]
        assert connection.state_json["activity_observed_at"]

    def unavailable(**_kwargs: object) -> dict[str, Any]:
        raise ServarrError("connection_failed", "must not persist remote detail")

    assert refresh_servarr_activity(
        session_factory, settings, secret_box, discoverer=unavailable
    ) == 1
    with session_factory() as session:
        connection = session.get(IntegrationConnection, connection_id)
        assert connection is not None
        assert connection.state_json["activity"] == {"quality": "temporarily_unavailable"}
        assert "active_writes" not in connection.state_json


def test_servarr_credential_failure_is_stable_and_secret_safe(tmp_path: Path) -> None:
    settings, session_factory = _runtime(tmp_path)
    connection_id = "bad-credential"
    encrypted_by_another_key = SecretBox(b"a" * 32).encrypt(
        INTEGRATION_AAD_RECORD_TYPE,
        connection_id,
        "plaintext-that-must-not-leak",
    )
    with session_factory() as session, session.begin():
        session.add(
            IntegrationConnection(
                id=connection_id,
                name="Radarr",
                expected_product="radarr",
                base_url="http://10.0.0.21:7878",
                approved_ips_json=["10.0.0.21"],
                api_key_ciphertext=encrypted_by_another_key,
                verify_tls=False,
            )
        )
    operation_id = _enqueue(
        session_factory,
        kind="servarr.discover",
        resource_type="integration_connection",
        resource_id=connection_id,
    )

    assert run_once(
        session_factory=session_factory,
        settings=settings,
        secret_box=SecretBox(b"b" * 32),
        worker_id="worker-one",
    )

    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        connection = session.get(IntegrationConnection, connection_id)
        assert operation is not None and operation.status == "failed"
        assert operation.error_json == {
            "code": "credential_unavailable",
            "message": "The Servarr API credential could not be loaded",
        }
        assert connection is not None and connection.status == "error"
        assert connection.state_json == {"last_error": {"code": "credential_unavailable"}}
        assert "plaintext-that-must-not-leak" not in json.dumps(operation.error_json)


def test_servarr_reflected_credential_is_rejected_before_persistence(tmp_path: Path) -> None:
    settings, session_factory = _runtime(tmp_path)
    secret_box = SecretBox(b"r" * 32)
    connection_id = "reflected-credential"
    api_key = "credential-that-was-reflected"
    with session_factory() as session, session.begin():
        session.add(
            IntegrationConnection(
                id=connection_id,
                name="Sonarr",
                expected_product="sonarr",
                base_url="http://10.0.0.22:8989",
                approved_ips_json=["10.0.0.22"],
                api_key_ciphertext=secret_box.encrypt(
                    INTEGRATION_AAD_RECORD_TYPE,
                    connection_id,
                    api_key,
                ),
            )
        )
    operation_id = _enqueue(
        session_factory,
        kind="servarr.discover",
        resource_type="integration_connection",
        resource_id=connection_id,
    )

    def discoverer(**_kwargs: object) -> dict[str, Any]:
        return {
            "product": "sonarr",
            "version": "4.0.0",
            "capabilities": [],
            "state": {
                "status": {
                    "app_name": "Sonarr",
                    "instance_name": f"remote echoed {api_key}",
                }
            },
        }

    assert run_once(
        session_factory=session_factory,
        settings=settings,
        secret_box=secret_box,
        worker_id="worker-one",
        servarr_discoverer=discoverer,
    )

    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        connection = session.get(IntegrationConnection, connection_id)
        assert operation is not None
        assert operation.error_json == {
            "code": "credential_reflected",
            "message": "Servarr reflected its API credential in discovery data",
        }
        assert connection is not None and connection.status == "error"
        durable = json.dumps(
            {"operation": operation.error_json, "connection": connection.state_json}
        )
        assert api_key not in durable


def test_running_servarr_cancellation_cleans_up_pending_connection(tmp_path: Path) -> None:
    settings, session_factory = _runtime(tmp_path)
    secret_box = SecretBox(b"c" * 32)
    connection_id = "cancel-running"
    with session_factory() as session, session.begin():
        session.add(
            IntegrationConnection(
                id=connection_id,
                name="Sonarr",
                expected_product="sonarr",
                base_url="http://10.0.0.23:8989",
                approved_ips_json=["10.0.0.23"],
                api_key_ciphertext=secret_box.encrypt(
                    INTEGRATION_AAD_RECORD_TYPE,
                    connection_id,
                    "cancel-test-api-key",
                ),
                status="pending",
            )
        )
    operation_id = _enqueue(
        session_factory,
        kind="servarr.discover",
        resource_type="integration_connection",
        resource_id=connection_id,
    )

    def discoverer(**_kwargs: object) -> dict[str, Any]:
        with session_factory() as session, session.begin():
            operation = session.get(Operation, operation_id)
            assert operation is not None
            request_cancellation(session, operation)
        return {
            "product": "sonarr",
            "version": "4.0.0",
            "capabilities": [],
            "state": {"status": {"app_name": "Sonarr"}},
        }

    assert run_once(
        session_factory=session_factory,
        settings=settings,
        secret_box=secret_box,
        worker_id="worker-one",
        servarr_discoverer=discoverer,
    )
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        connection = session.get(IntegrationConnection, connection_id)
        assert operation is not None and operation.status == "cancelled"
        assert connection is not None and connection.status == "cancelled"
        assert connection.state_json == {"last_error": {"code": "operation_cancelled"}}


def test_stale_servarr_operation_moves_connection_out_of_pending(tmp_path: Path) -> None:
    settings, session_factory = _runtime(tmp_path)
    connection_id = "stale-discovery"
    with session_factory() as session, session.begin():
        session.add(
            IntegrationConnection(
                id=connection_id,
                name="Sonarr",
                expected_product="sonarr",
                base_url="http://10.0.0.24:8989",
                approved_ips_json=["10.0.0.24"],
                api_key_ciphertext=b"encrypted",
                status="pending",
            )
        )
    operation_id = _enqueue(
        session_factory,
        kind="servarr.discover",
        resource_type="integration_connection",
        resource_id=connection_id,
    )
    with session_factory() as session, session.begin():
        operation = session.get(Operation, operation_id)
        assert operation is not None
        operation.status = "running"
        operation.heartbeat_at = utc_now() - timedelta(minutes=10)
        operation.lease_owner = "dead-worker"

    assert recover_abandoned_operations(session_factory=session_factory, settings=settings) == 1
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        connection = session.get(IntegrationConnection, connection_id)
        assert operation is not None and operation.status == "needs_attention"
        assert connection is not None and connection.status == "error"
        assert connection.state_json == {"last_error": {"code": "worker_interrupted"}}


def test_recovery_reconciles_completed_storage_after_worker_loss(tmp_path: Path) -> None:
    settings, session_factory = _runtime(tmp_path)
    document = {"apply_available": True, "blockers": [], "actions": []}
    plan_sha256 = document_hash(document)
    wizard = WizardSession(
        id="recovery-wizard",
        workflow="storage.add",
        status="applying",
        revision=3,
        current_step="apply",
    )
    plan = Plan(
        id="recovery-plan",
        wizard_session_id=wizard.id,
        revision=3,
        kind="storage.add",
        document_json=document,
        sha256=plan_sha256,
    )
    wizard.plan_id = plan.id
    request = {
        "schema_version": 1,
        "wizard_id": wizard.id,
        "wizard_revision": 3,
        "plan_id": plan.id,
        "plan_sha256": plan_sha256,
    }
    operation = Operation(
        kind="storage.apply",
        actor_type="browser_session",
        actor_id="test-user",
        resource_type="wizard_session",
        resource_id=wizard.id,
        idempotency_key="completed-storage-recovery",
        request_sha256=document_hash(request),
        request_json=request,
        status="running",
        lease_owner="lost-worker",
        heartbeat_at=utc_now() - timedelta(minutes=20),
        cancel_requested=True,
    )
    with session_factory() as session, session.begin():
        session.add(wizard)
    with session_factory() as session, session.begin():
        session.add_all([plan, operation])

    result = {"operation_id": operation.id, "topology": "individual"}

    def completed_status(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {"state": "succeeded", "result": result}

    assert (
        recover_abandoned_operations(
            session_factory=session_factory,
            settings=settings,
            storage_status=completed_status,
        )
        == 1
    )
    with session_factory() as session:
        recovered = session.get(Operation, operation.id)
        recovered_wizard = session.get(WizardSession, wizard.id)
        events = list(
            session.scalars(
                select(OperationEvent)
                .where(OperationEvent.operation_id == operation.id)
                .order_by(OperationEvent.sequence)
            )
        )
        assert recovered is not None and recovered.status == "succeeded"
        assert recovered.cancel_requested is False
        assert recovered.result_json == result
        assert recovered_wizard is not None and recovered_wizard.status == "applied"
        assert [event.event_type for event in events] == [
            "cancellation_too_late",
            "succeeded",
        ]


def test_worker_executes_exact_durable_tier_transfer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, session_factory = _runtime(tmp_path)
    plan = plan_transfer(
        {
            "workload": "usenet",
            "source": "/data/downloads/job.mkv",
            "destination": "/data/media/job.mkv",
            "source_identity": "dev:1",
            "destination_identity": "dev:2",
            "same_filesystem": False,
            "method": "move",
            "required_bytes": 1024,
            "completed_steps": ["download", "repair", "unpack", "verify"],
        }
    ).document()
    request = {"plan": plan, "plan_sha256": document_hash(plan)}
    operation_id = _enqueue(
        session_factory,
        kind="storage.transfer",
        resource_type="storage_transfer",
        resource_id=request["plan_sha256"],
        request=request,
    )
    observed: list[dict[str, Any]] = []

    def transfer(value: object, **_kwargs: object) -> dict[str, Any]:
        observed.append(value.document())  # type: ignore[attr-defined]
        return {"state": "completed", "method": "move"}

    monkeypatch.setattr("hoardarr.operations.worker.execute_transfer", transfer)
    assert run_once(
        session_factory=session_factory,
        settings=settings,
        secret_box=SecretBox(b"t" * 32),
        worker_id="transfer-worker",
    )
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation is not None and operation.status == "succeeded"
        assert operation.result_json is not None
        assert operation.result_json["state"] == "completed"
        assert operation.result_json["method"] == "move"
        assert operation.result_json["processed_bytes"] == 1024
        assert operation.result_json["elapsed_seconds"] > 0
        assert operation.result_json["observed_bytes_per_second"] > 0
    assert observed == [plan]
