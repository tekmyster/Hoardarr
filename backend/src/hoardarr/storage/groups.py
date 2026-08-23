from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hoardarr.auth.service import Principal
from hoardarr.db.models import (
    PhysicalDisk,
    StorageBackend,
    StorageEntity,
    StorageGroup,
    StorageLifecycleEvent,
)

SAFE_TRANSITIONS = {
    "assigned": {"active"},
    "active": {"preferred_write"},
    "preferred_write": {"active"},
}
MOVER_OWNED_STATES = {
    "draining",
    "verifying",
    "read_only",
    "retired",
    "reuse_ready",
    "wipe_pending",
}


class StorageGroupError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_namespace(value: str) -> str:
    candidate = value.strip()
    if not candidate.startswith("/") or candidate == "/":
        raise StorageGroupError(
            "invalid_namespace", "The namespace must be an absolute non-root path."
        )
    if any(ord(char) < 32 for char in candidate):
        raise StorageGroupError("invalid_namespace", "The namespace contains control characters.")
    path = PurePosixPath(candidate)
    if ".." in path.parts:
        raise StorageGroupError(
            "invalid_namespace", "The namespace cannot contain parent traversal."
        )
    return str(path)


def _event(
    session: Session,
    *,
    group: StorageGroup,
    principal: Principal,
    event_type: str,
    resulting_state: str,
    backend: StorageBackend | None = None,
    previous_state: str | None = None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        StorageLifecycleEvent(
            storage_group_id=group.id,
            storage_backend_id=backend.id if backend else None,
            physical_disk_id=backend.physical_disk_id if backend else None,
            event_type=event_type,
            previous_state=previous_state,
            resulting_state=resulting_state,
            actor_type=principal.auth_type,
            actor_id=principal.user_id,
            reason=reason,
            details_json=details or {},
        )
    )


def create_group(
    session: Session,
    *,
    name: str,
    namespace_path: str,
    purpose: str,
    principal: Principal,
) -> StorageGroup:
    cleaned_name = name.strip()
    if not cleaned_name:
        raise StorageGroupError("invalid_group_name", "The Storage Group name cannot be blank.")
    group = StorageGroup(
        name=cleaned_name,
        namespace_path=normalize_namespace(namespace_path),
        purpose=purpose,
        state="active",
        policy_json={"placement": "preferred_then_available", "single_writer": True},
    )
    session.add(group)
    try:
        session.flush()
    except IntegrityError as exc:
        raise StorageGroupError(
            "storage_group_conflict", "A Storage Group already uses that name or namespace."
        ) from exc
    _event(
        session,
        group=group,
        principal=principal,
        event_type="storage_group_created",
        resulting_state="active",
        details={"namespace_path": group.namespace_path, "purpose": group.purpose},
    )
    return group


def register_disk(session: Session, observation: dict[str, Any]) -> tuple[PhysicalDisk, bool]:
    stable_identity = str(observation.get("stable_identity") or "").strip()
    if len(stable_identity) < 3:
        raise StorageGroupError(
            "stable_identity_required",
            "A durable WWN, EUI, NGUID, or serial-derived identity is required.",
        )
    disk = session.scalar(
        select(PhysicalDisk).where(PhysicalDisk.stable_identity == stable_identity)
    )
    created = disk is None
    if disk is None:
        disk = PhysicalDisk(stable_identity=stable_identity)
        session.add(disk)
    for field in (
        "kernel_path",
        "serial",
        "wwn",
        "vendor",
        "model",
        "capacity_bytes",
        "logical_sector_bytes",
        "physical_sector_bytes",
        "media_type",
    ):
        if field in observation:
            setattr(disk, field, observation[field])
    disk.health_state = str(observation.get("health_state") or "not_reported")
    disk.metadata_json = dict(observation.get("metadata") or {})
    disk.last_seen_at = datetime.now(UTC)
    session.flush()
    return disk, created


def _bounded_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = "".join(char for char in value.strip() if ord(char) >= 32)
    return cleaned[:limit] or None


