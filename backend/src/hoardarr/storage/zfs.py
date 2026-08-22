from __future__ import annotations

import argparse
import re
import subprocess
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

_POOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,254}")
_MANAGED_SNAPSHOT = re.compile(r"hoardarr-(\d{8}T\d{6}Z)")


class ZfsSnapshotError(RuntimeError):
    pass


Runner = Callable[[Sequence[str], int], str]


def _validate(pool: str, retention: int) -> None:
    if not _POOL_NAME.fullmatch(pool) or pool in {"log", "mirror", "raidz", "spare"}:
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
    """Return only snapshots created by this task, oldest first."""
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
    _validate(pool, retention)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and prune Hoardarr-managed ZFS snapshots")
    parser.add_argument("--pool", required=True)
    parser.add_argument("--retention", required=True, type=int)
    args = parser.parse_args()
    try:
        create_and_prune_snapshots(pool=args.pool, retention=args.retention)
    except ZfsSnapshotError as exc:
        parser.error(str(exc))
