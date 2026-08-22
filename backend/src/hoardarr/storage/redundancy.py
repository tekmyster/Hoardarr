from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.db.models import MetricEntity, StorageController, StorageEntity, StoragePath, utc_now
from hoardarr.operations.service import document_hash


class RedundancyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


AUTHORITATIVE_IDENTIFIERS = ("wwn", "nguid", "eui64")
SAFE_POLICY_VALUES = {"recommended", "failover", "multibus", "group_by_prio"}


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().casefold()
    return cleaned or None


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
            model=_text(connection.get("controller_model")),
            state_json={},
        )
        session.add(controller)
        session.flush()
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
    if not isinstance(storage, Mapping) or storage.get("topology") not in {
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
) -> dict[str, Any]:
    if action not in {"add", "remove", "replace"}:
        raise RedundancyError(
            "action_invalid", "The redundancy action must be add, remove, or replace."
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
    if action in {"add", "replace"}:
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
    return {**value, "plan_sha256": supplied_hash}


def apply_redundancy_result(
    session: Session,
    *,
    plan: Mapping[str, Any],
    observed_device: Mapping[str, Any] | None,
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
    if operation in {"redundancy.add", "redundancy.replace"}:
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
        _upsert_path(session, entity, observed_device)
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
            session.delete(old_path)
    elif operation == "redundancy.remove":
        path = session.scalar(
            select(StoragePath).where(
                StoragePath.storage_entity_id == entity.id,
                StoragePath.stable_path_identity == selected["stable_path_identity"],
            )
        )
        if path is None:
            raise RedundancyError("path_not_found", "The selected path no longer exists.")
        session.delete(path)
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
    else:
        raise RedundancyError("action_invalid", "The redundancy action is invalid.")
    entity.topology_state = str(validated["after"]["topology_state"])
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
            entity.topology_state = topology_state
            entity.updated_at = utc_now()
            changed += 1
    return changed


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
        documents.append(
            {
                "id": entity.id,
                "name": entity.name,
                "stable_identity": entity.stable_identity,
                "filesystem_uuid": entity.filesystem_uuid,
                "mountpoint": entity.mountpoint,
                "presentation_device": entity.presentation_device,
                "topology_state": entity.topology_state,
                "capacity_bytes": entity.capacity_bytes,
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
                            }
                            if path.controller_id in controllers
                            else None
                        ),
                    }
                    for path in paths
                ],
                "available_paths": available_paths,
            }
        )
    return documents
