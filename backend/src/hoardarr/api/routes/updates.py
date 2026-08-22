from __future__ import annotations

import shutil
from copy import deepcopy

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hoardarr import __version__
from hoardarr.api.dependencies import (
    database_session,
    idempotency_key,
    require_scope,
    require_state_scope,
)
from hoardarr.api.problem import Problem
from hoardarr.api.schemas import UpdateApplyRequest
from hoardarr.api.serializers import operation_document
from hoardarr.audit.service import record_audit
from hoardarr.auth.service import Principal
from hoardarr.db.models import AddonInstallation, Operation, UpdateState, utc_now
from hoardarr.operations.service import OperationConflict, create_operation, document_hash
from hoardarr.updates.service import UpdateError, fetch_signed_metadata, preflight_update

router = APIRouter(prefix="/updates", tags=["updates"])


def _state(session: Session, channel: str) -> UpdateState:
    state = session.get(UpdateState, "system")
    if state is None:
        state = UpdateState(id="system", channel=channel)
        session.add(state)
        session.flush()
    return state


@router.get("/status")
def update_status(
    request: Request,
    _principal: Principal = Depends(require_scope("read")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    state = _state(session, request.app.state.settings.update_channel)
    operation = session.get(Operation, state.last_operation_id) if state.last_operation_id else None
    metadata = deepcopy(state.latest_metadata_json)
    return {
        "current_version": __version__,
        "latest_version": metadata.get("version") if metadata else None,
        "channel": state.channel,
        "metadata_sha256": state.metadata_sha256,
        "last_checked_at": state.last_checked_at,
        "last_error": deepcopy(state.last_error_json),
        "operation": operation_document(operation) if operation else None,
    }


@router.post("/check")
def check_update(
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    settings = request.app.state.settings
    state = _state(session, settings.update_channel)
    try:
        metadata = fetch_signed_metadata(
            settings.update_metadata_url,
            settings.update_signature_url,
            trust_path=settings.update_trust_file,
            channel=settings.update_channel,
        )
    except UpdateError as exc:
        state.last_error_json = {"code": exc.code, "message": str(exc)}
        state.last_checked_at = utc_now()
        raise Problem(502, exc.code, "Update check failed", str(exc)) from exc
    state.channel = settings.update_channel
    state.latest_metadata_json = metadata
    state.metadata_sha256 = document_hash(metadata)
    state.last_checked_at = utc_now()
    state.last_error_json = None
    addons = [
        {
            "name": item.name,
            "enabled": item.state == "enabled",
            "api_min": item.manifest_json["api"]["minimum"],
            "api_max": item.manifest_json["api"]["maximum"],
        }
        for item in session.scalars(select(AddonInstallation))
    ]
    active = session.scalar(
        select(func.count(Operation.id)).where(
            Operation.kind.in_(("storage.apply", "storage.transfer")),
            Operation.status.in_(("queued", "running")),
        )
    )
    free = shutil.disk_usage(settings.update_artifact_root.parent).free
    result = preflight_update(
        metadata,
        current_version=__version__,
        active_storage_operations=int(active or 0),
        free_bytes=free,
        installed_addons=addons,
    )
    record_audit(
        session,
        principal=principal,
        action="update.check",
        outcome="completed",
        correlation_id=request.state.request_id,
        target_type="update",
        details={"channel": state.channel, "version": metadata["version"]},
    )
    return {**result, "metadata_sha256": state.metadata_sha256}


@router.post("/apply", status_code=202)
def apply_update(
    payload: UpdateApplyRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    state = _state(session, request.app.state.settings.update_channel)
    if state.latest_metadata_json is None or state.metadata_sha256 != payload.metadata_sha256:
        raise Problem(409, "update_metadata_changed", "Update changed", "Check for updates again.")
    active = session.scalar(
        select(func.count(Operation.id)).where(
            Operation.kind.in_(("storage.apply", "storage.transfer")),
            Operation.status.in_(("queued", "running")),
        )
    )
    if active:
        raise Problem(
            409, "storage_active", "Storage is active", "Wait for storage work to finish."
        )
    try:
        operation, created = create_operation(
            session,
            kind="update.apply",
            principal=principal,
            request={
                "schema_version": 1,
                "metadata_sha256": state.metadata_sha256,
                "metadata": deepcopy(state.latest_metadata_json),
            },
            idempotency_key=key,
            resource_type="update",
            resource_id="system",
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    state.last_operation_id = operation.id
    record_audit(
        session,
        principal=principal,
        action="update.apply",
        outcome="accepted",
        correlation_id=request.state.request_id,
        target_type="update",
        target_id=operation.id,
        details={"version": state.latest_metadata_json["version"]},
    )
    return {"operation": operation_document(operation), "replayed": not created}
