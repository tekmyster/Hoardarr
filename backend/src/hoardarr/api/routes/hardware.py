from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.api.dependencies import (
    authenticated_principal,
    database_session,
    idempotency_key,
    require_state_scope,
)
from hoardarr.api.problem import Problem
from hoardarr.api.serializers import operation_document, snapshot_document
from hoardarr.audit.service import record_audit
from hoardarr.auth.service import Principal
from hoardarr.db.models import HardwareSnapshot
from hoardarr.operations.service import OperationConflict, create_operation

router = APIRouter(prefix="/hardware", tags=["hardware"])


@router.post("/scans", status_code=202)
def scan_hardware(
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        operation, created = create_operation(
            session,
            kind="hardware.scan",
            principal=principal,
            request={"schema_version": 1},
            idempotency_key=key,
            resource_type="hardware_snapshot",
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        record_audit(
            session,
            principal=principal,
            action="hardware.scan.queue",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="operation",
            target_id=operation.id,
        )
    return {"operation": operation_document(operation), "replayed": not created}


@router.get("/snapshots")
def list_snapshots(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    snapshots = session.scalars(
        select(HardwareSnapshot).order_by(HardwareSnapshot.captured_at.desc()).limit(100)
    )
    return {"items": [snapshot_document(item, include_payload=False) for item in snapshots]}


@router.get("/snapshots/latest")
def get_latest_snapshot(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    snapshot = session.scalar(
        select(HardwareSnapshot).order_by(HardwareSnapshot.captured_at.desc()).limit(1)
    )
    if snapshot is None:
        raise Problem(404, "snapshot_not_found", "Not found", "No hardware snapshot is available.")
    return snapshot_document(snapshot, include_payload=True)


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(
    snapshot_id: str,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    snapshot = session.get(HardwareSnapshot, snapshot_id)
    if snapshot is None:
        raise Problem(404, "snapshot_not_found", "Not found", "Hardware snapshot was not found.")
    return snapshot_document(snapshot, include_payload=True)
