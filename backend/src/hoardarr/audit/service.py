from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from hoardarr.auth.service import Principal
from hoardarr.db.models import AuditEvent

UNAUTHENTICATED_ACTOR_ID = "00000000-0000-0000-0000-000000000000"


def record_audit(
    session: Session,
    *,
    principal: Principal,
    action: str,
    outcome: str,
    correlation_id: str,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_type=principal.auth_type,
        actor_id=principal.user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        outcome=outcome,
        correlation_id=correlation_id,
        details_json=details or {},
    )
    session.add(event)
    return event


def record_unauthenticated_audit(
    session: Session,
    *,
    action: str,
    outcome: str,
    correlation_id: str,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_type="unauthenticated",
        actor_id=UNAUTHENTICATED_ACTOR_ID,
        action=action,
        outcome=outcome,
        correlation_id=correlation_id,
        details_json=details or {},
    )
    session.add(event)
    return event
