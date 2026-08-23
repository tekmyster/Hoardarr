from __future__ import annotations

from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from hoardarr.auth.service import Principal
from hoardarr.db.models import Base, utc_now
from hoardarr.operations.service import claim_next_operation, create_operation


def test_worker_skips_future_operation_and_claims_ready_work() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    principal = Principal(
        user_id="11111111-1111-4111-8111-111111111111",
        username="owner",
        is_admin=True,
        auth_type="session",
        scopes=frozenset({"operate"}),
    )
    with Session(engine) as session, session.begin():
        future, _ = create_operation(
            session,
            kind="storage.drain",
            principal=principal,
            request={"sequence": "future"},
            idempotency_key="future",
            not_before=utc_now() + timedelta(hours=1),
        )
        ready, _ = create_operation(
            session,
            kind="hardware.scan",
            principal=principal,
            request={"sequence": "ready"},
            idempotency_key="ready",
        )
        future_id = future.id
        ready_id = ready.id
    with Session(engine) as session, session.begin():
        claimed = claim_next_operation(session, "test-worker")
        assert claimed is not None and claimed.id == ready_id
    with Session(engine) as session, session.begin():
        assert claim_next_operation(session, "test-worker") is None
        future = session.get(type(claimed), future_id)
        assert future is not None and future.status == "queued"
