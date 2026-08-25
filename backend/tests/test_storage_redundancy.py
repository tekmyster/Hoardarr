from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from hoardarr.db.models import (
    Base,
    ConnectivityService,
    MetricAlert,
    MetricEntity,
    MetricSample,
    PhysicalDisk,
    StorageBackend,
    StorageEntity,
    StorageGroup,
    StoragePath,
    StorageRedundancyEvent,
)
from hoardarr.operations.service import document_hash
from hoardarr.storage.executor import (
    ExecutorFailure,
    Paths,
    apply_storage_redundancy,
    storage_operation_status,
)
from hoardarr.storage.redundancy import (
    RedundancyError,
    apply_redundancy_result,
    build_redundancy_plan,
    reconcile_storage_path_health,
    redundancy_event_documents,
    register_completed_storage,
    register_single_path_storage,
    stable_path_identity,
    storage_documents,
)
from hoardarr.telemetry.alerts import evaluate_basic_alerts


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database:
        yield database


def _path(
    controller: str,
    kernel_path: str,
    *,
    wwid: str = "naa.600a098000abc",
    capacity: int = 8_000_000_000_000,
) -> dict[str, object]:
    return {
        "id": f"wwn:{wwid}",
        "kernel_path": kernel_path,
        "capacity_bytes": capacity,
        "identity": {"serial": "LUN-7", "wwn": wwid, "eui64": None, "nguid": None},
        "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
        "connection": {
            "transport": "fc",
            "protocol": "fc",
            "controller_address": controller,
            "target_port_wwn": f"50:00:{controller}",
        },
    }


def _registered(session: Session) -> tuple[StorageEntity, dict[str, object]]:
    first = _path("hba-a", "/dev/sdb")
    entity = register_single_path_storage(
        session,
        name="MediaPool",
        device=first,
        mountpoint="/media",
        presentation_device="/dev/sdb",
        filesystem_uuid="11111111-1111-4111-8111-111111111111",
    )
    entity.config_json = {**entity.config_json, "device_mountpoint": "/mnt/hoardarr/lun7"}
    session.flush()
    return entity, first


def test_second_controller_becomes_another_path_not_another_storage(session: Session) -> None:
    entity, first = _registered(session)
    original_id = entity.id
    original_metric = session.scalar(
        select(MetricEntity).where(MetricEntity.entity_type == "logical_storage")
    )
    assert original_metric is not None
    second = _path("hba-b", "/dev/sdc")
    plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="a" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="add",
        policy="recommended",
    )

    assert plan["destructive"] is False
    assert plan["format"] is False
    assert plan["copy_data"] is False
    assert plan["before"]["filesystem_uuid"] == plan["after"]["filesystem_uuid"]
    assert plan["before"]["mountpoint"] == plan["after"]["mountpoint"] == "/media"
    updated = apply_redundancy_result(session, plan=plan, observed_device=second)
    session.flush()

    assert updated.id == original_id
    assert updated.filesystem_uuid == "11111111-1111-4111-8111-111111111111"
    assert updated.mountpoint == "/media"
    assert updated.topology_state == "fully_redundant"
    assert len(storage_documents(session)) == 1
    assert len(storage_documents(session)[0]["paths"]) == 2
    metric = session.scalar(select(MetricEntity).where(MetricEntity.id == original_metric.id))
    assert metric is not None
    assert metric.stable_id == original_metric.stable_id
    assert metric.topology_json["path_count"] == 2


def test_existing_multipath_import_becomes_one_logical_storage_object(
    session: Session,
) -> None:
    first = _path("hba-a", "/dev/sdb")
    second = _path("hba-b", "/dev/sdc")
    mapper = deepcopy(first)
    mapper["id"] = "wwn:naa.600a098000abc:mapper"
    mapper["kernel_path"] = "/dev/mapper/naa.600a098000abc"
    mapper["connection"] = {
        "transport": "multipath",
        "protocol": "scsi",
    }
    entity = register_completed_storage(
        session,
        {
            "storage": {
                "topology": "import",
                "purpose": "MediaPool",
                "selected_devices": [mapper],
            }
        },
        {
            "mountpoint": "/media",
            "filesystem_uuids": {str(mapper["id"]): "11111111-1111-4111-8111-111111111111"},
            "member_mountpoints": {str(mapper["id"]): "/mnt/hoardarr/lun7"},
        },
        hardware_snapshot={"disks": [mapper, first, second]},
    )
    session.flush()
    assert entity is not None
    assert entity.presentation_device == "/dev/mapper/naa.600a098000abc"
    assert entity.topology_state == "fully_redundant"
    documents = storage_documents(session)
    assert len(documents) == 1
    assert len(documents[0]["paths"]) == 2


