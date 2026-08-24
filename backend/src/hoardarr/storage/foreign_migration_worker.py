from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from hoardarr.db.models import (
    ForeignMigrationEntry,
    ForeignMigrationJob,
    HardwareSnapshot,
    Operation,
    StorageBackend,
    utc_now,
)
from hoardarr.operations.service import append_event
from hoardarr.storage.drain import arr_activity
from hoardarr.storage.drain_worker import (
    DrainExecutionError,
    DrainPaused,
    _copy_entry,
    _open_root_directory,
    _verify_entry,
)
from hoardarr.storage.foreign import ForeignStorageError, validate_migration_plan

INVENTORY_BATCH_SIZE = 250
MAXIMUM_FILES = 100_000


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


class ForeignMigrationError(RuntimeError):
    def __init__(self, code: str, message: str, *, needs_attention: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.needs_attention = needs_attention


def _translate(exc: DrainExecutionError) -> ForeignMigrationError:
    return ForeignMigrationError(exc.code, exc.safe_message, needs_attention=exc.needs_attention)


def _run(arguments: list[str], timeout: int = 120) -> str:
    try:
        completed = subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ForeignMigrationError(
            "foreign_mount_failed", "The source could not be mounted safely for migration."
        ) from exc
    if completed.returncode != 0:
        raise ForeignMigrationError(
            "foreign_mount_failed", "The source could not be mounted safely for migration."
        )
    return completed.stdout[:65536]


def _verified_mount(target: Path, plan: dict[str, Any], findmnt: str) -> dict[str, Any] | None:
    try:
        output = _run(
            [
                findmnt,
                "--json",
                "--mountpoint",
                os.fspath(target),
                "--output",
                "SOURCE,FSTYPE,OPTIONS",
            ],
            30,
        )
        payload = json.loads(output)
    except (ForeignMigrationError, json.JSONDecodeError):
        return None
    filesystems = payload.get("filesystems") if isinstance(payload, dict) else None
    item = filesystems[0] if isinstance(filesystems, list) and len(filesystems) == 1 else None
    if not isinstance(item, dict):
        return None
    options = item.get("options")
    option_set = (
        {value.strip() for value in options.split(",")} if isinstance(options, str) else set()
    )
    source = item.get("source")
    expected_source = Path(str(plan["source"]["kernel_path_at_preview"]))
    try:
        source_matches = (
            isinstance(source, str) and Path(source).resolve() == expected_source.resolve()
        )
    except OSError:
        source_matches = False
    if (
        not source_matches
        or item.get("fstype") != plan["source"]["filesystem_type"]
        or "ro" not in option_set
        or "rw" in option_set
    ):
        return None
    return {"source": source, "filesystem_type": item.get("fstype"), "options": option_set}


def _validate_live_binding(
    session_factory: SessionFactory, operation_id: str, plan: dict[str, Any]
) -> None:
    with session_factory() as session:
        job = session.get(ForeignMigrationJob, operation_id)
        operation = session.get(Operation, operation_id)
        latest = session.scalar(
            select(HardwareSnapshot).order_by(HardwareSnapshot.captured_at.desc()).limit(1)
        )
        destination = session.get(StorageBackend, str(plan["destination"]["backend_id"]))
        if job is None or operation is None:
            raise ForeignMigrationError(
                "foreign_migration_job_missing", "The durable migration checkpoint is unavailable."
            )
        if latest is None or latest.sha256 != plan["hardware_snapshot_sha256"]:
            raise ForeignMigrationError(
                "hardware_snapshot_changed",
                "Storage discovery changed after review. Review the source again.",
            )
        if (
            destination is None
            or destination.stable_identity != plan["destination"]["stable_identity"]
            or destination.lifecycle_state not in {"active", "preferred_write"}
        ):
            raise ForeignMigrationError(
                "foreign_destination_changed", "The managed destination changed after review."
            )
    source = Path(str(plan["source"]["kernel_path_at_preview"]))
    destination_path = Path(str(plan["destination"]["path"]))
    try:
        source_details = source.stat()
        destination_details = destination_path.stat()
        usage = os.statvfs(destination_path)
    except OSError as exc:
        raise ForeignMigrationError(
            "foreign_path_unavailable", "The source or destination is unavailable."
        ) from exc
    if not stat.S_ISBLK(source_details.st_mode):
        raise ForeignMigrationError(
            "foreign_source_changed", "The reviewed source is no longer a block device."
        )
    if destination_details.st_dev != plan["destination"]["device_number"]:
        raise ForeignMigrationError(
            "foreign_destination_changed", "The destination filesystem identity changed."
        )
    required = int(plan["inventory"]["total_bytes"]) + int(plan["destination"]["reserve_bytes"])
    if usage.f_bavail * usage.f_frsize < required:
        raise ForeignMigrationError(
            "foreign_destination_full", "The destination no longer has the reviewed free space."
        )
    blkid = shutil.which("blkid")
    if blkid is None:
        raise ForeignMigrationError(
            "foreign_signature_tool_missing", "The filesystem identity tool is unavailable."
        )
    observed_type = _run([blkid, "-o", "value", "-s", "TYPE", os.fspath(source)], 30).strip()
    observed_uuid = _run([blkid, "-o", "value", "-s", "UUID", os.fspath(source)], 30).strip()
    expected_type = str(plan["source"]["filesystem_type"])
    expected_uuid = str(plan["source"].get("filesystem_uuid") or "")
    if observed_type.casefold() != expected_type.casefold() or (
        expected_uuid and observed_uuid.casefold() != expected_uuid.casefold()
    ):
        raise ForeignMigrationError(
            "foreign_signature_changed", "The source filesystem signature changed after review."
        )


@contextmanager
def _read_only_source(operation_id: str, plan: dict[str, Any]) -> Iterator[Path]:
    root = Path("/run/hoardarr/foreign-migrations")
    target = root / operation_id
    mount = shutil.which("mount")
    umount = shutil.which("umount")
    findmnt = shutil.which("findmnt")
    if not all((mount, umount, findmnt)):
        raise ForeignMigrationError(
            "foreign_mount_tools_missing", "Linux read-only mount tools are unavailable."
        )
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root_details = root.lstat()
    except OSError as exc:
        raise ForeignMigrationError(
            "foreign_private_path_unavailable", "The private migration path is unavailable."
        ) from exc
    if not stat.S_ISDIR(root_details.st_mode) or root_details.st_mode & 0o077 or root.is_symlink():
        raise ForeignMigrationError(
            "foreign_private_path_unsafe", "The private migration path has unsafe permissions."
        )
    mounted = False
    if target.exists():
        try:
            target_details = target.lstat()
            safe_target = (
                stat.S_ISDIR(target_details.st_mode)
                and not target.is_symlink()
                and target_details.st_uid == os.geteuid()
                and not target_details.st_mode & 0o077
            )
        except OSError:
            safe_target = False
        if not safe_target:
            raise ForeignMigrationError(
                "foreign_mountpoint_unsafe",
                "The private migration mount has unsafe ownership or permissions.",
                needs_attention=True,
            )
        if os.path.ismount(target):
            if _verified_mount(target, plan, str(findmnt)) is None:
                raise ForeignMigrationError(
                    "foreign_mountpoint_busy",
                    "The existing private mount does not match this migration.",
                    needs_attention=True,
                )
            mounted = True
        else:
            try:
                target.rmdir()
            except OSError as exc:
                raise ForeignMigrationError(
                    "foreign_mountpoint_busy",
                    "The private migration path contains unexpected data.",
                    needs_attention=True,
                ) from exc
    if not mounted:
        target.mkdir(mode=0o700)
    try:
        if not mounted:
            source = str(plan["source"]["kernel_path_at_preview"])
            options = ",".join(plan["source"]["read_only_options"])
            _run(
                [
                    str(mount),
                    "--read-only",
                    "--types",
                    str(plan["source"]["filesystem_type"]),
                    "--options",
                    options,
                    source,
                    os.fspath(target),
                ]
            )
            mounted = True
        if _verified_mount(target, plan, str(findmnt)) is None:
            raise ForeignMigrationError(
                "foreign_mount_not_read_only",
                "The source mount identity or read-only state could not be proven.",
                needs_attention=True,
            )
        yield target
    finally:
        if mounted:
            try:
                _run([str(umount), "--", os.fspath(target)])
                mounted = False
            except ForeignMigrationError as exc:
                raise ForeignMigrationError(
                    "foreign_unmount_failed",
                    "The private source mount could not be detached.",
                    needs_attention=True,
                ) from exc
        if not mounted:
            try:
                target.rmdir()
            except OSError as exc:
                if target.exists():
                    raise ForeignMigrationError(
                        "foreign_cleanup_failed",
                        "The private migration path could not be removed.",
                        needs_attention=True,
                    ) from exc


def _control(session_factory: SessionFactory, operation_id: str) -> None:
    with session_factory() as session, session.begin():
        job = session.get(ForeignMigrationJob, operation_id)
        operation = session.get(Operation, operation_id)
        if job is None or operation is None:
            raise ForeignMigrationError(
                "foreign_migration_job_missing", "The durable migration checkpoint is unavailable."
            )
        if job.pause_requested or operation.cancel_requested:
            raise DrainPaused()
        operation.heartbeat_at = utc_now()
        operation.updated_at = utc_now()


def _set_phase(
    session_factory: SessionFactory, operation_id: str, phase: str, message: str
) -> None:
    with session_factory() as session, session.begin():
        job = session.get(ForeignMigrationJob, operation_id)
        operation = session.get(Operation, operation_id)
        if job is None or operation is None:
            raise ForeignMigrationError(
                "foreign_migration_job_missing", "The durable migration checkpoint is unavailable."
            )
        job.phase = phase
        job.status = "running"
        if job.started_at is None:
            job.started_at = utc_now()
        job.updated_at = utc_now()
        operation.heartbeat_at = utc_now()
        append_event(session, operation, phase, message)


def _inventory(
    session_factory: SessionFactory, operation_id: str, source_root: Path, plan: dict[str, Any]
) -> None:
    with session_factory() as session, session.begin():
        job = session.get(ForeignMigrationJob, operation_id)
        if job is None:
            raise ForeignMigrationError(
                "foreign_migration_job_missing", "The durable migration checkpoint is unavailable."
            )
        existing = session.scalar(
            select(ForeignMigrationEntry.id)
            .where(ForeignMigrationEntry.job_id == operation_id)
            .limit(1)
        )
        if existing is not None:
            job.phase = "copying"
            return
        session.execute(
            delete(ForeignMigrationEntry).where(ForeignMigrationEntry.job_id == operation_id)
        )
    batch: list[ForeignMigrationEntry] = []
    count = total = 0
    for current, directories, files in os.walk(source_root, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        _control(session_factory, operation_id)
        relative_parent = os.path.relpath(current, source_root)
        for name in files:
            path = Path(current) / name
            try:
                details = path.lstat()
            except OSError as exc:
                raise ForeignMigrationError(
                    "foreign_source_read_failed", "A source file could not be inspected."
                ) from exc
            if not stat.S_ISREG(details.st_mode):
                continue
            count += 1
            if count > MAXIMUM_FILES:
                raise ForeignMigrationError(
                    "foreign_inventory_limit", "The source exceeds the reviewed inventory limit."
                )
            relative = name if relative_parent == "." else f"{relative_parent}/{name}"
            batch.append(
                ForeignMigrationEntry(
                    job_id=operation_id,
                    relative_path=relative,
                    source_size=details.st_size,
                    source_mtime_ns=details.st_mtime_ns,
                    status="pending",
                )
            )
            total += details.st_size
            if len(batch) >= INVENTORY_BATCH_SIZE:
                with session_factory() as session, session.begin():
                    session.add_all(batch)
                batch = []
        directories[:] = [name for name in directories if not (Path(current) / name).is_symlink()]
    if batch:
        with session_factory() as session, session.begin():
            session.add_all(batch)
    if count != plan["inventory"]["file_count"] or total != plan["inventory"]["total_bytes"]:
        raise ForeignMigrationError(
            "foreign_source_changed", "Source contents changed after the read-only inventory."
        )
    with session_factory() as session, session.begin():
        job = session.get(ForeignMigrationJob, operation_id)
        operation = session.get(Operation, operation_id)
        if job is None or operation is None:
            raise ForeignMigrationError(
                "foreign_migration_job_missing", "The durable migration checkpoint is unavailable."
            )
        job.files_total = count
        job.bytes_total = total
        job.phase = "copying"
        job.updated_at = utc_now()
        append_event(
            session,
            operation,
            "inventory",
            "Source inventory was revalidated",
            {"files": count, "bytes": total},
        )


def _next_entry(
    session_factory: SessionFactory, operation_id: str, status_value: str
) -> ForeignMigrationEntry | None:
    with session_factory() as session:
        entry = session.scalar(
            select(ForeignMigrationEntry)
            .where(
                ForeignMigrationEntry.job_id == operation_id,
                ForeignMigrationEntry.status == status_value,
            )
            .order_by(ForeignMigrationEntry.id)
            .limit(1)
        )
        if entry is not None:
            session.expunge(entry)
        return entry


def _checkpoint_copy(
    session_factory: SessionFactory, operation_id: str, entry_id: int, digest: str, reused: bool
) -> None:
    with session_factory() as session, session.begin():
        entry = session.get(ForeignMigrationEntry, entry_id)
        job = session.get(ForeignMigrationJob, operation_id)
        operation = session.get(Operation, operation_id)
        if entry is None or job is None or operation is None:
            raise ForeignMigrationError(
                "foreign_migration_job_changed", "The migration checkpoint changed."
            )
        if entry.status == "pending":
            entry.status = "copied"
            entry.digest_algorithm = "blake3"
            entry.digest_hex = digest
            entry.copied_at = utc_now()
            job.files_copied += 1
            job.bytes_copied += entry.source_size
            if reused:
                job.files_reused += 1
        job.current_relative_path = entry.relative_path
        job.updated_at = utc_now()
        operation.heartbeat_at = utc_now()


def _checkpoint_verified(session_factory: SessionFactory, operation_id: str, entry_id: int) -> None:
    with session_factory() as session, session.begin():
        entry = session.get(ForeignMigrationEntry, entry_id)
        job = session.get(ForeignMigrationJob, operation_id)
        operation = session.get(Operation, operation_id)
        if entry is None or job is None or operation is None:
            raise ForeignMigrationError(
                "foreign_migration_job_changed", "The migration checkpoint changed."
            )
        if entry.status == "copied":
            entry.status = "verified"
            entry.verified_at = utc_now()
            job.files_verified += 1
        job.current_relative_path = entry.relative_path
        job.updated_at = utc_now()
        operation.heartbeat_at = utc_now()


def execute_foreign_migration(
    session_factory: SessionFactory,
    operation_id: str,
    plan: dict[str, Any],
    *,
    source_root_override: Path | None = None,
    phase_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    try:
        validate_migration_plan(plan)
    except ForeignStorageError as exc:
        raise ForeignMigrationError(exc.code, str(exc)) from exc
    if source_root_override is None:
        _validate_live_binding(session_factory, operation_id, plan)
        source_context = _read_only_source(operation_id, plan)
    else:

        @contextmanager
        def override() -> Iterator[Path]:
            yield source_root_override

        source_context = override()
    with source_context as source_root:
        with session_factory() as session:
            job = session.get(ForeignMigrationJob, operation_id)
            if job is None:
                raise ForeignMigrationError(
                    "foreign_migration_job_missing",
                    "The durable migration checkpoint is unavailable.",
                )
            phase = job.phase
        if phase in {"preflight", "inventory"}:
            _set_phase(session_factory, operation_id, "inventory", "Revalidating source inventory")
            _inventory(session_factory, operation_id, source_root, plan)
            phase = "copying"
        if phase_hook:
            phase_hook(phase)
        source_fd = _open_root_directory(source_root, source=True)
        destination_fd = _open_root_directory(plan["destination"]["path"], source=False)
        try:
            if phase == "copying":
                while entry := _next_entry(session_factory, operation_id, "pending"):
                    _control(session_factory, operation_id)
                    with session_factory() as session:
                        if arr_activity(session)["active_writes"]:
                            raise DrainPaused()
                    relative = Path(entry.relative_path)
                    destination_exists = (Path(plan["destination"]["path"]) / relative).exists()
                    try:
                        digest = _copy_entry(
                            source_fd,
                            destination_fd,
                            entry,  # The safe copier needs the shared immutable entry fields only.
                            control=lambda: _control(session_factory, operation_id),
                            digest_algorithm="blake3",
                            collision_policy=str(plan["collision_policy"]),
                        )
                    except DrainExecutionError as exc:
                        raise _translate(exc) from exc
                    _checkpoint_copy(
                        session_factory, operation_id, entry.id, digest, destination_exists
                    )
                _set_phase(session_factory, operation_id, "verifying", "Copy phase completed")
                phase = "verifying"
            if phase_hook:
                phase_hook(phase)
            if phase == "verifying":
                while entry := _next_entry(session_factory, operation_id, "copied"):
                    _control(session_factory, operation_id)
                    try:
                        _verify_entry(
                            destination_fd,
                            entry,
                            str(plan["verification"]["mode"]),
                            control=lambda: _control(session_factory, operation_id),
                        )
                    except DrainExecutionError as exc:
                        raise _translate(exc) from exc
                    _checkpoint_verified(session_factory, operation_id, entry.id)
                _set_phase(session_factory, operation_id, "finalizing", "Verification completed")
                phase = "finalizing"
        finally:
            os.close(source_fd)
            os.close(destination_fd)
    if phase != "finalizing":
        raise ForeignMigrationError(
            "foreign_migration_phase_invalid", "The migration phase is invalid."
        )
    with session_factory() as session, session.begin():
        job = session.get(ForeignMigrationJob, operation_id)
        operation = session.get(Operation, operation_id)
        if job is None or operation is None:
            raise ForeignMigrationError(
                "foreign_migration_job_missing", "The durable migration checkpoint is unavailable."
            )
        completed = utc_now()
        report = {
            "operation_id": operation_id,
            "candidate_id": plan["candidate_id"],
            "destination_backend_id": plan["destination"]["backend_id"],
            "destination_path": plan["destination"]["path"],
            "files_total": job.files_total,
            "files_copied": job.files_copied,
            "files_verified": job.files_verified,
            "files_reused": job.files_reused,
            "bytes_copied": job.bytes_copied,
            "verification": plan["verification"],
            "collision_policy": plan["collision_policy"],
            "relative_paths_preserved": True,
            "source_access": "read_only",
            "source_retained": True,
            "parity_reused": False,
            "completed_at": completed.isoformat(),
        }
        job.status = "succeeded"
        job.phase = "completed"
        job.current_relative_path = None
        job.report_json = report
        job.completed_at = completed
        job.updated_at = completed
        append_event(
            session,
            operation,
            "foreign_migration_completed",
            "Foreign file migration completed",
            report,
        )
        return report


def mark_foreign_migration_paused(session: Session, operation: Operation) -> None:
    job = session.get(ForeignMigrationJob, operation.id)
    if job is None:
        raise ForeignMigrationError(
            "foreign_migration_job_missing", "The durable migration checkpoint is unavailable."
        )
    job.report_json = {**job.report_json, "resume_phase": job.phase}
    job.status = "paused"
    job.phase = "paused"
    job.pause_requested = True
    job.updated_at = utc_now()
    operation.status = "paused"
    operation.cancel_requested = False
    operation.lease_owner = None
    operation.leased_at = None
    operation.heartbeat_at = utc_now()
    operation.updated_at = utc_now()
    append_event(session, operation, "paused", "Foreign migration paused at a safe checkpoint")


def request_foreign_migration_pause(session: Session, operation: Operation) -> ForeignMigrationJob:
    job = session.get(ForeignMigrationJob, operation.id)
    if job is None or operation.kind != "storage.foreign.migrate":
        raise ForeignMigrationError(
            "foreign_migration_job_missing", "The durable migration checkpoint is unavailable."
        )
    if operation.status == "paused":
        return job
    if operation.status not in {"queued", "running"}:
        raise ForeignMigrationError(
            "foreign_migration_not_running", "This migration cannot be paused now."
        )
    if operation.status == "queued":
        mark_foreign_migration_paused(session, operation)
    elif not job.pause_requested:
        job.pause_requested = True
        job.updated_at = utc_now()
        append_event(
            session,
            operation,
            "pause_requested",
            "Migration will pause at the next safe checkpoint",
        )
    return job


def resume_foreign_migration(session: Session, operation: Operation) -> ForeignMigrationJob:
    job = session.get(ForeignMigrationJob, operation.id)
    if job is None or operation.kind != "storage.foreign.migrate":
        raise ForeignMigrationError(
            "foreign_migration_job_missing", "The durable migration checkpoint is unavailable."
        )
    if operation.status not in {"paused", "failed", "needs_attention"}:
        raise ForeignMigrationError(
            "foreign_migration_not_paused", "This migration is not resumable now."
        )
    resume_phase = job.report_json.get("resume_phase") if job.phase == "paused" else job.phase
    if resume_phase not in {"preflight", "inventory", "copying", "verifying", "finalizing"}:
        resume_phase = "inventory"
    job.phase = str(resume_phase)
    job.report_json = {
        key: value for key, value in job.report_json.items() if key != "resume_phase"
    }
    job.pause_requested = False
    job.status = "queued"
    job.updated_at = utc_now()
    operation.status = "queued"
    operation.cancel_requested = False
    operation.lease_owner = None
    operation.leased_at = None
    operation.heartbeat_at = None
    operation.updated_at = utc_now()
    append_event(session, operation, "resumed", "Foreign migration queued to resume")
    return job
