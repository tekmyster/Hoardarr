from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends

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
    activate_pending,
    apply,
    build_plan,
    confirm,
    finalize_confirmation,
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
    background_tasks: BackgroundTasks,
    _principal: Principal = Depends(require_state_scope("admin")),
) -> dict[str, object]:
    try:
        pending = apply(
            payload.configuration,
            payload.plan_sha256,
            payload.changed_components,
            activate=False,
        )
        background_tasks.add_task(activate_pending, str(pending["token"]))
        return pending
    except NetworkFailure as exc:
        raise _problem(exc) from exc


@router.post("/confirm")
def networking_confirm(
    payload: ManagedNetworkConfirmRequest,
    background_tasks: BackgroundTasks,
    _principal: Principal = Depends(require_state_scope("admin")),
) -> dict[str, object]:
    try:
        result = confirm(payload.token, finalize=False)
        background_tasks.add_task(finalize_confirmation, payload.token)
        return result
    except NetworkFailure as exc:
        raise _problem(exc) from exc
