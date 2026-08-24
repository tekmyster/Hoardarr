from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from hoardarr.core.config import Settings
from hoardarr.core.secrets import SecretBox
from hoardarr.db.models import (
    Base,
    FleetTelemetryQueue,
    HardwareSnapshot,
    Operation,
    utc_now,
)
from hoardarr.fleet.service import (
    canonical_json,
    deliver_batch,
    enqueue,
    enqueue_heartbeat,
    enqueue_inventory,
    enqueue_lifecycle_event,
    ensure_state,
    heartbeat_payload,
    install_credential,
    pending_payloads,
    pseudonymous_drive_id,
    queue_summary,
    register_installation,
    reset_identity,
)


def _runtime(*, max_records: int = 100, max_bytes: int = 1024 * 1024):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(
        environment="test",
        database_url="sqlite:///:memory:",
        fleet_telemetry_endpoint="https://fleet.test/api/telemetry/v1",
        fleet_queue_max_records=max_records,
        fleet_queue_max_bytes=max_bytes,
    )
    return factory, settings, SecretBox(b"f" * 32)


def _snapshot(session: Session) -> None:
    operation = Operation(
        kind="hardware.scan",
        status="succeeded",
        actor_type="system",
        actor_id="worker",
        request_sha256="0" * 64,
        request_json={},
    )
    session.add(operation)
    session.flush()
    payload = {
        "schema_version": 1,
        "disks": [
            {
                "id": "wwn:5000c500aabbccdd",
                "kernel_path": "/dev/sdz",
                "vendor": "Example",
                "model": "SSD-1",
                "serial": "PRIVATE-SERIAL-A93F",
                "capacity_bytes": 1_000_000_000,
                "identity": {"wwn": "5000c500aabbccdd"},
                "connection": {"protocol": "sas", "enclosure_model": "Shelf"},
                "health": {"overall": "healthy", "temperature_celsius": 31},
                "api_key": "must-never-leave",
            }
        ],
    }
    session.add(
        HardwareSnapshot(
            operation_id=operation.id,
            detector_schema_version=1,
            source="test",
            payload_json=payload,
            sha256=hashlib.sha256(canonical_json(payload)).hexdigest(),
        )
    )


def test_required_heartbeat_is_minimal_and_identity_is_random_and_persistent() -> None:
    factory, settings, _box = _runtime()
    with factory() as session, session.begin():
        first = ensure_state(session)
        installation_id = first.installation_id
        payload = heartbeat_payload(first)
        assert set(payload) == {
            "installation_id",
            "hoardarr_version",
            "build_commit",
            "schema_version",
            "platform_family",
            "heartbeat_at",
        }
        enqueue_heartbeat(session, settings)
        enqueue_heartbeat(session, settings)
    with factory() as session:
        assert ensure_state(session).installation_id == installation_id
        assert session.query(FleetTelemetryQueue).count() == 1


def test_drive_pseudonym_is_cross_installation_and_serial_is_never_transmitted() -> None:
    drive = {
        "identity": {"wwn": "5000C500AABBCCDD"},
        "serial": "PRIVATE-SERIAL-A93F",
        "vendor": "Example",
        "model": "SSD-1",
    }
    first, source = pseudonymous_drive_id(drive)
    second, _source = pseudonymous_drive_id({**drive, "identity": {"wwn": "5000c500aabbccdd"}})
    assert first == second
    assert source == "wwn"
    assert first and "5000" not in first
    factory, settings, _box = _runtime()
    with factory() as session, session.begin():
        _snapshot(session)
        enqueue_inventory(session, settings)
        encoded = json.dumps(pending_payloads(session), default=str, ensure_ascii=False).encode()
        assert b"PRIVATE-SERIAL" not in encoded
        assert b"/dev/sdz" not in encoded
        assert b"must-never-leave" not in encoded
        assert b"\xe2\x80\xa6A93F" in encoded


