from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from hoardarr.auth.service import Principal
from hoardarr.db.models import (
    Base,
    Operation,
    StorageBackend,
    StorageDrainEntry,
    StorageDrainJob,
    utc_now,
)
from hoardarr.operations.service import document_hash, recover_stale_operations
from hoardarr.storage.drain_worker import (
    DrainPaused,
    execute_drain,
    mark_drain_paused,
    resume_drain,
)
from hoardarr.storage.groups import (
    assign_backend,
    begin_drain_placement,
    create_group,
    register_disk,
    transition_backend,
)


def _principal() -> Principal:
    return Principal(
        user_id="11111111-1111-1111-1111-111111111111",
        username="owner",
        is_admin=True,
        auth_type="session",
        scopes=frozenset({"operate"}),
    )


def _runtime(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'drain.db').as_posix()}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _seed_job(session_factory, tmp_path: Path) -> tuple[str, dict[str, object], Path, Path]:
    source_path = tmp_path / "source"
    destination_path = tmp_path / "destination"
    source_path.mkdir()
    destination_path.mkdir()
    (source_path / "Movies").mkdir()
    (source_path / "Movies" / "example.mkv").write_bytes(b"media-payload" * 4096)
    (source_path / "metadata.xml").write_text("<movie>safe</movie>", encoding="utf-8")
    total_bytes = sum(path.stat().st_size for path in source_path.rglob("*") if path.is_file())
    source_device = source_path.stat().st_dev
    destination_device = source_device + 1
    actor = _principal()
    with session_factory() as session, session.begin():
        group = create_group(
            session,
            name="Media",
            namespace_path="/srv/hoardarr/media",
            purpose="media",
            principal=actor,
        )
        backend_ids: list[str] = []
        for suffix in ("source", "destination"):
            disk, _created = register_disk(
                session,
                {
                    "stable_identity": f"wwn:{suffix}",
                    "health_state": "healthy",
                    "capacity_bytes": 10_000_000,
                },
            )
            backend = assign_backend(
                session,
                group_id=group.id,
                physical_disk_id=disk.id,
                storage_entity_id=None,
                namespace_path=f"/srv/hoardarr/test/{suffix}",
                role="data",
                principal=actor,
            )
            transition_backend(
                session,
                group_id=group.id,
                backend_id=backend.id,
                target_state="active",
                principal=actor,
                reason="disposable test mount verified",
            )
            backend_ids.append(backend.id)
        transition_backend(
            session,
            group_id=group.id,
            backend_id=backend_ids[0],
            target_state="preferred_write",
            principal=actor,
            reason="test placement",
        )
        document: dict[str, object] = {
            "schema_version": 1,
            "kind": "storage.drain",
            "storage_group_id": group.id,
            "storage_group_namespace": group.namespace_path,
            "source": {
                "backend_id": backend_ids[0],
                "stable_identity": "disk:wwn:source",
                "path": str(source_path),
                "filesystem_device": source_device,
                "required_bytes": total_bytes,
                "health": "healthy",
                "lifecycle_state": "preferred_write",
            },
            "destinations": [
                {
                    "backend_id": backend_ids[1],
                    "stable_identity": "disk:wwn:destination",
                    "path": str(destination_path),
                    "filesystem_device": destination_device,
                    "free_bytes": 100_000_000,
                    "total_bytes": 200_000_000,
                    "health": "healthy",
                }
            ],
            "verification": {
                "mode": "accurate",
                "full_hashes": True,
                "additional_read_pass": False,
            },
            "capacity": {
                "required_bytes": total_bytes,
                "destination_free_bytes": 100_000_000,
                "reserve_bytes": 0,
            },
            "open_use": {"quality": "available", "open_handles": 0, "processes": []},
            "arr_activity": {"quality": "available", "active_writes": 0},
            "blockers": [],
            "warnings": [],
            "ready": True,
            "phases": ["preflight", "remove_from_write_placement", "copy", "verify", "finalize"],
        }
        document["plan_sha256"] = document_hash(document)
        operation = Operation(
            kind="storage.drain",
            actor_type="session",
            actor_id=actor.user_id,
            resource_type="storage_group",
            resource_id=group.id,
            idempotency_key="drain-test",
            request_sha256=document_hash(document),
            request_json={"plan": document},
            status="running",
            heartbeat_at=utc_now(),
        )
        session.add(operation)
        session.flush()
        session.add(
            StorageDrainJob(
                id=operation.id,
                storage_group_id=group.id,
                source_backend_id=backend_ids[0],
                plan_sha256=str(document["plan_sha256"]),
                verification_mode="accurate",
                status="running",
                phase="preflight",
                report_json={},
            )
        )
        begin_drain_placement(
            session,
            group_id=group.id,
            source_backend_id=backend_ids[0],
            destination_backend_ids=[backend_ids[1]],
            operation_id=operation.id,
            plan_sha256=str(document["plan_sha256"]),
            principal=actor,
        )
        operation_id = operation.id
    return operation_id, document, source_path, destination_path


