from __future__ import annotations

from datetime import timedelta

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
from hoardarr.api.schemas import (
    HardwareLocateRequest,
    TopologyExpectationCreateRequest,
    TopologyExpectationRemoveRequest,
)
from hoardarr.api.serializers import operation_document, snapshot_document
from hoardarr.audit.service import record_audit
from hoardarr.auth.service import Principal
from hoardarr.db.models import HardwareSnapshot, TopologyDriftEvent, TopologyExpectation, utc_now
from hoardarr.hardware.locate import LocateError, build_locate_plan
from hoardarr.hardware.topology_expectations import (
    create_topology_expectation,
    drift_document,
    expectation_document,
    reconcile_topology_snapshot,
)
from hoardarr.operations.service import OperationConflict, create_operation, document_hash

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


@router.post("/locate", status_code=202)
def locate_hardware(
    payload: HardwareLocateRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    snapshot = session.scalar(
        select(HardwareSnapshot).order_by(HardwareSnapshot.captured_at.desc()).limit(1)
    )
    if snapshot is None:
        raise Problem(
            409, "hardware_snapshot_required", "Discovery required", "Run discovery first."
        )
    try:
        plan = build_locate_plan(
            snapshot.payload_json, device_id=payload.device_id, enabled=payload.enabled
        )
    except LocateError as exc:
        raise Problem(422, exc.code, "Locate unavailable", str(exc)) from exc
    request_document = {
        "plan": plan,
        "plan_sha256": document_hash(plan),
        "duration_seconds": payload.duration_seconds,
    }
    try:
        operation, created = create_operation(
            session,
            kind="hardware.locate",
            principal=principal,
            request=request_document,
            idempotency_key=key,
            resource_type="drive",
            resource_id=payload.device_id,
        )
        automatic_clear = None
        if payload.enabled:
            clear_plan = {**plan, "enabled": False}
            clear_plan["binding_sha256"] = plan["binding_sha256"]
            clear_request = {
                "plan": clear_plan,
                "plan_sha256": document_hash(clear_plan),
                "automatic_clear": True,
                "duration_seconds": payload.duration_seconds,
            }
            clear_key = f"{key[:80]}:clear:{clear_request['plan_sha256'][:16]}"
            automatic_clear, _clear_created = create_operation(
                session,
                kind="hardware.locate",
                principal=principal,
                request=clear_request,
                idempotency_key=clear_key,
                resource_type="drive",
                resource_id=payload.device_id,
                not_before=utc_now() + timedelta(seconds=payload.duration_seconds),
            )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        record_audit(
            session,
            principal=principal,
            action="hardware.locate.queue",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="drive",
            target_id=payload.device_id,
            details={"enabled": payload.enabled, "duration_seconds": payload.duration_seconds},
        )
    return {
        "operation": operation_document(operation),
        "automatic_clear": operation_document(automatic_clear) if automatic_clear else None,
        "replayed": not created,
    }


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


@router.get("/topology/expectation")
def get_topology_expectation(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    expectation = session.scalar(
        select(TopologyExpectation)
        .where(TopologyExpectation.active.is_(True))
        .order_by(TopologyExpectation.updated_at.desc())
        .limit(1)
    )
    if expectation is None:
        return {"expectation": None, "active_drifts": [], "recent_events": []}
    events = list(
        session.scalars(
            select(TopologyDriftEvent)
            .where(TopologyDriftEvent.expectation_id == expectation.id)
            .order_by(TopologyDriftEvent.last_seen_at.desc())
            .limit(100)
        )
    )
    return {
        "expectation": expectation_document(expectation),
        "active_drifts": [drift_document(event) for event in events if event.state == "active"],
        "recent_events": [drift_document(event) for event in events],
    }


@router.post("/topology/expectations", status_code=201)
def save_topology_expectation(
    payload: TopologyExpectationCreateRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    snapshot = session.get(HardwareSnapshot, payload.snapshot_id)
    if snapshot is None:
        raise Problem(404, "snapshot_not_found", "Not found", "Hardware snapshot was not found.")
    expectation = create_topology_expectation(
        session,
        snapshot=snapshot,
        name=payload.name,
        created_by=principal.user_id,
    )
    result = reconcile_topology_snapshot(session, snapshot)
    record_audit(
        session,
        principal=principal,
        action="hardware.topology.expectation.save",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="topology_expectation",
        target_id=expectation.id,
        details={"snapshot_id": snapshot.id, **result},
    )
    return {"expectation": expectation_document(expectation), "reconciliation": result}


@router.delete("/topology/expectations/{expectation_id}")
def remove_topology_expectation(
    expectation_id: str,
    payload: TopologyExpectationRemoveRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    expectation = session.get(TopologyExpectation, expectation_id)
    if expectation is None or not expectation.active:
        raise Problem(
            404,
            "expectation_not_found",
            "Not found",
            "Active topology expectation was not found.",
        )
    now = utc_now()
    expectation.active = False
    expectation.updated_at = now
    active_events = session.scalars(
        select(TopologyDriftEvent).where(
            TopologyDriftEvent.expectation_id == expectation.id,
            TopologyDriftEvent.state == "active",
        )
    )
    for event in active_events:
        event.state = "resolved"
        event.last_seen_at = now
        event.resolved_at = now
    record_audit(
        session,
        principal=principal,
        action="hardware.topology.expectation.remove",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="topology_expectation",
        target_id=expectation.id,
    )
    return {"removed": True}
