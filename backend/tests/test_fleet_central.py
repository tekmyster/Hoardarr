from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from hoardarr.core.config import Settings
from hoardarr.core.secrets import SecretBox
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.db.models import FleetTelemetryQueue, HardwareSnapshot, Operation, utc_now
from hoardarr.fleet.central import (
    FleetCentralSettings,
    InstallationHeartbeat,
    create_central_app,
    run_retention,
)
from hoardarr.fleet.service import (
    canonical_json,
    deliver_batch,
    enqueue_heartbeat,
    enqueue_inventory,
    register_installation,
)


def _central(tmp_path: Path) -> TestClient:
    database = tmp_path / "central.db"
    app = create_central_app(
        FleetCentralSettings(
            database_url=f"sqlite:///{database.as_posix()}",
            secret_key_file=tmp_path / "central.key",
            admin_token="test-admin-token-that-is-long",
        )
    )
    return TestClient(app)


def _register(client: TestClient, installation_id: str) -> str:
    response = client.post(
        "/api/telemetry/v1/register",
        json={
            "installation_id": installation_id,
            "hoardarr_version": "0.3.11",
            "build_commit": "a" * 40,
            "schema_version": 1,
            "platform_family": "linux",
            "heartbeat_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["credential"])


def _record(message_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "id": str(uuid.uuid4()),
        "message_type": message_type,
        "telemetry_level": 0 if message_type == "heartbeat" else 1,
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "payload_sha256": hashlib.sha256(canonical_json(payload)).hexdigest(),
        "payload": payload,
    }


def _batch(
    installation_id: str, sequence: int, records: list[dict[str, object]]
) -> dict[str, object]:
    return {
        "installation_id": installation_id,
        "schema_version": 1,
        "sequence_number": sequence,
        "batch_id": str(uuid.uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "payload_digest": hashlib.sha256(canonical_json(records)).hexdigest(),
        "records": records,
    }


def _send(client: TestClient, credential: str, body: dict[str, object]):
    raw = canonical_json(body)
    signature = hmac.new(credential.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/api/telemetry/v1/batch",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hoardarr-Signature": f"v1={signature}"},
    )


def _central_transport(client: TestClient) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        response = client.request(
            request.method,
            request.url.path,
            content=request.content,
            headers=dict(request.headers),
        )
        return httpx.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
            request=request,
        )

    return httpx.MockTransport(handler)


def _client_runtime(tmp_path: Path, name: str):
    database = tmp_path / f"{name}.db"
    key = tmp_path / f"{name}.key"
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{database.as_posix()}",
        secret_key_file=key,
        fleet_telemetry_endpoint="https://fleet.test/api/telemetry/v1",
        fleet_heartbeat_interval_seconds=900,
    )
    upgrade_database(settings.database_url)
    engine = create_database_engine(settings.database_url)
    return settings, engine, create_session_factory(engine), SecretBox.from_file(key, create=True)


def test_registration_is_per_installation_and_cannot_be_silently_reissued(tmp_path: Path) -> None:
    with _central(tmp_path) as client:
        installation_id = str(uuid.uuid4())
        credential = _register(client, installation_id)
        assert len(credential) >= 32
        repeated = client.post(
            "/api/telemetry/v1/register",
            json={
                "installation_id": installation_id,
                "hoardarr_version": "0.3.11",
                "schema_version": 1,
                "platform_family": "linux",
                "heartbeat_at": datetime.now(UTC).isoformat(),
            },
        )
        assert repeated.status_code == 409
        assert "credential" not in repeated.text


