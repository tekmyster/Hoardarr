from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.db.models import HardwareSnapshot, PhysicalDisk, StorageBackend, StorageGroup
from hoardarr.operations.service import document_hash


@dataclass(frozen=True)
class FilesystemUsage:
    device_number: int
    total_bytes: int
    used_bytes: int
    free_bytes: int


def inspect_filesystem_usage(path: str) -> FilesystemUsage:
    usage = shutil.disk_usage(path)
    return FilesystemUsage(os.stat(path).st_dev, usage.total, usage.used, usage.free)


def _snapshot_disks(snapshot: HardwareSnapshot) -> dict[str, dict[str, Any]]:
    raw = snapshot.payload_json.get("disks")
    if not isinstance(raw, list):
        return {}
    return {
        str(item["id"]): item
        for item in raw[:4096]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _existing_data(observation: dict[str, Any] | None) -> dict[str, Any]:
    if observation is None:
        return {
            "state": "unknown",
            "detail": "The current hardware snapshot does not contain this registered disk.",
        }
    partitions = observation.get("partitions")
    signatures = observation.get("signatures")
    partition_count = len(partitions) if isinstance(partitions, list) else 0
    signature_items = signatures if isinstance(signatures, list) else []
    signature_names = sorted(
        {
            str(item.get("type"))
            for item in signature_items
            if isinstance(item, dict) and item.get("type")
        }
    )
    if partition_count or signature_names:
        evidence = []
        if partition_count:
            evidence.append(f"{partition_count} partition{'s' if partition_count != 1 else ''}")
        if signature_names:
            evidence.append(f"signatures: {', '.join(signature_names)}")
        return {
            "state": "detected",
            "detail": "; ".join(evidence),
        }
    scan = observation.get("signature_scan")
    if isinstance(scan, dict) and scan.get("status") == "complete":
        return {
            "state": "none_detected",
            "detail": (
                "The complete read-only signature scan found no partition or filesystem metadata."
            ),
        }
    return {
        "state": "unknown",
        "detail": "Existing data has not been ruled out by a complete read-only signature scan.",
    }


def _disk_document(disk: PhysicalDisk, observation: dict[str, Any] | None) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    if observation is None:
        blockers.append("The disk is absent from the latest hardware snapshot.")
    elif observation.get("system_disk") is True or observation.get("system_device") is True:
        blockers.append("Protected system storage cannot be used for expansion or import.")
    if disk.capacity_bytes is None or disk.capacity_bytes <= 0:
        blockers.append("Capacity is not reported.")
    if not disk.kernel_path:
        blockers.append("The disk is not currently present.")
    if disk.health_state == "critical":
        blockers.append("The disk reports a critical health state.")
    elif disk.health_state != "healthy":
        warnings.append(f"Drive health is {disk.health_state.replace('_', ' ')}.")
    existing = _existing_data(observation)
    if existing["state"] == "detected":
        warnings.append(
            "Existing partitions or filesystem signatures require an import-first review."
        )
    elif existing["state"] == "unknown":
        warnings.append("Existing data has not been ruled out.")
    return {
        "id": disk.id,
        "stable_identity": disk.stable_identity,
        "kernel_path": disk.kernel_path,
        "vendor": disk.vendor,
        "model": disk.model,
        "capacity_bytes": disk.capacity_bytes,
        "media_type": disk.media_type or "unknown",
        "health": disk.health_state,
        "existing_data": existing,
        "eligible": not blockers,
        "blockers": blockers,
        "warnings": warnings,
    }


def _candidate(
    *,
    kind: str,
    disk_ids: Iterable[str],
    title: str,
    summary: str,
    group: StorageGroup | None,
    raw_delta: int,
    usable_delta: int | None,
    protection: str,
    expansion: str,
    migration: str,
    recommended: bool,
    mode: str,
    restrictions: list[str] | None = None,
    methodology: str,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "kind": kind,
        "disk_ids": list(disk_ids),
        "storage_group_id": group.id if group else None,
        "storage_group_name": group.name if group else None,
        "title": title,
        "summary": summary,
        "recommended": recommended,
        "setup_mode": mode,
        "capacity": {
            "raw_delta_bytes": raw_delta,
            "estimated_usable_delta_bytes": usable_delta,
            "methodology": methodology,
        },
        "protection_impact": protection,
        "future_expansion": expansion,
        "migration_work": migration,
        "restrictions": restrictions or [],
    }
    document["id"] = document_hash(document)[:24]
    return document


def build_expansion_assessment(
    session: Session,
    *,
    snapshot: HardwareSnapshot,
    storage_inventory: dict[str, Any] | None = None,
    filesystem_probe: Callable[[str], FilesystemUsage] | None = None,
) -> dict[str, Any]:
    """Describe safe expansion choices without claiming that an unapproved change occurred."""

    observations = _snapshot_disks(snapshot)
    filesystem_probe = filesystem_probe or inspect_filesystem_usage
    assigned_ids = {
        value
        for value in session.scalars(
            select(StorageBackend.physical_disk_id).where(
                StorageBackend.physical_disk_id.is_not(None),
                StorageBackend.lifecycle_state != "retired",
            )
        )
        if value
    }
    disks = list(session.scalars(select(PhysicalDisk).order_by(PhysicalDisk.first_seen_at)))
    available = [
        _disk_document(disk, observations.get(disk.stable_identity))
        for disk in disks
        if disk.id not in assigned_ids and disk.lifecycle_state in {"discovered", "reuse_ready"}
    ]
    groups = list(session.scalars(select(StorageGroup).order_by(StorageGroup.name)))
    pools = ((storage_inventory or {}).get("pools") or {}).get("items")
    pool_items = pools if isinstance(pools, list) else []
    pool_types = {
        str(item.get("type", "")).casefold() for item in pool_items if isinstance(item, dict)
    }
    has_mergerfs = any("mergerfs" in value for value in pool_types)
    has_snapraid = any("snapraid" in value for value in pool_types)
    has_zfs = any("zfs" in value for value in pool_types)

    candidates: list[dict[str, Any]] = []
    usable_blank = [
        item
        for item in available
        if item["eligible"] and item["existing_data"]["state"] == "none_detected"
    ]
    for disk in available:
        if not disk["eligible"]:
            continue
        capacity = int(disk["capacity_bytes"] or 0)
        existing = disk["existing_data"]["state"]
        if existing != "none_detected":
            candidates.append(
                _candidate(
                    kind="import_existing",
                    disk_ids=[disk["id"]],
                    title="Review and import existing storage",
                    summary=(
                        "Inspect this disk read-only before deciding whether to copy or reuse its "
                        "files."
                    ),
                    group=None,
                    raw_delta=capacity,
                    usable_delta=None,
                    protection="No protection change is claimed during read-only import.",
                    expansion="Choose a destination only after the file inventory is known.",
                    migration=(
                        "Read-only preview, then an explicit verified intake or drain operation."
                    ),
                    recommended=True,
                    mode="import",
                    restrictions=[disk["existing_data"]["detail"]],
                    methodology=(
                        "Usable capacity is not estimated until the existing filesystem is "
                        "inspected."
                    ),
                )
            )
            continue
        media_groups = [group for group in groups if group.purpose == "media"]
        for group in media_groups:
            if has_mergerfs:
                candidates.append(
                    _candidate(
                        kind="add_mergerfs_member",
                        disk_ids=[disk["id"]],
                        title=f"Add capacity to {group.name}",
                        summary=(
                            "Add another independently readable member to the combined media "
                            "folder."
                        ),
                        group=group,
                        raw_delta=capacity,
                        usable_delta=capacity,
                        protection=(
                            "SnapRAID protection must be resynchronized after the member is added."
                            if has_snapraid
                            else "Combined storage alone does not add drive-failure protection."
                        ),
                        expansion="Different-size members can be added later.",
                        migration=(
                            "Format only after approval, mount the member, update mergerFS, then "
                            "verify placement."
                        ),
                        recommended=True,
                        mode="expand",
                        restrictions=(
                            ["Parity must be at least as large as the largest protected data disk."]
                            if has_snapraid
                            else []
                        ),
                        methodology=(
                            "An independent mergerFS data member contributes its formatted "
                            "capacity; filesystem overhead is not subtracted here."
                        ),
                    )
                )
            if disk["media_type"] in {"ssd", "nvme"}:
                candidates.append(
                    _candidate(
                        kind="add_download_tier",
                        disk_ids=[disk["id"]],
                        title=f"Use as fast downloads for {group.name}",
                        summary=(
                            "Keep torrent/Usenet temporary work on fast storage and import "
                            "finished "
                            "media into the stable library path."
                        ),
                        group=group,
                        raw_delta=capacity,
                        usable_delta=capacity,
                        protection=(
                            "Temporary downloads are not treated as protected media until import "
                            "completes."
                        ),
                        expansion="The media namespace remains unchanged.",
                        migration=(
                            "Create landing paths, configure retention rules, and preview "
                            "ARR/download-client path changes."
                        ),
                        recommended=not has_mergerfs,
                        mode="cache",
                        methodology=(
                            "The estimate is the new tier's formatted capacity before filesystem "
                            "overhead."
                        ),
                    )
                )
        candidates.append(
            _candidate(
                kind="new_storage_group",
                disk_ids=[disk["id"]],
                title="Create a separate storage location",
                summary="Keep this disk independent with its own stable Hoardarr path.",
                group=None,
                raw_delta=capacity,
                usable_delta=capacity,
                protection="A single disk has no drive-failure protection.",
                expansion="It can later become a member of a combined Storage Group.",
                migration=(
                    "Create a filesystem and mount only after the destructive review is approved."
                ),
                recommended=not groups,
                mode="configure",
                methodology=(
                    "Usable capacity is approximated as raw capacity before filesystem overhead."
                ),
            )
        )

    matched = sorted(usable_blank, key=lambda item: int(item["capacity_bytes"] or 0), reverse=True)
    if len(matched) >= 2:
        first, second = matched[:2]
        first_capacity = int(first["capacity_bytes"] or 0)
        second_capacity = int(second["capacity_bytes"] or 0)
        if second_capacity and first_capacity / second_capacity <= 1.05:
            candidates.append(
                _candidate(
                    kind="zfs_mirror_vdev" if has_zfs else "new_zfs_mirror",
                    disk_ids=[first["id"], second["id"]],
                    title="Add a matched two-drive protected pair"
                    if has_zfs
                    else "Create a protected two-drive pool",
                    summary=(
                        "Store one copy on each selected disk so one drive can fail without losing "
                        "the pool."
                    ),
                    group=None,
                    raw_delta=first_capacity + second_capacity,
                    usable_delta=min(first_capacity, second_capacity),
                    protection="Can tolerate one drive failure in this mirror.",
                    expansion="Capacity grows by adding another matched mirror pair.",
                    migration=(
                        "Create a new ZFS mirror vdev after identity revalidation and exact "
                        "approval."
                    ),
                    recommended=has_zfs,
                    mode="advanced",
                    restrictions=["Both disks must remain dedicated to the ZFS vdev."],
                    methodology=(
                        "Mirror usable capacity equals the smallest member capacity before pool "
                        "overhead."
                    ),
                )
            )

    group_documents = []
    for group in groups:
        backends = list(
            session.scalars(
                select(StorageBackend).where(StorageBackend.storage_group_id == group.id)
            )
        )
        capacities = []
        member_usage: list[tuple[str, FilesystemUsage]] = []
        for backend in backends:
            if backend.physical_disk_id:
                disk = session.get(PhysicalDisk, backend.physical_disk_id)
                if disk and disk.capacity_bytes:
                    capacities.append(disk.capacity_bytes)
            if backend.namespace_path and backend.lifecycle_state != "retired":
                try:
                    usage = filesystem_probe(backend.namespace_path)
                except OSError:
                    continue
                if not any(item.device_number == usage.device_number for _, item in member_usage):
                    member_usage.append((backend.id, usage))
        aggregate_usage = None
        with suppress(OSError):
            aggregate_usage = filesystem_probe(group.namespace_path)
        utilizations = [
            item.used_bytes / item.total_bytes * 100
            for _, item in member_usage
            if item.total_bytes > 0
        ]
        spread = max(utilizations) - min(utilizations) if len(utilizations) >= 2 else None
        parity = [
            item
            for item in backends
            if item.role == "parity" and item.lifecycle_state != "retired"
        ]
        data = [
            item
            for item in backends
            if item.role in {"data", "archive"} and item.lifecycle_state != "retired"
        ]
        group_documents.append(
            {
                "id": group.id,
                "name": group.name,
                "namespace_path": group.namespace_path,
                "purpose": group.purpose,
                "backend_count": len(backends),
                "raw_capacity_bytes": sum(capacities) if capacities else None,
                "capacity": {
                    "total_bytes": aggregate_usage.total_bytes if aggregate_usage else None,
                    "used_bytes": aggregate_usage.used_bytes if aggregate_usage else None,
                    "free_bytes": aggregate_usage.free_bytes if aggregate_usage else None,
                    "quality": "available" if aggregate_usage else "not_reported",
                    "source": "statvfs Storage Group namespace" if aggregate_usage else None,
                },
                "distribution": {
                    "reported_members": len(utilizations),
                    "minimum_utilization_percent": min(utilizations) if utilizations else None,
                    "maximum_utilization_percent": max(utilizations) if utilizations else None,
                    "spread_percentage_points": spread,
                    "methodology": (
                        "Maximum minus minimum used percentage across distinct reported member "
                        "filesystems. Uneven use is context, not a failure."
                    ),
                },
                "protection": {
                    "data_backends": len(data),
                    "parity_backends": len(parity),
                    "summary": (
                        f"{len(parity)} parity backend{'s' if len(parity) != 1 else ''} configured"
                        if parity
                        else "No parity backend is configured in this Storage Group."
                    ),
                },
                "preferred_backend_id": next(
                    (item.id for item in backends if item.lifecycle_state == "preferred_write"),
                    None,
                ),
            }
        )

    return {
        "schema_version": 1,
        "hardware_snapshot_id": snapshot.id,
        "hardware_snapshot_sha256": snapshot.sha256,
        "captured_at": snapshot.captured_at.isoformat(),
        "storage_groups": group_documents,
        "available_disks": available,
        "detected_capabilities": {
            "mergerfs": has_mergerfs,
            "snapraid": has_snapraid,
            "zfs": has_zfs,
        },
        "candidates": sorted(
            candidates,
            key=lambda item: (not bool(item["recommended"]), str(item["title"]), str(item["id"])),
        ),
        "methodology": (
            "Plans use the latest persisted hardware discovery and current managed Storage Groups. "
            "No disk is assigned, formatted, mounted, or reserved by this read-only assessment."
        ),
    }
