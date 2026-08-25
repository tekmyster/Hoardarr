from __future__ import annotations

import io
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import func, select, text

import hoardarr.auth.service as auth_service
import hoardarr.cli as cli
from hoardarr.auth.service import (
    PASSWORD_HASHER,
    AuthenticationError,
    PasswordResetError,
    authenticate_password,
    hash_token,
    inspect_administrator_password_reset,
    principal_from_api_token,
    principal_from_session,
    reset_administrator_password,
)
from hoardarr.core.config import Settings
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.db.models import (
    ApiToken,
    AuditEvent,
    AuthSession,
    SetupClaim,
    User,
    utc_now,
)

OLD_PASSWORD = "old-disposable-password"
NEW_PASSWORD = "new-disposable-password"


def runtime(root: Path):  # type: ignore[no-untyped-def]
    database = root / "password-reset.db"
    url = f"sqlite:///{database.as_posix()}"
    upgrade_database(url)
    engine = create_database_engine(url)
    return engine, create_session_factory(engine)


def settings_for(root: Path) -> Settings:
    root.mkdir(parents=True, exist_ok=True)
    database = root / "hoardarr.db"
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{database.as_posix()}",
        secret_key_file=root / "secret.key",
        secure_cookies=False,
    )
    upgrade_database(settings.database_url)
    return settings


def seed(factory, *, active: int = 2):  # type: ignore[no-untyped-def]
    with factory() as session, session.begin():
        target = User(
            id="target-admin",
            username="owner",
            password_hash=PASSWORD_HASHER.hash(OLD_PASSWORD),
            is_admin=True,
            is_active=True,
        )
        other = User(
            id="other-admin",
            username="other",
            password_hash=PASSWORD_HASHER.hash("other-password"),
            is_admin=True,
            is_active=True,
        )
        session.add_all((target, other))
        session.flush()
        for index in range(active):
            session.add(
                AuthSession(
                    id=f"target-active-{index}",
                    user_id=target.id,
                    token_hash=f"{index + 1:064x}",
                    csrf_hash=f"{index + 101:064x}",
                    expires_at=utc_now() + timedelta(hours=1),
                )
            )
        session.add_all(
            (
                AuthSession(
                    id="target-expired",
                    user_id=target.id,
                    token_hash="e" * 64,
                    csrf_hash="f" * 64,
                    expires_at=utc_now() - timedelta(hours=1),
                ),
                AuthSession(
                    id="other-active",
                    user_id=other.id,
                    token_hash="d" * 64,
                    csrf_hash="c" * 64,
                    expires_at=utc_now() + timedelta(hours=1),
                ),
                ApiToken(
                    id="target-api-token",
                    user_id=target.id,
                    name="automation",
                    token_hash="a" * 64,
                    scopes_json=["read", "admin"],
                ),
                SetupClaim(
                    id="initial-owner",
                    token_hash="b" * 64,
                    expires_at=utc_now() - timedelta(days=1),
                    consumed_at=utc_now() - timedelta(days=2),
                ),
                AuditEvent(
                    id=100,
                    actor_type="session",
                    actor_id=target.id,
                    action="historical.event",
                    outcome="succeeded",
                    correlation_id="historical-correlation",
                    details_json={"kept": True},
                ),
            )
        )
    return target, other


def snapshot(factory, *, expected: int):  # type: ignore[no-untyped-def]
    with factory() as session:
        return inspect_administrator_password_reset(
            session,
            username="owner",
            expected_active_sessions=expected,
        )


def apply_reset(factory, *, expected: int, password: str = NEW_PASSWORD, hook=None):  # type: ignore[no-untyped-def]
    observed = snapshot(factory, expected=expected)
    with factory() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        try:
            result = reset_administrator_password(
                session,
                snapshot=observed,
                expected_active_sessions=expected,
                new_password=password,
                failure_hook=hook,
            )
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise


def assert_original_state(factory, *, active: int = 2) -> None:  # type: ignore[no-untyped-def]
    with factory() as session:
        owner = session.get(User, "target-admin")
        assert owner is not None and PASSWORD_HASHER.verify(owner.password_hash, OLD_PASSWORD)
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(
                    AuthSession.user_id == "target-admin",
                    AuthSession.expires_at > utc_now(),
                )
            )
            == active
        )
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1


