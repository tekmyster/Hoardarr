from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from hoardarr.core.config import Settings
from hoardarr.core.secrets import SecretBox, SecretStoreError
from hoardarr.db.models import WebhookDelivery, WebhookEndpoint, utc_now
from hoardarr.integrations.url_policy import (
    IntegrationTarget,
    IntegrationTargetError,
    revalidate_approved_target,
)

WEBHOOK_RECORD_TYPE = "webhook_endpoint"
MAX_ENDPOINTS = 64
MAX_PAYLOAD_BYTES = 32 * 1024
MAX_ATTEMPTS = 5
RETRY_SECONDS = (30, 120, 600, 3600)
EVENT_TYPES = frozenset(
    {
        "alert.opened",
        "alert.acknowledged",
        "alert.suppressed",
        "alert.cleared",
        "alert.unsuppressed",
        "test.delivery",
    }
)
SENSITIVE_KEY_PARTS = ("secret", "password", "credential", "api_key", "token")


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _bounded(value: Any, *, depth: int = 0) -> Any:
    if depth >= 8:
        return "[maximum depth]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:1024]
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:100]:
            key = str(raw_key)[:128]
            if any(part in key.lower() for part in SENSITIVE_KEY_PARTS):
                output[key] = "[redacted]"
            else:
                output[key] = _bounded(item, depth=depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        return [_bounded(item, depth=depth + 1) for item in list(value)[:100]]
    return str(value)[:1024]


def sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = _bounded(payload)
    assert isinstance(sanitized, dict)
    encoded = canonical_json(sanitized)
    if len(encoded) > MAX_PAYLOAD_BYTES:
        return {
            "truncated": True,
            "reason": "payload exceeded the 32 KiB webhook limit",
            "original_sha256": hashlib.sha256(encoded).hexdigest(),
        }
    return sanitized


def endpoint_document(endpoint: WebhookEndpoint) -> dict[str, Any]:
    return {
        "id": endpoint.id,
        "name": endpoint.name,
        "url": endpoint.url,
        "event_types": list(endpoint.event_types_json),
        "allow_localhost": endpoint.allow_localhost,
        "verify_tls": endpoint.verify_tls,
        "enabled": endpoint.enabled,
        "status": endpoint.status,
        "secret_configured": True,
        "secret_fingerprint": endpoint.secret_fingerprint,
        "last_success_at": endpoint.last_success_at,
        "last_error": endpoint.last_error_json,
        "created_at": endpoint.created_at,
        "updated_at": endpoint.updated_at,
    }


def delivery_document(delivery: WebhookDelivery) -> dict[str, Any]:
    return {
        "id": delivery.id,
        "endpoint_id": delivery.endpoint_id,
        "event_id": delivery.event_id,
        "event_type": delivery.event_type,
        "status": delivery.status,
        "attempt_count": delivery.attempt_count,
        "next_attempt_at": delivery.next_attempt_at,
        "response_status": delivery.response_status,
        "last_error": delivery.last_error_json,
        "delivered_at": delivery.delivered_at,
        "created_at": delivery.created_at,
        "updated_at": delivery.updated_at,
    }


def queue_event(
    session: Session,
    *,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    endpoint_ids: set[str] | None = None,
) -> int:
    if event_type not in EVENT_TYPES:
        raise ValueError("unsupported webhook event type")
    # Include deliveries queued earlier in this transaction in the deduplication
    # query. Hoardarr sessions intentionally disable implicit autoflush.
    session.flush()
    bounded = sanitize_payload(payload)
    payload_sha256 = hashlib.sha256(canonical_json(bounded)).hexdigest()
    endpoints = session.scalars(
        select(WebhookEndpoint)
        .where(WebhookEndpoint.enabled.is_(True))
        .order_by(WebhookEndpoint.id)
        .limit(MAX_ENDPOINTS)
    )
    queued = 0
    for endpoint in endpoints:
        if endpoint_ids is not None and endpoint.id not in endpoint_ids:
            continue
        if event_type not in endpoint.event_types_json and "*" not in endpoint.event_types_json:
            continue
        exists = session.scalar(
            select(WebhookDelivery.id).where(
                WebhookDelivery.endpoint_id == endpoint.id,
                WebhookDelivery.event_id == event_id,
            )
        )
        if exists is not None:
            continue
        session.add(
            WebhookDelivery(
                endpoint_id=endpoint.id,
                event_id=event_id[:160],
                event_type=event_type,
                payload_json=bounded,
                payload_sha256=payload_sha256,
            )
        )
        queued += 1
    return queued


def _host_header(target: IntegrationTarget) -> str:
    host = f"[{target.hostname}]" if ":" in target.hostname else target.hostname
    default_port = 443 if target.scheme == "https" else 80
    return host if target.port == default_port else f"{host}:{target.port}"


def _network_url(target: IntegrationTarget) -> str:
    address = ipaddress.ip_address(target.resolved_ips[0])
    host = f"[{address}]" if address.version == 6 else str(address)
    return f"{target.scheme}://{host}:{target.port}{target.base_path}"


def _body(delivery: WebhookDelivery) -> bytes:
    return canonical_json(
        {
            "schema_version": 1,
            "delivery_id": delivery.id,
            "event_id": delivery.event_id,
            "event_type": delivery.event_type,
            "occurred_at": delivery.created_at.isoformat(),
            "payload": delivery.payload_json,
        }
    )


def _send(
    endpoint: WebhookEndpoint,
    delivery: WebhookDelivery,
    *,
    settings: Settings,
    secret: str,
    transport: httpx.BaseTransport | None,
) -> int:
    target = revalidate_approved_target(
        endpoint.url,
        list(endpoint.approved_ips_json),
        settings,
        allow_localhost=endpoint.allow_localhost,
    )
    body = _body(delivery)
    timestamp = str(int(datetime.now(UTC).timestamp()))
    signature = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    headers = {
        "Host": _host_header(target),
        "Content-Type": "application/json",
        "User-Agent": "Hoardarr-Webhook/1",
        "X-Hoardarr-Delivery": delivery.id,
        "X-Hoardarr-Event": delivery.event_type,
        "X-Hoardarr-Timestamp": timestamp,
        "X-Hoardarr-Signature": f"v1={signature}",
    }
    with httpx.Client(
        timeout=httpx.Timeout(settings.integration_timeout_seconds),
        verify=endpoint.verify_tls,
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    ) as client:
        request = client.build_request(
            "POST",
            _network_url(target),
            headers=headers,
            content=body,
            extensions={"sni_hostname": target.hostname},
        )
        response = client.send(request)
    return response.status_code


def deliver_one(
    session_factory: SessionFactory,
    settings: Settings,
    secret_box: SecretBox,
    *,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    now = utc_now()
    with session_factory() as session, session.begin():
        session.execute(
            update(WebhookDelivery)
            .where(
                WebhookDelivery.status == "delivering",
                WebhookDelivery.updated_at < now - timedelta(minutes=5),
            )
            .values(status="retrying", next_attempt_at=now, updated_at=now)
        )
        delivery = session.scalar(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.status.in_(("queued", "retrying")),
                WebhookDelivery.next_attempt_at <= now,
            )
            .order_by(WebhookDelivery.next_attempt_at, WebhookDelivery.created_at)
            .limit(1)
        )
        if delivery is None:
            return False
        claimed = session.execute(
            update(WebhookDelivery)
            .where(
                WebhookDelivery.id == delivery.id,
                WebhookDelivery.status.in_(("queued", "retrying")),
            )
            .values(status="delivering", updated_at=now)
        )
        if claimed.rowcount != 1:
            return False
        delivery_id = delivery.id
        endpoint_id = delivery.endpoint_id

    try:
        with session_factory() as session:
            delivery = session.get(WebhookDelivery, delivery_id)
            endpoint = session.get(WebhookEndpoint, endpoint_id)
            if delivery is None or endpoint is None or not endpoint.enabled:
                raise RuntimeError("webhook endpoint is unavailable")
            secret = secret_box.decrypt(
                WEBHOOK_RECORD_TYPE, endpoint.id, endpoint.secret_ciphertext
            )
            status_code = _send(
                endpoint,
                delivery,
                settings=settings,
                secret=secret,
                transport=transport,
            )
        retryable = status_code in {408, 425, 429} or status_code >= 500
        if not 200 <= status_code < 300:
            raise WebhookResponseError(status_code, retryable=retryable)
    except (httpx.HTTPError, IntegrationTargetError, SecretStoreError, RuntimeError) as exc:
        with session_factory() as session, session.begin():
            delivery = session.get(WebhookDelivery, delivery_id)
            endpoint = session.get(WebhookEndpoint, endpoint_id)
            if delivery is None:
                return True
            delivery.attempt_count += 1
            retryable = not isinstance(exc, WebhookResponseError) or exc.retryable
            if retryable and delivery.attempt_count < MAX_ATTEMPTS:
                delivery.status = "retrying"
                delay = RETRY_SECONDS[min(delivery.attempt_count - 1, len(RETRY_SECONDS) - 1)]
                delivery.next_attempt_at = utc_now() + timedelta(seconds=delay)
            else:
                delivery.status = "failed"
            code = "http_error"
            if isinstance(exc, WebhookResponseError):
                delivery.response_status = exc.status_code
                code = "remote_rejected"
            elif isinstance(exc, SecretStoreError):
                code = "secret_unavailable"
            elif isinstance(exc, IntegrationTargetError):
                code = "target_revalidation_failed"
            delivery.last_error_json = {"code": code, "message": type(exc).__name__}
            if endpoint is not None:
                endpoint.status = "degraded"
                endpoint.last_error_json = delivery.last_error_json
        return True

    with session_factory() as session, session.begin():
        delivery = session.get(WebhookDelivery, delivery_id)
        endpoint = session.get(WebhookEndpoint, endpoint_id)
        if delivery is not None:
            delivery.attempt_count += 1
            delivery.status = "delivered"
            delivery.response_status = status_code
            delivery.last_error_json = None
            delivery.delivered_at = utc_now()
        if endpoint is not None:
            endpoint.status = "healthy"
            endpoint.last_success_at = utc_now()
            endpoint.last_error_json = None
    return True


class WebhookResponseError(RuntimeError):
    def __init__(self, status_code: int, *, retryable: bool) -> None:
        super().__init__(f"webhook returned HTTP {status_code}")
        self.status_code = status_code
        self.retryable = retryable
