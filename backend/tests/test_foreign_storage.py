from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from hoardarr.db.models import Base, HardwareSnapshot, Operation, StorageBackend, StorageGroup
from hoardarr.operations.service import document_hash
from hoardarr.storage.foreign import (
    ForeignStorageError,
    assess_foreign_storage,
    build_inspection_plan,
    build_migration_plan,
    normalize_archive_selection,
    persist_nas_evidence,
    persist_unraid_evidence,
    validate_inspection_plan,
    validate_migration_plan,
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


def _disk(
    identity: str,
    signature_type: str,
    *,
    signature_uuid: str | None = None,
    scan_status: str = "complete",
    system: bool = False,
    mountpoints: list[str] | None = None,
    removable: bool = False,
    transport: str | None = None,
) -> dict[str, object]:
    signature = {
        "type": signature_type,
        "usage": "filesystem" if signature_type in {"ext4", "xfs", "btrfs"} else "raid",
        "uuid": signature_uuid,
        "source": "fixture",
    }
    return {
        "id": identity,
        "stable_identity": True,
        "kernel_path": f"/dev/{identity.rsplit(':', 1)[-1]}",
        "model": "Archive disk",
        "identity": {
            "serial": f"SERIAL-{identity.rsplit(':', 1)[-1]}",
            "wwn": identity.removeprefix("wwn:") if identity.startswith("wwn:") else None,
            "eui64": None,
            "nguid": None,
        },
        "capacity_bytes": 8_000_000_000,
        "system_disk": system,
        "removable": removable,
        "connection": {"transport": transport, "protocol": None},
        "mountpoints": mountpoints or [],
        "partitions": [],
        "signatures": [signature],
        "signature_scan": {
            "status": scan_status,
            "source": "fixture",
            "reason": None,
        },
    }


def test_standalone_filesystem_is_a_non_mutating_review_candidate() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        snapshot = _snapshot(session, [_disk("wwn:archive", "ext4")])
        document = assess_foreign_storage(session, snapshot=snapshot)

    assert document["policy"] == {
        "default_access": "read_only",
        "automatic_mount": False,
        "automatic_assembly": False,
        "mutation_performed": False,
    }
    assert len(document["candidates"]) == 1
    candidate = document["candidates"][0]
    assert candidate["profile"] == "standalone_filesystem"
    assert candidate["filesystems"] == ["ext4"]
    assert candidate["confidence"] == "high"
    assert candidate["state"] == "ready"
    assert candidate["origin"]["name"] == "Not reported"
    assert candidate["modes"][0]["available"] is True
    assert candidate["modes"][1]["available"] is False
    assert candidate["mutation_performed"] is False


def test_external_filesystem_is_classified_as_read_only_archive_intake() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        snapshot = _snapshot(
            session,
            [_disk("wwn:usb-archive", "exfat", removable=True, transport="usb")],
        )
        candidate = assess_foreign_storage(session, snapshot=snapshot)["candidates"][0]

    assert candidate["archive_intake"] == {
        "state": "discovered_external",
        "default_access": "read_only",
        "reason": (
            "The connection is reported as removable or external. Hoardarr will treat it "
            "as archive intake and keep the source read-only."
        ),
    }
    assert candidate["members"][0]["connection"]["transport"] == "usb"
    assert candidate["modes"][0]["available"] is True


def test_inspection_plan_binds_source_identity_signature_and_limits() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        snapshot = _snapshot(session, [_disk("wwn:archive", "ext4", signature_uuid="fs-1")])
        candidate_id = assess_foreign_storage(session, snapshot=snapshot)["candidates"][0]["id"]
        plan = build_inspection_plan(session, snapshot=snapshot, candidate_id=candidate_id)

    assert validate_inspection_plan(plan) == plan
    assert plan["device"]["id"] == "wwn:archive"
    assert plan["source"] == {
        "kind": "whole_device",
        "kernel_path_at_preview": "/dev/archive",
        "partition_number": None,
        "filesystem_type": "ext4",
        "filesystem_uuid": "fs-1",
        "filesystem_label": None,
        "signature_source": "fixture",
        "read_only_options": ["ro", "noload", "nodev", "nosuid", "noexec"],
    }
    assert plan["persistent_mount"] is False
    assert plan["mutation_performed"] is False


def test_completed_inventory_is_returned_with_snapshot_freshness() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        snapshot = _snapshot(session, [_disk("wwn:archive", "ext4", signature_uuid="fs-1")])
        candidate_id = assess_foreign_storage(session, snapshot=snapshot)["candidates"][0]["id"]
        plan = build_inspection_plan(session, snapshot=snapshot, candidate_id=candidate_id)
        session.add(
            Operation(
                kind="storage.foreign.inspect",
                status="succeeded",
                actor_type="user",
                actor_id="owner",
                resource_type="foreign_storage",
                resource_id=candidate_id,
                request_sha256=document_hash(plan),
                request_json={"plan": plan},
                result_json={
                    "filesystem": {"type": "ext4", "uuid": "fs-1", "label": "Media"},
                    "inventory": {
                        "file_count": 42,
                        "total_bytes": 123_456,
                        "largest_file": {"path": "Movies/Feature.mkv", "bytes": 100_000},
                        "oldest_mtime_unix": 1_700_000_000,
                        "newest_mtime_unix": 1_710_000_000,
                        "extension_distribution": [{"extension": ".mkv", "files": 12}],
                        "case_collision_count": 0,
                        "unicode_collision_count": 0,
                        "read_errors": [],
                        "truncated": False,
                    },
                    "access": "read_only",
                    "persistent_mount": False,
                    "mutation_performed": False,
                },
            )
        )
        session.flush()
        current = assess_foreign_storage(session, snapshot=snapshot)["candidates"][0]

        changed_disk = _disk("wwn:archive", "ext4", signature_uuid="fs-2")
        changed_snapshot = _snapshot(session, [changed_disk])
        stale = assess_foreign_storage(session, snapshot=changed_snapshot)["candidates"][0]

    assert current["latest_inventory"]["current_snapshot_match"] is True
    assert current["latest_inventory"]["inventory"]["file_count"] == 42
    assert current["latest_inventory"]["access"] == "read_only"
    assert current["latest_inventory"]["mutation_performed"] is False
    assert stale["latest_inventory"]["current_snapshot_match"] is False


def test_migration_plan_binds_current_inventory_and_managed_destination() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        snapshot = _snapshot(session, [_disk("wwn:archive", "ext4", signature_uuid="fs-1")])
        candidate_id = assess_foreign_storage(session, snapshot=snapshot)["candidates"][0]["id"]
        inspection = build_inspection_plan(session, snapshot=snapshot, candidate_id=candidate_id)
        inventory = {
            "file_count": 2,
            "total_bytes": 128,
            "largest_file": {"path": "Movies/Feature.mkv", "bytes": 96},
            "oldest_mtime_unix": 1_700_000_000,
            "newest_mtime_unix": 1_710_000_000,
            "extension_distribution": [{"extension": ".mkv", "files": 1}],
            "case_collision_count": 0,
            "unicode_collision_count": 0,
            "read_errors": [],
            "truncated": False,
        }
        source_report = Operation(
            kind="storage.foreign.inspect",
            status="succeeded",
            actor_type="user",
            actor_id="owner",
            resource_type="foreign_storage",
            resource_id=candidate_id,
            request_sha256=document_hash(inspection),
            request_json={"plan": inspection},
            result_json={
                "filesystem": {"type": "ext4", "uuid": "fs-1", "label": "Media"},
                "inventory": inventory,
                "access": "read_only",
                "persistent_mount": False,
                "mutation_performed": False,
            },
        )
        group = StorageGroup(name="Media", namespace_path="/", purpose="media")
        session.add_all([source_report, group])
        session.flush()
        destination = StorageBackend(
            storage_group_id=group.id,
            stable_identity="managed:test-destination",
            namespace_path="/",
            role="data",
            lifecycle_state="preferred_write",
        )
        session.add(destination)
        session.flush()
        plan = build_migration_plan(
            session,
            snapshot=snapshot,
            candidate_id=candidate_id,
            destination_backend_id=destination.id,
            verification_mode="accurate",
            collision_policy="stop",
            reserve_bytes=0,
        )

    assert validate_migration_plan(plan) == plan
    assert plan["source_inventory_operation_id"] == source_report.id
    assert plan["source_inventory_sha256"] == document_hash(inventory)
    assert plan["destination"]["stable_identity"] == "managed:test-destination"
    assert plan["verification"] == {"mode": "accurate", "algorithm": "blake3"}
    assert plan["source_retained"] is True
    assert plan["parity_reuse_supported"] is False
    assert plan["schema_version"] == 2
    assert plan["selection"] == {
        "mode": "full",
        "include_paths": [],
        "include_extensions": [],
        "include_globs": [],
        "exclude_globs": [],
        "capacity_upper_bound_bytes": inventory["total_bytes"],
        "exact_selected_bytes_at_review": inventory["total_bytes"],
    }


def test_archive_selection_is_bounded_normalized_and_rejects_traversal() -> None:
    assert normalize_archive_selection(
        {
            "mode": "filtered",
            "include_paths": [],
            "include_extensions": [".MKV", ".mkv"],
            "include_globs": ["Movies/*"],
            "exclude_globs": ["Movies/Samples/*"],
        }
    ) == {
        "mode": "filtered",
        "include_paths": [],
        "include_extensions": [".mkv"],
        "include_globs": ["Movies/*"],
        "exclude_globs": ["Movies/Samples/*"],
    }
    for invalid in (
        {"mode": "selected_folders", "include_paths": ["../etc"]},
        {"mode": "full", "include_paths": ["Movies"]},
        {"mode": "filtered"},
    ):
        with pytest.raises(ForeignStorageError) as failure:
            normalize_archive_selection(invalid)
        assert failure.value.code == "foreign_selection_invalid"


def test_migration_plan_rejects_parity_and_tampering() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        parity = _disk("wwn:parity", "ext4")
        parity["signatures"] = []
        parity["signature_scan"] = {"status": "complete", "source": "wipefs", "reason": None}
        snapshot = _snapshot(session, [parity])
        persist_unraid_evidence(
            session,
            created_by="owner",
            document={
                "schema_version": 1,
                "source": "unraid_runtime_state",
                "captured_at": "2026-08-23T20:00:00Z",
                "unraid_version": "7.2.0",
                "assignments": [
                    {
                        "slot": "parity",
                        "role": "parity",
                        "serial": "SERIAL-parity",
                        "wwn": "parity",
                        "capacity_bytes": 8_000_000_000,
                        "filesystem_type": None,
                    }
                ],
            },
        )
        candidate_id = assess_foreign_storage(session, snapshot=snapshot)["candidates"][0]["id"]
        group = StorageGroup(name="Media", namespace_path="/", purpose="media")
        session.add(group)
        session.flush()
        destination = StorageBackend(
            storage_group_id=group.id,
            stable_identity="managed:destination",
            namespace_path="/",
            lifecycle_state="active",
        )
        session.add(destination)
        session.flush()
        try:
            build_migration_plan(
                session,
                snapshot=snapshot,
                candidate_id=candidate_id,
                destination_backend_id=destination.id,
                verification_mode="accurate",
                collision_policy="stop",
                reserve_bytes=0,
            )
        except ForeignStorageError as exc:
            assert exc.code == "foreign_parity_not_importable"
        else:
            raise AssertionError("parity was accepted as file content")


def test_inspection_plan_rejects_tampering() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        snapshot = _snapshot(session, [_disk("wwn:archive", "xfs", signature_uuid="fs-2")])
        candidate_id = assess_foreign_storage(session, snapshot=snapshot)["candidates"][0]["id"]
        plan = build_inspection_plan(session, snapshot=snapshot, candidate_id=candidate_id)
    plan["source"]["read_only_options"] = ["ro"]

    try:
        validate_inspection_plan(plan)
    except ForeignStorageError as exc:
        assert exc.code == "foreign_plan_invalid"
    else:  # pragma: no cover - makes the safety assertion explicit.
        raise AssertionError("tampered mount options were accepted")


def test_partition_filesystem_plan_uses_the_reviewed_partition_not_parent_disk() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        disk = _disk("wwn:partitioned", "gpt")
        disk["signatures"][0]["usage"] = "partition_table"  # type: ignore[index]
        filesystem = {
            "type": "ext4",
            "usage": "filesystem",
            "uuid": "partition-fs",
            "label": "Media",
            "source": "wipefs",
        }
        disk["partitions"] = [
            {
                "kernel_path": "/dev/partitioned1",
                "number": 1,
                "mountpoints": [],
                "signatures": [filesystem],
                "filesystem": filesystem,
            }
        ]
        snapshot = _snapshot(session, [disk])
        candidate_id = assess_foreign_storage(session, snapshot=snapshot)["candidates"][0]["id"]
        plan = build_inspection_plan(session, snapshot=snapshot, candidate_id=candidate_id)

    assert plan["source"]["kind"] == "partition"
    assert plan["source"]["partition_number"] == 1
    assert plan["source"]["kernel_path_at_preview"] == "/dev/partitioned1"
    assert plan["source"]["filesystem_uuid"] == "partition-fs"


def test_partial_mounted_and_system_evidence_fails_closed() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        snapshot = _snapshot(
            session,
            [
                _disk(
                    "wwn:system",
                    "xfs",
                    scan_status="partial",
                    system=True,
                    mountpoints=["/"],
                )
            ],
        )
        document = assess_foreign_storage(session, snapshot=snapshot)

    candidate = document["candidates"][0]
    assert candidate["confidence"] == "medium"
    assert candidate["state"] == "blocked"
    assert any("system storage" in item for item in candidate["blockers"])
    assert any("already mounted" in item for item in candidate["blockers"])
    assert any("incomplete" in item for item in candidate["warnings"])


def test_linux_md_members_group_by_reported_uuid_without_assembly(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("hoardarr.storage.foreign.shutil.which", lambda _name: "/usr/bin/mdadm")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        snapshot = _snapshot(
            session,
            [
                _disk("wwn:md-a", "linux_raid_member", signature_uuid="md-array-1"),
                _disk("wwn:md-b", "linux_raid_member", signature_uuid="md-array-1"),
            ],
        )
        document = assess_foreign_storage(session, snapshot=snapshot)

    assert len(document["candidates"]) == 1
    candidate = document["candidates"][0]
    assert candidate["profile"] == "linux_md"
    assert len(candidate["members"]) == 2
    assert candidate["state"] == "ready"
    assert (
        next(item for item in candidate["modes"] if item["id"] == "inspect_read_only")["available"]
        is False
    )
    assert (
        next(item for item in candidate["modes"] if item["id"] == "preview_stack")["available"]
        is True
    )
    assert candidate["origin"]["confidence"] == "unknown"
    assert any("no array" in item.lower() for item in candidate["warnings"])
    assert candidate["blockers"] == []


def test_source_nas_runtime_evidence_identifies_complete_md_candidate() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        disks = [
            _disk("wwn:syno-a", "linux_raid_member", signature_uuid="md-data"),
            _disk("wwn:syno-b", "linux_raid_member", signature_uuid="md-data"),
        ]
        snapshot = _snapshot(session, disks)
        persist_nas_evidence(
            session,
            created_by="owner",
            document={
                "schema_version": 1,
                "source": "nas_runtime_state",
                "captured_at": "2026-08-23T20:00:00Z",
                "platform": "synology",
                "platform_marker": "synology_runtime",
                "product_version": "7.2.2",
                "members": [
                    {
                        "member": "drive1",
                        "serial": "SERIAL-syno-a",
                        "wwn": "syno-a",
                        "capacity_bytes": 8_000_000_000,
                    },
                    {
                        "member": "drive2",
                        "serial": "SERIAL-syno-b",
                        "wwn": "syno-b",
                        "capacity_bytes": 8_000_000_000,
                    },
                ],
            },
        )
        document = assess_foreign_storage(session, snapshot=snapshot)

    candidate = document["candidates"][0]
    assert candidate["origin"]["name"] == "Synology DSM"
    assert candidate["origin"]["confidence"] == "high"
    assert candidate["nas_origin"]["members"] == ["drive1", "drive2"]
    assert candidate["profile_name"] == "Identified Synology DSM Linux MD stack"
    assert document["nas_evidence"]["matched_member_count"] == 2


def test_partial_nas_identity_evidence_does_not_manufacture_vendor_origin() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        snapshot = _snapshot(
            session,
            [
                _disk("wwn:qnap-a", "linux_raid_member", signature_uuid="md-data"),
                _disk("wwn:qnap-b", "linux_raid_member", signature_uuid="md-data"),
            ],
        )
        persist_nas_evidence(
            session,
            created_by="owner",
            document={
                "schema_version": 1,
                "source": "nas_runtime_state",
                "captured_at": "2026-08-23T20:00:00Z",
                "platform": "qnap",
                "platform_marker": "qnap_runtime",
                "product_version": None,
                "members": [
                    {"member": "disk1", "serial": "SERIAL-qnap-a", "wwn": "qnap-a"}
                ],
            },
        )
        document = assess_foreign_storage(session, snapshot=snapshot)

    candidate = document["candidates"][0]
    assert candidate["origin"]["name"] == "Not reported"
    assert candidate["nas_origin"] is None
    assert any("only part" in warning for warning in candidate["warnings"])


def test_unrecognized_media_remains_unknown_instead_of_being_called_empty() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        disk = _disk("wwn:unknown", "ext4")
        disk["signatures"] = []
        disk["signature_scan"] = {
            "status": "unavailable",
            "source": "sysfs",
            "reason": "udev data unavailable",
        }
        snapshot = _snapshot(session, [disk])
        document = assess_foreign_storage(session, snapshot=snapshot)

    assert len(document["candidates"]) == 1
    assert document["candidates"][0]["profile"] == "unraid_unknown"
    assert document["candidates"][0]["unraid"]["classification"] == "unknown"
    assert document["candidates"][0]["unraid"]["role"] == "unknown"
    assert document["unrecognized_device_count"] == 1


def test_unraid_assignment_evidence_identifies_data_and_parity_by_stable_identity() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        data = _disk("wwn:data-one", "xfs")
        parity = _disk("wwn:parity-one", "ext4")
        parity["signatures"] = []
        parity["signature_scan"] = {"status": "complete", "source": "wipefs", "reason": None}
        snapshot = _snapshot(session, [data, parity])
        persist_unraid_evidence(
            session,
            created_by="owner",
            document={
                "schema_version": 1,
                "source": "unraid_runtime_state",
                "captured_at": "2026-08-23T20:00:00Z",
                "unraid_version": "7.2.0",
                "assignments": [
                    {
                        "slot": "disk1",
                        "role": "data",
                        "serial": "SERIAL-data-one",
                        "wwn": "data-one",
                        "capacity_bytes": 8_000_000_000,
                        "filesystem_type": "xfs",
                    },
                    {
                        "slot": "parity",
                        "role": "parity",
                        "serial": "SERIAL-parity-one",
                        "wwn": "parity-one",
                        "capacity_bytes": 8_000_000_000,
                        "filesystem_type": None,
                    },
                ],
            },
        )
        document = assess_foreign_storage(session, snapshot=snapshot)

    assert document["unraid_evidence"]["matched_assignment_count"] == 2
    classified = {item["unraid"]["role"]: item for item in document["candidates"]}
    assert classified["data"]["origin"]["name"] == "Unraid"
    assert classified["data"]["unraid"]["classification"] == "identified"
    assert classified["parity"]["profile_name"] == "Identified Unraid parity disk"
    assert classified["parity"]["unraid"]["parity_reuse_supported"] is False
    assert not any(mode["available"] for mode in classified["parity"]["modes"])


def test_signature_free_capacity_match_is_only_suspected_parity() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        data = _disk("wwn:data", "xfs")
        possible_parity = _disk("wwn:unknown-large", "ext4")
        possible_parity["signatures"] = []
        possible_parity["signature_scan"] = {
            "status": "complete",
            "source": "wipefs",
            "reason": None,
        }
        snapshot = _snapshot(session, [data, possible_parity])
        document = assess_foreign_storage(session, snapshot=snapshot)

    candidate = next(item for item in document["candidates"] if item["profile"] == "unraid_unknown")
    assert candidate["unraid"]["role"] == "parity"
    assert candidate["unraid"]["classification"] == "suspected"
    assert candidate["origin"]["name"] == "Not reported"
    assert "could also be blank" in candidate["unraid"]["reason"]


def test_conflicting_wwn_prevents_serial_only_assignment_match() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        data = _disk("wwn:actual", "xfs")
        snapshot = _snapshot(session, [data])
        persist_unraid_evidence(
            session,
            created_by="owner",
            document={
                "schema_version": 1,
                "source": "unraid_runtime_state",
                "captured_at": "2026-08-23T20:00:00Z",
                "unraid_version": None,
                "assignments": [
                    {
                        "slot": "disk1",
                        "role": "data",
                        "serial": "SERIAL-actual",
                        "wwn": "different-wwn",
                    }
                ],
            },
        )
        document = assess_foreign_storage(session, snapshot=snapshot)

    assert document["unraid_evidence"]["matched_assignment_count"] == 0
    assert document["unraid_evidence"]["unmatched_slots"] == ["disk1"]
    assert document["candidates"][0]["origin"]["name"] == "Not reported"
    assert document["candidates"][0]["unraid"]["classification"] == "suspected"
