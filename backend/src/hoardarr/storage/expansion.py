from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.db.models import (
    HardwareSnapshot,
    MetricEntity,
    MetricRollup,
    MetricSample,
    PhysicalDisk,
    StorageBackend,
    StorageEntity,
    StorageGroup,
)
from hoardarr.operations.service import document_hash
from hoardarr.telemetry.analytics import capacity_forecast


@dataclass(frozen=True)
class FilesystemUsage:
    device_number: int
    total_bytes: int
    used_bytes: int
    free_bytes: int


def inspect_filesystem_usage(path: str) -> FilesystemUsage:
    usage = shutil.disk_usage(path)
    return FilesystemUsage(os.stat(path).st_dev, usage.total, usage.used, usage.free)


def _group_capacity_forecast(
    session: Session,
    group: StorageGroup,
    backends: list[StorageBackend],
    total_bytes: int | None,
) -> dict[str, Any]:
    storage_ids = {item.storage_entity_id for item in backends if item.storage_entity_id}
    entities = list(
        session.scalars(
            select(StorageEntity).where(
                (StorageEntity.id.in_(storage_ids))
                | (StorageEntity.mountpoint == group.namespace_path)
            )
        )
    )
    unique = {item.id: item for item in entities}
    if len(unique) != 1:
        return {
            "status": "not_reported",
            "reason": (
                "No logical telemetry entity maps uniquely to this Storage Group."
                if not unique
                else "More than one logical telemetry entity maps to this Storage Group."
            ),
            "metric_entity_id": None,
        }
    storage = next(iter(unique.values()))
    metric_entity = session.scalar(
        select(MetricEntity).where(
            MetricEntity.entity_type == "logical_storage",
            MetricEntity.stable_id == f"logical-storage:{storage.stable_identity}",
        )
    )
    if metric_entity is None:
        return {
            "status": "insufficient_history",
            "reason": "Capacity telemetry has not been persisted for this storage yet.",
            "metric_entity_id": None,
        }
    start = datetime.now(UTC) - timedelta(days=30)
    raw = session.execute(
        select(MetricSample.observed_at, MetricSample.value)
        .where(
            MetricSample.entity_id == metric_entity.id,
            MetricSample.metric_id == "capacity.used",
            MetricSample.observed_at >= start,
            MetricSample.value.is_not(None),
        )
        .order_by(MetricSample.observed_at)
        .limit(5000)
    ).all()
    rolled = session.execute(
        select(MetricRollup.period_start, MetricRollup.last)
        .where(
            MetricRollup.entity_id == metric_entity.id,
            MetricRollup.metric_id == "capacity.used",
            MetricRollup.resolution == "day",
            MetricRollup.period_start >= start,
            MetricRollup.last.is_not(None),
        )
        .order_by(MetricRollup.period_start)
        .limit(365)
    ).all()
    points = [
        (timestamp, float(value)) for timestamp, value in [*rolled, *raw] if value is not None
    ]
    forecast = capacity_forecast(points, total_bytes=total_bytes).document()
    return {
        **forecast,
        "metric_entity_id": metric_entity.id,
        "reason": None,
    }


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
        "device_id": (
            str(observation.get("id"))
            if isinstance(observation, dict) and isinstance(observation.get("id"), str)
            else disk.stable_identity
        ),
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
    target: dict[str, str] | None = None,
    configuration: dict[str, Any] | None = None,
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
        "target": target,
        "configuration": configuration or {},
    }
    document["id"] = document_hash(document)[:24]
    return document


def _best_matched_disks(
    items: list[dict[str, Any]], *, minimum: int, maximum: int | None = None
) -> list[dict[str, Any]] | None:
    """Choose a close-capacity set without letting one outlier suppress a valid plan."""

    ordered = sorted(items, key=lambda item: int(item["capacity_bytes"] or 0), reverse=True)
    best: list[dict[str, Any]] | None = None
    best_score: tuple[int, int, int] | None = None
    for start in range(len(ordered)):
        upper = len(ordered) if maximum is None else min(len(ordered), start + maximum)
        for end in range(start + minimum, upper + 1):
            possible = ordered[start:end]
            capacities = [int(item["capacity_bytes"] or 0) for item in possible]
            smallest = min(capacities)
            largest = max(capacities)
            if smallest <= 0 or largest / smallest > 1.05:
                continue
            score = (len(possible), smallest, sum(capacities))
            if best_score is None or score > best_score:
                best = possible
                best_score = score
    return best


