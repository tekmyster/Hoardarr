from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.api.dependencies import (
    authenticated_principal,
    database_session,
    require_state_scope,
    settings_from_request,
)
from hoardarr.api.problem import Problem
from hoardarr.api.serializers import event_document, operation_document
from hoardarr.audit.service import record_audit
from hoardarr.auth.service import Principal
from hoardarr.core.config import Settings
from hoardarr.db.models import (
    ForeignMigrationJob,
    Operation,
    OperationEvent,
    StorageDrainJob,
    utc_now,
)
from hoardarr.operations.service import (
    OperationConflict,
    request_cancellation,
    resume_storage_apply,
)
from hoardarr.storage.client import StorageExecutorError, storage_operation_status
from hoardarr.storage.drain_worker import (
    DrainExecutionError,
    request_drain_pause,
    resume_drain,
)
from hoardarr.storage.foreign_migration_worker import (
    ForeignMigrationError,
    request_foreign_migration_pause,
    resume_foreign_migration,
)
from hoardarr.updates.service import UpdatePaths

router = APIRouter(prefix="/operations", tags=["operations"])


def visible_operation(session: Session, operation_id: str, principal: Principal) -> Operation:
    operation = session.get(Operation, operation_id)
    if operation is None or (not principal.is_admin and operation.actor_id != principal.user_id):
        raise Problem(404, "operation_not_found", "Not found", "Operation was not found.")
    return operation


def drain_progress_document(job: StorageDrainJob, operation: Operation) -> dict[str, object]:
    if job.phase in {"preflight", "inventory", "paused"}:
        percent = 0 if job.phase == "preflight" else 5
    elif job.phase == "copying":
        percent = 5 + round(60 * job.bytes_copied / max(job.bytes_total, 1))
    elif job.phase == "verifying":
        percent = 65 + round(25 * job.files_verified / max(job.files_total, 1))
    elif job.phase == "finalizing":
        percent = 95
    else:
        percent = 100 if job.status == "succeeded" else 0
    elapsed_seconds = 0
    if job.started_at is not None:
        started = job.started_at
        now = utc_now()
        if started.tzinfo is None:
            started = started.replace(tzinfo=now.tzinfo)
        elapsed_seconds = max(int((now - started).total_seconds()), 0)
    bytes_per_second = job.bytes_copied / elapsed_seconds if elapsed_seconds else 0
    remaining_bytes = max(job.bytes_total - job.bytes_copied, 0)
    remaining_seconds = round(remaining_bytes / bytes_per_second) if bytes_per_second > 0 else None
    return {
        "operation_id": operation.id,
        "state": job.status,
        "phase": job.phase,
        "completed_steps": job.files_verified,
        "total_steps": job.files_total,
        "percent": min(max(percent, 0), 100),
        "completed_actions": [],
        "notices": [],
        "current_action": {
            "id": job.current_relative_path,
            "type": job.phase,
            "progress": {
                "kind": "bytes",
                "device": job.source_backend_id,
                "processed_bytes": job.bytes_copied,
                "total_bytes": job.bytes_total,
                "percent": round(100 * job.bytes_copied / max(job.bytes_total, 1)),
                "elapsed_seconds": elapsed_seconds,
                "bytes_per_second": round(bytes_per_second),
                "estimated_seconds_remaining": remaining_seconds,
            },
        }
        if job.current_relative_path
        else None,
        "estimate": {
            "scope": "storage drain copy",
            "estimated_seconds_remaining": remaining_seconds,
            "estimated_completion_at": int(utc_now().timestamp()) + remaining_seconds,
            "remaining_bytes": remaining_bytes,
        }
        if remaining_seconds is not None
        else None,
        "updated_at": int(job.updated_at.timestamp()) if job.updated_at else None,
        "files": {
            "total": job.files_total,
            "copied": job.files_copied,
            "verified": job.files_verified,
        },
        "bytes": {"total": job.bytes_total, "copied": job.bytes_copied},
        "report": job.report_json if job.status == "succeeded" else None,
    }


