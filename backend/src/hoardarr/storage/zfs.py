from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from hoardarr.storage.layouts import CommandSpec, LayoutError

_POOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,254}$")
_POOL_GUID = re.compile(r"^[1-9][0-9]{0,19}$")
_VDEV = re.compile(r"^(mirror|raidz1|raidz2|raidz3)-[0-9]+$")
_DEVICE = re.compile(
    r"^/dev/(?:disk/by-id/[A-Za-z0-9._:+-]+|mapper/[A-Za-z0-9._+-]+|[A-Za-z0-9._+-]+)$"
)
_NON_DATA_SECTIONS = frozenset({"logs", "cache", "spares", "special", "dedup"})
_MANAGED_SNAPSHOT = re.compile(r"hoardarr-(\d{8}T\d{6}Z)")
_RESERVED_POOL_NAMES = frozenset({"log", "mirror", "raidz", "spare"})


class ZfsSnapshotError(RuntimeError):
    pass


Runner = Callable[[Sequence[str], int], str]


def _validate_snapshot_options(pool: str, retention: int) -> None:
    if not _POOL_NAME.fullmatch(pool) or pool in _RESERVED_POOL_NAMES:
        raise ZfsSnapshotError("invalid ZFS pool name")
    if not 1 <= retention <= 4096:
        raise ZfsSnapshotError("snapshot retention must be from 1 through 4096")


def _run(argv: Sequence[str], timeout: int) -> str:
    try:
        result = subprocess.run(
            list(argv),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ZfsSnapshotError("ZFS snapshot command failed") from exc
    return result.stdout


def managed_snapshots(pool: str, output: str) -> list[str]:
    """Return only snapshots created by Hoardarr's snapshot task, oldest first."""

    prefix = f"{pool}@"
    snapshots: list[tuple[str, str]] = []
    for raw in output.splitlines():
        name = raw.strip()
        if not name.startswith(prefix):
            continue
        suffix = name.removeprefix(prefix)
        match = _MANAGED_SNAPSHOT.fullmatch(suffix)
        if match:
            snapshots.append((match.group(1), name))
    snapshots.sort()
    return [name for _timestamp, name in snapshots]


def create_and_prune_snapshots(
    *,
    pool: str,
    retention: int,
    now: datetime | None = None,
    runner: Runner = _run,
) -> list[str]:
    _validate_snapshot_options(pool, retention)
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    created = f"{pool}@hoardarr-{timestamp}"
    runner(("zfs", "snapshot", created), 300)
    output = runner(
        (
            "zfs",
            "list",
            "-H",
            "-t",
            "snapshot",
            "-o",
            "name",
            "-s",
            "creation",
            "-r",
            pool,
        ),
        300,
    )
    snapshots = managed_snapshots(pool, output)
    removed: list[str] = []
    for snapshot in snapshots[:-retention]:
        runner(("zfs", "destroy", snapshot), 3600)
        removed.append(snapshot)
    return removed


@dataclass(frozen=True)
class ZfsDataTopology:
    quality: str
    vdev_type: str | None
    vdev_width: int | None
    vdev_count: int
    data_vdevs: tuple[tuple[str, ...], ...]
    member_paths: tuple[str, ...]
    config_sha256: str | None
    errors: tuple[str, ...]

    def document(self) -> dict[str, object]:
        return {
            "quality": self.quality,
            "vdev_type": self.vdev_type,
            "vdev_width": self.vdev_width,
            "vdev_count": self.vdev_count,
            "data_vdevs": [
                {"type": self.vdev_type, "member_paths": list(members)}
                for members in self.data_vdevs
            ],
            "member_paths": list(self.member_paths),
            "config_sha256": self.config_sha256,
            "errors": list(self.errors),
        }


def parse_zpool_data_topology(output: str, pool_name: str) -> ZfsDataTopology:
    """Parse only the authoritative data-vdev tree from ``zpool status -P``.

    Cache, log, spare, special, and dedup classes are deliberately excluded. A
    candidate is available only when every top-level data vdev has one supported,
    uniform redundancy type and width. Stripe layouts remain visible in inventory
    but are not guessed into a safe expansion geometry.
    """

    if len(output) > 1024 * 1024:
        return ZfsDataTopology(
            "temporarily_unavailable",
            None,
            None,
            0,
            (),
            (),
            None,
            ("ZFS status output is too large.",),
        )
    if not _POOL_NAME.fullmatch(pool_name):
        return ZfsDataTopology(
            "temporarily_unavailable", None, None, 0, (), (), None, ("ZFS pool name is invalid.",)
        )
    rows: list[tuple[int, str]] = []
    in_config = False
    for raw_line in output.splitlines():
        stripped = raw_line.strip()
        if stripped == "config:":
            in_config = True
            continue
        if not in_config:
            continue
        if stripped.startswith("errors:"):
            break
        if not stripped or stripped.startswith("NAME "):
            continue
        token = stripped.split(maxsplit=1)[0]
        rows.append((len(raw_line) - len(raw_line.lstrip()), token))
    root_indexes = [index for index, (_, token) in enumerate(rows) if token == pool_name]
    if len(root_indexes) != 1:
        return ZfsDataTopology(
            "temporarily_unavailable",
            None,
            None,
            0,
            (),
            (),
            None,
            ("ZFS data-vdev root was not reported exactly once.",),
        )
    root_index = root_indexes[0]
    root_indent = rows[root_index][0]
    vdevs: list[tuple[str, tuple[str, ...]]] = []
    errors: list[str] = []
    index = root_index + 1
    while index < len(rows):
        indent, token = rows[index]
        if indent <= root_indent or token in _NON_DATA_SECTIONS:
            break
        match = _VDEV.fullmatch(token)
        if not match:
            if indent == root_indent + 2:
                errors.append(f"Unsupported top-level data vdev {token}.")
            index += 1
            continue
        member_paths: list[str] = []
        member_index = index + 1
        while member_index < len(rows):
            child_indent, child = rows[member_index]
            if child_indent <= indent:
                break
            if child.startswith("/dev/"):
                if not _DEVICE.fullmatch(child):
                    errors.append(f"Unsafe ZFS member path {child}.")
                else:
                    member_paths.append(child)
            member_index += 1
        if not member_paths:
            errors.append(f"No member paths were reported for {token}.")
        vdevs.append((match.group(1), tuple(member_paths)))
        index = member_index
    types = {kind for kind, _ in vdevs}
    widths = {len(members) for _, members in vdevs if members}
    if len(types) > 1:
        errors.append("Top-level data vdevs use mixed redundancy types.")
    if len(widths) > 1:
        errors.append("Top-level data vdevs use mixed widths.")
    canonical = [{"type": kind, "member_paths": list(members)} for kind, members in vdevs]
    digest = (
        hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode(
                "utf-8", errors="strict"
            )
        ).hexdigest()
        if vdevs and not errors
        else None
    )
    return ZfsDataTopology(
        "available" if vdevs and not errors else "unsupported",
        next(iter(types)) if len(types) == 1 else None,
        next(iter(widths)) if len(widths) == 1 else None,
        len(vdevs),
        tuple(members for _, members in vdevs),
        tuple(member for _, members in vdevs for member in members),
        digest,
        tuple(errors or (() if vdevs else ("No supported data vdev was reported.",))),
    )


