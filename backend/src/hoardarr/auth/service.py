from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from hoardarr.db.models import ApiToken, AuthSession, SetupClaim, User, utc_now

USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{2,63}")
PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("not-a-real-hoardarr-password")


class AuthenticationError(RuntimeError):
    pass


class SetupUnavailableError(AuthenticationError):
    pass


class SessionRevocationError(AuthenticationError):
    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        expected_count: int,
        observed_count: int,
        exit_code: int = 3,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.expected_count = expected_count
        self.observed_count = observed_count
        self.exit_code = exit_code


class PasswordResetError(AuthenticationError):
    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        expected_active_sessions: int,
        observed_active_sessions: int = 0,
        user_id: str | None = None,
        username: str | None = None,
        exit_code: int = 3,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.expected_active_sessions = expected_active_sessions
        self.observed_active_sessions = observed_active_sessions
        self.user_id = user_id
        self.username = username
        self.exit_code = exit_code


@dataclass(frozen=True)
class IssuedSession:
    session: AuthSession
    session_token: str
    csrf_token: str


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    is_admin: bool
    auth_type: str
    scopes: frozenset[str]
    session_id: str | None = None
    api_token_id: str | None = None


@dataclass(frozen=True)
class PasswordResetSnapshot:
    user_id: str
    username: str
    is_admin: bool
    is_active: bool
    password_hash: str = field(repr=False)
    password_hash_version: str = field(repr=False)
    observed_active_sessions: int


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def tokens_equal(first_hash: str, raw_second: str) -> bool:
    return hmac.compare_digest(first_hash, hash_token(raw_second))


def normalize_username(value: str) -> str:
    normalized = value.strip().casefold()
    if not USERNAME_RE.fullmatch(normalized):
        raise AuthenticationError(
            "username must be 3-64 lowercase letters, numbers, dots, dashes, or underscores"
        )
    return normalized


def validate_password(value: str) -> None:
    if not value:
        raise AuthenticationError("password cannot be empty")


def _owner(username: str, password: str) -> User:
    return User(
        username=normalize_username(username),
        password_hash=PASSWORD_HASHER.hash(password),
        is_admin=True,
    )


def create_initial_owner(session: Session, *, username: str, password: str) -> User:
    """Create the first administrator from a trusted local setup command."""
    validate_password(password)
    if session.scalar(select(func.count()).select_from(User)):
        raise SetupUnavailableError("initial setup has already been completed")
    user = _owner(username, password)
    session.add(user)
    session.flush()
    return user


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def issue_setup_token(session: Session, *, ttl_seconds: int = 900) -> str:
    if session.scalar(select(func.count()).select_from(User)):
        raise SetupUnavailableError("an administrator already exists")
    raw = "hsetup_" + secrets.token_urlsafe(32)
    claim = session.get(SetupClaim, "initial-owner")
    if claim is None:
        claim = SetupClaim(id="initial-owner", token_hash=hash_token(raw), expires_at=utc_now())
        session.add(claim)
    claim.token_hash = hash_token(raw)
    claim.expires_at = utc_now() + timedelta(seconds=ttl_seconds)
    claim.consumed_at = None
    claim.created_at = utc_now()
    session.flush()
    return raw


def claim_setup(session: Session, *, token: str, username: str, password: str) -> User:
    normalized = normalize_username(username)
    validate_password(password)
    if session.scalar(select(func.count()).select_from(User)):
        raise SetupUnavailableError("initial setup has already been completed")
    now = utc_now()
    consumed = session.execute(
        update(SetupClaim)
        .where(
            SetupClaim.id == "initial-owner",
            SetupClaim.consumed_at.is_(None),
            SetupClaim.expires_at > now,
            SetupClaim.token_hash == hash_token(token),
        )
        .values(consumed_at=now)
    )
    if consumed.rowcount != 1:
        raise AuthenticationError("setup token is invalid or expired")
    user = _owner(normalized, password)
    session.add(user)
    session.flush()
    return user


