from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.auth.service import Principal
from hoardarr.backups.service import target_fingerprint
from hoardarr.db.models import Operation, RemoteBackupRun, RemoteBackupTarget, utc_now
from hoardarr.operations.service import create_operation

SCHEDULER_PRINCIPAL = Principal(
    user_id="system-backup-scheduler",
    username="backup-scheduler",
    is_admin=True,
    auth_type="worker",
    scopes=frozenset({"read", "operate"}),
)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def queue_due_control_plane_backups(session: Session, *, now: datetime | None = None) -> int:
    """Queue each due target once; durable operations provide concurrency and retry safety."""

    current = now or utc_now()
    queued = 0
    targets = session.scalars(
        select(RemoteBackupTarget).where(RemoteBackupTarget.enabled.is_(True))
    )
    for target in targets:
        schedule = target.schedule_json if isinstance(target.schedule_json, dict) else {}
        if schedule.get("enabled") is not True or target.status not in {"available", "degraded"}:
            continue
        interval_hours = schedule.get("interval_hours")
        if not isinstance(interval_hours, int) or not 1 <= interval_hours <= 720:
            continue
        active = session.scalar(
            select(Operation.id).where(
                Operation.kind == "backup.control_plane",
                Operation.resource_type == "remote_backup_target",
                Operation.resource_id == target.id,
                Operation.status.in_(("queued", "running")),
            )
        )
        if active is not None:
            continue
        latest_run_at = session.scalar(
            select(RemoteBackupRun.created_at)
            .where(RemoteBackupRun.target_id == target.id)
            .order_by(RemoteBackupRun.created_at.desc())
            .limit(1)
        )
        reference = (
            latest_run_at or target.last_success_at or target.updated_at or target.created_at
        )
        if reference is not None and _aware(reference) + timedelta(hours=interval_hours) > current:
            continue
        bucket = int(current.timestamp() // (interval_hours * 3600))
        request = {
            "target_id": target.id,
            "target_fingerprint": target_fingerprint(target),
            "backup_kind": "control_plane",
            "secrets_included": False,
            "scheduled": True,
        }
        operation, created = create_operation(
            session,
            kind="backup.control_plane",
            principal=SCHEDULER_PRINCIPAL,
            request=request,
            idempotency_key=f"scheduled:{target.id}:{bucket}",
            resource_type="remote_backup_target",
            resource_id=target.id,
        )
        if created:
            session.add(
                RemoteBackupRun(
                    id=operation.id,
                    target_id=target.id,
                    backup_kind="control_plane",
                )
            )
            session.flush()
            queued += 1
    return queued
