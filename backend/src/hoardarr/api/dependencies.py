from __future__ import annotations

import re
from collections.abc import Iterator

from fastapi import Depends, Header, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from hoardarr.api.problem import Problem
from hoardarr.auth.service import (
    Principal,
    principal_from_api_token,
    principal_from_session,
    tokens_equal,
)
from hoardarr.core.config import Settings
from hoardarr.core.secrets import SecretBox
from hoardarr.db.models import AuthSession

IDEMPOTENCY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}")
BEARER_SCHEME = HTTPBearer(
    auto_error=False,
    scheme_name="HoardarrApiToken",
    description="Personal API token issued by Hoardarr",
)


def settings_from_request(request: Request) -> Settings:
    return request.app.state.settings


def secret_box_from_request(request: Request) -> SecretBox:
    return request.app.state.secret_box


def database_session(request: Request) -> Iterator[Session]:
    if not request.app.state.database_ready:
        raise Problem(
            503,
            "database_not_ready",
            "Database not ready",
            "Database migrations must be applied before using the API.",
        )
    session = request.app.state.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def authenticated_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(BEARER_SCHEME),
    session: Session = Depends(database_session),
) -> Principal:
    authorization = request.headers.get("authorization")
    principal: Principal | None = None
    if authorization:
        if credentials is not None and credentials.scheme.casefold() == "bearer":
            principal = principal_from_api_token(session, credentials.credentials)
    else:
        token = request.cookies.get(request.app.state.settings.session_cookie_name)
        if token:
            principal = principal_from_session(session, token)
    if principal is None:
        raise Problem(
            401,
            "authentication_required",
            "Authentication required",
            "Provide a valid session or API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def require_scope(scope: str):  # type: ignore[no-untyped-def]
    def dependency(principal: Principal = Depends(authenticated_principal)) -> Principal:
        if principal.is_admin or scope in principal.scopes:
            return principal
        raise Problem(403, "insufficient_scope", "Forbidden", f"The {scope} scope is required.")

    return dependency


def _request_origin(request: Request) -> str:
    host = request.headers.get("host", request.url.netloc)
    return f"{request.url.scheme}://{host}".lower()


def validate_origin(request: Request, *, required: bool) -> None:
    supplied = request.headers.get("origin")
    if supplied is None:
        if required:
            raise Problem(403, "origin_required", "Forbidden", "An Origin header is required.")
        return
    settings: Settings = request.app.state.settings
    allowed = set(settings.allowed_origins) or {_request_origin(request)}
    if supplied.rstrip("/").lower() not in allowed:
        raise Problem(403, "origin_rejected", "Forbidden", "The request origin is not allowed.")


def authorize_state_change(
    request: Request,
    principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> Principal:
    if principal.auth_type == "session":
        validate_origin(request, required=True)
        csrf = request.headers.get("x-csrf-token")
        record = session.get(AuthSession, principal.session_id)
        if record is None or csrf is None or not tokens_equal(record.csrf_hash, csrf):
            raise Problem(
                403, "csrf_rejected", "Forbidden", "The CSRF token is missing or invalid."
            )
    return principal


def require_state_scope(scope: str):  # type: ignore[no-untyped-def]
    def dependency(
        principal: Principal = Depends(authorize_state_change),
    ) -> Principal:
        if principal.is_admin or scope in principal.scopes:
            return principal
        raise Problem(403, "insufficient_scope", "Forbidden", f"The {scope} scope is required.")

    return dependency


def idempotency_key(value: str | None = Header(default=None, alias="Idempotency-Key")) -> str:
    if value is None or not IDEMPOTENCY_RE.fullmatch(value):
        raise Problem(
            400,
            "idempotency_key_required",
            "Idempotency key required",
            "Idempotency-Key must contain 8-128 safe characters.",
        )
    return value
