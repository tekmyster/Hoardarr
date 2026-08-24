from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.api.dependencies import authenticated_principal, database_session
from hoardarr.api.serializers import operation_document
from hoardarr.auth.service import Principal
from hoardarr.db.models import HardwareSnapshot, Operation, PhysicalDisk, utc_now
from hoardarr.storage.groups import group_documents
from hoardarr.system.overview import summarize_storage

router = APIRouter(prefix="/integrations/home-assistant", tags=["automation"])


@router.get("/summary")
def home_assistant_summary(
    request: Request,
    principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    """Return a bounded, versioned, read-only home-automation document."""

    snapshot = session.scalar(
        select(HardwareSnapshot).order_by(HardwareSnapshot.captured_at.desc()).limit(1)
    )
    storage = summarize_storage(snapshot.payload_json if snapshot is not None else None)
    groups = group_documents(session)
    disks = list(session.scalars(select(PhysicalDisk).order_by(PhysicalDisk.id).limit(256)))
    query = select(Operation).order_by(Operation.created_at.desc()).limit(25)
    if not principal.is_admin:
        query = query.where(Operation.actor_id == principal.user_id)
    operations = list(session.scalars(query))
    health_counts = Counter(item.health_state for item in disks)
    operation_counts = Counter(item.status for item in operations)
    critical = health_counts["critical"]
    warning = health_counts["warning"] + operation_counts["needs_attention"]
    overall = "critical" if critical else "warning" if warning else "healthy"
    return {
        "schema_version": 1,
        "captured_at": utc_now(),
        "source": "hoardarr_persisted_state",
        "application": {
            "name": "Hoardarr",
            "version": request.app.version,
            "database_ready": request.app.state.database_ready,
        },
        "health": {
            "state": overall,
            "critical_drives": critical,
            "warning_drives": health_counts["warning"],
            "operations_needing_attention": operation_counts["needs_attention"],
            "failed_operations_in_recent_window": operation_counts["failed"],
        },
        "alerts": [
            *[
                {
                    "kind": "drive_health",
                    "severity": disk.health_state,
                    "entity_type": "drive",
                    "entity_id": disk.id,
                    "state": "active",
                }
                for disk in disks
                if disk.health_state in {"warning", "critical"}
            ],
            *[
                {
                    "kind": "operation",
                    "severity": "warning",
                    "entity_type": "operation",
                    "entity_id": operation.id,
                    "state": "active",
                    "operation_kind": operation.kind,
                    "operation_status": operation.status,
                }
                for operation in operations
                if operation.status in {"failed", "needs_attention"}
            ],
        ][:50],
        "storage": {
            "detected_drive_count": storage.get("drive_count", 0),
            "raw_capacity_bytes": storage.get("raw_capacity_bytes", 0),
            "health": storage.get("health", {}),
            "latest_hardware_observation": (
                {"captured_at": snapshot.captured_at, "source": snapshot.source}
                if snapshot is not None
                else None
            ),
            "groups": [
                {
                    "id": group["id"],
                    "name": group["name"],
                    "namespace_path": group["namespace_path"],
                    "purpose": group["purpose"],
                    "state": group["state"],
                    "backend_states": dict(
                        Counter(item["lifecycle_state"] for item in group["backends"])
                    ),
                }
                for group in groups
            ],
            "drives": [
                {
                    "id": disk.id,
                    "stable_identity": disk.stable_identity,
                    "vendor": disk.vendor,
                    "model": disk.model,
                    "capacity_bytes": disk.capacity_bytes,
                    "media_type": disk.media_type,
                    "health_state": disk.health_state,
                    "lifecycle_state": disk.lifecycle_state,
                    "last_seen_at": disk.last_seen_at,
                }
                for disk in disks
            ],
        },
        "jobs": {
            "counts": dict(operation_counts),
            "recent": [operation_document(item) for item in operations],
            "limit": 25,
        },
        "maintenance": {
            "active": bool(operation_counts["queued"] or operation_counts["running"]),
            "queued": operation_counts["queued"],
            "running": operation_counts["running"],
        },
    }
