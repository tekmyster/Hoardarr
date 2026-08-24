from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from hoardarr.db.models import (
    Base,
    ForeignMigrationEntry,
    ForeignMigrationJob,
    Operation,
    StorageBackend,
    StorageGroup,
    utc_now,
)
from hoardarr.operations.service import document_hash, recover_stale_operations
from hoardarr.storage import foreign_migration_worker
from hoardarr.storage.drain_worker import DrainExecutionError, DrainPaused
from hoardarr.storage.foreign_migration_worker import (
    ForeignMigrationError,
    _selected,
    execute_foreign_migration,
    mark_foreign_migration_paused,
    resume_foreign_migration,
)


def test_archive_selection_keeps_folder_and_filter_semantics_distinct() -> None:
    selected_folders = {"selection": {"mode": "selected_folders", "include_paths": ["Movies"]}}
    assert _selected("Movies/Feature.mkv", selected_folders)
    assert not _selected("TV/Episode.mkv", selected_folders)

    filtered = {
        "selection": {
            "mode": "filtered",
            "include_extensions": [".mkv"],
            "include_globs": [],
            "exclude_globs": ["Movies/Samples/*"],
        }
    }
    assert _selected("Movies/Feature.MKV", filtered)
    assert not _selected("Movies/Samples/trailer.mkv", filtered)
    assert not _selected("Music/Track.flac", filtered)


def _runtime(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'migration.db').as_posix()}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _plan(destination: Path, source: Path, *, collision_policy: str = "stop") -> dict[str, object]:
    files = [path for path in source.rglob("*") if path.is_file()]
    device = {
        "id": "wwn:foreign-source",
        "stable_identity": True,
        "vendor": "TEST",
        "model": "Disposable source",
        "serial": "SOURCE-1",
        "wwn": "5000000000000001",
        "eui64": None,
        "nguid": None,
        "capacity_bytes": 1_000_000_000,
        "logical_sector_bytes": 512,
        "physical_sector_bytes": 4096,
    }
    value: dict[str, object] = {
        "schema_version": 1,
        "operation": "foreign.migrate_files",
        "candidate_id": "foreign:" + "1" * 24,
        "hardware_snapshot_id": "11111111-1111-1111-1111-111111111111",
        "hardware_snapshot_sha256": "2" * 64,
        "source_inventory_operation_id": "22222222-2222-2222-2222-222222222222",
        "source_inventory_sha256": "3" * 64,
        "device": device,
        "device_binding_sha256": document_hash(device),
        "source": {
            "kind": "whole_device",
            "kernel_path_at_preview": "/dev/test-source",
            "partition_number": None,
            "filesystem_type": "ext4",
            "filesystem_uuid": "source-fs",
            "filesystem_label": "Archive",
            "signature_source": "fixture",
            "read_only_options": ["ro", "noload", "nodev", "nosuid", "noexec"],
        },
        "destination": {
            "backend_id": "33333333-3333-3333-3333-333333333333",
            "storage_group_id": "44444444-4444-4444-4444-444444444444",
            "name": "Media destination",
            "path": str(destination),
            "stable_identity": "managed:destination",
            "device_number": destination.stat().st_dev,
            "free_bytes_at_preview": 1_000_000_000,
            "reserve_bytes": 0,
        },
        "inventory": {
            "file_count": len(files),
            "total_bytes": sum(path.stat().st_size for path in files),
        },
        "verification": {"mode": "accurate", "algorithm": "blake3"},
        "collision_policy": collision_policy,
        "source_access": "read_only",
        "source_retained": True,
        "parity_reuse_supported": False,
    }
    value["plan_sha256"] = document_hash(value)
    return value


def _seed(tmp_path: Path, *, collision_policy: str = "stop"):
    session_factory = _runtime(tmp_path)
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "Movies").mkdir()
    (source / "Movies" / "feature.mkv").write_bytes(b"media-payload" * 4096)
    (source / "metadata.xml").write_text("<movie>safe</movie>", encoding="utf-8")
    plan = _plan(destination, source, collision_policy=collision_policy)
    with session_factory() as session, session.begin():
        group = StorageGroup(
            id=str(plan["destination"]["storage_group_id"]),
            name="Media",
            namespace_path=str(destination),
            purpose="media",
        )
        backend = StorageBackend(
            id=str(plan["destination"]["backend_id"]),
            storage_group_id=group.id,
            stable_identity="managed:destination",
            namespace_path=str(destination),
            lifecycle_state="preferred_write",
        )
        operation = Operation(
            kind="storage.foreign.migrate",
            status="running",
            actor_type="session",
            actor_id="owner",
            resource_type="foreign_storage",
            resource_id=str(plan["candidate_id"]),
            request_sha256=document_hash(plan),
            request_json={"plan": plan},
            heartbeat_at=utc_now(),
        )
        session.add_all([group, backend, operation])
        session.flush()
        session.add(
            ForeignMigrationJob(
                id=operation.id,
                candidate_id=str(plan["candidate_id"]),
                destination_backend_id=backend.id,
                plan_sha256=str(plan["plan_sha256"]),
                verification_mode="accurate",
                collision_policy=collision_policy,
                status="running",
                phase="preflight",
                report_json={},
            )
        )
        operation_id = operation.id
    return session_factory, operation_id, plan, source, destination


