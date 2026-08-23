from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from hoardarr.auth.service import Principal
from hoardarr.db.models import (
    Operation,
    StorageBackend,
    StorageDrainEntry,
    StorageDrainJob,
    utc_now,
)
from hoardarr.operations.service import append_event
from hoardarr.storage.drain import (
    DrainPlanError,
    arr_activity,
    build_drain_plan,
    validate_drain_plan,
)
from hoardarr.storage.groups import (
    StorageGroupError,
    advance_drain_lifecycle,
    begin_drain_placement,
)

COPY_CHUNK_BYTES = 1024 * 1024
INVENTORY_BATCH_SIZE = 250


@dataclass
class BandwidthLimiter:
    """Bound copy throughput without retaining samples or building a work queue."""

    bytes_per_second: int
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self._started = self.clock()
        self._bytes = 0

    def consume(self, byte_count: int) -> None:
        self._bytes += byte_count
        delay = (self._bytes / self.bytes_per_second) - (self.clock() - self._started)
        if delay > 0:
            self.sleeper(min(delay, 1.0))


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


class DrainExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, needs_attention: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.needs_attention = needs_attention


class DrainPaused(RuntimeError):
    pass


def _execution_deadline(controls: dict[str, Any]) -> datetime | None:
    """Give each started or resumed run its own bounded maintenance window."""

    window = controls.get("maintenance_window_minutes")
    if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
        return None
    return utc_now() + timedelta(minutes=window)


