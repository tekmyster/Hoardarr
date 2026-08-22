from __future__ import annotations

import pytest

from hoardarr.storage.layouts import (
    LayoutError,
    layout_commands,
    mergerfs_expand_commands,
    normalize_layout,
    normalize_sector_conversion,
    normalize_wipe,
    sector_conversion_commands,
    snapraid_config,
    wipe_commands,
)


def test_zfs_normalizes_multiple_vdevs_and_generates_argv_only_commands() -> None:
    ids = [f"disk-{index}" for index in range(8)]
    plan = normalize_layout(
        "zfs",
        {
            "name": "media",
            "vdevs": [
                {"type": "raidz2", "device_ids": ids[:4]},
                {"type": "raidz2", "device_ids": ids[4:]},
            ],
            "ashift": 12,
            "recordsize": "1M",
            "compression": "zstd",
            "mountpoint": "/mnt/hoardarr/media",
            "snapshots": {"enabled": True, "retention": 12},
        },
        ids,
    )
    assert [item["tolerated_failures"] for item in plan["vdevs"]] == [2, 2]
    commands = layout_commands("zfs", plan, {item: f"/dev/disk/by-id/{item}" for item in ids})
    assert commands[0].argv[:5] == ("zpool", "create", "-f", "-o", "ashift=12")
    assert commands[0].argv.count("raidz2") == 2
    assert commands[1].argv == ("zfs", "snapshot", "media@hoardarr-initial")
    assert commands[0].cancellable_before is False


def test_zfs_auxiliary_roles_are_unique_and_special_devices_are_mirrored() -> None:
    ids = ["a", "b", "special-a", "special-b", "cache", "log-a", "log-b"]
    plan = normalize_layout(
        "zfs",
        {
            "name": "fast",
            "vdevs": [{"type": "mirror", "device_ids": ["a", "b"]}],
            "special": ["special-a", "special-b"],
            "cache": ["cache"],
            "log": ["log-a", "log-b"],
            "mountpoint": "/mnt/hoardarr/fast",
            "scrub_schedule": "weekly",
        },
        ids,
    )
    argv = layout_commands("zfs", plan, {item: f"/dev/disk/by-id/{item}" for item in ids})[0].argv
    assert argv[argv.index("special") + 1] == "mirror"
    assert argv[argv.index("log") + 1] == "mirror"
    with pytest.raises(LayoutError, match="special metadata"):
        normalize_layout(
            "zfs",
            {
                "name": "bad",
                "vdevs": [{"type": "mirror", "device_ids": ["a", "b"]}],
                "special": ["special-a"],
                "mountpoint": "/mnt/hoardarr/bad",
            },
            ["a", "b", "special-a"],
        )


@pytest.mark.parametrize("level,count", [("raid1", 2), ("raid5", 3), ("raid6", 4), ("raid10", 4)])
def test_linux_md_levels_are_validated_and_commanded(level: str, count: int) -> None:
    ids = [f"d{index}" for index in range(count)]
    plan = normalize_layout(
        "raid",
        {
            "name": "vmstore",
            "level": level,
            "device_ids": ids,
            "filesystem": "xfs",
            "mountpoint": "/mnt/hoardarr/vmstore",
        },
        ids,
    )
    commands = layout_commands("raid", plan, {item: f"/dev/disk/by-id/{item}" for item in ids})
    assert commands[0].argv[0] == "mdadm"
    assert f"--level={level.removeprefix('raid')}" in commands[0].argv
    assert commands[1].argv[:3] == ("mkfs.xfs", "-f", "-K")


def test_snapraid_starts_not_synced_and_has_sync_command() -> None:
    plan = normalize_layout(
        "snapraid",
        {
            "name": "archive",
            "data": ["d1", "d2"],
            "parity": ["p1"],
            "mountpoint": "/mnt/hoardarr/archive",
        },
        ["d1", "d2", "p1"],
    )
    assert plan["parity_state"] == "not_synced"
    commands = layout_commands("snapraid", plan, {})
    assert commands[-1].argv[-1] == "sync"


def test_snapraid_multi_parity_config_and_schedules_are_validated() -> None:
    plan = normalize_layout(
        "snapraid",
        {
            "name": "archive",
            "data": ["d1", "d2", "d3"],
            "parity": ["p1", "p2"],
            "mountpoint": "/mnt/hoardarr/archive",
            "sync_schedule": "daily",
            "scrub_schedule": "monthly",
        },
        ["d1", "d2", "d3", "p1", "p2"],
    )
    config = snapraid_config(
        plan,
        {
            "d1": "/mnt/hoardarr/disks/d1",
            "d2": "/mnt/hoardarr/disks/d2",
            "d3": "/mnt/hoardarr/disks/d3",
            "p1": "/mnt/hoardarr/disks/p1",
            "p2": "/mnt/hoardarr/disks/p2",
        },
    )
    assert "parity /mnt/hoardarr/disks/p1/snapraid.parity" in config
    assert "2-parity /mnt/hoardarr/disks/p2/snapraid.parity" in config
    assert config.count("content /mnt/hoardarr/disks/") == 3
    with pytest.raises(LayoutError, match="disabled, daily, or weekly"):
        normalize_layout(
            "snapraid",
            {
                "name": "bad",
                "data": ["d1", "d2"],
                "parity": ["p1"],
                "mountpoint": "/mnt/hoardarr/bad",
                "sync_schedule": "every minute; rm -rf /",
            },
            ["d1", "d2", "p1"],
        )


