from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from hoardarr.api.app import create_app
from hoardarr.auth.service import issue_setup_token
from hoardarr.automation.webhooks import deliver_one, queue_event
from hoardarr.core.config import Settings
from hoardarr.core.secrets import SecretBox
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.db.models import MetricSample, WebhookDelivery, WebhookEndpoint, utc_now
from hoardarr.telemetry.alerts import evaluate_basic_alerts
from hoardarr.telemetry.samples import EntityReading, MetricReading
from hoardarr.telemetry.store import ingest

SECRET = "test-webhook-secret-with-at-least-32-bytes"


def runtime(tmp_path: Path):  # type: ignore[no-untyped-def]
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{(tmp_path / 'webhooks.db').as_posix()}",
        secret_key_file=tmp_path / "secret.key",
        secure_cookies=False,
        integration_timeout_seconds=1,
    )
    upgrade_database(settings.database_url)
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        token = issue_setup_token(session)
    return settings, engine, factory, token


def claim(client: TestClient, token: str) -> str:
    response = client.post(
        "/api/v1/setup/claim",
        headers={"Origin": "http://testserver"},
        json={"token": token, "username": "owner", "password": "test"},
    )
    assert response.status_code == 201
    return response.json()["csrf_token"]


def create_endpoint(client: TestClient, csrf: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/integrations/webhooks",
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        json={
            "name": "Home automation",
            "url": "http://127.0.0.1:9900/events",
            "secret": SECRET,
            "event_types": ["test.delivery", "alert.opened", "alert.cleared"],
            "allow_localhost": True,
            "verify_tls": False,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_webhook_api_encrypts_secret_and_delivers_signed_bounded_payload(tmp_path: Path) -> None:
    settings, engine, factory, token = runtime(tmp_path)
    received: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(204)

    with TestClient(create_app(settings), base_url="http://testserver") as client:
        unauthorized = client.post("/api/v1/integrations/webhooks", json={})
        assert unauthorized.status_code == 401
        csrf = claim(client, token)
        rejected = client.post(
            "/api/v1/integrations/webhooks",
            json={
                "name": "No CSRF",
                "url": "http://127.0.0.1:9900/events",
                "secret": SECRET,
                "event_types": ["test.delivery"],
                "allow_localhost": True,
            },
        )
        assert rejected.status_code == 403
        endpoint = create_endpoint(client, csrf)
        assert "secret" not in endpoint
        assert endpoint["secret_configured"] is True
        endpoint_id = str(endpoint["id"])
        queued = client.post(
            f"/api/v1/integrations/webhooks/{endpoint_id}/test",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        )
        assert queued.status_code == 202
        assert queued.json()["status"] == "queued"

    secret_box = SecretBox.from_file(settings.secret_key_file, create=False)
    assert deliver_one(factory, settings, secret_box, transport=httpx.MockTransport(handler))
    assert len(received) == 1
    request = received[0]
    body = request.content
    payload = json.loads(body)
    assert payload["schema_version"] == 1
    assert payload["event_type"] == "test.delivery"
    assert len(body) < 32 * 1024
    timestamp = request.headers["X-Hoardarr-Timestamp"]
    expected = hmac.new(SECRET.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    assert request.headers["X-Hoardarr-Signature"] == f"v1={expected.hexdigest()}"
    with factory() as session:
        delivery = session.scalar(select(WebhookDelivery))
        stored = session.get(WebhookEndpoint, endpoint_id)
        assert delivery is not None and delivery.status == "delivered"
        assert delivery.attempt_count == 1
        assert stored is not None and SECRET.encode() not in stored.secret_ciphertext
        assert stored.status == "healthy"
    engine.dispose()


def test_webhook_event_is_deduplicated_redacted_and_retried_in_place(tmp_path: Path) -> None:
    settings, engine, factory, token = runtime(tmp_path)
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        csrf = claim(client, token)
        create_endpoint(client, csrf)

    with factory() as session, session.begin():
        payload = {
            "entity_id": "drive-1",
            "api_key": "must-not-leave",
            "nested": {"password": "also-secret", "state": "warning"},
        }
        assert queue_event(
            session,
            event_id="alert:one:opened",
            event_type="alert.opened",
            payload=payload,
        ) == 1
        assert queue_event(
            session,
            event_id="alert:one:opened",
            event_type="alert.opened",
            payload=payload,
        ) == 0

    responses = iter((503, 200))
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return httpx.Response(next(responses))

    secret_box = SecretBox.from_file(settings.secret_key_file, create=False)
    transport = httpx.MockTransport(handler)
    assert deliver_one(factory, settings, secret_box, transport=transport)
    with factory() as session, session.begin():
        delivery = session.scalar(
            select(WebhookDelivery).where(WebhookDelivery.event_id == "alert:one:opened")
        )
        assert delivery is not None
        assert delivery.status == "retrying"
        assert delivery.attempt_count == 1
        delivery.next_attempt_at = utc_now()
    assert deliver_one(factory, settings, secret_box, transport=transport)
    with factory() as session:
        delivery = session.scalar(
            select(WebhookDelivery).where(WebhookDelivery.event_id == "alert:one:opened")
        )
        assert delivery is not None and delivery.status == "delivered"
        assert delivery.attempt_count == 2
        assert session.scalar(select(WebhookDelivery).where(
            WebhookDelivery.event_id == "alert:one:opened"
        )) is not None
    assert b"must-not-leave" not in bodies[0]
    assert b"also-secret" not in bodies[0]
    assert b"[redacted]" in bodies[0]
    engine.dispose()


def test_webhook_rejects_unapproved_event_and_permanent_http_failure(tmp_path: Path) -> None:
    settings, engine, factory, token = runtime(tmp_path)
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        csrf = claim(client, token)
        invalid = client.post(
            "/api/v1/integrations/webhooks",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
            json={
                "name": "Invalid event",
                "url": "http://127.0.0.1:9900/events",
                "secret": SECRET,
                "event_types": ["arbitrary.command"],
                "allow_localhost": True,
            },
        )
        assert invalid.status_code == 422
        endpoint_id = str(create_endpoint(client, csrf)["id"])
        queued = client.post(
            f"/api/v1/integrations/webhooks/{endpoint_id}/test",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        )
        assert queued.status_code == 202

    secret_box = SecretBox.from_file(settings.secret_key_file, create=False)
    assert deliver_one(
        factory,
        settings,
        secret_box,
        transport=httpx.MockTransport(lambda _request: httpx.Response(400)),
    )
    with factory() as session:
        delivery = session.scalar(select(WebhookDelivery))
        assert delivery is not None and delivery.status == "failed"
        assert delivery.attempt_count == 1
        assert delivery.response_status == 400
    engine.dispose()


def test_webhook_secret_rotation_and_safe_removal(tmp_path: Path) -> None:
    settings, engine, _factory, token = runtime(tmp_path)
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        csrf = claim(client, token)
        endpoint = create_endpoint(client, csrf)
        endpoint_id = str(endpoint["id"])
        original_fingerprint = endpoint["secret_fingerprint"]
        replacement = "replacement-webhook-secret-with-32-characters"
        rotated = client.put(
            f"/api/v1/integrations/webhooks/{endpoint_id}/secret",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
            json={"secret": replacement},
        )
        assert rotated.status_code == 200
        assert rotated.json()["secret_fingerprint"] != original_fingerprint
        assert replacement not in rotated.text
        blocked = client.delete(
            f"/api/v1/integrations/webhooks/{endpoint_id}",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        )
        assert blocked.status_code == 409
        disabled = client.put(
            f"/api/v1/integrations/webhooks/{endpoint_id}",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
            json={
                "enabled": False,
                "event_types": endpoint["event_types"],
                "verify_tls": False,
            },
        )
        assert disabled.status_code == 200
        removed = client.delete(
            f"/api/v1/integrations/webhooks/{endpoint_id}",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        )
        assert removed.status_code == 204
        assert client.get("/api/v1/integrations/webhooks").json()["items"] == []
    engine.dispose()


def test_real_alert_transitions_route_once_and_deliver_without_api_consumer(
    tmp_path: Path,
) -> None:
    settings, engine, factory, token = runtime(tmp_path)
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        csrf = claim(client, token)
        create_endpoint(client, csrf)

    def temperature(value: float, observed_at):  # type: ignore[no-untyped-def]
        return MetricReading(
            entity=EntityReading("drive", "wwn:webhook-drive", "Webhook drive"),
            metric_id="drive.temperature",
            observed_at=observed_at,
            value=value,
            quality="available",
            source="test collector",
            collection_interval_seconds=5,
        )

    opened_at = utc_now()
    with factory() as session, session.begin():
        ingest(session, [temperature(66, opened_at)])
        sample = session.scalar(select(MetricSample).order_by(MetricSample.id.desc()))
        assert sample is not None
        assert evaluate_basic_alerts(session, [sample]) == {"opened": 1, "resolved": 0}
        session.flush()
        assert session.scalar(
            select(WebhookDelivery).where(WebhookDelivery.event_type == "alert.opened")
        ) is not None

    received: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content)["event_type"])
        return httpx.Response(204)

    secret_box = SecretBox.from_file(settings.secret_key_file, create=False)
    transport = httpx.MockTransport(handler)
    assert deliver_one(factory, settings, secret_box, transport=transport)
    with factory() as session, session.begin():
        ingest(session, [temperature(50, opened_at + timedelta(seconds=5))])
        sample = session.scalar(select(MetricSample).order_by(MetricSample.id.desc()))
        assert sample is not None
        assert evaluate_basic_alerts(session, [sample]) == {"opened": 0, "resolved": 1}
    assert deliver_one(factory, settings, secret_box, transport=transport)
    assert received == ["alert.opened", "alert.cleared"]
    with factory() as session:
        deliveries = list(
            session.scalars(select(WebhookDelivery).order_by(WebhookDelivery.created_at))
        )
        assert [item.status for item in deliveries] == ["delivered", "delivered"]
    engine.dispose()