def authenticate_password(session: Session, username: str, password: str) -> User:
    try:
        normalized = normalize_username(username)
    except AuthenticationError:
        normalized = "invalid"
    user = session.scalar(select(User).where(User.username == normalized))
    stored = user.password_hash if user else DUMMY_PASSWORD_HASH
    try:
        PASSWORD_HASHER.verify(stored, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError) as exc:
        raise AuthenticationError("invalid username or password") from exc
    if user is None or not user.is_active:
        raise AuthenticationError("invalid username or password")
    return user


def refresh_password_hash_if_needed(user: User, password: str) -> None:
    """Upgrade a verified password hash only inside the caller's write transaction."""

    if PASSWORD_HASHER.check_needs_rehash(user.password_hash):
        user.password_hash = PASSWORD_HASHER.hash(password)


def create_session(session: Session, user: User, ttl_seconds: int) -> IssuedSession:
    raw_session = "hs_" + secrets.token_urlsafe(32)
    raw_csrf = "hc_" + secrets.token_urlsafe(32)
    record = AuthSession(
        user_id=user.id,
        token_hash=hash_token(raw_session),
        csrf_hash=hash_token(raw_csrf),
        expires_at=utc_now() + timedelta(seconds=ttl_seconds),
    )
    session.add(record)
    session.flush()
    return IssuedSession(record, raw_session, raw_csrf)


def refresh_session_csrf(session: Session, session_id: str) -> str:
    record = session.get(AuthSession, session_id)
    if record is None or _aware(record.expires_at) <= utc_now():
        raise AuthenticationError("session is unavailable or expired")
    raw_csrf = "hc_" + secrets.token_urlsafe(32)
    record.csrf_hash = hash_token(raw_csrf)
    record.last_seen_at = utc_now()
    session.flush()
    return raw_csrf


def principal_from_session(session: Session, raw_token: str) -> Principal | None:
    record = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(raw_token))
    )
    if record is None or _aware(record.expires_at) <= utc_now():
        return None
    user = session.get(User, record.user_id)
    if user is None or not user.is_active:
        return None
    if _aware(record.last_seen_at) < utc_now() - timedelta(minutes=5):
        record.last_seen_at = utc_now()
    return Principal(
        user_id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        auth_type="session",
        scopes=frozenset({"admin"}) if user.is_admin else frozenset(),
        session_id=record.id,
    )


def principal_from_api_token(session: Session, raw_token: str) -> Principal | None:
    record = session.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(raw_token)))
    if record is None or (record.expires_at and _aware(record.expires_at) <= utc_now()):
        return None
    user = session.get(User, record.user_id)
    if user is None or not user.is_active:
        return None
    if record.last_used_at is None or _aware(record.last_used_at) < utc_now() - timedelta(
        minutes=5
    ):
        record.last_used_at = utc_now()
    scopes = frozenset(record.scopes_json)
    return Principal(
        user_id=user.id,
        username=user.username,
        is_admin="admin" in scopes,
        auth_type="api_token",
        scopes=scopes,
        api_token_id=record.id,
    )


def revoke_session(session: Session, session_id: str) -> bool:
    record = session.get(AuthSession, session_id)
    if record is None:
        return False
    session.delete(record)
    return True


SESSION_REVOCATION_REASON_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
LOCAL_CONSOLE_ACTOR_ID = "00000000-0000-0000-0000-000000000000"
SessionRevocationHook = Callable[[str, Session], None]
PasswordResetHook = Callable[[str, Session], None]


def active_session_count(session: Session, *, now: datetime | None = None) -> int:
    """Count sessions accepted by the same expiry boundary used during authentication."""
    observed_at = now or utc_now()
    return int(
        session.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(AuthSession.expires_at > observed_at)
        )
        or 0
    )


