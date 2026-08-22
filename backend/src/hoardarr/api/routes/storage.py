from __future__ import annotations

from pathlib import Path

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
    DeviceMaintenanceApplyRequest,
    DeviceMaintenancePreviewRequest,
    SnapraidReplacementApplyRequest,
    SnapraidReplacementPreviewRequest,
    StorageRedundancyApplyRequest,
    StorageRedundancyPreviewRequest,
    TierTransferApplyRequest,
    TierTransferCleanupRequest,
    TierTransferPreviewRequest,
)
from hoardarr.api.serializers import operation_document
from hoardarr.audit.service import record_audit
from hoardarr.auth.service import Principal
from hoardarr.db.models import HardwareSnapshot, Operation
from hoardarr.operations.service import OperationConflict, create_operation, document_hash
from hoardarr.storage.inventory import discover_storage_inventory
from hoardarr.storage.maintenance import MaintenanceError, build_plan, validate_plan
from hoardarr.storage.mergerfs import discover_mergerfs
from hoardarr.storage.redundancy import (
    RedundancyError,
    build_redundancy_plan,
    storage_documents,
    validate_redundancy_plan,
)
from hoardarr.storage.reservations import active_storage_reservations, reserved_device_ids
from hoardarr.storage.snapraid import (
    SnapraidReplacementError,
    build_replacement_plan,
    validate_replacement_plan,
)
from hoardarr.storage.telemetry import storage_telemetry
from hoardarr.storage.tiering import TieringError, plan_transfer

router = APIRouter(prefix="/storage", tags=["storage"])


def _latest_hardware(session: Session) -> HardwareSnapshot:
    snapshot = session.scalar(
        select(HardwareSnapshot).order_by(HardwareSnapshot.captured_at.desc()).limit(1)
    )
    if snapshot is None:
        raise Problem(
            409, "hardware_snapshot_required", "Discovery required", "Run discovery first."
        )
    return snapshot


@router.get("/mergerfs")
def mergerfs_inventory(
    _principal: Principal = Depends(authenticated_principal),
) -> dict[str, object]:
    """Return live mergerFS instances without inventing configured storage."""

    return discover_mergerfs()


