from __future__ import annotations

import os
import shutil
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select, update
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
from hoardarr.operations.service import document_hash

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


def _decode_mount_field(value: str) -> str:
    for escaped, decoded in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        value = value.replace(escaped, decoded)
    return value


def _mount_source_for(path: str) -> str | None:
    mountinfo = Path("/proc/self/mountinfo")
    if os.name != "posix" or not mountinfo.is_file():
        return None
    try:
        lines = mountinfo.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines[:16_384]:
        fields = line.split()
        if len(fields) < 10 or "-" not in fields:
            continue
        separator = fields.index("-")
        if separator + 2 >= len(fields):
            continue
        if _decode_mount_field(fields[4]) == path:
            return _decode_mount_field(fields[separator + 2])
    return None


def _source_matches_device(source: str, kernel_path: str) -> bool:
    try:
        if os.path.realpath(source) == os.path.realpath(kernel_path):
            return True
    except OSError:
        return False
    if shutil.which("lsblk") is None:
        return False
    try:
        result = subprocess.run(
            ["lsblk", "--noheadings", "--paths", "--output", "PKNAME", source],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    parents = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return os.path.realpath(kernel_path) in {os.path.realpath(item) for item in parents}


def inspect_backend_activation(
    path_value: str,
    *,
    expected_kernel_path: str | None,
    expected_entity_mountpoint: str | None,
) -> dict[str, Any]:
    """Collect read-only evidence that an assigned backend is its intended mounted storage."""

    path = normalize_namespace(path_value)
    allowed_roots = (PurePosixPath("/data"), PurePosixPath("/mnt"), PurePosixPath("/srv"))
    pure_path = PurePosixPath(path)
    if not any(pure_path == root or root in pure_path.parents for root in allowed_roots):
        raise StorageGroupError(
            "activation_path_outside_managed_storage",
            "The backend path must be beneath /data, /mnt, or /srv.",
        )
    current = Path(Path(path).anchor)
    for component in Path(path).parts[1:]:
        current /= component
        try:
            details = current.lstat()
        except FileNotFoundError as exc:
            raise StorageGroupError(
                "activation_path_unavailable", "The configured backend path does not exist."
            ) from exc
        except OSError as exc:
            raise StorageGroupError(
                "activation_path_unavailable", "The configured backend path cannot be inspected."
            ) from exc
        if stat.S_ISLNK(details.st_mode):
            raise StorageGroupError(
                "activation_path_symlink", "The configured backend path contains a symbolic link."
            )
    try:
        details = Path(path).stat()
        usage = os.statvfs(path)
    except OSError as exc:
        raise StorageGroupError(
            "activation_path_unavailable", "The configured backend path cannot be inspected."
        ) from exc
    if not stat.S_ISDIR(details.st_mode):
        raise StorageGroupError(
            "activation_path_not_directory", "The configured backend path is not a directory."
        )
    source = _mount_source_for(path)
    if source is None:
        raise StorageGroupError(
            "activation_exact_mount_required",
            "Activation requires the backend path to be an exact mounted filesystem.",
        )
    if expected_kernel_path is not None:
        identity_match = _source_matches_device(source, expected_kernel_path)
        identity_basis = "mounted source or its lsblk parent matches the registered kernel device"
    elif expected_entity_mountpoint is not None:
        identity_match = path == expected_entity_mountpoint
        identity_basis = "backend path matches the registered logical-storage mount"
    else:
        identity_match = False
        identity_basis = "no registered device or logical-storage mount was available"
    total = usage.f_blocks * usage.f_frsize
    free = usage.f_bavail * usage.f_frsize
    return {
        "path": path,
        "filesystem_device": details.st_dev,
        "mount_source": source,
        "exact_mount": True,
        "identity_match": identity_match,
        "identity_basis": identity_basis,
        "total_bytes": total,
        "free_bytes": free,
    }


def build_backend_activation_plan(
    session: Session,
    *,
    group_id: str,
    backend_id: str,
) -> dict[str, Any]:
    group = session.get(StorageGroup, group_id)
    backend = session.get(StorageBackend, backend_id)
    if group is None or backend is None or backend.storage_group_id != group.id:
        raise StorageGroupError("backend_not_found", "The Storage Group backend does not exist.")
    if backend.lifecycle_state != "assigned":
        raise StorageGroupError(
            "backend_not_assigned", "Only an assigned backend can be reviewed for activation."
        )
    disk = (
        session.get(PhysicalDisk, backend.physical_disk_id) if backend.physical_disk_id else None
    )
    entity = (
        session.get(StorageEntity, backend.storage_entity_id)
        if backend.storage_entity_id
        else None
    )
    path = backend.namespace_path or (entity.mountpoint if entity is not None else None)
    if path is None:
        raise StorageGroupError(
            "activation_path_required", "Configure the backend mount path before activation."
        )
    evidence = inspect_backend_activation(
        path,
        expected_kernel_path=disk.kernel_path if disk is not None else None,
        expected_entity_mountpoint=entity.mountpoint if entity is not None else None,
    )
    blockers: list[dict[str, str]] = []
    if not evidence["identity_match"]:
        blockers.append(
            {
                "code": "activation_identity_mismatch",
                "message": (
                    "The mounted filesystem cannot be proven to belong to the assigned stable "
                    "storage identity."
                ),
            }
        )
    health = disk.health_state if disk is not None else "not_reported"
    if health == "critical":
        blockers.append(
            {
                "code": "activation_health_critical",
                "message": "A disk reporting critical health cannot enter active placement.",
            }
        )
    document: dict[str, Any] = {
        "schema_version": 1,
        "kind": "storage.backend.activate",
        "storage_group_id": group.id,
        "storage_group_namespace": group.namespace_path,
        "backend_id": backend.id,
        "stable_identity": backend.stable_identity,
        "lifecycle_state": backend.lifecycle_state,
        "health": health,
        "evidence": evidence,
        "blockers": blockers,
        "ready": not blockers,
    }
    # Free space is shown to the operator but is not an identity property and
    # can legitimately change between review and apply. Bind activation to the
    # stable mount/device evidence instead of making ordinary filesystem writes
    # invalidate an otherwise safe review.
    hashed_evidence = {key: value for key, value in evidence.items() if key != "free_bytes"}
    document["plan_sha256"] = document_hash({**document, "evidence": hashed_evidence})
    return document


def activate_backend(
    session: Session,
    *,
    group_id: str,
    backend_id: str,
    plan_sha256: str,
    principal: Principal,
    reason: str | None,
) -> StorageBackend:
    plan = build_backend_activation_plan(session, group_id=group_id, backend_id=backend_id)
    if plan["plan_sha256"] != plan_sha256:
        raise StorageGroupError(
            "activation_plan_changed",
            "The mounted-storage evidence changed; review activation again.",
        )
    if not plan["ready"]:
        raise StorageGroupError(
            "activation_blocked", "The backend failed its activation safety review."
        )
    backend = transition_backend(
        session,
        group_id=group_id,
        backend_id=backend_id,
        target_state="active",
        principal=principal,
        reason=reason,
    )
    backend.config_json = {
        **backend.config_json,
        "activation": {
            "plan_sha256": plan_sha256,
            "verified_at": datetime.now(UTC).isoformat(),
            "evidence": plan["evidence"],
        },
    }
    session.flush()
    return backend


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


def _namespace_availability(
    group: StorageGroup, backends: list[StorageBackend]
) -> dict[str, str | bool | None]:
    """Report whether the advertised application path is backed by managed storage.

    A database namespace is not evidence that Linux can actually serve files at
    that path.  Only an exact activated backend path or an exact mount is
    reported as available.  This deliberately rejects an ordinary directory on
    the root filesystem as a usable media namespace.
    """

    if os.name != "posix":
        return {"quality": "not_reported", "available": None, "reason": "platform"}
    path = group.namespace_path
    active = [
        item
        for item in backends
        if item.lifecycle_state
        in {"active", "preferred_write", "draining", "verifying", "read_only"}
    ]
    for backend in active:
        activation = (backend.config_json or {}).get("activation")
        if backend.namespace_path == path and isinstance(activation, dict):
            evidence = activation.get("evidence")
            if isinstance(evidence, dict) and evidence.get("identity_match") is True:
                return {
                    "quality": "available",
                    "available": True,
                    "reason": "verified_backend_path",
                }
    if _mount_source_for(path) is not None:
        return {"quality": "available", "available": True, "reason": "exact_mount"}
    return {
        "quality": "temporarily_unavailable",
        "available": False,
        "reason": "path_missing" if not Path(path).exists() else "not_a_managed_mount",
    }


def reconcile_group_namespace(
    session: Session,
    *,
    group_id: str,
    backend_id: str,
    principal: Principal,
) -> StorageGroup:
    """Repair an unused, unavailable group path to one verified backend mount.

    This is intentionally narrow.  It never mounts, moves, copies, or deletes
    anything and refuses to change an existing path because applications may
    already depend on it.  It is for the onboarding case where a group was
    created with a placeholder path before an existing managed pool was chosen.
    """

    group = session.get(StorageGroup, group_id)
    backend = session.get(StorageBackend, backend_id)
    if group is None or backend is None or backend.storage_group_id != group.id:
        raise StorageGroupError("backend_not_found", "The Storage Group backend does not exist.")
    siblings = list(
        session.scalars(select(StorageBackend).where(StorageBackend.storage_group_id == group.id))
    )
    usable = [item for item in siblings if item.lifecycle_state != "reuse_ready"]
    if len(usable) != 1 or usable[0].id != backend.id:
        raise StorageGroupError(
            "namespace_reconciliation_ambiguous",
            "Automatic path repair requires exactly one managed backend.",
        )
    if backend.lifecycle_state not in {"active", "preferred_write"}:
        raise StorageGroupError(
            "namespace_backend_not_active", "Verify and activate the managed storage first."
        )
    activation = (backend.config_json or {}).get("activation")
    evidence = activation.get("evidence") if isinstance(activation, dict) else None
    target = backend.namespace_path
    if (
        not isinstance(evidence, dict)
        or evidence.get("identity_match") is not True
        or evidence.get("exact_mount") is not True
        or not isinstance(target, str)
        or evidence.get("path") != target
    ):
        raise StorageGroupError(
            "namespace_identity_unverified",
            "The backend does not have verified mounted-storage identity evidence.",
        )
    current_status = _namespace_availability(group, siblings)
    if current_status["available"] is not False:
        raise StorageGroupError(
            "namespace_already_available",
            "The current Storage Group path is already available and will not be changed.",
        )
    if Path(group.namespace_path).exists():
        raise StorageGroupError(
            "namespace_path_in_use",
            "The current Storage Group path exists and cannot be changed automatically.",
        )
    target_status = inspect_backend_activation(
        target,
        expected_kernel_path=None,
        expected_entity_mountpoint=target if backend.storage_entity_id else None,
    )
    if (
        target_status.get("identity_match") is not True
        or target_status.get("filesystem_device") != evidence.get("filesystem_device")
        or target_status.get("mount_source") != evidence.get("mount_source")
    ):
        raise StorageGroupError(
            "namespace_identity_changed",
            "The verified backend mount changed before namespace reconciliation.",
        )
    previous = group.namespace_path
    group.namespace_path = normalize_namespace(target)
    _event(
        session,
        group=group,
        backend=backend,
        principal=principal,
        event_type="storage_group_namespace_reconciled",
        previous_state=previous,
        resulting_state=group.namespace_path,
        reason="Unavailable placeholder replaced with the verified managed-storage mount.",
        details={"previous_namespace": previous, "backend_id": backend.id},
    )
    session.flush()
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
    # Discovery refreshes provider observations, but durable lifecycle links
    # (for example membership in a managed file-level pool) belong to the
    # registry and must survive the next hardware scan.
    disk.metadata_json = {
        **(disk.metadata_json or {}),
        **dict(observation.get("metadata") or {}),
    }
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
                    **{
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
                    "system_device": bool(
                        raw.get("system_device") is True or raw.get("system_disk") is True
                    ),
                },
            },
        )
        created += int(was_created)
        updated += int(not was_created)
    return {"observed": len(observed), "created": created, "updated": updated, "skipped": skipped}


