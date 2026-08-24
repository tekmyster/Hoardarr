from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from hoardarr.operations.service import document_hash
from hoardarr.storage.zfs import valid_pool_guid

_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_PURPOSES = frozenset({"media", "downloads", "archive", "backup", "general", "vm"})
_MINIMUM_ZVOL_BYTES = 1024 * 1024 * 1024
_CAPACITY_RESERVE_BYTES = 1024 * 1024 * 1024
_COMPRESSIONS = frozenset({"off", "lz4", "zstd", "zstd-1", "zstd-3", "zstd-6"})
_RECORDSIZES = frozenset({"16K", "32K", "64K", "128K", "256K", "512K", "1M"})
_VOLBLOCKSIZES = frozenset({"4K", "8K", "16K", "32K", "64K", "128K"})


class VolumePlanError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _zfs_candidates(pools: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(
        (
            item
            for item in pools[:1024]
            if item.get("type") == "ZFS"
            and isinstance(item.get("name"), str)
            and valid_pool_guid(item.get("pool_guid"))
        ),
        key=lambda item: (-int(item.get("free_bytes") or 0), str(item["name"])),
    )


def build_guided_volume_plan(
    pools: Sequence[Mapping[str, Any]],
    *,
    name: str,
    purpose: str,
    pool_id: str | None = None,
    size_bytes: int | None = None,
    advanced: bool = False,
    resource_type: str | None = None,
    compression: str | None = None,
    recordsize: str | None = None,
    atime: str | None = None,
    mountpoint: str | None = None,
    volblocksize: str | None = None,
    sparse: bool | None = None,
) -> dict[str, Any]:
    name = name.strip().lower()
    purpose = purpose.strip().lower()
    if not _NAME.fullmatch(name):
        raise VolumePlanError(
            "volume_name_invalid",
            "Use a lower-case name containing letters, numbers, dashes, or underscores.",
        )
    if purpose not in _PURPOSES:
        raise VolumePlanError("volume_purpose_invalid", "The storage purpose is unsupported.")
    if size_bytes is not None and (
        not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes <= 0
    ):
        raise VolumePlanError("volume_size_invalid", "Volume size must be a positive byte count.")
    overrides = {compression, recordsize, atime, mountpoint, volblocksize, sparse, resource_type}
    if not advanced and any(value is not None for value in overrides):
        raise VolumePlanError(
            "volume_advanced_settings_disabled",
            "Provider settings require Advanced customization.",
        )
    if resource_type is not None and resource_type not in {"dataset", "zvol"}:
        raise VolumePlanError("volume_resource_type_invalid", "The ZFS resource type is invalid.")
    if compression is not None and compression not in _COMPRESSIONS:
        raise VolumePlanError(
            "volume_compression_invalid", "The ZFS compression setting is invalid."
        )
    if recordsize is not None and recordsize not in _RECORDSIZES:
        raise VolumePlanError("volume_recordsize_invalid", "The ZFS record size is invalid.")
    if atime is not None and atime not in {"on", "off"}:
        raise VolumePlanError("volume_atime_invalid", "The ZFS access-time setting is invalid.")
    if volblocksize is not None and volblocksize not in _VOLBLOCKSIZES:
        raise VolumePlanError(
            "volume_volblocksize_invalid", "The ZFS volume block size is invalid."
        )
    if mountpoint is not None and (
        not mountpoint.startswith(("/srv/", "/mnt/", "/media/"))
        or ".." in mountpoint.split("/")
        or any(ord(char) < 33 for char in mountpoint)
    ):
        raise VolumePlanError(
            "volume_mountpoint_invalid",
            "Use an absolute storage path below /srv, /mnt, or /media without traversal.",
        )

    candidates = _zfs_candidates(pools)
    if pool_id is not None:
        candidates = [item for item in candidates if item.get("id") == pool_id]
    if not candidates:
        raise VolumePlanError(
            "volume_backend_unavailable",
            "No compatible online ZFS storage with a stable pool identity was detected.",
        )
    pool = candidates[0]
    free_bytes = int(pool.get("free_bytes") or 0)
    blockers: list[dict[str, str]] = []
    if str(pool.get("status", "")).casefold() != "online" or pool.get("degraded") is True:
        blockers.append(
            {
                "code": "volume_pool_not_healthy",
                "message": "The selected storage pool is not healthy enough for a new volume.",
            }
        )
    if free_bytes <= _CAPACITY_RESERVE_BYTES:
        blockers.append(
            {
                "code": "volume_capacity_insufficient",
                "message": "The selected pool does not have enough free capacity after reserve.",
            }
        )

    is_block = (
        resource_type == "zvol" if advanced and resource_type is not None else purpose == "vm"
    )
    if is_block and (size_bytes is None or size_bytes < _MINIMUM_ZVOL_BYTES):
        blockers.append(
            {
                "code": "volume_size_required",
                "message": "VM storage requires a size of at least 1 GiB.",
            }
        )
    if size_bytes is not None and size_bytes > max(0, free_bytes - _CAPACITY_RESERVE_BYTES):
        blockers.append(
            {
                "code": "volume_capacity_insufficient",
                "message": "The requested size would consume the pool's safety reserve.",
            }
        )
    if not is_block and size_bytes is not None:
        blockers.append(
            {
                "code": "volume_size_not_applicable",
                "message": (
                    "A dataset grows from its pool; an explicit size applies only to block storage."
                ),
            }
        )

    resource_type = "zvol" if is_block else "dataset"
    provider_resource_id = f"{pool['name']}/{name}"
    properties: dict[str, object]
    if is_block:
        properties = {
            "compression": compression or "zstd",
            "volblocksize": volblocksize or "16K",
            "sparse": True if sparse is None else sparse,
        }
    else:
        properties = {
            "compression": compression or "zstd",
            "recordsize": recordsize
            or ("1M" if purpose in {"media", "archive", "backup"} else "128K"),
            "atime": atime or "off",
            "mountpoint": mountpoint or f"/srv/hoardarr/volumes/{name}",
        }
    plan = {
        "schema_version": 1,
        "kind": "storage.volume.create",
        "mode": "advanced" if advanced else "guided",
        "name": name,
        "purpose": purpose,
        "provider": "zfs",
        "resource_type": resource_type,
        "provider_resource_id": provider_resource_id,
        "presentation": "block" if is_block else "file",
        "parent": {
            "pool_id": pool["id"],
            "pool_name": pool["name"],
            "pool_guid": pool["pool_guid"],
            "free_bytes_at_preview": free_bytes,
        },
        "size_bytes": size_bytes,
        "properties": properties,
        "blockers": blockers,
        "ready": not blockers,
        "explanation": _explanation(purpose, is_block),
    }
    return {**plan, "plan_sha256": document_hash(plan)}


def validate_guided_volume_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(plan))
    supplied_hash = raw.pop("plan_sha256", None)
    if not isinstance(supplied_hash, str) or document_hash(raw) != supplied_hash:
        raise VolumePlanError("volume_plan_changed", "The volume plan changed after review.")
    parent = raw.get("parent")
    if not isinstance(parent, Mapping):
        raise VolumePlanError("volume_plan_invalid", "The volume plan is incomplete.")
    rebuilt = build_guided_volume_plan(
        [
            {
                "id": parent.get("pool_id"),
                "name": parent.get("pool_name"),
                "type": "ZFS",
                "status": "online" if not raw.get("blockers") else "unknown",
                "pool_guid": parent.get("pool_guid"),
                "free_bytes": parent.get("free_bytes_at_preview"),
                "degraded": False,
            }
        ],
        name=str(raw.get("name", "")),
        purpose=str(raw.get("purpose", "")),
        pool_id=str(parent.get("pool_id", "")),
        size_bytes=raw.get("size_bytes"),
        advanced=raw.get("mode") == "advanced",
        resource_type=str(raw.get("resource_type", "")) if raw.get("mode") == "advanced" else None,
        **(
            {
                "compression": raw.get("properties", {}).get("compression"),
                "recordsize": raw.get("properties", {}).get("recordsize"),
                "atime": raw.get("properties", {}).get("atime"),
                "mountpoint": raw.get("properties", {}).get("mountpoint"),
                "volblocksize": raw.get("properties", {}).get("volblocksize"),
                "sparse": raw.get("properties", {}).get("sparse"),
            }
            if raw.get("mode") == "advanced" and isinstance(raw.get("properties"), Mapping)
            else {}
        ),
    )
    if rebuilt != dict(plan):
        raise VolumePlanError("volume_plan_changed", "The volume plan changed after review.")
    return rebuilt


def volume_create_command(plan: Mapping[str, Any]) -> list[str]:
    validated = validate_guided_volume_plan(plan)
    if validated["ready"] is not True or validated["blockers"]:
        raise VolumePlanError("volume_plan_blocked", "The volume plan has unresolved blockers.")
    properties = validated["properties"]
    if not isinstance(properties, Mapping):
        raise VolumePlanError("volume_plan_invalid", "The volume properties are invalid.")
    command = ["zfs", "create"]
    if validated["resource_type"] == "zvol":
        command.extend(["-V", str(validated["size_bytes"])])
    for name in sorted(properties):
        value = properties[name]
        if isinstance(value, bool):
            value = "on" if value else "off"
        command.extend(["-o", f"{name}={value}"])
    command.append(str(validated["provider_resource_id"]))
    return command


def _explanation(purpose: str, is_block: bool) -> str:
    if is_block:
        return "Creates dedicated block storage for a VM without changing the parent pool."
    labels = {
        "media": "large media files",
        "downloads": "downloads and temporary processing",
        "archive": "long-lived archive files",
        "backup": "backup content",
        "general": "general files and folders",
    }
    return f"Creates a separate storage area tuned for {labels[purpose]}."
