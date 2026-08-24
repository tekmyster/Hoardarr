from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.auth.service import Principal
from hoardarr.db.models import (
    Operation,
    StorageVolume,
    StorageVolumeSnapshot,
    StorageVolumeSnapshotSchedule,
    utc_now,
)
from hoardarr.operations.service import create_operation, document_hash
from hoardarr.storage.snapshot_plans import build_snapshot_plan
from hoardarr.storage.volumes import register_volume, volume_document

SNAPSHOT_SCHEDULER_PRINCIPAL = Principal(
    user_id="system-snapshot-scheduler",
    username="snapshot-scheduler",
    is_admin=True,
    auth_type="worker",
    scopes=frozenset({"read", "operate"}),
)
_PREFIX = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class SnapshotLifecycleError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def provider_guid(volume: StorageVolume) -> str:
    configured = volume.config_json.get("provider_guid") if volume.config_json else None
    value = configured or volume.filesystem_uuid
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+", value) is None:
        raise SnapshotLifecycleError(
            "snapshot_provider_identity_unavailable",
            "The live provider identity is unavailable; no snapshot action can be planned.",
        )
    return value


def require_snapshot_capability(volume: StorageVolume) -> None:
    capability = volume.capabilities_json.get("snapshot", {})
    if capability.get("support") != "supported":
        raise SnapshotLifecycleError(
            "snapshot_provider_unsupported", "This provider does not support snapshots."
        )
    if capability.get("availability") != "available":
        raise SnapshotLifecycleError(
            "snapshot_capability_unavailable",
            "Snapshot capability is not currently available from the provider.",
        )