def _set_source_mount_read_only(path: str, read_only: bool) -> None:
    option = "remount,ro" if read_only else "remount,rw"
    try:
        completed = subprocess.run(
            ["mount", "-o", option, path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=60,
        )
        observed = bool(os.statvfs(path).f_flag & os.ST_RDONLY)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DrainExecutionError(
            "source_read_only_failed",
            "Hoardarr could not safely change the source mount access mode.",
            needs_attention=True,
        ) from exc
    if completed.returncode != 0 or observed is not read_only:
        raise DrainExecutionError(
            "source_read_only_failed",
            "Hoardarr could not verify the requested source mount access mode.",
            needs_attention=True,
        )


def _principal(operation: Operation) -> Principal:
    return Principal(
        user_id=operation.actor_id,
        username="durable storage operation",
        is_admin=True,
        auth_type=operation.actor_type,
        scopes=frozenset({"operate"}),
    )


def _relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or len(value) > 4096:
        raise DrainExecutionError("drain_path_invalid", "A drain manifest path is unsafe.")
    if any(part in {"", "."} or any(ord(char) < 32 for char in part) for part in path.parts):
        raise DrainExecutionError("drain_path_invalid", "A drain manifest path is unsafe.")
    return path


def _job_control(
    session_factory: SessionFactory,
    operation_id: str,
    deadline: datetime | None = None,
) -> None:
    expired = False
    with session_factory() as session, session.begin():
        job = session.get(StorageDrainJob, operation_id)
        operation = session.get(Operation, operation_id)
        if job is None or operation is None:
            raise DrainExecutionError("drain_job_missing", "The durable drain job is unavailable.")
        if job.pause_requested:
            raise DrainPaused()
        if operation.cancel_requested:
            # Drain cancellation is a safe pause. Verified source data is never
            # deleted until finalization and can be resumed through the same job.
            raise DrainPaused()
        now = utc_now()
        if deadline is not None:
            comparable_deadline = deadline.replace(tzinfo=None) if now.tzinfo is None else deadline
            if now >= comparable_deadline:
                job.pause_requested = True
                job.report_json = {
                    **job.report_json,
                    "pause_reason": "maintenance_window_ended",
                    "maintenance_window_end": deadline.isoformat(),
                }
                expired = True
            else:
                operation.heartbeat_at = now
                operation.updated_at = now
        else:
            operation.heartbeat_at = now
            operation.updated_at = now
    if expired:
        raise DrainPaused()


def _set_phase(
    session_factory: SessionFactory,
    operation_id: str,
    phase: str,
    message: str,
) -> None:
    with session_factory() as session, session.begin():
        job = session.get(StorageDrainJob, operation_id)
        operation = session.get(Operation, operation_id)
        if job is None or operation is None:
            raise DrainExecutionError("drain_job_missing", "The durable drain job is unavailable.")
        job.status = "running"
        job.phase = phase
        job.updated_at = utc_now()
        operation.heartbeat_at = utc_now()
        append_event(session, operation, "progress", message, {"phase": phase})


def _plan_destinations(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = plan.get("destinations")
    if not isinstance(values, list) or not values:
        raise DrainExecutionError("drain_plan_invalid", "The drain destinations are invalid.")
    result: dict[str, dict[str, Any]] = {}
    for item in values:
        if not isinstance(item, dict) or not isinstance(item.get("backend_id"), str):
            raise DrainExecutionError("drain_plan_invalid", "A drain destination is invalid.")
        result[item["backend_id"]] = item
    return result


def _preflight_and_exclude(
    session_factory: SessionFactory, operation_id: str, plan: dict[str, Any]
) -> None:
    source = plan.get("source")
    if not isinstance(source, dict):
        raise DrainExecutionError("drain_plan_invalid", "The drain source is invalid.")
    destinations = _plan_destinations(plan)
    try:
        with session_factory() as session, session.begin():
            job = session.get(StorageDrainJob, operation_id)
            operation = session.get(Operation, operation_id)
            backend = session.get(StorageBackend, source.get("backend_id"))
            if job is None or operation is None or backend is None:
                raise DrainExecutionError(
                    "drain_job_missing", "The durable drain job is unavailable."
                )
            previous_phase = job.phase
            first_exclusion = backend.lifecycle_state in {"active", "preferred_write"}
            if first_exclusion:
                fresh = build_drain_plan(
                    session,
                    group_id=str(plan.get("storage_group_id")),
                    source_backend_id=str(source.get("backend_id")),
                    destination_backend_ids=list(destinations),
                    verification_mode=str(plan.get("verification", {}).get("mode")),
                    reserve_bytes=int(plan.get("capacity", {}).get("reserve_bytes", -1)),
                    enforce_source_read_only=bool(
                        plan.get("controls", {}).get("enforce_source_read_only", False)
                    ),
                    bandwidth_limit_mib_per_second=plan.get("controls", {}).get(
                        "bandwidth_limit_mib_per_second"
                    ),
                    start_at=(
                        datetime.fromisoformat(plan["controls"]["start_at"])
                        if plan.get("controls", {}).get("start_at")
                        else None
                    ),
                    maintenance_window_minutes=plan.get("controls", {}).get(
                        "maintenance_window_minutes"
                    ),
                )
                if not fresh["ready"]:
                    raise DrainExecutionError(
                        "drain_preflight_changed",
                        "Storage activity, health, or capacity changed after preview.",
                    )
                identity_fields = ("backend_id", "stable_identity", "path", "filesystem_device")
                if any(fresh["source"].get(key) != source.get(key) for key in identity_fields):
                    raise DrainExecutionError(
                        "source_identity_changed", "The drain source identity changed."
                    )
                fresh_destinations = {item["backend_id"]: item for item in fresh["destinations"]}
                for backend_id, expected in destinations.items():
                    observed = fresh_destinations.get(backend_id, {})
                    if any(observed.get(key) != expected.get(key) for key in identity_fields):
                        raise DrainExecutionError(
                            "destination_identity_changed",
                            "A drain destination identity changed.",
                        )
            if backend.lifecycle_state in {"active", "preferred_write", "draining"}:
                begin_drain_placement(
                    session,
                    group_id=job.storage_group_id,
                    source_backend_id=job.source_backend_id,
                    destination_backend_ids=list(destinations),
                    operation_id=operation_id,
                    plan_sha256=job.plan_sha256,
                    principal=_principal(operation),
                )
            elif backend.lifecycle_state in {"verifying", "read_only"}:
                drain = backend.config_json.get("drain")
                if not isinstance(drain, dict) or drain.get("operation_id") != operation_id:
                    raise DrainExecutionError(
                        "drain_operation_changed", "The drain source ownership changed."
                    )
            else:
                raise DrainExecutionError(
                    "source_state_invalid", "The drain source lifecycle state is invalid."
                )
            job.status = "running"
            job.phase = (
                previous_phase
                if previous_phase in {"copying", "verifying", "finalizing"}
                else "inventory"
            )
            job.started_at = job.started_at or utc_now()
            job.updated_at = utc_now()
            if first_exclusion:
                append_event(
                    session,
                    operation,
                    "placement_excluded",
                    "Source removed from new-file placement",
                    {"source_backend_id": job.source_backend_id},
                )
    except (DrainPlanError, StorageGroupError) as exc:
        code = getattr(exc, "code", "drain_preflight_failed")
        raise DrainExecutionError(code, str(exc)) from exc


def _walk_source(root: Path) -> Iterator[tuple[str, os.stat_result]]:
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_names.sort()
        file_names.sort()
        directory_path = Path(directory)
        for name in list(directory_names):
            candidate = directory_path / name
            if candidate.is_symlink():
                raise DrainExecutionError(
                    "source_symlink_unsupported", "Drain sources cannot contain symbolic links."
                )
        for name in file_names:
            candidate = directory_path / name
            try:
                facts = candidate.lstat()
            except OSError as exc:
                raise DrainExecutionError(
                    "source_inventory_failed", "A source file changed during inventory."
                ) from exc
            if stat.S_ISLNK(facts.st_mode):
                raise DrainExecutionError(
                    "source_symlink_unsupported", "Drain sources cannot contain symbolic links."
                )
            if not stat.S_ISREG(facts.st_mode):
                raise DrainExecutionError(
                    "source_type_unsupported",
                    "Drain sources may contain directories and regular files only.",
                )
            relative = candidate.relative_to(root).as_posix()
            _relative_path(relative)
            yield relative, facts


def _flush_inventory(
    session_factory: SessionFactory,
    operation_id: str,
    entries: list[StorageDrainEntry],
    byte_count: int,
) -> None:
    with session_factory() as session, session.begin():
        job = session.get(StorageDrainJob, operation_id)
        operation = session.get(Operation, operation_id)
        if job is None or operation is None:
            raise DrainExecutionError("drain_job_missing", "The durable drain job is unavailable.")
        session.add_all(entries)
        job.files_total += len(entries)
        job.bytes_total += byte_count
        job.updated_at = utc_now()
        operation.heartbeat_at = utc_now()


def _inventory(
    session_factory: SessionFactory,
    operation_id: str,
    plan: dict[str, Any],
    *,
    deadline: datetime | None,
) -> None:
    source = plan["source"]
    root = Path(source["path"])
    try:
        if root.stat().st_dev != source["filesystem_device"]:
            raise DrainExecutionError("source_identity_changed", "The drain source changed.")
    except OSError as exc:
        raise DrainExecutionError("source_unavailable", "The drain source is unavailable.") from exc
    destinations = _plan_destinations(plan)
    reserve = int(plan["capacity"]["reserve_bytes"])
    remaining: dict[str, int] = {}
    for backend_id, destination in destinations.items():
        path = Path(destination["path"])
        try:
            facts = path.stat()
            usage = os.statvfs(path)
        except OSError as exc:
            raise DrainExecutionError(
                "destination_unavailable", "A drain destination is unavailable."
            ) from exc
        if facts.st_dev != destination["filesystem_device"]:
            raise DrainExecutionError(
                "destination_identity_changed", "A drain destination changed."
            )
        remaining[backend_id] = max(
            usage.f_bavail * usage.f_frsize - (reserve // len(destinations)), 0
        )

    with session_factory() as session, session.begin():
        job = session.get(StorageDrainJob, operation_id)
        if job is None:
            raise DrainExecutionError("drain_job_missing", "The durable drain job is unavailable.")
        if job.phase == "inventory":
            session.execute(delete(StorageDrainEntry).where(StorageDrainEntry.job_id == job.id))
            job.files_total = job.files_copied = job.files_verified = 0
            job.bytes_total = job.bytes_copied = 0
            job.current_relative_path = None

    batch: list[StorageDrainEntry] = []
    batch_bytes = 0
    for index, (relative, facts) in enumerate(_walk_source(root)):
        if index % INVENTORY_BATCH_SIZE == 0:
            _job_control(session_factory, operation_id, deadline)
        eligible = [item for item in remaining if remaining[item] >= facts.st_size]
        if not eligible:
            raise DrainExecutionError(
                "destination_capacity_insufficient",
                "No destination has enough safe free space for the next file.",
            )
        destination_id = max(eligible, key=lambda item: remaining[item])
        remaining[destination_id] -= facts.st_size
        batch.append(
            StorageDrainEntry(
                job_id=operation_id,
                relative_path=relative,
                destination_backend_id=destination_id,
                source_size=facts.st_size,
                source_mtime_ns=facts.st_mtime_ns,
                status="pending",
            )
        )
        batch_bytes += facts.st_size
        if len(batch) >= INVENTORY_BATCH_SIZE:
            _flush_inventory(session_factory, operation_id, batch, batch_bytes)
            batch = []
            batch_bytes = 0
    if batch:
        _flush_inventory(session_factory, operation_id, batch, batch_bytes)
    _set_phase(session_factory, operation_id, "copying", "Source inventory completed")


def _open_directory(root_fd: int, parts: tuple[str, ...], *, create: bool) -> int:
    current = os.dup(root_fd)
    try:
        for part in parts:
            try:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=current,
                )
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o755, dir_fd=current)
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=current,
                )
            os.close(current)
            current = next_fd
        return current
    except Exception:
        os.close(current)
        raise


