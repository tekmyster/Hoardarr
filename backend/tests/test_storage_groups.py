from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from hoardarr.auth.service import Principal
from hoardarr.db.models import Base, StorageLifecycleEvent
from hoardarr.storage.groups import (
    StorageGroupError,
    assign_backend,
    create_group,
    disk_documents,
    group_documents,
    normalize_namespace,
    register_disk,
    transition_backend,
)


def principal() -> Principal:
    return Principal(
        user_id="11111111-1111-1111-1111-111111111111",
        username="owner",
        is_admin=True,
        auth_type="session",
        scopes=frozenset({"operate"}),
    )


def test_namespace_is_absolute_bounded_and_cannot_traverse() -> None:
    assert normalize_namespace("/srv/hoardarr/media/") == "/srv/hoardarr/media"
    for unsafe in ("relative/media", "/", "/srv/../etc", "/srv/media\nother"):
        try:
            normalize_namespace(unsafe)
        except StorageGroupError as exc:
            assert exc.code == "invalid_namespace"
        else:
            raise AssertionError(f"unsafe namespace accepted: {unsafe!r}")


def test_disk_registry_preserves_identity_across_kernel_path_changes() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        first, created = register_disk(
            session,
            {
                "stable_identity": "wwn:5000c500feed0001",
                "kernel_path": "/dev/sdb",
                "serial": "SANITIZED-0001",
                "capacity_bytes": 8_000_000_000_000,
            },
        )
        original_id = first.id
        second, created_again = register_disk(
            session,
            {
                "stable_identity": "wwn:5000c500feed0001",
                "kernel_path": "/dev/sdz",
                "health_state": "healthy",
            },
        )
        assert created is True
        assert created_again is False
        assert second.id == original_id
        assert disk_documents(session)[0]["kernel_path"] == "/dev/sdz"
        assert disk_documents(session)[0]["health_state"] == "healthy"


def test_group_assignment_activation_and_single_preferred_writer() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    actor = principal()
    with Session(engine) as session:
        group = create_group(
            session,
            name="Media",
            namespace_path="/srv/hoardarr/media",
            purpose="media",
            principal=actor,
        )
        disk_a, _ = register_disk(session, {"stable_identity": "wwn:disk-a"})
        disk_b, _ = register_disk(session, {"stable_identity": "wwn:disk-b"})
        backend_a = assign_backend(
            session,
            group_id=group.id,
            physical_disk_id=disk_a.id,
            storage_entity_id=None,
            namespace_path="/srv/hoardarr/backends/a",
            role="data",
            principal=actor,
        )
        backend_b = assign_backend(
            session,
            group_id=group.id,
            physical_disk_id=disk_b.id,
            storage_entity_id=None,
            namespace_path="/srv/hoardarr/backends/b",
            role="data",
            principal=actor,
        )
        for backend in (backend_a, backend_b):
            transition_backend(
                session,
                group_id=group.id,
                backend_id=backend.id,
                target_state="active",
                principal=actor,
                reason="validated mount",
            )
        transition_backend(
            session,
            group_id=group.id,
            backend_id=backend_a.id,
            target_state="preferred_write",
            principal=actor,
            reason="initial placement",
        )
        transition_backend(
            session,
            group_id=group.id,
            backend_id=backend_b.id,
            target_state="preferred_write",
            principal=actor,
            reason="operator selection",
        )

        document = group_documents(session)[0]
        states = {item["id"]: item["lifecycle_state"] for item in document["backends"]}
        assert states == {backend_a.id: "active", backend_b.id: "preferred_write"}
        assert len(list(session.scalars(select(StorageLifecycleEvent)))) == 8


def test_drain_owned_states_cannot_be_set_without_durable_operation() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    actor = principal()
    with Session(engine) as session:
        group = create_group(
            session,
            name="Media",
            namespace_path="/srv/hoardarr/media",
            purpose="media",
            principal=actor,
        )
        disk, _ = register_disk(session, {"stable_identity": "wwn:disk-a"})
        backend = assign_backend(
            session,
            group_id=group.id,
            physical_disk_id=disk.id,
            storage_entity_id=None,
            namespace_path=None,
            role="data",
            principal=actor,
        )
        try:
            transition_backend(
                session,
                group_id=group.id,
                backend_id=backend.id,
                target_state="draining",
                principal=actor,
                reason=None,
            )
        except StorageGroupError as exc:
            assert exc.code == "durable_operation_required"
        else:
            raise AssertionError("draining was allowed without a durable operation")
