from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
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
    WizardCreateRequest,
    WizardPlanApprovalRequest,
    WizardPlanRequest,
    WizardStepRequest,
)
from hoardarr.api.serializers import (
    operation_document,
    plan_document,
    snapshot_document,
    wizard_document,
)
from hoardarr.audit.service import record_audit
from hoardarr.auth.service import Principal
from hoardarr.db.models import HardwareSnapshot, Operation, Plan, WizardSession, utc_now
from hoardarr.operations.service import OperationConflict, create_operation, document_hash
from hoardarr.storage.reservations import plan_selected_device_ids, reserved_device_ids
from hoardarr.wizard.service import (
    WizardConflict,
    WizardConsentError,
    WizardError,
    WizardNotFound,
    WizardStateError,
    WizardValidationError,
    approve_plan,
    cancel_wizard,
    create_plan,
    create_wizard,
    get_wizard,
    plan_approval_status,
    refresh_plan_for_latest_discovery,
    require_current_plan_approval,
    update_step,
)

router = APIRouter(prefix="/wizards", tags=["wizards"])


def _raise_wizard_problem(exc: WizardError) -> None:
    if isinstance(exc, WizardNotFound):
        raise Problem(404, exc.code, "Not found", str(exc)) from exc
    if isinstance(exc, WizardConflict):
        raise Problem(
            409,
            exc.code,
            "Wizard changed",
            str(exc),
            errors=[
                {
                    "expected_revision": exc.expected_revision,
                    "current_revision": exc.current_revision,
                }
            ],
        ) from exc
    if isinstance(exc, WizardConsentError):
        raise Problem(
            409,
            exc.code,
            "Destructive approval required",
            str(exc),
            errors=[{"reason": exc.reason}],
        ) from exc
    if isinstance(exc, WizardStateError):
        raise Problem(409, exc.code, "Wizard state conflict", str(exc)) from exc
    if isinstance(exc, WizardValidationError):
        raise Problem(
            422,
            exc.code,
            "Wizard answers are invalid",
            "Review the highlighted wizard answers.",
            errors=[{"field": field, "message": message} for field, message in exc.errors.items()],
        ) from exc
    raise Problem(422, exc.code, "Wizard error", str(exc)) from exc


def _with_etag(response: Response, wizard: WizardSession) -> None:
    response.headers["ETag"] = f'"wizard-{wizard.id}-revision-{wizard.revision}"'


