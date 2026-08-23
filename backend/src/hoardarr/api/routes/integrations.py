from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.api.dependencies import (
    authenticated_principal,
    database_session,
    idempotency_key,
    require_state_scope,
    secret_box_from_request,
)
from hoardarr.api.problem import Problem
from hoardarr.api.schemas import (
    IntegrationCreateRequest,
    IntegrationResolveRequest,
    ServarrApplyRequest,
)
from hoardarr.api.serializers import integration_document, operation_document
from hoardarr.audit.service import record_audit
from hoardarr.auth.service import Principal
from hoardarr.core.secrets import SecretBox
from hoardarr.db.models import IntegrationConnection, new_id
from hoardarr.integrations.servarr import ServarrError, normalize_mutation_plan
from hoardarr.integrations.url_policy import IntegrationTargetError, normalize_and_resolve_target
from hoardarr.operations.service import OperationConflict, create_operation, document_hash

router = APIRouter(prefix="/integrations", tags=["integrations"])
MEDIA_PRODUCTS = frozenset({"plex", "jellyfin", "emby"})


def _allow_risky_options(principal: Principal, *, allow_localhost: bool, verify_tls: bool) -> None:
    if (allow_localhost or not verify_tls) and not principal.is_admin:
        raise Problem(
            403,
            "admin_required",
            "Administrator required",
            "Localhost targets and disabled TLS verification require an administrator.",
        )


@router.post("/resolve")
def resolve_integration(
    payload: IntegrationResolveRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
) -> dict[str, object]:
    _allow_risky_options(principal, allow_localhost=payload.allow_localhost, verify_tls=True)
    try:
        target = normalize_and_resolve_target(
            payload.base_url,
            request.app.state.settings,
            allow_localhost=payload.allow_localhost,
        )
    except IntegrationTargetError as exc:
        raise Problem(422, "integration_target_rejected", "Target rejected", str(exc)) from exc
    return {
        "base_url": target.base_url,
        "hostname": target.hostname,
        "port": target.port,
        "resolved_ips": target.resolved_ips,
    }


@router.post("", status_code=202)
def create_integration(
    payload: IntegrationCreateRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
    secret_box: SecretBox = Depends(secret_box_from_request),
) -> dict[str, object]:
    _allow_risky_options(
        principal,
        allow_localhost=payload.allow_localhost,
        verify_tls=payload.verify_tls,
    )
    try:
        target = normalize_and_resolve_target(
            payload.base_url,
            request.app.state.settings,
            allow_localhost=payload.allow_localhost,
        )
    except IntegrationTargetError as exc:
        raise Problem(422, "integration_target_rejected", "Target rejected", str(exc)) from exc
    api_key = payload.api_key.get_secret_value()
    safe_request = {
        "name": payload.name,
        "product": payload.product,
        "base_url": target.base_url,
        "approved_ips": list(target.resolved_ips),
        "verify_tls": payload.verify_tls,
        "allow_localhost": payload.allow_localhost,
        "credential_fingerprint": secret_box.fingerprint(
            "media_api_key" if payload.product in MEDIA_PRODUCTS else "servarr_api_key",
            api_key,
        ),
    }
    connection_id = new_id()
    try:
        operation, created = create_operation(
            session,
            kind="media.discover" if payload.product in MEDIA_PRODUCTS else "servarr.discover",
            principal=principal,
            request=safe_request,
            idempotency_key=key,
            resource_type="integration_connection",
            resource_id=connection_id,
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        connection = IntegrationConnection(
            id=connection_id,
            adapter="media" if payload.product in MEDIA_PRODUCTS else "servarr",
            name=payload.name,
            expected_product=payload.product,
            base_url=target.base_url,
            approved_ips_json=list(target.resolved_ips),
            allow_localhost=payload.allow_localhost,
            api_key_ciphertext=secret_box.encrypt("integration_connection", connection_id, api_key),
            verify_tls=payload.verify_tls,
            status="pending",
        )
        session.add(connection)
        session.flush()
        record_audit(
            session,
            principal=principal,
            action="integration.create",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="integration_connection",
            target_id=connection.id,
            details={"product": payload.product, "base_url": target.base_url},
        )
    else:
        connection = session.get(IntegrationConnection, operation.resource_id)
        if connection is None:
            raise Problem(
                409,
                "idempotency_resource_missing",
                "Conflict",
                "The original integration resource is unavailable.",
            )
    return {
        "integration": integration_document(connection),
        "operation": operation_document(operation),
        "replayed": not created,
    }


@router.get("")
def list_integrations(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    connections = session.scalars(
        select(IntegrationConnection).order_by(IntegrationConnection.created_at)
    )
    return {"items": [integration_document(item) for item in connections]}


@router.get("/{connection_id}")
def get_integration(
    connection_id: str,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    connection = session.get(IntegrationConnection, connection_id)
    if connection is None:
        raise Problem(404, "integration_not_found", "Not found", "Integration was not found.")
    return integration_document(connection)


@router.post("/{connection_id}/refresh", status_code=202)
def refresh_integration(
    connection_id: str,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    connection = session.get(IntegrationConnection, connection_id)
    if connection is None:
        raise Problem(404, "integration_not_found", "Not found", "Integration was not found.")
    try:
        operation, created = create_operation(
            session,
            kind="media.discover" if connection.adapter == "media" else "servarr.discover",
            principal=principal,
            request={"connection_id": connection.id, "action": "refresh"},
            idempotency_key=key,
            resource_type="integration_connection",
            resource_id=connection.id,
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        record_audit(
            session,
            principal=principal,
            action="integration.refresh",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="integration_connection",
            target_id=connection.id,
        )
    return {"operation": operation_document(operation), "replayed": not created}


@router.post("/{connection_id}/changes/preview")
def preview_integration_changes(
    connection_id: str,
    payload: dict[str, object],
    _principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    connection = session.get(IntegrationConnection, connection_id)
    if connection is None:
        raise Problem(404, "integration_not_found", "Not found", "Integration was not found.")
    if connection.status != "connected":
        raise Problem(409, "integration_not_ready", "Not ready", "Refresh this integration first.")
    try:
        plan = normalize_mutation_plan(connection.expected_product, payload)
    except ServarrError as exc:
        raise Problem(422, exc.code, "Change plan rejected", str(exc)) from exc
    return {"connection_id": connection.id, "plan": plan, "plan_sha256": document_hash(plan)}


@router.post("/{connection_id}/changes/apply", status_code=202)
def apply_integration_changes(
    connection_id: str,
    payload: ServarrApplyRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    connection = session.get(IntegrationConnection, connection_id)
    if connection is None:
        raise Problem(404, "integration_not_found", "Not found", "Integration was not found.")
    try:
        plan = normalize_mutation_plan(connection.expected_product, payload.plan)
    except ServarrError as exc:
        raise Problem(422, exc.code, "Change plan rejected", str(exc)) from exc
    if plan != payload.plan or document_hash(plan) != payload.plan_sha256:
        raise Problem(
            409, "integration_plan_changed", "Plan changed", "Preview the exact changes again."
        )
    try:
        operation, created = create_operation(
            session,
            kind="servarr.apply",
            principal=principal,
            request={"schema_version": 1, "plan": plan, "plan_sha256": payload.plan_sha256},
            idempotency_key=key,
            resource_type="integration_connection",
            resource_id=connection.id,
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        record_audit(
            session,
            principal=principal,
            action="integration.apply",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="integration_connection",
            target_id=connection.id,
            details={"plan_sha256": payload.plan_sha256},
        )
    return {"operation": operation_document(operation), "replayed": not created}