def test_completed_mergerfs_pool_is_one_logical_backend_and_members_are_not_reusable(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    members = [
        PhysicalDisk(stable_identity="wwn:member-a", lifecycle_state="discovered"),
        PhysicalDisk(stable_identity="wwn:member-b", lifecycle_state="discovered"),
    ]
    session.add_all(members)
    session.flush()
    monkeypatch.setattr(
        "hoardarr.storage.redundancy.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=61_440_000, used=0, free=61_440_000),
    )

    entity = register_completed_storage(
        session,
        {
            "storage": {
                "topology": "mergerfs",
                "selected_devices": [
                    {"id": "wwn:member-a", "capacity_bytes": 40_000_000},
                    {"id": "wwn:member-b", "capacity_bytes": 40_000_000},
                ],
                "mergerfs": {
                    "name": "media-library",
                    "mountpoint": "/mnt/hoardarr/media",
                    "create_policy": "mfs",
                    "search_policy": "ff",
                },
            }
        },
        {"mountpoint": "/data"},
    )
    session.flush()

    assert entity is not None
    assert entity.provider == "mergerfs"
    assert entity.mountpoint == "/data"
    assert entity.presentation_device == "/mnt/hoardarr/media"
    assert entity.capacity_bytes == 61_440_000
    assert entity.logical_sector_bytes is None
    assert entity.physical_sector_bytes is None
    assert entity.topology_state == "not_applicable"
    assert entity.config_json["member_stable_identities"] == ["wwn:member-a", "wwn:member-b"]
    assert {member.lifecycle_state for member in members} == {"managed_member"}
    assert all(member.metadata_json["managed_storage_entity_id"] == entity.id for member in members)
    document = storage_documents(session)[0]
    assert document["storage_kind"] == "mergerfs"
    assert document["provider"] == "mergerfs"
    assert document["redundancy_capable"] is False
    assert document["paths"] == []


def test_completed_zfs_pool_is_registered_as_one_logical_backend(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    members = [
        PhysicalDisk(stable_identity="wwn:zfs-a", lifecycle_state="discovered"),
        PhysicalDisk(stable_identity="wwn:zfs-b", lifecycle_state="discovered"),
        PhysicalDisk(stable_identity="wwn:zfs-c", lifecycle_state="discovered"),
    ]
    session.add_all(members)
    session.flush()
    monkeypatch.setattr(
        "hoardarr.storage.redundancy.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=25_000_000, used=1_000_000, free=24_000_000),
    )

    plan = {
        "storage": {
            "topology": "zfs",
            "selected_devices": [{"id": disk.stable_identity} for disk in members],
            "layout_options": {
                "name": "media",
                "mountpoint": "/data",
                "ashift": 12,
                "recordsize": "1M",
                "compression": "lz4",
                "vdevs": [
                    {"type": "raidz1", "device_ids": [disk.stable_identity for disk in members]}
                ],
            },
        }
    }
    entity = register_completed_storage(session, plan, {"mountpoint": "/data"})
    session.flush()

    assert entity is not None
    assert entity.stable_identity == "zfs:media"
    assert entity.provider == "zfs"
    assert entity.mountpoint == "/data"
    assert entity.presentation_device == "media"
    assert entity.capacity_bytes == 25_000_000
    assert {disk.lifecycle_state for disk in members} == {"managed_member"}
    replayed = register_completed_storage(session, plan, {"mountpoint": "/data"})
    assert replayed is not None and replayed.id == entity.id
    document = storage_documents(session)[0]
    assert document["storage_kind"] == "zfs"
    assert document["provider"] == "zfs"
    assert document["redundancy_capable"] is False
    assert document["paths"] == []


def test_mergerfs_expansion_preserves_group_entity_and_merges_presentation_alias(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_disk = PhysicalDisk(stable_identity="wwn:old", lifecycle_state="managed_member")
    new_disk = PhysicalDisk(stable_identity="wwn:new", lifecycle_state="discovered")
    canonical = StorageEntity(
        name="Media Library",
        stable_identity="mergerfs:canonical",
        storage_kind="mergerfs",
        filesystem_uuid=None,
        mountpoint="/data",
        presentation_device="/mnt/hoardarr/media",
        capacity_bytes=60_000,
        logical_sector_bytes=None,
        physical_sector_bytes=None,
        topology_state="not_applicable",
        provider="mergerfs",
        config_json={
            "pool_mountpoint": "/mnt/hoardarr/media",
            "member_stable_identities": ["wwn:old"],
            "create_policy": "mfs",
            "search_policy": "ff",
        },
    )
    group = StorageGroup(name="Media Library", namespace_path="/data", purpose="media")
    session.add_all([old_disk, new_disk, canonical, group])
    session.flush()
    session.add(
        StorageBackend(
            storage_group_id=group.id,
            storage_entity_id=canonical.id,
            stable_identity="storage:mergerfs:canonical",
            namespace_path="/data",
            role="data",
            lifecycle_state="preferred_write",
        )
    )
    canonical_metric = MetricEntity(
        entity_type="logical_storage",
        stable_id="logical-storage:mergerfs:canonical",
        display_name="Media Library",
        labels_json={"storage_entity_id": canonical.id},
        topology_json={"member_count": 1},
    )
    alias_identity = f"mergerfs:{hashlib.sha256(b'/data').hexdigest()[:16]}"
    alias = StorageEntity(
        name="data",
        stable_identity=alias_identity,
        storage_kind="mergerfs",
        filesystem_uuid=None,
        mountpoint="/data",
        presentation_device="/data",
        capacity_bytes=80_000,
        logical_sector_bytes=None,
        physical_sector_bytes=None,
        topology_state="not_applicable",
        provider="mergerfs",
        config_json={"pool_mountpoint": "/data", "member_stable_identities": ["wwn:new"]},
    )
    alias_metric = MetricEntity(
        entity_type="logical_storage",
        stable_id=f"logical-storage:{alias_identity}",
        display_name="data",
        labels_json={"storage_entity_id": alias.id},
        topology_json={"member_count": 1},
    )
    session.add_all([canonical_metric, alias, alias_metric])
    session.flush()
    observed_at = datetime.now(UTC)
    session.add(
        MetricSample(
            entity_id=alias_metric.id,
            metric_id="storage.capacity.total_bytes",
            value=80_000.0,
            value_text=None,
            quality="available",
            source="test",
            collection_interval_seconds=60,
            raw=True,
            labels_json={},
            error_code=None,
            observed_at=observed_at,
        )
    )
    session.flush()
    monkeypatch.setattr(
        "hoardarr.storage.redundancy.shutil.disk_usage",
        lambda path: SimpleNamespace(total=20_000 if str(path) == "/mnt/new" else 60_000),
    )

    entity = register_completed_storage(
        session,
        {
            "storage": {
                "topology": "mergerfs",
                "selected_devices": [{"id": "wwn:new", "capacity_bytes": 21_000}],
                "mergerfs": {"name": "data", "mountpoint": "/data", "mode": "existing"},
                "expansion": {
                    "kind": "add_mergerfs_member",
                    "storage_group_id": group.id,
                },
            }
        },
        {"mountpoint": "/data", "member_mountpoints": {"wwn:new": "/mnt/new"}},
    )
    session.flush()

    assert entity is not None
    assert entity.id == canonical.id
    assert entity.stable_identity == "mergerfs:canonical"
    assert entity.capacity_bytes == 80_000
    assert entity.config_json["pool_mountpoint"] == "/mnt/hoardarr/media"
    assert entity.config_json["member_stable_identities"] == ["wwn:old", "wwn:new"]
    assert session.get(StorageEntity, alias.id) is None
    assert session.scalar(select(MetricEntity).where(MetricEntity.id == alias_metric.id)) is None
    moved = session.scalar(select(MetricSample).where(MetricSample.observed_at == observed_at))
    assert moved is not None and moved.entity_id == canonical_metric.id
    assert new_disk.metadata_json["managed_storage_entity_id"] == canonical.id
    replayed = register_completed_storage(
        session,
        {
            "storage": {
                "topology": "mergerfs",
                "selected_devices": [{"id": "wwn:new", "capacity_bytes": 21_000}],
                "mergerfs": {"name": "data", "mountpoint": "/data", "mode": "existing"},
                "expansion": {
                    "kind": "add_mergerfs_member",
                    "storage_group_id": group.id,
                },
            }
        },
        {"mountpoint": "/data", "member_mountpoints": {"wwn:new": "/mnt/new"}},
    )
    assert replayed is not None and replayed.id == canonical.id
    assert replayed.capacity_bytes == 80_000


def test_matching_capacity_is_not_enough_when_wwid_differs(session: Session) -> None:
    entity, first = _registered(session)
    unrelated = _path("hba-b", "/dev/sdc", wwid="naa.600a098000different")
    with pytest.raises(RedundancyError, match="cannot safely confirm"):
        build_redundancy_plan(
            session,
            storage_entity_id=entity.id,
            hardware_snapshot_sha256="b" * 64,
            hardware_snapshot={"disks": [first, unrelated]},
            action="add",
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"capacity_bytes": 7_000_000_000_000}, "different capacity"),
        (
            {"sector_sizes": {"logical_bytes": 4096, "physical_bytes": 4096}},
            "different sector geometry",
        ),
    ],
)
def test_path_geometry_drift_fails_before_conversion(
    session: Session, change: dict[str, object], message: str
) -> None:
    entity, first = _registered(session)
    second = _path("hba-b", "/dev/sdc")
    second.update(change)
    with pytest.raises(RedundancyError, match=message):
        build_redundancy_plan(
            session,
            storage_entity_id=entity.id,
            hardware_snapshot_sha256="c" * 64,
            hardware_snapshot={"disks": [first, second]},
            action="add",
        )