@router.post("", status_code=201)
def start_wizard(
    payload: WizardCreateRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    if payload.hardware_snapshot_id is None:
        raise Problem(
            422,
            "storage_discovery_required",
            "Storage discovery required",
            "Run storage discovery and select its hardware snapshot before starting the wizard.",
        )
    if (
        payload.hardware_snapshot_id is not None
        and session.get(HardwareSnapshot, payload.hardware_snapshot_id) is None
    ):
        raise Problem(
            422,
            "snapshot_not_found",
            "Hardware snapshot not found",
            "Select an existing hardware snapshot.",
        )
    try:
        wizard = create_wizard(
            session,
            mode=payload.mode,
            hardware_snapshot_id=payload.hardware_snapshot_id,
        )
    except WizardError as exc:
        _raise_wizard_problem(exc)
    record_audit(
        session,
        principal=principal,
        action="wizard.create",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="wizard_session",
        target_id=wizard.id,
        details={"workflow": payload.workflow, "mode": payload.mode},
    )
    _with_etag(response, wizard)
    return wizard_document(wizard)


@router.get("")
def list_wizards(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    wizards = session.scalars(
        select(WizardSession).order_by(WizardSession.updated_at.desc()).limit(100)
    )
    return {"items": [wizard_document(wizard) for wizard in wizards]}


@router.get("/{wizard_id}")
def read_wizard(
    wizard_id: str,
    response: Response,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        wizard = get_wizard(session, wizard_id)
    except WizardError as exc:
        _raise_wizard_problem(exc)
    _with_etag(response, wizard)
    return wizard_document(wizard)


@router.put("/{wizard_id}/steps/{step}")
def save_step(
    wizard_id: str,
    step: str,
    payload: WizardStepRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        wizard = update_step(
            session,
            wizard_id=wizard_id,
            expected_revision=payload.revision,
            step=step,
            answers=payload.answers,
        )
    except WizardError as exc:
        _raise_wizard_problem(exc)
    record_audit(
        session,
        principal=principal,
        action="wizard.step.update",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="wizard_session",
        target_id=wizard.id,
        details={"step": step, "revision": wizard.revision},
    )
    _with_etag(response, wizard)
    return wizard_document(wizard)


@router.post("/{wizard_id}/plan", status_code=201)
def review_plan(
    wizard_id: str,
    payload: WizardPlanRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        plan = create_plan(session, wizard_id=wizard_id, expected_revision=payload.revision)
        wizard = get_wizard(session, wizard_id)
    except WizardError as exc:
        _raise_wizard_problem(exc)
    record_audit(
        session,
        principal=principal,
        action="wizard.plan.create",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="plan",
        target_id=plan.id,
        details={"wizard_session_id": wizard.id, "revision": plan.revision},
    )
    _with_etag(response, wizard)
    return {
        "wizard": wizard_document(wizard),
        "plan": plan_document(plan),
        "approval": plan_approval_status(session, wizard_id=wizard.id),
    }


@router.get("/{wizard_id}/plan")
def read_plan(
    wizard_id: str,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        wizard = get_wizard(session, wizard_id)
    except WizardError as exc:
        _raise_wizard_problem(exc)
    if wizard.plan_id is None:
        raise Problem(404, "plan_not_found", "Not found", "This wizard has no current plan.")
    plan = session.get(Plan, wizard.plan_id)
    if plan is None:
        raise Problem(404, "plan_not_found", "Not found", "The wizard plan was not found.")
    document = plan_document(plan)
    document["approval"] = plan_approval_status(session, wizard_id=wizard.id)
    return document


@router.post("/{wizard_id}/complete")
def complete_wizard(
    wizard_id: str,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    """Close a successfully applied wizard after one-time credentials are handled."""

    try:
        wizard = get_wizard(session, wizard_id)
    except WizardError as exc:
        _raise_wizard_problem(exc)
    if wizard.status == "completed":
        return wizard_document(wizard)
    operation = session.scalar(
        select(Operation)
        .where(
            Operation.kind == "storage.apply",
            Operation.resource_type == "wizard_session",
            Operation.resource_id == wizard.id,
            Operation.status == "succeeded",
        )
        .order_by(Operation.updated_at.desc())
        .limit(1)
    )
    if wizard.status != "applied" or operation is None:
        raise Problem(
            409,
            "wizard_not_applied",
            "Storage setup is not complete",
            "The storage operation must succeed before this setup can be closed.",
        )
    wizard.status = "completed"
    wizard.updated_at = utc_now()
    record_audit(
        session,
        principal=principal,
        action="wizard.complete",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="wizard_session",
        target_id=wizard.id,
        details={"operation_id": operation.id},
    )
    return wizard_document(wizard)


@router.post("/{wizard_id}/plan/refresh")
def refresh_plan(
    wizard_id: str,
    payload: WizardPlanRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        wizard, plan, snapshot = refresh_plan_for_latest_discovery(
            session,
            wizard_id=wizard_id,
            expected_revision=payload.revision,
        )
    except WizardError as exc:
        _raise_wizard_problem(exc)
    record_audit(
        session,
        principal=principal,
        action="wizard.plan.refresh",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="plan",
        target_id=plan.id,
        details={
            "wizard_session_id": wizard.id,
            "revision": plan.revision,
            "hardware_snapshot_id": snapshot.id,
        },
    )
    _with_etag(response, wizard)
    return {
        "wizard": wizard_document(wizard),
        "plan": plan_document(plan),
        "hardware_snapshot": snapshot_document(snapshot, include_payload=True),
        "approval": plan_approval_status(session, wizard_id=wizard.id),
    }


@router.get("/{wizard_id}/plan/approval")
def read_plan_approval(
    wizard_id: str,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        return plan_approval_status(session, wizard_id=wizard_id)
    except WizardError as exc:
        _raise_wizard_problem(exc)


@router.post("/{wizard_id}/plan/approve", status_code=201)
def approve_destructive_plan(
    wizard_id: str,
    payload: WizardPlanApprovalRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        approval = approve_plan(
            session,
            wizard_id=wizard_id,
            expected_revision=payload.revision,
            plan_sha256=payload.plan_sha256,
            hardware_snapshot_sha256=payload.hardware_snapshot_sha256,
            selected_device_ids=payload.selected_device_ids,
            confirmation=payload.confirmation,
            actor_type=principal.auth_type,
            actor_id=principal.user_id,
        )
    except WizardError as exc:
        record_audit(
            session,
            principal=principal,
            action="wizard.plan.approve",
            outcome="rejected",
            correlation_id=request.state.request_id,
            target_type="wizard_session",
            target_id=wizard_id,
            details={
                "code": exc.code,
                "reason": getattr(exc, "reason", None),
                "submitted_plan_sha256": payload.plan_sha256,
                "submitted_hardware_snapshot_sha256": payload.hardware_snapshot_sha256,
                "submitted_device_ids": payload.selected_device_ids,
            },
        )
        session.commit()
        _raise_wizard_problem(exc)
    record_audit(
        session,
        principal=principal,
        action="wizard.plan.approve",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="plan",
        target_id=approval.plan_id,
        details={
            "approval_id": approval.id,
            "wizard_session_id": approval.wizard_session_id,
            "wizard_revision": approval.wizard_revision,
            "plan_sha256": approval.plan_sha256,
            "hardware_snapshot_sha256": approval.hardware_snapshot_sha256,
            "selected_device_ids": approval.selected_device_ids_json,
            "confirmation_sha256": approval.confirmation_sha256,
        },
    )
    return {
        "approval_id": approval.id,
        "approved_at": approval.approved_at,
        "status": plan_approval_status(session, wizard_id=wizard_id),
    }


@router.post("/{wizard_id}/cancel")
def cancel(
    wizard_id: str,
    payload: WizardPlanRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        wizard = cancel_wizard(
            session,
            wizard_id=wizard_id,
            expected_revision=payload.revision,
        )
    except WizardError as exc:
        _raise_wizard_problem(exc)
    record_audit(
        session,
        principal=principal,
        action="wizard.cancel",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="wizard_session",
        target_id=wizard.id,
    )
    _with_etag(response, wizard)
    return wizard_document(wizard)


@router.post("/{wizard_id}/apply", status_code=202)
def apply_plan(
    wizard_id: str,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        require_current_plan_approval(session, wizard_id=wizard_id)
    except WizardError as exc:
        _raise_wizard_problem(exc)
    wizard = get_wizard(session, wizard_id)
    plan = session.get(Plan, wizard.plan_id) if wizard.plan_id else None
    if plan is None or plan.sha256 != document_hash(plan.document_json):
        raise Problem(409, "plan_integrity_failed", "Plan changed", "Review a new storage plan.")
    blockers = plan.document_json.get("blockers")
    if plan.document_json.get("apply_available") is not True or blockers != []:
        raise Problem(
            409,
            "storage_apply_blocked",
            "This plan needs attention",
            "Complete the listed storage choices before applying the plan.",
            errors=blockers if isinstance(blockers, list) else [{"code": "plan_invalid"}],
        )
    conflicts = sorted(set(plan_selected_device_ids(plan)) & reserved_device_ids(session))
    if conflicts:
        raise Problem(
            409,
            "storage_drives_reserved",
            "Selected drives are already in use",
            "One or more selected drives belong to another queued or running storage operation.",
            errors=[{"code": "drive_reserved", "device_id": item} for item in conflicts],
        )
    operation_request = {
        "schema_version": 1,
        "wizard_id": wizard.id,
        "wizard_revision": wizard.revision,
        "plan_id": plan.id,
        "plan_sha256": plan.sha256,
    }
    try:
        operation, created = create_operation(
            session,
            kind="storage.apply",
            principal=principal,
            request=operation_request,
            idempotency_key=key,
            resource_type="wizard_session",
            resource_id=wizard.id,
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        record_audit(
            session,
            principal=principal,
            action="storage.apply.queue",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="operation",
            target_id=operation.id,
            details={"wizard_id": wizard.id, "plan_id": plan.id, "plan_sha256": plan.sha256},
        )
    return {"operation": operation_document(operation), "replayed": not created}


@router.post("/{wizard_id}/reconcile-access", status_code=202)
def reconcile_access(
    wizard_id: str,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    wizard = get_wizard(session, wizard_id)
    plan = session.get(Plan, wizard.plan_id) if wizard.plan_id else None
    if wizard.status not in {"applied", "completed"} or plan is None:
        raise Problem(
            409,
            "storage_not_applied",
            "Storage is not ready",
            "Only a successfully applied storage plan can have its access reconciled.",
        )
    if plan.sha256 != document_hash(plan.document_json):
        raise Problem(409, "plan_integrity_failed", "Plan changed", "Review the storage plan.")
    directories = plan.document_json.get("actions", {}).get("directories")
    if not isinstance(directories, list) or not directories:
        raise Problem(
            409,
            "storage_access_not_configured",
            "No managed folders",
            "This storage plan does not contain managed media or download folders.",
        )
    operation_request = {
        "schema_version": 1,
        "wizard_id": wizard.id,
        "wizard_revision": wizard.revision,
        "plan_id": plan.id,
        "plan_sha256": plan.sha256,
    }
    try:
        operation, created = create_operation(
            session,
            kind="storage.access.reconcile",
            principal=principal,
            request=operation_request,
            idempotency_key=key,
            resource_type="wizard_session",
            resource_id=wizard.id,
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        record_audit(
            session,
            principal=principal,
            action="storage.access.reconcile.queue",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="operation",
            target_id=operation.id,
            details={"wizard_id": wizard.id, "plan_id": plan.id, "plan_sha256": plan.sha256},
        )
    return {"operation": operation_document(operation), "replayed": not created}
