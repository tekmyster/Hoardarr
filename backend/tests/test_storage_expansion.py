from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from hoardarr.auth.service import Principal
from hoardarr.db.models import Base, HardwareSnapshot, Operation
from hoardarr.operations.service import document_hash
from hoardarr.storage.expansion import build_expansion_assessment
from hoardarr.storage.groups import assign_backend, create_group, register_disk, transition_backend


def _principal() -> Principal:
    return Principal(
        user_id="11111111-1111-1111-1111-111111111111",
        username="owner",
        is_admin=True,
        auth_type="session",
        scopes=frozenset({"operate"}),
    )


def _snapshot(session: Session, disks: list[dict[str, object]]) -> HardwareSnapshot:
    payload = {"schema_version": 1, "source": {"kind": "fixture"}, "disks": disks}
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


def _observation(
    identity: str, *, signatures: list[dict[str, str]] | None = None
) -> dict[str, object]:
    return {
        "id": identity,
        "stable_identity": True,
        "kernel_path": f"/dev/{identity.rsplit(':', 1)[-1]}",
        "partitions": [] if not signatures else [{"path": "/dev/test1"}],
        "signatures": signatures or [],
        "signature_scan": {"status": "complete", "source": "wipefs", "reason": None},
    }


def test_existing_data_is_import_first_and_never_given_fake_usable_capacity() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        disk, _ = register_disk(
            session,
            {
                "stable_identity": "wwn:archive",
                "kernel_path": "/dev/sdb",
                "capacity_bytes": 8_000_000_000,
                "media_type": "hdd",
                "health_state": "not_reported",
            },
        )
        snapshot = _snapshot(
            session,
            [_observation("wwn:archive", signatures=[{"type": "xfs", "usage": "filesystem"}])],
        )
        result = build_expansion_assessment(session, snapshot=snapshot)
        assert result["available_disks"][0]["id"] == disk.id
        assert result["available_disks"][0]["existing_data"]["state"] == "detected"
        assert [item["kind"] for item in result["candidates"]] == ["import_existing"]
        assert result["candidates"][0]["capacity"]["estimated_usable_delta_bytes"] is None


def test_media_group_gets_real_mergerfs_and_download_tier_candidates() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        actor = _principal()
        group = create_group(
            session,
            name="Media",
            namespace_path="/srv/hoardarr/media",
            purpose="media",
            principal=actor,
        )
        current, _ = register_disk(
            session,
            {
                "stable_identity": "wwn:current",
                "kernel_path": "/dev/sda",
                "capacity_bytes": 4_000_000_000,
                "media_type": "hdd",
                "health_state": "healthy",
            },
        )
        backend = assign_backend(
            session,
            group_id=group.id,
            physical_disk_id=current.id,
            storage_entity_id=None,
            namespace_path="/srv/hoardarr/backends/current",
            role="data",
            principal=actor,
        )
        transition_backend(
            session,
            group_id=group.id,
            backend_id=backend.id,
            target_state="active",
            principal=actor,
            reason="fixture",
        )
        fresh, _ = register_disk(
            session,
            {
                "stable_identity": "wwn:fresh",
                "kernel_path": "/dev/sdb",
                "capacity_bytes": 2_000_000_000,
                "media_type": "ssd",
                "health_state": "healthy",
            },
        )
        snapshot = _snapshot(
            session,
            [_observation("wwn:current"), _observation("wwn:fresh")],
        )
        result = build_expansion_assessment(
            session,
            snapshot=snapshot,
            storage_inventory={
                "pools": {
                    "items": [
                        {"type": "mergerFS"},
                        {"type": "SnapRAID"},
                    ]
                }
            },
        )
        available = result["available_disks"]
        assert [item["id"] for item in available] == [fresh.id]
        candidates = {item["kind"]: item for item in result["candidates"]}
        assert candidates["add_mergerfs_member"]["recommended"] is True
        assert (
            candidates["add_mergerfs_member"]["capacity"]["estimated_usable_delta_bytes"]
            == 2_000_000_000
        )
        assert "resynchronized" in candidates["add_mergerfs_member"]["protection_impact"]
        assert candidates["add_download_tier"]["setup_mode"] == "cache"
        assert candidates["new_storage_group"]["recommended"] is False


def test_two_matched_blank_disks_produce_explicit_mirror_math() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        observations = []
        for suffix, capacity in (("one", 10_000_000_000), ("two", 9_900_000_000)):
            register_disk(
                session,
                {
                    "stable_identity": f"wwn:{suffix}",
                    "kernel_path": f"/dev/{suffix}",
                    "capacity_bytes": capacity,
                    "media_type": "hdd",
                    "health_state": "healthy",
                },
            )
            observations.append(_observation(f"wwn:{suffix}"))
        snapshot = _snapshot(session, observations)
        result = build_expansion_assessment(session, snapshot=snapshot)
        mirror = next(item for item in result["candidates"] if item["kind"] == "new_zfs_mirror")
        assert mirror["capacity"]["raw_delta_bytes"] == 19_900_000_000
        assert mirror["capacity"]["estimated_usable_delta_bytes"] == 9_900_000_000
        assert mirror["recommended"] is False
        assert len(mirror["disk_ids"]) == 2
