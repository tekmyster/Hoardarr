from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from hoardarr.auth.service import Principal
from hoardarr.db.models import Base, HardwareSnapshot, Operation
from hoardarr.operations.service import document_hash
from hoardarr.storage.expansion import FilesystemUsage, build_expansion_assessment
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
        member_two, _ = register_disk(
            session,
            {
                "stable_identity": "wwn:member-two",
                "kernel_path": "/dev/sdc",
                "capacity_bytes": 4_000_000_000,
                "media_type": "hdd",
                "health_state": "healthy",
            },
        )
        second_backend = assign_backend(
            session,
            group_id=group.id,
            physical_disk_id=member_two.id,
            storage_entity_id=None,
            namespace_path="/srv/hoardarr/backends/member-two",
            role="data",
            principal=actor,
        )
        transition_backend(
            session,
            group_id=group.id,
            backend_id=second_backend.id,
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
            [
                _observation("wwn:current"),
                _observation("wwn:member-two"),
                _observation("wwn:fresh"),
            ],
        )
        result = build_expansion_assessment(
            session,
            snapshot=snapshot,
            storage_inventory={
                "pools": {
                    "items": [
                        {
                            "id": "mergerfs:0123456789abcdef",
                            "type": "mergerFS",
                            "mountpoint": "/srv/hoardarr/media",
                            "branches": [
                                "/srv/hoardarr/backends/current",
                                "/srv/hoardarr/backends/member-two",
                            ],
                        },
                        {"type": "SnapRAID"},
                    ]
                }
            },
            filesystem_probe=lambda path: {
                "/srv/hoardarr/media": FilesystemUsage(900, 4_000, 1_500, 2_500),
                "/srv/hoardarr/backends/current": FilesystemUsage(
                    901, 4_000, 3_000, 1_000
                ),
                "/srv/hoardarr/backends/member-two": FilesystemUsage(
                    902, 4_000, 1_000, 3_000
                ),
            }[path],
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
        assert candidates["add_mergerfs_member"]["target"] == {
            "provider": "mergerfs",
            "instance_id": "mergerfs:0123456789abcdef",
            "mountpoint": "/srv/hoardarr/media",
        }
        assert candidates["add_download_tier"]["setup_mode"] == "cache"
        assert candidates["new_storage_group"]["recommended"] is False
        current_state = result["storage_groups"][0]
        assert current_state["capacity"] == {
            "total_bytes": 4_000,
            "used_bytes": 1_500,
            "free_bytes": 2_500,
            "quality": "available",
            "source": "statvfs Storage Group namespace",
        }
        assert current_state["distribution"]["reported_members"] == 2
        assert current_state["distribution"]["minimum_utilization_percent"] == 25
        assert current_state["distribution"]["maximum_utilization_percent"] == 75
        assert current_state["distribution"]["spread_percentage_points"] == 50
        assert current_state["protection"]["summary"] == (
            "No parity backend is configured in this Storage Group."
        )


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
        assert mirror["configuration"] == {
            "topology": "zfs",
            "vdev_type": "mirror",
            "vdev_width": 2,
        }


def test_five_matched_blank_disks_offer_source_backed_raidz_geometry_math() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        observations = []
        for index in range(5):
            identity = f"wwn:raidz-{index}"
            register_disk(
                session,
                {
                    "stable_identity": identity,
                    "kernel_path": f"/dev/test{index}",
                    "capacity_bytes": 10_000_000_000,
                    "media_type": "hdd",
                    "health_state": "healthy",
                },
            )
            observations.append(_observation(identity))
        result = build_expansion_assessment(
            session, snapshot=_snapshot(session, observations)
        )
        candidates = {item["kind"]: item for item in result["candidates"]}
        assert candidates["new_zfs_raidz1"]["capacity"][
            "estimated_usable_delta_bytes"
        ] == 40_000_000_000
        assert candidates["new_zfs_raidz2"]["capacity"][
            "estimated_usable_delta_bytes"
        ] == 30_000_000_000
        assert candidates["new_zfs_raidz3"]["capacity"][
            "estimated_usable_delta_bytes"
        ] == 20_000_000_000
        assert candidates["new_zfs_raidz2"]["configuration"] == {
            "topology": "zfs",
            "vdev_type": "raidz2",
            "vdev_width": 5,
        }


def test_system_disk_is_visible_as_protected_but_never_becomes_a_candidate() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        disk, _ = register_disk(
            session,
            {
                "stable_identity": "wwn:system",
                "kernel_path": "/dev/sda",
                "capacity_bytes": 100_000_000_000,
                "media_type": "ssd",
                "health_state": "healthy",
            },
        )
        observation = _observation(
            "wwn:system", signatures=[{"type": "ext4", "usage": "filesystem"}]
        )
        observation["system_disk"] = True
        result = build_expansion_assessment(
            session,
            snapshot=_snapshot(session, [observation]),
        )
        protected = result["available_disks"][0]
        assert protected["id"] == disk.id
        assert protected["eligible"] is False
        assert protected["blockers"] == [
            "Protected system storage cannot be used for expansion or import."
        ]
        assert result["candidates"] == []


def test_reserved_disk_is_reported_but_excluded_from_candidates() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        disk, _ = register_disk(
            session,
            {
                "stable_identity": "wwn:reserved",
                "kernel_path": "/dev/sdz",
                "capacity_bytes": 1_000_000_000,
                "health_state": "healthy",
            },
        )
        disk.lifecycle_state = "reserved"
        result = build_expansion_assessment(
            session,
            snapshot=_snapshot(session, [_observation("wwn:reserved")]),
        )
        assert result["available_disks"] == []
        assert [item["id"] for item in result["reserved_disks"]] == [disk.id]
        assert result["candidates"] == []
