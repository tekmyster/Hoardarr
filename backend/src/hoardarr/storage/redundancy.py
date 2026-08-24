from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.db.models import (
    ConnectivityService,
    MetricEntity,
    PhysicalDisk,
    StorageController,
    StorageEntity,
    StoragePath,
    StorageRedundancyEvent,
    utc_now,
)
from hoardarr.operations.service import document_hash


class RedundancyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


AUTHORITATIVE_IDENTIFIERS = ("wwn", "nguid", "eui64")
SAFE_POLICY_VALUES = {"recommended", "failover", "multibus", "group_by_prio"}
TRANSITION_MODES = {
    "online_supported",
    "brief_maintenance_required",
    "automatic_conversion_unsupported",
}
DEFAULT_REDUNDANCY_SETTINGS: dict[str, Any] = {
    "mode": "recommended",
    "path_grouping_policy": "group_by_prio",
    "path_selector": "service-time 0",
    "failback": "followover",
    "no_path_retry": "fail",
    "polling_interval_seconds": 5,
    "minimum_healthy_paths": 2,
    "alert_on_reduced": True,
    "alert_on_failover": True,
    "alert_on_path_flapping": True,
    "alert_on_total_loss": True,
}


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().casefold()
    return cleaned or None


def _reported(value: object, maximum: int = 256) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = "".join(character for character in value.strip() if character.isprintable())
    return cleaned[:maximum] or None


def logical_storage_identity(device: Mapping[str, Any]) -> str:
    identity = device.get("identity") if isinstance(device.get("identity"), Mapping) else {}
    for field in AUTHORITATIVE_IDENTIFIERS:
        value = _text(identity.get(field) if isinstance(identity, Mapping) else None)
        value = value or _text(device.get(field))
        if value:
            canonical = re.sub(r"[^a-z0-9._:-]", "", value.removeprefix("0x"))
            if canonical:
                return f"{field}:{canonical}"
    raise RedundancyError(
        "logical_identity_not_reported",
        "Hoardarr cannot safely confirm this is another path to the existing storage "
        "because no authoritative WWID, NAA, NGUID, or EUI-64 was reported.",
    )


def _connection(device: Mapping[str, Any]) -> Mapping[str, Any]:
    value = device.get("connection")
    return value if isinstance(value, Mapping) else device


def stable_path_identity(device: Mapping[str, Any]) -> str:
    connection = _connection(device)
    controller = _text(connection.get("controller_address"))
    target = next(
        (
            _text(connection.get(field))
            for field in ("target_port_wwn", "target_port_sas_address", "portal", "host_address")
            if _text(connection.get(field))
        ),
        None,
    )
    protocol = _text(connection.get("protocol")) or _text(connection.get("transport"))
    if not controller or not protocol:
        raise RedundancyError(
            "path_identity_not_reported",
            "The controller and protocol identity for this path were not reported, so "
            "Hoardarr cannot persist it as a redundant path.",
        )
    # Kernel paths are deliberately excluded. A target-port identity is preferred;
    # the controller identity remains the bounded fallback for direct SAS paths.
    return f"{protocol}:{controller}:{target or 'controller-path'}"


def _capacity(device: Mapping[str, Any]) -> int:
    value = device.get("capacity_bytes")
    if not isinstance(value, int) or value <= 0:
        raise RedundancyError("capacity_not_reported", "The path capacity was not reported.")
    return value


def _sectors(device: Mapping[str, Any]) -> tuple[int, int]:
    value = device.get("sector_sizes")
    sectors = value if isinstance(value, Mapping) else {}
    logical = sectors.get("logical_bytes") or device.get("logical_sector_bytes")
    physical = sectors.get("physical_bytes") or device.get("physical_sector_bytes")
    if (
        not isinstance(logical, int)
        or not isinstance(physical, int)
        or logical <= 0
        or physical <= 0
    ):
        raise RedundancyError(
            "sector_geometry_not_reported", "The path sector geometry was not reported."
        )
    return logical, physical


def _controller_identity(device: Mapping[str, Any]) -> str:
    value = _text(_connection(device).get("controller_address"))
    if not value:
        raise RedundancyError(
            "controller_identity_not_reported",
            "The storage controller identity was not reported.",
        )
    return value


def normalized_redundancy_settings(value: Mapping[str, Any] | None) -> dict[str, Any]:
    settings = {**DEFAULT_REDUNDANCY_SETTINGS, **dict(value or {})}
    allowed = {
        "mode": {"recommended", "custom"},
        "path_grouping_policy": {"failover", "group_by_prio", "multibus"},
        "path_selector": {"service-time 0", "round-robin 0", "queue-length 0"},
        "failback": {"immediate", "manual", "followover"},
        "no_path_retry": {"fail", "queue", "queue_30"},
    }
    for field, choices in allowed.items():
        if settings.get(field) not in choices:
            raise RedundancyError("redundancy_settings_invalid", f"{field} is not supported.")
    interval = settings.get("polling_interval_seconds")
    minimum = settings.get("minimum_healthy_paths")
    if not isinstance(interval, int) or not 5 <= interval <= 60:
        raise RedundancyError(
            "redundancy_settings_invalid", "Path checks must be between 5 and 60 seconds."
        )
    if not isinstance(minimum, int) or not 1 <= minimum <= 8:
        raise RedundancyError(
            "redundancy_settings_invalid", "The minimum healthy path count must be 1 through 8."
        )
    for field in (
        "alert_on_reduced",
        "alert_on_failover",
        "alert_on_path_flapping",
        "alert_on_total_loss",
    ):
        if not isinstance(settings.get(field), bool):
            raise RedundancyError("redundancy_settings_invalid", f"{field} must be true or false.")
    return settings