def build_expansion_assessment(
    session: Session,
    *,
    snapshot: HardwareSnapshot,
    storage_inventory: dict[str, Any] | None = None,
    filesystem_probe: Callable[[str], FilesystemUsage] | None = None,
    tool_probe: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Describe safe expansion choices without claiming that an unapproved change occurred."""

    observations = _snapshot_disks(snapshot)
    filesystem_probe = filesystem_probe or inspect_filesystem_usage
    tool_probe = tool_probe or (
        lambda name: shutil.which(name, path="/usr/sbin:/usr/bin:/sbin:/bin") is not None
    )
    tool_availability = {
        "mergerfs": tool_probe("mergerfs"),
        "snapraid": tool_probe("snapraid"),
        "zfs": tool_probe("zpool") and tool_probe("zfs"),
        "linux_md": tool_probe("mdadm"),
    }
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
    unassigned = [
        (disk.lifecycle_state, _disk_document(disk, observations.get(disk.stable_identity)))
        for disk in disks
        if disk.id not in assigned_ids
        and disk.lifecycle_state in {"discovered", "reuse_ready", "reserved"}
    ]
    available = [document for state, document in unassigned if state != "reserved"]
    reserved = [document for state, document in unassigned if state == "reserved"]
    groups = list(session.scalars(select(StorageGroup).order_by(StorageGroup.name)))
    pools = ((storage_inventory or {}).get("pools") or {}).get("items")
    pool_items = pools if isinstance(pools, list) else []
    occupied_mountpoints = sorted(
        {
            *(group.namespace_path for group in groups if group.namespace_path),
            *(
                str(item["mountpoint"])
                for item in pool_items
                if isinstance(item, dict)
                and isinstance(item.get("mountpoint"), str)
                and str(item["mountpoint"]).startswith("/")
            ),
        }
    )
    pool_types = {
        str(item.get("type", "")).casefold() for item in pool_items if isinstance(item, dict)
    }
    has_mergerfs = any("mergerfs" in value for value in pool_types)
    has_snapraid = any("snapraid" in value for value in pool_types)
    has_zfs = any("zfs" in value for value in pool_types)
    has_linux_md = any(value in {"md", "linux md", "linux_md"} for value in pool_types)
    mergerfs_pools = [
        item
        for item in pool_items
        if isinstance(item, dict) and str(item.get("type", "")).casefold() == "mergerfs"
    ] if tool_availability["mergerfs"] else []
    snapraid_pools = [
        item
        for item in pool_items
        if isinstance(item, dict) and str(item.get("type", "")).casefold() == "snapraid"
    ] if tool_availability["snapraid"] else []
    zfs_pools = [
        item
        for item in pool_items
        if isinstance(item, dict) and str(item.get("type", "")).casefold() == "zfs"
    ] if tool_availability["zfs"] else []
    group_mergerfs_targets: dict[str, dict[str, Any]] = {}
    group_snapraid_targets: dict[str, dict[str, Any]] = {}
    group_zfs_targets: dict[str, dict[str, Any]] = {}
    for group in groups:
        backend_paths = {
            item.namespace_path
            for item in session.scalars(
                select(StorageBackend).where(
                    StorageBackend.storage_group_id == group.id,
                    StorageBackend.lifecycle_state != "retired",
                )
            )
            if item.namespace_path
        }
        matches = [
            item
            for item in mergerfs_pools
            if item.get("mountpoint") == group.namespace_path
            or bool(backend_paths.intersection(str(path) for path in item.get("branches", [])))
        ]
        if len(matches) == 1:
            group_mergerfs_targets[group.id] = matches[0]
            mergerfs_branches = {
                str(path) for path in matches[0].get("branches", []) if isinstance(path, str)
            }
            snapraid_matches = []
            for snapraid_pool in snapraid_pools:
                configuration = snapraid_pool.get("configuration")
                if (
                    not isinstance(configuration, dict)
                    or configuration.get("quality") != "available"
                ):
                    continue
                data_paths = {
                    str(item.get("path"))
                    for item in configuration.get("data_disks", [])
                    if isinstance(item, dict) and isinstance(item.get("path"), str)
                }
                if data_paths.intersection(backend_paths | mergerfs_branches):
                    snapraid_matches.append(snapraid_pool)
            if len(snapraid_matches) == 1:
                group_snapraid_targets[group.id] = snapraid_matches[0]
        zfs_matches = [
            item
            for item in zfs_pools
            if item.get("mountpoint") == group.namespace_path
            or item.get("mountpoint") in backend_paths
        ]
        if len(zfs_matches) == 1:
            group_zfs_targets[group.id] = zfs_matches[0]

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
                    disk_ids=[disk["device_id"]],
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
                    configuration={"topology": "import"},
                )
            )
            continue
        media_groups = [group for group in groups if group.purpose == "media"]
        for group in media_groups:
            mergerfs_target = group_mergerfs_targets.get(group.id)
            if mergerfs_target is not None:
                snapraid_target = group_snapraid_targets.get(group.id)
                snapraid_configuration = (
                    snapraid_target.get("configuration")
                    if isinstance(snapraid_target, dict)
                    and isinstance(snapraid_target.get("configuration"), dict)
                    else None
                )
                configuration: dict[str, Any] = {"topology": "mergerfs"}
                if snapraid_configuration is not None:
                    configuration.update(
                        {
                            "snapraid_role": "data",
                            "snapraid_instance_id": snapraid_target["id"],
                            "snapraid_config_sha256": snapraid_configuration["config_sha256"],
                        }
                    )
                candidates.append(
                    _candidate(
                        kind="add_mergerfs_member",
                        disk_ids=[disk["device_id"]],
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
                            if snapraid_target is not None
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
                            if snapraid_target is not None
                            else []
                        ),
                        methodology=(
                            "An independent mergerFS data member contributes its formatted "
                            "capacity; filesystem overhead is not subtracted here."
                        ),
                        target={
                            "provider": "mergerfs",
                            "instance_id": str(mergerfs_target["id"]),
                            "mountpoint": str(mergerfs_target["mountpoint"]),
                        },
                        configuration=configuration,
                    )
                )
                if snapraid_configuration is not None:
                    parity_paths = [
                        str(item.get("path"))
                        for item in snapraid_configuration.get("parity_disks", [])
                        if isinstance(item, dict) and isinstance(item.get("path"), str)
                    ]
                    largest_data_capacity = max(
                        (
                            int(item.capacity_bytes or 0)
                            for item in session.scalars(
                                select(PhysicalDisk)
                                .join(
                                    StorageBackend,
                                    StorageBackend.physical_disk_id == PhysicalDisk.id,
                                )
                                .where(
                                    StorageBackend.storage_group_id == group.id,
                                    StorageBackend.role.in_(("data", "archive")),
                                    StorageBackend.lifecycle_state != "retired",
                                )
                            )
                        ),
                        default=0,
                    )
                    restrictions = [
                        "The parity disk must be at least as large as every protected data disk.",
                        "Parity capacity does not increase the media folder's usable capacity.",
                    ]
                    if capacity < largest_data_capacity:
                        restrictions.insert(
                            0,
                            "This disk is too small to protect the largest current data disk.",
                        )
                    if len(parity_paths) < 6 and capacity >= largest_data_capacity:
                        candidates.append(
                            _candidate(
                                kind="add_snapraid_parity",
                                disk_ids=[disk["device_id"]],
                                title=f"Add another parity disk to {group.name}",
                                summary=(
                                    "Increase parity protection without adding this disk to the "
                                    "combined media folder."
                                ),
                                group=group,
                                raw_delta=capacity,
                                usable_delta=0,
                                protection=(
                                    f"Adds parity level {len(parity_paths) + 1}; a full sync is "
                                    "required before the added protection is current."
                                ),
                                expansion="Media capacity is unchanged; protection capacity grows.",
                                migration=(
                                    "Format and mount the parity disk, update the exact SnapRAID "
                                    "configuration, validate it, then perform a durable parity "
                                    "sync."
                                ),
                                recommended=False,
                                mode="advanced",
                                restrictions=restrictions,
                                methodology=(
                                    "A parity member contributes protection rather than file "
                                    "storage, so usable media capacity changes by zero bytes."
                                ),
                                target={
                                    "provider": "mergerfs",
                                    "instance_id": str(mergerfs_target["id"]),
                                    "mountpoint": str(mergerfs_target["mountpoint"]),
                                },
                                configuration={
                                    "topology": "mergerfs",
                                    "snapraid_role": "parity",
                                    "snapraid_instance_id": snapraid_target["id"],
                                    "snapraid_config_sha256": snapraid_configuration[
                                        "config_sha256"
                                    ],
                                },
                            )
                        )
            if disk["media_type"] in {"ssd", "nvme"}:
                candidates.append(
                    _candidate(
                        kind="add_download_tier",
                        disk_ids=[disk["device_id"]],
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
                        configuration={"topology": "download-cache"},
                    )
                )
        candidates.append(
            _candidate(
                kind="new_storage_group",
                disk_ids=[disk["device_id"]],
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
                configuration={"topology": "individual"},
            )
        )

    # Existing ZFS pools expand by appending another complete top-level vdev
    # with the exact, already-observed redundancy geometry. Never suggest a
    # force override or a lone disk merely because it has similar capacity.
    for group in groups:
        zfs_target = group_zfs_targets.get(group.id)
        if zfs_target is None:
            continue
        zfs_configuration = zfs_target.get("configuration")
        if not isinstance(zfs_configuration, dict):
            continue
        vdev_type = zfs_configuration.get("vdev_type")
        vdev_width = zfs_configuration.get("vdev_width")
        config_sha256 = zfs_configuration.get("config_sha256")
        pool_guid = zfs_target.get("pool_guid")
        if (
            zfs_configuration.get("quality") != "available"
            or vdev_type not in {"mirror", "raidz1", "raidz2", "raidz3"}
            or not isinstance(vdev_width, int)
            or not 2 <= vdev_width <= 64
            or not isinstance(config_sha256, str)
            or len(config_sha256) != 64
            or not isinstance(pool_guid, str)
            or not pool_guid.isdigit()
        ):
            continue
        capacity_ordered = sorted(
            usable_blank,
            key=lambda item: int(item["capacity_bytes"] or 0),
            reverse=True,
        )
        selected: list[dict[str, Any]] | None = None
        for offset in range(0, len(capacity_ordered) - vdev_width + 1):
            possible = capacity_ordered[offset : offset + vdev_width]
            possible_capacities = [int(item["capacity_bytes"] or 0) for item in possible]
            smallest_possible = min(possible_capacities)
            largest_possible = max(possible_capacities)
            if smallest_possible > 0 and largest_possible / smallest_possible <= 1.05:
                selected = possible
                break
        if selected is None:
            continue
        capacities = [int(item["capacity_bytes"] or 0) for item in selected]
        smallest = min(capacities)
        parity_columns = {"mirror": vdev_width - 1, "raidz1": 1, "raidz2": 2, "raidz3": 3}[
            str(vdev_type)
        ]
        pool_name = str(zfs_target.get("name") or "")
        candidates.append(
            _candidate(
                kind="add_zfs_vdev",
                disk_ids=[item["device_id"] for item in selected],
                title=f"Add another protected vdev to {group.name}",
                summary=(
                    f"Add one complete {str(vdev_type).upper()} group to the existing ZFS pool "
                    "without recreating its datasets or mount path."
                ),
                group=group,
                raw_delta=sum(capacities),
                usable_delta=smallest * (vdev_width - parity_columns),
                protection=(
                    f"Matches the existing {str(vdev_type).upper()} data-vdev geometry. "
                    "Protection remains defined independently within each top-level vdev."
                ),
                expansion=(
                    "Future growth requires another complete matching vdev. Existing data is not "
                    "automatically rebalanced onto the new vdev."
                ),
                migration=(
                    "Validate the pool GUID and exact data-vdev configuration, run ZFS's no-change "
                    "dry run, then add the reviewed vdev without formatting or recreating the pool."
                ),
                recommended=False,
                mode="advanced",
                restrictions=[
                    "Adding a top-level vdev changes the pool permanently and requires exact "
                    "destructive approval.",
                    "Hoardarr will not use zpool -f to override a geometry or device-safety "
                    "warning.",
                    "ZFS directs new writes to the added vdev; it does not rebalance existing "
                    "blocks.",
                ],
                methodology=(
                    "Estimated usable capacity is the smallest new member multiplied by the data "
                    "columns in the reviewed matching vdev, before ZFS overhead."
                ),
                target={
                    "provider": "zfs",
                    "instance_id": f"zfs:{pool_name}",
                    "mountpoint": str(zfs_target.get("mountpoint") or group.namespace_path),
                },
                configuration={
                    "topology": "zfs",
                    "vdev_type": vdev_type,
                    "vdev_width": vdev_width,
                    "zfs_pool_guid": pool_guid,
                    "zfs_config_sha256": config_sha256,
                    "zfs_vdev_count": int(zfs_configuration.get("vdev_count") or 0),
                },
            )
        )

    matched = (
        sorted(usable_blank, key=lambda item: int(item["capacity_bytes"] or 0), reverse=True)
        if tool_availability["zfs"]
        else []
    )
    mirror_members = _best_matched_disks(matched, minimum=2, maximum=2)
    if mirror_members is not None:
        first, second = mirror_members
        first_capacity = int(first["capacity_bytes"] or 0)
        second_capacity = int(second["capacity_bytes"] or 0)
        candidates.append(
            _candidate(
                kind="new_zfs_mirror",
                disk_ids=[first["device_id"], second["device_id"]],
                title="Create a protected two-drive pool",
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
                    "Create a new ZFS mirror vdev after identity revalidation and exact approval."
                ),
                recommended=False,
                mode="advanced",
                restrictions=["Both disks must remain dedicated to the ZFS vdev."],
                methodology=(
                    "Mirror usable capacity equals the smallest member capacity before pool "
                    "overhead."
                ),
                configuration={"topology": "zfs", "vdev_type": "mirror", "vdev_width": 2},
            )
        )

    # Offer complete, executable single-vdev RAIDZ geometries from the best
    # close-capacity subset. Outliers remain available for other plans instead
    # of suppressing a valid matched group or being silently stranded in it.
    if len(matched) >= 3:
        geometry = (
            ("raidz1", 1, 3),
            ("raidz2", 2, 4),
            ("raidz3", 3, 5),
        )
        for vdev_type, parity_count, minimum in geometry:
            selected = _best_matched_disks(matched, minimum=minimum)
            if selected is not None:
                capacities = [int(item["capacity_bytes"] or 0) for item in selected]
                smallest = min(capacities)
                width = len(selected)
                raw = sum(int(item["capacity_bytes"] or 0) for item in selected)
                usable = smallest * (width - parity_count)
                candidates.append(
                    _candidate(
                        kind=f"new_zfs_{vdev_type}",
                        disk_ids=[item["device_id"] for item in selected],
                        title=f"Create a {width}-drive protected ZFS pool",
                        summary=(
                            f"Use {vdev_type.upper()} so the pool tolerates {parity_count} "
                            f"drive failure{'s' if parity_count != 1 else ''}."
                        ),
                        group=None,
                        raw_delta=raw,
                        usable_delta=usable,
                        protection=(
                            f"Can tolerate {parity_count} drive failure"
                            f"{'s' if parity_count != 1 else ''} in this vdev."
                        ),
                        expansion=(
                            "Capacity grows by adding another complete vdev; individual disks "
                            "cannot be appended to this RAIDZ vdev."
                        ),
                        migration=(
                            "Create a new ZFS pool after immutable identity review and explicit "
                            "destructive approval."
                        ),
                        recommended=(
                            (width == 3 and vdev_type == "raidz1")
                            or (width >= 4 and vdev_type == "raidz2")
                        ),
                        mode="advanced",
                        restrictions=[
                            "All selected disks become dedicated members of one ZFS vdev.",
                            "Changing RAIDZ width later requires adding another vdev or "
                            "migrating data.",
                        ],
                        methodology=(
                            "Estimated usable capacity is the smallest member capacity multiplied "
                            "by data columns (drive count minus parity columns), before ZFS "
                            "overhead."
                        ),
                        configuration={
                            "topology": "zfs",
                            "vdev_type": vdev_type,
                            "vdev_width": width,
                            "occupied_mountpoints": occupied_mountpoints,
                        },
                    )
                )

    # Linux MD creation is already supported by the immutable storage planner
    # and executor. Offer only complete, close-capacity geometries here; online
    # reshape of an existing array is deliberately not inferred from discovery.
    # That operation has level-specific restrictions and remains a separate
    # advanced workflow rather than an unsafe generic "add disk" suggestion.
    md_matched = (
        sorted(usable_blank, key=lambda item: int(item["capacity_bytes"] or 0), reverse=True)
        if tool_availability["linux_md"]
        else []
    )
    for level, minimum, maximum, data_columns in (
        ("raid1", 2, 2, lambda width: 1),
        ("raid5", 3, None, lambda width: width - 1),
        ("raid6", 4, None, lambda width: width - 2),
        ("raid10", 4, None, lambda width: width // 2),
    ):
        selected = _best_matched_disks(
            md_matched,
            minimum=minimum,
            maximum=maximum if maximum is not None else 64,
        )
        if selected is None:
            continue
        if level == "raid10" and len(selected) % 2:
            selected = selected[:-1]
        if len(selected) < minimum:
            continue
        capacities = [int(item["capacity_bytes"] or 0) for item in selected]
        width = len(selected)
        smallest = min(capacities)
        candidates.append(
            _candidate(
                kind=f"new_linux_md_{level}",
                disk_ids=[item["device_id"] for item in selected],
                title=f"Create a {width}-drive Linux {level.upper()} array",
                summary=(
                    "Create one Linux software RAID device from matched blank drives, then "
                    "place the filesystem above the array."
                ),
                group=None,
                raw_delta=sum(capacities),
                usable_delta=smallest * data_columns(width),
                protection=(
                    "Can tolerate one member failure."
                    if level in {"raid1", "raid5", "raid10"}
                    else "Can tolerate two member failures."
                ),
                expansion=(
                    "Future changes are constrained by mdadm reshape rules for this exact level; "
                    "Hoardarr does not promise a generic one-disk expansion."
                ),
                migration=(
                    "Create a new MD array and filesystem after stable-identity revalidation and "
                    "exact destructive approval."
                ),
                recommended=False,
                mode="advanced",
                restrictions=[
                    "All selected disks become dedicated members of the array.",
                    "Existing MD arrays are not reshaped by this candidate.",
                ],
                methodology=(
                    "Estimated usable capacity is the smallest member multiplied by the data "
                    f"columns defined by {level.upper()}, before MD and filesystem overhead."
                ),
                configuration={
                    "topology": "raid",
                    "md_level": level,
                    "member_count": width,
                    "occupied_mountpoints": occupied_mountpoints,
                },
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
            item for item in backends if item.role == "parity" and item.lifecycle_state != "retired"
        ]
        data = [
            item
            for item in backends
            if item.role in {"data", "archive"} and item.lifecycle_state != "retired"
        ]
        forecast = _group_capacity_forecast(
            session,
            group,
            backends,
            aggregate_usage.total_bytes if aggregate_usage else None,
        )
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
                "growth_forecast": forecast,
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
        "reserved_disks": reserved,
        "detected_capabilities": {
            "mergerfs": has_mergerfs,
            "snapraid": has_snapraid,
            "zfs": has_zfs,
            "linux_md": has_linux_md,
        },
        "tool_availability": tool_availability,
        "candidates": sorted(
            candidates,
            key=lambda item: (not bool(item["recommended"]), str(item["title"]), str(item["id"])),
        ),
        "methodology": (
            "Plans use the latest persisted hardware discovery and current managed Storage Groups. "
            "No disk is assigned, formatted, mounted, or reserved by this read-only assessment."
        ),
    }