def set_disk_reservation(
    session: Session,
    *,
    disk_id: str,
    action: str,
    protected_identities: set[str] | None = None,
) -> PhysicalDisk:
    """Reserve an unassigned disk for a future plan without touching the device."""

    disk = session.get(PhysicalDisk, disk_id)
    if disk is None:
        raise StorageGroupError("physical_disk_not_found", "The registered disk does not exist.")
    protected_identities = protected_identities or set()
    if disk.stable_identity in protected_identities:
        raise StorageGroupError(
            "system_disk_protected",
            "Protected system storage cannot be reserved for a storage change.",
        )
    if action == "reserve":
        if disk.lifecycle_state == "reserved":
            return disk
        if disk.lifecycle_state not in {"discovered", "reuse_ready"}:
            raise StorageGroupError(
                "disk_not_reservable",
                f"A disk in {disk.lifecycle_state.replace('_', ' ')} state cannot be reserved.",
            )
        disk.lifecycle_state = "reserved"
    elif action == "release":
        if disk.lifecycle_state != "reserved":
            raise StorageGroupError(
                "disk_not_reserved", "Only a reserved, unassigned disk can be released."
            )
        disk.lifecycle_state = "discovered"
    else:
        raise StorageGroupError("invalid_reservation_action", "Use reserve or release.")
    session.flush()
    return disk