def zfs_add_vdev_commands(
    *,
    pool_name: str,
    vdev_type: str,
    device_ids: Sequence[str],
    device_paths: Mapping[str, str],
) -> list[CommandSpec]:
    """Generate an argv-only, no-force ZFS top-level-vdev expansion sequence."""

    if not _POOL_NAME.fullmatch(pool_name):
        raise LayoutError("storage.expansion.target.instance_id", "contains an invalid pool name")
    minimums = {"mirror": 2, "raidz1": 3, "raidz2": 4, "raidz3": 5}
    if vdev_type not in minimums:
        raise LayoutError("storage.expansion.configuration.vdev_type", "is unsupported")
    if len(device_ids) < minimums[vdev_type] or len(device_ids) != len(set(device_ids)):
        raise LayoutError("storage.expansion.disk_ids", "does not form the reviewed ZFS vdev")
    resolved: list[str] = []
    for identity in device_ids:
        path = device_paths.get(identity)
        if not isinstance(path, str) or not _DEVICE.fullmatch(path):
            raise LayoutError(
                "device_paths", "every ZFS member must resolve to a stable device path"
            )
        resolved.append(path)
    geometry = [vdev_type, *resolved]
    return [
        CommandSpec(
            ("zpool", "add", "-n", pool_name, *geometry),
            120,
            "Validating ZFS expansion without changing the pool",
        ),
        CommandSpec(
            ("zpool", "add", pool_name, *geometry),
            3600,
            "Adding the reviewed ZFS vdev",
            False,
        ),
        CommandSpec(
            ("zpool", "status", "-P", pool_name),
            120,
            "Verifying the expanded ZFS pool",
            False,
        ),
    ]


def valid_pool_guid(value: object) -> bool:
    return isinstance(value, str) and _POOL_GUID.fullmatch(value) is not None


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and prune Hoardarr-managed ZFS snapshots")
    parser.add_argument("--pool", required=True)
    parser.add_argument("--retention", required=True, type=int)
    args = parser.parse_args()
    try:
        create_and_prune_snapshots(pool=args.pool, retention=args.retention)
    except ZfsSnapshotError as exc:
        parser.error(str(exc))
