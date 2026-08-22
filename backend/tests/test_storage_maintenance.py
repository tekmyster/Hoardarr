from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path

import pytest

from hoardarr.operations.service import document_hash
from hoardarr.storage import executor
from hoardarr.storage.executor import ExecutorFailure, Paths, apply_device_maintenance
from hoardarr.storage.maintenance import MaintenanceError, build_plan, validate_plan

DEVICE_ID = "wwn:5000c500test"


def disk(*, logical: int = 512, system: bool = False) -> dict[str, object]:
    return {
        "id": DEVICE_ID,
        "stable_identity": True,
        "system_device": system,
        "selectable": not system,
        "kernel_path": "/dev/sdz",
        "vendor": "SEAGATE",
        "model": "TEST",
        "identity": {"serial": "SERIAL", "wwn": "5000c500test", "eui64": None, "nguid": None},
        "capacity_bytes": 1_000_000_000,
        "sector_sizes": {"logical_bytes": logical, "physical_bytes": logical},
        "partitions": [],
        "maintenance_capabilities": {
            "ata_secure_erase": True,
            "nvme_block_erase": False,
            "supported_logical_sector_bytes": [512, 520],
            "sector_format_passthrough": True,
        },
    }


def request(plan: dict[str, object]) -> dict[str, object]:
    return {
        "operation": "apply_device_maintenance",
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "plan_sha256": document_hash(plan),
        "plan": plan,
        "confirmation_sha256": document_hash({"confirmation": "I AGREE"}),
    }


def test_wipe_plan_requires_capability_and_excludes_system_device() -> None:
    plan = build_plan(
        disk=disk(),
        hardware_snapshot_sha256="a" * 64,
        action="wipe",
        method="ata_secure_erase",
    )
    assert validate_plan(plan) == plan
    unsupported = disk()
    unsupported["maintenance_capabilities"] = {}
    with pytest.raises(MaintenanceError) as failure:
        build_plan(
            disk=unsupported,
            hardware_snapshot_sha256="a" * 64,
            action="wipe",
            method="ata_secure_erase",
        )
    assert failure.value.code == "maintenance_capability_unavailable"
    with pytest.raises(MaintenanceError) as failure:
        build_plan(
            disk=disk(system=True),
            hardware_snapshot_sha256="a" * 64,
            action="wipe",
            method="quick",
        )
    assert failure.value.code == "system_device_forbidden"


def test_sector_conversion_is_capability_gated_and_bound() -> None:
    plan = build_plan(
        disk=disk(logical=520),
        hardware_snapshot_sha256="b" * 64,
        action="sector_conversion",
        target_logical_bytes=512,
    )
    assert plan["advanced_only"] is True
    changed = deepcopy(plan)
    changed["device"]["serial"] = "REPLACED"
    with pytest.raises(MaintenanceError) as failure:
        validate_plan(changed)
    assert failure.value.code == "maintenance_plan_invalid"


def test_executor_revalidates_before_each_ata_command_and_journals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = build_plan(
        disk=disk(),
        hardware_snapshot_sha256="a" * 64,
        action="wipe",
        method="ata_secure_erase",
    )
    calls = 0

    def inventory() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"disks": [disk()]}

    commands: list[list[str]] = []
    monkeypatch.setattr(executor, "_device_locks", lambda *_args: nullcontext())
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})
    monkeypatch.setattr(executor, "_ensure_not_active", lambda *_args: None)
    monkeypatch.setattr(executor, "_stable_path", lambda *_args: Path("/dev/disk/by-id/ata-test"))
    monkeypatch.setattr(executor, "_tool", lambda name: name)
    result = apply_device_maintenance(
        request(plan),
        paths=Paths(
            transaction_root=tmp_path / "transactions",
            lock_root=tmp_path / "locks",
            quarantine_marker=tmp_path / "missing-quarantine",
        ),
        inventory_provider=inventory,
        runner=lambda command, _timeout: commands.append(command),
    )
    assert calls == 4
    assert [command[3] for command in commands[:2]] == ["--security-set-pass", "--security-erase"]
    assert commands[-1][:2] == ["hdparm", "-I"]
    assert result["completed_actions"] == [
        "maintenance:1",
        "maintenance:2",
        "maintenance:3",
    ]
    status = executor.storage_operation_status(
        result["operation_id"], paths=Paths(transaction_root=tmp_path / "transactions")
    )
    assert status["state"] == "succeeded"
    assert status["percent"] == 100


def test_executor_fails_before_first_command_on_identity_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = build_plan(
        disk=disk(),
        hardware_snapshot_sha256="a" * 64,
        action="wipe",
        method="quick",
    )
    replacement = disk()
    replacement["capacity_bytes"] = 2_000_000_000
    monkeypatch.setattr(executor, "_device_locks", lambda *_args: nullcontext())
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})
    with pytest.raises(ExecutorFailure) as failure:
        apply_device_maintenance(
            request(plan),
            paths=Paths(
                transaction_root=tmp_path / "transactions",
                lock_root=tmp_path / "locks",
                quarantine_marker=tmp_path / "missing-quarantine",
            ),
            inventory_provider=lambda: {"disks": [replacement]},
            runner=lambda *_args: pytest.fail("no command may run"),
        )
    assert failure.value.code == "drive_identity_changed"


def test_executor_verifies_converted_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = build_plan(
        disk=disk(logical=520),
        hardware_snapshot_sha256="a" * 64,
        action="sector_conversion",
        target_logical_bytes=512,
    )
    converted = False

    def inventory() -> dict[str, object]:
        return {"disks": [disk(logical=512 if converted else 520)]}

    def runner(command: list[str], _timeout: int) -> None:
        nonlocal converted
        if command[0] == "sg_format":
            converted = True

    monkeypatch.setattr(executor, "_device_locks", lambda *_args: nullcontext())
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})
    monkeypatch.setattr(executor, "_ensure_not_active", lambda *_args: None)
    monkeypatch.setattr(executor, "_stable_path", lambda *_args: Path("/dev/disk/by-id/scsi-test"))
    monkeypatch.setattr(executor, "_tool", lambda name: name)
    result = apply_device_maintenance(
        request(plan),
        paths=Paths(
            transaction_root=tmp_path / "transactions",
            lock_root=tmp_path / "locks",
            quarantine_marker=tmp_path / "missing-quarantine",
        ),
        inventory_provider=inventory,
        runner=runner,
    )
    assert result["action"] == "sector_conversion"