@router.get("/inventory")
def storage_inventory(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    """Return live storage plus drives reserved by active backend operations."""

    snapshot = session.scalar(
        select(HardwareSnapshot).order_by(HardwareSnapshot.captured_at.desc()).limit(1)
    )
    return {
        **discover_storage_inventory(
            hardware_snapshot=snapshot.payload_json if snapshot is not None else None
        ),
        "active_operations": active_storage_reservations(session),
    }


@router.get("/logical")
def logical_storage_inventory(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    snapshot = session.scalar(
        select(HardwareSnapshot).order_by(HardwareSnapshot.captured_at.desc()).limit(1)
    )
    return {
        "items": storage_documents(session, snapshot.payload_json if snapshot is not None else None)
    }


@router.post("/redundancy/preview")
def preview_storage_redundancy(
    payload: StorageRedundancyPreviewRequest,
    _principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    snapshot = _latest_hardware(session)
    try:
        plan = build_redundancy_plan(
            session,
            storage_entity_id=payload.storage_entity_id,
            hardware_snapshot_sha256=snapshot.sha256,
            hardware_snapshot=snapshot.payload_json,
            action=payload.action,
            candidate_path_identity=payload.path_identity,
            remove_path_identity=payload.remove_path_identity,
            policy=payload.policy,
        )
    except RedundancyError as exc:
        raise Problem(422, exc.code, "Redundancy unavailable", str(exc)) from exc
    return {"plan": plan, "plan_sha256": plan["plan_sha256"]}


@router.post("/redundancy", status_code=202)
def apply_storage_redundancy_plan(
    payload: StorageRedundancyApplyRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        plan = validate_redundancy_plan(payload.plan)
    except RedundancyError as exc:
        raise Problem(422, exc.code, "Invalid redundancy plan", str(exc)) from exc
    if payload.plan_sha256 != plan["plan_sha256"]:
        raise Problem(409, "redundancy_plan_changed", "Plan changed", "Preview the change again.")
    if _latest_hardware(session).sha256 != plan["hardware_snapshot_sha256"]:
        raise Problem(
            409,
            "hardware_snapshot_changed",
            "Discovery changed",
            "Run discovery and review the change again.",
        )
    try:
        operation, created = create_operation(
            session,
            kind="storage.redundancy.apply",
            principal=principal,
            request={
                "plan": plan,
                "plan_sha256": plan["plan_sha256"],
                "confirmation_sha256": document_hash({"confirmation": "APPLY"}),
            },
            idempotency_key=key,
            resource_type="storage_entity",
            resource_id=str(plan["storage_entity_id"]),
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        conflict = session.scalar(
            select(Operation).where(
                Operation.id != operation.id,
                Operation.status.in_(("queued", "running")),
                Operation.resource_type == "storage_entity",
                Operation.resource_id == str(plan["storage_entity_id"]),
            )
        )
        if conflict is not None:
            raise Problem(
                409,
                "storage_entity_reserved",
                "Storage is busy",
                "Another controller-path change is already queued or running.",
            )
        record_audit(
            session,
            principal=principal,
            action=str(plan["operation"]),
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="storage_entity",
            target_id=str(plan["storage_entity_id"]),
            details={
                "plan_sha256": plan["plan_sha256"],
                "path": plan["selected_path"]["stable_path_identity"],
            },
        )
    return {"operation": operation_document(operation), "replayed": not created}


@router.get("/telemetry")
def storage_performance(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    """Return live block I/O, daily writes, and reported SSD endurance."""

    snapshot = session.scalar(
        select(HardwareSnapshot).order_by(HardwareSnapshot.captured_at.desc()).limit(1)
    )
    inventory = discover_storage_inventory(
        hardware_snapshot=snapshot.payload_json if snapshot is not None else None
    )
    return storage_telemetry.sample(
        hardware_snapshot=snapshot.payload_json if snapshot is not None else None,
        pools=inventory["pools"]["items"],
    )


@router.post("/maintenance/preview")
def preview_maintenance(
    payload: DeviceMaintenancePreviewRequest,
    _principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    snapshot = _latest_hardware(session)
    disks = snapshot.payload_json.get("disks")
    matches = (
        [item for item in disks if isinstance(item, dict) and item.get("id") == payload.device_id]
        if isinstance(disks, list)
        else []
    )
    if len(matches) != 1:
        raise Problem(404, "drive_not_found", "Drive not found", "Run discovery and try again.")
    try:
        plan = build_plan(
            disk=matches[0],
            hardware_snapshot_sha256=snapshot.sha256,
            action=payload.action,
            method=payload.method,
            passes=payload.passes,
            target_logical_bytes=payload.target_logical_bytes,
        )
    except MaintenanceError as exc:
        raise Problem(422, exc.code, "Maintenance unavailable", str(exc)) from exc
    return {"plan": plan, "plan_sha256": document_hash(plan)}


@router.post("/maintenance", status_code=202)
def apply_maintenance(
    payload: DeviceMaintenanceApplyRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    if document_hash(payload.plan) != payload.plan_sha256:
        raise Problem(409, "maintenance_plan_changed", "Plan changed", "Preview the action again.")
    try:
        plan = validate_plan(payload.plan)
    except MaintenanceError as exc:
        raise Problem(422, exc.code, "Invalid maintenance plan", str(exc)) from exc
    snapshot = _latest_hardware(session)
    if snapshot.sha256 != plan["hardware_snapshot_sha256"]:
        raise Problem(
            409, "hardware_snapshot_changed", "Discovery changed", "Preview the action again."
        )
    try:
        operation, created = create_operation(
            session,
            kind="storage.maintenance",
            principal=principal,
            request={
                "plan": plan,
                "plan_sha256": payload.plan_sha256,
                "confirmation_sha256": document_hash({"confirmation": "I AGREE"}),
            },
            idempotency_key=key,
            resource_type="drive",
            resource_id=str(plan["device"]["id"]),
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        if str(plan["device"]["id"]) in reserved_device_ids(
            session, exclude_operation_id=operation.id
        ):
            raise Problem(
                409,
                "drive_reserved",
                "Drive is busy",
                "Another storage operation uses this drive.",
            )
        record_audit(
            session,
            principal=principal,
            action="storage.maintenance",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="drive",
            target_id=str(plan["device"]["id"]),
            details={"action": plan["action"], "plan_sha256": payload.plan_sha256},
        )
    return {"operation": operation_document(operation), "replayed": not created}


@router.post("/snapraid/replacements/preview")
def preview_snapraid_replacement(
    payload: SnapraidReplacementPreviewRequest,
    request: Request,
    _principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    snapshot = _latest_hardware(session)
    disks = snapshot.payload_json.get("disks", [])
    matches = [
        item
        for item in disks
        if isinstance(item, dict) and item.get("id") == payload.replacement_device_id
    ]
    config_root = request.app.state.settings.snapraid_config_root
    config_path = config_root / f"{payload.pool_name}.conf"
    try:
        if len(matches) != 1 or config_path.parent != config_root:
            raise SnapraidReplacementError("drive_not_found", "Replacement drive not found.")
        config = config_path.read_text(encoding="utf-8")
        plan = build_replacement_plan(
            pool_name=payload.pool_name,
            data_name=payload.data_name,
            config=config,
            disk=matches[0],
            hardware_snapshot_sha256=snapshot.sha256,
            filesystem=payload.filesystem,
        )
    except OSError as exc:
        raise Problem(
            409,
            "snapraid_config_unavailable",
            "SnapRAID unavailable",
            "The SnapRAID configuration is unavailable.",
        ) from exc
    except SnapraidReplacementError as exc:
        raise Problem(422, exc.code, "Replacement unavailable", str(exc)) from exc
    return {"plan": plan, "plan_sha256": document_hash(plan)}


@router.post("/snapraid/replacements", status_code=202)
def apply_snapraid_replacement(
    payload: SnapraidReplacementApplyRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    if document_hash(payload.plan) != payload.plan_sha256:
        raise Problem(
            409, "snapraid_plan_changed", "Plan changed", "Preview the replacement again."
        )
    try:
        plan = validate_replacement_plan(payload.plan)
    except SnapraidReplacementError as exc:
        raise Problem(422, exc.code, "Invalid replacement plan", str(exc)) from exc
    if _latest_hardware(session).sha256 != plan["hardware_snapshot_sha256"]:
        raise Problem(
            409, "hardware_snapshot_changed", "Discovery changed", "Preview the replacement again."
        )
    try:
        operation, created = create_operation(
            session,
            kind="storage.snapraid.replace",
            principal=principal,
            request={
                "plan": plan,
                "plan_sha256": payload.plan_sha256,
                "confirmation_sha256": document_hash({"confirmation": "I AGREE"}),
            },
            idempotency_key=key,
            resource_type="drive",
            resource_id=str(plan["device"]["id"]),
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        if str(plan["device"]["id"]) in reserved_device_ids(
            session, exclude_operation_id=operation.id
        ):
            raise Problem(
                409,
                "drive_reserved",
                "Drive is busy",
                "Another storage operation uses this drive.",
            )
        record_audit(
            session,
            principal=principal,
            action="storage.snapraid.replace",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="drive",
            target_id=str(plan["device"]["id"]),
            details={
                "pool_name": plan["pool_name"],
                "data_name": plan["data_name"],
                "plan_sha256": payload.plan_sha256,
            },
        )
    return {"operation": operation_document(operation), "replayed": not created}


def _transfer_plan(payload: TierTransferPreviewRequest) -> dict[str, object]:
    source = Path(payload.source)
    destination_parent = Path(payload.destination).parent
    try:
        source_stat = source.stat()
        destination_stat = destination_parent.stat()
    except OSError as exc:
        raise Problem(
            422,
            "transfer_path_unavailable",
            "Path unavailable",
            "The source and destination folder must exist.",
        ) from exc
    if not source.is_file() or not destination_parent.is_dir():
        raise Problem(
            422,
            "transfer_path_invalid",
            "Invalid path",
            "The source must be a file and the destination folder must exist.",
        )
    raw = payload.model_dump(exclude_none=True)
    raw.update(
        {
            "source_identity": f"dev:{source_stat.st_dev}",
            "destination_identity": f"dev:{destination_stat.st_dev}",
            "same_filesystem": source_stat.st_dev == destination_stat.st_dev,
            "required_bytes": source_stat.st_size,
        }
    )
    try:
        return plan_transfer(raw).document()
    except TieringError as exc:
        raise Problem(422, exc.code, "Invalid transfer", str(exc)) from exc


@router.post("/transfers/preview")
def preview_transfer(
    payload: TierTransferPreviewRequest,
    _principal: Principal = Depends(authenticated_principal),
) -> dict[str, object]:
    plan = _transfer_plan(payload)
    return {"plan": plan, "plan_sha256": document_hash(plan)}


@router.post("/transfers", status_code=202)
def apply_transfer(
    payload: TierTransferApplyRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    if document_hash(payload.plan) != payload.plan_sha256:
        raise Problem(409, "transfer_plan_changed", "Plan changed", "Preview the transfer again.")
    try:
        normalized = plan_transfer(payload.plan).document()
    except TieringError as exc:
        raise Problem(422, exc.code, "Invalid transfer", str(exc)) from exc
    if normalized != payload.plan:
        raise Problem(409, "transfer_plan_changed", "Plan changed", "Preview the transfer again.")
    try:
        operation, created = create_operation(
            session,
            kind="storage.transfer",
            principal=principal,
            request={"plan": normalized, "plan_sha256": payload.plan_sha256},
            idempotency_key=key,
            resource_type="storage_transfer",
            resource_id=payload.plan_sha256,
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        record_audit(
            session,
            principal=principal,
            action="storage.transfer",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="storage_transfer",
            target_id=payload.plan_sha256,
            details={"workload": normalized["workload"], "method": normalized["method"]},
        )
    return {"operation": operation_document(operation), "replayed": not created}


@router.post("/transfers/{transfer_id}/cleanup", status_code=202)
def cleanup_transfer(
    transfer_id: str,
    payload: TierTransferCleanupRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    original = session.get(Operation, transfer_id)
    if (
        original is None
        or original.kind != "storage.transfer"
        or original.status != "succeeded"
        or (not principal.is_admin and original.actor_id != principal.user_id)
        or not isinstance(original.result_json, dict)
        or original.result_json.get("state") != "retained"
    ):
        raise Problem(
            409, "transfer_not_retained", "Cleanup unavailable", "No retained transfer was found."
        )
    plan = original.request_json.get("plan")
    plan_sha256 = original.request_json.get("plan_sha256")
    if not isinstance(plan, dict) or document_hash(plan) != plan_sha256:
        raise Problem(
            409, "transfer_plan_changed", "Plan changed", "The retained transfer is invalid."
        )
    try:
        operation, created = create_operation(
            session,
            kind="storage.transfer.cleanup",
            principal=principal,
            request={"plan": plan, "plan_sha256": plan_sha256, "transfer_id": transfer_id},
            idempotency_key=key,
            resource_type="storage_transfer",
            resource_id=transfer_id,
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        record_audit(
            session,
            principal=principal,
            action="storage.transfer.cleanup",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="storage_transfer",
            target_id=transfer_id,
        )
    return {"operation": operation_document(operation), "replayed": not created}
