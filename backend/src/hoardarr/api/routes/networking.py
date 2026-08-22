from __future__ import annotations

from fastapi import APIRouter, Depends

from hoardarr.api.dependencies import authenticated_principal, require_state_scope
from hoardarr.api.networking_schemas import (
    ManagedNetworkApplyRequest,
    ManagedNetworkConfirmRequest,
    ManagedNetworkPlanRequest,
)
from hoardarr.api.problem import Problem
from hoardarr.auth.service import Principal
from hoardarr.networking.executor import (
    NetworkFailure,
    apply,
    build_plan,
    confirm,
    status,
)

router = APIRouter(prefix="/networking", tags=["networking"])


def _problem(exc: NetworkFailure) -> Problem:
    status_code = 409 if exc.code.endswith(("changed", "pending")) else 422
    return Problem(status_code, exc.code, "Networking request failed", exc.message)


@router.get("")
def networking_status(
    _principal: Principal = Depends(authenticated_principal),
) -> dict[str, object]:
    return status()


@router.post("/plan")
def networking_plan(
    payload: ManagedNetworkPlanRequest,
    _principal: Principal = Depends(require_state_scope("operate")),
) -> dict[str, object]:
    try:
        return build_plan(payload.configuration, payload.changed_components)
    except NetworkFailure as exc:
        raise _problem(exc) from exc


@router.post("/apply")
def networking_apply(
    payload: ManagedNetworkApplyRequest,
    _principal: Principal = Depends(require_state_scope("admin")),
) -> dict[str, object]:
    try:
        return apply(payload.configuration, payload.plan_sha256, payload.changed_components)
    except NetworkFailure as exc:
        raise _problem(exc) from exc


@router.post("/confirm")
def networking_confirm(
    payload: ManagedNetworkConfirmRequest,
    _principal: Principal = Depends(require_state_scope("admin")),
) -> dict[str, object]:
    try:
        return confirm(payload.token)
    except NetworkFailure as exc:
        raise _problem(exc) from exc