def transition_capability(
    entity: StorageEntity, *, action: str, resulting_path_count: int
) -> dict[str, str]:
    """Describe whether this exact access-layer change needs a maintenance window."""

    if action == "replace" or (
        action == "add" and entity.presentation_device.startswith("/dev/mapper/")
    ):
        return {
            "mode": "online_supported",
            "message": "The verified path can be added while this storage remains online.",
        }
    if action == "remove" and resulting_path_count > 1:
        return {
            "mode": "online_supported",
            "message": "The selected path can be retired while other healthy paths remain online.",
        }
    if not entity.filesystem_uuid or entity.storage_kind not in {
        "filesystem",
        "individual",
        "import",
        "cache",
    }:
        return {
            "mode": "automatic_conversion_unsupported",
            "message": "Hoardarr cannot automatically transition this storage stack to multipath.",
        }
    return {
        "mode": "brief_maintenance_required",
        "message": (
            "Adding redundancy requires a brief storage interruption. The filesystem, data, "
            "shares, and paths will not be recreated."
        ),
    }


def _managed_access_services(session: Session, mountpoint: str) -> list[dict[str, str]]:
    root = PurePosixPath(mountpoint)
    result: list[dict[str, str]] = []
    for service in session.scalars(
        select(ConnectivityService).where(
            ConnectivityService.status == "active",
            ConnectivityService.protocol.in_(("smb", "nfs")),
        )
    ):
        path = service.config_json.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            continue
        candidate = PurePosixPath(path)
        if candidate != root and root not in candidate.parents:
            continue
        result.append(
            {
                "id": service.id,
                "protocol": service.protocol,
                "name": service.name,
                "path": path,
            }
        )
    return result


