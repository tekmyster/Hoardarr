from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from hoardarr.db.models import (
    Base,
    HardwareSnapshot,
    Operation,
    TopologyDriftEvent,
)
from hoardarr.hardware.topology_expectations import (
    compare_topology,
    create_topology_expectation,
    reconcile_topology_snapshot,
)
from hoardarr.operations.service import document_hash


def _hardware(*, include_path: bool = True, slot: str = "03", rate: float = 12.0) -> dict:
    connection = {
        "controller_address": "0000:01:00.0",
        "protocol": "sas",
        "transport": "sas",
        "enclosure_id": "500a098000000424",
        "enclosure_model": "DS424IOM6",
        "slot": slot,
        "path_id": "end_device-6:0:3" if include_path else None,
        "negotiated_speed_gbps": rate,
        "capable_speed_gbps": 12.0,
    }
    return {
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
                "id": "wwn:5000c50012345678",
                "kernel_path": "/dev/sdb",
                "model": "TEST",
                "identity": {"serial": "SANITIZED"},
                "connection": connection,
            }
        ],
    }


def _snapshot(session: Session, payload: dict, sequence: int) -> HardwareSnapshot:
    operation = Operation(
        id=f"00000000-0000-0000-0000-{sequence:012d}",
        kind="hardware.scan",
        status="succeeded",
        actor_type="session",
        actor_id="owner",
        request_sha256="0" * 64,
        request_json={},
    )
    session.add(operation)
    snapshot = HardwareSnapshot(
        operation_id=operation.id,
        detector_schema_version=1,
        source="fixture",
        payload_json=payload,
        sha256=document_hash(payload),
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def test_compare_topology_detects_moved_bay_missing_path_and_degraded_rate() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        baseline = _snapshot(session, _hardware(), 1)
        expectation = create_topology_expectation(
            session, snapshot=baseline, name="Expected media shelf", created_by="owner"
        )
        observed = _hardware(include_path=False, slot="04", rate=6.0)
        kinds = {item["kind"] for item in compare_topology(expectation.expected_json, observed)}
        assert {"missing_path", "drive_moved", "link_rate_degraded"} <= kinds


def test_reconcile_opens_one_episode_updates_it_and_resolves_on_recovery() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        baseline = _snapshot(session, _hardware(), 1)
        create_topology_expectation(
            session, snapshot=baseline, name="Expected media shelf", created_by="owner"
        )
        degraded = _snapshot(session, _hardware(rate=6.0), 2)
        first = reconcile_topology_snapshot(session, degraded)
        assert first["opened"] >= 1
        active_count = len(
            list(
                session.scalars(
                    select(TopologyDriftEvent).where(TopologyDriftEvent.state == "active")
                )
            )
        )
        repeated = _snapshot(session, _hardware(rate=6.0), 3)
        second = reconcile_topology_snapshot(session, repeated)
        assert second["opened"] == 0
        assert len(
            list(
                session.scalars(
                    select(TopologyDriftEvent).where(TopologyDriftEvent.state == "active")
                )
            )
        ) == active_count

        recovered = _snapshot(session, _hardware(), 4)
        result = reconcile_topology_snapshot(session, recovered)
        assert result["active"] == 0
        assert result["resolved"] == active_count
        assert session.scalars(
            select(TopologyDriftEvent).where(TopologyDriftEvent.state == "active")
        ).first() is None


def test_new_controller_is_information_not_a_missing_device_failure() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        baseline = _snapshot(session, _hardware(), 1)
        expectation = create_topology_expectation(
            session, snapshot=baseline, name="Expected media shelf", created_by="owner"
        )
        observed = _hardware()
        observed["controllers"].append(
            {
                "address": "0000:02:00.0",
                "bus_type": "pci",
                "provider": {"name": "New HBA"},
            }
        )
        observed["disks"].append(
            {
                "id": "wwn:5000c50099999999",
                "kernel_path": "/dev/sdc",
                "model": "NEW PATH",
                "identity": {"serial": "SANITIZED-NEW"},
                "connection": {
                    "controller_address": "0000:02:00.0",
                    "protocol": "sas",
                    "transport": "sas",
                    "path_id": "end_device-7:0:1",
                },
            }
        )
        additions = [
            item
            for item in compare_topology(expectation.expected_json, observed)
            if item["kind"] == "new_controller"
        ]
        assert len(additions) == 1
        assert additions[0]["severity"] == "info"
