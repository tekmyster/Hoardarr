from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.api.backup_schemas import (
    BackupConfirmationRequest,
    BackupCredentialRotationRequest,
    BackupRestoreValidationRequest,
    BackupScheduleRequest,
    BackupTargetCreateRequest,
)
from hoardarr.api.dependencies import (
    authenticated_principal,
    database_session,
    idempotency_key,
    require_state_scope,
    secret_box_from_request,
)
from hoardarr.api.problem import Problem
from hoardarr.api.serializers import operation_document
from hoardarr.audit.service import record_audit
from hoardarr.auth.service import Principal
from hoardarr.backups.service import (
    BackupError,
    encrypt_credentials,
    normalize_prefix,
    run_document,
    target_document,
    target_fingerprint,
    validate_endpoint,
)
from hoardarr.core.secrets import SecretBox
from hoardarr.db.models import Operation, RemoteBackupRun, RemoteBackupTarget, new_id
from hoardarr.operations.service import OperationConflict, create_operation

router = APIRouter(prefix="/backups", tags=["backups"])


def _target(session: Session, target_id: str) -> RemoteBackupTarget:
    target = session.get(RemoteBackupTarget, target_id)
    if target is None:
        raise Problem(404, "backup_target_not_found", "Not found", "Backup target was not found.")
    return target


def _backup_problem(exc: BackupError) -> Problem:
    return Problem(422, exc.code, "Backup target rejected", exc.safe_message)