def test_reset_updates_one_admin_revokes_only_active_sessions_and_audits(
    tmp_path: Path,
) -> None:
    engine, factory = runtime(tmp_path)
    _target, other = seed(factory, active=2)
    other_hash = other.password_hash

    result = apply_reset(factory, expected=2)

    assert result == {
        "schema_version": 1,
        "status": "succeeded",
        "user_id": "target-admin",
        "username": "owner",
        "expected_active_sessions": 2,
        "observed_active_sessions": 2,
        "revoked_active_sessions": 2,
        "remaining_active_sessions": 0,
        "preserved_expired_sessions": 1,
        "audit_event_id": result["audit_event_id"],
    }
    with factory() as session:
        owner = session.get(User, "target-admin")
        preserved_other = session.get(User, "other-admin")
        assert owner is not None and PASSWORD_HASHER.verify(owner.password_hash, NEW_PASSWORD)
        with pytest.raises(VerifyMismatchError):
            PASSWORD_HASHER.verify(owner.password_hash, OLD_PASSWORD)
        assert preserved_other is not None and preserved_other.password_hash == other_hash
        assert session.get(AuthSession, "target-expired") is not None
        assert session.get(AuthSession, "other-active") is not None
        token = session.get(ApiToken, "target-api-token")
        assert token is not None and token.token_hash == "a" * 64
        assert session.get(SetupClaim, "initial-owner") is not None
        assert session.get(AuditEvent, 100).details_json == {"kept": True}  # type: ignore[union-attr]
        event = session.get(AuditEvent, result["audit_event_id"])
        assert event is not None
        assert event.action == "auth.password.reset"
        assert event.actor_type == "local_console"
        assert event.target_id == "target-admin"
        assert event.details_json == {
            "expected_active_sessions": 2,
            "observed_active_sessions": 2,
            "revoked_active_sessions": 2,
            "preserved_expired_sessions": 1,
        }
        serialized = json.dumps(event.details_json)
        assert all(
            secret not in serialized
            for secret in (OLD_PASSWORD, NEW_PASSWORD, "token_hash", "csrf", "password_hash")
        )
    engine.dispose()


def test_zero_active_session_reset_succeeds_and_preserves_expired_history(
    tmp_path: Path,
) -> None:
    engine, factory = runtime(tmp_path)
    seed(factory, active=0)
    result = apply_reset(factory, expected=0)
    assert result["revoked_active_sessions"] == 0
    assert result["preserved_expired_sessions"] == 1
    with factory() as session:
        assert session.get(AuthSession, "target-expired") is not None
    engine.dispose()


def test_preflight_rejects_count_mismatch_missing_disabled_and_wrong_role(
    tmp_path: Path,
) -> None:
    engine, factory = runtime(tmp_path)
    seed(factory)
    with factory() as session:
        with pytest.raises(PasswordResetError) as mismatch:
            inspect_administrator_password_reset(
                session, username="owner", expected_active_sessions=1
            )
        assert mismatch.value.code == "active_session_count_mismatch"
        assert mismatch.value.observed_active_sessions == 2
        with pytest.raises(PasswordResetError) as missing:
            inspect_administrator_password_reset(
                session, username="absent", expected_active_sessions=0
            )
        assert missing.value.code == "user_not_found"

    with factory() as session, session.begin():
        owner = session.get(User, "target-admin")
        assert owner is not None
        owner.is_active = False
    with factory() as session:
        with pytest.raises(PasswordResetError) as disabled:
            inspect_administrator_password_reset(
                session, username="owner", expected_active_sessions=2
            )
        assert disabled.value.code == "user_disabled"

    with factory() as session, session.begin():
        owner = session.get(User, "target-admin")
        assert owner is not None
        owner.is_active = True
        owner.is_admin = False
    with factory() as session:
        with pytest.raises(PasswordResetError) as role:
            inspect_administrator_password_reset(
                session, username="owner", expected_active_sessions=2
            )
        assert role.value.code == "user_role_unexpected"
    engine.dispose()


