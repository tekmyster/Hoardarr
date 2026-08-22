from __future__ import annotations

from datetime import UTC

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hoardarr.api.client import client_identity
from hoardarr.api.dependencies import (
    authenticated_principal,
    authorize_state_change,
    database_session,
    require_scope,
    require_state_scope,
    validate_origin,
)
from hoardarr.api.problem import Problem
from hoardarr.api.rate_limit import RateLimitExceeded
from hoardarr.api.schemas import LoginRequest, SetupClaimRequest, TokenCreateRequest
from hoardarr.api.serializers import token_document, user_document
from hoardarr.audit.service import record_audit, record_unauthenticated_audit
from hoardarr.auth.service import (
    AuthenticationError,
    Principal,
    SetupUnavailableError,
    authenticate_password,
    claim_setup,
    create_api_token,
    create_session,
    refresh_session_csrf,
    revoke_session,
    tokens_equal,
)
from hoardarr.db.models import ApiToken, AuthSession, SetupClaim, User, utc_now

router = APIRouter(prefix="/auth", tags=["authentication"])
setup_router = APIRouter(prefix="/setup", tags=["setup"])


def _set_session_cookie(
    response: Response,
    request: Request,
    token: str,
    *,
    max_age_seconds: int | None,
) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=max_age_seconds,
        secure=settings.secure_cookies,
        httponly=True,
        samesite="strict",
        path="/",
    )


def _set_csrf_cookie(
    response: Response,
    request: Request,
    token: str,
    *,
    max_age_seconds: int | None,
) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        settings.csrf_cookie_name,
        token,
        max_age=max_age_seconds,
        secure=settings.secure_cookies,
        httponly=False,
        samesite="strict",
        path="/",
    )


def _login_keys(request: Request, username: str) -> tuple[str, str]:
    address = client_identity(request)
    return address, f"account|{username.strip().casefold()}"


def _rate_limited(exc: RateLimitExceeded) -> Problem:
    return Problem(
        429,
        "authentication_rate_limited",
        "Too many attempts",
        "Wait before trying again.",
        headers={"Retry-After": str(exc.retry_after)},
    )


@setup_router.get("/status")
def setup_status(session: Session = Depends(database_session)) -> dict[str, object]:
    configured = bool(session.scalar(select(func.count()).select_from(User)))
    claim = session.get(SetupClaim, "initial-owner")
    available = bool(
        not configured
        and claim is not None
        and claim.consumed_at is None
        and (
            claim.expires_at.replace(tzinfo=UTC)
            if claim.expires_at.tzinfo is None
            else claim.expires_at
        )
        > utc_now()
    )
    return {"configured": configured, "claim_available": available}


@setup_router.post("/claim", status_code=201)
def setup_claim(
    payload: SetupClaimRequest,
    request: Request,
    response: Response,
    session: Session = Depends(database_session),
) -> dict[str, object]:
    validate_origin(request, required=False)
    try:
        request.app.state.setup_limiter.consume(client_identity(request))
    except RateLimitExceeded as exc:
        raise _rate_limited(exc) from exc
    if session.scalar(select(func.count()).select_from(User)):
        raise Problem(
            409,
            "setup_unavailable",
            "Setup unavailable",
            "Initial setup has already been completed.",
        )
    try:
        user = claim_setup(
            session,
            token=payload.token.get_secret_value(),
            username=payload.username,
            password=payload.password.get_secret_value(),
        )
    except SetupUnavailableError as exc:
        raise Problem(409, "setup_unavailable", "Setup unavailable", str(exc)) from exc
    except AuthenticationError as exc:
        raise Problem(401, "setup_claim_rejected", "Setup claim rejected", str(exc)) from exc
    issued = create_session(session, user, request.app.state.settings.session_ttl_seconds)
    principal = Principal(
        user_id=user.id,
        username=user.username,
        is_admin=True,
        auth_type="session",
        scopes=frozenset({"admin"}),
        session_id=issued.session.id,
    )
    record_audit(
        session,
        principal=principal,
        action="setup.claim",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="user",
        target_id=user.id,
    )
    # Publish browser credentials only after their database records are durable.
    # Yield-dependency cleanup may run after response headers have been sent, and
    # an immediate browser request must never receive a cookie for an uncommitted
    # session.
    session.commit()
    _set_session_cookie(
        response,
        request,
        issued.session_token,
        max_age_seconds=request.app.state.settings.session_ttl_seconds,
    )
    _set_csrf_cookie(
        response,
        request,
        issued.csrf_token,
        max_age_seconds=request.app.state.settings.session_ttl_seconds,
    )
    return {"user": user_document(user), "csrf_token": issued.csrf_token}


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(database_session),
) -> dict[str, object]:
    validate_origin(request, required=False)
    ip_key, account_key = _login_keys(request, payload.username)
    try:
        request.app.state.login_ip_limiter.consume(ip_key)
        try:
            request.app.state.login_limiter.consume(account_key)
        except RateLimitExceeded:
            request.app.state.login_ip_limiter.refund(ip_key)
            raise
    except RateLimitExceeded as exc:
        raise _rate_limited(exc) from exc
    if not request.app.state.authentication_slots.acquire(blocking=False):
        request.app.state.login_ip_limiter.refund(ip_key)
        request.app.state.login_limiter.refund(account_key)
        raise Problem(
            429,
            "authentication_busy",
            "Authentication busy",
            "Wait briefly before trying again.",
            headers={"Retry-After": "1"},
        )
    try:
        try:
            user = authenticate_password(
                session, payload.username, payload.password.get_secret_value()
            )
        except AuthenticationError as exc:
            record_unauthenticated_audit(
                session,
                action="auth.login",
                outcome="rejected",
                correlation_id=request.state.request_id,
            )
            session.commit()
            raise Problem(
                401, "login_rejected", "Sign-in failed", "Invalid username or password."
            ) from exc
    finally:
        request.app.state.authentication_slots.release()
    request.app.state.login_ip_limiter.refund(ip_key)
    request.app.state.login_limiter.refund(account_key)
    settings = request.app.state.settings
    session_ttl = (
        settings.remembered_session_ttl_seconds
        if payload.remember_me
        else settings.session_ttl_seconds
    )
    issued = create_session(session, user, session_ttl)
    principal = Principal(
        user_id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        auth_type="session",
        scopes=frozenset({"admin"}) if user.is_admin else frozenset(),
        session_id=issued.session.id,
    )
    record_audit(
        session,
        principal=principal,
        action="auth.login",
        outcome="succeeded",
        correlation_id=request.state.request_id,
    )
    session.commit()
    _set_session_cookie(
        response,
        request,
        issued.session_token,
        max_age_seconds=session_ttl if payload.remember_me else None,
    )
    _set_csrf_cookie(
        response,
        request,
        issued.csrf_token,
        max_age_seconds=session_ttl if payload.remember_me else None,
    )
    return {"user": user_document(user), "csrf_token": issued.csrf_token}


