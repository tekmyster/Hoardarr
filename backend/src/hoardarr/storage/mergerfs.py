from __future__ import annotations

import hashlib
import re
import shlex
import shutil
from pathlib import Path
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
        try:
            fields = shlex.split(stripped, comments=True)
        except ValueError:
            continue
        if len(fields) < 4 or fields[2] not in MERGERFS_TYPES:
            continue
        source = _unescape(fields[0])
        instances.append(
            {
                "mountpoint": _unescape(fields[1]),
                "source": source,
                "branches": _branches(source),
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
        if candidate["active"]:
            current["source"] = candidate["source"]
            current["branches"] = candidate["branches"]
        current["options"] = sorted(set(current["options"]) | set(candidate["options"]))

    items: list[dict[str, Any]] = []
    for mountpoint, item in sorted(by_mountpoint.items()):
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