@pytest.mark.skipif(os.name != "posix", reason="descriptor-relative mover requires Linux")
def test_drain_moves_verifies_retires_and_preserves_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_factory = _runtime(tmp_path)
    operation_id, plan, source, destination = _seed_job(session_factory, tmp_path)
    original_stat = Path.stat

    def filesystem_stat(path: Path, *args, **kwargs):
        facts = original_stat(path, *args, **kwargs)
        if path == destination:
            return SimpleNamespace(st_dev=int(plan["destinations"][0]["filesystem_device"]))
        return facts

    monkeypatch.setattr(Path, "stat", filesystem_stat)
    paused_once = False

    def pause_after_inventory(phase: str) -> None:
        nonlocal paused_once
        if phase == "copying" and not paused_once:
            paused_once = True
            with session_factory() as session, session.begin():
                job = session.get(StorageDrainJob, operation_id)
                assert job is not None
                job.pause_requested = True

    with pytest.raises(DrainPaused):
        execute_drain(session_factory, operation_id, plan, phase_hook=pause_after_inventory)
    with session_factory() as session, session.begin():
        operation = session.get(Operation, operation_id)
        assert operation is not None
        mark_drain_paused(session, operation)
        assert operation.status == "paused"
        resume_drain(session, operation)
        assert operation.status == "queued"
        operation.status = "running"

    report = execute_drain(session_factory, operation_id, plan)
    assert report["namespace_path"] == "/srv/hoardarr/media"
    assert report["namespace_preserved"] is True
    assert not any(path.is_file() for path in source.rglob("*"))
    assert (destination / "metadata.xml").read_text(encoding="utf-8") == "<movie>safe</movie>"
    assert hashlib.sha256((destination / "Movies" / "example.mkv").read_bytes()).hexdigest()
    with session_factory() as session:
        job = session.get(StorageDrainJob, operation_id)
        source_backend = session.get(StorageBackend, job.source_backend_id if job else "")
        entries = list(
            session.scalars(
                select(StorageDrainEntry).where(StorageDrainEntry.job_id == operation_id)
            )
        )
        assert job is not None and job.status == "succeeded"
        assert source_backend is not None and source_backend.lifecycle_state == "retired"
        assert entries and {entry.status for entry in entries} == {"removed"}


def test_interrupted_drain_is_requeued_from_durable_checkpoint(tmp_path: Path) -> None:
    session_factory = _runtime(tmp_path)
    operation_id, _plan, _source, _destination = _seed_job(session_factory, tmp_path)
    with session_factory() as session, session.begin():
        operation = session.get(Operation, operation_id)
        job = session.get(StorageDrainJob, operation_id)
        assert operation is not None and job is not None
        operation.heartbeat_at = utc_now() - timedelta(minutes=10)
        operation.lease_owner = "stopped-worker"
        job.phase = "copying"
    with session_factory() as session, session.begin():
        assert recover_stale_operations(session, max_age_seconds=30) == 1
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        job = session.get(StorageDrainJob, operation_id)
        assert operation is not None and operation.status == "queued"
        assert operation.lease_owner is None
        assert job is not None and job.status == "queued" and job.phase == "copying"