def reconcile_snapshot_disks(session: Session, snapshot: dict[str, Any]) -> dict[str, int]:
    """Update the registry from a validated detector snapshot without trusting /dev identity."""

    observed = snapshot.get("disks")
    if not isinstance(observed, list):
        return {"observed": 0, "created": 0, "updated": 0, "skipped": 0}
    created = updated = skipped = 0
    for raw in observed[:4096]:
        if not isinstance(raw, dict) or raw.get("stable_identity") is not True:
            skipped += 1
            continue
        stable_identity = _bounded_text(raw.get("id"), 512)
        identity = raw.get("identity") if isinstance(raw.get("identity"), dict) else {}
        sectors = raw.get("sector_sizes") if isinstance(raw.get("sector_sizes"), dict) else {}
        connection = raw.get("connection") if isinstance(raw.get("connection"), dict) else {}
        if stable_identity is None:
            skipped += 1
            continue
        transport = _bounded_text(connection.get("transport"), 32)
        media_type = (
            "nvme"
            if transport == "nvme"
            else "removable"
            if raw.get("removable") is True
            else "hdd"
            if raw.get("rotational") is True
            else "ssd"
            if raw.get("rotational") is False
            else "unknown"
        )
        health = raw.get("health") if isinstance(raw.get("health"), dict) else {}
        overall = health.get("overall") if isinstance(health.get("overall"), dict) else {}
        reported_health = _bounded_text(overall.get("status"), 32)
        health_state = (
            reported_health
            if reported_health in {"healthy", "warning", "critical", "unsupported"}
            else "not_reported"
        )
        _disk, was_created = register_disk(
            session,
            {
                "stable_identity": stable_identity,
                "kernel_path": _bounded_text(raw.get("kernel_path"), 4096),
                "serial": _bounded_text(identity.get("serial"), 256),
                "wwn": _bounded_text(identity.get("wwn"), 256),
                "vendor": _bounded_text(raw.get("vendor"), 128),
                "model": _bounded_text(raw.get("model"), 256),
                "capacity_bytes": raw.get("capacity_bytes")
                if isinstance(raw.get("capacity_bytes"), int)
                and 0 <= raw["capacity_bytes"] <= 10**18
                else None,
                "logical_sector_bytes": sectors.get("logical_bytes")
                if isinstance(sectors.get("logical_bytes"), int)
                else None,
                "physical_sector_bytes": sectors.get("physical_bytes")
                if isinstance(sectors.get("physical_bytes"), int)
                else None,
                "media_type": media_type,
                "health_state": health_state,
                "metadata": {
                    key: connection[key]
                    for key in (
                        "transport",
                        "protocol",
                        "controller_address",
                        "enclosure_id",
                        "slot",
                    )
                    if key in connection
                },
            },
        )
        created += int(was_created)
        updated += int(not was_created)
    return {"observed": len(observed), "created": created, "updated": updated, "skipped": skipped}


def assign_backend(
    session: Session,
    *,
    group_id: str,
    physical_disk_id: str | None,
    storage_entity_id: str | None,
    namespace_path: str | None,
    role: str,
    principal: Principal,
) -> StorageBackend:
    group = session.get(StorageGroup, group_id)
    if group is None:
        raise StorageGroupError("storage_group_not_found", "The Storage Group does not exist.")
    if bool(physical_disk_id) == bool(storage_entity_id):
        raise StorageGroupError(
            "backend_identity_required",
            "Select exactly one physical disk or logical storage entity.",
        )
    disk = session.get(PhysicalDisk, physical_disk_id) if physical_disk_id else None
    entity = session.get(StorageEntity, storage_entity_id) if storage_entity_id else None
    if physical_disk_id and disk is None:
        raise StorageGroupError("physical_disk_not_found", "The registered disk does not exist.")
    if storage_entity_id and entity is None:
        raise StorageGroupError(
            "storage_entity_not_found", "The logical storage object does not exist."
        )
    stable_identity = (
        f"disk:{disk.stable_identity}" if disk else f"storage:{entity.stable_identity}"
    )
    backend = StorageBackend(
        storage_group_id=group.id,
        storage_entity_id=entity.id if entity else None,
        physical_disk_id=disk.id if disk else None,
        stable_identity=stable_identity,
        namespace_path=normalize_namespace(namespace_path) if namespace_path else None,
        role=role,
        lifecycle_state="assigned",
        config_json={},
    )
    session.add(backend)
    try:
        session.flush()
    except IntegrityError as exc:
        raise StorageGroupError(
            "backend_already_assigned", "That backend is already assigned to this group."
        ) from exc
    if disk:
        disk.lifecycle_state = "assigned"
    _event(
        session,
        group=group,
        backend=backend,
        principal=principal,
        event_type="backend_assigned",
        resulting_state="assigned",
        details={"stable_identity": stable_identity, "role": role},
    )
    return backend