def test_authenticated_batch_rejects_tampering_replay_and_bad_record_digest(tmp_path: Path) -> None:
    with _central(tmp_path) as client:
        installation_id = str(uuid.uuid4())
        credential = _register(client, installation_id)
        payload = {
            "installation_id": installation_id,
            "hoardarr_version": "0.3.11",
            "schema_version": 1,
            "platform_family": "linux",
            "heartbeat_at": datetime.now(UTC).isoformat(),
        }
        body = _batch(installation_id, 1, [_record("heartbeat", payload)])
        assert _send(client, "incorrect-credential-value", body).status_code == 401
        accepted = _send(client, credential, body)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["acknowledged_record_ids"] == [body["records"][0]["id"]]
        assert _send(client, credential, body).status_code == 409

        bad_record = _record("inventory", {"storage_hardware": []})
        bad_record["payload_sha256"] = "0" * 64
        malformed = _batch(installation_id, 2, [bad_record])
        assert _send(client, credential, malformed).status_code == 422
        corrected = _record("inventory", {"storage_hardware": []})
        retry = _batch(installation_id, 2, [corrected])
        assert _send(client, credential, retry).status_code == 200


def test_cross_installation_drive_lifecycle_and_admin_aggregate(tmp_path: Path) -> None:
    with _central(tmp_path) as client:
        shared_drive = hashlib.sha256(b"hoardarr-drive-v1\0wwn:naa.6000-test").hexdigest()
        for installation_id in (str(uuid.uuid4()), str(uuid.uuid4())):
            credential = _register(client, installation_id)
            payload = {
                "installation_id": installation_id,
                "schema_version": 1,
                "observed_at": datetime.now(UTC).isoformat(),
                "system": {"os": "Linux"},
                "storage_hardware": [
                    {
                        "drive_id": shared_drive,
                        "drive_identity_version": 1,
                        "vendor": "SanitizedVendor",
                        "model": "VirtualSSD",
                        "capacity_bytes": 10_000_000,
                    }
                ],
                "storage_configuration": {"backend_roles": ["media"]},
                "applications_detected": ["Plex", "Radarr"],
            }
            assert (
                _send(
                    client, credential, _batch(installation_id, 1, [_record("inventory", payload)])
                ).status_code
                == 200
            )
        denied = client.get("/api/admin/v1/fleet/summary")
        assert denied.status_code == 401
        summary = client.get(
            "/api/admin/v1/fleet/summary",
            headers={"X-Hoardarr-Admin-Token": "test-admin-token-that-is-long"},
        )
        assert summary.status_code == 200
        assert summary.json()["active_installations"] == 2
        assert summary.json()["drives_seen_in_multiple_installations"] == 1
        assert summary.json()["drives"]["models"] == [{"value": "VirtualSSD", "count": 1}]
        assert summary.json()["applications"] == [
            {"product": "Plex", "installations": 2},
            {"product": "Radarr", "installations": 2},
        ]
        assert "manufacturer failure-rate" in summary.json()["methodology"]


