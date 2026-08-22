from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from hoardarr.db.models import Base, Operation, Plan, WizardSession
from hoardarr.storage.reservations import active_storage_reservations, reserved_device_ids


def test_only_queued_and_running_storage_plans_reserve_drives() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        wizard = WizardSession(
            workflow="storage_setup",
            mode="guided",
            status="ready",
            current_step="review",
            revision=1,
        )
        session.add(wizard)
        session.flush()
        plan = Plan(
            wizard_session_id=wizard.id,
            revision=1,
            kind="storage_setup",
            document_json={
                "storage": {
                    "snapshot_binding": {
                        "selected_device_ids": ["serial:drive-one", "serial:drive-two"]
                    }
                }
            },
            sha256="a" * 64,
        )
        session.add(plan)
        session.flush()
        session.add_all(
            [
                Operation(
                    kind="storage.apply",
                    status="running",
                    actor_type="user",
                    actor_id="user",
                    request_sha256="b" * 64,
                    request_json={"plan_id": plan.id},
                ),
                Operation(
                    kind="storage.apply",
                    status="succeeded",
                    actor_type="user",
                    actor_id="user",
                    request_sha256="c" * 64,
                    request_json={"plan_id": plan.id},
                ),
                Operation(
                    kind="hardware.scan",
                    status="running",
                    actor_type="user",
                    actor_id="user",
                    request_sha256="d" * 64,
                    request_json={"plan_id": plan.id},
                ),
            ]
        )
        session.commit()

        reservations = active_storage_reservations(session)

        assert len(reservations) == 1
        assert reservations[0]["status"] == "running"
        assert reservations[0]["selected_device_ids"] == [
            "serial:drive-one",
            "serial:drive-two",
        ]
        assert reserved_device_ids(session) == {"serial:drive-one", "serial:drive-two"}
