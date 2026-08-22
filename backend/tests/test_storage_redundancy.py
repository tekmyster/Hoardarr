from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from hoardarr.db.models import Base, MetricEntity, StorageEntity, StoragePath
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
    register_completed_storage,
    register_single_path_storage,
    stable_path_identity,
    storage_documents,
)


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
        paths=Paths(transaction_root=tmp_path / "transactions"),
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
        paths=Paths(transaction_root=tmp_path / "transactions"),
        inventory_provider=lambda: {"disks": [first, second]},
        runner=lambda command, _timeout: commands.append(command),
    )
    flattened = "\n".join(" ".join(command) for command in commands)
    assert "multipathd del path sdc" in flattened
    assert "multipath -f naa.600a098000abc" in flattened
    assert "mount /dev/sdb /mnt/hoardarr/lun7" in flattened
    assert "mkfs" not in flattened
    assert result["storage_entity_id"] == entity.id
    assert result["filesystem_uuid"] == entity.filesystem_uuid
    assert result["mountpoint"] == entity.mountpoint


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
        paths=Paths(transaction_root=tmp_path / "transactions"),
        inventory_provider=lambda: {"disks": [first, second]},
        runner=lambda command, _timeout: commands.append(command),
        mapper_exists=lambda _path: True,
        filesystem_uuid_provider=lambda _path: entity.filesystem_uuid or "",
    )
    assert [
        "/usr/sbin/multipath",
        "-v2",
        "-p",
        "group_by_prio",
        "/dev/sdc",
    ] in commands


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
    replacement = _path("hba-c", "/dev/sdd")
    plan = build_redundancy_plan(
        session,
        storage_entity_id=entity.id,
        hardware_snapshot_sha256="f" * 64,
        hardware_snapshot={"disks": [first, second, replacement]},
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
        paths=Paths(transaction_root=tmp_path / "transactions"),
        inventory_provider=lambda: {"disks": [first, second, replacement]},
        runner=lambda command, _timeout: commands.append(command),
        mapper_exists=lambda _path: True,
        filesystem_uuid_provider=lambda _path: original_uuid or "",
    )
    flattened = "\n".join(" ".join(command) for command in commands)
    assert flattened.index("multipath -v2 /dev/sdd") < flattened.index("multipathd del path sdc")
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
    paths = Paths(transaction_root=tmp_path / "transactions")
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
        paths=Paths(transaction_root=tmp_path / "transactions"),
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
            paths=Paths(transaction_root=tmp_path / "transactions"),
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
