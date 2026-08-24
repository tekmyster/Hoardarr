from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.api.dependencies import (
    authenticated_principal,
    database_session,
    idempotency_key,
    require_state_scope,
    settings_from_request,
)
from hoardarr.api.problem import Problem
from hoardarr.api.schemas import (
    ArrayReplacementApplyRequest,
    ArrayReplacementPreviewRequest,
    DeviceMaintenanceApplyRequest,
    DeviceMaintenancePreviewRequest,
    ForeignInspectionApplyRequest,
    ForeignInspectionPreviewRequest,
    ForeignMigrationApplyRequest,
    ForeignMigrationPreviewRequest,
    ForeignStackPreviewRequest,
    NASEvidenceRequest,
    PhysicalDiskReconcileRequest,
    PhysicalDiskReservationRequest,
    SnapraidReplacementApplyRequest,
    SnapraidReplacementPreviewRequest,
    StorageBackendActivationRequest,
    StorageBackendAssignRequest,
    StorageBackendRetirementRequest,
    StorageBackendTransitionRequest,
    StorageDrainApplyRequest,
    StorageDrainPreviewRequest,
    StorageGroupCreateRequest,
    StorageRedundancyApplyRequest,
    StorageRedundancyPreviewRequest,
    StorageVolumeApplyRequest,
    StorageVolumeCapacityApplyRequest,
    StorageVolumeCapacityPreviewRequest,
    StorageVolumePreviewRequest,
    StorageVolumeSnapshotApplyRequest,
    StorageVolumeSnapshotPreviewRequest,
    StorageVolumeSnapshotScheduleRequest,
    TierTransferApplyRequest,
    TierTransferCleanupRequest,
    TierTransferPreviewRequest,
    UnraidEvidenceRequest,
)
from hoardarr.api.serializers import operation_document
from hoardarr.audit.service import record_audit
from hoardarr.auth.service import Principal
from hoardarr.core.config import Settings
from hoardarr.db.models import (
    ForeignMigrationJob,
    HardwareSnapshot,
    Operation,
    StorageBackend,
    StorageDrainJob,
    StorageGroup,
    StorageVolume,
    StorageVolumeSnapshot,
    StorageVolumeSnapshotSchedule,
)
from hoardarr.operations.service import OperationConflict, create_operation, document_hash
from hoardarr.storage.capacity_plans import (
    CapacityPlanError,
    build_capacity_plan,
    validate_capacity_plan,
)
from hoardarr.storage.client import StorageExecutorError, preview_foreign_stack
from hoardarr.storage.drain import DrainPlanError, build_drain_plan, validate_drain_plan
from hoardarr.storage.expansion import build_expansion_assessment
from hoardarr.storage.foreign import (
    ForeignStorageError,
    assess_foreign_storage,
    build_inspection_plan,
    build_migration_plan,
    build_stack_preview_plan,
    clear_nas_evidence,
    clear_unraid_evidence,
    persist_nas_evidence,
    persist_unraid_evidence,
    validate_inspection_plan,
    validate_migration_plan,
)
from hoardarr.storage.groups import (
    StorageGroupError,
    activate_backend,
    assign_backend,
    build_backend_activation_plan,
    create_group,
    disk_documents,
    group_documents,
    register_disk,
    release_retired_backend,
    set_disk_reservation,
    transition_backend,
)
from hoardarr.storage.inventory import discover_storage_inventory
from hoardarr.storage.maintenance import MaintenanceError, build_plan, validate_plan
from hoardarr.storage.mergerfs import discover_mergerfs
from hoardarr.storage.redundancy import (
    RedundancyError,
    build_redundancy_plan,
    redundancy_event_documents,
    storage_documents,
    validate_redundancy_plan,
)
from hoardarr.storage.replacement import (
    ArrayReplacementError,
    build_md_replacement_plan,
    build_zfs_replacement_plan,
    validate_array_replacement_plan,
)
from hoardarr.storage.reservations import active_storage_reservations, reserved_device_ids
from hoardarr.storage.snapraid import (
    SnapraidReplacementError,
    build_replacement_plan,
    validate_replacement_plan,
)
from hoardarr.storage.snapshot_plans import (
    SnapshotPlanError,
    build_snapshot_plan,
    validate_snapshot_plan,
)
from hoardarr.storage.snapshots import (
    SnapshotLifecycleError,
    configure_schedule,
    provider_guid,
    require_snapshot_capability,
    schedule_document,
    snapshot_document,
    snapshot_documents,
)
from hoardarr.storage.telemetry import storage_telemetry
from hoardarr.storage.tiering import TieringError, plan_transfer, transfer_queue_summary
from hoardarr.storage.volume_plans import (
    VolumePlanError,
    build_guided_volume_plan,
    validate_guided_volume_plan,
)
from hoardarr.storage.volumes import canonical_volume_identity, volume_document, volume_documents

router = APIRouter(prefix="/storage", tags=["storage"])