def foreign_migration_progress_document(
    job: ForeignMigrationJob, operation: Operation
) -> dict[str, object]:
    if job.phase in {"preflight", "inventory", "paused"}:
        percent = 0 if job.phase == "preflight" else 5
    elif job.phase == "copying":
        percent = 5 + round(70 * job.bytes_copied / max(job.bytes_total, 1))
    elif job.phase == "verifying":
        percent = 75 + round(20 * job.files_verified / max(job.files_total, 1))
    elif job.phase == "finalizing":
        percent = 98
    else:
        percent = 100 if job.status == "succeeded" else 0
    elapsed_seconds = 0
    if job.started_at is not None:
        started = job.started_at
        now = utc_now()
        if started.tzinfo is None:
            started = started.replace(tzinfo=now.tzinfo)
        elapsed_seconds = max(int((now - started).total_seconds()), 0)
    bytes_per_second = job.bytes_copied / elapsed_seconds if elapsed_seconds else 0
    remaining_bytes = max(job.bytes_total - job.bytes_copied, 0)
    remaining_seconds = round(remaining_bytes / bytes_per_second) if bytes_per_second > 0 else None
    return {
        "operation_id": operation.id,
        "state": job.status,
        "phase": job.phase,
        "completed_steps": job.files_verified,
        "total_steps": job.files_total,
        "percent": min(max(percent, 0), 100),
        "completed_actions": [],
        "notices": [],
        "current_action": {
            "id": job.current_relative_path,
            "type": job.phase,
            "progress": {
                "kind": "bytes",
                "device": job.destination_backend_id,
                "processed_bytes": job.bytes_copied,
                "total_bytes": job.bytes_total,
                "percent": round(100 * job.bytes_copied / max(job.bytes_total, 1)),
                "elapsed_seconds": elapsed_seconds,
                "bytes_per_second": round(bytes_per_second),
                "estimated_seconds_remaining": remaining_seconds,
            },
        }
        if job.current_relative_path
        else None,
        "estimate": {
            "scope": "foreign storage copy",
            "estimated_seconds_remaining": remaining_seconds,
            "estimated_completion_at": int(utc_now().timestamp()) + remaining_seconds,
            "remaining_bytes": remaining_bytes,
        }
        if remaining_seconds is not None
        else None,
        "updated_at": int(job.updated_at.timestamp()) if job.updated_at else None,
        "files": {
            "total": job.files_total,
            "copied": job.files_copied,
            "verified": job.files_verified,
            "reused": job.files_reused,
        },
        "bytes": {"total": job.bytes_total, "copied": job.bytes_copied},
        "report": job.report_json if job.status == "succeeded" else None,
    }