def test_kernel_rename_does_not_change_logical_or_path_identity(session: Session) -> None:
    entity, first = _registered(session)
    renamed = deepcopy(first)
    renamed["kernel_path"] = "/dev/sdz"
    same = register_single_path_storage(
        session,
        name="MediaPool",
        device=renamed,
        mountpoint="/media",
        presentation_device="/dev/sdz",
        filesystem_uuid=entity.filesystem_uuid,
    )
    paths = list(
        session.scalars(select(StoragePath).where(StoragePath.storage_entity_id == entity.id))
    )
    assert same.id == entity.id
    assert len(paths) == 1
    assert paths[0].kernel_path == "/dev/sdz"


def test_privileged_transition_uses_multipath_and_remounts_without_formatting(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entity, first = _registered(session)
    second = _path("hba-b", "/dev/sdc")
    plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="d" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="add",
    )
    commands: list[list[str]] = []
    monkeypatch.setattr("hoardarr.storage.executor._tool", lambda name: f"/usr/sbin/{name}")
    result = apply_storage_redundancy(
        {
            "operation": "apply_storage_redundancy",
            "operation_id": "11111111-1111-4111-8111-111111111111",
            "plan_sha256": plan["plan_sha256"],
            "plan": plan,
            "confirmation_sha256": document_hash({"confirmation": "APPLY"}),
        },
        paths=Paths(
            transaction_root=tmp_path / "transactions",
            multipath_config_root=tmp_path / "multipath",
        ),
        inventory_provider=lambda: {"disks": [first, second]},
        runner=lambda command, _timeout: commands.append(command),
        mapper_exists=lambda _path: True,
        filesystem_uuid_provider=lambda _path: "11111111-1111-4111-8111-111111111111",
    )
    flattened = "\n".join(" ".join(command) for command in commands)
    assert flattened.index("umount /mnt/hoardarr/lun7") < flattened.index(
        "multipath -v2 /dev/sdc"
    )
    assert "multipath -a" in flattened
    assert "multipath -v2 /dev/sdc" in flattened
    assert "umount /media" in flattened
    assert "mount --bind /mnt/hoardarr/lun7 /media" in flattened
    assert "mkfs" not in flattened
    assert "parted" not in flattened
    assert result["mountpoint"] == "/media"
    assert result["filesystem_uuid"] == entity.filesystem_uuid