def assign_backend(
    session: Session,
    *,
    group_id: str,
    physical_disk_id: str | None,
    storage_entity_id: str | None,
    namespace_path: str | None,
    role: str,
    principal: Principal,
    protected_identities: set[str] | None = None,
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
    protected_identities = protected_identities or set()
    if disk is not None and (
        disk.stable_identity in protected_identities
        or (disk.metadata_json or {}).get("system_device") is True
    ):
        raise StorageGroupError(
            "system_disk_protected",
            "Protected system storage cannot be assigned to a Storage Group.",
        )
    if disk is not None and disk.lifecycle_state not in {"discovered", "reuse_ready"}:
        raise StorageGroupError(
            "disk_not_assignable",
            f"A disk in {disk.lifecycle_state.replace('_', ' ')} state cannot be assigned.",
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


def begin_drain_placement(
    session: Session,
    *,
    group_id: str,
    source_backend_id: str,
    destination_backend_ids: list[str],
    operation_id: str,
    plan_sha256: str,
    principal: Principal,
) -> StorageBackend:
    """Atomically remove a drain source from all new-write placement.

    The mover calls this transaction boundary before it copies the first byte.
    Replaying the same operation is safe; another operation cannot adopt an
    already-draining backend.
    """

    group = session.get(StorageGroup, group_id)
    source = session.get(StorageBackend, source_backend_id)
    if group is None or source is None or source.storage_group_id != group.id:
        raise StorageGroupError("backend_not_found", "The drain source does not exist.")
    if not operation_id or len(operation_id) > 128:
        raise StorageGroupError("operation_id_invalid", "The drain operation ID is invalid.")
    if len(plan_sha256) != 64 or any(char not in "0123456789abcdef" for char in plan_sha256):
        raise StorageGroupError("drain_plan_invalid", "The immutable drain plan digest is invalid.")

    existing_drain = source.config_json.get("drain")
    if source.lifecycle_state == "draining":
        if isinstance(existing_drain, dict) and (
            existing_drain.get("operation_id") == operation_id
            and existing_drain.get("plan_sha256") == plan_sha256
        ):
            return source
        raise StorageGroupError(
            "drain_in_progress", "Another durable operation already owns this drain source."
        )
    if source.lifecycle_state not in {"active", "preferred_write"}:
        raise StorageGroupError(
            "source_state_invalid", "Only an active backend can begin draining."
        )
    if not destination_backend_ids or len(destination_backend_ids) > 64:
        raise StorageGroupError(
            "destination_required", "Select at least one bounded drain destination."
        )
    if len(set(destination_backend_ids)) != len(destination_backend_ids):
        raise StorageGroupError("destination_duplicate", "A drain destination was selected twice.")
    if source.id in destination_backend_ids:
        raise StorageGroupError(
            "source_destination_same", "The drain source cannot receive itself."
        )

    destinations: list[StorageBackend] = []
    for destination_id in destination_backend_ids:
        destination = session.get(StorageBackend, destination_id)
        if destination is None or destination.storage_group_id != group.id:
            raise StorageGroupError(
                "destination_backend_not_found", "A drain destination does not exist."
            )
        if destination.lifecycle_state not in {"active", "preferred_write"}:
            raise StorageGroupError(
                "destination_state_invalid", "Drain destinations must remain write-active."
            )
        if destination.role not in {"data", "archive"}:
            raise StorageGroupError(
                "destination_role_invalid",
                "Parity, cache, and landing backends cannot receive a drain.",
            )
        destinations.append(destination)

    previous_state = source.lifecycle_state
    drain_config = {
        **source.config_json,
        "drain": {
            "operation_id": operation_id,
            "plan_sha256": plan_sha256,
            "previous_state": previous_state,
            "destination_backend_ids": destination_backend_ids,
            "new_write_placement_removed": True,
        },
    }
    claimed = session.execute(
        update(StorageBackend)
        .where(
            StorageBackend.id == source.id,
            StorageBackend.lifecycle_state == previous_state,
        )
        .values(lifecycle_state="draining", config_json=drain_config)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        session.expire(source)
        raise StorageGroupError(
            "drain_in_progress", "Another durable operation changed this drain source first."
        )
    source.lifecycle_state = "draining"
    source.config_json = drain_config
    if source.physical_disk_id:
        source_disk = session.get(PhysicalDisk, source.physical_disk_id)
        if source_disk is not None:
            source_disk.lifecycle_state = "draining"

    # Demote the source before selecting its replacement so the partial unique
    # index can never observe two preferred writers, even within this transaction.
    session.flush()
    current_preferred = session.scalar(
        select(StorageBackend).where(
            StorageBackend.storage_group_id == group.id,
            StorageBackend.lifecycle_state == "preferred_write",
        )
    )
    replacement = current_preferred or destinations[0]
    if replacement.lifecycle_state != "preferred_write":
        replacement.lifecycle_state = "preferred_write"
        if replacement.physical_disk_id:
            replacement_disk = session.get(PhysicalDisk, replacement.physical_disk_id)
            if replacement_disk is not None:
                replacement_disk.lifecycle_state = "preferred_write"
        _event(
            session,
            group=group,
            backend=replacement,
            principal=principal,
            event_type="drain_destination_preferred",
            previous_state="active",
            resulting_state="preferred_write",
            details={"operation_id": operation_id, "source_backend_id": source.id},
        )

    _event(
        session,
        group=group,
        backend=source,
        principal=principal,
        event_type="backend_drain_started",
        previous_state=previous_state,
        resulting_state="draining",
        details={
            "operation_id": operation_id,
            "plan_sha256": plan_sha256,
            "destination_backend_ids": destination_backend_ids,
            "new_write_placement_removed": True,
        },
    )
    session.flush()
    return source


def advance_drain_lifecycle(
    session: Session,
    *,
    group_id: str,
    source_backend_id: str,
    operation_id: str,
    target_state: str,
    principal: Principal,
    details: dict[str, Any] | None = None,
) -> StorageBackend:
    """Advance only the drain-owned terminal sequence with ownership checks."""

    allowed = {
        "draining": "verifying",
        "verifying": "read_only",
        "read_only": "retired",
    }
    group = session.get(StorageGroup, group_id)
    source = session.get(StorageBackend, source_backend_id)
    if group is None or source is None or source.storage_group_id != group.id:
        raise StorageGroupError("backend_not_found", "The drain source does not exist.")
    drain = source.config_json.get("drain")
    if not isinstance(drain, dict) or drain.get("operation_id") != operation_id:
        raise StorageGroupError(
            "drain_operation_changed", "The drain source is owned by another operation."
        )
    if source.lifecycle_state == target_state:
        return source
    expected = allowed.get(source.lifecycle_state)
    if expected != target_state:
        raise StorageGroupError(
            "drain_phase_invalid",
            f"Cannot advance a drain from {source.lifecycle_state} to {target_state}.",
        )
    previous = source.lifecycle_state
    source.lifecycle_state = target_state
    source.config_json = {
        **source.config_json,
        "drain": {**drain, "phase": target_state, **(details or {})},
    }
    if source.physical_disk_id:
        disk = session.get(PhysicalDisk, source.physical_disk_id)
        if disk is not None:
            disk.lifecycle_state = target_state
    _event(
        session,
        group=group,
        backend=source,
        principal=principal,
        event_type=f"backend_{target_state}",
        previous_state=previous,
        resulting_state=target_state,
        details={"operation_id": operation_id, **(details or {})},
    )
    session.flush()
    return source


def release_retired_backend(
    session: Session,
    *,
    group_id: str,
    backend_id: str,
    principal: Principal,
    reason: str | None,
) -> tuple[StorageBackend, PhysicalDisk]:
    """Release a verified retired disk from a group without touching its contents.

    The historical backend row and lifecycle events remain durable.  Its unique
    hardware association is detached so the physical disk can be assigned again;
    no mount, partition, filesystem, or wipe operation occurs here.
    """

    group = session.get(StorageGroup, group_id)
    backend = session.get(StorageBackend, backend_id)
    if group is None or backend is None or backend.storage_group_id != group.id:
        raise StorageGroupError("backend_not_found", "The retired backend does not exist.")
    if backend.lifecycle_state != "retired":
        raise StorageGroupError(
            "backend_not_retired",
            "Only a backend retired by a completed verified drain can be released for reuse.",
        )
    drain = backend.config_json.get("drain")
    if not isinstance(drain, dict) or drain.get("phase") != "retired":
        raise StorageGroupError(
            "retirement_not_verified",
            "The backend does not contain completed drain verification evidence.",
        )
    if backend.physical_disk_id is None:
        raise StorageGroupError(
            "physical_disk_not_found", "The retired backend is not attached to a physical disk."
        )
    disk = session.get(PhysicalDisk, backend.physical_disk_id)
    if disk is None:
        raise StorageGroupError(
            "physical_disk_not_found", "The retired physical disk is no longer registered."
        )

    previous_identity = backend.stable_identity
    released_at = datetime.now(UTC).isoformat()
    _event(
        session,
        group=group,
        backend=backend,
        principal=principal,
        event_type="backend_released_for_reuse",
        previous_state="retired",
        resulting_state="reuse_ready",
        reason=reason,
        details={
            "stable_identity": previous_identity,
            "released_at": released_at,
            "device_contents_changed": False,
        },
    )
    backend.config_json = {
        **backend.config_json,
        "release": {
            "stable_identity": previous_identity,
            "physical_disk_id": disk.id,
            "released_at": released_at,
            "device_contents_changed": False,
        },
    }
    # Keep the historical row without retaining either uniqueness claim.  The
    # physical registry remains the source of truth for the reusable identity.
    backend.stable_identity = f"released:{backend.id}"
    backend.physical_disk_id = None
    backend.lifecycle_state = "reuse_ready"
    disk.lifecycle_state = "reuse_ready"
    session.flush()
    return backend, disk


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
                "namespace": _namespace_availability(group, backends),
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
                    if not (
                        item.lifecycle_state == "reuse_ready"
                        and item.physical_disk_id is None
                        and item.storage_entity_id is None
                    )
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


def disk_documents(
    session: Session,
    *,
    protected_identities: set[str] | None = None,
    assignment_evidence_available: bool = True,
) -> list[dict[str, Any]]:
    protected_identities = protected_identities or set()
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
            "system_device": bool(
                disk.stable_identity in protected_identities
                or (disk.metadata_json or {}).get("system_device") is True
            ),
            "assignable": bool(
                assignment_evidence_available
                and disk.lifecycle_state in {"discovered", "reuse_ready"}
                and disk.stable_identity not in protected_identities
                and (disk.metadata_json or {}).get("system_device") is not True
            ),
            "last_seen_at": disk.last_seen_at.isoformat(),
        }
        for disk in session.scalars(select(PhysicalDisk).order_by(PhysicalDisk.stable_identity))
    ]
