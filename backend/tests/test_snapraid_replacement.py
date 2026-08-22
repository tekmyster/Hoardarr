from __future__ import annotations

import pytest

from hoardarr.storage.snapraid import (
    SnapraidReplacementError,
    build_replacement_plan,
    data_entries,
    replace_data_entry,
    validate_replacement_plan,
)


def _disk() -> dict[str, object]:
    return {
        "id": "wwn:replacement",
        "stable_identity": True,
        "system_device": False,
        "selectable": True,
        "read_only": False,
        "vendor": "TEST",
        "model": "DISK",
        "identity": {"serial": "SERIAL", "wwn": "replacement", "eui64": None, "nguid": None},
        "capacity_bytes": 4_000_000_000,
        "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
    }


def test_replacement_plan_binds_config_and_device() -> None:
    config = "parity /mnt/parity/snapraid.parity\ndata d1 /mnt/old-one\ndata d2 /mnt/two\n"
    plan = build_replacement_plan(
        pool_name="media",
        data_name="d1",
        config=config,
        disk=_disk(),
        hardware_snapshot_sha256="a" * 64,
        filesystem="ext4",
    )
    assert validate_replacement_plan(plan) == plan
    changed = replace_data_entry(config, data_name="d1", new_path=plan["replacement_mount"])
    assert data_entries(changed)["d1"] == plan["replacement_mount"]
    assert data_entries(changed)["d2"] == "/mnt/two"


@pytest.mark.parametrize(
    "config",
    [
        "data d1 relative\n",
        "data d1 /mnt/one\ndata d1 /mnt/two\n",
        "data ../bad /mnt/one\n",
    ],
)
def test_config_parser_fails_closed(config: str) -> None:
    with pytest.raises(SnapraidReplacementError):
        data_entries(config)


def test_replacement_rejects_system_drive_and_missing_data_name() -> None:
    disk = _disk()
    disk["system_device"] = True
    with pytest.raises(SnapraidReplacementError):
        build_replacement_plan(
            pool_name="media",
            data_name="d1",
            config="data d1 /mnt/old\n",
            disk=disk,
            hardware_snapshot_sha256="a" * 64,
            filesystem="ext4",
        )
    with pytest.raises(SnapraidReplacementError):
        replace_data_entry("data d1 /mnt/old\n", data_name="d2", new_path="/mnt/new")
