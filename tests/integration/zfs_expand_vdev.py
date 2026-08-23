from __future__ import annotations

import argparse
import json
from pathlib import Path

from hoardarr.storage.executor import _live_zfs_pool_state, _run
from hoardarr.storage.zfs import zfs_add_vdev_commands


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exercise Hoardarr's no-force existing-ZFS expansion boundary."
    )
    parser.add_argument("--pool", required=True)
    parser.add_argument("--vdev-type", required=True)
    parser.add_argument("--expected-guid", required=True)
    parser.add_argument("--expected-config-sha256", required=True)
    parser.add_argument("--expected-vdev-count", required=True, type=int)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("devices", nargs="+")
    arguments = parser.parse_args()
    before = _live_zfs_pool_state(arguments.pool)
    expected = {
        "pool_guid": arguments.expected_guid,
        "config_sha256": arguments.expected_config_sha256,
        "vdev_type": arguments.vdev_type,
        "vdev_count": arguments.expected_vdev_count,
    }
    if any(before.get(key) != value for key, value in expected.items()):
        raise SystemExit("reviewed ZFS identity/topology changed before execution")
    commands = zfs_add_vdev_commands(
        pool_name=arguments.pool,
        vdev_type=arguments.vdev_type,
        device_ids=list(arguments.devices),
        device_paths={device: device for device in arguments.devices},
    )
    if any("-f" in command.argv or "create" in command.argv for command in commands):
        raise SystemExit("unsafe ZFS expansion command generated")
    for command in commands:
        _run(list(command.argv), command.timeout_seconds)
    after = _live_zfs_pool_state(arguments.pool)
    if (
        after.get("pool_guid") != arguments.expected_guid
        or after.get("vdev_type") != arguments.vdev_type
        or after.get("vdev_width") != len(arguments.devices)
        or after.get("vdev_count") != arguments.expected_vdev_count + 1
        or after.get("config_sha256") == arguments.expected_config_sha256
    ):
        raise SystemExit("expanded ZFS topology did not match the immutable plan")
    arguments.evidence.parent.mkdir(parents=True, exist_ok=True)
    arguments.evidence.write_text(
        json.dumps(
            {
                "classification": "VERIFIED IN ISOLATION",
                "source": "disposable Linux loop devices",
                "pool": arguments.pool,
                "pool_guid_before": before["pool_guid"],
                "pool_guid_after": after["pool_guid"],
                "config_sha256_before": before["config_sha256"],
                "config_sha256_after": after["config_sha256"],
                "vdev_type": after["vdev_type"],
                "vdev_width": after["vdev_width"],
                "vdev_count_before": before["vdev_count"],
                "vdev_count_after": after["vdev_count"],
                "dry_run_executed": commands[0].argv[2] == "-n",
                "force_override_used": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
