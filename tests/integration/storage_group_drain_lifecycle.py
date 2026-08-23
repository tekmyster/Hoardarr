#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hoardarr.auth.service import Principal
from hoardarr.core.config import Settings
from hoardarr.core.secrets import SecretBox
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.models import (
    Base,
    Operation,
    OperationEvent,
    StorageBackend,
    StorageDrainEntry,
    StorageDrainJob,
    StorageGroup,
    utc_now,
)
from hoardarr.operations.service import document_hash, recover_stale_operations
from hoardarr.operations.worker import run_once
from hoardarr.storage.drain import build_drain_plan
from hoardarr.storage.drain_worker import (
    execute_drain,
    request_drain_pause,
    resume_drain,
)
from hoardarr.storage.groups import (
    assign_backend,
    create_group,
    register_disk,
    transition_backend,
)
from sqlalchemy import select


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    destination = args.destination.resolve(strict=True)
    if source.stat().st_dev == destination.stat().st_dev:
        raise SystemExit("source and destination must be separate disposable filesystems")

    files = {
        "Movies/Feature.mkv": b"hoardarr-media-block\n" * 262_144,
        "TV/Series/episode.mkv": b"episode-block\n" * 131_072,
        "Music/album/track.flac": b"audio-block\n" * 32_768,
        "metadata/library.xml": b"<library><verified>true</verified></library>\n",
    }
    before_hashes: dict[str, str] = {}
    for relative, payload in files.items():
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        before_hashes[relative] = digest(target)

    args.state.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{(args.state / 'drain.db').as_posix()}",
        secret_key_file=args.state / "secret.key",
        secure_cookies=False,
    )
    engine = create_database_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    actor = Principal(
        user_id="11111111-1111-4111-8111-111111111111",
        username="integration-owner",
        is_admin=True,
        auth_type="session",
        scopes=frozenset({"operate"}),
    )
    with session_factory() as session, session.begin():
        group = create_group(
            session,
            name="Disposable Media",
            namespace_path="/srv/hoardarr/media",
            purpose="media",
            principal=actor,
        )
        backend_ids: list[str] = []
        for suffix, path in (("source", source), ("destination", destination)):
            disk, _created = register_disk(
                session,
                {
                    "stable_identity": f"loop-integration:{path.stat().st_dev}:{suffix}",
                    "health_state": "healthy",
                    "capacity_bytes": 512 * 1024 * 1024,
                    "media_type": "virtual",
                },
            )
            backend = assign_backend(
                session,
                group_id=group.id,
                physical_disk_id=disk.id,
                storage_entity_id=None,
                namespace_path=str(path),
                role="data",
                principal=actor,
            )
            transition_backend(
                session,
                group_id=group.id,
                backend_id=backend.id,
                target_state="active",
                principal=actor,
                reason="disposable loop filesystem mounted",
            )
            backend_ids.append(backend.id)
        transition_backend(
            session,
            group_id=group.id,
            backend_id=backend_ids[0],
            target_state="preferred_write",
            principal=actor,
            reason="initial test placement",
        )
        plan = build_drain_plan(
            session,
            group_id=group.id,
            source_backend_id=backend_ids[0],
            destination_backend_ids=[backend_ids[1]],
            verification_mode="accurate",
            reserve_bytes=8 * 1024 * 1024,
            enforce_source_read_only=True,
            bandwidth_limit_mib_per_second=16,
            io_priority="background",
            start_at=datetime.now(UTC) - timedelta(minutes=1),
            maintenance_window_minutes=60,
        )
        if not plan["ready"]:
            raise RuntimeError(f"disposable drain preflight blocked: {plan['blockers']}")
        request = {
            "plan": plan,
            "plan_sha256": plan["plan_sha256"],
            "confirmation_sha256": document_hash({"confirmation": "I AGREE"}),
        }
        operation = Operation(
            kind="storage.drain",
            actor_type="session",
            actor_id=actor.user_id,
            resource_type="storage_group",
            resource_id=group.id,
            idempotency_key="isolated-storage-drain",
            request_sha256=document_hash(request),
            request_json=request,
            status="queued",
        )
        session.add(operation)
        session.flush()
        session.add(
            StorageDrainJob(
                id=operation.id,
                storage_group_id=group.id,
                source_backend_id=backend_ids[0],
                plan_sha256=plan["plan_sha256"],
                verification_mode="accurate",
                status="queued",
                phase="preflight",
                report_json={},
            )
        )
        operation_id = operation.id
        group_id = group.id

    with session_factory() as session, session.begin():
        operation = session.get(Operation, operation_id)
        assert operation is not None
        request_drain_pause(session, operation)
        assert operation.status == "paused"
        resume_drain(session, operation)
        assert operation.status == "queued"
        operation.status = "running"
        operation.lease_owner = "intentionally-stopped-worker"
        operation.leased_at = utc_now()
        operation.heartbeat_at = utc_now()

    interrupted_phase: str | None = None

    def interrupt_after_inventory(phase: str) -> None:
        nonlocal interrupted_phase
        if phase == "copying" and interrupted_phase is None:
            interrupted_phase = phase
            raise RuntimeError("intentional worker interruption")

    try:
        execute_drain(session_factory, operation_id, plan, phase_hook=interrupt_after_inventory)
    except RuntimeError as exc:
        if str(exc) != "intentional worker interruption":
            raise
    if interrupted_phase != "copying":
        raise RuntimeError("worker interruption did not occur after durable inventory")
    with session_factory() as session:
        manifest_ids_before_restart = list(
            session.scalars(
                select(StorageDrainEntry.id)
                .where(StorageDrainEntry.job_id == operation_id)
                .order_by(StorageDrainEntry.id)
            )
        )
    if len(manifest_ids_before_restart) != len(files):
        raise RuntimeError("durable manifest was not checkpointed before interruption")
    with session_factory() as session, session.begin():
        operation = session.get(Operation, operation_id)
        assert operation is not None
        operation.heartbeat_at = utc_now() - timedelta(minutes=10)
    with session_factory() as session, session.begin():
        if recover_stale_operations(session, max_age_seconds=30) != 1:
            raise RuntimeError("interrupted drain was not recovered")

    if not run_once(
        session_factory=session_factory,
        settings=settings,
        secret_box=SecretBox.from_file(settings.secret_key_file, create=True),
        worker_id="replacement-worker",
    ):
        raise RuntimeError("replacement worker did not claim the recovered drain")

    after_hashes = {relative: digest(destination / relative) for relative in files}
    if after_hashes != before_hashes:
        raise RuntimeError("destination hashes differ after drain")
    if any(path.is_file() for path in source.rglob("*")):
        raise RuntimeError("verified source files were not retired")
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        job = session.get(StorageDrainJob, operation_id)
        group = session.get(StorageGroup, group_id)
        source_backend = session.get(StorageBackend, job.source_backend_id if job else "")
        entries = list(
            session.scalars(select(StorageDrainEntry).where(StorageDrainEntry.job_id == operation_id))
        )
        events = list(
            session.scalars(
                select(OperationEvent)
                .where(OperationEvent.operation_id == operation_id)
                .order_by(OperationEvent.sequence)
            )
        )
        if operation is None or operation.status != "succeeded":
            raise RuntimeError("recovered drain operation did not succeed")
        if job is None or job.status != "succeeded" or job.files_verified != len(files):
            raise RuntimeError("drain checkpoints are incomplete")
        if source_backend is None or source_backend.lifecycle_state != "retired":
            raise RuntimeError("source backend was not retired")
        if group is None or group.namespace_path != "/srv/hoardarr/media":
            raise RuntimeError("Storage Group namespace changed")
        if sorted(entry.id for entry in entries) != manifest_ids_before_restart:
            raise RuntimeError("restart discarded the durable file manifest")
        evidence = {
            "classification": "VERIFIED IN ISOLATION",
            "environment": "Ubuntu disposable loop-backed ext4 filesystems",
            "operation_id": operation_id,
            "storage_group_id": group_id,
            "namespace_before": "/srv/hoardarr/media",
            "namespace_after": group.namespace_path,
            "source_device": source.stat().st_dev,
            "destination_device": destination.stat().st_dev,
            "files": len(files),
            "bytes": sum(len(value) for value in files.values()),
            "hashes_before": before_hashes,
            "hashes_after": after_hashes,
            "pause_resume_before_start": True,
            "interrupted_phase": interrupted_phase,
            "restart_recovered": True,
            "checkpoint_manifest_preserved": True,
            "source_lifecycle": source_backend.lifecycle_state,
            "source_mount_read_only": bool(os.statvfs(source).f_flag & os.ST_RDONLY),
            "bandwidth_limit_mib_per_second": job.report_json.get("bandwidth_limit_mib_per_second"),
            "io_priority": job.report_json.get("io_priority"),
            "verification_algorithm": job.report_json.get("verification_algorithm"),
            "elapsed_seconds": job.report_json.get("elapsed_seconds"),
            "average_mib_per_second": job.report_json.get("average_mib_per_second"),
            "entry_states": sorted({entry.status for entry in entries}),
            "operation_events": [event.event_type for event in events],
            "report": job.report_json,
        }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