def test_mixed_layout_builds_two_component_pools_behind_one_mergerfs_namespace() -> None:
    ids = [f"d{index}" for index in range(6)]
    plan = normalize_layout(
        "mixed",
        {
            "name": "media_all",
            "mountpoint": "/mnt/hoardarr/media-all",
            "create_policy": "epmfs",
            "search_policy": "ff",
            "components": [
                {
                    "topology": "zfs",
                    "device_ids": ids[:3],
                    "options": {
                        "name": "media_a",
                        "vdevs": [{"type": "raidz1", "device_ids": ids[:3]}],
                        "mountpoint": "/mnt/hoardarr/media-a",
                    },
                },
                {
                    "topology": "raid",
                    "device_ids": ids[3:],
                    "options": {
                        "name": "media_b",
                        "level": "raid5",
                        "device_ids": ids[3:],
                        "filesystem": "xfs",
                        "mountpoint": "/mnt/hoardarr/media-b",
                    },
                },
            ],
        },
        ids,
    )
    commands = layout_commands(
        "mixed", plan, {identifier: f"/dev/disk/by-id/{identifier}" for identifier in ids}
    )
    assert [command.argv[0] for command in commands].count("zpool") == 1
    assert [command.argv[0] for command in commands].count("mdadm") == 1
    mergerfs = next(command for command in commands if command.argv[0] == "mergerfs")
    assert mergerfs.argv[-2:] == (
        "/mnt/hoardarr/media-a:/mnt/hoardarr/media-b",
        "/mnt/hoardarr/media-all",
    )
    assert "category.create=epmfs" in mergerfs.argv[2]
    assert commands[-1].argv == ("findmnt", "--mountpoint", "/mnt/hoardarr/media-all")


def test_mixed_layout_rejects_duplicate_members_and_overlapping_mounts() -> None:
    base = {
        "name": "all",
        "mountpoint": "/mnt/hoardarr/all",
        "components": [
            {
                "topology": "zfs",
                "device_ids": ["a", "b"],
                "options": {
                    "name": "one",
                    "vdevs": [{"type": "mirror", "device_ids": ["a", "b"]}],
                    "mountpoint": "/mnt/hoardarr/one",
                },
            },
            {
                "topology": "raid",
                "device_ids": ["b", "c"],
                "options": {
                    "name": "two",
                    "level": "raid1",
                    "device_ids": ["b", "c"],
                    "mountpoint": "/mnt/hoardarr/two",
                },
            },
        ],
    }
    with pytest.raises(LayoutError, match="only one component"):
        normalize_layout("mixed", base, ["a", "b", "c"])

    non_overlapping = {
        **base,
        "mountpoint": "/mnt/hoardarr/one/combined",
        "components": [
            base["components"][0],
            {
                **base["components"][1],
                "device_ids": ["c", "d"],
                "options": {**base["components"][1]["options"], "device_ids": ["c", "d"]},
            },
        ],
    }
    with pytest.raises(LayoutError, match="must not overlap"):
        normalize_layout("mixed", non_overlapping, ["a", "b", "c", "d"])


def test_mergerfs_expansion_uses_runtime_xattr_and_verifies() -> None:
    commands = mergerfs_expand_commands(
        "/mnt/hoardarr/media",
        ["/mnt/hoardarr/disks/a", "/mnt/hoardarr/disks/b"],
    )
    assert commands[0].argv[:4] == (
        "setfattr",
        "-n",
        "user.mergerfs.branches",
        "-v",
    )
    assert commands[0].argv[4] == "+>/mnt/hoardarr/disks/a:/mnt/hoardarr/disks/b"
    assert commands[1].argv[0] == "getfattr"


def test_layout_rejects_duplicate_or_unassigned_drive_roles() -> None:
    with pytest.raises(LayoutError):
        normalize_layout(
            "zfs",
            {
                "name": "bad",
                "vdevs": [{"type": "mirror", "device_ids": ["a", "b"]}],
                "mountpoint": "/mnt/hoardarr/bad",
                "cache": ["b"],
            },
            ["a", "b"],
        )


def test_secure_wipe_is_capability_gated_and_never_uses_shell() -> None:
    with pytest.raises(LayoutError):
        normalize_wipe({"method": "nvme_sanitize", "capability": False})
    plan = normalize_wipe({"method": "nvme_sanitize", "capability": True})
    commands = wipe_commands(plan, "/dev/disk/by-id/nvme-test")
    assert commands[0].argv == (
        "nvme",
        "sanitize",
        "/dev/disk/by-id/nvme-test",
        "--sanact=start-block-erase",
        "--wait",
    )
    assert commands[1].argv[:2] == ("nvme", "sanitize-log")
    with pytest.raises(LayoutError):
        wipe_commands(plan, "/dev/nvme0n1")


def test_ata_secure_erase_sets_empty_ephemeral_password_then_verifies() -> None:
    plan = normalize_wipe({"method": "ata_secure_erase", "capability": True})
    commands = wipe_commands(plan, "/dev/disk/by-id/ata-test")
    assert [item.argv[3] for item in commands[:2]] == ["--security-set-pass", "--security-erase"]
    assert commands[0].argv[4] == commands[1].argv[4] == "NULL"
    assert commands[-1].argv[:2] == ("hdparm", "-I")


def test_sector_conversion_requires_verified_passthrough_and_post_check() -> None:
    with pytest.raises(LayoutError):
        normalize_sector_conversion(
            {
                "current_logical_bytes": 520,
                "target_logical_bytes": 512,
                "drive_support": True,
                "controller_passthrough": False,
            }
        )
    plan = normalize_sector_conversion(
        {
            "current_logical_bytes": 528,
            "target_logical_bytes": 4096,
            "drive_support": True,
            "controller_passthrough": True,
        }
    )
    commands = sector_conversion_commands(plan, "/dev/disk/by-id/scsi-test")
    assert commands[0].argv == ("sg_format", "--format", "--size=4096", "/dev/disk/by-id/scsi-test")
    assert commands[1].argv[0] == "sg_readcap"
