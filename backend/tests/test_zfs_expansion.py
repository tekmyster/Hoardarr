from __future__ import annotations

import pytest

from hoardarr.storage.layouts import LayoutError
from hoardarr.storage.zfs import parse_zpool_data_topology, zfs_add_vdev_commands

MIRROR_STATUS = """
  pool: media
 state: ONLINE
config:

        NAME                                      STATE     READ WRITE CKSUM
        media                                     ONLINE       0     0     0
          mirror-0                                ONLINE       0     0     0
            /dev/disk/by-id/scsi-disk-a           ONLINE       0     0     0
            /dev/disk/by-id/scsi-disk-b           ONLINE       0     0     0
        logs
          /dev/disk/by-id/scsi-log                ONLINE       0     0     0
        cache
          /dev/disk/by-id/scsi-cache              ONLINE       0     0     0

errors: No known data errors
"""


def test_zpool_topology_parser_uses_only_uniform_data_vdevs() -> None:
    parsed = parse_zpool_data_topology(MIRROR_STATUS, "media")

    assert parsed.quality == "available"
    assert parsed.vdev_type == "mirror"
    assert parsed.vdev_width == 2
    assert parsed.vdev_count == 1
    assert parsed.member_paths == (
        "/dev/disk/by-id/scsi-disk-a",
        "/dev/disk/by-id/scsi-disk-b",
    )
    assert parsed.config_sha256 is not None
    assert "scsi-log" not in str(parsed.document())
    assert "scsi-cache" not in str(parsed.document())


def test_zpool_topology_parser_refuses_mixed_or_striped_geometry() -> None:
    mixed = MIRROR_STATUS.replace(
        "        logs",
        "          raidz1-1                               ONLINE       0     0     0\n"
        "            /dev/disk/by-id/scsi-disk-c           ONLINE       0     0     0\n"
        "            /dev/disk/by-id/scsi-disk-d           ONLINE       0     0     0\n"
        "            /dev/disk/by-id/scsi-disk-e           ONLINE       0     0     0\n"
        "        logs",
    )
    assert parse_zpool_data_topology(mixed, "media").quality == "unsupported"

    striped = MIRROR_STATUS.replace(
        "          mirror-0                                ONLINE       0     0     0\n",
        "",
    )
    assert parse_zpool_data_topology(striped, "media").quality == "unsupported"


def test_zfs_add_vdev_commands_dry_run_then_mutate_without_force() -> None:
    commands = zfs_add_vdev_commands(
        pool_name="media",
        vdev_type="mirror",
        device_ids=["wwn:a", "wwn:b"],
        device_paths={
            "wwn:a": "/dev/disk/by-id/scsi-a",
            "wwn:b": "/dev/disk/by-id/scsi-b",
        },
    )

    assert commands[0].argv == (
        "zpool",
        "add",
        "-n",
        "media",
        "mirror",
        "/dev/disk/by-id/scsi-a",
        "/dev/disk/by-id/scsi-b",
    )
    assert commands[1].argv == (
        "zpool",
        "add",
        "media",
        "mirror",
        "/dev/disk/by-id/scsi-a",
        "/dev/disk/by-id/scsi-b",
    )
    assert "-f" not in commands[1].argv
    assert commands[1].cancellable_before is False
    assert commands[2].argv == ("zpool", "status", "-P", "media")


@pytest.mark.parametrize(
    ("vdev_type", "members"),
    [("mirror", 1), ("raidz1", 2), ("raidz2", 3), ("raidz3", 4)],
)
def test_zfs_add_vdev_commands_reject_incomplete_geometry(vdev_type: str, members: int) -> None:
    device_ids = [f"wwn:{index}" for index in range(members)]
    with pytest.raises(LayoutError):
        zfs_add_vdev_commands(
            pool_name="media",
            vdev_type=vdev_type,
            device_ids=device_ids,
            device_paths={
                identity: f"/dev/disk/by-id/scsi-{index}"
                for index, identity in enumerate(device_ids)
            },
        )
