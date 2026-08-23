from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from hoardarr.operations.service import document_hash
from hoardarr.storage.maintenance import IDENTITY_FIELDS, reviewed_device
from hoardarr.storage.snapraid import existing_data_summary

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,254}$")
_MD_NAME = re.compile(r"^md[0-9]+$")
_MD_UUID = re.compile(r"^[0-9a-fA-F:-]{8,128}$")
_DEVICE = re.compile(
    r"^/dev/(?:disk/by-id/[A-Za-z0-9._:+-]+|mapper/[A-Za-z0-9._+-]+|[A-Za-z0-9._+-]+)$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LEVELS = frozenset({"raid1", "raid5", "raid6", "raid10"})


class ArrayReplacementError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def configuration_hash(configuration: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _safe_replacement(disk: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    device = reviewed_device(disk)
    if device["stable_identity"] is not True or not isinstance(device["id"], str):
        raise ArrayReplacementError("drive_identity_unstable", "The drive has no stable identity.")
    if (
        any(disk.get(field) is True for field in ("system_device", "system_disk", "read_only"))
        or disk.get("selectable") is False
    ):
        raise ArrayReplacementError("system_device_forbidden", "System storage cannot be used.")
    return device, existing_data_summary(disk)


def build_zfs_replacement_plan(
    *,
    pool: Mapping[str, Any],
    member_path: str,
    disk: Mapping[str, Any],
    hardware_snapshot_sha256: str,
) -> dict[str, Any]:
    name = pool.get("name")
    guid = pool.get("pool_guid")
    configuration = pool.get("configuration")
    if (
        not isinstance(name, str)
        or not _NAME.fullmatch(name)
        or not isinstance(guid, str)
        or not guid.isdigit()
        or not isinstance(configuration, Mapping)
        or configuration.get("quality") != "available"
        or member_path not in configuration.get("member_paths", [])
        or not _DEVICE.fullmatch(member_path)
        or not _SHA256.fullmatch(str(configuration.get("config_sha256", "")))
    ):
        raise ArrayReplacementError(
            "zfs_replacement_unavailable",
            "The selected ZFS member does not have a complete authoritative pool binding.",
        )
    device, existing_data = _safe_replacement(disk)
    capacities = configuration.get("member_capacities")
    minimum_capacity = capacities.get(member_path) if isinstance(capacities, Mapping) else None
    if isinstance(minimum_capacity, int) and device.get("capacity_bytes", 0) < minimum_capacity:
        raise ArrayReplacementError(
            "replacement_too_small", "The replacement drive is smaller than the selected member."
        )
    plan = {
        "schema_version": 1,
        "kind": "array_replacement",
        "provider": "zfs",
        "target_id": f"zfs:{name}",
        "target_name": name,
        "target_identity": guid,
        "configuration_sha256": str(configuration["config_sha256"]),
        "level": str(configuration.get("vdev_type")),
        "member_count": len(configuration.get("member_paths", [])),
        "degraded": bool(pool.get("degraded")),
        "old_member_path": member_path,
        "minimum_capacity_bytes": minimum_capacity if isinstance(minimum_capacity, int) else None,
        "device": device,
        "device_binding_sha256": document_hash(device),
        "hardware_snapshot_sha256": hardware_snapshot_sha256,
        "existing_data": existing_data,
        "destructive": True,
    }
    return plan


def build_md_replacement_plan(
    *,
    array: Mapping[str, Any],
    member_path: str | None,
    disk: Mapping[str, Any],
    hardware_snapshot_sha256: str,
) -> dict[str, Any]:
    name = array.get("name")
    configuration = array.get("configuration")
    if (
        not isinstance(name, str)
        or not _MD_NAME.fullmatch(name)
        or not isinstance(configuration, Mapping)
        or not _MD_UUID.fullmatch(str(configuration.get("array_uuid", "")))
        or configuration.get("level") not in _LEVELS
        or not isinstance(configuration.get("raid_disks"), int)
        or not _SHA256.fullmatch(str(configuration.get("config_sha256", "")))
    ):
        raise ArrayReplacementError(
            "md_replacement_unavailable",
            "The Linux MD array does not have a complete authoritative identity and geometry.",
        )
    members = configuration.get("member_paths", [])
    if member_path is not None and (
        member_path not in members or not _DEVICE.fullmatch(member_path)
    ):
        raise ArrayReplacementError("md_member_invalid", "The selected MD member is not current.")
    degraded = bool(array.get("degraded"))
    if not degraded and member_path is None:
        raise ArrayReplacementError(
            "md_member_required", "Select the online MD member that is being proactively replaced."
        )
    device, existing_data = _safe_replacement(disk)
    plan = {
        "schema_version": 1,
        "kind": "array_replacement",
        "provider": "linux_md",
        "target_id": f"md:{name}",
        "target_name": name,
        "target_identity": str(configuration["array_uuid"]),
        "configuration_sha256": str(configuration["config_sha256"]),
        "level": str(configuration["level"]),
        "member_count": int(configuration["raid_disks"]),
        "degraded": degraded,
        "old_member_path": member_path,
        "minimum_capacity_bytes": None,
        "device": device,
        "device_binding_sha256": document_hash(device),
        "hardware_snapshot_sha256": hardware_snapshot_sha256,
        "existing_data": existing_data,
        "destructive": True,
    }
    return plan


def validate_array_replacement_plan(value: object) -> dict[str, Any]:
    fields = {
        "schema_version",
        "kind",
        "provider",
        "target_id",
        "target_name",
        "target_identity",
        "configuration_sha256",
        "level",
        "member_count",
        "degraded",
        "old_member_path",
        "minimum_capacity_bytes",
        "device",
        "device_binding_sha256",
        "hardware_snapshot_sha256",
        "existing_data",
        "destructive",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ArrayReplacementError("array_replacement_plan_invalid", "Invalid replacement plan.")
    provider = value.get("provider")
    name = value.get("target_name")
    old_path = value.get("old_member_path")
    minimum = value.get("minimum_capacity_bytes")
    device = value.get("device")
    existing = value.get("existing_data")
    valid_name = isinstance(name, str) and (
        _NAME.fullmatch(name) if provider == "zfs" else _MD_NAME.fullmatch(name)
    )
    if (
        value.get("schema_version") != 1
        or value.get("kind") != "array_replacement"
        or provider not in {"zfs", "linux_md"}
        or not valid_name
        or value.get("target_id") != (f"zfs:{name}" if provider == "zfs" else f"md:{name}")
        or not isinstance(value.get("target_identity"), str)
        or not _SHA256.fullmatch(str(value.get("configuration_sha256", "")))
        or value.get("level")
        not in ({"mirror", "raidz1", "raidz2", "raidz3"} if provider == "zfs" else _LEVELS)
        or not isinstance(value.get("member_count"), int)
        or not 1 <= value["member_count"] <= 1024
        or not isinstance(value.get("degraded"), bool)
        or (
            old_path is not None
            and (not isinstance(old_path, str) or not _DEVICE.fullmatch(old_path))
        )
        or (provider == "zfs" and old_path is None)
        or (minimum is not None and (not isinstance(minimum, int) or minimum <= 0))
        or not isinstance(device, dict)
        or set(device) != set(IDENTITY_FIELDS)
        or device.get("stable_identity") is not True
        or document_hash(device) != value.get("device_binding_sha256")
        or not isinstance(existing, dict)
        or set(existing) != {"detected", "partition_count", "signature_types", "scan_status"}
        or existing.get("scan_status") not in {"complete", "partial", "unavailable"}
        or not isinstance(existing.get("detected"), bool)
        or not isinstance(existing.get("partition_count"), int)
        or not isinstance(existing.get("signature_types"), list)
        or value.get("destructive") is not True
    ):
        raise ArrayReplacementError("array_replacement_plan_invalid", "Invalid replacement plan.")
    return value
