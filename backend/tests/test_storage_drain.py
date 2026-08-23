from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from hoardarr.auth.service import Principal
from hoardarr.db.models import Base, IntegrationConnection, PhysicalDisk, StorageBackend
from hoardarr.storage.drain import (
    DrainPlanError,
    FilesystemFacts,
    build_drain_plan,
    inspect_filesystem,
    inspect_open_use,
    validate_drain_plan,
)
from hoardarr.storage.groups import assign_backend, create_group, register_disk, transition_backend


def _principal() -> Principal:
    return Principal(
        user_id="11111111-1111-1111-1111-111111111111",
        username="owner",
        is_admin=True,
        auth_type="session",
        scopes=frozenset({"operate"}),
    )


def _group_with_backends(session: Session) -> tuple[str, str, str]:
    actor = _principal()
    group = create_group(
        session,
        name="Media",
        namespace_path="/srv/hoardarr/media",
        purpose="media",
        principal=actor,
    )
    ids: list[str] = []
    for suffix in ("source", "destination"):
        disk, _created = register_disk(
            session,
            {
                "stable_identity": f"wwn:{suffix}",
                "health_state": "healthy",
                "capacity_bytes": 20_000_000_000,
            },
        )
        backend = assign_backend(
            session,
            group_id=group.id,
            physical_disk_id=disk.id,
            storage_entity_id=None,
            namespace_path=f"/srv/hoardarr/backends/{suffix}",
            role="data",
            principal=actor,
        )
        transition_backend(
            session,
            group_id=group.id,
            backend_id=backend.id,
            target_state="active",
            principal=actor,
            reason="test mount verified",
        )
        ids.append(backend.id)
    transition_backend(
        session,
        group_id=group.id,
        backend_id=ids[0],
        target_state="preferred_write",
        principal=actor,
        reason="test placement",
    )
    return group.id, ids[0], ids[1]


def _facts(path: str) -> FilesystemFacts:
    if path.endswith("source"):
        return FilesystemFacts(path, 101, 20_000, 8_000, 12_000)
    return FilesystemFacts(path, 202, 30_000, 1_000, 29_000)


def test_drain_preflight_is_immutable_and_exposes_methodology() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        group_id, source_id, destination_id = _group_with_backends(session)
        plan = build_drain_plan(
            session,
            group_id=group_id,
            source_backend_id=source_id,
            destination_backend_ids=[destination_id],
            verification_mode="paranoid",
            reserve_bytes=2_000,
            filesystem_probe=_facts,
            open_use_probe=lambda _path: {
                "quality": "available",
                "open_handles": 0,
                "processes": [],
            },
        )
        assert plan["ready"] is True
        assert plan["capacity"] == {
            "required_bytes": 8_000,
            "destination_free_bytes": 29_000,
            "reserve_bytes": 2_000,
        }
        assert plan["verification"] == {
            "mode": "paranoid",
            "full_hashes": True,
            "additional_read_pass": True,
        }
        assert plan["source"]["stable_identity"] == "disk:wwn:source"
        validate_drain_plan(plan)
        plan["capacity"]["required_bytes"] = 7_999
        try:
            validate_drain_plan(plan)
        except DrainPlanError as exc:
            assert exc.code == "drain_plan_changed"
        else:
            raise AssertionError("a modified drain plan passed integrity validation")


def test_drain_preflight_blocks_capacity_open_files_health_and_arr_activity() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        group_id, source_id, destination_id = _group_with_backends(session)
        destination = session.get(StorageBackend, destination_id)
        assert destination is not None and destination.physical_disk_id is not None
        disk = session.get(PhysicalDisk, destination.physical_disk_id)
        assert disk is not None
        disk.health_state = "critical"
        session.add(
            IntegrationConnection(
                name="Sonarr",
                expected_product="sonarr",
                base_url="http://sonarr.test",
                api_key_ciphertext=b"encrypted-test-key",
                status="connected",
                state_json={"active_writes": 2},
            )
        )
        session.flush()

        def insufficient(path: str) -> FilesystemFacts:
            if path.endswith("source"):
                return FilesystemFacts(path, 101, 20_000, 18_000, 2_000)
            return FilesystemFacts(path, 202, 20_000, 19_000, 1_000)

        plan = build_drain_plan(
            session,
            group_id=group_id,
            source_backend_id=source_id,
            destination_backend_ids=[destination_id],
            verification_mode="fast",
            reserve_bytes=1_000,
            filesystem_probe=insufficient,
            open_use_probe=lambda _path: {
                "quality": "available",
                "open_handles": 3,
                "processes": [{"pid": 123, "name": "radarr", "handles": 3}],
            },
        )
        assert plan["ready"] is False
        assert {item["code"] for item in plan["blockers"]} == {
            "arr_active_writes",
            "destination_capacity_insufficient",
            "destination_unhealthy",
            "source_in_use",
        }
        assert plan["arr_activity"]["active_writes"] == 2


def test_drain_preflight_rejects_duplicate_and_cross_group_destinations() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        group_id, source_id, destination_id = _group_with_backends(session)
        try:
            build_drain_plan(
                session,
                group_id=group_id,
                source_backend_id=source_id,
                destination_backend_ids=[destination_id, destination_id],
                verification_mode="accurate",
                reserve_bytes=0,
                filesystem_probe=_facts,
                open_use_probe=lambda _path: {},
            )
        except DrainPlanError as exc:
            assert exc.code == "destination_duplicate"
        else:
            raise AssertionError("duplicate destination was accepted")
        try:
            build_drain_plan(
                session,
                group_id=group_id,
                source_backend_id=source_id,
                destination_backend_ids=[destination_id],
                verification_mode="accurate",
                reserve_bytes=0,
                filesystem_probe=lambda path: FilesystemFacts(path, 101, 20_000, 8_000, 12_000),
                open_use_probe=lambda _path: {},
            )
        except DrainPlanError as exc:
            assert exc.code == "source_destination_filesystem_same"
        else:
            raise AssertionError("same-filesystem drain destination was accepted")


@pytest.mark.skipif(os.name != "posix", reason="requires Linux /proc and POSIX paths")
def test_linux_preflight_probes_real_filesystem_and_open_descriptor(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = source / "active-download.part"
    payload.write_bytes(b"active")
    facts = inspect_filesystem(str(source))
    assert facts.total_bytes > 0
    assert facts.free_bytes > 0
    assert facts.device_number == source.stat().st_dev
    with payload.open("rb"):
        activity = inspect_open_use(str(source))
        assert activity["quality"] == "available"
        assert activity["open_handles"] >= 1
        assert any(item["pid"] == os.getpid() for item in activity["processes"])