def test_redundancy_can_be_removed_without_recreating_storage(session: Session) -> None:
    entity, first = _registered(session)
    second = _path("hba-b", "/dev/sdc")
    add_plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="e" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="add",
    )
    apply_redundancy_result(session, plan=add_plan, observed_device=second)
    remove_plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="f" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="remove",
        candidate_path_identity=stable_path_identity(second),
    )
    updated = apply_redundancy_result(session, plan=remove_plan, observed_device=None)
    session.flush()
    assert updated.id == entity.id
    assert updated.topology_state == "single_path"
    assert updated.mountpoint == "/media"
    assert updated.filesystem_uuid == entity.filesystem_uuid
    assert len(storage_documents(session)[0]["paths"]) == 1


def test_privileged_removal_returns_to_remaining_direct_path_without_formatting(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entity, first = _registered(session)
    second = _path("hba-b", "/dev/sdc")
    add_plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="e" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="add",
    )
    apply_redundancy_result(session, plan=add_plan, observed_device=second)
    remove_plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="f" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="remove",
        candidate_path_identity=stable_path_identity(second),
    )
    commands: list[list[str]] = []
    monkeypatch.setattr("hoardarr.storage.executor._tool", lambda name: f"/usr/sbin/{name}")
    result = apply_storage_redundancy(
        {
            "operation": "apply_storage_redundancy",
            "operation_id": "33333333-3333-4333-8333-333333333333",
            "plan_sha256": remove_plan["plan_sha256"],
            "plan": remove_plan,
            "confirmation_sha256": document_hash({"confirmation": "APPLY"}),
        },
        paths=Paths(
            transaction_root=tmp_path / "transactions",
            multipath_config_root=tmp_path / "multipath",
        ),
        inventory_provider=lambda: {"disks": [first, second]},
        runner=lambda command, _timeout: commands.append(command),
    )
    flattened = "\n".join(" ".join(command) for command in commands)
    assert "multipathd del path sdc" in flattened
    assert "multipathd fail path sdc" in flattened
    assert "multipath -f naa.600a098000abc" in flattened
    assert "mount /dev/sdb /mnt/hoardarr/lun7" in flattened
    assert "mkfs" not in flattened
    assert result["storage_entity_id"] == entity.id
    assert result["filesystem_uuid"] == entity.filesystem_uuid
    assert result["mountpoint"] == entity.mountpoint


def test_redundancy_removal_waits_for_busy_mapper_and_preserves_mount(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entity, first = _registered(session)
    second = _path("hba-b", "/dev/sdc")
    add_plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="e" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="add",
    )
    apply_redundancy_result(session, plan=add_plan, observed_device=second)
    remove_plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="f" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="remove",
        candidate_path_identity=stable_path_identity(second),
    )
    commands: list[list[str]] = []
    waits: list[float] = []
    flush_attempts = 0

    def busy_then_ready(command: list[str], _timeout: float) -> None:
        nonlocal flush_attempts
        commands.append(command)
        if command[0].endswith("multipath") and "-f" in command:
            flush_attempts += 1
            if flush_attempts < 3:
                raise ExecutorFailure("multipath_busy", "The map is still busy")

    monkeypatch.setattr("hoardarr.storage.executor._tool", lambda name: f"/usr/sbin/{name}")
    result = apply_storage_redundancy(
        {
            "operation": "apply_storage_redundancy",
            "operation_id": "77777777-7777-4777-8777-777777777777",
            "plan_sha256": remove_plan["plan_sha256"],
            "plan": remove_plan,
            "confirmation_sha256": document_hash({"confirmation": "APPLY"}),
        },
        paths=Paths(
            transaction_root=tmp_path / "transactions",
            multipath_config_root=tmp_path / "multipath",
        ),
        inventory_provider=lambda: {"disks": [first, second]},
        runner=busy_then_ready,
        sleep=waits.append,
    )

    assert flush_attempts == 3
    assert waits == [0.2, 0.4]
    assert ["/usr/sbin/udevadm", "settle", "--timeout=60"] in commands
    assert commands[-2:] == [
        ["/usr/sbin/mount", "/dev/sdb", "/mnt/hoardarr/lun7"],
        ["/usr/sbin/mount", "--bind", "/mnt/hoardarr/lun7", "/media"],
    ]
    assert result["storage_entity_id"] == entity.id


