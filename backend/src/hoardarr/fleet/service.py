from __future__ import annotations

import hashlib
import hmac
import json
import platform
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from hoardarr import __version__
from hoardarr.core.config import Settings
from hoardarr.core.secrets import SecretBox, SecretStoreError
from hoardarr.db.models import (
    FleetTelemetryQueue,
    FleetTelemetryState,
    HardwareSnapshot,
    IntegrationConnection,
    StorageBackend,
    StorageGroup,
    utc_now,
)

SCHEMA_VERSION = 1
DRIVE_IDENTITY_VERSION = 1
STATE_ID = "system"
CREDENTIAL_RECORD_TYPE = "fleet_telemetry"
MESSAGE_TYPES = frozenset({"heartbeat", "inventory", "event", "observation"})
EVENT_TYPES = frozenset(
    {
        "drive_first_seen",
        "drive_assigned",
        "drive_health_changed",
        "drive_warning",
        "drive_removed",
        "drive_replaced",
        "drive_decommissioned",
        "pool_created",
        "pool_expanded",
        "storage_layout_changed",
        "controller_added",
        "controller_redundancy_enabled",
        "application_detected",
        "application_removed",
        "hoardarr_updated",
    }
)
NEVER_KEYS = (
    "password",
    "api_key",
    "apikey",
    "secret",
    "token",
    "credential",
    "private_key",
    "community",
    "chap",
    "session",
)
RETRY_BASE_SECONDS = (30, 120, 600, 1800, 7200, 21600)
MAX_RECORD_BYTES = 128 * 1024


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def ensure_state(session: Session) -> FleetTelemetryState:
    state = session.get(FleetTelemetryState, STATE_ID)
    if state is not None:
        return state
    timezone = datetime.now().astimezone().tzinfo
    timezone_name = getattr(timezone, "key", None) or str(timezone or "UTC")
    state = FleetTelemetryState(
        id=STATE_ID,
        installation_id=str(uuid.uuid4()),
        timezone=timezone_name[:128],
        location_detection_method="os_timezone",
    )
    session.add(state)
    session.flush()
    return state