def test_preflight_refuses_ambiguous_exact_username(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, factory = runtime(tmp_path)
    seed(factory)
    with factory() as session:
        existing = session.get(User, "target-admin")
        assert existing is not None
        duplicate = User(
            id="duplicate",
            username="owner",
            password_hash=existing.password_hash,
            is_admin=True,
            is_active=True,
        )
        monkeypatch.setattr(
            auth_service,
            "_password_reset_users",
            lambda _session, _username: [existing, duplicate],
        )
        with pytest.raises(PasswordResetError) as ambiguous:
            inspect_administrator_password_reset(
                session, username="owner", expected_active_sessions=2
            )
        assert ambiguous.value.code == "user_ambiguous"
    engine.dispose()


def test_empty_and_unchanged_passwords_fail_without_success_audit(tmp_path: Path) -> None:
    engine, factory = runtime(tmp_path)
    seed(factory)
    with pytest.raises(PasswordResetError) as invalid:
        apply_reset(factory, expected=2, password="")
    assert invalid.value.code == "password_invalid"
    with pytest.raises(PasswordResetError) as unchanged:
        apply_reset(factory, expected=2, password=OLD_PASSWORD)
    assert unchanged.value.code == "password_unchanged"
    assert_original_state(factory)
    engine.dispose()


def test_identity_and_in_transaction_count_drift_roll_back(tmp_path: Path) -> None:
    engine, factory = runtime(tmp_path)
    seed(factory)
    observed = snapshot(factory, expected=2)
    with factory() as session, session.begin():
        owner = session.get(User, "target-admin")
        assert owner is not None
        owner.username = "renamed"
    with factory() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        with pytest.raises(PasswordResetError) as identity:
            reset_administrator_password(
                session,
                snapshot=observed,
                expected_active_sessions=2,
                new_password=NEW_PASSWORD,
            )
        session.rollback()
    assert identity.value.code == "user_identity_drift"
    with factory() as session, session.begin():
        owner = session.get(User, "target-admin")
        assert owner is not None
        owner.username = "owner"

    def add_active_session(phase: str, session) -> None:  # type: ignore[no-untyped-def]
        if phase == "after_count":
            session.add(
                AuthSession(
                    id="drifted-session",
                    user_id="target-admin",
                    token_hash="9" * 64,
                    csrf_hash="8" * 64,
                    expires_at=utc_now() + timedelta(hours=1),
                )
            )
            session.flush()

    with pytest.raises(PasswordResetError) as drift:
        apply_reset(factory, expected=2, hook=add_active_session)
    assert drift.value.code == "active_session_count_drift"
    assert_original_state(factory)
    with factory() as session:
        assert session.get(AuthSession, "drifted-session") is None
    engine.dispose()


@pytest.mark.parametrize(
    ("failure_kind", "expected_code"),
    [
        ("password_readback", "password_readback_failed"),
        ("session_delete", "session_delete_mismatch"),
        ("audit", None),
    ],
)
def test_password_session_and_audit_failures_roll_back_together(
    failure_kind: str,
    expected_code: str | None,
    tmp_path: Path,
) -> None:
    engine, factory = runtime(tmp_path)
    seed(factory)

    def inject(phase: str, session) -> None:  # type: ignore[no-untyped-def]
        if failure_kind == "password_readback" and phase == "after_audit":
            owner = session.get(User, "target-admin")
            assert owner is not None
            owner.password_hash = "invalid-readback-verifier"
            session.flush()
        elif failure_kind == "session_delete" and phase == "after_password_update":
            target_session = session.get(AuthSession, "target-active-0")
            assert target_session is not None
            session.delete(target_session)
            session.flush()
        elif failure_kind == "audit" and phase == "after_audit":
            raise RuntimeError("injected audit persistence failure")

    if expected_code is None:
        with pytest.raises(RuntimeError, match="injected audit"):
            apply_reset(factory, expected=2, hook=inject)
    else:
        with pytest.raises(PasswordResetError) as caught:
            apply_reset(factory, expected=2, hook=inject)
        assert caught.value.code == expected_code
    assert_original_state(factory)
    engine.dispose()


def test_password_hash_generation_failure_rolls_back_without_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, factory = runtime(tmp_path)
    seed(factory)

    def fail_hash(_password: str) -> str:
        raise RuntimeError("injected password hashing failure")

    class FailingHasher:
        verify = auth_service.PASSWORD_HASHER.verify
        check_needs_rehash = auth_service.PASSWORD_HASHER.check_needs_rehash
        hash = staticmethod(fail_hash)

    monkeypatch.setattr(auth_service, "PASSWORD_HASHER", FailingHasher())
    with pytest.raises(RuntimeError, match="injected password hashing"):
        apply_reset(factory, expected=2)
    assert_original_state(factory)
    engine.dispose()


def test_disabled_user_cannot_authenticate_by_password_session_or_api_token(
    tmp_path: Path,
) -> None:
    engine, factory = runtime(tmp_path)
    seed(factory)
    with factory() as session, session.begin():
        owner = session.get(User, "target-admin")
        assert owner is not None
        owner.is_active = False
    with factory() as session:
        with pytest.raises(AuthenticationError):
            authenticate_password(session, "owner", OLD_PASSWORD)
        assert principal_from_session(session, "unused") is None
        session_record = session.get(AuthSession, "target-active-0")
        assert session_record is not None
        assert principal_from_session(session, "raw-token-does-not-match") is None
        session_record.token_hash = hash_token("known-session")
        token = session.get(ApiToken, "target-api-token")
        assert token is not None
        token.token_hash = hash_token("known-api-token")
        session.flush()
        assert principal_from_session(session, "known-session") is None
        assert principal_from_api_token(session, "known-api-token") is None
    engine.dispose()


def _set_reset_argv(monkeypatch: pytest.MonkeyPatch, *, expected: str = "2") -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hoardarr",
            "auth",
            "reset-password",
            "--username",
            "owner",
            "--expected-active-sessions",
            expected,
            "--password-stdin",
            "--json",
        ],
    )