def test_expert_grouping_policy_is_applied_only_to_new_map(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entity, first = _registered(session)
    second = _path("hba-b", "/dev/sdc")
    plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="d" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="add",
        policy="group_by_prio",
    )
    commands: list[list[str]] = []
    monkeypatch.setattr("hoardarr.storage.executor._tool", lambda name: f"/usr/sbin/{name}")
    apply_storage_redundancy(
        {
            "operation": "apply_storage_redundancy",
            "operation_id": "11111111-1111-4111-8111-111111111111",
            "plan_sha256": plan["plan_sha256"],
            "plan": plan,
            "confirmation_sha256": document_hash({"confirmation": "APPLY"}),
        },
        paths=Paths(
            transaction_root=tmp_path / "transactions",
            multipath_config_root=tmp_path / "multipath",
        ),
        inventory_provider=lambda: {"disks": [first, second]},
        runner=lambda command, _timeout: commands.append(command),
        mapper_exists=lambda _path: True,
        filesystem_uuid_provider=lambda _path: entity.filesystem_uuid or "",
    )
    assert ["/usr/sbin/multipath", "-t"] in commands
    assert ["/usr/sbin/multipath", "-v2", "/dev/sdc"] in commands
    config = next((tmp_path / "multipath").glob("hoardarr-*.conf")).read_text()
    assert "path_grouping_policy group_by_prio" in config
    assert 'path_selector "service-time 0"' in config


def test_controller_path_replacement_adds_new_path_before_removing_old(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entity, first = _registered(session)
    second = _path("hba-b", "/dev/sdc")
    add_plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="e" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="add",
    )
    apply_redundancy_result(session, plan=add_plan, observed_device=second)
    original_id = entity.id
    original_uuid = entity.filesystem_uuid
    original_mount = entity.mountpoint
    second_renumbered = deepcopy(second)
    second_renumbered["kernel_path"] = "/dev/sdz"
    replacement = _path("hba-c", "/dev/sdd")
    plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="f" * 64,
        hardware_snapshot={"disks": [first, second_renumbered, replacement]},
        action="replace",
        remove_path_identity=stable_path_identity(second),
    )
    commands: list[list[str]] = []
    monkeypatch.setattr("hoardarr.storage.executor._tool", lambda name: f"/usr/sbin/{name}")
    result = apply_storage_redundancy(
        {
            "operation": "apply_storage_redundancy",
            "operation_id": "44444444-4444-4444-8444-444444444444",
            "plan_sha256": plan["plan_sha256"],
            "plan": plan,
            "confirmation_sha256": document_hash({"confirmation": "APPLY"}),
        },
        paths=Paths(
            transaction_root=tmp_path / "transactions",
            multipath_config_root=tmp_path / "multipath",
        ),
        inventory_provider=lambda: {"disks": [first, second_renumbered, replacement]},
        runner=lambda command, _timeout: commands.append(command),
        mapper_exists=lambda _path: True,
        filesystem_uuid_provider=lambda _path: original_uuid or "",
    )
    flattened = "\n".join(" ".join(command) for command in commands)
    assert plan["removed_path"]["kernel_path"] == "/dev/sdz"
    assert flattened.index("multipath -v2 /dev/sdd") < flattened.index(
        "multipathd fail path sdz"
    )
    assert flattened.index("multipathd fail path sdz") < flattened.index(
        "multipathd del path sdz"
    )
    assert not any(command[0].endswith("/umount") for command in commands)
    assert not any(command[0].endswith("/mount") for command in commands)
    assert "mkfs" not in flattened

    updated = apply_redundancy_result(session, plan=plan, observed_device=replacement)
    session.flush()
    documents = storage_documents(session)
    assert updated.id == original_id == result["storage_entity_id"]
    assert updated.filesystem_uuid == original_uuid == result["filesystem_uuid"]
    assert updated.mountpoint == original_mount == result["mountpoint"]
    assert len(documents) == 1
    assert {item["kernel_path"] for item in documents[0]["paths"]} == {
        "/dev/sdb",
        "/dev/sdd",
    }


