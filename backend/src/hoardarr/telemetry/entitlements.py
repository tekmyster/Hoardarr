from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from hoardarr.core.config import Settings
from hoardarr.db.models import TelemetryState

KNOWN_CAPABILITIES = frozenset(
    {
        "metrics.history.extended",
        "metrics.drive.advanced",
        "metrics.pool.advanced",
        "metrics.controller.advanced",
        "metrics.network.advanced",
        "metrics.analytics.capacity",
        "metrics.analytics.performance",
        "metrics.analytics.anomaly",
        "metrics.analytics.endurance",
        "metrics.analytics.workload",
        "metrics.reporting",
        "metrics.export",
        "metrics.alerting.advanced",
    }
)


def canonical_json(document: object) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def installation_id(path: Path) -> str:
    try:
        identity = path.read_bytes().strip()
    except OSError as exc:
        raise ValueError("installation identity is unavailable") from exc
    if not identity:
        raise ValueError("installation identity is empty")
    return hashlib.sha256(b"hoardarr-installation\x00" + identity).hexdigest()


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class EntitlementStatus:
    state: str
    capabilities: frozenset[str]
    expires_at: datetime | None
    license_id: str | None
    detail: str
    validated_at: datetime
    cached: bool = False

    def allows(self, capability: str | None) -> bool:
        return capability is None or capability in self.capabilities

    def document(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "capabilities": sorted(self.capabilities),
            "expires_at": self.expires_at,
            "license_id": self.license_id,
            "detail": self.detail,
            "validated_at": self.validated_at,
            "cached": self.cached,
            "basic_metrics_available": True,
        }


class EntitlementService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    def _basic(now: datetime, state: str, detail: str) -> EntitlementStatus:
        return EntitlementStatus(state, frozenset(), None, None, detail, now)

    def _cache(self, session: Session, status: EntitlementStatus, now: datetime) -> None:
        record = session.get(TelemetryState, "telemetry_license")
        previous_wall = (
            _parse_time(record.state_json.get("last_wall_time")) if record is not None else None
        )
        recorded_wall = max(previous_wall, now) if previous_wall is not None else now
        document = {
            "state": status.state,
            "capabilities": sorted(status.capabilities),
            "expires_at": status.expires_at.isoformat() if status.expires_at else None,
            "license_id": status.license_id,
            "last_wall_time": recorded_wall.isoformat(),
            "validated_at": status.validated_at.isoformat(),
        }
        if record is None:
            session.execute(
                sqlite_insert(TelemetryState)
                .values(id="telemetry_license", state_json=document, updated_at=now)
                .on_conflict_do_update(
                    index_elements=[TelemetryState.id],
                    set_={"state_json": document, "updated_at": now},
                )
            )
            session.flush()
        else:
            record.state_json = document
            record.updated_at = now

    def _cached_on_io_failure(
        self, session: Session, now: datetime, detail: str
    ) -> EntitlementStatus:
        record = session.get(TelemetryState, "telemetry_license")
        document = record.state_json if record is not None else {}
        expires = _parse_time(document.get("expires_at"))
        capabilities = frozenset(
            value
            for value in document.get("capabilities", [])
            if isinstance(value, str) and value in KNOWN_CAPABILITIES
        )
        if document.get("state") == "valid" and expires and now < expires and capabilities:
            return EntitlementStatus(
                "temporarily_unavailable",
                capabilities,
                expires,
                str(document.get("license_id") or "") or None,
                detail,
                now,
                cached=True,
            )
        return self._basic(now, "temporarily_unavailable", detail)

    def evaluate(self, session: Session, *, now: datetime | None = None) -> EntitlementStatus:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        cached = session.get(TelemetryState, "telemetry_license")
        cached_document = cached.state_json if cached is not None else {}
        previous_wall = _parse_time(cached_document.get("last_wall_time"))
        if previous_wall and current < previous_wall - timedelta(minutes=5):
            status = self._basic(
                current,
                "clock_invalid",
                "The host clock moved backward; advanced capabilities require a valid clock.",
            )
            self._cache(session, status, current)
            return status
        try:
            raw_license = self.settings.telemetry_license_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            # Basic/unlicensed reads do not need a durable cache. Keeping these
            # GET paths read-only avoids write contention when the Settings and
            # Analytics pages request policy and catalog data concurrently.
            return self._basic(current, "unlicensed", "Basic telemetry is active.")
        except OSError:
            return self._cached_on_io_failure(
                session,
                current,
                "The installed license could not be read; a still-valid cached result is in use.",
            )
        try:
            envelope = json.loads(raw_license)
            trust = json.loads(
                self.settings.telemetry_license_trust_file.read_text(encoding="utf-8")
            )
            payload = envelope["payload"]
            key_id = envelope["key_id"]
            encoded_signature = envelope["signature"]
            encoded_key = trust["keys"][key_id]
            if not isinstance(payload, dict) or not all(
                isinstance(value, str) for value in (key_id, encoded_signature, encoded_key)
            ):
                raise ValueError
            public_key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(encoded_key, validate=True)
            )
            public_key.verify(
                base64.b64decode(encoded_signature, validate=True), canonical_json(payload)
            )
        except FileNotFoundError:
            status = self._basic(current, "invalid", "The license trust root is not installed.")
            self._cache(session, status, current)
            return status
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, InvalidSignature):
            status = self._basic(current, "invalid", "The installed license is invalid.")
            self._cache(session, status, current)
            return status
        try:
            expected_installation = installation_id(self.settings.installation_identity_file)
        except ValueError:
            status = self._basic(current, "invalid", "The installation identity is unavailable.")
            self._cache(session, status, current)
            return status
        if payload.get("installation_id") != expected_installation:
            status = self._basic(
                current, "installation_mismatch", "The license belongs to another installation."
            )
            self._cache(session, status, current)
            return status
        starts = _parse_time(payload.get("not_before"))
        expires = _parse_time(payload.get("expires_at"))
        if starts is None or expires is None or expires <= starts:
            status = self._basic(current, "invalid", "The license validity period is invalid.")
            self._cache(session, status, current)
            return status
        if current < starts:
            status = self._basic(current, "not_yet_valid", "The license is not active yet.")
            self._cache(session, status, current)
            return status
        if current >= expires:
            status = EntitlementStatus(
                "expired",
                frozenset(),
                expires,
                str(payload.get("license_id") or "") or None,
                "The license expired. Basic telemetry remains active.",
                current,
            )
            self._cache(session, status, current)
            return status
        requested = payload.get("capabilities")
        if not isinstance(requested, list) or any(
            not isinstance(value, str) for value in requested
        ):
            status = self._basic(current, "invalid", "The license capability list is invalid.")
            self._cache(session, status, current)
            return status
        capabilities = frozenset(value for value in requested if value in KNOWN_CAPABILITIES)
        status = EntitlementStatus(
            "valid",
            capabilities,
            expires,
            str(payload.get("license_id") or "") or None,
            "Advanced telemetry capabilities are active.",
            current,
        )
        self._cache(session, status, current)
        return status
