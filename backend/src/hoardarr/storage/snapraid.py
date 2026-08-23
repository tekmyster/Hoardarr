from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from hoardarr.operations.service import document_hash
from hoardarr.storage.layouts import CommandSpec
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


def recovery_commands(*, config_path: str, data_name: str) -> list[CommandSpec]:
    """Return the official lost-data-disk recovery sequence as typed argv.

    The audit-only check deliberately runs before sync: once sync succeeds, the
    prior recovery state can no longer be retried according to SnapRAID's
    documented recovery workflow.
    """

    path = PurePosixPath(config_path)
    if not path.is_absolute() or ".." in path.parts or not _DATA_NAME.fullmatch(data_name):
        raise SnapraidReplacementError(
            "snapraid_recovery_invalid", "The SnapRAID recovery target is invalid."
        )
    prefix = ("snapraid", "-c", str(path))
    return [
        CommandSpec((*prefix, "status"), 300, "Validating replacement configuration"),
        CommandSpec(
            (*prefix, "-d", data_name, "fix"),
            86400,
            "Reconstructing missing SnapRAID data",
            False,
        ),
        CommandSpec(
            (*prefix, "-d", data_name, "-a", "check"),
            86400,
            "Verifying reconstructed SnapRAID data",
            False,
        ),
        CommandSpec((*prefix, "sync"), 86400, "Synchronizing SnapRAID parity", False),
    ]


def existing_data_summary(disk: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bounded destructive-review evidence exposed by discovery."""

    raw_partitions = disk.get("partitions")
    partition_count = len(raw_partitions) if isinstance(raw_partitions, list) else 0
    raw_signatures = disk.get("signatures")
    signature_types: list[str] = []
    if isinstance(raw_signatures, list):
        for item in raw_signatures[:32]:
            value = item.get("type") if isinstance(item, Mapping) else item
            if isinstance(value, str):
                cleaned = " ".join(value.split())[:64]
                if cleaned and cleaned not in signature_types:
                    signature_types.append(cleaned)
    raw_scan = disk.get("signature_scan")
    scan_status = (
        raw_scan.get("status")
        if isinstance(raw_scan, Mapping)
        and raw_scan.get("status") in {"complete", "partial", "unavailable"}
        else "unavailable"
    )
    return {
        "detected": partition_count > 0 or bool(signature_types),
        "partition_count": partition_count,
        "signature_types": signature_types,
        "scan_status": scan_status,
    }


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
    existing_data = existing_data_summary(disk)
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
        "existing_data": existing_data,
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
        "existing_data",
        "destructive",
    }
    if not isinstance(plan, dict) or set(plan) != fields:
        raise SnapraidReplacementError("snapraid_plan_invalid", "Invalid replacement plan.")
    device = plan.get("device")
    existing_data = plan.get("existing_data")
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
        or not isinstance(existing_data, dict)
        or set(existing_data)
        != {"detected", "partition_count", "signature_types", "scan_status"}
        or not isinstance(existing_data.get("detected"), bool)
        or not isinstance(existing_data.get("partition_count"), int)
        or not 0 <= existing_data["partition_count"] <= 4096
        or not isinstance(existing_data.get("signature_types"), list)
        or len(existing_data["signature_types"]) > 32
        or any(
            not isinstance(value, str) or not 1 <= len(value) <= 64
            for value in existing_data["signature_types"]
        )
        or existing_data.get("scan_status") not in {"complete", "partial", "unavailable"}
        or existing_data["detected"]
        != (existing_data["partition_count"] > 0 or bool(existing_data["signature_types"]))
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
