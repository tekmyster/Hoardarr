from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hoardarr.auth.service import Principal
from hoardarr.db.models import (
    ConnectivityService,
    ForeignMigrationJob,
    IntegrationConnection,
    Operation,
    OperationEvent,
    RemoteBackupRun,
    RemoteBackupTarget,
    StorageDrainJob,
    utc_now,
)


class OperationConflict(RuntimeError):
    pass


# These operations can cross a point where the host has already been changed.
# The current executors deliberately do not accept asynchronous cancellation,
# because killing a filesystem, share, or target command can leave an outcome
# that cannot be inferred safely. A queued mutation can still be cancelled;
# once claimed, its real result must be recorded instead of a false
# ``cancelled`` state.
NON_CANCELLABLE_AFTER_START = frozenset(
    {
        "storage.apply",
        "storage.maintenance",
        "storage.foreign.inspect",
        "storage.foreign.migrate",
        "storage.snapraid.replace",
        "storage.redundancy.apply",
        "storage.volume.create",
        "storage.volume.snapshot",
        "storage.volume.capacity",
        "storage.transfer",
        "hardware.locate",
        "connectivity.apply",
        "connectivity.remove",
        "backup.control_plane",
    }
)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def document_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def append_event(
    session: Session,
    operation: Operation,
    event_type: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> OperationEvent:
    sequence = session.execute(
        update(Operation)
        .where(Operation.id == operation.id)
        .values(event_sequence=Operation.event_sequence + 1)
        .returning(Operation.event_sequence)
        .execution_options(synchronize_session="fetch")
    ).scalar_one()
    event = OperationEvent(
        operation_id=operation.id,
        sequence=sequence,
        event_type=event_type,
        message=message,
        data_json=data or {},
    )
    session.add(event)
    return event


def create_operation(
    session: Session,
    *,
    kind: str,
    principal: Principal,
    request: dict[str, Any],
    idempotency_key: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    not_before: datetime | None = None,
) -> tuple[Operation, bool]:
    request_sha256 = document_hash(request)
    idempotency_query = select(Operation).where(
        Operation.actor_id == principal.user_id,
        Operation.kind == kind,
        Operation.idempotency_key == idempotency_key,
    )
    existing = session.scalar(idempotency_query)
    if existing is not None:
        if existing.request_sha256 != request_sha256:
            raise OperationConflict("the idempotency key was already used with another request")
        return existing, False
    operation = Operation(
        kind=kind,
        actor_type=principal.auth_type,
        actor_id=principal.user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        request_json=request,
        not_before=not_before,
    )
    try:
        # The savepoint lets a concurrent duplicate lose the unique-key race
        # without poisoning the route's surrounding transaction.
        with session.begin_nested():
            session.add(operation)
            session.flush()
    except IntegrityError as exc:
        existing = session.scalar(idempotency_query)
        if existing is None:
            raise
        if existing.request_sha256 != request_sha256:
            raise OperationConflict(
                "the idempotency key was already used with another request"
            ) from exc
        return existing, False
    append_event(session, operation, "queued", "Operation queued")
    return operation, True


def claim_next_operation(session: Session, worker_id: str) -> Operation | None:
    now = utc_now()
    candidate = session.scalar(
        select(Operation)
        .where(
            Operation.status == "queued",
            or_(Operation.not_before.is_(None), Operation.not_before <= now),
        )
        .order_by(Operation.created_at)
        .limit(1)
    )
    if candidate is None:
        return None
    claimed = session.execute(
        update(Operation)
        .where(Operation.id == candidate.id, Operation.status == "queued")
        .values(
            status="running",
            lease_owner=worker_id,
            leased_at=now,
            heartbeat_at=now,
            updated_at=now,
        )
    )
    if claimed.rowcount != 1:
        session.expire_all()
        return None
    session.refresh(candidate)
    append_event(session, candidate, "running", "Operation started")
    return candidate


def complete_operation(session: Session, operation: Operation, result: dict[str, Any]) -> None:
    operation.status = "succeeded"
    operation.result_json = result
    operation.error_json = None
    operation.heartbeat_at = utc_now()
    operation.updated_at = utc_now()
    append_event(session, operation, "succeeded", "Operation completed", result)


def fail_operation(
    session: Session,
    operation: Operation,
    *,
    code: str,
    message: str,
    needs_attention: bool = False,
) -> None:
    operation.status = "needs_attention" if needs_attention else "failed"
    operation.error_json = {"code": code, "message": message}
    operation.updated_at = utc_now()
    append_event(session, operation, operation.status, message, {"code": code})


def resume_storage_apply(session: Session, operation: Operation) -> None:
    if operation.kind != "storage.apply":
        raise OperationConflict("this operation is not a storage build")
    if operation.status != "needs_attention":
        raise OperationConflict("this storage build is not waiting for a safe resume")
    metadata = dict(operation.result_json or {})
    attempt = int(metadata.get("resume_attempt", 0)) + 1
    metadata.update({"resume_requested": True, "resume_attempt": attempt})
    operation.result_json = metadata
    operation.status = "queued"
    operation.cancel_requested = False
    operation.lease_owner = None
    operation.leased_at = None
    operation.heartbeat_at = None
    operation.not_before = None
    operation.updated_at = utc_now()
    append_event(
        session,
        operation,
        "resumed",
        "Storage build queued to resume from its durable checkpoint",
        {"attempt": attempt},
    )


def request_cancellation(session: Session, operation: Operation) -> None:
    now = utc_now()
    if (
        operation.kind == "hardware.locate"
        and operation.request_json.get("automatic_clear") is True
    ):
        raise OperationConflict("an automatic Locate clear cannot be cancelled")
    queued = session.execute(
        update(Operation)
        .where(Operation.id == operation.id, Operation.status == "queued")
        .values(status="cancelled", updated_at=now)
        .execution_options(synchronize_session="fetch")
    )
    if queued.rowcount == 1:
        mark_cancelled_resource(session, operation)
        append_event(session, operation, "cancelled", "Operation cancelled before execution")
        return
    session.refresh(operation)
    if operation.status == "running" and operation.kind in NON_CANCELLABLE_AFTER_START:
        raise OperationConflict(
            "this host-changing operation cannot be cancelled after execution has started"
        )
    running = session.execute(
        update(Operation)
        .where(
            Operation.id == operation.id,
            Operation.status == "running",
            Operation.cancel_requested.is_(False),
        )
        .values(cancel_requested=True, updated_at=now)
        .execution_options(synchronize_session="fetch")
    )
    if running.rowcount == 1:
        append_event(session, operation, "cancel_requested", "Cancellation requested")
        return
    session.refresh(operation)


def mark_cancelled_resource(session: Session, operation: Operation) -> None:
    if operation.kind == "backup.control_plane":
        run = session.get(RemoteBackupRun, operation.id)
        if run is not None:
            run.status = "cancelled"
            run.updated_at = utc_now()
        return
    if operation.kind == "storage.foreign.migrate":
        job = session.get(ForeignMigrationJob, operation.id)
        if job is not None:
            job.status = "cancelled"
            job.updated_at = utc_now()
        return
    if operation.kind == "storage.drain":
        job = session.get(StorageDrainJob, operation.id)
        if job is not None:
            job.status = "cancelled"
            job.updated_at = utc_now()
        return
    if operation.resource_type == "connectivity_service" and operation.resource_id:
        service = session.get(ConnectivityService, operation.resource_id)
        if service is not None:
            service.status = "error"
            service.last_error_json = {"code": "operation_cancelled"}
            service.updated_at = utc_now()
        return
    if (
        operation.kind not in {"servarr.discover", "media.discover"}
        or operation.resource_type != "integration_connection"
        or operation.resource_id is None
    ):
        return
    connection = session.get(IntegrationConnection, operation.resource_id)
    if connection is None or connection.status != "pending":
        return
    state = dict(connection.state_json)
    state["last_error"] = {"code": "operation_cancelled"}
    connection.state_json = state
    connection.status = "cancelled"
    connection.updated_at = utc_now()


def mark_failed_resource(session: Session, operation: Operation, code: str) -> None:
    if operation.kind in {"backup.target.test", "backup.control_plane", "backup.restore.validate"}:
        target_id = operation.request_json.get("target_id")
        target = session.get(RemoteBackupTarget, target_id) if isinstance(target_id, str) else None
        now = utc_now()
        if target is not None:
            target.status = "degraded" if target.last_success_at is not None else "error"
            target.last_error_json = {"code": code}
            target.updated_at = now
            if operation.kind == "backup.target.test":
                target.last_tested_at = now
        if operation.kind == "backup.control_plane":
            run = session.get(RemoteBackupRun, operation.id)
            if run is not None:
                run.status = "failed"
                run.phase = "failed"
                run.completed_at = now
                run.updated_at = now
        return
    if operation.resource_type == "connectivity_service" and operation.resource_id:
        service = session.get(ConnectivityService, operation.resource_id)
        if service is not None:
            service.status = "error"
            service.last_error_json = {"code": code}
            service.updated_at = utc_now()
        return
    if (
        operation.kind not in {"servarr.discover", "media.discover"}
        or operation.resource_type != "integration_connection"
        or operation.resource_id is None
    ):
        return
    connection = session.get(IntegrationConnection, operation.resource_id)
    if connection is None:
        return
    state = dict(connection.state_json)
    state["last_error"] = {"code": code}
    connection.state_json = state
    connection.status = "error"
    connection.last_checked_at = utc_now()
    connection.updated_at = utc_now()


def recover_stale_operations(
    session: Session,
    *,
    max_age_seconds: int = 120,
    max_age_by_kind: dict[str, int] | None = None,
) -> int:
    now = utc_now()
    oldest_cutoff = now - timedelta(
        seconds=max([max_age_seconds, *(max_age_by_kind or {}).values()])
    )
    stale = list(
        session.scalars(
            select(Operation).where(
                Operation.status == "running",
                Operation.heartbeat_at.is_not(None),
                Operation.heartbeat_at < oldest_cutoff,
            )
        )
    )
    if max_age_by_kind:
        # The broad database cutoff bounds the query; each operation still uses
        # its own kind-specific lease duration below.
        candidates = list(
            session.scalars(
                select(Operation).where(
                    Operation.status == "running",
                    Operation.heartbeat_at.is_not(None),
                )
            )
        )
        stale = [
            operation
            for operation in candidates
            if operation.heartbeat_at
            < (now.replace(tzinfo=None) if operation.heartbeat_at.tzinfo is None else now)
            - timedelta(seconds=max_age_by_kind.get(operation.kind, max_age_seconds))
        ]
    for operation in stale:
        if operation.kind in {
            "storage.drain",
            "storage.foreign.migrate",
            "backup.control_plane",
        }:
            job = (
                session.get(StorageDrainJob, operation.id)
                if operation.kind == "storage.drain"
                else (
                    session.get(ForeignMigrationJob, operation.id)
                    if operation.kind == "storage.foreign.migrate"
                    else session.get(RemoteBackupRun, operation.id)
                )
            )
            if job is not None and job.status != "succeeded":
                job.status = "queued"
                if hasattr(job, "pause_requested"):
                    job.pause_requested = False
                job.updated_at = utc_now()
                operation.status = "queued"
                operation.lease_owner = None
                operation.leased_at = None
                operation.heartbeat_at = None
                operation.updated_at = utc_now()
                append_event(
                    session,
                    operation,
                    "recovered",
                    "Interrupted durable storage job queued from its last checkpoint",
                )
                continue
        mark_failed_resource(session, operation, "worker_interrupted")
        fail_operation(
            session,
            operation,
            code="worker_interrupted",
            message="Worker stopped before the operation outcome was known",
            needs_attention=True,
        )
    return len(stale)
