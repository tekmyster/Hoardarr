from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from hoardarr.operations.service import document_hash
from hoardarr.storage.maintenance import IDENTITY_FIELDS, reviewed_device

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,62}")
_DATA_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}")


class SnapraidReplacementError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def data_entries(config: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in config.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=2)
        if len(parts) == 3 and parts[0] == "data":
            name, path = parts[1:]
            parsed = PurePosixPath(path)
            if not _DATA_NAME.fullmatch(name) or not parsed.is_absolute() or ".." in parsed.parts:
                raise SnapraidReplacementError(
                    "snapraid_config_invalid", "The SnapRAID data configuration is unsafe."
                )
            if name in result:
                raise SnapraidReplacementError(
                    "snapraid_config_invalid", "The SnapRAID data name is duplicated."
                )
            result[name] = path
    return result


def replace_data_entry(config: str, *, data_name: str, new_path: str) -> str:
    if not _DATA_NAME.fullmatch(data_name):
        raise SnapraidReplacementError("snapraid_data_invalid", "Invalid SnapRAID data name.")
    path = PurePosixPath(new_path)
    if not path.is_absolute() or ".." in path.parts:
        raise SnapraidReplacementError("snapraid_path_invalid", "Invalid replacement path.")
    entries = data_entries(config)
    if data_name not in entries:
        raise SnapraidReplacementError(
            "snapraid_data_not_found", "The selected SnapRAID data disk was not found."
        )
    changed = 0
    lines: list[str] = []
    for raw in config.splitlines():
        parts = raw.strip().split(maxsplit=2)
        if len(parts) == 3 and parts[0] == "data" and parts[1] == data_name:
            lines.append(f"data {data_name} {path}")
            changed += 1
        else:
            lines.append(raw)
    if changed != 1:
        raise SnapraidReplacementError(
            "snapraid_config_invalid", "The SnapRAID data configuration changed unexpectedly."
        )
    return "\n".join(lines) + "\n"


def build_replacement_plan(
    *,
    pool_name: str,
    data_name: str,
    config: str,
    disk: Mapping[str, Any],
    hardware_snapshot_sha256: str,
    filesystem: str,
) -> dict[str, Any]:
    if not _NAME.fullmatch(pool_name):
        raise SnapraidReplacementError("snapraid_pool_invalid", "Invalid SnapRAID name.")
    entries = data_entries(config)
    if data_name not in entries:
        raise SnapraidReplacementError(
            "snapraid_data_not_found", "The selected SnapRAID data disk was not found."
        )
    if filesystem not in {"ext4", "xfs", "btrfs"}:
        raise SnapraidReplacementError("filesystem_invalid", "Unsupported replacement filesystem.")
    device = reviewed_device(disk)
    if device["stable_identity"] is not True or not isinstance(device["id"], str):
        raise SnapraidReplacementError(
            "drive_identity_unstable", "The drive has no stable identity."
        )
    if (
        any(disk.get(field) is True for field in ("system_device", "system_disk", "read_only"))
        or disk.get("selectable") is False
    ):
        raise SnapraidReplacementError("system_device_forbidden", "System storage cannot be used.")
    suffix = hashlib.sha256(device["id"].encode()).hexdigest()[:16]
    replacement_mount = f"/mnt/hoardarr/disks/snapraid-{pool_name}-{data_name}-{suffix}"
    plan = {
        "schema_version": 1,
        "kind": "snapraid_replacement",
        "pool_name": pool_name,
        "data_name": data_name,
        "old_path": entries[data_name],
        "replacement_mount": replacement_mount,
        "filesystem": filesystem,
        "config_sha256": hashlib.sha256(config.encode()).hexdigest(),
        "device": device,
        "hardware_snapshot_sha256": hardware_snapshot_sha256,
        "destructive": True,
    }
    return {**plan, "device_binding_sha256": document_hash(device)}


def validate_replacement_plan(plan: object) -> dict[str, Any]:
    fields = {
        "schema_version",
        "kind",
        "pool_name",
        "data_name",
        "old_path",
        "replacement_mount",
        "filesystem",
        "config_sha256",
        "device",
        "device_binding_sha256",
        "hardware_snapshot_sha256",
        "destructive",
    }
    if not isinstance(plan, dict) or set(plan) != fields:
        raise SnapraidReplacementError("snapraid_plan_invalid", "Invalid replacement plan.")
    device = plan.get("device")
    if (
        plan.get("schema_version") != 1
        or plan.get("kind") != "snapraid_replacement"
        or plan.get("destructive") is not True
        or not _NAME.fullmatch(str(plan.get("pool_name", "")))
        or not _DATA_NAME.fullmatch(str(plan.get("data_name", "")))
        or plan.get("filesystem") not in {"ext4", "xfs", "btrfs"}
        or not isinstance(plan.get("config_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", str(plan.get("config_sha256")))
        or not isinstance(device, dict)
        or set(device) != set(IDENTITY_FIELDS)
        or device.get("stable_identity") is not True
        or document_hash(device) != plan.get("device_binding_sha256")
    ):
        raise SnapraidReplacementError("snapraid_plan_invalid", "Invalid replacement plan.")
    mount = PurePosixPath(str(plan.get("replacement_mount", "")))
    old = PurePosixPath(str(plan.get("old_path", "")))
    if (
        not mount.is_absolute()
        or not old.is_absolute()
        or ".." in mount.parts
        or ".." in old.parts
        or not str(mount).startswith("/mnt/hoardarr/disks/")
    ):
        raise SnapraidReplacementError("snapraid_plan_invalid", "Invalid replacement paths.")
    return plan
