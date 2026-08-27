from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hoardarr.api.dependencies import (
    authenticated_principal,
    database_session,
    idempotency_key,
    require_state_scope,
    secret_box_from_request,
)
from hoardarr.api.problem import Problem
from hoardarr.api.schemas import ConnectivityDeleteRequest, ConnectivityServiceRequest
from hoardarr.api.serializers import connectivity_document as _connectivity_document
from hoardarr.api.serializers import operation_document
from hoardarr.audit.service import record_audit
from hoardarr.auth.service import Principal
from hoardarr.connectivity.executor import ExecutorFailure, capabilities, resolve_fcoe_interfaces
from hoardarr.connectivity.service import (
    ManagedZvolBindingError,
    config_hash,
    normalize_connectivity_request,
    resolve_managed_zvol_binding,
)
from hoardarr.core.secrets import SecretBox
from hoardarr.db.models import ConnectivityService, Operation, new_id, utc_now
from hoardarr.operations.service import OperationConflict, create_operation

router = APIRouter(prefix="/connectivity", tags=["connectivity"])
SECRET_RECORD = "connectivity_service"


def connectivity_document(service: ConnectivityService) -> dict[str, object]:
    document = _connectivity_document(service)
    config = dict(service.config_json)
    binding = config.get("managed_zvol_binding")
    if isinstance(binding, dict):
        config["managed_zvol_binding"] = {
            key: value
            for key, value in binding.items()
            if key not in {"stable_identity", "provider_resource_id", "device_path"}
        }
    document["config"] = config
    return document


def _normalized_config(
    payload: ConnectivityServiceRequest,
    *,
    require_secret: bool,
    session: Session,
    expected_binding: object | None = None,
) -> dict[str, object]:
    binding = None
    if payload.storage_volume_id is not None:
        binding = resolve_managed_zvol_binding(
            session,
            storage_volume_id=payload.storage_volume_id,
            expected=expected_binding,
        )
    config = normalize_connectivity_request(
        payload,
        require_secret=require_secret,
        managed_zvol_binding=binding,
    )
    if payload.protocol == "fcoe":
        try:
            resolved = resolve_fcoe_interfaces(config["interfaces"])
        except ExecutorFailure as exc:
            raise Problem(422, exc.code, "FCoE port unavailable", str(exc)) from exc
        config["interfaces"] = resolved["interfaces"]
        config["target_wwpns"] = resolved["target_wwpns"]
    return config


def _pending_operation(session: Session, service_id: str) -> Operation | None:
    return session.scalar(
        select(Operation).where(
            Operation.resource_type == "connectivity_service",
            Operation.resource_id == service_id,
            Operation.status.in_(("queued", "running")),
        )
    )


@router.get("/capabilities")
def get_capabilities(
    _request: Request,
    _principal: Principal = Depends(authenticated_principal),
) -> dict[str, object]:
    result = capabilities()
    result["service_available"] = True
    return result


