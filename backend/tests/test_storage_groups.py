from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from hoardarr.auth.service import Principal
from hoardarr.db.models import Base, PhysicalDisk, StorageBackend, StorageLifecycleEvent
from hoardarr.storage import groups as group_service
from hoardarr.storage.groups import (
    StorageGroupError,
    activate_backend,
    advance_drain_lifecycle,
    assign_backend,
    begin_drain_placement,
    build_backend_activation_plan,
    create_group,
    disk_documents,
    group_documents,
    normalize_namespace,
    register_disk,
    release_retired_backend,
    set_disk_reservation,
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


def test_disk_reservation_is_persistent_idempotent_and_blocks_assignment() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    actor = principal()
    with Session(engine) as session:
        disk, _ = register_disk(session, {"stable_identity": "wwn:future-disk"})
        group = create_group(
            session,
            name="Future media",
            namespace_path="/srv/hoardarr/future",
            purpose="media",
            principal=actor,
        )
        reserved = set_disk_reservation(session, disk_id=disk.id, action="reserve")
        assert reserved.lifecycle_state == "reserved"
        assert set_disk_reservation(session, disk_id=disk.id, action="reserve") is reserved
        session.commit()

        try:
            assign_backend(
                session,
                group_id=group.id,
                physical_disk_id=disk.id,
                storage_entity_id=None,
                namespace_path="/srv/hoardarr/backends/future",
                role="data",
                principal=actor,
            )
        except StorageGroupError as exc:
            assert exc.code == "disk_not_assignable"
        else:
            raise AssertionError("reserved disk was assigned")

        released = set_disk_reservation(session, disk_id=disk.id, action="release")
        assert released.lifecycle_state == "discovered"


def test_system_disk_cannot_be_reserved() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        disk, _ = register_disk(session, {"stable_identity": "wwn:system-disk"})
        try:
            set_disk_reservation(
                session,
                disk_id=disk.id,
                action="reserve",
                protected_identities={disk.stable_identity},
            )
        except StorageGroupError as exc:
            assert exc.code == "system_disk_protected"
        else:
            raise AssertionError("system disk was reserved")


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


def test_activation_binds_exact_mount_identity_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    actor = principal()
    evidence = {
        "path": "/srv/hoardarr/backends/a",
        "filesystem_device": 101,
        "mount_source": "/dev/sdb1",
        "exact_mount": True,
        "identity_match": True,
        "identity_basis": "test-created mount source matches registered disk",
        "total_bytes": 8_000_000_000_000,
        "free_bytes": 7_000_000_000_000,
    }
    monkeypatch.setattr(
        group_service,
        "inspect_backend_activation",
        lambda *_args, **_kwargs: dict(evidence),
    )
    with Session(engine) as session:
        group = create_group(
            session,
            name="Media",
            namespace_path="/srv/hoardarr/media",
            purpose="media",
            principal=actor,
        )
        disk, _ = register_disk(
            session,
            {
                "stable_identity": "wwn:disk-a",
                "kernel_path": "/dev/sdb",
                "health_state": "healthy",
            },
        )
        backend = assign_backend(
            session,
            group_id=group.id,
            physical_disk_id=disk.id,
            storage_entity_id=None,
            namespace_path=evidence["path"],
            role="data",
            principal=actor,
        )
        plan = build_backend_activation_plan(session, group_id=group.id, backend_id=backend.id)
        assert plan["ready"] is True
        assert plan["evidence"]["identity_match"] is True
        evidence["free_bytes"] -= 4096

        activated = activate_backend(
            session,
            group_id=group.id,
            backend_id=backend.id,
            plan_sha256=plan["plan_sha256"],
            principal=actor,
            reason="verified test mount",
        )
        assert activated.lifecycle_state == "active"
        assert activated.config_json["activation"]["plan_sha256"] == plan["plan_sha256"]
        assert activated.config_json["activation"]["evidence"]["mount_source"] == "/dev/sdb1"


def test_activation_fails_closed_when_mounted_identity_does_not_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    actor = principal()
    monkeypatch.setattr(
        group_service,
        "inspect_backend_activation",
        lambda *_args, **_kwargs: {
            "path": "/srv/hoardarr/backends/a",
            "filesystem_device": 101,
            "mount_source": "/dev/sdz1",
            "exact_mount": True,
            "identity_match": False,
            "identity_basis": "mounted source does not match registered disk",
            "total_bytes": 8_000_000_000_000,
            "free_bytes": 7_000_000_000_000,
        },
    )
    with Session(engine) as session:
        group = create_group(
            session,
            name="Media",
            namespace_path="/srv/hoardarr/media",
            purpose="media",
            principal=actor,
        )
        disk, _ = register_disk(
            session,
            {"stable_identity": "wwn:disk-a", "kernel_path": "/dev/sdb"},
        )
        backend = assign_backend(
            session,
            group_id=group.id,
            physical_disk_id=disk.id,
            storage_entity_id=None,
            namespace_path="/srv/hoardarr/backends/a",
            role="data",
            principal=actor,
        )
        plan = build_backend_activation_plan(session, group_id=group.id, backend_id=backend.id)
        assert plan["ready"] is False
        with pytest.raises(StorageGroupError, match="failed its activation safety review"):
            activate_backend(
                session,
                group_id=group.id,
                backend_id=backend.id,
                plan_sha256=plan["plan_sha256"],
                principal=actor,
                reason=None,
            )
        assert backend.lifecycle_state == "assigned"


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


def test_drain_atomically_removes_source_from_new_write_placement() -> None:
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
        backends: list[StorageBackend] = []
        for suffix in ("a", "b", "c"):
            disk, _ = register_disk(session, {"stable_identity": f"wwn:disk-{suffix}"})
            backend = assign_backend(
                session,
                group_id=group.id,
                physical_disk_id=disk.id,
                storage_entity_id=None,
                namespace_path=f"/srv/hoardarr/backends/{suffix}",
                role="data",
                principal=actor,
            )
            transition_backend(
                session,
                group_id=group.id,
                backend_id=backend.id,
                target_state="active",
                principal=actor,
                reason="mounted",
            )
            backends.append(backend)
        transition_backend(
            session,
            group_id=group.id,
            backend_id=backends[0].id,
            target_state="preferred_write",
            principal=actor,
            reason="initial placement",
        )
        digest = "a" * 64
        source = begin_drain_placement(
            session,
            group_id=group.id,
            source_backend_id=backends[0].id,
            destination_backend_ids=[backends[1].id, backends[2].id],
            operation_id="drain-operation-1",
            plan_sha256=digest,
            principal=actor,
        )

        assert source.lifecycle_state == "draining"
        assert source.config_json["drain"]["new_write_placement_removed"] is True
        assert backends[1].lifecycle_state == "preferred_write"
        assert backends[2].lifecycle_state == "active"
        states = list(
            session.scalars(
                select(StorageBackend.lifecycle_state).where(
                    StorageBackend.storage_group_id == group.id
                )
            )
        )
        assert states.count("preferred_write") == 1
        assert session.get(PhysicalDisk, source.physical_disk_id).lifecycle_state == "draining"
        events_before_replay = len(list(session.scalars(select(StorageLifecycleEvent))))

        replay = begin_drain_placement(
            session,
            group_id=group.id,
            source_backend_id=backends[0].id,
            destination_backend_ids=[backends[1].id, backends[2].id],
            operation_id="drain-operation-1",
            plan_sha256=digest,
            principal=actor,
        )
        assert replay.id == source.id
        assert len(list(session.scalars(select(StorageLifecycleEvent)))) == events_before_replay

        try:
            begin_drain_placement(
                session,
                group_id=group.id,
                source_backend_id=backends[0].id,
                destination_backend_ids=[backends[1].id],
                operation_id="another-operation",
                plan_sha256="b" * 64,
                principal=actor,
            )
        except StorageGroupError as exc:
            assert exc.code == "drain_in_progress"
        else:
            raise AssertionError("a second operation adopted an active drain")


def test_drain_keeps_an_existing_destination_preferred() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    actor = principal()
    with Session(engine) as session:
        group = create_group(
            session,
            name="Archive",
            namespace_path="/srv/hoardarr/archive",
            purpose="archive",
            principal=actor,
        )
        backends: list[StorageBackend] = []
        for suffix in ("source", "preferred"):
            disk, _ = register_disk(session, {"stable_identity": f"wwn:{suffix}"})
            backend = assign_backend(
                session,
                group_id=group.id,
                physical_disk_id=disk.id,
                storage_entity_id=None,
                namespace_path=f"/srv/hoardarr/backends/{suffix}",
                role="archive",
                principal=actor,
            )
            transition_backend(
                session,
                group_id=group.id,
                backend_id=backend.id,
                target_state="active",
                principal=actor,
                reason=None,
            )
            backends.append(backend)
        transition_backend(
            session,
            group_id=group.id,
            backend_id=backends[1].id,
            target_state="preferred_write",
            principal=actor,
            reason=None,
        )

        begin_drain_placement(
            session,
            group_id=group.id,
            source_backend_id=backends[0].id,
            destination_backend_ids=[backends[1].id],
            operation_id="drain-operation-2",
            plan_sha256="c" * 64,
            principal=actor,
        )
        assert backends[0].lifecycle_state == "draining"
        assert backends[1].lifecycle_state == "preferred_write"


def test_verified_retirement_can_be_released_and_reassigned_without_touching_disk() -> None:
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
        disk, _ = register_disk(session, {"stable_identity": "wwn:retired-source"})
        destination_disk, _ = register_disk(
            session, {"stable_identity": "wwn:drain-destination"}
        )
        source = assign_backend(
            session,
            group_id=group.id,
            physical_disk_id=disk.id,
            storage_entity_id=None,
            namespace_path="/srv/hoardarr/backends/source",
            role="data",
            principal=actor,
        )
        destination = assign_backend(
            session,
            group_id=group.id,
            physical_disk_id=destination_disk.id,
            storage_entity_id=None,
            namespace_path="/srv/hoardarr/backends/destination",
            role="data",
            principal=actor,
        )
        for backend in (source, destination):
            transition_backend(
                session,
                group_id=group.id,
                backend_id=backend.id,
                target_state="active",
                principal=actor,
                reason="mounted",
            )
        begin_drain_placement(
            session,
            group_id=group.id,
            source_backend_id=source.id,
            destination_backend_ids=[destination.id],
            operation_id="verified-release-operation",
            plan_sha256="d" * 64,
            principal=actor,
        )
        for state in ("verifying", "read_only", "retired"):
            advance_drain_lifecycle(
                session,
                group_id=group.id,
                source_backend_id=source.id,
                operation_id="verified-release-operation",
                target_state=state,
                principal=actor,
                details={"files_verified": 2} if state == "verifying" else None,
            )

        historical, reusable = release_retired_backend(
            session,
            group_id=group.id,
            backend_id=source.id,
            principal=actor,
            reason="operator chose reuse",
        )
        assert historical.lifecycle_state == "reuse_ready"
        assert historical.physical_disk_id is None
        assert historical.config_json["release"]["device_contents_changed"] is False
        assert reusable.id == disk.id
        assert reusable.lifecycle_state == "reuse_ready"
        assert all(item["id"] != source.id for item in group_documents(session)[0]["backends"])

        reassigned = assign_backend(
            session,
            group_id=group.id,
            physical_disk_id=disk.id,
            storage_entity_id=None,
            namespace_path="/srv/hoardarr/backends/reused",
            role="data",
            principal=actor,
        )
        assert reassigned.physical_disk_id == disk.id
        assert reassigned.stable_identity == "disk:wwn:retired-source"
        released_event = session.scalar(
            select(StorageLifecycleEvent).where(
                StorageLifecycleEvent.event_type == "backend_released_for_reuse"
            )
        )
        assert released_event is not None
        assert released_event.physical_disk_id == disk.id
        assert released_event.details_json["device_contents_changed"] is False


def test_unverified_or_active_backend_cannot_be_released_for_reuse() -> None:
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
        disk, _ = register_disk(session, {"stable_identity": "wwn:still-active"})
        backend = assign_backend(
            session,
            group_id=group.id,
            physical_disk_id=disk.id,
            storage_entity_id=None,
            namespace_path="/srv/hoardarr/backends/active",
            role="data",
            principal=actor,
        )
        try:
            release_retired_backend(
                session,
                group_id=group.id,
                backend_id=backend.id,
                principal=actor,
                reason=None,
            )
        except StorageGroupError as exc:
            assert exc.code == "backend_not_retired"
        else:
            raise AssertionError("an active backend was released")