def active_user_session_count(
    session: Session,
    *,
    user_id: str,
    now: datetime | None = None,
) -> int:
    """Count one user's sessions at the authentication expiry boundary."""
    observed_at = now or utc_now()
    return int(
        session.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.expires_at > observed_at,
            )
        )
        or 0
    )


def _password_hash_version(value: str) -> str:
    try:
        parameters = extract_parameters(value)
    except InvalidHashError as exc:
        raise PasswordResetError(
            "password_verifier_unsupported",
            "The account password verifier is not supported for a protected reset.",
            expected_active_sessions=0,
            exit_code=4,
        ) from exc
    return f"{parameters.type.name.casefold()}-v{parameters.version}"


def _password_reset_users(session: Session, normalized_username: str) -> list[User]:
    return list(
        session.scalars(
            select(User).where(User.username == normalized_username).limit(2)
        )
    )


def inspect_administrator_password_reset(
    session: Session,
    *,
    username: str,
    expected_active_sessions: int,
) -> PasswordResetSnapshot:
    """Resolve a reset target without consuming or accepting the new password."""
    if not 0 <= expected_active_sessions <= 1_000_000:
        raise PasswordResetError(
            "expected_active_sessions_invalid",
            "Expected active-session count must be between 0 and 1,000,000.",
            expected_active_sessions=expected_active_sessions,
            exit_code=2,
        )
    try:
        normalized = normalize_username(username)
    except AuthenticationError as exc:
        raise PasswordResetError(
            "username_invalid",
            "Username must be an exact normalized Hoardarr username.",
            expected_active_sessions=expected_active_sessions,
            exit_code=2,
        ) from exc
    users = _password_reset_users(session, normalized)
    if not users:
        raise PasswordResetError(
            "user_not_found",
            "The exact active administrator account was not found.",
            expected_active_sessions=expected_active_sessions,
            username=normalized,
            exit_code=4,
        )
    if len(users) != 1:
        raise PasswordResetError(
            "user_ambiguous",
            "The username did not resolve to exactly one account.",
            expected_active_sessions=expected_active_sessions,
            username=normalized,
            exit_code=4,
        )
    user = users[0]
    if not user.is_active:
        raise PasswordResetError(
            "user_disabled",
            "The exact administrator account is disabled.",
            expected_active_sessions=expected_active_sessions,
            user_id=user.id,
            username=user.username,
            exit_code=4,
        )
    if not user.is_admin:
        raise PasswordResetError(
            "user_role_unexpected",
            "The exact account is not a Hoardarr administrator.",
            expected_active_sessions=expected_active_sessions,
            user_id=user.id,
            username=user.username,
            exit_code=4,
        )
    try:
        version = _password_hash_version(user.password_hash)
    except PasswordResetError as exc:
        exc.expected_active_sessions = expected_active_sessions
        exc.user_id = user.id
        exc.username = user.username
        raise
    observed_at = utc_now()
    observed = active_user_session_count(session, user_id=user.id, now=observed_at)
    if observed != expected_active_sessions:
        raise PasswordResetError(
            "active_session_count_mismatch",
            "Active-session count does not match the supplied precondition.",
            expected_active_sessions=expected_active_sessions,
            observed_active_sessions=observed,
            user_id=user.id,
            username=user.username,
            exit_code=4,
        )
    return PasswordResetSnapshot(
        user_id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        is_active=user.is_active,
        password_hash=user.password_hash,
        password_hash_version=version,
        observed_active_sessions=observed,
    )


