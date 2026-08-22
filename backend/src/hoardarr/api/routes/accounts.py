from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from hoardarr.accounts.client import AccountExecutorError, provision_media_account
from hoardarr.api.dependencies import database_session, require_state_scope, settings_from_request
from hoardarr.api.problem import Problem
from hoardarr.api.schemas import MediaAccountProvisionRequest
from hoardarr.audit.service import record_audit
from hoardarr.auth.service import Principal
from hoardarr.core.config import Settings

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("/media", status_code=201)
def create_or_update_media_account(
    payload: MediaAccountProvisionRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
    settings: Settings = Depends(settings_from_request),
) -> dict[str, object]:
    generated = payload.credential_mode == "generate"
    password = (
        secrets.token_urlsafe(24) if generated else payload.password.get_secret_value()  # type: ignore[union-attr]
    )
    try:
        result = provision_media_account(
            settings.account_executor_socket,
            username=payload.username,
            password=password,
            timeout_seconds=settings.account_executor_timeout_seconds,
        )
    except AccountExecutorError as exc:
        record_audit(
            session,
            principal=principal,
            action="media_account.provision",
            outcome="failed",
            correlation_id=request.state.request_id,
            target_type="media_account",
            target_id=payload.username,
            details={"code": exc.code},
        )
        session.commit()
        status = 409 if exc.code in {"account_name_in_use", "managed_account_changed"} else 503
        raise Problem(status, exc.code, "Account creation failed", str(exc)) from exc
    record_audit(
        session,
        principal=principal,
        action="media_account.provision",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="media_account",
        target_id=payload.username,
        details={
            "created": result.get("created") is True,
            "password_updated": result.get("password_updated") is True,
            "smb_enabled": result.get("smb_enabled") is True,
        },
    )
    return {
        "account": {
            "username": payload.username,
            "created": result.get("created") is True,
            "password_updated": result.get("password_updated") is True,
            "smb_enabled": result.get("smb_enabled") is True,
            "shell_login": False,
        },
        "credential": {
            "generated": generated,
            "password": password if generated else None,
            "display_once": generated,
        },
    }