def transition_backend(
    session: Session,
    *,
    group_id: str,
    backend_id: str,
    target_state: str,
    principal: Principal,
    reason: str | None,
) -> StorageBackend:
    group = session.get(StorageGroup, group_id)
    backend = session.get(StorageBackend, backend_id)
    if group is None or backend is None or backend.storage_group_id != group.id:
        raise StorageGroupError("backend_not_found", "The Storage Group backend does not exist.")
    previous = backend.lifecycle_state
    if target_state in MOVER_OWNED_STATES:
        raise StorageGroupError(
            "durable_operation_required",
            "Drain, verification, retirement, reuse, and wipe states must be entered "
            "by their durable operation.",
        )
    if target_state not in SAFE_TRANSITIONS.get(previous, set()):
        raise StorageGroupError(
            "invalid_lifecycle_transition",
            f"Cannot transition a backend from {previous} to {target_state}.",
        )
    if target_state == "preferred_write":
        current_preferred = list(
            session.scalars(
                select(StorageBackend).where(
                    StorageBackend.storage_group_id == group.id,
                    StorageBackend.lifecycle_state == "preferred_write",
                    StorageBackend.id != backend.id,
                )
            )
        )
        for current in current_preferred:
            current.lifecycle_state = "active"
            _event(
                session,
                group=group,
                backend=current,
                principal=principal,
                event_type="preferred_write_replaced",
                previous_state="preferred_write",
                resulting_state="active",
                reason=reason,
            )
        # The partial unique index is the final concurrency guard. Flush the
        # demotion before promotion so SQLite cannot observe two preferred rows
        # during SQLAlchemy's otherwise unordered UPDATE batch. Both remain in
        # the same transaction and roll back together on any later failure.
        if current_preferred:
            session.flush()
    backend.lifecycle_state = target_state
    if backend.physical_disk_id:
        disk = session.get(PhysicalDisk, backend.physical_disk_id)
        if disk:
            disk.lifecycle_state = target_state
    _event(
        session,
        group=group,
        backend=backend,
        principal=principal,
        event_type=f"backend_{target_state}",
        previous_state=previous,
        resulting_state=target_state,
        reason=reason,
    )
    try:
        session.flush()
    except IntegrityError as exc:
        raise StorageGroupError(
            "preferred_write_conflict",
            "Another request selected a preferred-write backend first. Refresh and try again.",
        ) from exc
    return backend


def group_documents(session: Session) -> list[dict[str, Any]]:
    groups = list(session.scalars(select(StorageGroup).order_by(StorageGroup.name)))
    documents: list[dict[str, Any]] = []
    for group in groups:
        backends = list(
            session.scalars(
                select(StorageBackend)
                .where(StorageBackend.storage_group_id == group.id)
                .order_by(StorageBackend.created_at)
            )
        )
        events = list(
            session.scalars(
                select(StorageLifecycleEvent)
                .where(StorageLifecycleEvent.storage_group_id == group.id)
                .order_by(StorageLifecycleEvent.occurred_at.desc())
                .limit(50)
            )
        )
        documents.append(
            {
                "id": group.id,
                "name": group.name,
                "namespace_path": group.namespace_path,
                "purpose": group.purpose,
                "state": group.state,
                "policy": group.policy_json,
                "backends": [
                    {
                        "id": item.id,
                        "stable_identity": item.stable_identity,
                        "physical_disk_id": item.physical_disk_id,
                        "storage_entity_id": item.storage_entity_id,
                        "namespace_path": item.namespace_path,
                        "role": item.role,
                        "lifecycle_state": item.lifecycle_state,
                    }
                    for item in backends
                ],
                "events": [
                    {
                        "id": event.id,
                        "event_type": event.event_type,
                        "backend_id": event.storage_backend_id,
                        "previous_state": event.previous_state,
                        "resulting_state": event.resulting_state,
                        "reason": event.reason,
                        "occurred_at": event.occurred_at.isoformat(),
                    }
                    for event in events
                ],
            }
        )
    return documents


def disk_documents(session: Session) -> list[dict[str, Any]]:
    return [
        {
            "id": disk.id,
            "stable_identity": disk.stable_identity,
            "kernel_path": disk.kernel_path,
            "serial": disk.serial,
            "wwn": disk.wwn,
            "vendor": disk.vendor,
            "model": disk.model,
            "capacity_bytes": disk.capacity_bytes,
            "media_type": disk.media_type,
            "health_state": disk.health_state,
            "lifecycle_state": disk.lifecycle_state,
            "last_seen_at": disk.last_seen_at.isoformat(),
        }
        for disk in session.scalars(select(PhysicalDisk).order_by(PhysicalDisk.stable_identity))
    ]