@pytest.mark.parametrize("gate", ["root", "api", "sqlite", "missing", "migration"])
def test_cli_gates_fail_before_password_consumption(
    gate: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = settings_for(tmp_path)
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    seed(factory)
    engine.dispose()
    password_reads = 0

    def forbidden_password_read(_password_stdin: bool) -> str:
        nonlocal password_reads
        password_reads += 1
        raise AssertionError("password must not be consumed before preflight passes")

    _set_reset_argv(monkeypatch)
    monkeypatch.setattr(cli, "_read_password", forbidden_password_read)
    monkeypatch.setattr(cli, "_is_root", lambda: gate != "root")
    monkeypatch.setattr(cli, "_active_units", lambda _units: ["active"] if gate == "api" else [])

    def forbidden_settings() -> Settings:
        raise AssertionError("root and API gates must reject before database configuration")

    if gate in {"root", "api"}:
        monkeypatch.setattr(cli, "Settings", forbidden_settings)
    elif gate == "sqlite":
        unsupported = settings.model_copy(update={"database_url": "postgresql://invalid/hoardarr"})
        monkeypatch.setattr(cli, "Settings", lambda: unsupported)
    elif gate == "missing":
        missing = settings.model_copy(
            update={"database_url": f"sqlite:///{(tmp_path / 'missing.db').as_posix()}"}
        )
        monkeypatch.setattr(cli, "Settings", lambda: missing)
    else:
        monkeypatch.setattr(cli, "Settings", lambda: settings)
    if gate == "migration":
        monkeypatch.setattr(cli, "database_is_current", lambda _engine, _url: False)

    with pytest.raises(SystemExit):
        cli.main()
    output = capsys.readouterr().out
    document = json.loads(output)
    assert document["status"] == "rejected"
    assert password_reads == 0
    assert all(
        forbidden not in output
        for forbidden in (
            NEW_PASSWORD,
            OLD_PASSWORD,
            "password_hash",
            "token_hash",
            "csrf",
            str(tmp_path),
            "SELECT ",
            "OperationalError",
        )
    )


@pytest.mark.parametrize(
    ("state", "expected_code"),
    [
        ("missing", "user_not_found"),
        ("disabled", "user_disabled"),
        ("role", "user_role_unexpected"),
        ("count", "active_session_count_mismatch"),
        ("hash", "password_verifier_unsupported"),
        ("username", "username_invalid"),
    ],
)
def test_cli_target_preflight_rejections_are_sanitized_and_do_not_read_password(
    state: str,
    expected_code: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = settings_for(tmp_path)
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    if state != "missing":
        seed(factory)
        with factory() as session, session.begin():
            owner = session.get(User, "target-admin")
            assert owner is not None
            if state == "disabled":
                owner.is_active = False
            elif state == "role":
                owner.is_admin = False
            elif state == "hash":
                owner.password_hash = "unsupported-verifier"
    engine.dispose()
    password_reads = 0

    def forbidden_password_read(_password_stdin: bool) -> str:
        nonlocal password_reads
        password_reads += 1
        raise AssertionError("target preflight must reject before reading a password")

    expected = "1" if state == "count" else "2"
    _set_reset_argv(monkeypatch, expected=expected)
    if state == "username":
        sys.argv[4] = "NOT A NORMALIZED USER"
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_is_root", lambda: True)
    monkeypatch.setattr(cli, "_active_units", lambda _units: [])
    monkeypatch.setattr(cli, "_read_password", forbidden_password_read)

    with pytest.raises(SystemExit):
        cli.main()
    output = capsys.readouterr().out
    document = json.loads(output)
    assert document["error"]["code"] == expected_code
    assert password_reads == 0
    assert all(
        forbidden not in output
        for forbidden in (
            OLD_PASSWORD,
            NEW_PASSWORD,
            "password_hash",
            "token_hash",
            "csrf",
            str(tmp_path),
            "SELECT ",
            "OperationalError",
        )
    )


def test_cli_rechecks_api_state_after_password_read_before_database_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = settings_for(tmp_path)
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    seed(factory)
    engine.dispose()
    service_checks = 0
    password_reads = 0

    def drifting_api_state(units: tuple[str, ...]) -> list[str]:
        nonlocal service_checks
        assert units == ("hoardarr-api.service",)
        service_checks += 1
        return [] if service_checks == 1 else ["hoardarr-api.service"]

    def read_password(_password_stdin: bool) -> str:
        nonlocal password_reads
        password_reads += 1
        return NEW_PASSWORD

    _set_reset_argv(monkeypatch)
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_is_root", lambda: True)
    monkeypatch.setattr(cli, "_active_units", drifting_api_state)
    monkeypatch.setattr(cli, "_read_password", read_password)

    with pytest.raises(SystemExit) as rejected:
        cli.main()
    assert rejected.value.code == 3
    output = capsys.readouterr().out
    document = json.loads(output)
    assert document["error"]["code"] == "api_service_active"
    assert document["revoked_active_sessions"] == 0
    assert service_checks == 2 and password_reads == 1
    assert all(
        forbidden not in output
        for forbidden in (
            NEW_PASSWORD,
            OLD_PASSWORD,
            "password_hash",
            "token_hash",
            "csrf",
            str(tmp_path),
            "SELECT",
            "OperationalError",
        )
    )
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    assert_original_state(factory)
    engine.dispose()


def test_cli_success_and_generic_failure_json_are_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = settings_for(tmp_path)
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    seed(factory)
    engine.dispose()
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_is_root", lambda: True)
    monkeypatch.setattr(cli, "_active_units", lambda _units: [])
    _set_reset_argv(monkeypatch)
    monkeypatch.setattr(sys, "stdin", io.StringIO(NEW_PASSWORD + "\n"))
    cli.main()
    output = capsys.readouterr().out
    document = json.loads(output)
    assert document["status"] == "succeeded"
    assert document["revoked_active_sessions"] == 2
    assert all(
        forbidden not in output
        for forbidden in (NEW_PASSWORD, OLD_PASSWORD, "password_hash", "token_hash", "csrf")
    )

    second = settings_for(tmp_path / "generic")
    engine = create_database_engine(second.database_url)
    factory = create_session_factory(engine)
    seed(factory)
    engine.dispose()
    monkeypatch.setattr(cli, "Settings", lambda: second)
    _set_reset_argv(monkeypatch)
    monkeypatch.setattr(sys, "stdin", io.StringIO(NEW_PASSWORD + "\n"))

    def unsafe_failure(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError(
            f"{NEW_PASSWORD} token_hash csrf SELECT password_hash {tmp_path}"
        )

    monkeypatch.setattr(cli, "reset_administrator_password", unsafe_failure)
    with pytest.raises(SystemExit) as failed:
        cli.main()
    assert failed.value.code == 5
    rejected_output = capsys.readouterr().out
    rejected = json.loads(rejected_output)
    assert rejected["error"]["code"] == "password_reset_failed"
    assert all(
        forbidden not in rejected_output
        for forbidden in (
            NEW_PASSWORD,
            "token_hash",
            "csrf",
            "SELECT",
            "password_hash",
            str(tmp_path),
            "RuntimeError",
        )
    )


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_cli_commit_uncertainty_is_not_retried_or_reclassified_when_rollback_fails(
    rollback_fails: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = settings_for(tmp_path)
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    seed(factory)
    engine.dispose()
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_is_root", lambda: True)
    monkeypatch.setattr(cli, "_active_units", lambda _units: [])
    _set_reset_argv(monkeypatch)
    monkeypatch.setattr(sys, "stdin", io.StringIO(NEW_PASSWORD + "\n"))

    real_create_factory = cli.create_session_factory
    factory_calls = 0
    rollback_calls = 0

    class CommitFailureSession:
        def __init__(self, session) -> None:  # type: ignore[no-untyped-def]
            self.session = session

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *_args) -> None:  # type: ignore[no-untyped-def]
            self.session.close()

        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            return getattr(self.session, name)

        def commit(self) -> None:
            raise RuntimeError(f"uncertain {NEW_PASSWORD} {tmp_path} SELECT token_hash")

        def rollback(self) -> None:
            nonlocal rollback_calls
            rollback_calls += 1
            if rollback_fails:
                raise RuntimeError(
                    f"rollback failed {NEW_PASSWORD} {tmp_path} SELECT csrf"
                )
            self.session.rollback()

    def create_commit_failing_factory(engine):  # type: ignore[no-untyped-def]
        base = real_create_factory(engine)

        def make_session():  # type: ignore[no-untyped-def]
            nonlocal factory_calls
            factory_calls += 1
            session = base()
            return session if factory_calls == 1 else CommitFailureSession(session)

        return make_session

    reset_calls = 0
    real_reset = cli.reset_administrator_password

    def count_reset(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal reset_calls
        reset_calls += 1
        return real_reset(*args, **kwargs)

    monkeypatch.setattr(cli, "create_session_factory", create_commit_failing_factory)
    monkeypatch.setattr(cli, "reset_administrator_password", count_reset)
    with pytest.raises(SystemExit) as uncertain:
        cli.main()
    assert uncertain.value.code == 6
    output = capsys.readouterr().out
    document = json.loads(output)
    assert document["error"]["code"] == "password_reset_commit_uncertain"
    assert reset_calls == 1 and factory_calls == 2
    assert rollback_calls == 1
    assert all(
        forbidden not in output
        for forbidden in (
            NEW_PASSWORD,
            str(tmp_path),
            "SELECT",
            "token_hash",
            "csrf",
            "RuntimeError",
        )
    )
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    assert_original_state(factory)
    engine.dispose()


def test_active_state_migration_preserves_existing_users_as_enabled(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    url = f"sqlite:///{database.as_posix()}"
    upgrade_database(url, "0028_physical_disk_identity_aliases")
    engine = create_database_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, username, password_hash, is_admin, created_at) "
                "VALUES ('owner', 'owner', 'verifier', 1, CURRENT_TIMESTAMP)"
            )
        )
    engine.dispose()
    upgrade_database(url)
    engine = create_database_engine(url)
    factory = create_session_factory(engine)
    with factory() as session:
        owner = session.get(User, "owner")
        assert owner is not None and owner.is_active is True
    engine.dispose()
