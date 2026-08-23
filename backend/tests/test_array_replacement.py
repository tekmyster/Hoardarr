from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path, PurePosixPath

import pytest

from hoardarr.operations.service import document_hash
from hoardarr.storage import executor
from hoardarr.storage.executor import Paths, apply_array_replacement
from hoardarr.storage.replacement import (
    ArrayReplacementError,
    build_md_replacement_plan,
    build_zfs_replacement_plan,
    validate_array_replacement_plan,
)


def disk(*, capacity: int = 2_000_000_000) -> dict[str, object]:
    return {
        "id": "wwn:replacement",
        "stable_identity": True,
        "vendor": "TEST",
        "model": "DISPOSABLE",
        "identity": {"serial": "REPLACE-1", "wwn": "replacement", "eui64": None, "nguid": None},
        "capacity_bytes": capacity,
        "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
        "system_device": False,
        "system_disk": False,
        "read_only": False,
        "selectable": True,
        "partitions": [{"name": "old"}],
        "signatures": [{"type": "ext4"}],
        "signature_scan": {"status": "complete"},
        "kernel_path": "/dev/sdz",
    }


def zfs_pool() -> dict[str, object]:
    return {
        "name": "media",
        "pool_guid": "1234567890123456789",
        "degraded": True,
        "configuration": {
            "quality": "available",
            "vdev_type": "mirror",
            "member_paths": ["/dev/disk/by-id/scsi-old", "/dev/disk/by-id/scsi-live"],
            "member_capacities": {"/dev/disk/by-id/scsi-old": 1_000_000_000},
            "config_sha256": "a" * 64,
        },
    }


def md_array(*, degraded: bool = True) -> dict[str, object]:
    return {
        "name": "md0",
        "degraded": degraded,
        "configuration": {
            "array_path": "/dev/md0",
            "array_uuid": "abcd1234:abcd1234:abcd1234:abcd1234",
            "level": "raid1",
            "raid_disks": 2,
            "member_paths": ["/dev/sdb"],
            "config_sha256": "b" * 64,
        },
    }


def test_zfs_plan_binds_pool_member_contents_and_replacement() -> None:
    plan = build_zfs_replacement_plan(
        pool=zfs_pool(),
        member_path="/dev/disk/by-id/scsi-old",
        disk=disk(),
        hardware_snapshot_sha256="c" * 64,
    )
    assert validate_array_replacement_plan(plan) == plan
    assert plan["target_identity"] == "1234567890123456789"
    assert plan["existing_data"]["detected"] is True
    assert plan["device_binding_sha256"] == document_hash(plan["device"])


def test_zfs_plan_rejects_small_or_unknown_member() -> None:
    with pytest.raises(ArrayReplacementError, match="smaller"):
        build_zfs_replacement_plan(
            pool=zfs_pool(),
            member_path="/dev/disk/by-id/scsi-old",
            disk=disk(capacity=999_999_999),
            hardware_snapshot_sha256="c" * 64,
        )
    with pytest.raises(ArrayReplacementError, match="authoritative"):
        build_zfs_replacement_plan(
            pool=zfs_pool(),
            member_path="/dev/disk/by-id/scsi-missing",
            disk=disk(),
            hardware_snapshot_sha256="c" * 64,
        )


def test_md_degraded_replacement_does_not_require_present_old_member() -> None:
    plan = build_md_replacement_plan(
        array=md_array(),
        member_path=None,
        disk=disk(),
        hardware_snapshot_sha256="c" * 64,
    )
    assert validate_array_replacement_plan(plan) == plan
    assert plan["old_member_path"] is None
    assert plan["degraded"] is True


def test_md_proactive_replacement_requires_current_member_and_tampering_fails() -> None:
    with pytest.raises(ArrayReplacementError, match="Select"):
        build_md_replacement_plan(
            array=md_array(degraded=False),
            member_path=None,
            disk=disk(),
            hardware_snapshot_sha256="c" * 64,
        )
    plan = build_md_replacement_plan(
        array=md_array(degraded=False),
        member_path="/dev/sdb",
        disk=disk(),
        hardware_snapshot_sha256="c" * 64,
    )
    changed = deepcopy(plan)
    changed["target_identity"] = "different"
    # The generic validator checks structure; execution also compares the bound
    # identity to the live provider state immediately before mutation.
    assert validate_array_replacement_plan(changed) == changed
    changed["device"]["capacity_bytes"] = 1
    with pytest.raises(ArrayReplacementError, match="Invalid"):
        validate_array_replacement_plan(changed)