def test_path_failure_and_recovery_preserve_logical_storage_identity(session: Session) -> None:
    entity, first = _registered(session)
    second = _path("hba-b", "/dev/sdc")
    add_plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="e" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="add",
    )
    apply_redundancy_result(session, plan=add_plan, observed_device=second)
    original_id = entity.id
    original_uuid = entity.filesystem_uuid
    original_mount = entity.mountpoint

    changed = reconcile_storage_path_health(
        session,
        [
            {
                "wwid": "naa.600a098000abc",
                "paths": [
                    {"kernel_name": "sdb", "state": "failed", "optimized": False},
                    {"kernel_name": "sdc", "state": "ready", "optimized": True},
                ],
            }
        ],
    )
    assert changed == 1
    assert entity.topology_state == "failed_over"
    assert entity.id == original_id
    assert entity.filesystem_uuid == original_uuid
    assert entity.mountpoint == original_mount
    assert session.scalar(
        select(StorageRedundancyEvent).where(
            StorageRedundancyEvent.event_type == "controller_failover"
        )
    ) is not None
    failover_count = len(
        list(
            session.scalars(
                select(StorageRedundancyEvent).where(
                    StorageRedundancyEvent.event_type == "controller_failover"
                )
            )
        )
    )
    reconcile_storage_path_health(
        session,
        [
            {
                "wwid": "naa.600a098000abc",
                "paths": [
                    {"kernel_name": "sdb", "state": "failed", "optimized": False},
                    {"kernel_name": "sdc", "state": "ready", "optimized": True},
                ],
            }
        ],
    )
    assert len(
        list(
            session.scalars(
                select(StorageRedundancyEvent).where(
                    StorageRedundancyEvent.event_type == "controller_failover"
                )
            )
        )
    ) == failover_count

    reconcile_storage_path_health(
        session,
        [
            {
                "wwid": "naa.600a098000abc",
                "paths": [
                    {"kernel_name": "sdb", "state": "ready", "optimized": True},
                    {"kernel_name": "sdc", "state": "ready", "optimized": True},
                ],
            }
        ],
    )
    assert entity.topology_state == "fully_redundant"
    assert entity.id == original_id
    assert session.scalar(
        select(StorageRedundancyEvent).where(
            StorageRedundancyEvent.event_type == "redundancy_restored"
        )
    ) is not None

    reconcile_storage_path_health(
        session,
        [
            {
                "wwid": "naa.600a098000abc",
                "paths": [
                    {"kernel_name": "sdb", "state": "failed", "optimized": False},
                    {"kernel_name": "sdc", "state": "failed", "optimized": False},
                ],
            }
        ],
    )
    assert entity.topology_state == "no_path"
    assert entity.id == original_id
    assert entity.filesystem_uuid == original_uuid
    assert entity.mountpoint == original_mount


def test_redundancy_journal_replays_success_and_reports_command_failure(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entity, first = _registered(session)
    second = _path("hba-b", "/dev/sdc")
    plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="d" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="add",
    )
    paths = Paths(
        transaction_root=tmp_path / "transactions",
        multipath_config_root=tmp_path / "multipath",
    )
    request = {
        "operation": "apply_storage_redundancy",
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "plan_sha256": plan["plan_sha256"],
        "plan": plan,
        "confirmation_sha256": document_hash({"confirmation": "APPLY"}),
    }
    commands: list[list[str]] = []
    monkeypatch.setattr("hoardarr.storage.executor._tool", lambda name: f"/usr/sbin/{name}")
    first_result = apply_storage_redundancy(
        request,
        paths=paths,
        inventory_provider=lambda: {"disks": [first, second]},
        runner=lambda command, _timeout: commands.append(command),
        mapper_exists=lambda _path: True,
        filesystem_uuid_provider=lambda _path: entity.filesystem_uuid or "",
    )
    replay = apply_storage_redundancy(
        request,
        paths=paths,
        inventory_provider=lambda: {"disks": [first, second]},
        runner=lambda command, _timeout: commands.append(command),
        mapper_exists=lambda _path: True,
        filesystem_uuid_provider=lambda _path: entity.filesystem_uuid or "",
    )
    assert first_result["replayed"] is False
    assert replay["replayed"] is True
    assert storage_operation_status(request["operation_id"], paths=paths)["state"] == "succeeded"

    failed_request = {**request, "operation_id": "22222222-2222-4222-8222-222222222222"}

    failed_commands: list[list[str]] = []

    def fail_create(command: list[str], _timeout: float) -> None:
        failed_commands.append(command)
        if "-v2" in command:
            raise ExecutorFailure("multipath_failed", "Map creation failed")

    with pytest.raises(ExecutorFailure, match="original storage path was restored"):
        apply_storage_redundancy(
            failed_request,
            paths=paths,
            inventory_provider=lambda: {"disks": [first, second]},
            runner=fail_create,
            mapper_exists=lambda _path: True,
            filesystem_uuid_provider=lambda _path: entity.filesystem_uuid or "",
        )
    assert ["/usr/sbin/umount", "/media"] in failed_commands
    assert ["/usr/sbin/umount", "/mnt/hoardarr/lun7"] in failed_commands
    assert ["/usr/sbin/mount", "/dev/sdb", "/mnt/hoardarr/lun7"] in failed_commands
    assert failed_commands[-1] == [
        "/usr/sbin/mount",
        "--bind",
        "/mnt/hoardarr/lun7",
        "/media",
    ]
    assert (
        storage_operation_status(failed_request["operation_id"], paths=paths)["state"]
        == "needs_attention"
    )


