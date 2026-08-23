from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from hoardarr.hardware.locate import (
    LocateError,
    build_locate_plan,
    execute_locate_plan,
    revalidate_locate_plan,
)


def _hardware() -> dict:
    return {
        "disks": [
            {
                "id": "wwn:5000c50012345678",
                "stable_identity": True,
                "identity": {"serial": "SANITIZED", "wwn": "5000c50012345678"},
                "connection": {
                    "enclosure_id": "enclosure-6-0",
                    "slot": "3",
                    "mapping_source": "sysfs enclosure_device",
                    "mapping_confidence": "high",
                },
            }
        ]
    }


def test_locate_uses_bounded_slot_query_then_exact_indicator_command(tmp_path: Path) -> None:
    endpoint = tmp_path / "class/enclosure/enclosure-6-0/device/scsi_generic/sg4"
    endpoint.mkdir(parents=True)
    commands: list[list[str]] = []

    def runner(command: list[str]) -> int:
        commands.append(command)
        return 0

    plan = build_locate_plan(_hardware(), device_id="wwn:5000c50012345678", enabled=True)
    result = execute_locate_plan(plan, _hardware(), sysfs_root=tmp_path, runner=runner)

    assert commands == [
        ["sg_ses", "--dev-slot-num=3", "--get=ident", "--readonly", "/dev/sg4"],
        ["sg_ses", "--dev-slot-num=3", "--set=ident", "/dev/sg4"],
    ]
    assert result["enabled"] is True
    assert result["verification"] == "command accepted after read-only slot query"


def test_locate_fails_closed_for_inferred_mapping_identity_drift_and_ambiguous_endpoint(
    tmp_path: Path,
) -> None:
    inferred = _hardware()
    inferred["disks"][0]["connection"]["mapping_confidence"] = "medium"
    with pytest.raises(LocateError, match="confirmed"):
        build_locate_plan(inferred, device_id="wwn:5000c50012345678", enabled=True)

    plan = build_locate_plan(_hardware(), device_id="wwn:5000c50012345678", enabled=True)
    moved = deepcopy(_hardware())
    moved["disks"][0]["connection"]["slot"] = "4"
    with pytest.raises(LocateError, match="changed"):
        revalidate_locate_plan(plan, moved)

    for name in ("sg4", "sg5"):
        (tmp_path / f"class/enclosure/enclosure-6-0/device/scsi_generic/{name}").mkdir(
            parents=True, exist_ok=True
        )
    with pytest.raises(LocateError, match="exactly one"):
        execute_locate_plan(plan, _hardware(), sysfs_root=tmp_path, runner=lambda _command: 0)


def test_locate_does_not_send_control_when_read_only_slot_check_fails(tmp_path: Path) -> None:
    (tmp_path / "class/enclosure/enclosure-6-0/device/scsi_generic/sg4").mkdir(parents=True)
    commands: list[list[str]] = []

    def runner(command: list[str]) -> int:
        commands.append(command)
        return 2

    plan = build_locate_plan(_hardware(), device_id="wwn:5000c50012345678", enabled=False)
    with pytest.raises(LocateError, match="confirm"):
        execute_locate_plan(plan, _hardware(), sysfs_root=tmp_path, runner=runner)
    assert len(commands) == 1
    assert commands[0][2] == "--get=ident"
