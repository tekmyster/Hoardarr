from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.db.models import Operation, Plan

ACTIVE_STORAGE_STATUSES = ("queued", "running")


def plan_selected_device_ids(plan: Plan) -> list[str]:
    storage = plan.document_json.get("storage")
    binding = storage.get("snapshot_binding") if isinstance(storage, dict) else None
    selected = binding.get("selected_device_ids") if isinstance(binding, dict) else None
    if not isinstance(selected, list):
        return []
    return [item for item in selected if isinstance(item, str)]


def active_storage_reservations(
    session: Session, *, exclude_operation_id: str | None = None
) -> list[dict[str, Any]]:
    operations = list(
        session.scalars(
            select(Operation)
            .where(
                Operation.kind.in_(
                    ("storage.apply", "storage.maintenance", "storage.snapraid.replace")
                ),
                Operation.status.in_(ACTIVE_STORAGE_STATUSES),
            )
            .order_by(Operation.created_at)
        )
    )
    reservations: list[dict[str, Any]] = []
    for operation in operations:
        if operation.id == exclude_operation_id:
            continue
        if operation.kind == "storage.apply":
            plan_id = operation.request_json.get("plan_id")
            plan = session.get(Plan, plan_id) if isinstance(plan_id, str) else None
            selected = plan_selected_device_ids(plan) if plan is not None else []
        elif operation.resource_type == "drive" and isinstance(operation.resource_id, str):
            selected = [operation.resource_id]
        else:
            selected = []
        if not selected:
            continue
        reservations.append(
            {
                "operation_id": operation.id,
                "status": operation.status,
                "selected_device_ids": selected,
                "created_at": operation.created_at.isoformat(),
                "updated_at": operation.updated_at.isoformat(),
            }
        )
    return reservations


def reserved_device_ids(session: Session, *, exclude_operation_id: str | None = None) -> set[str]:
    return {
        device_id
        for reservation in active_storage_reservations(
            session, exclude_operation_id=exclude_operation_id
        )
        for device_id in reservation["selected_device_ids"]
    }