def reset_administrator_password(
    session: Session,
    *,
    snapshot: PasswordResetSnapshot,
    expected_active_sessions: int,
    new_password: str,
    failure_hook: PasswordResetHook | None = None,
) -> dict[str, object]:
    """Reset one administrator and revoke only its active sessions atomically."""
    if snapshot.observed_active_sessions != expected_active_sessions:
        raise PasswordResetError(
            "active_session_count_drift",
            "Active-session preflight no longer matches the requested reset.",
            expected_active_sessions=expected_active_sessions,
            observed_active_sessions=snapshot.observed_active_sessions,
            user_id=snapshot.user_id,
            username=snapshot.username,
            exit_code=4,
        )
    try:
        validate_password(new_password)
    except AuthenticationError as exc:
        raise PasswordResetError(
            "password_invalid",
            "The new password does not satisfy the local account password policy.",
            expected_active_sessions=expected_active_sessions,
            observed_active_sessions=snapshot.observed_active_sessions,
            user_id=snapshot.user_id,
            username=snapshot.username,
            exit_code=2,
        ) from exc

    users = _password_reset_users(session, snapshot.username)
    if len(users) != 1:
        raise PasswordResetError(
            "user_identity_drift",
            "The account identity changed after reset preflight.",
            expected_active_sessions=expected_active_sessions,
            observed_active_sessions=snapshot.observed_active_sessions,
            user_id=snapshot.user_id,
            username=snapshot.username,
            exit_code=4,
        )
    user = users[0]
    try:
        current_hash_version = _password_hash_version(user.password_hash)
    except PasswordResetError as exc:
        exc.expected_active_sessions = expected_active_sessions
        exc.observed_active_sessions = snapshot.observed_active_sessions
        exc.user_id = snapshot.user_id
        exc.username = snapshot.username
        raise
    if (
        user.id != snapshot.user_id
        or user.username != snapshot.username
        or user.is_admin != snapshot.is_admin
        or user.is_active != snapshot.is_active
        or not user.is_admin
        or not user.is_active
        or not hmac.compare_digest(user.password_hash, snapshot.password_hash)
        or current_hash_version != snapshot.password_hash_version
    ):
        raise PasswordResetError(
            "user_identity_drift",
            "The account identity changed after reset preflight.",
            expected_active_sessions=expected_active_sessions,
            observed_active_sessions=snapshot.observed_active_sessions,
            user_id=snapshot.user_id,
            username=snapshot.username,
            exit_code=4,
        )

    now = utc_now()
    observed = active_user_session_count(session, user_id=user.id, now=now)
    if observed != expected_active_sessions:
        raise PasswordResetError(
            "active_session_count_drift",
            "Active-session count changed after reset preflight.",
            expected_active_sessions=expected_active_sessions,
            observed_active_sessions=observed,
            user_id=user.id,
            username=user.username,
            exit_code=4,
        )
    if failure_hook is not None:
        failure_hook("after_count", session)
    rechecked = active_user_session_count(session, user_id=user.id, now=now)
    if rechecked != expected_active_sessions:
        raise PasswordResetError(
            "active_session_count_drift",
            "Active-session count changed inside the protected transaction.",
            expected_active_sessions=expected_active_sessions,
            observed_active_sessions=rechecked,
            user_id=user.id,
            username=user.username,
            exit_code=4,
        )

    try:
        if PASSWORD_HASHER.verify(user.password_hash, new_password):
            raise PasswordResetError(
                "password_unchanged",
                "The new password must differ from the current password.",
                expected_active_sessions=expected_active_sessions,
                observed_active_sessions=observed,
                user_id=user.id,
                username=user.username,
                exit_code=4,
            )
    except VerifyMismatchError:
        pass
    except PasswordResetError:
        raise
    except (InvalidHashError, VerificationError) as exc:
        raise PasswordResetError(
            "password_verifier_unsupported",
            "The account password verifier is not supported for a protected reset.",
            expected_active_sessions=expected_active_sessions,
            observed_active_sessions=observed,
            user_id=user.id,
            username=user.username,
            exit_code=4,
        ) from exc

    new_password_hash = PASSWORD_HASHER.hash(new_password)
    updated = session.execute(
        update(User)
        .where(
            User.id == snapshot.user_id,
            User.username == snapshot.username,
            User.is_admin.is_(True),
            User.is_active.is_(True),
            User.password_hash == snapshot.password_hash,
        )
        .values(password_hash=new_password_hash)
        .execution_options(synchronize_session=False)
    )
    if int(getattr(updated, "rowcount", 0) or 0) != 1:
        raise PasswordResetError(
            "password_update_mismatch",
            "The exact administrator password row could not be updated.",
            expected_active_sessions=expected_active_sessions,
            observed_active_sessions=observed,
            user_id=user.id,
            username=user.username,
            exit_code=5,
        )
    session.expire(user, ["password_hash"])
    if failure_hook is not None:
        failure_hook("after_password_update", session)

    deleted = session.execute(
        delete(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.expires_at > now,
        )
    )
    revoked = int(getattr(deleted, "rowcount", 0) or 0)
    if revoked != expected_active_sessions:
        raise PasswordResetError(
            "session_delete_mismatch",
            "The exact active-session set could not be revoked.",
            expected_active_sessions=expected_active_sessions,
            observed_active_sessions=observed,
            user_id=user.id,
            username=user.username,
            exit_code=5,
        )
    if failure_hook is not None:
        failure_hook("after_session_delete", session)

    expired_preserved = int(
        session.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(
                AuthSession.user_id == user.id,
                AuthSession.expires_at <= now,
            )
        )
        or 0
    )
    from hoardarr.audit.service import record_audit

    event = record_audit(
        session,
        principal=Principal(
            user_id=LOCAL_CONSOLE_ACTOR_ID,
            username="local-console",
            is_admin=True,
            auth_type="local_console",
            scopes=frozenset({"admin"}),
        ),
        action="auth.password.reset",
        outcome="succeeded",
        correlation_id=str(uuid.uuid4()),
        target_type="user",
        target_id=user.id,
        details={
            "expected_active_sessions": expected_active_sessions,
            "observed_active_sessions": observed,
            "revoked_active_sessions": revoked,
            "preserved_expired_sessions": expired_preserved,
        },
    )
    session.flush()
    if failure_hook is not None:
        failure_hook("after_audit", session)

    session.expire(user, ["password_hash"])
    readback = session.get(User, user.id)
    try:
        hash_valid = bool(
            readback is not None
            and not hmac.compare_digest(readback.password_hash, snapshot.password_hash)
            and PASSWORD_HASHER.verify(readback.password_hash, new_password)
            and not PASSWORD_HASHER.check_needs_rehash(readback.password_hash)
        )
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        hash_valid = False
    if not hash_valid:
        raise PasswordResetError(
            "password_readback_failed",
            "Password reset readback failed.",
            expected_active_sessions=expected_active_sessions,
            observed_active_sessions=observed,
            user_id=user.id,
            username=user.username,
            exit_code=5,
        )
    remaining = active_user_session_count(session, user_id=user.id, now=now)
    if remaining != 0:
        raise PasswordResetError(
            "session_readback_failed",
            "Active-session readback was not zero after password reset.",
            expected_active_sessions=expected_active_sessions,
            observed_active_sessions=remaining,
            user_id=user.id,
            username=user.username,
            exit_code=5,
        )
    return {
        "schema_version": 1,
        "status": "succeeded",
        "user_id": user.id,
        "username": user.username,
        "expected_active_sessions": expected_active_sessions,
        "observed_active_sessions": observed,
        "revoked_active_sessions": revoked,
        "remaining_active_sessions": remaining,
        "preserved_expired_sessions": expired_preserved,
        "audit_event_id": event.id,
    }


