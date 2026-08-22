from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from hoardarr.storage.layouts import CommandSpec


class AclError(ValueError):
    pass


_ACCOUNT = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_ROLES = {
    "administrator": "rwx",
    "media_application": "rwx",
    "media_user": "r-x",
    "anonymous": "---",
}


def normalize_acl(value: Mapping[str, Any]) -> dict[str, Any]:
    path_value = value.get("path")
    if not isinstance(path_value, str):
        raise AclError("path is required")
    path = PurePosixPath(path_value)
    roots = tuple(map(PurePosixPath, ("/mnt/hoardarr", "/srv/hoardarr", "/data")))
    if (
        not path.is_absolute()
        or ".." in path.parts
        or not any(root == path or root in path.parents for root in roots)
    ):
        raise AclError("path must be inside managed storage")
    entries_value = value.get("entries")
    if not isinstance(entries_value, list) or not entries_value:
        raise AclError("at least one ACL entry is required")
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in entries_value:
        if not isinstance(item, Mapping):
            raise AclError("ACL entries must be objects")
        kind = item.get("kind")
        name = item.get("name")
        role = item.get("role")
        if (
            kind not in {"user", "group"}
            or not isinstance(name, str)
            or not _ACCOUNT.fullmatch(name)
            or role not in _ROLES
        ):
            raise AclError("ACL entry is invalid")
        key = (str(kind), name)
        if key in seen:
            raise AclError("duplicate ACL entry")
        seen.add(key)
        entries.append(
            {"kind": str(kind), "name": name, "role": str(role), "posix": _ROLES[str(role)]}
        )
    return {
        "path": str(path),
        "entries": entries,
        "inherit": value.get("inherit", True) is True,
        "anonymous": "deny",
    }


def acl_commands(plan: Mapping[str, Any]) -> list[CommandSpec]:
    path = str(plan["path"])
    access: list[str] = ["o::---"]
    default: list[str] = ["d:o::---"]
    for item in plan["entries"]:
        prefix = "u" if item["kind"] == "user" else "g"
        spec = f"{prefix}:{item['name']}:{item['posix']}"
        access.append(spec)
        if plan.get("inherit"):
            default.append(f"d:{spec}")
    commands = [
        CommandSpec(
            ("setfacl", "--physical", "-m", ",".join(access), path),
            300,
            "Applying share permissions",
        )
    ]
    if plan.get("inherit"):
        commands.append(
            CommandSpec(
                ("setfacl", "--physical", "-m", ",".join(default), path),
                300,
                "Applying inherited permissions",
            )
        )
    commands.append(
        CommandSpec(
            ("getfacl", "--physical", "--absolute-names", path), 120, "Verifying share permissions"
        )
    )
    return commands


def assert_no_symlink(path: Path, managed_root: Path) -> None:
    root = managed_root.resolve(strict=True)
    current = path
    while current != root:
        if current.is_symlink():
            raise AclError("ACL target contains a symlink")
        current = current.parent
        if root not in (current, *current.parents):
            raise AclError("ACL target escaped managed storage")
