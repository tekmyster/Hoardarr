from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from hoardarr.api.dependencies import authenticated_principal, database_session, require_state_scope
from hoardarr.api.problem import Problem
from hoardarr.api.schemas import HAConfigurationRequest, HAPeerHeartbeatRequest
from hoardarr.auth.service import Principal
from hoardarr.ha.service import (
    HAError,
    configuration,
    configure,
    event_documents,
    record_heartbeat,
    status_document,
)

router = APIRouter(prefix="/ha", tags=["high-availability"])


def _problem(exc: HAError) -> Problem:
    status = 409 if exc.code.endswith(("conflict", "mismatch", "not_configured")) else 422
    return Problem(status, exc.code, "HA request rejected", str(exc))


@router.get("")
def ha_status(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    item = configuration(session)
    return {**status_document(item), "events": event_documents(session, item)}


@router.put("/configuration")
def update_ha_configuration(
    payload: HAConfigurationRequest,
    _principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        item = configure(session, payload.model_dump())
    except HAError as exc:
        raise _problem(exc) from exc
    return {**status_document(item), "events": event_documents(session, item)}


@router.post("/heartbeat")
def accept_peer_heartbeat(
    payload: HAPeerHeartbeatRequest,
    _principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        item = record_heartbeat(session, payload.model_dump())
    except HAError as exc:
        raise _problem(exc) from exc
    return status_document(item)