@router.get("")
def list_services(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    services = session.scalars(
        select(ConnectivityService).order_by(ConnectivityService.protocol, ConnectivityService.name)
    )
    return {"items": [connectivity_document(item) for item in services]}


@router.post("", status_code=202)
def create_service(
    payload: ConnectivityServiceRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
    secret_box: SecretBox = Depends(secret_box_from_request),
) -> dict[str, object]:
    try:
        config = _normalized_config(payload, require_secret=True, session=session)
    except ManagedZvolBindingError as exc:
        raise Problem(422, exc.code, "Managed volume unavailable", str(exc)) from exc
    except ValueError as exc:
        raise Problem(422, "connectivity_invalid", "Invalid settings", str(exc)) from exc
    service_id = new_id()
    generated_password: str | None = None
    password: str | None = None
    if payload.protocol == "iscsi" and payload.chap_enabled:
        if payload.generate_chap_password:
            generated_password = secrets.token_urlsafe(24)
            password = generated_password
        elif payload.chap_password is not None:
            password = payload.chap_password.get_secret_value()
    digest = config_hash(config)
    try:
        operation, created = create_operation(
            session,
            kind="connectivity.apply",
            principal=principal,
            request={"service_id": service_id, "config_sha256": digest},
            idempotency_key=key,
            resource_type="connectivity_service",
            resource_id=service_id,
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        service = ConnectivityService(
            id=service_id,
            protocol=payload.protocol,
            name=payload.name,
            config_json=config,
            config_sha256=digest,
            secret_ciphertext=secret_box.encrypt(SECRET_RECORD, service_id, password)
            if password
            else None,
            status="pending",
        )
        session.add(service)
        try:
            session.flush()
        except IntegrityError as exc:
            raise Problem(
                409,
                "connectivity_name_in_use",
                "Name in use",
                "That name already exists for this protocol.",
            ) from exc
        record_audit(
            session,
            principal=principal,
            action="connectivity.create",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="connectivity_service",
            target_id=service.id,
            details={"protocol": service.protocol, "name": service.name},
        )
    else:
        service = session.get(ConnectivityService, operation.resource_id)
        if service is None:
            raise Problem(
                409,
                "idempotency_resource_missing",
                "Conflict",
                "The original service is unavailable.",
            )
    response: dict[str, object] = {
        "service": connectivity_document(service),
        "operation": operation_document(operation),
        "replayed": not created,
    }
    if generated_password is not None and created:
        response["generated_password"] = generated_password
    return response


@router.put("/{service_id}", status_code=202)
def update_service(
    service_id: str,
    payload: ConnectivityServiceRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
    secret_box: SecretBox = Depends(secret_box_from_request),
) -> dict[str, object]:
    service = session.get(ConnectivityService, service_id)
    if service is None:
        raise Problem(
            404, "connectivity_not_found", "Not found", "Connectivity service was not found."
        )
    if _pending_operation(session, service_id):
        raise Problem(409, "connectivity_busy", "Busy", "Connectivity is already changing.")
    if payload.protocol != service.protocol:
        raise Problem(
            422,
            "connectivity_protocol_changed",
            "Invalid setting",
            "Remove this connection before changing its type.",
        )
    try:
        existing_binding = service.config_json.get("managed_zvol_binding")
        config = _normalized_config(
            payload,
            require_secret=False,
            session=session,
            expected_binding=existing_binding,
        )
        replacement_binding = config.get("managed_zvol_binding")
        if (existing_binding is None) != (replacement_binding is None):
            raise Problem(
                409,
                "connectivity_recreate_required",
                "Recreate required",
                "Remove and recreate this connection to change its backing kind.",
            )
    except ManagedZvolBindingError as exc:
        if exc.code == "connectivity_managed_zvol_changed":
            raise Problem(
                409,
                "connectivity_recreate_required",
                "Recreate required",
                "Remove and recreate this connection to change its backing volume.",
            ) from exc
        raise Problem(422, exc.code, "Managed volume unavailable", str(exc)) from exc
    except ValueError as exc:
        raise Problem(422, "connectivity_invalid", "Invalid settings", str(exc)) from exc
    password = payload.chap_password.get_secret_value() if payload.chap_password else None
    generated_password: str | None = None
    if payload.protocol == "iscsi" and payload.chap_enabled and payload.generate_chap_password:
        generated_password = secrets.token_urlsafe(24)
        password = generated_password
    if (
        payload.protocol == "iscsi"
        and payload.chap_enabled
        and password is None
        and service.secret_ciphertext is None
    ):
        raise Problem(
            422,
            "connectivity_secret_required",
            "Password required",
            "Set or generate a CHAP password.",
        )
    digest = config_hash(config)
    try:
        operation, created = create_operation(
            session,
            kind="connectivity.apply",
            principal=principal,
            request={"service_id": service_id, "config_sha256": digest},
            idempotency_key=key,
            resource_type="connectivity_service",
            resource_id=service_id,
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        service.protocol = payload.protocol
        service.name = payload.name
        service.config_json = config
        service.config_sha256 = digest
        if password is not None:
            service.secret_ciphertext = secret_box.encrypt(SECRET_RECORD, service.id, password)
        elif payload.protocol != "iscsi" or not payload.chap_enabled:
            service.secret_ciphertext = None
        service.status = "pending"
        service.last_error_json = None
        service.updated_at = utc_now()
        try:
            session.flush()
        except IntegrityError as exc:
            raise Problem(
                409,
                "connectivity_name_in_use",
                "Name in use",
                "That name already exists for this protocol.",
            ) from exc
        record_audit(
            session,
            principal=principal,
            action="connectivity.update",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="connectivity_service",
            target_id=service.id,
            details={"protocol": service.protocol, "name": service.name},
        )
    response: dict[str, object] = {
        "service": connectivity_document(service),
        "operation": operation_document(operation),
        "replayed": not created,
    }
    if generated_password is not None and created:
        response["generated_password"] = generated_password
    return response


@router.delete("/{service_id}", status_code=202)
def delete_service(
    service_id: str,
    payload: ConnectivityDeleteRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    service = session.get(ConnectivityService, service_id)
    if service is None:
        raise Problem(
            404, "connectivity_not_found", "Not found", "Connectivity service was not found."
        )
    if _pending_operation(session, service_id):
        raise Problem(409, "connectivity_busy", "Busy", "Connectivity is already changing.")
    if payload.delete_backing_data and service.protocol not in {"iscsi", "fcoe"}:
        raise Problem(
            422,
            "connectivity_delete_invalid",
            "Invalid setting",
            "This service has no backing file.",
        )
    if payload.delete_backing_data and "managed_zvol_binding" in service.config_json:
        raise Problem(
            422,
            "connectivity_managed_zvol_delete_forbidden",
            "Backing data preserved",
            "Managed ZFS volume data cannot be deleted through connectivity removal.",
        )
    try:
        operation, created = create_operation(
            session,
            kind="connectivity.remove",
            principal=principal,
            request={
                "service_id": service_id,
                "config_sha256": service.config_sha256,
                "delete_backing_data": payload.delete_backing_data,
            },
            idempotency_key=key,
            resource_type="connectivity_service",
            resource_id=service_id,
        )
    except OperationConflict as exc:
        raise Problem(409, "idempotency_conflict", "Conflict", str(exc)) from exc
    if created:
        service.status = "removing"
        service.updated_at = utc_now()
        record_audit(
            session,
            principal=principal,
            action="connectivity.remove",
            outcome="accepted",
            correlation_id=request.state.request_id,
            target_type="connectivity_service",
            target_id=service.id,
            details={
                "protocol": service.protocol,
                "delete_backing_data": payload.delete_backing_data,
            },
        )
    return {
        "service": connectivity_document(service),
        "operation": operation_document(operation),
        "replayed": not created,
    }