@router.get("/targets")
def list_targets(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    items = session.scalars(select(RemoteBackupTarget).order_by(RemoteBackupTarget.created_at))
    return {"items": [target_document(item) for item in items]}


@router.post("/targets", status_code=201)
def create_target(
    payload: BackupTargetCreateRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
    secret_box: SecretBox = Depends(secret_box_from_request),
) -> dict[str, object]:
    if session.scalar(select(RemoteBackupTarget.id).where(RemoteBackupTarget.name == payload.name)):
        raise Problem(
            409,
            "backup_target_name_conflict",
            "Conflict",
            "A backup target already uses that name.",
        )
    try:
        endpoint = validate_endpoint(
            payload.provider,
            payload.endpoint_url,
            allow_private_network=payload.allow_private_network,
            allow_insecure_http=payload.allow_insecure_http,
        )
        prefix = normalize_prefix(payload.prefix)
    except BackupError as exc:
        raise _backup_problem(exc) from exc
    target_id = new_id()
    ciphertext, fingerprint = encrypt_credentials(
        secret_box,
        target_id,
        access_key_id=payload.access_key_id.get_secret_value(),
        secret_access_key=payload.secret_access_key.get_secret_value(),
        session_token=(payload.session_token.get_secret_value() if payload.session_token else None),
    )
    target = RemoteBackupTarget(
        id=target_id,
        name=payload.name,
        provider=payload.provider,
        endpoint_url=endpoint,
        region=payload.region,
        bucket=payload.bucket,
        prefix=prefix,
        force_path_style=payload.force_path_style,
        verify_tls=payload.verify_tls,
        allow_private_network=payload.allow_private_network,
        allow_insecure_http=payload.allow_insecure_http,
        bandwidth_limit_mib=payload.bandwidth_limit_mib,
        schedule_json={"enabled": False},
        secret_ciphertext=ciphertext,
        credential_fingerprint=fingerprint,
        created_by=principal.user_id,
    )
    session.add(target)
    session.flush()
    record_audit(
        session,
        principal=principal,
        action="backup.target.create",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="remote_backup_target",
        target_id=target.id,
        details={"provider": target.provider, "bucket": target.bucket, "prefix": target.prefix},
    )
    return target_document(target)


def _queue_target_operation(
    *,
    session: Session,
    target: RemoteBackupTarget,
    principal: Principal,
    key: str,
    kind: str,
    extra: dict[str, object] | None = None,
) -> tuple[Operation, bool]:
    request_document: dict[str, object] = {
        "target_id": target.id,
        "target_fingerprint": target_fingerprint(target),
        **(extra or {}),
    }
    try:
        return create_operation(
            session,
            kind=kind,
            principal=principal,
            request=request_document,
            idempotency_key=key,
            resource_type="remote_backup_target",
            resource_id=target.id,
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc


@router.post("/targets/{target_id}/test", status_code=202)
def test_target(
    target_id: str,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    target = _target(session, target_id)
    operation, created = _queue_target_operation(
        session=session,
        target=target,
        principal=principal,
        key=key,
        kind="backup.target.test",
    )
    if created:
        target.status = "testing"
        record_audit(
            session,
            principal=principal,
            action="backup.target.test",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="remote_backup_target",
            target_id=target.id,
        )
    return {"operation": operation_document(operation), "replayed": not created}


@router.put("/targets/{target_id}/schedule")
def update_schedule(
    target_id: str,
    payload: BackupScheduleRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    target = _target(session, target_id)
    if payload.enabled and target.status not in {"available", "degraded"}:
        raise Problem(
            409,
            "backup_target_not_ready",
            "Backup target not ready",
            "Test the target successfully before enabling automatic backups.",
        )
    target.schedule_json = {
        "enabled": payload.enabled,
        "interval_hours": payload.interval_hours,
    }
    record_audit(
        session,
        principal=principal,
        action="backup.schedule.update",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="remote_backup_target",
        target_id=target.id,
        details=target.schedule_json,
    )
    session.flush()
    return target_document(target)


@router.put("/targets/{target_id}/credentials")
def rotate_credentials(
    target_id: str,
    payload: BackupCredentialRotationRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
    secret_box: SecretBox = Depends(secret_box_from_request),
) -> dict[str, object]:
    target = _target(session, target_id)
    active = session.scalar(
        select(Operation.id).where(
            Operation.resource_type == "remote_backup_target",
            Operation.resource_id == target.id,
            Operation.status.in_(("queued", "running")),
        )
    )
    if active:
        raise Problem(
            409,
            "backup_target_busy",
            "Backup target busy",
            "Wait for the active backup operation before replacing credentials.",
        )
    ciphertext, fingerprint = encrypt_credentials(
        secret_box,
        target.id,
        access_key_id=payload.access_key_id.get_secret_value(),
        secret_access_key=payload.secret_access_key.get_secret_value(),
        session_token=(payload.session_token.get_secret_value() if payload.session_token else None),
    )
    previous_fingerprint = target.credential_fingerprint
    target.secret_ciphertext = ciphertext
    target.credential_fingerprint = fingerprint
    target.status = "not_tested"
    target.last_tested_at = None
    target.error_json = None
    target.schedule_json = {
        **target.schedule_json,
        "enabled": False,
    }
    record_audit(
        session,
        principal=principal,
        action="backup.target.credentials.rotate",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="remote_backup_target",
        target_id=target.id,
        details={
            "previous_fingerprint": previous_fingerprint,
            "credential_fingerprint": fingerprint,
            "automatic_backup_disabled": True,
        },
    )
    session.flush()
    return target_document(target)


@router.post("/targets/{target_id}/runs", status_code=202)
def start_backup(
    target_id: str,
    payload: BackupConfirmationRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    del payload
    target = _target(session, target_id)
    if not target.enabled or target.status not in {"available", "degraded"}:
        raise Problem(
            409,
            "backup_target_not_ready",
            "Backup target not ready",
            "Test the target successfully before starting a backup.",
        )
    active = session.scalar(
        select(Operation.id).where(
            Operation.resource_type == "remote_backup_target",
            Operation.resource_id == target.id,
            Operation.kind == "backup.control_plane",
            Operation.status.in_(("queued", "running")),
        )
    )
    if active:
        raise Problem(
            409,
            "backup_already_running",
            "Backup already running",
            "This target already has an active backup.",
        )
    operation, created = _queue_target_operation(
        session=session,
        target=target,
        principal=principal,
        key=key,
        kind="backup.control_plane",
        extra={"backup_kind": "control_plane", "secrets_included": False},
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
        record_audit(
            session,
            principal=principal,
            action="backup.control_plane.start",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="remote_backup_target",
            target_id=target.id,
            details={"operation_id": operation.id, "secrets_included": False},
        )
    run = session.get(RemoteBackupRun, operation.id)
    if run is None:
        raise Problem(
            500,
            "backup_run_missing",
            "Backup could not start",
            "The durable backup run could not be created.",
        )
    return {
        "operation": operation_document(operation),
        "run": run_document(run, operation),
        "replayed": not created,
    }


@router.get("/runs")
def list_runs(
    target_id: str | None = None,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    query = select(RemoteBackupRun, Operation).join(Operation, Operation.id == RemoteBackupRun.id)
    if target_id is not None:
        query = query.where(RemoteBackupRun.target_id == target_id)
    rows = session.execute(query.order_by(RemoteBackupRun.created_at.desc()).limit(100))
    return {"items": [run_document(run, operation) for run, operation in rows]}


@router.post("/runs/{run_id}/validate", status_code=202)
def validate_restore(
    run_id: str,
    payload: BackupRestoreValidationRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    del payload
    run = session.get(RemoteBackupRun, run_id)
    source_operation = session.get(Operation, run_id)
    if run is None or source_operation is None or source_operation.status != "succeeded":
        raise Problem(
            409,
            "backup_run_not_restorable",
            "Backup unavailable",
            "Only a successful backup can be validated.",
        )
    target = _target(session, run.target_id)
    if not run.object_key or not run.artifact_sha256:
        raise Problem(
            409,
            "backup_run_incomplete",
            "Backup unavailable",
            "The backup has no verified remote artifact.",
        )
    operation, created = _queue_target_operation(
        session=session,
        target=target,
        principal=principal,
        key=key,
        kind="backup.restore.validate",
        extra={
            "source_run_id": run.id,
            "object_key": run.object_key,
            "artifact_sha256": run.artifact_sha256,
        },
    )
    if created:
        operation.resource_type = "remote_backup_run"
        operation.resource_id = run.id
        record_audit(
            session,
            principal=principal,
            action="backup.restore.validate",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="remote_backup_run",
            target_id=run.id,
        )
    return {"operation": operation_document(operation), "replayed": not created}