def test_redundancy_waits_a_bounded_time_for_the_mapper_node(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entity, first = _registered(session)
    second = _path("hba-b", "/dev/sdc")
    plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="d" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="add",
    )
    checks = 0
    waits: list[float] = []

    def mapper_exists(_path: Path) -> bool:
        nonlocal checks
        checks += 1
        return checks == 4

    monkeypatch.setattr("hoardarr.storage.executor._tool", lambda name: f"/usr/sbin/{name}")
    apply_storage_redundancy(
        {
            "operation": "apply_storage_redundancy",
            "operation_id": "66666666-6666-4666-8666-666666666666",
            "plan_sha256": plan["plan_sha256"],
            "plan": plan,
            "confirmation_sha256": document_hash({"confirmation": "APPLY"}),
        },
        paths=Paths(
            transaction_root=tmp_path / "transactions",
            multipath_config_root=tmp_path / "multipath",
        ),
        inventory_provider=lambda: {"disks": [first, second]},
        runner=lambda _command, _timeout: None,
        mapper_exists=mapper_exists,
        filesystem_uuid_provider=lambda _path: entity.filesystem_uuid or "",
        sleep=waits.append,
    )
    assert checks == 4
    assert waits == [0.2, 0.2, 0.2]


def test_failed_bind_mount_rolls_back_from_mapper_to_reviewed_direct_path(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entity, first = _registered(session)
    second = _path("hba-b", "/dev/sdc")
    plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="d" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="add",
    )
    commands: list[list[str]] = []
    failed = False

    def fail_bind_once(command: list[str], _timeout: float) -> None:
        nonlocal failed
        commands.append(command)
        if "--bind" in command and not failed:
            failed = True
            raise ExecutorFailure("mount_failed", "Bind mount failed")

    monkeypatch.setattr("hoardarr.storage.executor._tool", lambda name: f"/usr/sbin/{name}")
    with pytest.raises(ExecutorFailure, match="original storage path was restored"):
        apply_storage_redundancy(
            {
                "operation": "apply_storage_redundancy",
                "operation_id": "55555555-5555-4555-8555-555555555555",
                "plan_sha256": plan["plan_sha256"],
                "plan": plan,
                "confirmation_sha256": document_hash({"confirmation": "APPLY"}),
            },
            paths=Paths(
                transaction_root=tmp_path / "transactions",
                multipath_config_root=tmp_path / "multipath",
            ),
            inventory_provider=lambda: {"disks": [first, second]},
            runner=fail_bind_once,
            mapper_exists=lambda _path: True,
            filesystem_uuid_provider=lambda _path: entity.filesystem_uuid or "",
        )
    assert ["/usr/sbin/umount", "/mnt/hoardarr/lun7"] in commands
    assert ["/usr/sbin/mount", "/dev/sdb", "/mnt/hoardarr/lun7"] in commands
    assert commands[-1] == [
        "/usr/sbin/mount",
        "--bind",
        "/mnt/hoardarr/lun7",
        "/media",
    ]


def test_configure_plan_applies_real_multipath_settings_and_records_event(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entity, first = _registered(session)
    second = _path("hba-b", "/dev/sdc")
    add = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="f" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="add",
    )
    apply_redundancy_result(session, plan=add, observed_device=second)
    settings = {
        **add["settings"],
        "mode": "custom",
        "path_grouping_policy": "multibus",
        "path_selector": "queue-length 0",
        "failback": "manual",
        "no_path_retry": "queue_30",
    }
    plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="f" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="configure",
        settings_override=settings,
    )
    assert plan["transition"]["mode"] == "online_supported"
    commands: list[list[str]] = []
    monkeypatch.setattr("hoardarr.storage.executor._tool", lambda name: f"/usr/sbin/{name}")
    result = apply_storage_redundancy(
        {
            "operation": "apply_storage_redundancy",
            "operation_id": "77777777-7777-4777-8777-777777777777",
            "plan_sha256": plan["plan_sha256"],
            "plan": plan,
            "confirmation_sha256": document_hash({"confirmation": "APPLY"}),
        },
        paths=Paths(
            transaction_root=tmp_path / "transactions",
            multipath_config_root=tmp_path / "multipath",
        ),
        inventory_provider=lambda: {"disks": [first, second]},
        runner=lambda command, _timeout: commands.append(command),
        mapper_exists=lambda _path: True,
    )
    assert result["topology_state"] == "fully_redundant"
    assert ["/usr/sbin/multipath", "-t"] in commands
    assert ["/usr/sbin/multipathd", "reconfigure"] in commands
    config = next((tmp_path / "multipath").glob("hoardarr-*.conf")).read_text()
    assert "path_grouping_policy multibus" in config
    assert 'path_selector "queue-length 0"' in config
    assert "failback manual" in config
    assert "no_path_retry 30" in config
    apply_redundancy_result(
        session,
        plan=plan,
        observed_device=None,
        operation_id="77777777-7777-4777-8777-777777777777",
    )
    event = session.scalar(
        select(StorageRedundancyEvent).where(
            StorageRedundancyEvent.event_type == "redundancy_settings_changed"
        )
    )
    assert event is not None
    assert event.operation_id == "77777777-7777-4777-8777-777777777777"
    documents = redundancy_event_documents(session, entity.id)
    assert documents[0]["event_type"] == "redundancy_settings_changed"