def validate_location(country_code: str | None, timezone: str) -> tuple[str | None, str]:
    country = country_code.strip().upper() if country_code else None
    if country is not None and (len(country) != 2 or not country.isalpha()):
        raise ValueError("country must be a two-letter region code")
    clean_timezone = timezone.strip()
    try:
        ZoneInfo(clean_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("timezone must be an IANA timezone name") from exc
    return country, clean_timezone


def _bounded(value: Any, *, depth: int = 0) -> Any:
    if depth >= 7:
        return "[maximum depth]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:512]
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:128]:
            key = str(raw_key)[:96]
            if any(part in key.casefold() for part in NEVER_KEYS):
                continue
            output[key] = _bounded(item, depth=depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        return [_bounded(item, depth=depth + 1) for item in list(value)[:256]]
    return str(value)[:512]


def pseudonymous_drive_id(drive: dict[str, Any]) -> tuple[str | None, str | None]:
    identity = drive.get("identity") if isinstance(drive.get("identity"), dict) else {}
    candidates = (
        ("wwn", drive.get("wwn") or identity.get("wwn") or drive.get("naa")),
        ("nguid", drive.get("nguid") or identity.get("nguid")),
        ("eui64", drive.get("eui64") or identity.get("eui64")),
    )
    for kind, raw_value in candidates:
        if isinstance(raw_value, str) and raw_value.strip():
            canonical = f"{kind}:{raw_value.strip().casefold()}"
            return hashlib.sha256(f"hoardarr-drive-v1\0{canonical}".encode()).hexdigest(), kind
    serial = drive.get("serial") or identity.get("serial")
    vendor = drive.get("vendor")
    model = drive.get("model")
    if all(isinstance(value, str) and value.strip() for value in (serial, vendor, model)):
        canonical = ":".join(str(value).strip().casefold() for value in (vendor, model, serial))
        return hashlib.sha256(
            f"hoardarr-drive-v1\0fallback:{canonical}".encode()
        ).hexdigest(), "serial_model"
    return None, None


def partial_serial(drive: dict[str, Any]) -> str | None:
    identity = drive.get("identity") if isinstance(drive.get("identity"), dict) else {}
    value = drive.get("serial") or identity.get("serial")
    if not isinstance(value, str) or not value.strip():
        return None
    clean = value.strip()
    return f"…{clean[-4:]}" if len(clean) > 4 else f"…{clean}"


def heartbeat_payload(state: FleetTelemetryState, *, now: datetime | None = None) -> dict[str, Any]:
    return {
        "installation_id": state.installation_id,
        "hoardarr_version": __version__,
        "build_commit": None,
        "schema_version": SCHEMA_VERSION,
        "platform_family": platform.system().casefold() or "unknown",
        "heartbeat_at": (now or utc_now()).astimezone(UTC).isoformat(),
    }


def _safe_drive(drive: dict[str, Any]) -> dict[str, Any] | None:
    pseudonym, source = pseudonymous_drive_id(drive)
    if pseudonym is None:
        return None
    connection = drive.get("connection") if isinstance(drive.get("connection"), dict) else {}
    health = drive.get("health") if isinstance(drive.get("health"), dict) else {}
    sector = drive.get("sector") if isinstance(drive.get("sector"), dict) else {}
    return _bounded(
        {
            "drive_id": pseudonym,
            "drive_identity_version": DRIVE_IDENTITY_VERSION,
            "identity_source": source,
            "serial_fragment": partial_serial(drive),
            "vendor": drive.get("vendor"),
            "model": drive.get("model"),
            "firmware": drive.get("firmware_revision"),
            "capacity_bytes": drive.get("capacity_bytes") or drive.get("size_bytes"),
            "rotational": drive.get("rotational"),
            "media_type": drive.get("media_type"),
            "protocol": connection.get("protocol") or connection.get("transport"),
            "logical_sector_bytes": sector.get("logical") or drive.get("logical_sector_size"),
            "physical_sector_bytes": sector.get("physical") or drive.get("physical_sector_size"),
            "controller_model": connection.get("controller_model"),
            "enclosure_model": connection.get("enclosure_model"),
            "health": health.get("overall") or drive.get("health_status"),
            "temperature_celsius": health.get("temperature_celsius"),
            "power_on_hours": health.get("power_on_hours"),
            "percentage_used": health.get("percentage_used"),
            "lifetime_host_writes_bytes": health.get("lifetime_host_writes_bytes"),
        }
    )


def inventory_payload(session: Session, state: FleetTelemetryState) -> dict[str, Any]:
    snapshot = session.scalar(
        select(HardwareSnapshot).order_by(HardwareSnapshot.captured_at.desc()).limit(1)
    )
    snapshot_payload = snapshot.payload_json if snapshot is not None else {}
    raw_disks = snapshot_payload.get("disks", []) if isinstance(snapshot_payload, dict) else []
    disks = [
        safe for item in raw_disks[:512] if isinstance(item, dict) and (safe := _safe_drive(item))
    ]
    applications = sorted(
        {
            item.expected_product
            for item in session.scalars(select(IntegrationConnection).limit(256))
            if item.expected_product
        }
    )
    groups = session.scalars(select(StorageGroup).order_by(StorageGroup.id).limit(256)).all()
    backends = session.scalars(select(StorageBackend).order_by(StorageBackend.id).limit(1024)).all()
    return {
        "installation_id": state.installation_id,
        "schema_version": SCHEMA_VERSION,
        "observed_at": utc_now().isoformat(),
        "level": 1,
        "system": {
            "cpu_architecture": platform.machine() or None,
            "os": platform.system() or None,
            "kernel": platform.release() or None,
        },
        "storage_hardware": disks,
        "storage_configuration": {
            "storage_group_count": len(groups),
            "backend_count": len(backends),
            "purposes": sorted({group.purpose for group in groups}),
            "backend_roles": sorted({backend.role for backend in backends}),
        },
        "applications_detected": applications,
    }


def _priority(message_type: str, payload: dict[str, Any]) -> int:
    if message_type == "event" and payload.get("event_type") in {
        "drive_warning",
        "drive_replaced",
        "drive_decommissioned",
        "drive_health_changed",
    }:
        return 10
    if message_type == "inventory" or message_type == "event":
        return 30
    if message_type == "observation":
        return 60
    return 90


def enqueue(
    session: Session,
    settings: Settings,
    *,
    message_type: str,
    telemetry_level: int,
    payload: dict[str, Any],
) -> FleetTelemetryQueue:
    if message_type not in MESSAGE_TYPES:
        raise ValueError("unsupported fleet telemetry message type")
    clean = _bounded(payload)
    if not isinstance(clean, dict):
        raise ValueError("telemetry payload must be an object")
    encoded = canonical_json(clean)
    if len(encoded) > MAX_RECORD_BYTES:
        raise ValueError("telemetry record exceeds the 128 KiB record limit")
    if message_type == "heartbeat":
        existing = session.scalar(
            select(FleetTelemetryQueue)
            .where(
                FleetTelemetryQueue.message_type == "heartbeat",
                FleetTelemetryQueue.status.in_(("queued", "retrying")),
            )
            .order_by(FleetTelemetryQueue.created_at.desc())
            .limit(1)
        )
        if existing is not None:
            existing.payload_json = clean
            existing.payload_sha256 = hashlib.sha256(encoded).hexdigest()
            existing.size_bytes = len(encoded)
            existing.updated_at = utc_now()
            return existing
    record = FleetTelemetryQueue(
        message_type=message_type,
        telemetry_level=telemetry_level,
        priority=_priority(message_type, clean),
        schema_version=SCHEMA_VERSION,
        payload_json=clean,
        payload_sha256=hashlib.sha256(encoded).hexdigest(),
        size_bytes=len(encoded),
        expires_at=utc_now() + timedelta(days=settings.fleet_queue_max_age_days),
    )
    session.add(record)
    session.flush()
    enforce_queue_bounds(session, settings)
    return record


def enqueue_heartbeat(session: Session, settings: Settings) -> FleetTelemetryQueue:
    state = ensure_state(session)
    return enqueue(
        session,
        settings,
        message_type="heartbeat",
        telemetry_level=0,
        payload=heartbeat_payload(state),
    )


def enqueue_inventory(session: Session, settings: Settings) -> FleetTelemetryQueue | None:
    state = ensure_state(session)
    if not state.hardware_enabled:
        return None
    return enqueue(
        session,
        settings,
        message_type="inventory",
        telemetry_level=1,
        payload=inventory_payload(session, state),
    )


def enqueue_lifecycle_event(
    session: Session,
    settings: Settings,
    *,
    event_type: str,
    details: dict[str, Any],
) -> FleetTelemetryQueue | None:
    if event_type not in EVENT_TYPES:
        raise ValueError("unsupported fleet lifecycle event")
    state = ensure_state(session)
    if not state.hardware_enabled:
        return None
    payload = {
        "installation_id": state.installation_id,
        "schema_version": SCHEMA_VERSION,
        "event_type": event_type,
        "occurred_at": utc_now().isoformat(),
        "details": details,
    }
    return enqueue(session, settings, message_type="event", telemetry_level=1, payload=payload)


def enforce_queue_bounds(session: Session, settings: Settings) -> None:
    now = utc_now()
    session.execute(
        delete(FleetTelemetryQueue).where(
            FleetTelemetryQueue.expires_at < now,
            FleetTelemetryQueue.status.in_(("queued", "retrying", "dead_letter")),
        )
    )
    session.flush()
    records = session.scalars(
        select(FleetTelemetryQueue)
        .where(FleetTelemetryQueue.status.in_(("queued", "retrying", "dead_letter")))
        .order_by(FleetTelemetryQueue.priority.desc(), FleetTelemetryQueue.created_at)
    ).all()
    total_bytes = sum(record.size_bytes for record in records)
    while records and (
        len(records) > settings.fleet_queue_max_records
        or total_bytes > settings.fleet_queue_max_bytes
    ):
        victim = records.pop(0)
        total_bytes -= victim.size_bytes
        session.delete(victim)


def queue_summary(session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(
            FleetTelemetryQueue.status,
            func.count(),
            func.coalesce(func.sum(FleetTelemetryQueue.size_bytes), 0),
        ).group_by(FleetTelemetryQueue.status)
    )
    by_status = {
        str(status): {"records": int(count), "bytes": int(size)} for status, count, size in rows
    }
    return {
        "queued_records": sum(
            item["records"] for key, item in by_status.items() if key in {"queued", "retrying"}
        ),
        "queued_bytes": sum(
            item["bytes"] for key, item in by_status.items() if key in {"queued", "retrying"}
        ),
        "dead_letter_records": by_status.get("dead_letter", {}).get("records", 0),
        "by_status": by_status,
    }


def pending_payloads(session: Session, *, limit: int = 100) -> list[dict[str, Any]]:
    records = session.scalars(
        select(FleetTelemetryQueue)
        .where(FleetTelemetryQueue.status.in_(("queued", "retrying", "dead_letter")))
        .order_by(FleetTelemetryQueue.priority, FleetTelemetryQueue.created_at)
        .limit(min(max(limit, 1), 1000))
    )
    return [
        {
            "id": record.id,
            "message_type": record.message_type,
            "telemetry_level": record.telemetry_level,
            "schema_version": record.schema_version,
            "payload": record.payload_json,
            "status": record.status,
            "attempt_count": record.attempt_count,
            "last_error": record.last_error_json,
            "created_at": record.created_at,
        }
        for record in records
    ]


def install_credential(state: FleetTelemetryState, secret_box: SecretBox, credential: str) -> None:
    if len(credential) < 32 or len(credential) > 512:
        raise ValueError("registration credential has an invalid length")
    state.credential_ciphertext = secret_box.encrypt(CREDENTIAL_RECORD_TYPE, state.id, credential)
    state.credential_fingerprint = secret_box.fingerprint("fleet-credential", credential)[:16]
    state.registration_status = "registered"
    state.last_error_json = None


def register_installation(
    session_factory: SessionFactory,
    settings: Settings,
    secret_box: SecretBox,
    *,
    transport: httpx.BaseTransport | None = None,
) -> None:
    with session_factory() as session, session.begin():
        state = ensure_state(session)
        payload = heartbeat_payload(state)
        state.last_attempt_at = utc_now()
    try:
        with httpx.Client(
            timeout=httpx.Timeout(settings.integration_timeout_seconds),
            verify=True,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            response = client.post(
                f"{settings.fleet_telemetry_endpoint}/register",
                json=payload,
                headers={"User-Agent": f"Hoardarr-Fleet/{__version__}"},
            )
        response.raise_for_status()
        document = response.json()
        credential = document.get("credential")
        if not isinstance(credential, str):
            raise ValueError("registration response omitted the installation credential")
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        with session_factory() as session, session.begin():
            state = ensure_state(session)
            state.registration_status = "registration_failed"
            state.last_error_json = {
                "code": "registration_failed",
                "message": type(exc).__name__,
            }
        raise
    with session_factory() as session, session.begin():
        state = ensure_state(session)
        install_credential(state, secret_box, credential)
        state.last_success_at = utc_now()


def _batch(
    session: Session, settings: Settings, state: FleetTelemetryState
) -> tuple[dict[str, Any], list[FleetTelemetryQueue]] | None:
    records: list[FleetTelemetryQueue] = []
    total = 0
    for record in session.scalars(
        select(FleetTelemetryQueue)
        .where(
            FleetTelemetryQueue.status.in_(("queued", "retrying")),
            FleetTelemetryQueue.next_attempt_at <= utc_now(),
        )
        .order_by(FleetTelemetryQueue.priority, FleetTelemetryQueue.created_at)
        .limit(settings.fleet_batch_max_records)
    ):
        if records and total + record.size_bytes > settings.fleet_batch_max_bytes:
            break
        records.append(record)
        total += record.size_bytes
    if not records:
        return None
    state.sequence_number += 1
    record_documents = [
        {
            "id": record.id,
            "message_type": record.message_type,
            "telemetry_level": record.telemetry_level,
            "schema_version": record.schema_version,
            "created_at": (
                record.created_at
                if record.created_at.tzinfo is not None
                else record.created_at.replace(tzinfo=UTC)
            )
            .astimezone(UTC)
            .isoformat(),
            "payload_sha256": record.payload_sha256,
            "payload": record.payload_json,
        }
        for record in records
    ]
    digest = hashlib.sha256(canonical_json(record_documents)).hexdigest()
    body = {
        "installation_id": state.installation_id,
        "schema_version": SCHEMA_VERSION,
        "sequence_number": state.sequence_number,
        "batch_id": str(uuid.uuid4()),
        "created_at": utc_now().isoformat(),
        "payload_digest": digest,
        "records": record_documents,
    }
    return body, records


def _retry_delay(record: FleetTelemetryQueue) -> int:
    base = RETRY_BASE_SECONDS[min(record.attempt_count, len(RETRY_BASE_SECONDS) - 1)]
    jitter = int(hashlib.sha256(f"{record.id}:{record.attempt_count}".encode()).hexdigest()[:4], 16)
    return base + jitter % max(base // 4, 1)


def deliver_batch(
    session_factory: SessionFactory,
    settings: Settings,
    secret_box: SecretBox,
    *,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    with session_factory() as session, session.begin():
        state = ensure_state(session)
        if state.credential_ciphertext is None:
            return False
        selected = _batch(session, settings, state)
        if selected is None:
            return False
        body, records = selected
        ids = [record.id for record in records]
        for record in records:
            record.status = "sending"
        state.last_attempt_at = utc_now()
        credential_blob = state.credential_ciphertext
    try:
        credential = secret_box.decrypt(CREDENTIAL_RECORD_TYPE, STATE_ID, credential_blob)
        encoded = canonical_json(body)
        signature = hmac.new(credential.encode(), encoded, hashlib.sha256).hexdigest()
        with httpx.Client(
            timeout=httpx.Timeout(settings.integration_timeout_seconds),
            verify=True,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            response = client.post(
                f"{settings.fleet_telemetry_endpoint}/batch",
                content=encoded,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": f"Hoardarr-Fleet/{__version__}",
                    "X-Hoardarr-Signature": f"v1={signature}",
                },
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise httpx.HTTPStatusError(
                "temporary ingestion failure", request=response.request, response=response
            )
        if response.status_code in {400, 413, 422}:
            reason = f"server rejected schema or body with HTTP {response.status_code}"
            with session_factory() as session, session.begin():
                for record in session.scalars(
                    select(FleetTelemetryQueue).where(FleetTelemetryQueue.id.in_(ids))
                ):
                    record.status = "dead_letter"
                    record.attempt_count += 1
                    record.last_error_json = {"code": "permanent_rejection", "message": reason}
                current = ensure_state(session)
                current.last_error_json = {"code": "permanent_rejection", "message": reason}
            return True
        if response.status_code in {401, 403}:
            raise PermissionError("installation credential was rejected")
        response.raise_for_status()
        document = response.json()
        acknowledged = set(document.get("acknowledged_record_ids", []))
        if not acknowledged.issubset(set(ids)):
            raise ValueError("server acknowledged an unknown record")
    except (
        httpx.HTTPError,
        SecretStoreError,
        PermissionError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        now = utc_now()
        with session_factory() as session, session.begin():
            for record in session.scalars(
                select(FleetTelemetryQueue).where(FleetTelemetryQueue.id.in_(ids))
            ):
                record.status = "retrying"
                record.attempt_count += 1
                record.next_attempt_at = now + timedelta(seconds=_retry_delay(record))
                record.last_error_json = {"code": "delivery_failed", "message": type(exc).__name__}
            current = ensure_state(session)
            current.last_error_json = {"code": "delivery_failed", "message": type(exc).__name__}
            if isinstance(exc, PermissionError):
                current.registration_status = "credential_rejected"
        return True
    with session_factory() as session, session.begin():
        session.execute(delete(FleetTelemetryQueue).where(FleetTelemetryQueue.id.in_(acknowledged)))
        for record in session.scalars(
            select(FleetTelemetryQueue).where(FleetTelemetryQueue.id.in_(set(ids) - acknowledged))
        ):
            record.status = "retrying"
            record.attempt_count += 1
            record.next_attempt_at = utc_now() + timedelta(seconds=_retry_delay(record))
            record.last_error_json = {
                "code": "not_acknowledged",
                "message": "server did not acknowledge this record",
            }
        current = ensure_state(session)
        current.last_success_at = utc_now()
        current.last_error_json = None
        current.registration_status = "registered"
    return True


def reset_identity(session: Session) -> FleetTelemetryState:
    state = ensure_state(session)
    state.installation_id = str(uuid.uuid4())
    state.credential_ciphertext = None
    state.credential_fingerprint = None
    state.registration_status = "unregistered"
    state.sequence_number = 0
    state.last_attempt_at = None
    state.last_success_at = None
    state.last_error_json = None
    session.execute(delete(FleetTelemetryQueue))
    return state
