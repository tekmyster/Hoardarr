from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, select, update
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
    if user is None:
        raise AuthenticationError("invalid username or password")
    if PASSWORD_HASHER.check_needs_rehash(user.password_hash):
        user.password_hash = PASSWORD_HASHER.hash(password)
    return user


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
    if user is None:
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
    if user is None:
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