def _require_capacity_capability(volume: StorageVolume) -> None:
    required = (
        ("quota", "reservation") if volume.resource_type == "dataset" else ("thin_provisioning",)
    )
    for name in required:
        capability = volume.capabilities_json.get(name, {})
        if capability.get("support") != "supported":
            raise CapacityPlanError(
                "volume_capacity_provider_unsupported",
                f"The provider does not support {name.replace('_', ' ')} for this resource.",
            )
        if capability.get("availability") != "available":
            raise CapacityPlanError(
                "volume_capacity_capability_unavailable",
                f"The provider cannot currently apply {name.replace('_', ' ')}.",
            )


def _group_problem(exc: StorageGroupError) -> Problem:
    status = (
        404
        if exc.code.endswith("_not_found")
        else 409
        if "conflict" in exc.code or "already" in exc.code
        else 422
    )
    return Problem(status, exc.code, "Storage lifecycle request rejected", str(exc))


def _drain_problem(exc: DrainPlanError) -> Problem:
    status = 404 if exc.code.endswith("_not_found") else 409 if "state" in exc.code else 422
    return Problem(status, exc.code, "Drain preflight rejected", str(exc))


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


@router.get("/groups")
def storage_groups(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    return {"items": group_documents(session)}


@router.get("/expansion")
def storage_expansion_assessment(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    """Return current, read-only expansion choices bound to the latest hardware snapshot."""

    snapshot = _latest_hardware(session)
    inventory = discover_storage_inventory(hardware_snapshot=snapshot.payload_json)
    return build_expansion_assessment(session, snapshot=snapshot, storage_inventory=inventory)


@router.get("/foreign")
def foreign_storage_assessment(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    """Fingerprint persisted signatures without mounting or activating foreign storage."""

    return assess_foreign_storage(session, snapshot=_latest_hardware(session))


@router.post("/foreign/unraid/evidence", status_code=201)
def save_unraid_assignment_evidence(
    payload: UnraidEvidenceRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    """Persist an assignment export; no source disk is opened or changed."""

    evidence = persist_unraid_evidence(
        session,
        document=payload.model_dump(mode="json"),
        created_by=principal.user_id,
    )
    assessment = assess_foreign_storage(session, snapshot=_latest_hardware(session))
    summary = assessment["unraid_evidence"]
    record_audit(
        session,
        principal=principal,
        action="storage.foreign.unraid_evidence.save",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="foreign_import_evidence",
        target_id=evidence.id,
        details={
            "document_sha256": evidence.document_sha256,
            "assignment_count": summary["assignment_count"] if summary else 0,
            "matched_assignment_count": summary["matched_assignment_count"] if summary else 0,
        },
    )
    return {"item": summary}


@router.delete("/foreign/unraid/evidence")
def remove_unraid_assignment_evidence(
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    cleared = clear_unraid_evidence(session)
    record_audit(
        session,
        principal=principal,
        action="storage.foreign.unraid_evidence.remove",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="foreign_import_evidence",
        details={"cleared_count": cleared},
    )
    return {"cleared": cleared}


@router.post("/foreign/nas/evidence", status_code=201)
def save_nas_source_evidence(
    payload: NASEvidenceRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    """Persist a source-NAS runtime export; current disks remain unopened and unchanged."""

    evidence = persist_nas_evidence(
        session,
        document=payload.model_dump(mode="json"),
        created_by=principal.user_id,
    )
    assessment = assess_foreign_storage(session, snapshot=_latest_hardware(session))
    summary = assessment["nas_evidence"]
    record_audit(
        session,
        principal=principal,
        action="storage.foreign.nas_evidence.save",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="foreign_import_evidence",
        target_id=evidence.id,
        details={
            "document_sha256": evidence.document_sha256,
            "platform": summary["platform"] if summary else None,
            "member_count": summary["member_count"] if summary else 0,
            "matched_member_count": summary["matched_member_count"] if summary else 0,
        },
    )
    return {"item": summary}


@router.delete("/foreign/nas/evidence")
def remove_nas_source_evidence(
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    cleared = clear_nas_evidence(session)
    record_audit(
        session,
        principal=principal,
        action="storage.foreign.nas_evidence.remove",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="foreign_import_evidence",
        details={"cleared_count": cleared},
    )
    return {"cleared": cleared}


@router.post("/foreign/stack-preview")
def preview_foreign_storage_stack(
    payload: ForeignStackPreviewRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    settings: Settings = Depends(settings_from_request),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    """Read provider metadata without assembling, activating, or importing a stack."""

    snapshot = _latest_hardware(session)
    try:
        plan = build_stack_preview_plan(
            session,
            snapshot=snapshot,
            candidate_id=payload.candidate_id,
        )
        result = preview_foreign_stack(
            settings.storage_executor_socket,
            plan_sha256=plan["plan_sha256"],
            plan=plan,
            timeout_seconds=min(120.0, settings.storage_executor_timeout_seconds),
        )
    except ForeignStorageError as exc:
        raise Problem(422, exc.code, "Stack preview unavailable", str(exc)) from exc
    except StorageExecutorError as exc:
        raise Problem(503, exc.code, "Stack preview unavailable", str(exc)) from exc
    record_audit(
        session,
        principal=principal,
        action="storage.foreign.preview_stack",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="foreign_storage",
        target_id=payload.candidate_id,
        details={
            "plan_sha256": plan["plan_sha256"],
            "provider": result.get("provider"),
            "activation_performed": False,
            "mutation_performed": False,
        },
    )
    return {"plan": plan, "result": result}


@router.post("/foreign/inspection/preview")
def preview_foreign_inspection(
    payload: ForeignInspectionPreviewRequest,
    _principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    """Build an immutable, non-persistent, read-only filesystem inventory plan."""

    try:
        plan = build_inspection_plan(
            session,
            snapshot=_latest_hardware(session),
            candidate_id=payload.candidate_id,
        )
    except ForeignStorageError as exc:
        raise Problem(422, exc.code, "Inspection unavailable", str(exc)) from exc
    return {"plan": plan, "plan_sha256": plan["plan_sha256"]}


@router.post("/foreign/inspection", status_code=202)
def start_foreign_inspection(
    payload: ForeignInspectionApplyRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        plan = validate_inspection_plan(payload.plan)
    except ForeignStorageError as exc:
        raise Problem(422, exc.code, "Invalid inspection plan", str(exc)) from exc
    if payload.plan_sha256 != plan["plan_sha256"]:
        raise Problem(409, "foreign_plan_changed", "Plan changed", "Preview the source again.")
    if _latest_hardware(session).sha256 != plan["hardware_snapshot_sha256"]:
        raise Problem(
            409,
            "hardware_snapshot_changed",
            "Discovery changed",
            "Run discovery and review the source again.",
        )
    try:
        operation, created = create_operation(
            session,
            kind="storage.foreign.inspect",
            principal=principal,
            request={
                "plan": plan,
                "plan_sha256": plan["plan_sha256"],
                "confirmation_sha256": document_hash({"confirmation": payload.confirmation}),
            },
            idempotency_key=key,
            resource_type="foreign_storage",
            resource_id=str(plan["candidate_id"]),
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        record_audit(
            session,
            principal=principal,
            action="storage.foreign.inspect_read_only",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="foreign_storage",
            target_id=str(plan["candidate_id"]),
            details={
                "plan_sha256": plan["plan_sha256"],
                "device_id": plan["device"]["id"],
                "filesystem_type": plan["source"]["filesystem_type"],
                "persistent_mount": False,
            },
        )
    return {"operation": operation_document(operation), "replayed": not created}


@router.post("/foreign/migration/preview")
def preview_foreign_migration(
    payload: ForeignMigrationPreviewRequest,
    _principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        plan = build_migration_plan(
            session,
            snapshot=_latest_hardware(session),
            candidate_id=payload.candidate_id,
            destination_backend_id=payload.destination_backend_id,
            verification_mode=payload.verification_mode,
            collision_policy=payload.collision_policy,
            reserve_bytes=payload.reserve_bytes,
            selection=payload.selection,
        )
    except ForeignStorageError as exc:
        raise Problem(422, exc.code, "Migration unavailable", str(exc)) from exc
    return {"plan": plan, "plan_sha256": plan["plan_sha256"]}


@router.post("/foreign/migration", status_code=202)
def start_foreign_migration(
    payload: ForeignMigrationApplyRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        plan = validate_migration_plan(payload.plan)
    except ForeignStorageError as exc:
        raise Problem(422, exc.code, "Invalid migration plan", str(exc)) from exc
    if payload.plan_sha256 != plan["plan_sha256"]:
        raise Problem(409, "foreign_plan_changed", "Plan changed", "Preview migration again.")
    if _latest_hardware(session).sha256 != plan["hardware_snapshot_sha256"]:
        raise Problem(
            409,
            "hardware_snapshot_changed",
            "Discovery changed",
            "Run discovery and review the source again.",
        )
    request_document = {
        "plan": plan,
        "plan_sha256": plan["plan_sha256"],
        "confirmation_sha256": document_hash({"confirmation": payload.confirmation}),
    }
    try:
        operation, created = create_operation(
            session,
            kind="storage.foreign.migrate",
            principal=principal,
            request=request_document,
            idempotency_key=key,
            resource_type="foreign_storage",
            resource_id=str(plan["candidate_id"]),
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        session.add(
            ForeignMigrationJob(
                id=operation.id,
                candidate_id=str(plan["candidate_id"]),
                destination_backend_id=str(plan["destination"]["backend_id"]),
                plan_sha256=str(plan["plan_sha256"]),
                verification_mode=str(plan["verification"]["mode"]),
                collision_policy=str(plan["collision_policy"]),
                report_json={
                    "source_inventory_operation_id": plan["source_inventory_operation_id"],
                    "source_retained": True,
                    "parity_reused": False,
                },
            )
        )
        record_audit(
            session,
            principal=principal,
            action="storage.foreign.migrate_files",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="foreign_storage",
            target_id=str(plan["candidate_id"]),
            details={
                "plan_sha256": plan["plan_sha256"],
                "destination_backend_id": plan["destination"]["backend_id"],
                "source_retained": True,
                "parity_reuse_supported": False,
            },
        )
    return {"operation": operation_document(operation), "replayed": not created}


@router.post("/groups", status_code=201)
def add_storage_group(
    payload: StorageGroupCreateRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        group = create_group(
            session,
            name=payload.name,
            namespace_path=payload.namespace_path,
            purpose=payload.purpose,
            principal=principal,
        )
    except StorageGroupError as exc:
        raise _group_problem(exc) from exc
    record_audit(
        session,
        principal=principal,
        action="storage.group.create",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="storage_group",
        target_id=group.id,
        details={"namespace_path": group.namespace_path, "purpose": group.purpose},
    )
    return {"item": next(item for item in group_documents(session) if item["id"] == group.id)}


@router.post("/groups/{group_id}/backends", status_code=201)
def add_storage_backend(
    group_id: str,
    payload: StorageBackendAssignRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        backend = assign_backend(
            session,
            group_id=group_id,
            physical_disk_id=payload.physical_disk_id,
            storage_entity_id=payload.storage_entity_id,
            namespace_path=payload.namespace_path,
            role=payload.role,
            principal=principal,
        )
    except StorageGroupError as exc:
        raise _group_problem(exc) from exc
    record_audit(
        session,
        principal=principal,
        action="storage.backend.assign",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="storage_backend",
        target_id=backend.id,
        details={"group_id": group_id, "stable_identity": backend.stable_identity},
    )
    return {"item": next(item for item in group_documents(session) if item["id"] == group_id)}


@router.post("/groups/{group_id}/backends/{backend_id}/transition")
def change_storage_backend_state(
    group_id: str,
    backend_id: str,
    payload: StorageBackendTransitionRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    if payload.target_state == "active":
        raise Problem(
            422,
            "activation_preflight_required",
            "Activation safety review required",
            "Review the mounted storage identity before activating this backend.",
        )
    try:
        backend = transition_backend(
            session,
            group_id=group_id,
            backend_id=backend_id,
            target_state=payload.target_state,
            principal=principal,
            reason=payload.reason,
        )
    except StorageGroupError as exc:
        raise _group_problem(exc) from exc
    record_audit(
        session,
        principal=principal,
        action="storage.backend.transition",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="storage_backend",
        target_id=backend.id,
        details={"group_id": group_id, "target_state": payload.target_state},
    )
    return {"item": next(item for item in group_documents(session) if item["id"] == group_id)}


@router.post("/groups/{group_id}/backends/{backend_id}/activation/preview")
def preview_storage_backend_activation(
    group_id: str,
    backend_id: str,
    _principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        plan = build_backend_activation_plan(
            session,
            group_id=group_id,
            backend_id=backend_id,
        )
    except StorageGroupError as exc:
        raise _group_problem(exc) from exc
    return {"plan": plan}


@router.post("/groups/{group_id}/backends/{backend_id}/activation")
def apply_storage_backend_activation(
    group_id: str,
    backend_id: str,
    payload: StorageBackendActivationRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        backend = activate_backend(
            session,
            group_id=group_id,
            backend_id=backend_id,
            plan_sha256=payload.plan_sha256,
            principal=principal,
            reason=payload.reason,
        )
    except StorageGroupError as exc:
        raise _group_problem(exc) from exc
    record_audit(
        session,
        principal=principal,
        action="storage.backend.activate",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="storage_backend",
        target_id=backend.id,
        details={"group_id": group_id, "plan_sha256": payload.plan_sha256},
    )
    return {"item": next(item for item in group_documents(session) if item["id"] == group_id)}


@router.post("/groups/{group_id}/backends/{backend_id}/retirement")
def release_storage_backend_for_reuse(
    group_id: str,
    backend_id: str,
    payload: StorageBackendRetirementRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    """Release only Hoardarr's retired assignment; never mutate device contents."""

    try:
        backend, disk = release_retired_backend(
            session,
            group_id=group_id,
            backend_id=backend_id,
            principal=principal,
            reason=payload.reason,
        )
    except StorageGroupError as exc:
        raise _group_problem(exc) from exc
    record_audit(
        session,
        principal=principal,
        action="storage.backend.release_for_reuse",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="storage_backend",
        target_id=backend.id,
        details={
            "group_id": group_id,
            "physical_disk_id": disk.id,
            "stable_identity": disk.stable_identity,
            "device_contents_changed": False,
        },
    )
    return {
        "item": next(item for item in group_documents(session) if item["id"] == group_id),
        "disk": next(item for item in disk_documents(session) if item["id"] == disk.id),
    }


@router.post("/groups/{group_id}/drain/preview")
def preview_storage_group_drain(
    group_id: str,
    payload: StorageDrainPreviewRequest,
    _principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    """Build an immutable, read-only drain preflight; this endpoint never starts movement."""

    try:
        plan = build_drain_plan(
            session,
            group_id=group_id,
            source_backend_id=payload.source_backend_id,
            destination_backend_ids=payload.destination_backend_ids,
            verification_mode=payload.verification_mode,
            reserve_bytes=payload.reserve_bytes,
            enforce_source_read_only=payload.enforce_source_read_only,
            bandwidth_limit_mib_per_second=payload.bandwidth_limit_mib_per_second,
            io_priority=payload.io_priority,
            start_at=payload.start_at,
            maintenance_window_minutes=payload.maintenance_window_minutes,
        )
    except DrainPlanError as exc:
        raise _drain_problem(exc) from exc
    return {"plan": plan}


@router.post("/groups/{group_id}/drain", status_code=202)
def start_storage_group_drain(
    group_id: str,
    payload: StorageDrainApplyRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    """Queue an approved immutable drain; file deletion begins only after verification."""

    try:
        validate_drain_plan(payload.plan)
    except DrainPlanError as exc:
        raise _drain_problem(exc) from exc
    if payload.plan.get("plan_sha256") != payload.plan_sha256:
        raise Problem(409, "drain_plan_changed", "Plan changed", "Preview the drain again.")
    if payload.plan.get("storage_group_id") != group_id:
        raise Problem(
            409,
            "drain_group_changed",
            "Storage Group changed",
            "The drain plan belongs to another Storage Group.",
        )
    if payload.plan.get("ready") is not True:
        raise Problem(
            409,
            "drain_preflight_blocked",
            "Drain is blocked",
            "Resolve every preflight blocker before starting the drain.",
        )
    source = payload.plan.get("source")
    verification = payload.plan.get("verification")
    if not isinstance(source, dict) or not isinstance(verification, dict):
        raise Problem(422, "drain_plan_invalid", "Invalid drain plan", "Preview the drain again.")
    operation_request = {
        "plan": payload.plan,
        "plan_sha256": payload.plan_sha256,
        "confirmation_sha256": document_hash({"confirmation": payload.confirmation}),
    }
    controls = payload.plan.get("controls")
    not_before = None
    if isinstance(controls, dict) and controls.get("start_at"):
        try:
            not_before = datetime.fromisoformat(str(controls["start_at"]))
        except ValueError as exc:
            raise Problem(
                422,
                "schedule_invalid",
                "Invalid schedule",
                "Preview the drain again.",
            ) from exc
    try:
        operation, created = create_operation(
            session,
            kind="storage.drain",
            principal=principal,
            request=operation_request,
            idempotency_key=key,
            resource_type="storage_group",
            resource_id=group_id,
            not_before=not_before,
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        session.add(
            StorageDrainJob(
                id=operation.id,
                storage_group_id=group_id,
                source_backend_id=str(source.get("backend_id")),
                plan_sha256=payload.plan_sha256,
                verification_mode=str(verification.get("mode")),
                status="queued",
                phase="preflight",
                report_json={},
            )
        )
        record_audit(
            session,
            principal=principal,
            action="storage.drain",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="storage_group",
            target_id=group_id,
            details={
                "source_backend_id": source.get("backend_id"),
                "verification_mode": verification.get("mode"),
                "plan_sha256": payload.plan_sha256,
            },
        )
    return {"operation": operation_document(operation), "replayed": not created}


@router.get("/disks")
def registered_disks(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    return {"items": disk_documents(session)}


@router.post("/disks/reconcile")
def reconcile_registered_disks(
    payload: PhysicalDiskReconcileRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    created = 0
    try:
        for item in payload.items:
            _disk, was_created = register_disk(session, item.model_dump())
            created += int(was_created)
    except StorageGroupError as exc:
        raise _group_problem(exc) from exc
    record_audit(
        session,
        principal=principal,
        action="storage.disks.reconcile",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="physical_disk_registry",
        details={"observed": len(payload.items), "created": created},
    )
    return {
        "items": disk_documents(session),
        "created": created,
        "updated": len(payload.items) - created,
    }


@router.post("/disks/{disk_id}/reservation")
def change_disk_reservation(
    disk_id: str,
    payload: PhysicalDiskReservationRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    snapshot = _latest_hardware(session)
    protected = {
        str(item.get("id"))
        for item in snapshot.payload_json.get("disks", [])[:4096]
        if isinstance(item, dict)
        and (item.get("system_disk") is True or item.get("system_device") is True)
    }
    try:
        disk = set_disk_reservation(
            session,
            disk_id=disk_id,
            action=payload.action,
            protected_identities=protected,
        )
    except StorageGroupError as exc:
        raise _group_problem(exc) from exc
    record_audit(
        session,
        principal=principal,
        action=f"storage.disk.{payload.action}",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="physical_disk",
        target_id=disk.id,
        details={"stable_identity": disk.stable_identity, "lifecycle_state": disk.lifecycle_state},
    )
    return {"item": next(item for item in disk_documents(session) if item["id"] == disk.id)}


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


@router.get("/volumes")
def storage_volume_inventory(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    """List provider-backed datasets, filesystem volumes, block volumes, and LUNs."""

    return {"items": volume_documents(session)}


@router.post("/volumes/preview")
def preview_storage_volume(
    payload: StorageVolumePreviewRequest,
    _principal: Principal = Depends(require_state_scope("operate")),
) -> dict[str, object]:
    inventory = discover_storage_inventory()
    try:
        plan = build_guided_volume_plan(
            inventory.get("pools", {}).get("items", []),
            **payload.model_dump(),
        )
    except VolumePlanError as exc:
        raise Problem(422, exc.code, "Volume plan rejected", str(exc)) from exc
    return {"plan": plan, "plan_sha256": plan["plan_sha256"]}


@router.post("/volumes", status_code=202)
def create_storage_volume(
    payload: StorageVolumeApplyRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    if payload.plan.get("plan_sha256") != payload.plan_sha256:
        raise Problem(409, "volume_plan_changed", "Plan changed", "Preview the volume again.")
    try:
        plan = validate_guided_volume_plan(payload.plan)
        stable_identity = canonical_volume_identity(
            str(plan["provider"]),
            str(plan["resource_type"]),
            str(plan["provider_resource_id"]),
        )
    except (VolumePlanError, ValueError) as exc:
        code = exc.code if isinstance(exc, VolumePlanError) else "volume_plan_invalid"
        raise Problem(422, code, "Volume plan rejected", str(exc)) from exc
    if plan["ready"] is not True:
        raise Problem(
            409,
            "volume_plan_blocked",
            "Volume plan blocked",
            "Resolve every plan blocker before creating storage.",
        )
    operation_request = {
        "plan": plan,
        "plan_sha256": payload.plan_sha256,
        "confirmation_sha256": document_hash({"confirmation": payload.confirmation}),
    }
    try:
        operation, created = create_operation(
            session,
            kind="storage.volume.create",
            principal=principal,
            request=operation_request,
            idempotency_key=key,
            resource_type="storage_volume",
            resource_id=stable_identity,
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        record_audit(
            session,
            principal=principal,
            action="storage.volume.create",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="storage_volume",
            target_id=stable_identity,
            details={"plan_sha256": payload.plan_sha256, "purpose": plan["purpose"]},
        )
    return {"operation": operation_document(operation), "replayed": not created}


@router.get("/volumes/{volume_id}")
def storage_volume_detail(
    volume_id: str,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    volume = session.get(StorageVolume, volume_id)
    if volume is None:
        raise Problem(
            404,
            "volume_not_found",
            "Storage area not found",
            "The storage area does not exist.",
        )
    operations = session.scalars(
        select(Operation)
        .where(
            Operation.resource_type == "storage_volume",
            Operation.resource_id == volume.stable_identity,
        )
        .order_by(Operation.created_at.desc())
        .limit(100)
    )
    return {
        "item": volume_document(volume),
        "operations": [operation_document(item) for item in operations],
    }


@router.get("/volumes/{volume_id}/snapshots")
def storage_volume_snapshots(
    volume_id: str,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    volume = session.get(StorageVolume, volume_id)
    if volume is None:
        raise Problem(
            404,
            "volume_not_found",
            "Storage area not found",
            "The storage area does not exist.",
        )
    schedule = session.scalar(
        select(StorageVolumeSnapshotSchedule).where(
            StorageVolumeSnapshotSchedule.volume_id == volume.id
        )
    )
    return {
        "items": snapshot_documents(session, volume.id),
        "schedule": schedule_document(schedule),
        "source": "durable_provider_operations",
    }


@router.post("/volumes/{volume_id}/capacity/preview")
def preview_storage_volume_capacity(
    volume_id: str,
    payload: StorageVolumeCapacityPreviewRequest,
    _principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    volume = session.get(StorageVolume, volume_id)
    if volume is None:
        raise Problem(
            404,
            "volume_not_found",
            "Storage area not found",
            "The storage area does not exist.",
        )
    try:
        _require_capacity_capability(volume)
        plan = build_capacity_plan(
            volume=volume_document(volume),
            provider_guid=provider_guid(volume),
            **payload.model_dump(),
        )
    except (CapacityPlanError, SnapshotLifecycleError) as exc:
        raise Problem(409, exc.code, "Capacity plan rejected", str(exc)) from exc
    return {"plan": plan, "plan_sha256": plan["plan_sha256"]}


@router.post("/volumes/{volume_id}/capacity", status_code=202)
def apply_storage_volume_capacity_plan(
    volume_id: str,
    payload: StorageVolumeCapacityApplyRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    volume = session.get(StorageVolume, volume_id)
    if volume is None:
        raise Problem(
            404,
            "volume_not_found",
            "Storage area not found",
            "The storage area does not exist.",
        )
    try:
        plan = validate_capacity_plan(payload.plan)
        _require_capacity_capability(volume)
        if (
            payload.plan_sha256 != plan["plan_sha256"]
            or plan["volume"]["id"] != volume.id
            or plan["volume"]["stable_identity"] != volume.stable_identity
            or plan["volume"]["provider_guid"] != provider_guid(volume)
        ):
            raise CapacityPlanError(
                "volume_capacity_plan_changed",
                "The provider-backed storage identity changed after review.",
            )
        if payload.confirmation != plan["confirmation"]:
            raise CapacityPlanError(
                "volume_capacity_confirmation_missing",
                "Enter the exact capacity-limit confirmation.",
            )
    except (CapacityPlanError, SnapshotLifecycleError) as exc:
        raise Problem(409, exc.code, "Capacity request rejected", str(exc)) from exc
    try:
        operation, created = create_operation(
            session,
            kind="storage.volume.capacity",
            principal=principal,
            request={
                "plan": plan,
                "plan_sha256": plan["plan_sha256"],
                "confirmation_sha256": document_hash({"confirmation": payload.confirmation}),
            },
            idempotency_key=key,
            resource_type="storage_volume",
            resource_id=volume.stable_identity,
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        record_audit(
            session,
            principal=principal,
            action="storage.volume.capacity.apply",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="storage_volume",
            target_id=volume.stable_identity,
            details={"plan_sha256": plan["plan_sha256"], "target": plan["target"]},
        )
    return {"operation": operation_document(operation), "replayed": not created}


@router.post("/volumes/{volume_id}/snapshots/preview")
def preview_storage_volume_snapshot(
    volume_id: str,
    payload: StorageVolumeSnapshotPreviewRequest,
    _principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    volume = session.get(StorageVolume, volume_id)
    if volume is None:
        raise Problem(
            404,
            "volume_not_found",
            "Storage area not found",
            "The storage area does not exist.",
        )
    snapshot = None
    if payload.action != "create":
        if payload.snapshot_id is None:
            raise Problem(
                422,
                "snapshot_required",
                "Snapshot required",
                "Select the exact provider snapshot before continuing.",
            )
        selected = session.get(StorageVolumeSnapshot, payload.snapshot_id)
        if selected is None or selected.volume_id != volume.id or selected.state != "available":
            raise Problem(
                404, "snapshot_not_found", "Snapshot not found", "The snapshot is unavailable."
            )
        snapshot = snapshot_document(selected)
    try:
        require_snapshot_capability(volume)
        plan = build_snapshot_plan(
            volume=volume_document(volume),
            provider_guid=provider_guid(volume),
            action=payload.action,
            snapshot_name=payload.snapshot_name,
            snapshot=snapshot,
            clone_name=payload.clone_name,
        )
    except (SnapshotLifecycleError, SnapshotPlanError) as exc:
        raise Problem(409, exc.code, "Snapshot plan rejected", str(exc)) from exc
    return {"plan": plan, "plan_sha256": plan["plan_sha256"]}


@router.post("/volumes/{volume_id}/snapshots", status_code=202)
def apply_storage_volume_snapshot_plan(
    volume_id: str,
    payload: StorageVolumeSnapshotApplyRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    volume = session.get(StorageVolume, volume_id)
    if volume is None:
        raise Problem(
            404,
            "volume_not_found",
            "Storage area not found",
            "The storage area does not exist.",
        )
    try:
        plan = validate_snapshot_plan(payload.plan)
        require_snapshot_capability(volume)
        if (
            payload.plan_sha256 != plan["plan_sha256"]
            or plan["volume"]["id"] != volume.id
            or plan["volume"]["stable_identity"] != volume.stable_identity
            or plan["volume"]["provider_guid"] != provider_guid(volume)
        ):
            raise SnapshotPlanError(
                "snapshot_plan_changed", "The volume identity changed after review."
            )
        if plan["action"] != "create":
            selected_plan = plan["snapshot"]
            selected = session.get(StorageVolumeSnapshot, selected_plan["id"])
            if (
                selected is None
                or selected.volume_id != volume.id
                or selected.state != "available"
                or selected.provider_snapshot_id != selected_plan["provider_snapshot_id"]
                or selected.provider_guid != selected_plan["provider_guid"]
            ):
                raise SnapshotPlanError(
                    "snapshot_identity_changed",
                    "The selected provider snapshot changed after review.",
                )
        if payload.confirmation != plan["confirmation"]:
            raise SnapshotPlanError(
                "snapshot_confirmation_missing", "Enter the exact snapshot confirmation."
            )
    except (SnapshotLifecycleError, SnapshotPlanError) as exc:
        raise Problem(409, exc.code, "Snapshot request rejected", str(exc)) from exc
    try:
        operation, created = create_operation(
            session,
            kind="storage.volume.snapshot",
            principal=principal,
            request={
                "plan": plan,
                "plan_sha256": plan["plan_sha256"],
                "confirmation_sha256": document_hash({"confirmation": payload.confirmation}),
            },
            idempotency_key=key,
            resource_type="storage_volume",
            resource_id=volume.stable_identity,
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        record_audit(
            session,
            principal=principal,
            action=f"storage.volume.snapshot.{plan['action']}",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="storage_volume",
            target_id=volume.stable_identity,
            details={"plan_sha256": plan["plan_sha256"]},
        )
    return {"operation": operation_document(operation), "replayed": not created}


@router.put("/volumes/{volume_id}/snapshot-schedule")
def update_storage_volume_snapshot_schedule(
    volume_id: str,
    payload: StorageVolumeSnapshotScheduleRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    volume = session.get(StorageVolume, volume_id)
    if volume is None:
        raise Problem(
            404,
            "volume_not_found",
            "Storage area not found",
            "The storage area does not exist.",
        )
    try:
        schedule = configure_schedule(session, volume, **payload.model_dump())
    except SnapshotLifecycleError as exc:
        raise Problem(409, exc.code, "Snapshot schedule rejected", str(exc)) from exc
    record_audit(
        session,
        principal=principal,
        action="storage.volume.snapshot.schedule",
        outcome="updated",
        correlation_id=request.state.request_id,
        target_type="storage_volume",
        target_id=volume.stable_identity,
        details={
            "enabled": schedule.enabled,
            "interval_hours": schedule.interval_hours,
            "retention_count": schedule.retention_count,
        },
    )
    return {"schedule": schedule_document(schedule)}


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
            settings_override=payload.settings,
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


@router.get("/logical/{storage_entity_id}/redundancy/events")
def storage_redundancy_events(
    storage_entity_id: str,
    limit: int = 200,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        return {
            "items": redundancy_event_documents(
                session, storage_entity_id, limit=max(1, min(limit, 500))
            )
        }
    except RedundancyError as exc:
        raise Problem(404, exc.code, "Storage not found", str(exc)) from exc


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


@router.post("/arrays/replacements/preview")
def preview_array_replacement(
    payload: ArrayReplacementPreviewRequest,
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
    inventory = discover_storage_inventory(hardware_snapshot=snapshot.payload_json)
    pools = inventory.get("pools", {}).get("items", [])
    targets = [
        item for item in pools if isinstance(item, dict) and item.get("id") == payload.target_id
    ]
    if len(matches) != 1:
        raise Problem(404, "drive_not_found", "Replacement drive not found", "Run discovery again.")
    if len(targets) != 1:
        raise Problem(404, "storage_not_found", "Storage not found", "Run storage discovery again.")
    try:
        plan = (
            build_zfs_replacement_plan(
                pool=targets[0],
                member_path=payload.old_member_path or "",
                disk=matches[0],
                hardware_snapshot_sha256=snapshot.sha256,
            )
            if payload.target_id.startswith("zfs:")
            else build_md_replacement_plan(
                array=targets[0],
                member_path=payload.old_member_path,
                disk=matches[0],
                hardware_snapshot_sha256=snapshot.sha256,
            )
        )
    except ArrayReplacementError as exc:
        raise Problem(422, exc.code, "Replacement unavailable", str(exc)) from exc
    return {"plan": plan, "plan_sha256": document_hash(plan)}


@router.post("/arrays/replacements", status_code=202)
def apply_array_replacement(
    payload: ArrayReplacementApplyRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    if document_hash(payload.plan) != payload.plan_sha256:
        raise Problem(409, "array_replacement_plan_changed", "Plan changed", "Preview again.")
    try:
        plan = validate_array_replacement_plan(payload.plan)
    except ArrayReplacementError as exc:
        raise Problem(422, exc.code, "Invalid replacement plan", str(exc)) from exc
    if _latest_hardware(session).sha256 != plan["hardware_snapshot_sha256"]:
        raise Problem(409, "hardware_snapshot_changed", "Discovery changed", "Preview again.")
    try:
        operation, created = create_operation(
            session,
            kind="storage.array.replace",
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
            raise Problem(409, "drive_reserved", "Drive is busy", "Another operation uses it.")
        record_audit(
            session,
            principal=principal,
            action="storage.array.replace",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type=str(plan["provider"]),
            target_id=str(plan["target_id"]),
            details={
                "plan_sha256": payload.plan_sha256,
                "replacement_device_id": plan["device"]["id"],
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


@router.get("/transfers/summary")
def transfer_summary(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    operations = session.scalars(
        select(Operation)
        .where(Operation.kind.in_(("storage.transfer", "storage.transfer.cleanup")))
        .order_by(Operation.created_at.desc())
        .limit(1000)
    ).all()
    tiers: list[dict[str, object]] = []
    backends = session.execute(
        select(StorageBackend, StorageGroup)
        .join(StorageGroup, StorageGroup.id == StorageBackend.storage_group_id)
        .where(
            StorageBackend.role.in_(("cache", "landing")),
            StorageBackend.lifecycle_state.notin_(("retired", "reuse_ready")),
        )
        .order_by(StorageGroup.name, StorageBackend.id)
        .limit(64)
    ).all()
    for backend, group in backends:
        quality = "temporarily_unavailable"
        total_bytes: int | None = None
        free_bytes: int | None = None
        used_bytes: int | None = None
        path = backend.namespace_path
        if isinstance(path, str) and path:
            try:
                usage = shutil.disk_usage(path)
            except OSError:
                pass
            else:
                quality = "available"
                total_bytes = usage.total
                free_bytes = usage.free
                used_bytes = usage.used
        else:
            quality = "not_reported"
        tiers.append(
            {
                "storage_group_id": group.id,
                "storage_group_name": group.name,
                "backend_id": backend.id,
                "role": backend.role,
                "path": path,
                "quality": quality,
                "total_bytes": total_bytes,
                "used_bytes": used_bytes,
                "free_bytes": free_bytes,
            }
        )
    return {"queue": transfer_queue_summary(operations), "tiers": tiers}


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
