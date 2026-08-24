#!/usr/bin/env python3
"""Execute read-only foreign intake into a disposable managed destination."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import uuid
from pathlib import Path

from hoardarr.db.models import (
    Base,
    ForeignMigrationEntry,
    ForeignMigrationJob,
    HardwareSnapshot,
    Operation,
    StorageBackend,
    StorageGroup,
    utc_now,
)
from hoardarr.operations.service import document_hash, recover_stale_operations
from hoardarr.storage import executor
from hoardarr.storage.drain_worker import DrainPaused
from hoardarr.storage.executor import Paths, apply_foreign_inspection
from hoardarr.storage.foreign import (
    assess_foreign_storage,
    build_inspection_plan,
    build_migration_plan,
)
from hoardarr.storage.foreign_migration_worker import (
    ForeignMigrationError,
    execute_foreign_migration,
    mark_foreign_migration_paused,
    resume_foreign_migration,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


def capture(*arguments: str) -> str:
    return subprocess.run(
        list(arguments), check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()


def mounted_targets(source: Path) -> str:
    result = subprocess.run(
        [
            "findmnt",
            "--noheadings",
            "--raw",
            "--source",
            str(source),
            "--output",
            "TARGET",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode not in {0, 1}:
        raise SystemExit("findmnt could not verify source activation")
    return result.stdout.strip()


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or not Path("/.hoardarr-disposable-runner").is_file():
        raise SystemExit("requires a marked disposable root runner")
    backing = Path(
        capture("losetup", "--noheadings", "--output", "BACK-FILE", str(args.loop))
    )
    if args.work_root.resolve() not in backing.resolve().parents:
        raise SystemExit("loop backing file is outside the test-created work root")
    if mounted_targets(args.loop):
        raise SystemExit("foreign source must begin unmounted")

    destination = args.work_root / "foreign-managed-destination"
    destination.mkdir(mode=0o700)
    filesystem_uuid = capture("blkid", "-s", "UUID", "-o", "value", str(args.loop))
    filesystem_type = capture("blkid", "-s", "TYPE", "-o", "value", str(args.loop))
    serial = document_hash(str(backing))[:32]
    device_id = f"loop-test:{document_hash(str(backing))[:24]}"
    hardware = {
        "schema_version": 1,
        "source": {"kind": "sysfs"},
        "disks": [
            {
                "id": device_id,
                "stable_identity": True,
                "vendor": "HOARDARR-CI",
                "model": "Disposable loop filesystem",
                "identity": {
                    "serial": serial,
                    "wwn": None,
                    "eui64": None,
                    "nguid": None,
                },
                "capacity_bytes": int(
                    capture("blockdev", "--getsize64", str(args.loop))
                ),
                "sector_sizes": {
                    "logical_bytes": int(
                        capture("blockdev", "--getss", str(args.loop))
                    ),
                    "physical_bytes": int(
                        capture("blockdev", "--getpbsz", str(args.loop))
                    ),
                },
                "kernel_path": str(args.loop),
                "system_disk": False,
                "read_only": False,
                "mountpoints": [],
                "partitions": [],
                "signatures": [
                    {
                        "type": filesystem_type,
                        "usage": "filesystem",
                        "uuid": filesystem_uuid,
                        "label": None,
                        "source": "blkid",
                    }
                ],
                "signature_scan": {
                    "status": "complete",
                    "source": "blkid",
                    "reason": None,
                },
            }
        ],
    }
    engine = create_engine(
        f"sqlite:///{(args.work_root / 'foreign-migration.db').as_posix()}"
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    with session_factory() as session, session.begin():
        scan = Operation(
            kind="hardware.scan",
            status="succeeded",
            actor_type="system",
            actor_id="integration",
            request_sha256=document_hash({}),
            request_json={},
        )
        session.add(scan)
        session.flush()
        snapshot = HardwareSnapshot(
            operation_id=scan.id,
            detector_schema_version=1,
            source="sysfs",
            payload_json=hardware,
            sha256=document_hash(hardware),
        )
        session.add(snapshot)
        session.flush()
        candidate_id = assess_foreign_storage(session, snapshot=snapshot)["candidates"][
            0
        ]["id"]
        inspection_plan = build_inspection_plan(
            session, snapshot=snapshot, candidate_id=candidate_id
        )
        snapshot_id = snapshot.id

    paths = Paths(
        quarantine_marker=args.work_root / "quarantine.json",
        transaction_root=args.work_root / "migration-transactions",
        lock_root=args.work_root / "migration-locks",
        inspection_root=args.work_root / "migration-inspection",
        dev_by_id=Path("/dev/disk/by-id"),
    )
    paths.dev_by_id.mkdir(parents=True, exist_ok=True)
    alias = paths.dev_by_id / f"hoardarr-ci-migration-{uuid.uuid4().hex}"
    alias.symlink_to(args.loop)
    original_quarantine = executor.validate_quarantine
    executor.validate_quarantine = lambda _marker: {"ready": True}
    try:
        inspection_result = apply_foreign_inspection(
            {
                "operation": "apply_foreign_inspection",
                "operation_id": str(uuid.uuid4()),
                "plan_sha256": inspection_plan["plan_sha256"],
                "plan": inspection_plan,
                "confirmation_sha256": document_hash(
                    {"confirmation": "INSPECT READ ONLY"}
                ),
            },
            paths=paths,
            inventory_provider=lambda: hardware,
        )
    finally:
        executor.validate_quarantine = original_quarantine
        alias.unlink(missing_ok=True)

    with session_factory() as session, session.begin():
        inspection = Operation(
            kind="storage.foreign.inspect",
            status="succeeded",
            actor_type="system",
            actor_id="integration",
            resource_type="foreign_storage",
            resource_id=candidate_id,
            request_sha256=document_hash(inspection_plan),
            request_json={"plan": inspection_plan},
            result_json=inspection_result,
        )
        group = StorageGroup(
            name="Managed media", namespace_path=str(destination), purpose="media"
        )
        session.add_all([inspection, group])
        session.flush()
        backend = StorageBackend(
            storage_group_id=group.id,
            stable_identity=f"filesystem:{destination.stat().st_dev}",
            namespace_path=str(destination),
            lifecycle_state="preferred_write",
        )
        session.add(backend)
        session.flush()
        snapshot = session.get(HardwareSnapshot, snapshot_id)
        assert snapshot is not None
        migration_plan = build_migration_plan(
            session,
            snapshot=snapshot,
            candidate_id=candidate_id,
            destination_backend_id=backend.id,
            verification_mode="accurate",
            collision_policy="stop",
            reserve_bytes=0,
        )
        operation = Operation(
            kind="storage.foreign.migrate",
            status="running",
            actor_type="system",
            actor_id="integration",
            resource_type="foreign_storage",
            resource_id=candidate_id,
            request_sha256=document_hash(migration_plan),
            request_json={"plan": migration_plan},
            heartbeat_at=utc_now(),
        )
        session.add(operation)
        session.flush()
        session.add(
            ForeignMigrationJob(
                id=operation.id,
                candidate_id=candidate_id,
                destination_backend_id=backend.id,
                plan_sha256=migration_plan["plan_sha256"],
                verification_mode="accurate",
                collision_policy="stop",
                status="running",
                phase="preflight",
                report_json={},
            )
        )
        operation_id = operation.id

    paused_once = False

    def pause_after_inventory(phase: str) -> None:
        nonlocal paused_once
        if phase == "copying" and not paused_once:
            paused_once = True
            with session_factory() as session, session.begin():
                job = session.get(ForeignMigrationJob, operation_id)
                assert job is not None
                job.pause_requested = True

    try:
        execute_foreign_migration(
            session_factory,
            operation_id,
            migration_plan,
            phase_hook=pause_after_inventory,
        )
    except DrainPaused:
        with session_factory() as session, session.begin():
            operation = session.get(Operation, operation_id)
            assert operation is not None
            mark_foreign_migration_paused(session, operation)
            resume_foreign_migration(session, operation)
            operation.status = "running"
    else:
        raise SystemExit("migration did not honor the requested safe pause")
    recovered_mount = Path("/run/hoardarr/foreign-migrations") / operation_id
    recovered_mount.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    recovered_mount.mkdir(mode=0o700)
    subprocess.run(
        [
            "mount",
            "--read-only",
            "--types",
            filesystem_type,
            "--options",
            "ro,noload,nodev,nosuid,noexec",
            str(args.loop),
            str(recovered_mount),
        ],
        check=True,
        timeout=30,
    )
    report = execute_foreign_migration(session_factory, operation_id, migration_plan)
    stale_private_mount_recovered = not recovered_mount.exists()

    verify_mount = args.work_root / "verify-source"
    verify_mount.mkdir()
    subprocess.run(
        ["mount", "--read-only", "-o", "ro,noload", str(args.loop), str(verify_mount)],
        check=True,
        timeout=30,
    )
    try:
        source_hash = tree_hash(verify_mount)
        destination_hash = tree_hash(destination)
    finally:
        subprocess.run(["umount", "--", str(verify_mount)], check=True, timeout=30)
        verify_mount.rmdir()
    if source_hash != destination_hash:
        raise SystemExit("source and destination hashes differ")

    selected_destination = args.work_root / "foreign-selected-destination"
    selected_destination.mkdir(mode=0o700)
    with session_factory() as session, session.begin():
        group = session.scalar(
            select(StorageGroup).where(StorageGroup.name == "Managed media")
        )
        snapshot = session.get(HardwareSnapshot, snapshot_id)
        assert group is not None and snapshot is not None
        selected_backend = StorageBackend(
            storage_group_id=group.id,
            stable_identity=f"filesystem:{selected_destination.stat().st_dev}:selected",
            namespace_path=str(selected_destination),
            lifecycle_state="active",
        )
        session.add(selected_backend)
        session.flush()
        selected_plan = build_migration_plan(
            session,
            snapshot=snapshot,
            candidate_id=candidate_id,
            destination_backend_id=selected_backend.id,
            verification_mode="accurate",
            collision_policy="stop",
            reserve_bytes=0,
            selection={
                "mode": "selected_folders",
                "include_paths": ["Movies"],
                "include_extensions": [],
                "include_globs": [],
                "exclude_globs": [],
            },
        )
        selected_operation = Operation(
            kind="storage.foreign.migrate",
            status="running",
            actor_type="system",
            actor_id="integration",
            resource_type="foreign_storage",
            resource_id=candidate_id,
            request_sha256=document_hash(selected_plan),
            request_json={"plan": selected_plan},
            heartbeat_at=utc_now(),
        )
        session.add(selected_operation)
        session.flush()
        session.add(
            ForeignMigrationJob(
                id=selected_operation.id,
                candidate_id=candidate_id,
                destination_backend_id=selected_backend.id,
                plan_sha256=selected_plan["plan_sha256"],
                verification_mode="accurate",
                collision_policy="stop",
                status="running",
                phase="preflight",
                report_json={},
            )
        )
        selected_operation_id = selected_operation.id
    selected_report = execute_foreign_migration(
        session_factory, selected_operation_id, selected_plan
    )
    selected_files = sorted(
        path.relative_to(selected_destination).as_posix()
        for path in selected_destination.rglob("*")
        if path.is_file()
    )
    if not selected_files or any(
        not path.startswith("Movies/") for path in selected_files
    ):
        raise SystemExit("selected-folder migration copied an unreviewed source path")

    with session_factory() as session:
        entries = list(
            session.scalars(
                select(ForeignMigrationEntry).where(
                    ForeignMigrationEntry.job_id == operation_id
                )
            )
        )
    collision_plan = dict(migration_plan)
    collision_plan.pop("plan_sha256")
    collision_plan["plan_sha256"] = document_hash(collision_plan)
    with session_factory() as session, session.begin():
        collision_operation = Operation(
            kind="storage.foreign.migrate",
            status="running",
            actor_type="system",
            actor_id="integration",
            resource_type="foreign_storage",
            resource_id=candidate_id,
            request_sha256=document_hash(collision_plan),
            request_json={"plan": collision_plan},
            heartbeat_at=utc_now(),
        )
        session.add(collision_operation)
        session.flush()
        session.add(
            ForeignMigrationJob(
                id=collision_operation.id,
                candidate_id=candidate_id,
                destination_backend_id=collision_plan["destination"]["backend_id"],
                plan_sha256=collision_plan["plan_sha256"],
                verification_mode="accurate",
                collision_policy="stop",
                status="running",
                phase="preflight",
                report_json={},
            )
        )
        collision_operation_id = collision_operation.id
    try:
        execute_foreign_migration(
            session_factory, collision_operation_id, collision_plan
        )
    except ForeignMigrationError as exc:
        collision_code = exc.code
    else:
        raise SystemExit("collision policy did not stop the duplicate copy")

    with session_factory() as session, session.begin():
        interrupted = session.get(Operation, collision_operation_id)
        interrupted_job = session.get(ForeignMigrationJob, collision_operation_id)
        assert interrupted is not None and interrupted_job is not None
        interrupted.heartbeat_at = utc_now().replace(year=2020)
        interrupted.lease_owner = "stopped-worker"
        interrupted_job.phase = "copying"
        recovered = recover_stale_operations(session, max_age_seconds=30)

    evidence = {
        "classification": "VERIFIED IN ISOLATION",
        "source": "disposable Linux loop-backed ext4 filesystem",
        "filesystem_uuid": filesystem_uuid,
        "files_total": report["files_total"],
        "files_verified": report["files_verified"],
        "bytes_copied": report["bytes_copied"],
        "relative_paths_preserved": report["relative_paths_preserved"],
        "source_access": report["source_access"],
        "source_retained": report["source_retained"],
        "parity_reused": report["parity_reused"],
        "pause_resume_executed": paused_once,
        "restart_recovery_requeued": recovered == 1,
        "stale_private_mount_recovered": stale_private_mount_recovered,
        "collision_policy": "stop",
        "collision_failure_code": collision_code,
        "source_sha256": source_hash,
        "destination_sha256": destination_hash,
        "entry_states": sorted({entry.status for entry in entries}),
        "source_unmounted_after": not bool(mounted_targets(args.loop)),
        "selected_folder_execution": {
            "mode": selected_report["selection"]["mode"],
            "include_paths": selected_report["selection"]["include_paths"],
            "files": selected_files,
            "files_verified": selected_report["files_verified"],
        },
    }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(
        json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