@router.get("/me")
def me(
    request: Request,
    response: Response,
    principal: Principal = Depends(authenticated_principal),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    user = session.get(User, principal.user_id)
    if user is None:
        raise Problem(
            401, "authentication_required", "Authentication required", "User is unavailable."
        )
    csrf_token: str | None = None
    if principal.auth_type == "session":
        if principal.session_id is None:
            raise Problem(
                401, "authentication_required", "Authentication required", "Session is unavailable."
            )
        record = session.get(AuthSession, principal.session_id)
        supplied_csrf = request.cookies.get(request.app.state.settings.csrf_cookie_name)
        if record is not None and supplied_csrf and tokens_equal(record.csrf_hash, supplied_csrf):
            csrf_token = supplied_csrf
        else:
            try:
                csrf_token = refresh_session_csrf(session, principal.session_id)
                session.commit()
            except AuthenticationError as exc:
                raise Problem(
                    401,
                    "authentication_required",
                    "Authentication required",
                    "Session is unavailable.",
                ) from exc
        _set_csrf_cookie(response, request, csrf_token, max_age_seconds=None)
    response.headers["Cache-Control"] = "no-store"
    return {
        "user": user_document(user),
        "auth_type": principal.auth_type,
        "scopes": sorted(principal.scopes),
        "csrf_token": csrf_token,
    }


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    principal: Principal = Depends(authorize_state_change),
    session: Session = Depends(database_session),
) -> Response:
    if principal.session_id is None:
        raise Problem(
            400, "session_required", "Session required", "API tokens cannot be logged out."
        )
    revoke_session(session, principal.session_id)
    record_audit(
        session,
        principal=principal,
        action="auth.logout",
        outcome="succeeded",
        correlation_id=request.state.request_id,
    )
    settings = request.app.state.settings
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="strict",
    )
    response.delete_cookie(
        settings.csrf_cookie_name,
        path="/",
        secure=settings.secure_cookies,
        httponly=False,
        samesite="strict",
    )
    response.status_code = 204
    return response


@router.get("/tokens")
def list_tokens(
    principal: Principal = Depends(require_scope("admin")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    tokens = session.scalars(
        select(ApiToken).where(ApiToken.user_id == principal.user_id).order_by(ApiToken.created_at)
    )
    return {"items": [token_document(token) for token in tokens]}


@router.post("/tokens", status_code=201)
def issue_token(
    payload: TokenCreateRequest,
    request: Request,
    principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
) -> dict[str, object]:
    user = session.get(User, principal.user_id)
    if user is None:
        raise Problem(
            401, "authentication_required", "Authentication required", "User is unavailable."
        )
    if payload.expires_at is not None:
        expires_at = payload.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        else:
            expires_at = expires_at.astimezone(UTC)
        if expires_at <= utc_now():
            raise Problem(
                422, "invalid_expiry", "Invalid expiry", "Token expiry must be in the future."
            )
    else:
        expires_at = None
    try:
        token, raw = create_api_token(
            session,
            user=user,
            name=payload.name,
            scopes=list(payload.scopes),
            expires_at=expires_at,
        )
    except AuthenticationError as exc:
        raise Problem(422, "invalid_token", "Invalid API token", str(exc)) from exc
    record_audit(
        session,
        principal=principal,
        action="auth.token.create",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="api_token",
        target_id=token.id,
        details={"scopes": token.scopes_json},
    )
    return {"token": token_document(token), "secret": raw}


@router.delete("/tokens/{token_id}", status_code=204)
def delete_token(
    token_id: str,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_state_scope("admin")),
    session: Session = Depends(database_session),
) -> Response:
    token = session.get(ApiToken, token_id)
    if token is None or token.user_id != principal.user_id:
        raise Problem(404, "token_not_found", "Not found", "API token was not found.")
    session.delete(token)
    record_audit(
        session,
        principal=principal,
        action="auth.token.delete",
        outcome="succeeded",
        correlation_id=request.state.request_id,
        target_type="api_token",
        target_id=token.id,
    )
    response.status_code = 204
    return response
