from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import islice
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.db.models import (
    IntegrationConnection,
    PhysicalDisk,
    StorageBackend,
    StorageEntity,
    StorageGroup,
)
from hoardarr.operations.service import document_hash


class DrainPlanError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FilesystemFacts:
    path: str
    device_number: int
    total_bytes: int
    used_bytes: int
    free_bytes: int


FilesystemProbe = Callable[[str], FilesystemFacts]
OpenUseProbe = Callable[[str], dict[str, Any]]


def _path_without_symlinks(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in PurePosixPath(value).parts or "\x00" in value:
        raise DrainPlanError("drain_path_invalid", "Drain paths must be absolute Linux paths.")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            if current.is_symlink():
                raise DrainPlanError(
                    "drain_path_symlink", "Drain paths cannot contain symbolic links."
                )
        except OSError as exc:
            raise DrainPlanError(
                "drain_path_unavailable", "A configured drain path could not be inspected."
            ) from exc
    return path


def inspect_filesystem(value: str) -> FilesystemFacts:
    path = _path_without_symlinks(value)
    try:
        stat = path.stat()
        usage = os.statvfs(path)
    except OSError as exc:
        raise DrainPlanError(
            "drain_path_unavailable", f"The configured backend path is unavailable: {value}"
        ) from exc
    total = usage.f_blocks * usage.f_frsize
    free = usage.f_bavail * usage.f_frsize
    return FilesystemFacts(
        path=str(path),
        device_number=stat.st_dev,
        total_bytes=total,
        used_bytes=max(total - (usage.f_bfree * usage.f_frsize), 0),
        free_bytes=max(free, 0),
    )


def inspect_open_use(
    value: str, *, process_limit: int = 2048, fd_limit: int = 65_536
) -> dict[str, Any]:
    """Return a bounded, read-only view of processes holding files below ``value``."""

    source = str(_path_without_symlinks(value)).rstrip("/") + "/"
    processes: list[dict[str, Any]] = []
    handles = inspected = 0
    try:
        proc_entries: Iterable[Path] = islice(Path("/proc").iterdir(), process_limit)
    except OSError:
        return {"quality": "temporarily_unavailable", "open_handles": None, "processes": []}
    for process in proc_entries:
        if not process.name.isdigit() or inspected >= fd_limit:
            continue
        matched = 0
        try:
            descriptors = (process / "fd").iterdir()
        except OSError:
            continue
        try:
            for descriptor in islice(descriptors, max(fd_limit - inspected, 0)):
                inspected += 1
                try:
                    target = os.readlink(descriptor)
                except OSError:
                    continue
                normalized = target.removesuffix(" (deleted)")
                if normalized == value or normalized.startswith(source):
                    handles += 1
                    matched += 1
        except OSError:
            # A process can disappear or deny /proc access after its directory
            # was listed. This is ordinary partial visibility, not a planner
            # failure; continue with the remaining bounded process set.
            continue
        if matched and len(processes) < 16:
            try:
                name = (process / "comm").read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                name = "not reported"
            safe_name = "".join(char for char in name if ord(char) >= 32)[:128]
            processes.append(
                {"pid": int(process.name), "name": safe_name or "not reported", "handles": matched}
            )
    return {
        "quality": "available",
        "open_handles": handles,
        "processes": processes,
        "inspection_limited": inspected >= fd_limit,
    }


def _backend_path(session: Session, backend: StorageBackend) -> str:
    if backend.namespace_path:
        return backend.namespace_path
    if backend.storage_entity_id:
        entity = session.get(StorageEntity, backend.storage_entity_id)
        if entity is not None:
            return entity.mountpoint
    raise DrainPlanError(
        "backend_path_required",
        "Every drain source and destination must have a configured backend mount path.",
    )


def _backend_health(session: Session, backend: StorageBackend) -> str:
    if backend.physical_disk_id:
        disk = session.get(PhysicalDisk, backend.physical_disk_id)
        return disk.health_state if disk is not None else "temporarily_unavailable"
    if backend.storage_entity_id:
        entity = session.get(StorageEntity, backend.storage_entity_id)
        if entity is None:
            return "temporarily_unavailable"
        if entity.topology_state in {"no_path", "offline", "faulted"}:
            return "critical"
        if entity.topology_state in {"reduced_redundancy", "degraded"}:
            return "warning"
    return "not_reported"


def arr_activity(session: Session) -> dict[str, Any]:
    connections = list(
        session.scalars(
            select(IntegrationConnection).where(IntegrationConnection.adapter == "servarr")
        )
    )
    if not connections:
        return {"quality": "unsupported", "active_writes": 0, "applications": []}
    active: list[dict[str, Any]] = []
    reported = 0
    for connection in connections:
        state = connection.state_json if isinstance(connection.state_json, dict) else {}
        value = state.get("active_writes")
        if isinstance(value, int) and value >= 0:
            reported += 1
            if value:
                active.append(
                    {
                        "integration_id": connection.id,
                        "product": connection.discovered_product or connection.expected_product,
                        "active_writes": value,
                    }
                )
    quality = "available" if reported == len(connections) else "temporarily_unavailable"
    return {
        "quality": quality,
        "active_writes": sum(item["active_writes"] for item in active),
        "applications": active,
        "reported_connections": reported,
        "configured_connections": len(connections),
    }


def build_drain_plan(
    session: Session,
    *,
    group_id: str,
    source_backend_id: str,
    destination_backend_ids: list[str],
    verification_mode: str,
    reserve_bytes: int,
    filesystem_probe: FilesystemProbe | None = None,
    open_use_probe: OpenUseProbe | None = None,
) -> dict[str, Any]:
    filesystem_probe = filesystem_probe or inspect_filesystem
    open_use_probe = open_use_probe or inspect_open_use
    if verification_mode not in {"fast", "accurate", "paranoid"}:
        raise DrainPlanError("verification_mode_invalid", "Unknown drain verification mode.")
    if reserve_bytes < 0 or reserve_bytes > 10**15:
        raise DrainPlanError("reserve_invalid", "The destination reserve is outside safe bounds.")
    if not destination_backend_ids or len(destination_backend_ids) > 64:
        raise DrainPlanError(
            "destination_required", "Select between one and 64 destination backends."
        )
    if len(set(destination_backend_ids)) != len(destination_backend_ids):
        raise DrainPlanError("destination_duplicate", "A destination backend was selected twice.")
    if source_backend_id in destination_backend_ids:
        raise DrainPlanError("source_destination_same", "The source cannot also be a destination.")

    group = session.get(StorageGroup, group_id)
    source = session.get(StorageBackend, source_backend_id)
    if group is None:
        raise DrainPlanError("storage_group_not_found", "The Storage Group does not exist.")
    if source is None or source.storage_group_id != group.id:
        raise DrainPlanError("source_backend_not_found", "The drain source does not exist.")
    if source.lifecycle_state not in {"active", "preferred_write"}:
        raise DrainPlanError(
            "source_state_invalid", "Only an active backend can be prepared for draining."
        )
    destinations: list[StorageBackend] = []
    for destination_id in destination_backend_ids:
        destination = session.get(StorageBackend, destination_id)
        if destination is None or destination.storage_group_id != group.id:
            raise DrainPlanError(
                "destination_backend_not_found", "A selected destination does not exist."
            )
        if destination.lifecycle_state not in {"active", "preferred_write"}:
            raise DrainPlanError(
                "destination_state_invalid", "Drain destinations must be active backends."
            )
        if destination.role not in {"data", "archive"}:
            raise DrainPlanError(
                "destination_role_invalid",
                "Parity, cache, and landing backends cannot receive a drain.",
            )
        destinations.append(destination)

    source_path = _backend_path(session, source)
    source_facts = filesystem_probe(source_path)
    destination_documents: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    total_free = 0
    destination_devices: set[int] = set()
    destination_paths: list[PurePosixPath] = []
    for destination in destinations:
        path = _backend_path(session, destination)
        source_posix = PurePosixPath(source_path)
        destination_posix = PurePosixPath(path)
        if (
            destination_posix == source_posix
            or destination_posix in source_posix.parents
            or source_posix in destination_posix.parents
        ):
            raise DrainPlanError(
                "drain_path_overlap", "A destination cannot contain the drain source."
            )
        facts = filesystem_probe(path)
        if facts.device_number == source_facts.device_number:
            raise DrainPlanError(
                "source_destination_filesystem_same",
                "A destination is on the same filesystem as the drain source.",
            )
        if facts.device_number in destination_devices:
            raise DrainPlanError(
                "destination_filesystem_duplicate",
                "Two selected destinations resolve to the same filesystem.",
            )
        if any(
            destination_posix in previous.parents or previous in destination_posix.parents
            for previous in destination_paths
        ):
            raise DrainPlanError(
                "destination_path_overlap", "Selected destination paths cannot overlap."
            )
        destination_devices.add(facts.device_number)
        destination_paths.append(destination_posix)
        health = _backend_health(session, destination)
        if health in {"critical", "temporarily_unavailable"}:
            blockers.append(
                {
                    "code": "destination_unhealthy",
                    "message": f"Destination {destination.id} is not healthy.",
                }
            )
        elif health in {"warning", "not_reported", "unsupported"}:
            warnings.append(
                {
                    "code": "destination_health_not_confirmed",
                    "message": f"Destination {destination.id} health is {health}.",
                }
            )
        total_free += facts.free_bytes
        destination_documents.append(
            {
                "backend_id": destination.id,
                "stable_identity": destination.stable_identity,
                "path": path,
                "filesystem_device": facts.device_number,
                "free_bytes": facts.free_bytes,
                "total_bytes": facts.total_bytes,
                "health": health,
            }
        )

    required_bytes = source_facts.used_bytes
    if total_free < required_bytes + reserve_bytes:
        blockers.append(
            {
                "code": "destination_capacity_insufficient",
                "message": "Selected destinations do not have enough free space plus reserve.",
            }
        )
    source_use = open_use_probe(source_path)
    if isinstance(source_use.get("open_handles"), int) and source_use["open_handles"] > 0:
        blockers.append(
            {
                "code": "source_in_use",
                "message": "Processes currently have files open on the source backend.",
            }
        )
    elif source_use.get("quality") != "available":
        warnings.append(
            {
                "code": "open_use_not_reported",
                "message": "Open-file activity could not be confirmed.",
            }
        )
    arr = arr_activity(session)
    if arr["active_writes"]:
        blockers.append(
            {
                "code": "arr_active_writes",
                "message": "A connected ARR application reports active write-sensitive work.",
            }
        )
    elif arr["quality"] == "temporarily_unavailable":
        warnings.append(
            {
                "code": "arr_activity_not_reported",
                "message": "Not every connected ARR application reported active-write state.",
            }
        )

    document: dict[str, Any] = {
        "schema_version": 1,
        "kind": "storage.drain",
        "storage_group_id": group.id,
        "storage_group_namespace": group.namespace_path,
        "source": {
            "backend_id": source.id,
            "stable_identity": source.stable_identity,
            "path": source_path,
            "filesystem_device": source_facts.device_number,
            "required_bytes": required_bytes,
            "health": _backend_health(session, source),
            "lifecycle_state": source.lifecycle_state,
        },
        "destinations": destination_documents,
        "verification": {
            "mode": verification_mode,
            "full_hashes": verification_mode in {"accurate", "paranoid"},
            "additional_read_pass": verification_mode == "paranoid",
        },
        "capacity": {
            "required_bytes": required_bytes,
            "destination_free_bytes": total_free,
            "reserve_bytes": reserve_bytes,
        },
        "open_use": source_use,
        "arr_activity": arr,
        "blockers": blockers,
        "warnings": warnings,
        "ready": not blockers,
        "phases": [
            "preflight",
            "remove_from_write_placement",
            "copy",
            "verify",
            "finalize",
            "reconcile_namespace",
        ],
    }
    document["plan_sha256"] = document_hash(document)
    return document


def validate_drain_plan(document: dict[str, Any]) -> None:
    supplied = document.get("plan_sha256")
    if not isinstance(supplied, str) or len(supplied) != 64:
        raise DrainPlanError("drain_plan_invalid", "The drain plan digest is missing.")
    unsigned = {key: value for key, value in document.items() if key != "plan_sha256"}
    if document_hash(unsigned) != supplied:
        raise DrainPlanError("drain_plan_changed", "The immutable drain plan was modified.")
    if document.get("kind") != "storage.drain" or document.get("schema_version") != 1:
        raise DrainPlanError("drain_plan_invalid", "The drain plan schema is unsupported.")

    def bounded_text(value: object, *, maximum: int = 4096) -> bool:
        return (
            isinstance(value, str)
            and 0 < len(value) <= maximum
            and not any(ord(character) < 32 for character in value)
        )

    def absolute_path(value: object) -> bool:
        return bounded_text(value) and PurePosixPath(str(value)).is_absolute()

    group_id = document.get("storage_group_id")
    namespace = document.get("storage_group_namespace")
    source = document.get("source")
    destinations = document.get("destinations")
    verification = document.get("verification")
    capacity = document.get("capacity")
    if (
        not bounded_text(group_id, maximum=64)
        or not absolute_path(namespace)
        or not isinstance(source, dict)
        or not isinstance(destinations, list)
        or not 1 <= len(destinations) <= 64
        or not isinstance(verification, dict)
        or not isinstance(capacity, dict)
        or document.get("ready") is not True
        or not isinstance(document.get("blockers"), list)
        or document.get("blockers")
        or not isinstance(document.get("warnings"), list)
    ):
        raise DrainPlanError("drain_plan_invalid", "The drain plan structure is invalid.")

    def valid_filesystem(item: object, *, source_item: bool) -> bool:
        if not isinstance(item, dict):
            return False
        required_numbers = ["filesystem_device"]
        required_numbers.append("required_bytes" if source_item else "free_bytes")
        return (
            bounded_text(item.get("backend_id"), maximum=64)
            and bounded_text(item.get("stable_identity"), maximum=512)
            and absolute_path(item.get("path"))
            and all(
                isinstance(item.get(field), int)
                and not isinstance(item.get(field), bool)
                and int(item[field]) >= 0
                for field in required_numbers
            )
        )

    if not valid_filesystem(source, source_item=True):
        raise DrainPlanError("drain_plan_invalid", "The drain source is invalid.")
    source_id = str(source["backend_id"])
    destination_ids: set[str] = set()
    destination_paths: list[PurePosixPath] = []
    for item in destinations:
        if not valid_filesystem(item, source_item=False):
            raise DrainPlanError("drain_plan_invalid", "A drain destination is invalid.")
        backend_id = str(item["backend_id"])
        path = PurePosixPath(str(item["path"]))
        if backend_id == source_id or backend_id in destination_ids:
            raise DrainPlanError("drain_plan_invalid", "Drain backend identities must be unique.")
        if item["filesystem_device"] == source["filesystem_device"]:
            raise DrainPlanError(
                "drain_plan_invalid", "The source and destination filesystems must differ."
            )
        if path == PurePosixPath(str(source["path"])) or any(
            path in previous.parents or previous in path.parents
            for previous in [PurePosixPath(str(source["path"])), *destination_paths]
        ):
            raise DrainPlanError("drain_plan_invalid", "Drain paths cannot overlap.")
        destination_ids.add(backend_id)
        destination_paths.append(path)

    mode = verification.get("mode")
    if mode not in {"fast", "accurate", "paranoid"}:
        raise DrainPlanError("verification_mode_invalid", "The verification mode is invalid.")
    for field in ("required_bytes", "destination_free_bytes", "reserve_bytes"):
        value = capacity.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise DrainPlanError("drain_plan_invalid", "The drain capacity values are invalid.")
    if capacity["required_bytes"] != source["required_bytes"]:
        raise DrainPlanError("drain_plan_invalid", "The drain capacity values disagree.")