def revoke_all_active_sessions(
    session: Session,
    *,
    expected_count: int,
    reason: str,
    failure_hook: SessionRevocationHook | None = None,
) -> dict[str, object]:
    """Atomically revoke an exact active-session set inside the caller-owned transaction."""
    if not 0 <= expected_count <= 1_000_000:
        raise SessionRevocationError(
            "expected_count_invalid",
            "Expected session count must be between 0 and 1,000,000.",
            expected_count=expected_count,
            observed_count=0,
            exit_code=2,
        )
    clean_reason = reason.strip().casefold()
    if not SESSION_REVOCATION_REASON_RE.fullmatch(clean_reason):
        raise SessionRevocationError(
            "reason_invalid",
            "Reason must contain 1-128 lowercase letters, numbers, dots, dashes, or underscores.",
            expected_count=expected_count,
            observed_count=0,
            exit_code=2,
        )
    now = utc_now()
    observed_count = active_session_count(session, now=now)
    if observed_count != expected_count:
        raise SessionRevocationError(
            "session_count_mismatch",
            "Active session count does not match the supplied precondition.",
            expected_count=expected_count,
            observed_count=observed_count,
            exit_code=4,
        )
    if failure_hook is not None:
        failure_hook("after_count", session)
    rechecked_count = active_session_count(session, now=now)
    if rechecked_count != expected_count:
        raise SessionRevocationError(
            "session_count_drift",
            "Active session count changed inside the protected transaction.",
            expected_count=expected_count,
            observed_count=rechecked_count,
            exit_code=4,
        )
    deleted = session.execute(delete(AuthSession).where(AuthSession.expires_at > now))
    revoked_count = int(getattr(deleted, "rowcount", 0) or 0)
    if revoked_count != expected_count:
        raise SessionRevocationError(
            "session_delete_mismatch",
            "The exact active-session set could not be revoked.",
            expected_count=expected_count,
            observed_count=observed_count,
            exit_code=5,
        )
    from hoardarr.audit.service import record_audit

    event = record_audit(
        session,
        principal=Principal(
            user_id=LOCAL_CONSOLE_ACTOR_ID,
            username="local-console",
            is_admin=True,
            auth_type="local_console",
            scopes=frozenset({"admin"}),
        ),
        action="auth.sessions.revoke_all",
        outcome="succeeded",
        correlation_id=str(uuid.uuid4()),
        target_type="auth_sessions",
        details={
            "reason": clean_reason,
            "expected_count": expected_count,
            "observed_count": observed_count,
            "revoked_count": revoked_count,
        },
    )
    session.flush()
    if failure_hook is not None:
        failure_hook("after_audit", session)
    remaining_count = active_session_count(session, now=now)
    if remaining_count != 0:
        raise SessionRevocationError(
            "session_readback_failed",
            "Active-session readback was not zero after revocation.",
            expected_count=expected_count,
            observed_count=remaining_count,
            exit_code=5,
        )
    return {
        "schema_version": 1,
        "status": "succeeded",
        "expected_count": expected_count,
        "observed_count": observed_count,
        "revoked_count": revoked_count,
        "remaining_active_count": remaining_count,
        "reason": clean_reason,
        "audit_event_id": event.id,
    }


def create_api_token(
    session: Session,
    *,
    user: User,
    name: str,
    scopes: list[str],
    expires_at: datetime | None,
) -> tuple[ApiToken, str]:
    clean_name = name.strip()
    if not 1 <= len(clean_name) <= 128:
        raise AuthenticationError("API token name must contain 1-128 characters")
    allowed = {"read", "operate", "admin"}
    if not scopes or not set(scopes) <= allowed:
        raise AuthenticationError("API token scopes must be read, operate, or admin")
    # API keys have their own unmistakable prefix. They must never be confused
    # with the short-lived hsetup_ browser-pairing credential.
    raw = "hak_" + secrets.token_urlsafe(32)
    normalized_expiry = _aware(expires_at) if expires_at is not None else None
    record = ApiToken(
        user_id=user.id,
        name=clean_name,
        token_hash=hash_token(raw),
        scopes_json=sorted(set(scopes)),
        expires_at=normalized_expiry,
    )
    session.add(record)
    session.flush()
    return record, raw
