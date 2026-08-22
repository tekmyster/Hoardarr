from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import shutil
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


class TieringError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class TransferPlan:
    workload: str
    source: str
    destination: str
    source_identity: str
    destination_identity: str
    same_filesystem: bool
    method: str
    retain_until: str
    cleanup: bool
    required_bytes: int
    completed_steps: tuple[str, ...]
    sha256: str

    def document(self) -> dict[str, Any]:
        result = self.__dict__.copy()
        result["completed_steps"] = list(self.completed_steps)
        return result


def _managed_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise TieringError("path_invalid", f"{field} is invalid")
    path = PurePosixPath(value)
    roots = tuple(map(PurePosixPath, ("/mnt/hoardarr", "/srv/hoardarr", "/data")))
    if (
        not path.is_absolute()
        or ".." in path.parts
        or not any(root == path or root in path.parents for root in roots)
    ):
        raise TieringError("path_outside_storage", f"{field} is outside managed storage")
    return str(path)


def plan_transfer(value: Mapping[str, Any]) -> TransferPlan:
    allowed = {
        "workload",
        "source",
        "destination",
        "source_identity",
        "destination_identity",
        "same_filesystem",
        "method",
        "retain_until",
        "cleanup",
        "required_bytes",
        "completed_steps",
        "sha256",
    }
    if set(value) - allowed:
        raise TieringError("plan_invalid", "transfer plan contains unknown fields")
    workload = value.get("workload")
    if workload not in {"torrent", "usenet"}:
        raise TieringError("workload_invalid", "workload must be torrent or usenet")
    source = _managed_path(value.get("source"), "source")
    destination = _managed_path(value.get("destination"), "destination")
    if source == destination or PurePosixPath(source) in PurePosixPath(destination).parents:
        raise TieringError("path_overlap", "source and destination must not overlap")
    identities = []
    for field in ("source_identity", "destination_identity"):
        item = value.get(field)
        if not isinstance(item, str) or not item:
            raise TieringError("identity_missing", f"{field} is required")
        identities.append(item)
    same_filesystem = value.get("same_filesystem") is True
    requested = value.get("method", "auto")
    if requested not in {"auto", "copy", "move", "hardlink"}:
        raise TieringError("method_invalid", "method must be auto, copy, move, or hardlink")
    if requested == "hardlink" and not same_filesystem:
        raise TieringError("hardlink_cross_filesystem", "hardlinks cannot cross filesystems")
    if requested == "auto":
        method = (
            "hardlink"
            if workload == "torrent" and same_filesystem
            else "copy"
            if workload == "torrent"
            else "move"
        )
    else:
        method = requested
    retain_until = value.get(
        "retain_until", "seeding_complete" if workload == "torrent" else "import_complete"
    )
    if workload == "torrent" and retain_until not in {"seeding_complete", "manual", "never"}:
        raise TieringError("retention_invalid", "torrent retention is invalid")
    if workload == "usenet" and retain_until != "import_complete":
        raise TieringError("retention_invalid", "Usenet work is retained until import completes")
    required_bytes = value.get("required_bytes")
    if not isinstance(required_bytes, int) or required_bytes < 0:
        raise TieringError("size_invalid", "required_bytes must be a non-negative integer")
    steps_value = value.get("completed_steps", [])
    if not isinstance(steps_value, (list, tuple)) or not all(
        isinstance(item, str) for item in steps_value
    ):
        raise TieringError("steps_invalid", "completed_steps must be a list")
    completed_steps = tuple(steps_value)
    required_usenet_steps = ("download", "repair", "unpack", "verify")
    if workload == "usenet" and completed_steps != required_usenet_steps:
        raise TieringError(
            "usenet_not_ready",
            "Usenet import requires download, repair, unpack, and verification to be complete",
        )
    if workload == "torrent" and completed_steps not in {(), ("download_complete",)}:
        raise TieringError("steps_invalid", "torrent completed_steps is invalid")
    payload = "\0".join(
        (
            workload,
            source,
            destination,
            *identities,
            str(same_filesystem),
            method,
            retain_until,
            str(required_bytes),
            ",".join(completed_steps),
        )
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()
    supplied_digest = value.get("sha256")
    if supplied_digest is not None and supplied_digest != digest:
        raise TieringError("plan_changed", "transfer plan digest does not match")
    return TransferPlan(
        workload,
        source,
        destination,
        identities[0],
        identities[1],
        same_filesystem,
        method,
        retain_until,
        value.get("cleanup", True) is True,
        required_bytes,
        completed_steps,
        digest,
    )


def transfer_phases(plan: TransferPlan) -> list[str]:
    if plan.workload == "torrent":
        phases = ["verify_source", "copy_or_link", "verify_destination"]
        if plan.retain_until != "never":
            phases.append("retain_while_seeding")
        if plan.cleanup:
            phases.append("cleanup")
        return phases
    return ["verify_source", "copy_or_move", "verify_destination", "cleanup"]


def _assert_no_symlink(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise TieringError("path_symlink", "transfer paths cannot contain symbolic links")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fd_sha256(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    for chunk in iter(lambda: os.read(descriptor, 8 * 1024 * 1024), b""):
        digest.update(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def _fd_prefix_matches(source: int, target: int, length: int) -> bool:
    os.lseek(source, 0, os.SEEK_SET)
    os.lseek(target, 0, os.SEEK_SET)
    remaining = length
    while remaining:
        amount = min(8 * 1024 * 1024, remaining)
        source_chunk = os.read(source, amount)
        target_chunk = os.read(target, amount)
        if not source_chunk or source_chunk != target_chunk:
            return False
        remaining -= len(source_chunk)
    return True


def _open_directory_fd(path: Path) -> int:
    """Open an absolute directory without following any path component symlink."""

    if not path.is_absolute() or os.name != "posix":
        raise TieringError("path_invalid", "transfer path is not an absolute Linux path")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise TieringError("path_symlink", "transfer path changed or contains a link") from exc


def _execute_transfer_posix(
    plan: TransferPlan,
    *,
    identity_provider: Callable[[Path], str],
    free_space_provider: Callable[[Path], int],
) -> dict[str, Any]:
    source = Path(plan.source)
    destination = Path(plan.destination)
    if not source.name or not destination.name:
        raise TieringError("path_invalid", "source and destination must name files")
    _assert_no_symlink(source.parent)
    _assert_no_symlink(destination.parent)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if (
        identity_provider(source) != plan.source_identity
        or identity_provider(destination.parent) != plan.destination_identity
    ):
        raise TieringError("identity_drift", "source or destination storage identity changed")
    source_parent_fd = _open_directory_fd(source.parent)
    destination_parent_fd = _open_directory_fd(destination.parent)
    source_fd = -1
    target_fd = -1
    temporary_name = f".{destination.name}.hoardarr-{plan.sha256[:16]}.part"
    try:
        source_fd = os.open(
            source.name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=source_parent_fd,
        )
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise TieringError("source_invalid", "source is not a regular file")
        try:
            os.stat(destination.name, dir_fd=destination_parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise TieringError("destination_exists", "destination already exists")
        if plan.method == "hardlink":
            if source_stat.st_dev != os.fstat(destination_parent_fd).st_dev:
                raise TieringError(
                    "hardlink_cross_filesystem", "hardlinks cannot cross filesystems"
                )
            os.link(
                source.name,
                temporary_name,
                src_dir_fd=source_parent_fd,
                dst_dir_fd=destination_parent_fd,
                follow_symlinks=False,
            )
            temporary_stat = os.stat(
                temporary_name, dir_fd=destination_parent_fd, follow_symlinks=False
            )
        elif plan.method in {"copy", "move"}:
            try:
                target_fd = os.open(
                    temporary_name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=destination_parent_fd,
                )
            except FileExistsError:
                target_fd = os.open(
                    temporary_name,
                    os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=destination_parent_fd,
                )
                existing = os.fstat(target_fd)
                if (
                    not stat.S_ISREG(existing.st_mode)
                    or existing.st_size > source_stat.st_size
                    or not _fd_prefix_matches(source_fd, target_fd, existing.st_size)
                ):
                    raise TieringError(
                        "staging_changed", "existing transfer staging content changed"
                    ) from None
            existing_bytes = os.fstat(target_fd).st_size
            remaining = max(0, source_stat.st_size - existing_bytes)
            if free_space_provider(destination.parent) < remaining:
                raise TieringError("insufficient_space", "destination has insufficient free space")
            os.lseek(source_fd, existing_bytes, os.SEEK_SET)
            os.lseek(target_fd, existing_bytes, os.SEEK_SET)
            while chunk := os.read(source_fd, 8 * 1024 * 1024):
                view = memoryview(chunk)
                while view:
                    written = os.write(target_fd, view)
                    view = view[written:]
            os.fsync(target_fd)
            temporary_stat = os.fstat(target_fd)
            if temporary_stat.st_size != source_stat.st_size:
                raise TieringError("verification_failed", "copied size did not match source")
            if _fd_sha256(target_fd) != _fd_sha256(source_fd):
                raise TieringError("verification_failed", "copied content did not match source")
        else:
            raise TieringError("method_invalid", "transfer method is invalid")
        named_stat = os.stat(temporary_name, dir_fd=destination_parent_fd, follow_symlinks=False)
        if (named_stat.st_dev, named_stat.st_ino) != (temporary_stat.st_dev, temporary_stat.st_ino):
            raise TieringError("path_changed", "transfer staging identity changed")
        if (
            identity_provider(source) != plan.source_identity
            or identity_provider(destination.parent) != plan.destination_identity
        ):
            raise TieringError("identity_drift", "source or destination storage identity changed")
        current_source = os.stat(source.name, dir_fd=source_parent_fd, follow_symlinks=False)
        if (current_source.st_dev, current_source.st_ino) != (
            source_stat.st_dev,
            source_stat.st_ino,
        ):
            raise TieringError("path_changed", "source identity changed during transfer")
        # Publish the verified staging inode without replacing a path that may
        # have appeared during a long copy. link(2) fails atomically when the
        # destination already exists; the staging link is removed in finally.
        os.link(
            temporary_name,
            destination.name,
            src_dir_fd=destination_parent_fd,
            dst_dir_fd=destination_parent_fd,
            follow_symlinks=False,
        )
        published = os.stat(destination.name, dir_fd=destination_parent_fd, follow_symlinks=False)
        if (published.st_dev, published.st_ino) != (
            temporary_stat.st_dev,
            temporary_stat.st_ino,
        ):
            raise TieringError("path_changed", "destination identity changed during transfer")
        if plan.method == "move" or (
            plan.workload == "torrent" and plan.cleanup and plan.retain_until == "never"
        ):
            os.unlink(source.name, dir_fd=source_parent_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise TieringError(
                "path_symlink", "transfer paths cannot contain symbolic links"
            ) from exc
        raise TieringError("transfer_io_failed", "transfer IO failed safely") from exc
    finally:
        if target_fd >= 0:
            os.close(target_fd)
        if source_fd >= 0:
            os.close(source_fd)
        with contextlib.suppress(FileNotFoundError, OSError):
            os.unlink(temporary_name, dir_fd=destination_parent_fd)
        os.close(destination_parent_fd)
        os.close(source_parent_fd)
    return _transfer_result(plan, source, destination)


def _transfer_result(plan: TransferPlan, source: Path, destination: Path) -> dict[str, Any]:
    return {
        "state": "retained"
        if plan.workload == "torrent" and plan.retain_until != "never"
        else "completed",
        "destination": str(destination),
        "source": str(source),
        "method": plan.method,
        "retain_until": plan.retain_until,
        "cleanup": plan.cleanup,
        "completed_prerequisites": list(plan.completed_steps),
        "phases": transfer_phases(plan),
    }


def execute_transfer(
    plan: TransferPlan,
    *,
    identity_provider: Callable[[Path], str],
    free_space_provider: Callable[[Path], int] = lambda path: shutil.disk_usage(path).free,
) -> dict[str, Any]:
    if os.name == "posix":
        return _execute_transfer_posix(
            plan,
            identity_provider=identity_provider,
            free_space_provider=free_space_provider,
        )
    source = Path(plan.source)
    destination = Path(plan.destination)
    _assert_no_symlink(source)
    _assert_no_symlink(destination.parent)
    if not source.exists():
        raise TieringError("source_missing", "source no longer exists")
    if (
        identity_provider(source) != plan.source_identity
        or identity_provider(destination.parent) != plan.destination_identity
    ):
        raise TieringError("identity_drift", "source or destination storage identity changed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise TieringError("destination_exists", "destination already exists")
    temporary = destination.with_name(f".{destination.name}.hoardarr-{plan.sha256[:16]}.part")
    if temporary.is_symlink():
        raise TieringError("path_symlink", "the transfer staging path is a symbolic link")
    existing_bytes = (
        temporary.stat().st_size if temporary.is_file() and not temporary.is_symlink() else 0
    )
    if temporary.exists() and existing_bytes > source.stat().st_size:
        temporary.unlink()
        existing_bytes = 0
    remaining = max(0, plan.required_bytes - existing_bytes)
    if plan.method != "hardlink" and free_space_provider(destination.parent) < remaining:
        raise TieringError("insufficient_space", "destination has insufficient free space")
    if plan.method == "hardlink":
        if source.stat().st_dev != destination.parent.stat().st_dev:
            raise TieringError("hardlink_cross_filesystem", "hardlinks cannot cross filesystems")
        os.link(source, temporary)
    elif plan.method in {"copy", "move"}:
        mode = "ab" if existing_bytes else "xb"
        with source.open("rb") as source_handle, temporary.open(mode) as target_handle:
            source_handle.seek(existing_bytes)
            shutil.copyfileobj(source_handle, target_handle, length=8 * 1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        if temporary.stat().st_size != source.stat().st_size or _file_sha256(
            temporary
        ) != _file_sha256(source):
            temporary.unlink(missing_ok=True)
            raise TieringError("verification_failed", "copied size did not match source")
    else:
        raise TieringError("method_invalid", "transfer method is invalid")
    if (
        identity_provider(source) != plan.source_identity
        or identity_provider(destination.parent) != plan.destination_identity
    ):
        raise TieringError("identity_drift", "source or destination storage identity changed")
    os.replace(temporary, destination)
    if plan.method == "move" or (
        plan.workload == "torrent" and plan.cleanup and plan.retain_until == "never"
    ):
        source.unlink()
    return _transfer_result(plan, source, destination)


def cleanup_retained_transfer(
    plan: TransferPlan, *, identity_provider: Callable[[Path], str]
) -> dict[str, Any]:
    if plan.workload != "torrent" or plan.retain_until == "never":
        raise TieringError("cleanup_not_applicable", "this transfer has no retained torrent source")
    source = Path(plan.source)
    destination = Path(plan.destination)
    if os.name == "posix":
        source_parent_fd = _open_directory_fd(source.parent)
        destination_parent_fd = _open_directory_fd(destination.parent)
        try:
            try:
                destination_stat = os.stat(
                    destination.name, dir_fd=destination_parent_fd, follow_symlinks=False
                )
            except FileNotFoundError as exc:
                raise TieringError(
                    "destination_missing", "the imported destination is missing"
                ) from exc
            if not stat.S_ISREG(destination_stat.st_mode):
                raise TieringError("destination_missing", "the imported destination is missing")
            try:
                source_stat = os.stat(source.name, dir_fd=source_parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                source_removed = True
            else:
                if not stat.S_ISREG(source_stat.st_mode):
                    raise TieringError("path_changed", "retained source identity changed")
                if identity_provider(source) != plan.source_identity:
                    raise TieringError("identity_drift", "source storage identity changed")
                current = os.stat(source.name, dir_fd=source_parent_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (source_stat.st_dev, source_stat.st_ino):
                    raise TieringError("path_changed", "retained source identity changed")
                os.unlink(source.name, dir_fd=source_parent_fd)
                source_removed = True
        finally:
            os.close(destination_parent_fd)
            os.close(source_parent_fd)
        return {
            "state": "completed",
            "source_removed": source_removed,
            "destination": str(destination),
        }
    _assert_no_symlink(source)
    _assert_no_symlink(destination)
    if not destination.is_file():
        raise TieringError("destination_missing", "the imported destination is missing")
    if source.exists():
        if identity_provider(source) != plan.source_identity:
            raise TieringError("identity_drift", "source storage identity changed")
        source.unlink()
    return {
        "state": "completed",
        "source_removed": not source.exists(),
        "destination": str(destination),
    }
