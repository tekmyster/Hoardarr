from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from hoardarr.api.dependencies import (
    authenticated_principal,
    database_session,
    require_state_scope,
    secret_box_from_request,
)
from hoardarr.api.problem import Problem
from hoardarr.api.serializers import operation_document
from hoardarr.audit.service import record_audit
from hoardarr.auth.service import Principal
from hoardarr.automation.webhooks import (
    EVENT_TYPES,
    WEBHOOK_RECORD_TYPE,
    delivery_document,
    endpoint_document,
    queue_event,
)
from hoardarr.core.secrets import SecretBox
from hoardarr.db.models import (
    HardwareSnapshot,
    Operation,
    PhysicalDisk,
    WebhookDelivery,
    WebhookEndpoint,
    new_id,
    utc_now,
)
from hoardarr.integrations.url_policy import IntegrationTargetError, normalize_and_resolve_target
from hoardarr.storage.groups import group_documents
from hoardarr.system.overview import summarize_storage

router = APIRouter(prefix="/integrations", tags=["automation"])


class WebhookCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=8, max_length=2048)
    secret: SecretStr = Field(min_length=32, max_length=512)
    event_types: list[str] = Field(min_length=1, max_length=32)
    allow_localhost: bool = False
    verify_tls: bool = True

    @field_validator("event_types")
    @classmethod
    def validate_event_types(cls, values: list[str]) -> list[str]:
        normalized = sorted(set(values))
        if any(value not in EVENT_TYPES for value in normalized):
            raise ValueError("one or more webhook event types are unsupported")
        return normalized


class WebhookUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    event_types: list[str] = Field(min_length=1, max_length=32)
    verify_tls: bool = True

    @field_validator("event_types")
    @classmethod
    def validate_event_types(cls, values: list[str]) -> list[str]:
        return WebhookCreateInput.validate_event_types(values)


class WebhookSecretInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret: SecretStr = Field(min_length=32, max_length=512)


def _webhook_endpoint(session: Session, endpoint_id: str) -> WebhookEndpoint:
    endpoint = session.get(WebhookEndpoint, endpoint_id)
    if endpoint is None:
        raise Problem(404, "webhook_not_found", "Webhook not found", "The endpoint does not exist.")
    return endpoint