def snapshot_document(item: StorageVolumeSnapshot) -> dict[str, object]:
    return {
        "id": item.id,
        "volume_id": item.volume_id,
        "provider_snapshot_id": item.provider_snapshot_id,
        "snapshot_name": item.snapshot_name,
        "provider_guid": item.provider_guid,
        "state": item.state,
        "detail": dict(item.detail_json),
        "restored_at": item.restored_at.isoformat() if item.restored_at else None,
        "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def snapshot_documents(session: Session, volume_id: str) -> list[dict[str, object]]:
    items = session.scalars(
        select(StorageVolumeSnapshot)
        .where(StorageVolumeSnapshot.volume_id == volume_id)
        .order_by(StorageVolumeSnapshot.created_at.desc())
        .limit(1024)
    )
    return [snapshot_document(item) for item in items]


def schedule_document(item: StorageVolumeSnapshotSchedule | None) -> dict[str, object]:
    if item is None:
        return {
            "enabled": False,
            "interval_hours": 24,
            "retention_count": 12,
            "prefix": "hoardarr-auto",
            "next_run_at": None,
            "last_run_at": None,
        }
    return {
        "enabled": item.enabled,
        "interval_hours": item.interval_hours,
        "retention_count": item.retention_count,
        "prefix": item.prefix,
        "next_run_at": item.next_run_at.isoformat() if item.next_run_at else None,
        "last_run_at": item.last_run_at.isoformat() if item.last_run_at else None,
    }


def configure_schedule(
    session: Session,
    volume: StorageVolume,
    *,
    enabled: bool,
    interval_hours: int,
    retention_count: int,
    prefix: str,
    now: datetime | None = None,
) -> StorageVolumeSnapshotSchedule:
    require_snapshot_capability(volume)
    prefix = prefix.strip().lower()
    if not _PREFIX.fullmatch(prefix):
        raise SnapshotLifecycleError(
            "snapshot_schedule_prefix_invalid", "The snapshot prefix is invalid."
        )
    if not 1 <= interval_hours <= 8760 or not 1 <= retention_count <= 1024:
        raise SnapshotLifecycleError(
            "snapshot_schedule_invalid", "Snapshot interval or retention is outside safe bounds."
        )
    current = now or utc_now()
    item = session.scalar(
        select(StorageVolumeSnapshotSchedule).where(
            StorageVolumeSnapshotSchedule.volume_id == volume.id
        )
    )
    if item is None:
        item = StorageVolumeSnapshotSchedule(volume_id=volume.id)
        session.add(item)
    item.enabled = enabled
    item.interval_hours = interval_hours
    item.retention_count = retention_count
    item.prefix = prefix
    item.next_run_at = current + timedelta(hours=interval_hours) if enabled else None
    session.flush()
    return item


def apply_snapshot_result(
    session: Session,
    *,
    operation: Operation,
    plan: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    volume = session.get(StorageVolume, str(plan["volume"]["id"]))
    if volume is None or volume.stable_identity != plan["volume"]["stable_identity"]:
        raise SnapshotLifecycleError(
            "snapshot_volume_identity_changed", "The canonical volume identity changed."
        )
    action = str(plan["action"])
    snapshot_result = result.get("snapshot")
    if not isinstance(snapshot_result, dict):
        raise SnapshotLifecycleError(
            "snapshot_result_invalid", "The snapshot provider result is invalid."
        )
    provider_snapshot_id = str(snapshot_result.get("provider_snapshot_id") or "")
    item = session.scalar(
        select(StorageVolumeSnapshot).where(
            StorageVolumeSnapshot.provider_snapshot_id == provider_snapshot_id
        )
    )
    current = utc_now()
    if action == "create":
        if item is None:
            item = StorageVolumeSnapshot(
                volume_id=volume.id,
                provider_snapshot_id=provider_snapshot_id,
                snapshot_name=str(snapshot_result["snapshot_name"]),
            )
            session.add(item)
        item.provider_guid = str(snapshot_result["provider_guid"])
        item.state = "available"
        item.created_by_operation_id = operation.id
        item.detail_json = dict(snapshot_result.get("detail") or {})
        item.deleted_at = None
    elif item is None or item.volume_id != volume.id:
        raise SnapshotLifecycleError(
            "snapshot_result_invalid", "The snapshot result does not match a managed snapshot."
        )
    elif action == "delete":
        item.state = "deleted"
        item.deleted_at = current
    elif action == "restore":
        item.restored_at = current
    if action == "clone":
        clone = result.get("clone_volume")
        if not isinstance(clone, dict):
            raise SnapshotLifecycleError("snapshot_result_invalid", "Clone verification is absent.")
        registered, _created = register_volume(session, clone)
        result = {**result, "clone_volume_id": registered.id}
    if plan.get("scheduled") is True and action == "create":
        # Sessions used by both the worker and deterministic maintenance tests may
        # disable autoflush.  Persist the new snapshot before selecting the
        # bounded retention window so the just-created point participates in it.
        session.flush()
        schedule = session.scalar(
            select(StorageVolumeSnapshotSchedule).where(
                StorageVolumeSnapshotSchedule.volume_id == volume.id
            )
        )
        if schedule is not None:
            schedule.last_run_at = current
            managed = list(
                session.scalars(
                    select(StorageVolumeSnapshot)
                    .where(
                        StorageVolumeSnapshot.volume_id == volume.id,
                        StorageVolumeSnapshot.state == "available",
                        StorageVolumeSnapshot.snapshot_name.startswith(f"{schedule.prefix}-"),
                    )
                    .order_by(StorageVolumeSnapshot.created_at.desc())
                    .limit(1024)
                )
            )
            for expired in managed[schedule.retention_count :]:
                delete_plan = build_snapshot_plan(
                    volume=volume_document(volume),
                    provider_guid=provider_guid(volume),
                    action="delete",
                    snapshot=snapshot_document(expired),
                    scheduled=True,
                )
                create_operation(
                    session,
                    kind="storage.volume.snapshot",
                    principal=SNAPSHOT_SCHEDULER_PRINCIPAL,
                    request={
                        "plan": delete_plan,
                        "plan_sha256": delete_plan["plan_sha256"],
                        "confirmation_sha256": document_hash(
                            {"confirmation": delete_plan["confirmation"]}
                        ),
                    },
                    idempotency_key=f"retention:{expired.id}",
                    resource_type="storage_volume",
                    resource_id=volume.stable_identity,
                )
    session.flush()
    return {**result, "snapshot_id": item.id}


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def queue_due_snapshots(session: Session, *, now: datetime | None = None) -> int:
    current = now or utc_now()
    queued = 0
    schedules = session.scalars(
        select(StorageVolumeSnapshotSchedule).where(
            StorageVolumeSnapshotSchedule.enabled.is_(True),
            StorageVolumeSnapshotSchedule.next_run_at <= current,
        )
    )
    for schedule in schedules:
        volume = session.get(StorageVolume, schedule.volume_id)
        if volume is None:
            schedule.enabled = False
            schedule.next_run_at = None
            continue
        active = session.scalar(
            select(Operation.id).where(
                Operation.kind == "storage.volume.snapshot",
                Operation.resource_type == "storage_volume",
                Operation.resource_id == volume.stable_identity,
                Operation.status.in_(("queued", "running")),
            )
        )
        if active is not None:
            continue
        try:
            require_snapshot_capability(volume)
            guid = provider_guid(volume)
        except SnapshotLifecycleError:
            continue
        stamp = current.astimezone(UTC).strftime("%Y%m%dt%H%M%Sz")
        plan = build_snapshot_plan(
            volume=volume_document(volume),
            provider_guid=guid,
            action="create",
            snapshot_name=f"{schedule.prefix}-{stamp}",
            scheduled=True,
        )
        bucket = int(current.timestamp() // (schedule.interval_hours * 3600))
        _operation, created = create_operation(
            session,
            kind="storage.volume.snapshot",
            principal=SNAPSHOT_SCHEDULER_PRINCIPAL,
            request={
                "plan": plan,
                "plan_sha256": plan["plan_sha256"],
                "confirmation_sha256": document_hash(
                    {"confirmation": plan["confirmation"]}
                ),
            },
            idempotency_key=f"scheduled:{volume.id}:{bucket}",
            resource_type="storage_volume",
            resource_id=volume.stable_identity,
        )
        if created:
            schedule.next_run_at = current + timedelta(hours=schedule.interval_hours)
            queued += 1
    return queued