@router.get("")
def list_operations(
    principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    query = select(Operation).order_by(Operation.created_at.desc()).limit(100)
    if not principal.is_admin:
        query = query.where(Operation.actor_id == principal.user_id)
    return {"items": [operation_document(item) for item in session.scalars(query)]}


@router.get("/{operation_id}")
def get_operation(
    operation_id: str,
    principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    return operation_document(visible_operation(session, operation_id, principal))


@router.get("/{operation_id}/events")
def get_operation_events(
    operation_id: str,
    principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    operation = visible_operation(session, operation_id, principal)
    events = session.scalars(
        select(OperationEvent)
        .where(OperationEvent.operation_id == operation.id)
        .order_by(OperationEvent.sequence)
    )
    return {"items": [event_document(event) for event in events]}


@router.get("/{operation_id}/progress")
def get_operation_progress(
    operation_id: str,
    principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
    settings: Settings = Depends(settings_from_request),
) -> dict[str, object]:
    operation = visible_operation(session, operation_id, principal)
    if operation.kind == "storage.drain":
        job = session.get(StorageDrainJob, operation.id)
        if job is None:
            raise Problem(
                503,
                "drain_job_missing",
                "Drain progress unavailable",
                "The durable drain checkpoint is unavailable.",
            )
        return drain_progress_document(job, operation)
    if operation.kind == "storage.foreign.migrate":
        migration_job = session.get(ForeignMigrationJob, operation.id)
        if migration_job is None:
            raise Problem(
                503,
                "foreign_migration_job_missing",
                "Migration progress unavailable",
                "The durable migration checkpoint is unavailable.",
            )
        return foreign_migration_progress_document(migration_job, operation)
    if operation.kind == "update.apply":
        current = settings.frontend_dir.parent
        paths = UpdatePaths(
            releases=current.parent / "releases",
            current=current,
            state=settings.secret_key_file.parent,
            config=settings.update_trust_file.parent,
            trust=settings.update_trust_file,
            backup=settings.update_artifact_root.parent / "update-backups",
        )
        try:
            journal = json.loads(paths.journal.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            journal = {}
        expected_release = operation.request_json.get("metadata", {}).get("release_id")
        if journal.get("release_id") != expected_release:
            journal = {}
        return {
            "state": journal.get("state", operation.status),
            "phase": journal.get("phase", operation.status.replace("_", " ")),
            "percent": journal.get("percent", 100 if operation.status == "succeeded" else 0),
            "estimated_seconds_remaining": None,
        }
    if operation.kind not in {
        "storage.apply",
        "storage.maintenance",
        "storage.foreign.inspect",
        "storage.snapraid.replace",
    }:
        raise Problem(
            409,
            "progress_not_supported",
            "Progress is unavailable",
            "This operation does not expose detailed storage progress.",
        )
    try:
        progress = storage_operation_status(
            settings.storage_status_socket,
            operation_id=operation.id,
            timeout_seconds=min(5.0, settings.storage_executor_timeout_seconds),
        )
    except StorageExecutorError as exc:
        raise Problem(503, exc.code, "Storage progress unavailable", str(exc)) from exc
    if progress.get("state") == "waiting" and operation.status in {
        "succeeded",
        "failed",
        "cancelled",
        "needs_attention",
    }:
        error = operation.error_json if isinstance(operation.error_json, dict) else {}
        progress = {
            **progress,
            "state": operation.status,
            "phase": error.get("detail")
            or error.get("message")
            or f"Storage ended with status {operation.status}.",
        }
    return progress


@router.post("/{operation_id}/pause", status_code=202)
def pause_operation(
    operation_id: str,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    operation = visible_operation(session, operation_id, principal)
    try:
        if operation.kind == "storage.foreign.migrate":
            request_foreign_migration_pause(session, operation)
        else:
            request_drain_pause(session, operation)
    except (DrainExecutionError, ForeignMigrationError) as exc:
        raise Problem(409, exc.code, "Operation cannot be paused", exc.safe_message) from exc
    record_audit(
        session,
        principal=principal,
        action="operation.pause",
        outcome="accepted",
        correlation_id=request.state.request_id,
        target_type="operation",
        target_id=operation.id,
    )
    return operation_document(operation)


@router.post("/{operation_id}/resume", status_code=202)
def resume_operation(
    operation_id: str,
    request: Request,
    settings: Settings = Depends(settings_from_request),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    operation = visible_operation(session, operation_id, principal)
    try:
        if operation.kind == "storage.apply":
            checkpoint_state = None
            if operation.status == "failed":
                try:
                    checkpoint = storage_operation_status(
                        settings.storage_status_socket,
                        operation_id=operation.id,
                        timeout_seconds=min(5.0, settings.storage_executor_timeout_seconds),
                    )
                except StorageExecutorError as exc:
                    raise Problem(
                        503,
                        exc.code,
                        "Storage checkpoint unavailable",
                        "Hoardarr could not verify whether this legacy failed operation has a "
                        "safe executor checkpoint.",
                    ) from exc
                checkpoint_state = str(checkpoint.get("state", ""))
            resume_storage_apply(
                session,
                operation,
                checkpoint_state=checkpoint_state,
            )
        elif operation.kind == "storage.foreign.migrate":
            resume_foreign_migration(session, operation)
        else:
            resume_drain(session, operation)
    except (DrainExecutionError, ForeignMigrationError, OperationConflict) as exc:
        code = getattr(exc, "code", "operation_not_resumable")
        message = getattr(exc, "safe_message", str(exc))
        raise Problem(409, code, "Operation cannot be resumed", message) from exc
    record_audit(
        session,
        principal=principal,
        action="operation.resume",
        outcome="accepted",
        correlation_id=request.state.request_id,
        target_type="operation",
        target_id=operation.id,
    )
    return operation_document(operation)


@router.post("/{operation_id}/cancel", status_code=202)
def cancel_operation(
    operation_id: str,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    operation = visible_operation(session, operation_id, principal)
    try:
        request_cancellation(session, operation)
    except OperationConflict as exc:
        raise Problem(
            409,
            "operation_not_cancellable",
            "Operation cannot be cancelled",
            str(exc),
        ) from exc
    record_audit(
        session,
        principal=principal,
        action="operation.cancel",
        outcome="accepted",
        correlation_id=request.state.request_id,
        target_type="operation",
        target_id=operation.id,
    )
    return operation_document(operation)