def _hash_fd(fd: int, control: Callable[[], None] | None = None) -> str:
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = 0
    while chunk := os.read(fd, COPY_CHUNK_BYTES):
        digest.update(chunk)
        chunks += 1
        if control is not None and chunks % 16 == 0:
            control()
    return digest.hexdigest()


def _write_all(fd: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = os.write(fd, value[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def _copy_entry(
    source_root_fd: int,
    destination_root_fd: int,
    entry: StorageDrainEntry,
    *,
    control: Callable[[], None],
    limiter: BandwidthLimiter | None = None,
) -> str:
    relative = _relative_path(entry.relative_path)
    source_parent = _open_directory(source_root_fd, relative.parts[:-1], create=False)
    destination_parent = _open_directory(destination_root_fd, relative.parts[:-1], create=True)
    source_fd = temporary_fd = -1
    temporary = f".hoardarr-drain-{entry.job_id}-{entry.id}.partial"
    try:
        source_fd = os.open(
            relative.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=source_parent,
        )
        before = os.fstat(source_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size != entry.source_size
            or before.st_mtime_ns != entry.source_mtime_ns
        ):
            raise DrainExecutionError(
                "source_changed", "A source file changed after the drain was planned."
            )
        try:
            existing_fd = os.open(
                relative.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=destination_parent,
            )
        except FileNotFoundError:
            existing_fd = -1
        if existing_fd >= 0:
            try:
                existing = os.fstat(existing_fd)
                source_digest = _hash_fd(source_fd, control)
                if (
                    existing.st_size != before.st_size
                    or _hash_fd(existing_fd, control) != source_digest
                ):
                    raise DrainExecutionError(
                        "destination_collision",
                        "A different file already exists at the drain destination.",
                    )
                return source_digest
            finally:
                os.close(existing_fd)
        try:
            stale = os.stat(temporary, dir_fd=destination_parent, follow_symlinks=False)
        except FileNotFoundError:
            stale = None
        if stale is not None:
            if not stat.S_ISREG(stale.st_mode):
                raise DrainExecutionError(
                    "destination_staging_unsafe", "A drain staging path is unsafe."
                )
            os.unlink(temporary, dir_fd=destination_parent)
        temporary_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            stat.S_IMODE(before.st_mode),
            dir_fd=destination_parent,
        )
        digest = hashlib.sha256()
        chunks = 0
        while chunk := os.read(source_fd, COPY_CHUNK_BYTES):
            digest.update(chunk)
            _write_all(temporary_fd, chunk)
            if limiter is not None:
                limiter.consume(len(chunk))
            chunks += 1
            if chunks % 16 == 0:
                control()
        os.fsync(temporary_fd)
        after = os.fstat(source_fd)
        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise DrainExecutionError(
                "source_changed", "A source file changed while it was being copied."
            )
        os.utime(
            temporary,
            ns=(before.st_atime_ns, before.st_mtime_ns),
            dir_fd=destination_parent,
            follow_symlinks=False,
        )
        # A hard-link publish is an atomic no-replace operation on the same
        # destination filesystem. Unlike rename, it cannot overwrite a file
        # created in the small interval after the collision check.
        try:
            os.link(
                temporary,
                relative.name,
                src_dir_fd=destination_parent,
                dst_dir_fd=destination_parent,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise DrainExecutionError(
                "destination_collision",
                "A file appeared at the drain destination during copying.",
            ) from exc
        os.unlink(temporary, dir_fd=destination_parent)
        os.fsync(destination_parent)
        return digest.hexdigest()
    except DrainExecutionError:
        raise
    except OSError as exc:
        raise DrainExecutionError(
            "drain_copy_failed", "A file could not be copied safely."
        ) from exc
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if source_fd >= 0:
            os.close(source_fd)
        os.close(source_parent)
        os.close(destination_parent)


def _next_entry(
    session_factory: SessionFactory, operation_id: str, status_value: str
) -> StorageDrainEntry | None:
    with session_factory() as session:
        item = session.scalar(
            select(StorageDrainEntry)
            .where(
                StorageDrainEntry.job_id == operation_id,
                StorageDrainEntry.status == status_value,
            )
            .order_by(StorageDrainEntry.id)
            .limit(1)
        )
        if item is None:
            return None
        session.expunge(item)
        return item


def _checkpoint_copy(
    session_factory: SessionFactory, operation_id: str, entry_id: int, digest: str
) -> None:
    with session_factory() as session, session.begin():
        entry = session.get(StorageDrainEntry, entry_id)
        job = session.get(StorageDrainJob, operation_id)
        operation = session.get(Operation, operation_id)
        if entry is None or job is None or operation is None or entry.job_id != job.id:
            raise DrainExecutionError("drain_job_changed", "The drain checkpoint changed.")
        if entry.status == "pending":
            entry.status = "copied"
            entry.digest_algorithm = "sha256"
            entry.digest_hex = digest
            entry.copied_at = utc_now()
            job.files_copied += 1
            job.bytes_copied += entry.source_size
        job.current_relative_path = entry.relative_path
        job.updated_at = utc_now()
        operation.heartbeat_at = utc_now()


def _copy(
    session_factory: SessionFactory,
    operation_id: str,
    plan: dict[str, Any],
    *,
    deadline: datetime | None,
    limiter: BandwidthLimiter | None,
) -> None:
    source_root = Path(plan["source"]["path"])
    destinations = _plan_destinations(plan)
    source_fd = os.open(source_root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    destination_fds: dict[str, int] = {}
    try:
        for backend_id, item in destinations.items():
            destination_fds[backend_id] = os.open(
                item["path"],
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
            )
        while entry := _next_entry(session_factory, operation_id, "pending"):
            _job_control(session_factory, operation_id, deadline)
            with session_factory() as session:
                if arr_activity(session)["active_writes"]:
                    raise DrainPaused()
            digest = _copy_entry(
                source_fd,
                destination_fds[entry.destination_backend_id],
                entry,
                control=lambda: _job_control(session_factory, operation_id, deadline),
                limiter=limiter,
            )
            _checkpoint_copy(session_factory, operation_id, entry.id, digest)
    finally:
        os.close(source_fd)
        for descriptor in destination_fds.values():
            os.close(descriptor)
    _set_phase(session_factory, operation_id, "verifying", "Copy phase completed")


def _verify_entry(
    destination_root_fd: int,
    entry: StorageDrainEntry,
    mode: str,
    *,
    control: Callable[[], None],
) -> None:
    relative = _relative_path(entry.relative_path)
    parent = _open_directory(destination_root_fd, relative.parts[:-1], create=False)
    descriptor = -1
    try:
        descriptor = os.open(
            relative.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        facts = os.fstat(descriptor)
        if facts.st_size != entry.source_size or facts.st_mtime_ns != entry.source_mtime_ns:
            raise DrainExecutionError(
                "drain_verification_failed", "A copied file did not preserve size and time."
            )
        if mode in {"accurate", "paranoid"} and _hash_fd(descriptor, control) != entry.digest_hex:
            raise DrainExecutionError(
                "drain_verification_failed", "A copied file failed checksum verification."
            )
        if mode == "paranoid" and _hash_fd(descriptor, control) != entry.digest_hex:
            raise DrainExecutionError(
                "drain_verification_failed", "A copied file failed its second read pass."
            )
    except DrainExecutionError:
        raise
    except OSError as exc:
        raise DrainExecutionError(
            "drain_verification_failed", "A copied file could not be verified."
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def _checkpoint_verified(session_factory: SessionFactory, operation_id: str, entry_id: int) -> None:
    with session_factory() as session, session.begin():
        entry = session.get(StorageDrainEntry, entry_id)
        job = session.get(StorageDrainJob, operation_id)
        operation = session.get(Operation, operation_id)
        if entry is None or job is None or operation is None or entry.job_id != job.id:
            raise DrainExecutionError("drain_job_changed", "The drain checkpoint changed.")
        if entry.status == "copied":
            entry.status = "verified"
            entry.verified_at = utc_now()
            job.files_verified += 1
        job.current_relative_path = entry.relative_path
        job.updated_at = utc_now()
        operation.heartbeat_at = utc_now()


def _verify(
    session_factory: SessionFactory,
    operation_id: str,
    plan: dict[str, Any],
    *,
    deadline: datetime | None,
) -> None:
    with session_factory() as session, session.begin():
        job = session.get(StorageDrainJob, operation_id)
        operation = session.get(Operation, operation_id)
        if job is None or operation is None:
            raise DrainExecutionError("drain_job_missing", "The durable drain job is unavailable.")
        advance_drain_lifecycle(
            session,
            group_id=job.storage_group_id,
            source_backend_id=job.source_backend_id,
            operation_id=operation_id,
            target_state="verifying",
            principal=_principal(operation),
            details={"files_copied": job.files_copied},
        )
    destinations = _plan_destinations(plan)
    destination_fds = {
        backend_id: os.open(
            item["path"], os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        )
        for backend_id, item in destinations.items()
    }
    try:
        while entry := _next_entry(session_factory, operation_id, "copied"):
            _job_control(session_factory, operation_id, deadline)
            _verify_entry(
                destination_fds[entry.destination_backend_id],
                entry,
                str(plan["verification"]["mode"]),
                control=lambda: _job_control(session_factory, operation_id, deadline),
            )
            _checkpoint_verified(session_factory, operation_id, entry.id)
    finally:
        for descriptor in destination_fds.values():
            os.close(descriptor)
    _set_phase(session_factory, operation_id, "finalizing", "Verification completed")


def _remove_source(source_root_fd: int, entry: StorageDrainEntry) -> None:
    relative = _relative_path(entry.relative_path)
    parent = _open_directory(source_root_fd, relative.parts[:-1], create=False)
    try:
        try:
            facts = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        if (
            not stat.S_ISREG(facts.st_mode)
            or facts.st_size != entry.source_size
            or facts.st_mtime_ns != entry.source_mtime_ns
        ):
            raise DrainExecutionError(
                "source_changed", "A verified source file changed before retirement."
            )
        descriptor = os.open(
            relative.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        try:
            if entry.digest_hex and _hash_fd(descriptor) != entry.digest_hex:
                raise DrainExecutionError(
                    "source_changed", "A verified source file changed before retirement."
                )
        finally:
            os.close(descriptor)
        os.unlink(relative.name, dir_fd=parent)
        os.fsync(parent)
    except DrainExecutionError:
        raise
    except OSError as exc:
        raise DrainExecutionError(
            "source_cleanup_failed", "A verified source file could not be retired."
        ) from exc
    finally:
        os.close(parent)


def _checkpoint_removed(session_factory: SessionFactory, operation_id: str, entry_id: int) -> None:
    with session_factory() as session, session.begin():
        entry = session.get(StorageDrainEntry, entry_id)
        job = session.get(StorageDrainJob, operation_id)
        operation = session.get(Operation, operation_id)
        if entry is None or job is None or operation is None or entry.job_id != job.id:
            raise DrainExecutionError("drain_job_changed", "The drain checkpoint changed.")
        entry.status = "removed"
        job.current_relative_path = entry.relative_path
        job.updated_at = utc_now()
        operation.heartbeat_at = utc_now()


def _finalize(
    session_factory: SessionFactory,
    operation_id: str,
    plan: dict[str, Any],
    *,
    deadline: datetime | None,
) -> dict[str, Any]:
    read_only_requested = bool(plan.get("controls", {}).get("enforce_source_read_only"))
    if read_only_requested:
        _set_source_mount_read_only(plan["source"]["path"], False)
    source_fd = -1
    try:
        source_fd = os.open(
            plan["source"]["path"],
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        )
        while entry := _next_entry(session_factory, operation_id, "verified"):
            _job_control(session_factory, operation_id, deadline)
            _remove_source(source_fd, entry)
            _checkpoint_removed(session_factory, operation_id, entry.id)
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if read_only_requested:
            _set_source_mount_read_only(plan["source"]["path"], True)

    with session_factory() as session, session.begin():
        job = session.get(StorageDrainJob, operation_id)
        operation = session.get(Operation, operation_id)
        if job is None or operation is None:
            raise DrainExecutionError("drain_job_missing", "The durable drain job is unavailable.")
        actor = _principal(operation)
        advance_drain_lifecycle(
            session,
            group_id=job.storage_group_id,
            source_backend_id=job.source_backend_id,
            operation_id=operation_id,
            target_state="verifying",
            principal=actor,
            details={"files_verified": job.files_verified},
        )
        advance_drain_lifecycle(
            session,
            group_id=job.storage_group_id,
            source_backend_id=job.source_backend_id,
            operation_id=operation_id,
            target_state="read_only",
            principal=actor,
            details={
                "filesystem_enforced": read_only_requested,
                "source_files_removed": True,
            },
        )
        advance_drain_lifecycle(
            session,
            group_id=job.storage_group_id,
            source_backend_id=job.source_backend_id,
            operation_id=operation_id,
            target_state="retired",
            principal=actor,
            details={"namespace_path": plan["storage_group_namespace"]},
        )
        report = {
            "storage_group_id": job.storage_group_id,
            "source_backend_id": job.source_backend_id,
            "files_moved": job.files_total,
            "bytes_moved": job.bytes_total,
            "verification_mode": job.verification_mode,
            "source_state": "retired",
            "namespace_path": plan["storage_group_namespace"],
            "namespace_preserved": True,
            "source_files_removed_after_verification": True,
            "filesystem_read_only_enforced": read_only_requested,
            "bandwidth_limit_mib_per_second": plan.get("controls", {}).get(
                "bandwidth_limit_mib_per_second"
            ),
            "scheduled_start": plan.get("controls", {}).get("start_at"),
        }
        job.status = "succeeded"
        job.phase = "completed"
        job.current_relative_path = None
        job.report_json = report
        job.completed_at = utc_now()
        job.updated_at = utc_now()
        append_event(session, operation, "drain_completed", "Storage drain completed", report)
        return report


def execute_drain(
    session_factory: SessionFactory,
    operation_id: str,
    plan: dict[str, Any],
    *,
    phase_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Execute or resume one immutable drain without holding a DB transaction during I/O."""

    try:
        validate_drain_plan(plan)
    except DrainPlanError as exc:
        raise DrainExecutionError(exc.code, str(exc)) from exc
    controls = plan.get("controls", {})
    deadline = _execution_deadline(controls)
    bandwidth = controls.get("bandwidth_limit_mib_per_second")
    limiter = (
        BandwidthLimiter(int(bandwidth) * 1024 * 1024)
        if isinstance(bandwidth, int) and bandwidth > 0
        else None
    )
    _job_control(session_factory, operation_id, deadline)
    _preflight_and_exclude(session_factory, operation_id, plan)
    if controls.get("enforce_source_read_only"):
        _set_source_mount_read_only(plan["source"]["path"], True)
    with session_factory() as session:
        job = session.get(StorageDrainJob, operation_id)
        if job is None:
            raise DrainExecutionError("drain_job_missing", "The durable drain job is unavailable.")
        phase = job.phase
    if phase_hook:
        phase_hook(phase)
    if phase in {"preflight", "inventory"}:
        _inventory(session_factory, operation_id, plan, deadline=deadline)
        phase = "copying"
    if phase_hook:
        phase_hook(phase)
    if phase == "copying":
        _copy(session_factory, operation_id, plan, deadline=deadline, limiter=limiter)
        phase = "verifying"
    if phase_hook:
        phase_hook(phase)
    if phase == "verifying":
        _verify(session_factory, operation_id, plan, deadline=deadline)
        phase = "finalizing"
    if phase_hook:
        phase_hook(phase)
    if phase == "finalizing":
        return _finalize(session_factory, operation_id, plan, deadline=deadline)
    with session_factory() as session:
        job = session.get(StorageDrainJob, operation_id)
        if job is not None and job.status == "succeeded":
            return dict(job.report_json)
    raise DrainExecutionError("drain_phase_invalid", "The drain job phase is invalid.")


def mark_drain_paused(session: Session, operation: Operation) -> None:
    job = session.get(StorageDrainJob, operation.id)
    if job is None:
        raise DrainExecutionError("drain_job_missing", "The durable drain job is unavailable.")
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
    append_event(session, operation, "paused", "Storage drain paused at a safe checkpoint")


def request_drain_pause(session: Session, operation: Operation) -> StorageDrainJob:
    job = session.get(StorageDrainJob, operation.id)
    if job is None or operation.kind != "storage.drain":
        raise DrainExecutionError("drain_job_missing", "The durable drain job is unavailable.")
    if operation.status == "paused":
        return job
    if operation.status not in {"queued", "running"}:
        raise DrainExecutionError("drain_not_running", "This drain cannot be paused now.")
    if operation.status == "queued":
        mark_drain_paused(session, operation)
        return job
    if not job.pause_requested:
        job.pause_requested = True
        job.updated_at = utc_now()
        append_event(
            session,
            operation,
            "pause_requested",
            "Storage drain will pause at the next safe checkpoint",
        )
    return job


def resume_drain(session: Session, operation: Operation) -> StorageDrainJob:
    job = session.get(StorageDrainJob, operation.id)
    if job is None or operation.kind != "storage.drain":
        raise DrainExecutionError("drain_job_missing", "The durable drain job is unavailable.")
    if operation.status not in {"paused", "failed", "needs_attention"}:
        raise DrainExecutionError("drain_not_paused", "This drain is not resumable now.")
    if job.status == "succeeded":
        raise DrainExecutionError("drain_completed", "This drain is already complete.")
    job.pause_requested = False
    job.status = "queued"
    if job.phase == "paused":
        recorded_phase = job.report_json.get("resume_phase")
        if recorded_phase in {"preflight", "inventory", "copying", "verifying", "finalizing"}:
            job.phase = str(recorded_phase)
        else:
            entries_remaining = session.scalar(
                select(StorageDrainEntry.id)
                .where(StorageDrainEntry.job_id == job.id, StorageDrainEntry.status == "pending")
                .limit(1)
            )
            unverified = session.scalar(
                select(StorageDrainEntry.id)
                .where(StorageDrainEntry.job_id == job.id, StorageDrainEntry.status == "copied")
                .limit(1)
            )
            verified = session.scalar(
                select(StorageDrainEntry.id)
                .where(StorageDrainEntry.job_id == job.id, StorageDrainEntry.status == "verified")
                .limit(1)
            )
            job.phase = (
                "copying"
                if entries_remaining is not None
                else "verifying"
                if unverified is not None
                else "finalizing"
                if verified is not None
                else "inventory"
            )
        job.report_json = {
            key: value for key, value in job.report_json.items() if key != "resume_phase"
        }
    job.updated_at = utc_now()
    operation.status = "queued"
    operation.cancel_requested = False
    operation.lease_owner = None
    operation.leased_at = None
    operation.heartbeat_at = None
    operation.updated_at = utc_now()
    append_event(session, operation, "resumed", "Storage drain queued to resume")
    return job
