from __future__ import annotations

import hashlib
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete
from sqlalchemy.orm import Session

from hoardarr.api.dependencies import authenticated_principal, database_session, require_state_scope
from hoardarr.api.problem import Problem
from hoardarr.audit.service import record_audit
from hoardarr.auth.service import Principal
from hoardarr.db.models import FleetTelemetryQueue
from hoardarr.fleet.service import (
    SCHEMA_VERSION,
    enqueue_heartbeat,
    enqueue_inventory,
    ensure_state,
    pending_payloads,
    queue_summary,
    reset_identity,
    validate_location,
)

router = APIRouter(prefix="/fleet-telemetry", tags=["fleet telemetry"])


class PreferencesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hardware_enabled: bool
    enhanced_enabled: bool = False
    content_enabled: bool = False
    country_code: str | None = Field(default=None, max_length=2)
    timezone: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def diagnostics_are_layered(self) -> PreferencesInput:
        if self.enhanced_enabled and not self.hardware_enabled:
            raise ValueError("enhanced diagnostics require hardware telemetry")
        if self.content_enabled and not self.enhanced_enabled:
            raise ValueError("content diagnostics require enhanced diagnostics")
        return self


class ResetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: Literal["RESET TELEMETRY IDENTITY"]


def _document(request: Request, session: Session) -> dict[str, object]:
    state = ensure_state(session)
    return {
        "anonymous_heartbeat": {"required": True, "enabled": True},
        "hardware_enabled": state.hardware_enabled,
        "enhanced_enabled": state.enhanced_enabled,
        "content_enabled": state.content_enabled,
        "installation_id": state.installation_id,
        "endpoint": request.app.state.settings.fleet_telemetry_endpoint,
        "connection_status": state.registration_status,
        "credential_fingerprint": state.credential_fingerprint,
        "last_successful_upload": state.last_success_at,
        "last_attempted_upload": state.last_attempt_at,
        "last_error": state.last_error_json,
        "schema_version": state.schema_version,
        "country_code": state.country_code,
        "timezone": state.timezone,
        "location_detection_method": state.location_detection_method,
        "location_confirmed": state.location_confirmed,
        **queue_summary(session),
        "limitations": (
            "Telemetry is authenticated and replay-resistant in transit, but an administrator "
            "with control of this host can modify locally collected data."
        ),
    }


@router.get("/settings")
def settings_document(
    request: Request,
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    return _document(request, session)


@router.put("/settings")
def update_settings(
    body: PreferencesInput,
    request: Request,
    principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    try:
        country, timezone = validate_location(body.country_code, body.timezone)
    except ValueError as exc:
        raise Problem(422, "invalid_location", "Invalid location", str(exc)) from exc
    state = ensure_state(session)
    state.hardware_enabled = body.hardware_enabled
    state.enhanced_enabled = body.enhanced_enabled
    state.content_enabled = body.content_enabled
    state.country_code = country
    state.timezone = timezone
    state.location_detection_method = "manual"
    state.location_confirmed = True
    if not state.hardware_enabled:
        session.execute(delete(FleetTelemetryQueue).where(FleetTelemetryQueue.telemetry_level > 0))
    elif not state.enhanced_enabled:
        session.execute(delete(FleetTelemetryQueue).where(FleetTelemetryQueue.telemetry_level > 1))
    record_audit(
        session,
        principal=principal,
        correlation_id=request.state.request_id,
        action="fleet_telemetry.preferences.update",
        target_type="fleet_telemetry",
        target_id="system",
        outcome="succeeded",
        details={
            "hardware_enabled": state.hardware_enabled,
            "enhanced_enabled": state.enhanced_enabled,
            "content_enabled": state.content_enabled,
            "country_code": state.country_code,
            "timezone": state.timezone,
        },
    )
    return _document(request, session)


@router.get("/pending")
def pending(
    _principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "field_groups": {
            "level_0": "Required anonymous installation heartbeat",
            "level_1": "Hardware and product telemetry",
            "level_2": "Enhanced diagnostics (explicit opt-in)",
            "level_3": "Content diagnostics (separate explicit opt-in)",
        },
        "items": pending_payloads(session),
    }


@router.post("/send-now")
def send_now(
    request: Request,
    principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    enqueue_heartbeat(session, request.app.state.settings)
    enqueue_inventory(session, request.app.state.settings)
    record_audit(
        session,
        principal=principal,
        correlation_id=request.state.request_id,
        action="fleet_telemetry.send.request",
        target_type="fleet_telemetry",
        target_id="system",
        outcome="queued",
        details=queue_summary(session),
    )
    return _document(request, session)


@router.post("/clear-optional")
def clear_optional(
    request: Request,
    principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    removed = session.execute(
        delete(FleetTelemetryQueue).where(FleetTelemetryQueue.telemetry_level > 0)
    ).rowcount
    record_audit(
        session,
        principal=principal,
        correlation_id=request.state.request_id,
        action="fleet_telemetry.optional.clear",
        target_type="fleet_telemetry",
        target_id="system",
        outcome="succeeded",
        details={"removed_records": removed},
    )
    return _document(request, session)


@router.post("/reset-identity")
def reset(
    body: ResetInput,
    request: Request,
    principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    del body
    old_id = ensure_state(session).installation_id
    state = reset_identity(session)
    record_audit(
        session,
        principal=principal,
        correlation_id=request.state.request_id,
        action="fleet_telemetry.identity.reset",
        target_type="fleet_telemetry",
        target_id="system",
        outcome="succeeded",
        details={"previous_id_sha256": hashlib.sha256(old_id.encode()).hexdigest()},
    )
    return _document(request, session) | {"installation_id": state.installation_id}
