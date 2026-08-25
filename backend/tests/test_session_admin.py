from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from hoardarr.auth.service import (
    SessionRevocationError,
    active_session_count,
    revoke_all_active_sessions,
)
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.db.models import ApiToken, AuditEvent, AuthSession, User, utc_now


def runtime(root: Path):  # type: ignore[no-untyped-def]
    database = root / "sessions.db"
    url = f"sqlite:///{database.as_posix()}"
    upgrade_database(url)
    engine = create_database_engine(url)
    return engine, create_session_factory(engine)


def seed(factory, *, active: int = 3) -> dict[str, object]:  # type: ignore[no-untyped-def]
    users = [
        User(id="user-a", username="owner", password_hash="hash-a", is_admin=True),
        User(id="user-b", username="viewer", password_hash="hash-b", is_admin=False),
    ]
    with factory() as session, session.begin():
        session.add_all(users)
        session.flush()
        for index in range(active):
            session.add(
                AuthSession(
                    id=f"active-{index}",
                    user_id=users[index % 2].id,
                    token_hash=f"{index + 1:064x}",
                    csrf_hash=f"{index + 101:064x}",
                    expires_at=utc_now() + timedelta(hours=1),
                )
            )
        session.add(
            AuthSession(
                id="expired",
                user_id="user-a",
                token_hash="e" * 64,
                csrf_hash="f" * 64,
                expires_at=utc_now() - timedelta(hours=1),
            )
        )
        session.add(
            ApiToken(
                id="api-token",
                user_id="user-a",
                name="automation",
                token_hash="a" * 64,
                scopes_json=["read"],
            )
        )
        session.add(
            AuditEvent(
                id=100,
                actor_type="session",
                actor_id="user-a",
                action="historical.event",
                outcome="succeeded",
                correlation_id="historical-correlation",
                details_json={"kept": True},
            )
        )
    return {
        "users": [(item.id, item.username, item.password_hash, item.is_admin) for item in users],
        "token_hash": "a" * 64,
    }


def call(factory, *, expected: int, reason: str = "migration-cutover", hook=None):  # type: ignore[no-untyped-def]
    with factory() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        try:
            result = revoke_all_active_sessions(
                session,
                expected_count=expected,
                reason=reason,
                failure_hook=hook,
            )
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise


def test_zero_session_revoke_is_deterministic_and_audited(tmp_path: Path) -> None:
    engine, factory = runtime(tmp_path)
    seed(factory, active=0)
    result = call(factory, expected=0)
    assert result == {
        "schema_version": 1,
        "status": "succeeded",
        "expected_count": 0,
        "observed_count": 0,
        "revoked_count": 0,
        "remaining_active_count": 0,
        "reason": "migration-cutover",
        "audit_event_id": result["audit_event_id"],
    }
    with factory() as session:
        event = session.get(AuditEvent, result["audit_event_id"])
        assert event is not None
        assert event.actor_type == "local_console"
        assert event.details_json["revoked_count"] == 0
        assert session.get(AuthSession, "expired") is not None
    engine.dispose()


def test_exact_multi_user_revoke_keeps_accounts_tokens_roles_and_prior_audit(
    tmp_path: Path,
) -> None:
    engine, factory = runtime(tmp_path)
    before = seed(factory)
    result = call(factory, expected=3)
    assert result["revoked_count"] == 3
    assert result["remaining_active_count"] == 0
    with factory() as session:
        assert active_session_count(session) == 0
        assert session.get(AuthSession, "expired") is not None
        users = [
            (item.id, item.username, item.password_hash, item.is_admin)
            for item in session.scalars(select(User).order_by(User.id))
        ]
        assert users == before["users"]
        token = session.get(ApiToken, "api-token")
        assert token is not None and token.token_hash == before["token_hash"]
        assert session.get(AuditEvent, 100).details_json == {"kept": True}  # type: ignore[union-attr]
        event = session.get(AuditEvent, result["audit_event_id"])
        assert event is not None
        assert event.action == "auth.sessions.revoke_all"
        assert event.details_json == {
            "reason": "migration-cutover",
            "expected_count": 3,
            "observed_count": 3,
            "revoked_count": 3,
        }
        serialized = str(event.details_json)
        assert "token_hash" not in serialized and "csrf" not in serialized
    engine.dispose()


def test_count_mismatch_revokes_nothing(tmp_path: Path) -> None:
    engine, factory = runtime(tmp_path)
    seed(factory)
    with pytest.raises(SessionRevocationError) as caught:
        call(factory, expected=2)
    assert caught.value.code == "session_count_mismatch"
    assert caught.value.observed_count == 3
    with factory() as session:
        assert active_session_count(session) == 3
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
    engine.dispose()


def test_concurrent_count_drift_and_audit_failure_roll_back(tmp_path: Path) -> None:
    engine, factory = runtime(tmp_path)
    seed(factory)

    def add_session(_phase: str, session) -> None:  # type: ignore[no-untyped-def]
        session.add(
            AuthSession(
                id="drifted",
                user_id="user-a",
                token_hash="d" * 64,
                csrf_hash="c" * 64,
                expires_at=utc_now() + timedelta(hours=1),
            )
        )
        session.flush()

    with pytest.raises(SessionRevocationError) as drift:
        call(factory, expected=3, hook=add_session)
    assert drift.value.code == "session_count_drift"
    with factory() as session:
        assert active_session_count(session) == 3
        assert session.get(AuthSession, "drifted") is None

    def fail_audit(phase: str, _session) -> None:  # type: ignore[no-untyped-def]
        if phase == "after_audit":
            raise RuntimeError("injected audit failure")

    with pytest.raises(RuntimeError, match="injected"):
        call(factory, expected=3, hook=fail_audit)
    with factory() as session:
        assert active_session_count(session) == 3
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
    engine.dispose()


@pytest.mark.parametrize("reason", ["", "contains spaces", "x" * 129])
def test_reason_is_strict_and_sanitized(tmp_path: Path, reason: str) -> None:
    engine, factory = runtime(tmp_path)
    seed(factory, active=0)
    with pytest.raises(SessionRevocationError) as caught:
        call(factory, expected=0, reason=reason)
    assert caught.value.code == "reason_invalid"
    engine.dispose()