def test_opt_out_remains_distinct_from_required_heartbeat() -> None:
    factory, settings, _box = _runtime()
    with factory() as session, session.begin():
        state = ensure_state(session)
        state.hardware_enabled = False
        assert enqueue_inventory(session, settings) is None
        assert (
            enqueue_lifecycle_event(
                session, settings, event_type="drive_warning", details={"password": "no"}
            )
            is None
        )
        enqueue_heartbeat(session, settings)
    with factory() as session:
        items = pending_payloads(session)
        assert [item["telemetry_level"] for item in items] == [0]


def test_queue_is_bounded_and_prefers_lifecycle_over_old_heartbeats() -> None:
    factory, settings, _box = _runtime(max_records=100)
    # Settings enforces a product minimum of 100 records. Fill beyond it and
    # prove lower-value observations are discarded before lifecycle warnings.
    with factory() as session, session.begin():
        ensure_state(session)
        for index in range(105):
            enqueue(
                session,
                settings,
                message_type="observation",
                telemetry_level=1,
                payload={"index": index},
            )
        enqueue_lifecycle_event(
            session,
            settings,
            event_type="drive_warning",
            details={"drive_id": "pseudonym", "state": "warning"},
        )
    with factory() as session:
        records = session.scalars(select(FleetTelemetryQueue)).all()
        assert len(records) == 100
        assert any(record.payload_json.get("event_type") == "drive_warning" for record in records)


def test_registration_offline_retry_and_acknowledged_deletion_survive_sessions() -> None:
    factory, settings, box = _runtime()
    credential = "installation-secret-credential-1234567890"

    def register_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/register")
        return httpx.Response(200, json={"credential": credential})

    register_installation(factory, settings, box, transport=httpx.MockTransport(register_handler))
    with factory() as session, session.begin():
        enqueue_heartbeat(session, settings)
        enqueue_lifecycle_event(
            session,
            settings,
            event_type="drive_replaced",
            details={"drive_id": "pseudonym"},
        )

    def offline(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    assert deliver_batch(factory, settings, box, transport=httpx.MockTransport(offline))
    with factory() as session, session.begin():
        assert queue_summary(session)["queued_records"] == 2
        for record in session.scalars(select(FleetTelemetryQueue)):
            record.next_attempt_at = utc_now() - timedelta(seconds=1)

    def online(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        expected = hmac.new(credential.encode(), request.content, hashlib.sha256).hexdigest()
        assert request.headers["X-Hoardarr-Signature"] == f"v1={expected}"
        return httpx.Response(
            200,
            json={"acknowledged_record_ids": [item["id"] for item in body["records"]]},
        )

    assert deliver_batch(factory, settings, box, transport=httpx.MockTransport(online))
    with factory() as session:
        assert queue_summary(session)["queued_records"] == 0
        assert ensure_state(session).last_success_at is not None


def test_permanent_schema_rejection_moves_record_to_bounded_dead_letter() -> None:
    factory, settings, box = _runtime()
    with factory() as session, session.begin():
        state = ensure_state(session)
        install_credential(state, box, "installation-secret-credential-1234567890")
        enqueue_heartbeat(session, settings)

    transport = httpx.MockTransport(lambda _request: httpx.Response(422, json={"error": "schema"}))
    assert deliver_batch(factory, settings, box, transport=transport)
    with factory() as session:
        item = session.scalar(select(FleetTelemetryQueue))
        assert item is not None
        assert item.status == "dead_letter"
        assert item.last_error_json["code"] == "permanent_rejection"


def test_identity_reset_removes_credential_and_all_pending_records() -> None:
    factory, settings, box = _runtime()
    with factory() as session, session.begin():
        state = ensure_state(session)
        previous = state.installation_id
        install_credential(state, box, "installation-secret-credential-1234567890")
        enqueue_heartbeat(session, settings)
        reset_identity(session)
        assert state.installation_id != previous
        assert state.credential_ciphertext is None
    with factory() as session:
        assert session.query(FleetTelemetryQueue).count() == 0


def test_production_fleet_endpoint_cannot_disable_tls() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        Settings(fleet_telemetry_endpoint="http://hoardarr.com/api/telemetry/v1")
