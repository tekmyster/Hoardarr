from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

MERGERFS_TYPES = frozenset({"mergerfs", "fuse.mergerfs"})
_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")


def _unescape(value: str) -> str:
    return _OCTAL_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def _branches(source: str) -> list[str]:
    return [_unescape(item) for item in source.split(":") if item]


def _instance_id(mountpoint: str) -> str:
    digest = hashlib.sha256(mountpoint.encode()).hexdigest()[:16]
    return f"mergerfs:{digest}"


def _live_branches(mountpoint: str) -> tuple[list[str], dict[str, str]] | None:
    """Read mergerFS' authoritative dynamic branch list when the control file is available."""

    try:
        raw = os.getxattr(
            os.fspath(Path(mountpoint) / ".mergerfs"),
            "user.mergerfs.branches",
        )
    except (AttributeError, OSError):
        return None
    if len(raw) > 65_536:
        return None
    try:
        reported = _branches(raw.decode("utf-8", errors="strict"))
    except UnicodeError:
        return None
    branches: list[str] = []
    modes: dict[str, str] = {}
    for value in reported:
        match = re.fullmatch(r"(.+)=(RW|RO|NC)", value)
        branch = match.group(1) if match else value
        mode = match.group(2) if match else "not_reported"
        branches.append(branch)
        modes[branch] = mode
    if (
        not branches
        or len(branches) != len(set(branches))
        or any(not PurePosixPath(branch).is_absolute() for branch in branches)
    ):
        return None
    return branches, modes


def _mountinfo_instances(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    instances: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = raw_line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if separator + 3 >= len(fields) or fields[separator + 1] not in MERGERFS_TYPES:
            continue
        source = _unescape(fields[separator + 2])
        instances.append(
            {
                "mountpoint": _unescape(fields[4]),
                "source": source,
                "branches": _branches(source),
                "options": sorted(
                    set(fields[5].split(",")) | set(fields[separator + 3].split(","))
                ),
                "active": True,
                "configured": False,
            }
        )
    return instances


def _fstab_instances(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    instances: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # fstab uses octal escapes for whitespace. Shell parsing would consume
        # the backslash before the fstab decoder can interpret it.
        fields = stripped.split()
        if len(fields) < 4 or fields[2] not in MERGERFS_TYPES:
            continue
        source = _unescape(fields[0])
        instances.append(
            {
                "mountpoint": _unescape(fields[1]),
                "source": source,
                "branches": _branches(source),
                "configured_source": source,
                "configured_branches": _branches(source),
                "options": sorted(set(fields[3].split(","))),
                "active": False,
                "configured": True,
            }
        )
    return instances


def discover_mergerfs(
    *,
    mountinfo_path: Path = Path("/proc/self/mountinfo"),
    fstab_path: Path = Path("/etc/fstab"),
    executable: str | None = None,
) -> dict[str, Any]:
    by_mountpoint: dict[str, dict[str, Any]] = {}
    for candidate in [*_fstab_instances(fstab_path), *_mountinfo_instances(mountinfo_path)]:
        mountpoint = str(candidate["mountpoint"])
        current = by_mountpoint.get(mountpoint)
        if current is None:
            by_mountpoint[mountpoint] = candidate
            continue
        current["active"] = bool(current["active"] or candidate["active"])
        current["configured"] = bool(current["configured"] or candidate["configured"])
        if candidate["configured"]:
            current["configured_source"] = candidate.get("configured_source")
            current["configured_branches"] = candidate.get("configured_branches")
        if candidate["active"]:
            current["source"] = candidate["source"]
            current["branches"] = candidate["branches"]
        current["options"] = sorted(set(current["options"]) | set(candidate["options"]))

    items: list[dict[str, Any]] = []
    for mountpoint, item in sorted(by_mountpoint.items()):
        if item["active"]:
            live_result = _live_branches(mountpoint)
            if live_result is not None:
                live_branches, live_modes = live_result
                item["runtime_source"] = item["source"]
                item["runtime_branches"] = live_branches
                item["runtime_branch_modes"] = live_modes
                item["source"] = ":".join(live_branches)
                item["branches"] = live_branches
                item["branch_evidence"] = "mergerfs runtime control xattr"
        name = Path(mountpoint).name or mountpoint
        items.append({"id": _instance_id(mountpoint), "name": name, **item})
    available = bool(executable or shutil.which("mergerfs"))
    return {
        "available": available,
        "status": (
            "configured" if items else "available_not_configured" if available else "unavailable"
        ),
        "items": items,
    }