@pytest.mark.skipif(os.name != "posix", reason="descriptor-relative mover requires Linux")
def test_foreign_migration_pauses_resumes_verifies_and_retains_source(tmp_path: Path) -> None:
    session_factory, operation_id, plan, source, destination = _seed(tmp_path)
    paused = False

    def pause_after_inventory(phase: str) -> None:
        nonlocal paused
        if phase == "copying" and not paused:
            paused = True
            with session_factory() as session, session.begin():
                job = session.get(ForeignMigrationJob, operation_id)
                assert job is not None
                job.pause_requested = True

    with pytest.raises(DrainPaused):
        execute_foreign_migration(
            session_factory,
            operation_id,
            plan,
            source_root_override=source,
            phase_hook=pause_after_inventory,
        )
    with session_factory() as session, session.begin():
        operation = session.get(Operation, operation_id)
        assert operation is not None
        mark_foreign_migration_paused(session, operation)
        resume_foreign_migration(session, operation)
        operation.status = "running"

    report = execute_foreign_migration(
        session_factory, operation_id, plan, source_root_override=source
    )
    assert report["files_verified"] == 2
    assert report["source_retained"] is True
    assert report["parity_reused"] is False
    assert (source / "metadata.xml").read_text(encoding="utf-8") == "<movie>safe</movie>"
    assert (destination / "Movies" / "feature.mkv").read_bytes() == (
        source / "Movies" / "feature.mkv"
    ).read_bytes()
    with session_factory() as session:
        job = session.get(ForeignMigrationJob, operation_id)
        entries = list(
            session.scalars(
                select(ForeignMigrationEntry).where(ForeignMigrationEntry.job_id == operation_id)
            )
        )
        assert job is not None and job.status == "succeeded"
        assert {entry.status for entry in entries} == {"verified"}
        assert {entry.digest_algorithm for entry in entries} == {"blake3"}


@pytest.mark.skipif(os.name != "posix", reason="descriptor-relative mover requires Linux")
def test_foreign_migration_stops_on_collision_without_overwrite(tmp_path: Path) -> None:
    session_factory, operation_id, plan, source, destination = _seed(tmp_path)
    (destination / "metadata.xml").write_text("existing", encoding="utf-8")

    with pytest.raises(ForeignMigrationError) as failure:
        execute_foreign_migration(session_factory, operation_id, plan, source_root_override=source)

    assert failure.value.code == "destination_collision"
    assert (destination / "metadata.xml").read_text(encoding="utf-8") == "existing"
    assert (source / "metadata.xml").read_text(encoding="utf-8") == "<movie>safe</movie>"


@pytest.mark.skipif(os.name != "posix", reason="descriptor-relative mover requires Linux")
def test_foreign_migration_copies_only_reviewed_archive_selection(tmp_path: Path) -> None:
    session_factory, operation_id, plan, source, destination = _seed(tmp_path)
    plan["schema_version"] = 2
    plan["selection"] = {
        "mode": "selected_folders",
        "include_paths": ["Movies"],
        "include_extensions": [],
        "include_globs": [],
        "exclude_globs": [],
        "capacity_upper_bound_bytes": plan["inventory"]["total_bytes"],
        "exact_selected_bytes_at_review": None,
    }
    plan.pop("plan_sha256")
    plan["plan_sha256"] = document_hash(plan)

    report = execute_foreign_migration(
        session_factory, operation_id, plan, source_root_override=source
    )

    assert report["files_total"] == 1
    assert report["selection"]["mode"] == "selected_folders"
    assert (destination / "Movies" / "feature.mkv").is_file()
    assert not (destination / "metadata.xml").exists()


@pytest.mark.skipif(os.name != "posix", reason="descriptor-relative mover requires Linux")
def test_foreign_migration_source_read_error_is_resumable_without_false_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_factory, operation_id, plan, source, destination = _seed(tmp_path)
    original_copy = foreign_migration_worker._copy_entry
    injected = False

    def fail_first_copy(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal injected
        if not injected:
            injected = True
            raise DrainExecutionError(
                "source_read_failed", "A source file could not be read safely."
            )
        return original_copy(*args, **kwargs)

    monkeypatch.setattr(foreign_migration_worker, "_copy_entry", fail_first_copy)
    with pytest.raises(ForeignMigrationError) as failure:
        execute_foreign_migration(session_factory, operation_id, plan, source_root_override=source)

    assert failure.value.code == "source_read_failed"
    assert not any(path.is_file() for path in destination.rglob("*"))
    assert (source / "metadata.xml").read_text(encoding="utf-8") == "<movie>safe</movie>"
    with session_factory() as session:
        job = session.get(ForeignMigrationJob, operation_id)
        entries = list(
            session.scalars(
                select(ForeignMigrationEntry).where(ForeignMigrationEntry.job_id == operation_id)
            )
        )
        assert job is not None and job.status != "succeeded"
        assert {entry.status for entry in entries} == {"pending"}

    monkeypatch.setattr(foreign_migration_worker, "_copy_entry", original_copy)
    report = execute_foreign_migration(
        session_factory, operation_id, plan, source_root_override=source
    )
    assert report["files_verified"] == 2
    assert report["source_retained"] is True


def test_interrupted_foreign_migration_requeues_durable_checkpoint(tmp_path: Path) -> None:
    session_factory, operation_id, _plan, _source, _destination = _seed(tmp_path)
    with session_factory() as session, session.begin():
        operation = session.get(Operation, operation_id)
        job = session.get(ForeignMigrationJob, operation_id)
        assert operation is not None and job is not None
        operation.heartbeat_at = utc_now() - timedelta(minutes=10)
        operation.lease_owner = "stopped-worker"
        job.phase = "copying"
    with session_factory() as session, session.begin():
        assert recover_stale_operations(session, max_age_seconds=30) == 1
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        job = session.get(ForeignMigrationJob, operation_id)
        assert operation is not None and operation.status == "queued"
        assert operation.lease_owner is None
        assert job is not None and job.status == "queued" and job.phase == "copying"