@router.get("/home-assistant/summary")
def home_assistant_summary(
    request: Request,
    principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    """Return a bounded, versioned, read-only home-automation document."""

    snapshot = session.scalar(
        select(HardwareSnapshot).order_by(HardwareSnapshot.captured_at.desc()).limit(1)
    )
    storage = summarize_storage(snapshot.payload_json if snapshot is not None else None)
    groups = group_documents(session)
    disks = list(session.scalars(select(PhysicalDisk).order_by(PhysicalDisk.id).limit(256)))
    query = select(Operation).order_by(Operation.created_at.desc()).limit(25)
    if not principal.is_admin:
        query = query.where(Operation.actor_id == principal.user_id)
    operations = list(session.scalars(query))
    health_counts = Counter(item.health_state for item in disks)
    operation_counts = Counter(item.status for item in operations)
    critical = health_counts["critical"]
    warning = health_counts["warning"] + operation_counts["needs_attention"]
    overall = "critical" if critical else "warning" if warning else "healthy"
    return {
        "schema_version": 1,
        "captured_at": utc_now(),
        "source": "hoardarr_persisted_state",
        "application": {
            "name": "Hoardarr",
            "version": request.app.version,
            "database_ready": request.app.state.database_ready,
        },
        "health": {
            "state": overall,
            "critical_drives": critical,
            "warning_drives": health_counts["warning"],
            "operations_needing_attention": operation_counts["needs_attention"],
            "failed_operations_in_recent_window": operation_counts["failed"],
        },
        "alerts": [
            *[
                {
                    "kind": "drive_health",
                    "severity": disk.health_state,
                    "entity_type": "drive",
                    "entity_id": disk.id,
                    "state": "active",
                }
                for disk in disks
                if disk.health_state in {"warning", "critical"}
            ],
            *[
                {
                    "kind": "operation",
                    "severity": "warning",
                    "entity_type": "operation",
                    "entity_id": operation.id,
                    "state": "active",
                    "operation_kind": operation.kind,
                    "operation_status": operation.status,
                }
                for operation in operations
                if operation.status in {"failed", "needs_attention"}
            ],
        ][:50],
        "storage": {
            "detected_drive_count": storage.get("drive_count", 0),
            "raw_capacity_bytes": storage.get("raw_capacity_bytes", 0),
            "health": storage.get("health", {}),
            "latest_hardware_observation": (
                {"captured_at": snapshot.captured_at, "source": snapshot.source}
                if snapshot is not None
                else None
            ),
            "groups": [
                {
                    "id": group["id"],
                    "name": group["name"],
                    "namespace_path": group["namespace_path"],
                    "purpose": group["purpose"],
                    "state": group["state"],
                    "backend_states": dict(
                        Counter(item["lifecycle_state"] for item in group["backends"])
                    ),
                }
                for group in groups
            ],
            "drives": [
                {
                    "id": disk.id,
                    "stable_identity": disk.stable_identity,
                    "vendor": disk.vendor,
                    "model": disk.model,
                    "capacity_bytes": disk.capacity_bytes,
                    "media_type": disk.media_type,
                    "health_state": disk.health_state,
                    "lifecycle_state": disk.lifecycle_state,
                    "last_seen_at": disk.last_seen_at,
                }
                for disk in disks
            ],
        },
        "jobs": {
            "counts": dict(operation_counts),
            "recent": [operation_document(item) for item in operations],
            "limit": 25,
        },
        "maintenance": {
            "active": bool(operation_counts["queued"] or operation_counts["running"]),
            "queued": operation_counts["queued"],
            "running": operation_counts["running"],
        },
    }


@router.get("/webhooks/event-types")
def webhook_event_types(
    _principal: Principal = Depends(authenticated_principal),
) -> dict[str, object]:
    return {"items": sorted(EVENT_TYPES)}


@router.get("/webhooks")
def webhooks(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    items = session.scalars(select(WebhookEndpoint).order_by(WebhookEndpoint.created_at).limit(64))
    return {"items": [endpoint_document(item) for item in items]}


@router.post("/webhooks", status_code=201)
def create_webhook(
    body: WebhookCreateInput,
    request: Request,
    principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
    secret_box: SecretBox = Depends(secret_box_from_request),
) -> dict[str, Any]:
    if session.scalar(select(WebhookEndpoint.id).where(WebhookEndpoint.name == body.name.strip())):
        raise Problem(409, "webhook_name_conflict", "Name in use", "Use a unique endpoint name.")
    try:
        target = normalize_and_resolve_target(
            body.url,
            request.app.state.settings,
            allow_localhost=body.allow_localhost,
        )
    except IntegrationTargetError as exc:
        raise Problem(422, "webhook_target_rejected", "Target rejected", str(exc)) from exc
    endpoint_id = new_id()
    secret = body.secret.get_secret_value()
    endpoint = WebhookEndpoint(
        id=endpoint_id,
        name=body.name.strip(),
        url=target.base_url,
        approved_ips_json=list(target.resolved_ips),
        allow_localhost=body.allow_localhost,
        verify_tls=body.verify_tls,
        event_types_json=body.event_types,
        secret_ciphertext=secret_box.encrypt(WEBHOOK_RECORD_TYPE, endpoint_id, secret),
        secret_fingerprint=secret_box.fingerprint("webhook", secret)[:16],
        created_by=principal.user_id,
    )
    session.add(endpoint)
    session.flush()
    record_audit(
        session,
        principal=principal,
        action="webhook.create",
        outcome="completed",
        correlation_id=request.state.request_id,
        target_type="webhook_endpoint",
        target_id=endpoint.id,
        details={"event_types": body.event_types},
    )
    return endpoint_document(endpoint)


@router.put("/webhooks/{endpoint_id}")
def update_webhook(
    endpoint_id: str,
    body: WebhookUpdateInput,
    request: Request,
    principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    endpoint = _webhook_endpoint(session, endpoint_id)
    endpoint.enabled = body.enabled
    endpoint.event_types_json = body.event_types
    endpoint.verify_tls = body.verify_tls
    record_audit(
        session,
        principal=principal,
        action="webhook.update",
        outcome="completed",
        correlation_id=request.state.request_id,
        target_type="webhook_endpoint",
        target_id=endpoint.id,
        details={"enabled": body.enabled, "event_types": body.event_types},
    )
    return endpoint_document(endpoint)


@router.put("/webhooks/{endpoint_id}/secret")
def rotate_webhook_secret(
    endpoint_id: str,
    body: WebhookSecretInput,
    request: Request,
    principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
    secret_box: SecretBox = Depends(secret_box_from_request),
) -> dict[str, Any]:
    endpoint = _webhook_endpoint(session, endpoint_id)
    secret = body.secret.get_secret_value()
    endpoint.secret_ciphertext = secret_box.encrypt(WEBHOOK_RECORD_TYPE, endpoint.id, secret)
    endpoint.secret_fingerprint = secret_box.fingerprint("webhook", secret)[:16]
    endpoint.status = "not_tested"
    endpoint.last_error_json = None
    record_audit(
        session,
        principal=principal,
        action="webhook.secret.rotate",
        outcome="completed",
        correlation_id=request.state.request_id,
        target_type="webhook_endpoint",
        target_id=endpoint.id,
    )
    return endpoint_document(endpoint)


@router.post("/webhooks/{endpoint_id}/test", status_code=202)
def test_webhook(
    endpoint_id: str,
    request: Request,
    principal: Principal = Depends(require_state_scope("operate")),
    session: Session = Depends(database_session),
) -> dict[str, Any]:
    endpoint = _webhook_endpoint(session, endpoint_id)
    event_id = f"test:{new_id()}"
    queued = queue_event(
        session,
        event_id=event_id,
        event_type="test.delivery",
        payload={"message": "Hoardarr webhook test", "requested_by": principal.user_id},
        endpoint_ids={endpoint.id},
    )
    if queued == 0:
        raise Problem(
            409,
            "webhook_test_not_routed",
            "Test not routed",
            "Enable this endpoint and subscribe it to test.delivery before testing.",
        )
    endpoint.status = "testing"
    record_audit(
        session,
        principal=principal,
        action="webhook.test",
        outcome="accepted",
        correlation_id=request.state.request_id,
        target_type="webhook_endpoint",
        target_id=endpoint.id,
    )
    delivery = session.scalar(
        select(WebhookDelivery).where(
            WebhookDelivery.endpoint_id == endpoint.id,
            WebhookDelivery.event_id == event_id,
        )
    )
    if delivery is None:
        session.flush()
        delivery = session.scalar(
            select(WebhookDelivery).where(
                WebhookDelivery.endpoint_id == endpoint.id,
                WebhookDelivery.event_id == event_id,
            )
        )
    assert delivery is not None
    return delivery_document(delivery)


@router.get("/webhooks/{endpoint_id}/deliveries")
def webhook_deliveries(
    endpoint_id: str,
    limit: int = 50,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    _webhook_endpoint(session, endpoint_id)
    bounded_limit = max(1, min(limit, 200))
    items = session.scalars(
        select(WebhookDelivery)
        .where(WebhookDelivery.endpoint_id == endpoint_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(bounded_limit)
    )
    return {"items": [delivery_document(item) for item in items], "limit": bounded_limit}


@router.delete("/webhooks/{endpoint_id}", status_code=204)
def delete_webhook(
    endpoint_id: str,
    request: Request,
    principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
) -> Response:
    endpoint = _webhook_endpoint(session, endpoint_id)
    if endpoint.enabled:
        raise Problem(
            409,
            "webhook_still_enabled",
            "Disable webhook first",
            "Disable the endpoint before permanently removing it.",
        )
    pending = session.scalar(
        select(WebhookDelivery.id).where(
            WebhookDelivery.endpoint_id == endpoint.id,
            WebhookDelivery.status.in_(("queued", "delivering", "retrying")),
        )
    )
    if pending is not None:
        raise Problem(
            409,
            "webhook_delivery_pending",
            "Delivery still pending",
            "Wait for or resolve pending deliveries before removal.",
        )
    record_audit(
        session,
        principal=principal,
        action="webhook.delete",
        outcome="completed",
        correlation_id=request.state.request_id,
        target_type="webhook_endpoint",
        target_id=endpoint.id,
        details={"name": endpoint.name},
    )
    session.execute(delete(WebhookDelivery).where(WebhookDelivery.endpoint_id == endpoint.id))
    session.delete(endpoint)
    return Response(status_code=204)