def test_admin_aggregates_memory_capacity_app_combinations_and_upgrades(
    tmp_path: Path,
) -> None:
    with _central(tmp_path) as client:
        installation_id = str(uuid.uuid4())
        credential = _register(client, installation_id)
        observed = datetime.now(UTC).isoformat()
        inventory = {
            "installation_id": installation_id,
            "schema_version": 1,
            "observed_at": observed,
            "system": {
                "cpu_vendor": "GenuineIntel",
                "installed_memory_bytes": 24 * 1024**3,
                "platform_vendor": "Virtual Lab",
            },
            "storage_hardware": [
                {
                    "drive_id": hashlib.sha256(b"aggregate-drive").hexdigest(),
                    "drive_identity_version": 1,
                    "vendor": "Virtual",
                    "model": "SSD",
                    "media_type": "ssd",
                    "capacity_bytes": 2 * 1024**4,
                    "enclosure_model": "Virtual Shelf",
                }
            ],
            "storage_configuration": {
                "backend_types": ["mergerfs", "snapraid"],
                "logical_capacity_bytes": 6 * 1024**4,
                "usable_capacity_bytes": 5 * 1024**4,
                "free_percent": 20.0,
                "controller_redundant_count": 1,
            },
            "controller_observations": {"models": ["scsi:Virtual HBA"], "count": 1},
            "applications_detected": ["Plex", "Radarr", "Sonarr"],
            "feature_usage": [],
        }
        first_heartbeat = {
            "installation_id": installation_id,
            "hoardarr_version": "0.3.11",
            "schema_version": 1,
            "platform_family": "linux",
            "heartbeat_at": observed,
        }
        assert (
            _send(
                client,
                credential,
                _batch(
                    installation_id,
                    1,
                    [_record("heartbeat", first_heartbeat), _record("inventory", inventory)],
                ),
            ).status_code
            == 200
        )
        upgraded = dict(first_heartbeat)
        upgraded["hoardarr_version"] = "0.3.12"
        upgraded["heartbeat_at"] = datetime.now(UTC).isoformat()
        assert (
            _send(
                client,
                credential,
                _batch(installation_id, 2, [_record("heartbeat", upgraded)]),
            ).status_code
            == 200
        )

        response = client.get(
            "/api/admin/v1/fleet/summary",
            headers={"X-Hoardarr-Admin-Token": "test-admin-token-that-is-long"},
        )
        assert response.status_code == 200
        summary = response.json()
        assert summary["versions"] == [{"version": "0.3.12", "installations": 1}]
        assert summary["hardware"]["installed_memory"] == [
            {"value": "16_to_32_GiB", "count": 1}
        ]
        assert summary["hardware"]["platform_vendors"] == [
            {"value": "Virtual Lab", "count": 1}
        ]
        assert summary["hardware"]["controllers"] == [
            {"value": "scsi:Virtual HBA", "count": 1}
        ]
        assert summary["hardware"]["enclosures"] == [
            {"value": "Virtual Shelf", "count": 1}
        ]
        assert summary["drives"]["capacities"] == [
            {"value": "1_to_4_TiB", "count": 1}
        ]
        assert summary["storage"]["logical_capacity"] == [
            {"value": "4_to_8_TiB", "count": 1}
        ]
        assert summary["storage"]["free_space_percent"] == [
            {"value": "10_to_25_percent", "count": 1}
        ]
        assert summary["storage"]["controller_redundancy_installations"] == 1
        assert summary["application_combinations"] == [
            {"value": "Plex + Radarr + Sonarr", "count": 1}
        ]
        assert summary["upgrade_adoption"] == {
            "installations_with_observed_upgrade": 1,
            "sampled_installations": 1,
            "transitions": [{"transition": "0.3.11 → 0.3.12", "installations": 1}],
        }