def request(plan: dict[str, object]) -> dict[str, object]:
    return {
        "operation": "apply_array_replacement",
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "plan_sha256": document_hash(plan),
        "plan": plan,
        "confirmation_sha256": document_hash({"confirmation": "I AGREE"}),
    }


def executor_paths(tmp_path: Path) -> Paths:
    return Paths(
        transaction_root=tmp_path / "transactions",
        lock_root=tmp_path / "locks",
        quarantine_marker=tmp_path / "quarantine.json",
    )


def prepare_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "validate_quarantine", lambda _path: {"ready": True})
    monkeypatch.setattr(executor, "_ensure_not_active", lambda *_args: None)
    monkeypatch.setattr(
        executor,
        "_stable_path",
        lambda *_args: PurePosixPath("/dev/disk/by-id/scsi-replacement"),
    )
    monkeypatch.setattr(executor, "_tool", lambda name: name)
    monkeypatch.setattr(executor, "_device_locks", lambda *_args: nullcontext())


def test_zfs_executor_waits_for_resilver_and_preserves_pool_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare_executor(monkeypatch)
    plan = build_zfs_replacement_plan(
        pool=zfs_pool(),
        member_path="/dev/disk/by-id/scsi-old",
        disk=disk(),
        hardware_snapshot_sha256="c" * 64,
    )
    initial = {
        "pool_guid": plan["target_identity"],
        "config_sha256": plan["configuration_sha256"],
        "member_paths": ["/dev/disk/by-id/scsi-old", "/dev/disk/by-id/scsi-live"],
        "degraded": True,
    }
    final = {
        **initial,
        "config_sha256": "d" * 64,
        "member_paths": ["/dev/disk/by-id/scsi-replacement", "/dev/disk/by-id/scsi-live"],
        "degraded": False,
    }
    states = [initial, initial, final]
    commands: list[list[str]] = []
    result = apply_array_replacement(
        request(plan),
        paths=executor_paths(tmp_path),
        inventory_provider=lambda: {"disks": [disk()]},
        runner=lambda argv, _timeout: commands.append(argv),
        zfs_state_provider=lambda _name: states.pop(0),
    )
    assert commands == [
        ["wipefs", "--all", "/dev/disk/by-id/scsi-replacement"],
        [
            "zpool",
            "replace",
            "-w",
            "media",
            "/dev/disk/by-id/scsi-old",
            "/dev/disk/by-id/scsi-replacement",
        ],
    ]
    assert result["target_identity"] == plan["target_identity"]
    assert states == []


def test_md_degraded_executor_adds_and_observes_real_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare_executor(monkeypatch)
    plan = build_md_replacement_plan(
        array=md_array(), member_path=None, disk=disk(), hardware_snapshot_sha256="c" * 64
    )
    initial = {
        "array_uuid": plan["target_identity"],
        "level": "raid1",
        "raid_disks": 2,
        "config_sha256": plan["configuration_sha256"],
        "member_paths": ["/dev/sdb"],
        "degraded": True,
        "sync_action": "idle",
    }
    rebuilding = {
        **initial,
        "member_paths": ["/dev/sdb", "/dev/disk/by-id/scsi-replacement"],
        "sync_action": "recover",
    }
    final = {**rebuilding, "config_sha256": "e" * 64, "degraded": False, "sync_action": "idle"}
    states = [initial, initial, rebuilding, final, final]
    commands: list[list[str]] = []
    apply_array_replacement(
        request(plan),
        paths=executor_paths(tmp_path),
        inventory_provider=lambda: {"disks": [disk()]},
        runner=lambda argv, _timeout: commands.append(argv),
        md_state_provider=lambda _name: states.pop(0),
        sleep=lambda _seconds: None,
    )
    assert commands[-1] == ["mdadm", "/dev/md0", "--add", "/dev/disk/by-id/scsi-replacement"]
    assert states == []


def test_executor_rejects_provider_drift_before_erasing_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare_executor(monkeypatch)
    plan = build_zfs_replacement_plan(
        pool=zfs_pool(),
        member_path="/dev/disk/by-id/scsi-old",
        disk=disk(),
        hardware_snapshot_sha256="c" * 64,
    )
    commands: list[list[str]] = []
    with pytest.raises(executor.ExecutorFailure, match="topology"):
        apply_array_replacement(
            request(plan),
            paths=executor_paths(tmp_path),
            inventory_provider=lambda: {"disks": [disk()]},
            runner=lambda argv, _timeout: commands.append(argv),
            zfs_state_provider=lambda _name: {
                "pool_guid": plan["target_identity"],
                "config_sha256": "f" * 64,
                "member_paths": [],
            },
        )
    assert commands == []