def test_maintenance_transition_coordinates_managed_smb_and_nfs(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entity, first = _registered(session)
    for protocol, name in (("smb", "Media"), ("nfs", "Media export")):
        config = {"path": "/media/library"}
        session.add(
            ConnectivityService(
                protocol=protocol,
                name=name,
                config_json=config,
                config_sha256=document_hash(config),
                status="active",
                state_json={},
            )
        )
    session.flush()
    second = _path("hba-b", "/dev/sdc")
    plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="f" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="add",
    )
    assert {item["protocol"] for item in plan["managed_access_services"]} == {"smb", "nfs"}
    commands: list[list[str]] = []
    monkeypatch.setattr("hoardarr.storage.executor._tool", lambda name: f"/usr/sbin/{name}")
    apply_storage_redundancy(
        {
            "operation": "apply_storage_redundancy",
            "operation_id": "88888888-8888-4888-8888-888888888888",
            "plan_sha256": plan["plan_sha256"],
            "plan": plan,
            "confirmation_sha256": document_hash({"confirmation": "APPLY"}),
        },
        paths=Paths(
            transaction_root=tmp_path / "transactions",
            multipath_config_root=tmp_path / "multipath",
        ),
        inventory_provider=lambda: {"disks": [first, second]},
        runner=lambda command, _timeout: commands.append(command),
        mapper_exists=lambda _path: True,
        filesystem_uuid_provider=lambda _path: entity.filesystem_uuid or "",
    )
    first_unmount = commands.index(["/usr/sbin/umount", "/media"])
    last_mount = commands.index(
        ["/usr/sbin/mount", "--bind", "/mnt/hoardarr/lun7", "/media"]
    )
    for unit in ("nfs-server.service", "smbd.service"):
        assert commands.index(["/usr/sbin/systemctl", "stop", unit]) < first_unmount
        assert commands.index(["/usr/sbin/systemctl", "start", unit]) > last_mount


def test_path_flapping_alert_uses_durable_transitions_and_setting(session: Session) -> None:
    entity, _first = _registered(session)
    path = session.scalar(select(StoragePath))
    assert path is not None
    path_metric = MetricEntity(
        entity_type="storage_path",
        stable_id=f"storage-path:{path.stable_path_identity}",
        display_name=path.kernel_path,
        topology_json={"storage_entity_id": entity.id},
    )
    session.add(path_metric)
    session.flush()
    now = datetime.now(UTC).replace(microsecond=0)
    for index, event_type in enumerate(
        ("path_failed", "path_recovered", "path_failed", "path_recovered")
    ):
        session.add(
            StorageRedundancyEvent(
                storage_entity_id=entity.id,
                path_id=path.id,
                controller_id=path.controller_id,
                event_type=event_type,
                previous_state="active" if event_type == "path_failed" else "failed",
                resulting_state="failed" if event_type == "path_failed" else "active",
                occurred_at=now - timedelta(minutes=4 - index),
            )
        )
    sample = MetricSample(
        entity_id=path_metric.id,
        metric_id="storage.path.state",
        value=None,
        value_text="active",
        quality="available",
        source="multipathd",
        collection_interval_seconds=30,
        raw=True,
        observed_at=now,
    )
    session.add(sample)
    session.flush()

    assert evaluate_basic_alerts(session, [sample]) == {"opened": 1, "resolved": 0}
    session.flush()
    alert = next(
        (
            item
            for item in session.scalars(select(MetricAlert))
            if item.details_json.get("condition") == "path_flapping"
        ),
        None,
    )
    assert alert is not None
    assert alert.trigger_value == 4

    entity.config_json = {
        **entity.config_json,
        "redundancy_settings": {"alert_on_path_flapping": False},
    }
    assert evaluate_basic_alerts(session, [sample]) == {"opened": 0, "resolved": 1}
    assert alert.state == "resolved"


def test_failed_settings_validation_restores_previous_multipath_config(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entity, first = _registered(session)
    second = _path("hba-b", "/dev/sdc")
    add = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="f" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="add",
    )
    apply_redundancy_result(session, plan=add, observed_device=second)
    plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="f" * 64,
        hardware_snapshot={"disks": [first, second]},
        action="configure",
        settings_override={
            **add["settings"],
            "mode": "custom",
            "failback": "manual",
        },
    )
    commands: list[list[str]] = []
    monkeypatch.setattr("hoardarr.storage.executor._tool", lambda name: f"/usr/sbin/{name}")

    def fail_validation(command: list[str], _timeout: float) -> None:
        commands.append(command)
        if command == ["/usr/sbin/multipath", "-t"]:
            raise ExecutorFailure("command_failed", "invalid multipath configuration")

    config_root = tmp_path / "multipath"
    with pytest.raises(ExecutorFailure, match="invalid multipath configuration"):
        apply_storage_redundancy(
            {
                "operation": "apply_storage_redundancy",
                "operation_id": "99999999-9999-4999-8999-999999999999",
                "plan_sha256": plan["plan_sha256"],
                "plan": plan,
                "confirmation_sha256": document_hash({"confirmation": "APPLY"}),
            },
            paths=Paths(
                transaction_root=tmp_path / "transactions",
                multipath_config_root=config_root,
            ),
            inventory_provider=lambda: {"disks": [first, second]},
            runner=fail_validation,
            mapper_exists=lambda _path: True,
        )
    assert list(config_root.glob("hoardarr-*.conf")) == []
    assert commands[-1] == ["/usr/sbin/multipathd", "reconfigure"]