def test_schema_and_body_limits_fail_closed(tmp_path: Path) -> None:
    with _central(tmp_path) as client:
        registration = client.post(
            "/api/telemetry/v1/register",
            content=json.dumps(
                {
                    "installation_id": str(uuid.uuid4()),
                    "hoardarr_version": "0.3.11",
                    "schema_version": 999,
                    "platform_family": "linux",
                    "heartbeat_at": datetime.now(UTC).isoformat(),
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        assert registration.status_code == 422
        oversized = client.post(
            "/api/telemetry/v1/batch",
            content=b"{" + b"x" * (513 * 1024),
            headers={"X-Hoardarr-Signature": "v1=invalid"},
        )
        assert oversized.status_code == 413


def test_real_client_store_and_forward_survives_outage_and_restart(tmp_path: Path) -> None:
    central = _central(tmp_path)
    with central:
        settings, engine, factory, secret_box = _client_runtime(tmp_path, "node-a")
        online = _central_transport(central)
        register_installation(factory, settings, secret_box, transport=online)
        with factory() as session, session.begin():
            enqueue_heartbeat(session, settings)

        def unavailable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("simulated internet outage", request=request)

        assert deliver_batch(
            factory,
            settings,
            secret_box,
            transport=httpx.MockTransport(unavailable),
        )
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(FleetTelemetryQueue)) == 1
        engine.dispose()

        restarted_engine = create_database_engine(settings.database_url)
        restarted_factory = create_session_factory(restarted_engine)
        with restarted_factory() as session, session.begin():
            record = session.scalar(select(FleetTelemetryQueue))
            assert record is not None
            assert record.status == "retrying"
            record.next_attempt_at = utc_now()
        assert deliver_batch(
            restarted_factory,
            settings,
            SecretBox.from_file(settings.secret_key_file, create=False),
            transport=online,
        )
        with restarted_factory() as session:
            assert session.scalar(select(func.count()).select_from(FleetTelemetryQueue)) == 0
        summary = central.get(
            "/api/admin/v1/fleet/summary",
            headers={"X-Hoardarr-Admin-Token": "test-admin-token-that-is-long"},
        ).json()
        assert summary["active_installations"] == 1
        restarted_engine.dispose()


def test_real_clients_preserve_drive_identity_across_two_installations(tmp_path: Path) -> None:
    central = _central(tmp_path)
    with central:
        for node in ("node-a", "node-b"):
            settings, engine, factory, secret_box = _client_runtime(tmp_path, node)
            with factory() as session, session.begin():
                operation = Operation(
                    kind="hardware.discovery",
                    status="succeeded",
                    actor_type="system",
                    actor_id="00000000-0000-0000-0000-000000000000",
                    request_sha256=hashlib.sha256(b"fleet-test").hexdigest(),
                    request_json={},
                )
                session.add(operation)
                session.flush()
                snapshot_payload = {
                    "disks": [
                        {
                            "wwn": "naa.6000-shared-virtual-drive",
                            "serial": "SANITIZED-0001",
                            "vendor": "SanitizedVendor",
                            "model": "VirtualSSD",
                            "size_bytes": 10_000_000,
                            "media_type": "ssd",
                        }
                    ]
                }
                session.add(
                    HardwareSnapshot(
                        operation_id=operation.id,
                        detector_schema_version=1,
                        source="sanitized-test-fixture",
                        payload_json=snapshot_payload,
                        sha256=hashlib.sha256(canonical_json(snapshot_payload)).hexdigest(),
                    )
                )
            transport = _central_transport(central)
            register_installation(factory, settings, secret_box, transport=transport)
            with factory() as session, session.begin():
                assert enqueue_inventory(session, settings) is not None
            assert deliver_batch(factory, settings, secret_box, transport=transport)
            engine.dispose()
        summary = central.get(
            "/api/admin/v1/fleet/summary",
            headers={"X-Hoardarr-Admin-Token": "test-admin-token-that-is-long"},
        ).json()
        assert summary["active_installations"] == 2
        assert summary["drives_seen_in_multiple_installations"] == 1


def test_central_retention_is_data_class_specific_and_bounded(tmp_path: Path) -> None:
    settings = FleetCentralSettings(
        database_url=f"sqlite:///{(tmp_path / 'retention.db').as_posix()}",
        secret_key_file=tmp_path / "retention.key",
        admin_token="test-admin-token-that-is-long",
        heartbeat_retention_days=30,
        retention_batch_size=10,
    )
    app = create_central_app(settings)
    with TestClient(app) as client:
        installation_id = str(uuid.uuid4())
        _register(client, installation_id)
        old = datetime.now(UTC) - timedelta(days=45)
        with app.state.sessions() as session, session.begin():
            for _ in range(12):
                session.add(
                    InstallationHeartbeat(
                        installation_id=installation_id,
                        version="0.3.10",
                        platform_family="linux",
                        observed_at=old,
                    )
                )
            session.add(
                InstallationHeartbeat(
                    installation_id=installation_id,
                    version="0.3.11",
                    platform_family="linux",
                    observed_at=datetime.now(UTC),
                )
            )
        with app.state.sessions() as session, session.begin():
            result = run_retention(session, settings, force=True)
            assert result["heartbeats"] == 10
        with app.state.sessions() as session:
            remaining = session.scalar(select(func.count()).select_from(InstallationHeartbeat))
            assert remaining == 3
        with app.state.sessions() as session, session.begin():
            second = run_retention(session, settings, force=True)
            assert second["heartbeats"] == 2
        summary = client.get(
            "/api/admin/v1/fleet/summary",
            headers={"X-Hoardarr-Admin-Token": "test-admin-token-that-is-long"},
        )
        assert summary.status_code == 200
        assert summary.json()["last_retention_run"] is not None