def _record_event(
    session: Session,
    entity: StorageEntity,
    event_type: str,
    *,
    path: StoragePath | None = None,
    previous_state: str | None = None,
    resulting_state: str | None = None,
    operation_id: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> StorageRedundancyEvent:
    event = StorageRedundancyEvent(
        storage_entity_id=entity.id,
        event_type=event_type,
        path_id=path.id if path else None,
        controller_id=path.controller_id if path else None,
        operation_id=operation_id,
        previous_state=previous_state,
        resulting_state=resulting_state or entity.topology_state,
        details_json=dict(details or {}),
        occurred_at=utc_now(),
    )
    session.add(event)
    return event


def _upsert_controller(session: Session, device: Mapping[str, Any]) -> StorageController:
    identity = _controller_identity(device)
    controller = session.scalar(
        select(StorageController).where(StorageController.stable_identity == identity)
    )
    connection = _connection(device)
    if controller is None:
        controller = StorageController(
            stable_identity=identity,
            provider=_text(connection.get("protocol"))
            or _text(connection.get("transport"))
            or "scsi",
            model=_reported(connection.get("controller_model")),
            state_json={},
        )
        session.add(controller)
        session.flush()
    controller.model = _reported(connection.get("controller_model")) or controller.model
    controller.state_json = {
        **controller.state_json,
        **{
            key: value
            for key, value in {
                "vendor": _reported(connection.get("controller_vendor")),
                "firmware": _reported(connection.get("controller_firmware")),
                "port": _reported(connection.get("controller_port")),
                "initiator": _reported(
                    connection.get("initiator") or connection.get("initiator_wwn")
                ),
            }.items()
            if value is not None
        },
    }
    controller.last_seen_at = utc_now()
    return controller


def _upsert_path(session: Session, entity: StorageEntity, device: Mapping[str, Any]) -> StoragePath:
    path_identity = stable_path_identity(device)
    path = session.scalar(
        select(StoragePath).where(
            StoragePath.storage_entity_id == entity.id,
            StoragePath.stable_path_identity == path_identity,
        )
    )
    controller = _upsert_controller(session, device)
    connection = _connection(device)
    if path is None:
        path = StoragePath(
            storage_entity_id=entity.id,
            controller_id=controller.id,
            stable_path_identity=path_identity,
            kernel_path=str(device.get("kernel_path") or device.get("path") or "Not reported"),
            logical_storage_identity=entity.stable_identity,
            protocol=_text(connection.get("protocol"))
            or _text(connection.get("transport"))
            or "scsi",
            state="active",
            optimized=None,
            active=True,
            metadata_json={},
        )
        session.add(path)
    else:
        path.controller_id = controller.id
        path.kernel_path = str(device.get("kernel_path") or device.get("path") or path.kernel_path)
        path.active = True
        path.state = "active"
        path.last_seen_at = utc_now()
    path.metadata_json = {
        **path.metadata_json,
        **{
            key: value
            for key, value in {
                "port": _reported(connection.get("port")),
                "target": _reported(
                    connection.get("target_port_wwn")
                    or connection.get("target_port_sas_address")
                    or connection.get("portal")
                ),
                "initiator": _reported(
                    connection.get("initiator") or connection.get("initiator_wwn")
                ),
                "hctl": _reported(connection.get("hctl")),
                "negotiated_speed": _reported(
                    connection.get("negotiated_speed") or connection.get("negotiated_link_rate")
                ),
                "capable_speed": _reported(
                    connection.get("capable_speed") or connection.get("maximum_link_rate")
                ),
                "path_group": _reported(connection.get("path_group")),
                "priority": _reported(connection.get("priority")),
                "checker": _reported(connection.get("checker")),
                "alua_state": _reported(connection.get("alua_state")),
            }.items()
            if value is not None
        },
    }
    session.flush()
    return path


def register_single_path_storage(
    session: Session,
    *,
    name: str,
    device: Mapping[str, Any],
    mountpoint: str,
    presentation_device: str,
    filesystem_uuid: str | None,
    storage_kind: str = "filesystem",
) -> StorageEntity:
    stable_identity = logical_storage_identity(device)
    logical_sector, physical_sector = _sectors(device)
    entity = session.scalar(
        select(StorageEntity).where(StorageEntity.stable_identity == stable_identity)
    )
    if entity is None:
        entity = StorageEntity(
            name=name,
            stable_identity=stable_identity,
            storage_kind=storage_kind,
            filesystem_uuid=filesystem_uuid,
            mountpoint=mountpoint,
            presentation_device=presentation_device,
            capacity_bytes=_capacity(device),
            logical_sector_bytes=logical_sector,
            physical_sector_bytes=physical_sector,
            topology_state="single_path",
            provider=_text(_connection(device).get("protocol")) or "scsi",
            config_json={
                "telemetry_stable_id": f"logical-storage:{stable_identity}",
                "preferred_path_identity": stable_path_identity(device),
            },
        )
        session.add(entity)
        session.flush()
    else:
        # Re-discovery may update the current kernel path, but never the logical ID,
        # filesystem, mountpoint, or telemetry identity.
        if entity.capacity_bytes != _capacity(device) or (
            entity.logical_sector_bytes,
            entity.physical_sector_bytes,
        ) != (logical_sector, physical_sector):
            raise RedundancyError(
                "logical_storage_geometry_changed", "The logical storage geometry changed."
            )
    _upsert_path(session, entity, device)
    metric = session.scalar(
        select(MetricEntity).where(
            MetricEntity.entity_type == "logical_storage",
            MetricEntity.stable_id == f"logical-storage:{stable_identity}",
        )
    )
    if metric is None:
        session.add(
            MetricEntity(
                entity_type="logical_storage",
                stable_id=f"logical-storage:{stable_identity}",
                display_name=name,
                labels_json={"storage_entity_id": entity.id},
                topology_json={"path_count": 1},
            )
        )
    return entity


def register_completed_storage(
    session: Session,
    plan_document: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    hardware_snapshot: Mapping[str, Any] | None = None,
) -> StorageEntity | None:
    storage = plan_document.get("storage")
    if not isinstance(storage, Mapping):
        return None
    topology = str(storage.get("topology") or "")
    if topology == "mergerfs":
        mergerfs = storage.get("mergerfs")
        selected = storage.get("selected_devices")
        if not isinstance(mergerfs, Mapping) or not isinstance(selected, list) or not selected:
            return None
        pool_mountpoint = mergerfs.get("mountpoint")
        presentation_mountpoint = result.get("mountpoint")
        if not isinstance(pool_mountpoint, str) or not pool_mountpoint.startswith("/"):
            return None
        if not isinstance(presentation_mountpoint, str) or not presentation_mountpoint.startswith(
            "/"
        ):
            presentation_mountpoint = pool_mountpoint
        stable_identity = f"mergerfs:{hashlib.sha256(pool_mountpoint.encode()).hexdigest()[:16]}"
        name = str(mergerfs.get("name") or "mergerFS storage")[:128]
        capacity_bytes = 0
        try:
            capacity_bytes = shutil.disk_usage(presentation_mountpoint).total
        except OSError:
            for item in selected:
                if isinstance(item, Mapping) and isinstance(item.get("capacity_bytes"), int):
                    capacity_bytes += max(0, int(item["capacity_bytes"]))
        if capacity_bytes <= 0:
            return None
        entity = session.scalar(
            select(StorageEntity).where(StorageEntity.stable_identity == stable_identity)
        )
        member_ids = [
            str(item.get("id"))
            for item in selected
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        ]
        configuration = {
            "pool_mountpoint": pool_mountpoint,
            "member_stable_identities": member_ids,
            "create_policy": mergerfs.get("create_policy"),
            "search_policy": mergerfs.get("search_policy"),
            "redundancy_capable": False,
        }
        if entity is None:
            entity = StorageEntity(
                name=name,
                stable_identity=stable_identity,
                storage_kind="mergerfs",
                filesystem_uuid=None,
                mountpoint=presentation_mountpoint,
                presentation_device=pool_mountpoint,
                capacity_bytes=capacity_bytes,
                logical_sector_bytes=None,
                physical_sector_bytes=None,
                topology_state="not_applicable",
                provider="mergerfs",
                config_json=configuration,
            )
            session.add(entity)
            session.flush()
        else:
            entity.name = name
            entity.mountpoint = presentation_mountpoint
            entity.presentation_device = pool_mountpoint
            entity.capacity_bytes = capacity_bytes
            entity.config_json = {**entity.config_json, **configuration}
        for stable_member_id in member_ids:
            disk = session.scalar(
                select(PhysicalDisk).where(PhysicalDisk.stable_identity == stable_member_id)
            )
            if disk is None:
                continue
            disk.lifecycle_state = "managed_member"
            disk.metadata_json = {
                **disk.metadata_json,
                "managed_storage_entity_id": entity.id,
                "managed_storage_kind": "mergerfs",
            }
        metric = session.scalar(
            select(MetricEntity).where(
                MetricEntity.entity_type == "logical_storage",
                MetricEntity.stable_id == f"logical-storage:{stable_identity}",
            )
        )
        if metric is None:
            session.add(
                MetricEntity(
                    entity_type="logical_storage",
                    stable_id=f"logical-storage:{stable_identity}",
                    display_name=name,
                    labels_json={"storage_entity_id": entity.id, "provider": "mergerfs"},
                    topology_json={
                        "member_count": len(member_ids),
                        "topology_state": "not_applicable",
                    },
                )
            )
        else:
            metric.display_name = name
            metric.labels_json = {
                **metric.labels_json,
                "storage_entity_id": entity.id,
                "provider": "mergerfs",
            }
            metric.topology_json = {
                **metric.topology_json,
                "member_count": len(member_ids),
                "topology_state": "not_applicable",
            }
        return entity
    if topology not in {
        "individual",
        "import",
        "cache",
        "block",
    }:
        return None
    selected = storage.get("selected_devices")
    if not isinstance(selected, list) or len(selected) != 1 or not isinstance(selected[0], Mapping):
        return None
    mountpoint = result.get("mountpoint")
    if not isinstance(mountpoint, str) or not mountpoint.startswith("/"):
        return None
    device = selected[0]
    device_id = str(device.get("id") or "")
    filesystem_uuids = result.get("filesystem_uuids")
    filesystem_uuid = (
        filesystem_uuids.get(device_id)
        if isinstance(filesystem_uuids, Mapping)
        and isinstance(filesystem_uuids.get(device_id), str)
        else None
    )
    member_mountpoints = result.get("member_mountpoints")
    device_mountpoint = (
        member_mountpoints.get(device_id)
        if isinstance(member_mountpoints, Mapping)
        and isinstance(member_mountpoints.get(device_id), str)
        else mountpoint
    )
    try:
        presentation_device = str(device.get("kernel_path") or device.get("path") or "Not reported")
        if (
            storage.get("topology") == "import"
            and presentation_device.startswith("/dev/mapper/")
            and hardware_snapshot is not None
        ):
            identity = logical_storage_identity(device)
            physical_paths = []
            for observed in matching_devices(hardware_snapshot, identity):
                try:
                    stable_path_identity(observed)
                except RedundancyError:
                    continue
                if not str(observed.get("kernel_path") or "").startswith("/dev/mapper/"):
                    physical_paths.append(observed)
            if len(physical_paths) >= 2:
                entity = register_single_path_storage(
                    session,
                    name=str(storage.get("purpose") or "storage"),
                    device=physical_paths[0],
                    mountpoint=mountpoint,
                    presentation_device=presentation_device,
                    filesystem_uuid=filesystem_uuid,
                    storage_kind="import",
                )
                for observed in physical_paths[1:]:
                    if logical_storage_identity(observed) != entity.stable_identity:
                        raise RedundancyError(
                            "logical_identity_changed",
                            "A discovered controller path identifies different storage.",
                        )
                    if _capacity(observed) != entity.capacity_bytes or _sectors(observed) != (
                        entity.logical_sector_bytes,
                        entity.physical_sector_bytes,
                    ):
                        raise RedundancyError(
                            "logical_storage_geometry_changed",
                            "A discovered controller path reports different storage geometry.",
                        )
                    _upsert_path(session, entity, observed)
                entity.presentation_device = presentation_device
                entity.topology_state = "fully_redundant"
                entity.config_json = {
                    **entity.config_json,
                    "device_mountpoint": device_mountpoint,
                }
                metric = session.scalar(
                    select(MetricEntity).where(
                        MetricEntity.entity_type == "logical_storage",
                        MetricEntity.stable_id == f"logical-storage:{entity.stable_identity}",
                    )
                )
                if metric is not None:
                    metric.topology_json = {
                        **metric.topology_json,
                        "path_count": len(physical_paths),
                        "topology_state": "fully_redundant",
                    }
                return entity
        entity = register_single_path_storage(
            session,
            name=str(storage.get("purpose") or "storage"),
            device=device,
            mountpoint=mountpoint,
            presentation_device=presentation_device,
            filesystem_uuid=filesystem_uuid,
            storage_kind=str(storage.get("topology")),
        )
        entity.config_json = {**entity.config_json, "device_mountpoint": device_mountpoint}
        return entity
    except RedundancyError:
        # Locally attached disks without an authoritative WWID remain usable;
        # they simply cannot advertise controller redundancy.
        return None


def matching_devices(snapshot: Mapping[str, Any], stable_identity: str) -> list[Mapping[str, Any]]:
    disks = snapshot.get("disks")
    if not isinstance(disks, list):
        return []
    matches: list[Mapping[str, Any]] = []
    for disk in disks:
        if not isinstance(disk, Mapping):
            continue
        try:
            if logical_storage_identity(disk) == stable_identity:
                matches.append(disk)
        except RedundancyError:
            continue
    return matches


def build_redundancy_plan(
    session: Session,
    *,
    storage_entity_id: str,
    hardware_snapshot_sha256: str,
    hardware_snapshot: Mapping[str, Any],
    action: str,
    candidate_path_identity: str | None = None,
    remove_path_identity: str | None = None,
    policy: str = "recommended",
    settings_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if action not in {"add", "remove", "replace", "configure"}:
        raise RedundancyError(
            "action_invalid", "The redundancy action must be add, remove, replace, or configure."
        )
    if policy not in SAFE_POLICY_VALUES:
        raise RedundancyError("policy_invalid", "The selected multipath policy is not supported.")
    entity = session.get(StorageEntity, storage_entity_id)
    if entity is None:
        raise RedundancyError("storage_not_found", "The logical storage object was not found.")
    observed = matching_devices(hardware_snapshot, entity.stable_identity)
    existing = list(
        session.scalars(select(StoragePath).where(StoragePath.storage_entity_id == entity.id))
    )
    observed_groups: dict[str, list[Mapping[str, Any]]] = {}
    for item in observed:
        identity = stable_path_identity(item)
        observed_groups.setdefault(identity, []).append(item)
    ambiguous = [identity for identity, items in observed_groups.items() if len(items) != 1]
    if ambiguous:
        raise RedundancyError(
            "path_identity_ambiguous",
            "More than one live path reports the same controller identity. "
            "Hoardarr cannot safely choose a path.",
        )
    observed_by_path = {identity: items[0] for identity, items in observed_groups.items()}
    existing_ids = {item.stable_path_identity for item in existing}
    if action == "configure":
        if len(existing) < 2 or not entity.presentation_device.startswith("/dev/mapper/"):
            raise RedundancyError(
                "redundancy_not_configured",
                "Controller settings can be applied after redundant storage access is active.",
            )
        before = [item.stable_path_identity for item in existing]
        after = list(before)
        selected_path = {
            "stable_path_identity": existing[0].stable_path_identity,
            "kernel_path": existing[0].kernel_path,
            "present": True,
        }
    elif action in {"add", "replace"}:
        candidates = [key for key in observed_by_path if key not in existing_ids]
        chosen = candidate_path_identity or (candidates[0] if len(candidates) == 1 else None)
        if not chosen or chosen not in candidates:
            raise RedundancyError(
                "redundant_path_not_unambiguous",
                "Hoardarr cannot safely confirm one new path to the existing storage.",
            )
        device = observed_by_path[chosen]
        logical_sector, physical_sector = _sectors(device)
        if _capacity(device) != entity.capacity_bytes:
            raise RedundancyError("capacity_mismatch", "The new path reports a different capacity.")
        if (logical_sector, physical_sector) != (
            entity.logical_sector_bytes,
            entity.physical_sector_bytes,
        ):
            raise RedundancyError(
                "sector_geometry_mismatch", "The new path reports different sector geometry."
            )
        before = [item.stable_path_identity for item in existing]
        if action == "replace":
            if len(existing) < 2:
                raise RedundancyError(
                    "replacement_requires_redundancy",
                    "Add redundancy before replacing the only controller path.",
                )
            if not remove_path_identity or remove_path_identity not in existing_ids:
                raise RedundancyError(
                    "replacement_path_not_found",
                    "Select the existing controller path that will be replaced.",
                )
            after = [item for item in before if item != remove_path_identity] + [chosen]
            removed_path = next(
                item for item in existing if item.stable_path_identity == remove_path_identity
            )
            removed_observed = observed_by_path.get(remove_path_identity)
        else:
            after = [*before, chosen]
        selected_path = {
            "stable_path_identity": chosen,
            "kernel_path": str(device.get("kernel_path") or device.get("path") or ""),
            "controller_identity": _controller_identity(device),
            "protocol": _text(_connection(device).get("protocol"))
            or _text(_connection(device).get("transport"))
            or "scsi",
        }
    else:
        chosen = candidate_path_identity
        if not chosen or chosen not in existing_ids:
            raise RedundancyError(
                "path_not_found", "The selected path does not belong to this storage."
            )
        if len(existing) <= 1:
            raise RedundancyError(
                "last_path_required", "Storage must keep at least one controller path."
            )
        before = [item.stable_path_identity for item in existing]
        after = [item for item in before if item != chosen]
        removed_path = next(item for item in existing if item.stable_path_identity == chosen)
        remaining_path = next(item for item in existing if item.stable_path_identity == after[0])
        removed_observed = observed_by_path.get(chosen)
        remaining_observed = observed_by_path.get(remaining_path.stable_path_identity)
        selected_path = {
            "stable_path_identity": chosen,
            "kernel_path": str(
                (removed_observed or {}).get("kernel_path") or removed_path.kernel_path
            ),
            "present": removed_observed is not None,
        }
    mapper_name = re.sub(r"[^a-zA-Z0-9_.-]", "", entity.stable_identity.split(":", 1)[1])
    settings = normalized_redundancy_settings(
        settings_override
        if settings_override is not None
        else entity.config_json.get("redundancy_settings")
    )
    if policy != "recommended":
        settings = {**settings, "mode": "custom", "path_grouping_policy": policy}
    capability = (
        {
            "mode": "online_supported",
            "message": "These settings can be applied while the redundant device remains online.",
        }
        if action == "configure"
        else transition_capability(entity, action=action, resulting_path_count=len(after))
    )
    plan: dict[str, Any] = {
        "schema_version": 1,
        "operation": f"redundancy.{action}",
        "storage_entity_id": entity.id,
        "logical_storage_identity": entity.stable_identity,
        "hardware_snapshot_sha256": hardware_snapshot_sha256,
        "identity_binding_sha256": document_hash(
            {
                "logical_storage_identity": entity.stable_identity,
                "capacity_bytes": entity.capacity_bytes,
                "logical_sector_bytes": entity.logical_sector_bytes,
                "physical_sector_bytes": entity.physical_sector_bytes,
                "filesystem_uuid": entity.filesystem_uuid,
                "mountpoint": entity.mountpoint,
            }
        ),
        "before": {
            "path_ids": before,
            "presentation_device": entity.presentation_device,
            "mountpoint": entity.mountpoint,
            "device_mountpoint": entity.config_json.get("device_mountpoint", entity.mountpoint),
            "filesystem_uuid": entity.filesystem_uuid,
        },
        "after": {
            "path_ids": after,
            "presentation_device": (
                f"/dev/mapper/{mapper_name}"
                if len(after) > 1
                else str(
                    (remaining_observed or {}).get("kernel_path")
                    or remaining_path.kernel_path
                )
            if action == "remove"
            else entity.presentation_device
            ),
            "mountpoint": entity.mountpoint,
            "filesystem_uuid": entity.filesystem_uuid,
            "topology_state": "fully_redundant" if len(after) > 1 else "single_path",
        },
        "selected_path": selected_path,
        "removed_path": (
            {
                "stable_path_identity": removed_path.stable_path_identity,
                "kernel_path": str(
                    (removed_observed or {}).get("kernel_path") or removed_path.kernel_path
                ),
                "present": removed_observed is not None,
            }
            if action == "replace"
            else None
        ),
        "policy": policy,
        "settings": settings,
        "transition": capability,
        "managed_access_services": _managed_access_services(session, entity.mountpoint),
        "destructive": False,
        "format": False,
        "copy_data": False,
        "preserves": [
            "storage_entity_id",
            "filesystem_uuid",
            "mountpoint",
            "shares",
            "application_paths",
            "acls",
            "telemetry_history",
        ],
    }
    plan["plan_sha256"] = document_hash(plan)
    return plan


def validate_redundancy_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(plan)
    supplied_hash = value.pop("plan_sha256", None)
    if not isinstance(supplied_hash, str) or document_hash(value) != supplied_hash:
        raise RedundancyError("redundancy_plan_changed", "The redundancy plan changed.")
    if (
        value.get("destructive") is not False
        or value.get("format") is not False
        or value.get("copy_data") is not False
    ):
        raise RedundancyError(
            "redundancy_plan_unsafe", "A redundancy plan may not format or copy user data."
        )
    before = value.get("before")
    after = value.get("after")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        raise RedundancyError("redundancy_plan_invalid", "The redundancy plan is incomplete.")
    if before.get("mountpoint") != after.get("mountpoint") or before.get(
        "filesystem_uuid"
    ) != after.get("filesystem_uuid"):
        raise RedundancyError(
            "storage_identity_changed", "The mount or filesystem identity changed."
        )
    transition = value.get("transition")
    if not isinstance(transition, Mapping) or transition.get("mode") not in TRANSITION_MODES:
        raise RedundancyError("redundancy_plan_invalid", "The transition capability is missing.")
    settings = value.get("settings")
    if not isinstance(settings, Mapping):
        raise RedundancyError("redundancy_plan_invalid", "Redundancy settings are missing.")
    value["settings"] = normalized_redundancy_settings(settings)
    return {**value, "plan_sha256": supplied_hash}


def apply_redundancy_result(
    session: Session,
    *,
    plan: Mapping[str, Any],
    observed_device: Mapping[str, Any] | None,
    operation_id: str | None = None,
) -> StorageEntity:
    validated = validate_redundancy_plan(plan)
    entity = session.get(StorageEntity, str(validated["storage_entity_id"]))
    if entity is None or entity.stable_identity != validated["logical_storage_identity"]:
        raise RedundancyError("storage_identity_changed", "The logical storage identity changed.")
    if (
        document_hash(
            {
                "logical_storage_identity": entity.stable_identity,
                "capacity_bytes": entity.capacity_bytes,
                "logical_sector_bytes": entity.logical_sector_bytes,
                "physical_sector_bytes": entity.physical_sector_bytes,
                "filesystem_uuid": entity.filesystem_uuid,
                "mountpoint": entity.mountpoint,
            }
        )
        != validated["identity_binding_sha256"]
    ):
        raise RedundancyError("storage_identity_changed", "The reviewed storage identity changed.")
    operation = str(validated["operation"])
    selected = validated["selected_path"]
    if operation == "redundancy.configure":
        previous_settings = entity.config_json.get("redundancy_settings")
        _record_event(
            session,
            entity,
            "redundancy_settings_changed",
            operation_id=operation_id,
            details={
                "previous_settings": previous_settings
                if isinstance(previous_settings, Mapping)
                else None,
                "settings": dict(validated["settings"]),
            },
        )
    elif operation in {"redundancy.add", "redundancy.replace"}:
        if (
            observed_device is None
            or stable_path_identity(observed_device) != selected["stable_path_identity"]
        ):
            raise RedundancyError(
                "path_identity_changed", "The new path identity changed before apply."
            )
        if logical_storage_identity(observed_device) != entity.stable_identity:
            raise RedundancyError(
                "logical_identity_changed", "The new path no longer identifies the same storage."
            )
        added_path = _upsert_path(session, entity, observed_device)
        if operation == "redundancy.replace":
            removed = validated.get("removed_path")
            removed_identity = (
                removed.get("stable_path_identity") if isinstance(removed, Mapping) else None
            )
            old_path = session.scalar(
                select(StoragePath).where(
                    StoragePath.storage_entity_id == entity.id,
                    StoragePath.stable_path_identity == removed_identity,
                )
            )
            if old_path is None:
                raise RedundancyError(
                    "replacement_path_not_found",
                    "The controller path being replaced no longer exists.",
                )
            if entity.config_json.get("preferred_path_identity") == old_path.stable_path_identity:
                entity.config_json = {
                    **entity.config_json,
                    "preferred_path_identity": selected["stable_path_identity"],
                }
            _record_event(
                session,
                entity,
                "controller_path_replaced",
                path=added_path,
                operation_id=operation_id,
                details={"removed_path_identity": old_path.stable_path_identity},
            )
            session.delete(old_path)
        else:
            _record_event(
                session,
                entity,
                "redundant_path_added",
                path=added_path,
                operation_id=operation_id,
            )
    elif operation == "redundancy.remove":
        path = session.scalar(
            select(StoragePath).where(
                StoragePath.storage_entity_id == entity.id,
                StoragePath.stable_path_identity == selected["stable_path_identity"],
            )
        )
        if path is None:
            raise RedundancyError("path_not_found", "The selected path no longer exists.")
        if entity.config_json.get("preferred_path_identity") == path.stable_path_identity:
            remaining = next(
                (
                    item
                    for item in validated["after"]["path_ids"]
                    if item != path.stable_path_identity
                ),
                None,
            )
            entity.config_json = {
                **entity.config_json,
                "preferred_path_identity": remaining,
            }
        _record_event(
            session,
            entity,
            "redundant_path_removed",
            path=path,
            operation_id=operation_id,
            details={"protection_reduced": len(validated["after"]["path_ids"]) == 1},
        )
        session.delete(path)
    else:
        raise RedundancyError("action_invalid", "The redundancy action is invalid.")
    entity.topology_state = str(validated["after"]["topology_state"])
    entity.config_json = {
        **entity.config_json,
        "redundancy_settings": dict(validated["settings"]),
        "last_transition_mode": validated["transition"]["mode"],
    }
    after_device = validated["after"].get("presentation_device")
    if isinstance(after_device, str):
        entity.presentation_device = after_device
    entity.updated_at = utc_now()
    metric = session.scalar(
        select(MetricEntity).where(
            MetricEntity.entity_type == "logical_storage",
            MetricEntity.stable_id == f"logical-storage:{entity.stable_identity}",
        )
    )
    if metric is not None:
        metric.topology_json = {
            **metric.topology_json,
            "path_count": len(validated["after"]["path_ids"]),
            "topology_state": entity.topology_state,
        }
        metric.last_seen_at = utc_now()
    return entity


def _wwid_key(value: object) -> str:
    cleaned = _text(value) or ""
    cleaned = cleaned.removeprefix("wwn:").removeprefix("0x").removeprefix("naa.")
    return re.sub(r"[^a-z0-9]", "", cleaned)


def reconcile_storage_path_health(session: Session, multipath_maps: list[Mapping[str, Any]]) -> int:
    """Attach live DM-Multipath path state to durable logical storage identity."""

    maps_by_wwid = {
        _wwid_key(item.get("wwid")): item for item in multipath_maps if _wwid_key(item.get("wwid"))
    }
    changed = 0
    entities = list(session.scalars(select(StorageEntity)))
    for entity in entities:
        multipath = maps_by_wwid.get(_wwid_key(entity.stable_identity))
        if multipath is None:
            continue
        paths = list(
            session.scalars(select(StoragePath).where(StoragePath.storage_entity_id == entity.id))
        )
        reported_paths = multipath.get("paths")
        reported = (
            {
                str(item.get("kernel_name") or ""): item
                for item in reported_paths
                if isinstance(item, Mapping) and item.get("kernel_name")
            }
            if isinstance(reported_paths, list)
            else {}
        )
        active_path_ids: set[str] = set()
        for path in paths:
            previous_path_state = path.state
            live = reported.get(path.kernel_path.rsplit("/", 1)[-1])
            state = str(live.get("state") or "unknown") if live else "missing"
            active = state in {"active", "ready", "running", "up"}
            path.state = state
            path.active = active
            path.optimized = (
                bool(live.get("optimized"))
                if live is not None and isinstance(live.get("optimized"), bool)
                else None
            )
            path.last_seen_at = utc_now()
            if active:
                active_path_ids.add(path.stable_path_identity)
            if path.controller_id:
                controller = session.get(StorageController, path.controller_id)
                if controller is not None:
                    controller.state_json = {
                        **controller.state_json,
                        "health": "healthy" if active else "unavailable",
                        "last_path_state": state,
                    }
                    controller.last_seen_at = utc_now()
            if previous_path_state != state:
                event_type = (
                    "path_recovered"
                    if active
                    else "path_failed"
                    if state in {"failed", "faulty", "offline", "missing"}
                    else "path_state_changed"
                )
                _record_event(
                    session,
                    entity,
                    event_type,
                    path=path,
                    previous_state=previous_path_state,
                    resulting_state=state,
                    details={"kernel_path": path.kernel_path},
                )
        preferred = entity.config_json.get("preferred_path_identity")
        if not active_path_ids:
            topology_state = "no_path"
        elif len(paths) == 1:
            topology_state = "single_path"
        elif len(active_path_ids) < len(paths):
            topology_state = (
                "failed_over" if preferred not in active_path_ids else "reduced_redundancy"
            )
        else:
            topology_state = "fully_redundant"
        if entity.topology_state != topology_state:
            previous_topology = entity.topology_state
            entity.topology_state = topology_state
            entity.updated_at = utc_now()
            event_type = {
                "fully_redundant": "redundancy_restored",
                "failed_over": "controller_failover",
                "reduced_redundancy": "redundancy_reduced",
                "no_path": "all_paths_lost",
                "single_path": "single_path_active",
            }.get(topology_state, "redundancy_state_changed")
            _record_event(
                session,
                entity,
                event_type,
                previous_state=previous_topology,
                resulting_state=topology_state,
                details={
                    "healthy_paths": len(active_path_ids),
                    "total_paths": len(paths),
                    "active_path_identities": sorted(active_path_ids),
                },
            )
            changed += 1
    return changed


def update_redundancy_settings(
    session: Session, storage_entity_id: str, settings: Mapping[str, Any]
) -> dict[str, Any]:
    entity = session.get(StorageEntity, storage_entity_id)
    if entity is None:
        raise RedundancyError("storage_not_found", "The logical storage object was not found.")
    normalized = normalized_redundancy_settings(settings)
    entity.config_json = {**entity.config_json, "redundancy_settings": normalized}
    entity.updated_at = utc_now()
    return normalized


def redundancy_event_documents(
    session: Session, storage_entity_id: str, *, limit: int = 200
) -> list[dict[str, Any]]:
    if session.get(StorageEntity, storage_entity_id) is None:
        raise RedundancyError("storage_not_found", "The logical storage object was not found.")
    events = session.scalars(
        select(StorageRedundancyEvent)
        .where(StorageRedundancyEvent.storage_entity_id == storage_entity_id)
        .order_by(StorageRedundancyEvent.occurred_at.desc(), StorageRedundancyEvent.id.desc())
        .limit(max(1, min(limit, 500)))
    )
    return [
        {
            "id": event.id,
            "event_type": event.event_type,
            "path_id": event.path_id,
            "controller_id": event.controller_id,
            "operation_id": event.operation_id,
            "previous_state": event.previous_state,
            "resulting_state": event.resulting_state,
            "details": event.details_json,
            "occurred_at": event.occurred_at,
        }
        for event in events
    ]


def storage_documents(
    session: Session, hardware_snapshot: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    entities = list(
        session.scalars(select(StorageEntity).order_by(StorageEntity.name, StorageEntity.id))
    )
    documents: list[dict[str, Any]] = []
    for entity in entities:
        paths = list(
            session.scalars(select(StoragePath).where(StoragePath.storage_entity_id == entity.id))
        )
        existing_path_ids = {path.stable_path_identity for path in paths}
        available_paths: list[dict[str, Any]] = []
        if hardware_snapshot is not None:
            for observed in matching_devices(hardware_snapshot, entity.stable_identity):
                try:
                    identity = stable_path_identity(observed)
                except RedundancyError:
                    continue
                if identity in existing_path_ids:
                    continue
                available_paths.append(
                    {
                        "stable_path_identity": identity,
                        "kernel_path": str(observed.get("kernel_path") or "Not reported"),
                        "controller_identity": _controller_identity(observed),
                        "protocol": _text(_connection(observed).get("protocol"))
                        or _text(_connection(observed).get("transport"))
                        or "scsi",
                    }
                )
        controllers = {
            item.id: item
            for item in session.scalars(
                select(StorageController).where(
                    StorageController.id.in_(
                        [path.controller_id for path in paths if path.controller_id]
                    )
                )
            )
        }
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        failover_events = list(
            session.scalars(
                select(StorageRedundancyEvent)
                .where(
                    StorageRedundancyEvent.storage_entity_id == entity.id,
                    StorageRedundancyEvent.event_type == "controller_failover",
                )
                .order_by(StorageRedundancyEvent.occurred_at.desc())
            )
        )
        failovers_today = sum(
            1
            for event in failover_events
            if (
                event.occurred_at.replace(tzinfo=UTC)
                if event.occurred_at.tzinfo is None
                else event.occurred_at.astimezone(UTC)
            )
            >= today
        )
        active_paths = sum(path.active for path in paths)
        degraded_since = session.scalar(
            select(StorageRedundancyEvent.occurred_at)
            .where(
                StorageRedundancyEvent.storage_entity_id == entity.id,
                StorageRedundancyEvent.resulting_state.in_(
                    ("reduced_redundancy", "failed_over", "no_path")
                ),
            )
            .order_by(StorageRedundancyEvent.occurred_at.desc())
            .limit(1)
        )
        degraded_seconds = 0
        if (
            entity.topology_state in {"reduced_redundancy", "failed_over", "no_path"}
            and degraded_since
        ):
            degraded_at = (
                degraded_since.replace(tzinfo=UTC)
                if degraded_since.tzinfo is None
                else degraded_since.astimezone(UTC)
            )
            degraded_seconds = max(0, int((datetime.now(UTC) - degraded_at).total_seconds()))
        documents.append(
            {
                "id": entity.id,
                "name": entity.name,
                "stable_identity": entity.stable_identity,
                "storage_kind": entity.storage_kind,
                "provider": entity.provider,
                "filesystem_uuid": entity.filesystem_uuid,
                "mountpoint": entity.mountpoint,
                "presentation_device": entity.presentation_device,
                "topology_state": entity.topology_state,
                "capacity_bytes": entity.capacity_bytes,
                "node_name": entity.config_json.get("node_name"),
                "storage_scope": entity.config_json.get("storage_scope", "local"),
                "ownership_mode": entity.config_json.get("ownership_mode"),
                "ownership_state": entity.config_json.get("ownership_state"),
                "peer_node": entity.config_json.get("peer_node"),
                "redundancy_capable": bool(
                    entity.config_json.get("redundancy_capable", entity.provider != "mergerfs")
                ),
                "transition_capability": (
                    transition_capability(entity, action="add", resulting_path_count=len(paths) + 1)
                    if entity.config_json.get("redundancy_capable", entity.provider != "mergerfs")
                    else {
                        "mode": "automatic_conversion_unsupported",
                        "message": (
                            "This file-level pool is managed above its member devices; "
                            "controller-path conversion does not apply."
                        ),
                    }
                ),
                "redundancy_settings": normalized_redundancy_settings(
                    entity.config_json.get("redundancy_settings")
                ),
                "redundancy_summary": {
                    "healthy_paths": active_paths,
                    "active_paths": active_paths,
                    "failed_paths": len(paths) - active_paths,
                    "failovers_today": failovers_today,
                    "last_failover": failover_events[0].occurred_at if failover_events else None,
                    "time_degraded_seconds": degraded_seconds,
                },
                "paths": [
                    {
                        "id": path.id,
                        "stable_path_identity": path.stable_path_identity,
                        "kernel_path": path.kernel_path,
                        "protocol": path.protocol,
                        "state": path.state,
                        "active": path.active,
                        "optimized": path.optimized,
                        "controller": (
                            {
                                "id": controllers[path.controller_id].id,
                                "stable_identity": controllers[path.controller_id].stable_identity,
                                "model": controllers[path.controller_id].model,
                                "provider": controllers[path.controller_id].provider,
                                "state": controllers[path.controller_id].state_json,
                            }
                            if path.controller_id in controllers
                            else None
                        ),
                        "metadata": path.metadata_json,
                    }
                    for path in paths
                ],
                "available_paths": available_paths,
            }
        )
    return documents
