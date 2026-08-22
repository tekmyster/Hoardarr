from __future__ import annotations

import hashlib
import os
import shutil
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


def execute_transfer(
    plan: TransferPlan,
    *,
    identity_provider: Callable[[Path], str],
    free_space_provider: Callable[[Path], int] = lambda path: shutil.disk_usage(path).free,
) -> dict[str, Any]:
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


def cleanup_retained_transfer(
    plan: TransferPlan, *, identity_provider: Callable[[Path], str]
) -> dict[str, Any]:
    if plan.workload != "torrent" or plan.retain_until == "never":
        raise TieringError("cleanup_not_applicable", "this transfer has no retained torrent source")
    source = Path(plan.source)
    destination = Path(plan.destination)
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
