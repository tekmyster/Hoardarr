from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

try:  # pragma: no cover - unavailable only on the Windows test host.
    import pwd
except ImportError:  # pragma: no cover
    pwd = None  # type: ignore[assignment]

from hoardarr.operations.service import document_hash
from hoardarr.storage.capacity_plans import (
    CapacityPlanError,
    capacity_command,
    validate_capacity_plan,
)
from hoardarr.storage.foreign import (
    ForeignStorageError,
    validate_inspection_plan,
    validate_stack_preview_plan,
)
from hoardarr.storage.layouts import (
    LayoutError,
    layout_commands,
    mergerfs_expand_commands,
    sector_conversion_commands,
    snapraid_config,
    snapraid_expand_config,
    wipe_commands,
)
from hoardarr.storage.maintenance import (
    MaintenanceError,
)
from hoardarr.storage.maintenance import (
    validate_plan as validate_maintenance_plan,
)
from hoardarr.storage.mergerfs import MERGERFS_TYPES, discover_mergerfs
from hoardarr.storage.quarantine import (
    MANAGED_STORAGE_STATE,
    MANAGED_UDEV_RULE,
    QuarantineError,
    atomic_json,
    atomic_text,
    managed_identity_from_device,
    persist_managed_identities,
    validate_quarantine,
)
from hoardarr.storage.redundancy import (
    RedundancyError,
    logical_storage_identity,
    matching_devices,
    stable_path_identity,
    validate_redundancy_plan,
)
from hoardarr.storage.replacement import (
    ArrayReplacementError,
    configuration_hash,
    validate_array_replacement_plan,
)
from hoardarr.storage.snapraid import (
    SnapraidReplacementError,
    existing_data_summary,
    recovery_commands,
    replace_data_entry,
    validate_replacement_plan,
)
from hoardarr.storage.snapshot_plans import (
    SnapshotPlanError,
    snapshot_command,
    validate_snapshot_plan,
)
from hoardarr.storage.volume_plans import (
    VolumePlanError,
    validate_guided_volume_plan,
    volume_create_command,
)
from hoardarr.storage.zfs import (
    parse_zpool_data_topology,
    valid_pool_guid,
    zfs_add_vdev_commands,
)

try:  # pragma: no cover - Windows exists only for repository tests.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

MAXIMUM_REQUEST_BYTES = 4 * 1024 * 1024
MAXIMUM_RESPONSE_BYTES = 1024 * 1024
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
OPERATION_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SERVICE_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
SMB_SHARE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,63}$")
SAFE_FILESYSTEMS = frozenset({"ext4", "xfs", "btrfs", "ntfs", "exfat"})
SAFE_TABLES = frozenset({"gpt", "mbr"})
SUPPORTED_TOPOLOGIES = frozenset(
    {
        "individual",
        "mergerfs",
        "zfs",
        "raid",
        "snapraid",
        "mixed",
        "cache",
        "block",
        "import",
        "test",
    }
)
ACTION_TYPES = frozenset(
    {
        "drive.identity.verify",
        "drive.surface.read",
        "drive.smart.short",
        "drive.smart.extended",
        "drive.write_read.destructive",
        "disk.partition_table.create",
        "filesystem.create",
        "storage.layout.ensure",
    }
)
ACTION_FIELDS = {
    "drive.identity.verify": frozenset({"action_id", "type", "device_id", "destructive"}),
    "drive.surface.read": frozenset({"action_id", "type", "device_id", "destructive"}),
    "drive.smart.short": frozenset({"action_id", "type", "device_id", "destructive"}),
    "drive.smart.extended": frozenset({"action_id", "type", "device_id", "destructive"}),
    "drive.write_read.destructive": frozenset({"action_id", "type", "device_id", "destructive"}),
    "disk.partition_table.create": frozenset(
        {"action_id", "type", "device_id", "table", "alignment_bytes", "destructive"}
    ),
    "filesystem.create": frozenset(
        {
            "action_id",
            "type",
            "device_id",
            "filesystem",
            "allocation_unit_bytes",
            "format_mode",
            "destructive",
        }
    ),
    "storage.layout.ensure": frozenset(
        {
            "action_id",
            "type",
            "topology",
            "device_ids",
            "purpose",
            "mergerfs",
            "requires_live_instance_revalidation",
            "layout_options",
            "destructive",
        }
    ),
}
ACTION_ESTIMATED_SECONDS = {
    "drive.identity.verify": 2,
    "drive.smart.short": 300,
    "drive.smart.extended": 14_400,
    "drive.write_read.destructive": 86_400,
    "disk.partition_table.create": 15,
    "filesystem.create": 45,
    "storage.layout.ensure": 15,
}
INITIAL_SURFACE_READ_BYTES_PER_SECOND = 100 * 1024 * 1024
IDENTITY_FIELDS = (
    "id",
    "stable_identity",
    "vendor",
    "model",
    "serial",
    "wwn",
    "eui64",
    "nguid",
    "capacity_bytes",
    "logical_sector_bytes",
    "physical_sector_bytes",
)


class ExecutorFailure(RuntimeError):
    def __init__(self, code: str, message: str, *, needs_attention: bool = False) -> None:
        self.code = code
        self.needs_attention = needs_attention
        super().__init__(message)


@dataclass(frozen=True)
class Paths:
    detector: Path = Path("/usr/lib/hoardarr/scripts/detect-hardware.py")
    quarantine_marker: Path = Path("/var/lib/hoardarr/storage-executor/quarantine.json")
    transaction_root: Path = Path("/var/lib/hoardarr/storage-executor/transactions")
    lock_root: Path = Path("/run/hoardarr/storage-locks")
    fstab: Path = Path("/etc/fstab")
    mount_root: Path = Path("/mnt/hoardarr/disks")
    inspection_root: Path = Path("/mnt/hoardarr/imports")
    sys_class_block: Path = Path("/sys/class/block")
    proc_swaps: Path = Path("/proc/swaps")
    samba_config: Path = Path("/etc/samba/smb.conf")
    samba_include: Path = Path("/etc/samba/hoardarr-shares.conf")
    dev_by_id: Path = Path("/dev/disk/by-id")
    snapraid_config_root: Path = Path("/etc/snapraid")
    systemd_unit_root: Path = Path("/etc/systemd/system")
    multipath_config_root: Path = Path("/etc/multipath/conf.d")
    managed_udev_rule: Path | None = None
    managed_storage_state: Path | None = None


CommandRunner = Callable[[list[str], int], None]
CommandProbe = Callable[[list[str], int], str]
InventoryProvider = Callable[[], dict[str, Any]]
InspectionInventoryProvider = Callable[[Path, Mapping[str, int]], dict[str, Any]]
ZfsStateProvider = Callable[[str], dict[str, Any]]
ZfsResourceProvider = Callable[[str], dict[str, Any]]
ZfsSnapshotProvider = Callable[[str], dict[str, Any] | None]
LOGGER = logging.getLogger(__name__)


def _executor_uid() -> int:
    """Return the identity that must own private executor state.

    The production storage services run as root, so this remains UID 0 there.
    Using the effective process identity also permits the same fail-closed checks
    to be exercised by an unprivileged test process without weakening modes.
    """

    return os.geteuid() if hasattr(os, "geteuid") else 0


def _tool(name: str) -> str:
    path = shutil.which(name, path="/usr/sbin:/usr/bin:/sbin:/bin")
    if path is None:
        raise ExecutorFailure(
            "storage_tool_missing", f"A required storage tool is unavailable: {name}."
        )
    return path


def _run(command: list[str], timeout_seconds: int) -> None:
    if not command or any(not isinstance(part, str) or "\0" in part for part in command):
        raise ExecutorFailure("executor_command_invalid", "A typed storage command was invalid.")
    try:
        subprocess.run(
            command,
            check=True,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
    except subprocess.TimeoutExpired as exc:
        raise ExecutorFailure(
            "storage_tool_timeout",
            "A storage operation exceeded its time limit and requires inspection.",
            needs_attention=True,
        ) from exc
    except subprocess.CalledProcessError as exc:
        LOGGER.warning(
            "Storage command failed tool=%s exit_code=%s",
            Path(command[0]).name,
            exc.returncode,
        )
        raise ExecutorFailure(
            "storage_tool_failed",
            "A storage tool reported a failure. The operation stopped and requires inspection.",
            needs_attention=True,
        ) from exc


def _capture(command: list[str], timeout_seconds: int) -> str:
    if not command or any(not isinstance(part, str) or "\0" in part for part in command):
        raise ExecutorFailure("executor_command_invalid", "A typed storage command was invalid.")
    try:
        result = subprocess.run(
            command,
            check=True,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise ExecutorFailure(
            "sanitize_status_unavailable",
            "The drive sanitize status could not be verified.",
            needs_attention=True,
        ) from exc
    if len(result.stdout) > MAXIMUM_RESPONSE_BYTES:
        raise ExecutorFailure(
            "sanitize_status_invalid", "The drive returned an oversized sanitize status."
        )
    return result.stdout


def _capture_read_only(command: list[str], timeout_seconds: int) -> str:
    if not command or any(not isinstance(part, str) or "\0" in part for part in command):
        raise ExecutorFailure("executor_command_invalid", "A typed storage command was invalid.")
    try:
        result = subprocess.run(
            command,
            check=True,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise ExecutorFailure(
            "foreign_probe_failed", "The source signature could not be revalidated read-only."
        ) from exc
    if len(result.stdout) > MAXIMUM_RESPONSE_BYTES:
        raise ExecutorFailure("foreign_probe_invalid", "The source signature report was oversized.")
    return result.stdout


def _nvme_sanitize_status(output: str) -> str:
    try:
        document = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ExecutorFailure(
            "sanitize_status_invalid", "The NVMe sanitize status was malformed."
        ) from exc
    if not isinstance(document, dict):
        raise ExecutorFailure("sanitize_status_invalid", "The NVMe sanitize status was malformed.")
    raw = document.get("sanitize_status", document.get("sstat"))
    if isinstance(raw, str):
        lowered = raw.casefold()
        if "progress" in lowered:
            return "in_progress"
        if "success" in lowered or "complete" in lowered:
            return "succeeded"
        if "fail" in lowered:
            return "failed"
        try:
            raw = int(raw, 0)
        except ValueError as exc:
            raise ExecutorFailure(
                "sanitize_status_invalid", "The NVMe sanitize status was not recognized."
            ) from exc
    if not isinstance(raw, int):
        raise ExecutorFailure(
            "sanitize_status_invalid", "The NVMe sanitize status was not reported."
        )
    return {1: "succeeded", 2: "in_progress", 3: "failed", 4: "succeeded"}.get(raw & 0x7, "unknown")


def _wait_for_nvme_sanitize(
    stable_path: str, *, probe: CommandProbe, timeout_seconds: int = 604800
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    observations = 0
    while True:
        output = probe([_tool("nvme"), "sanitize-log", stable_path, "--output-format=json"], 30)
        observations += 1
        status = _nvme_sanitize_status(output)
        if status == "succeeded":
            return {"status": status, "observations": observations, "source": "nvme sanitize-log"}
        if status == "failed":
            raise ExecutorFailure(
                "sanitize_failed",
                "The NVMe controller reported that sanitization failed.",
                needs_attention=True,
            )
        if status != "in_progress":
            raise ExecutorFailure(
                "sanitize_status_invalid", "The NVMe controller did not report a usable status."
            )
        if time.monotonic() >= deadline:
            raise ExecutorFailure(
                "sanitize_timeout",
                "The NVMe sanitize operation did not complete within the bounded wait period.",
                needs_attention=True,
            )
        time.sleep(5)


def _live_zfs_pool_state(pool_name: str) -> dict[str, Any]:
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,254}", pool_name) is None:
        raise ExecutorFailure("zfs_pool_identity_invalid", "The reviewed ZFS pool name is invalid.")

    def capture(arguments: list[str]) -> str:
        try:
            result = subprocess.run(
                [_tool("zpool"), *arguments],
                check=True,
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=120,
                env={
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                },
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ExecutorFailure(
                "zfs_pool_identity_unavailable",
                "The existing ZFS pool identity and topology could not be read safely.",
            ) from exc
        if len(result.stdout) > 1024 * 1024:
            raise ExecutorFailure(
                "zfs_pool_identity_unavailable", "The ZFS pool status output is unexpectedly large."
            )
        return result.stdout

    guid = capture(["get", "-Hp", "-o", "value", "guid", pool_name]).strip()
    topology = parse_zpool_data_topology(capture(["status", "-P", pool_name]), pool_name)
    if not valid_pool_guid(guid) or topology.quality != "available":
        raise ExecutorFailure(
            "zfs_pool_identity_unavailable",
            "The existing ZFS pool did not report a safe, uniform data-vdev identity.",
        )
    return {"pool_guid": guid, **topology.document()}


def _live_md_array_state(array_name: str) -> dict[str, Any]:
    if re.fullmatch(r"md[0-9]+", array_name) is None:
        raise ExecutorFailure("md_array_identity_invalid", "The reviewed MD array name is invalid.")
    array_path = Path("/dev") / array_name
    metadata = Path("/sys/class/block") / array_name / "md"
    try:
        completed = subprocess.run(
            [_tool("mdadm"), "--detail", "--export", str(array_path)],
            check=True,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
        if len(completed.stdout) > 1024 * 1024:
            raise OSError("oversized mdadm output")
        detail = dict(
            line.split("=", 1)
            for line in completed.stdout.splitlines()
            if line.startswith("MD_") and "=" in line
        )
        level = (metadata / "level").read_text(encoding="utf-8").strip()
        raid_disks = int((metadata / "raid_disks").read_text(encoding="utf-8").strip())
        degraded = int((metadata / "degraded").read_text(encoding="utf-8").strip())
        sync_action = (metadata / "sync_action").read_text(encoding="utf-8").strip()
        member_paths = sorted(
            f"/dev/{item.name}"
            for item in (Path("/sys/class/block") / array_name / "slaves").iterdir()
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ExecutorFailure(
            "md_array_identity_unavailable",
            "The existing Linux MD identity and recovery state could not be read safely.",
        ) from exc
    array_uuid = detail.get("MD_UUID")
    if not isinstance(array_uuid, str) or not array_uuid:
        raise ExecutorFailure("md_array_identity_unavailable", "The MD UUID was not reported.")
    configuration = {
        "array_path": str(array_path),
        "array_uuid": array_uuid,
        "level": level,
        "raid_disks": raid_disks,
        "member_paths": member_paths,
    }
    return {
        **configuration,
        "config_sha256": configuration_hash(configuration),
        "degraded": bool(degraded),
        "sync_action": sync_action,
    }


def _live_zfs_resource_state(resource_name: str) -> dict[str, Any]:
    if (
        re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,254}/[A-Za-z0-9_.:/-]{1,510}", resource_name)
        is None
    ):
        raise ExecutorFailure(
            "zfs_resource_identity_invalid", "The reviewed ZFS resource name is invalid."
        )
    try:
        result = subprocess.run(
            [
                _tool("zfs"),
                "get",
                "-Hp",
                "-o",
                "property,value",
                "guid,type,mountpoint,volsize,quota,reservation,refreservation,used,available",
                resource_name,
            ],
            check=True,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExecutorFailure(
            "zfs_resource_verification_failed",
            "The created ZFS resource could not be read back safely.",
            needs_attention=True,
        ) from exc
    if len(result.stdout) > 64 * 1024:
        raise ExecutorFailure(
            "zfs_resource_verification_failed",
            "The ZFS resource response is unexpectedly large.",
            needs_attention=True,
        )
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[0] in {
            "guid",
            "type",
            "mountpoint",
            "volsize",
            "quota",
            "reservation",
            "refreservation",
            "used",
            "available",
        }:
            values[parts[0]] = parts[1]
    if not valid_pool_guid(values.get("guid")) or values.get("type") not in {
        "filesystem",
        "volume",
    }:
        raise ExecutorFailure(
            "zfs_resource_verification_failed",
            "The created ZFS resource identity is incomplete.",
            needs_attention=True,
        )
    return values


def _live_zfs_snapshot_state(snapshot_name: str) -> dict[str, Any] | None:
    if (
        re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_.:-]{0,254}/[A-Za-z0-9_.:/-]{1,510}@[a-z0-9][a-z0-9_.:-]{0,95}",
            snapshot_name,
        )
        is None
    ):
        raise ExecutorFailure(
            "snapshot_identity_invalid", "The reviewed ZFS snapshot identity is invalid."
        )
    try:
        result = subprocess.run(
            [
                _tool("zfs"),
                "get",
                "-Hp",
                "-o",
                "property,value",
                "guid,creation,used,referenced",
                snapshot_name,
            ],
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExecutorFailure(
            "snapshot_capability_unavailable",
            "The ZFS snapshot provider could not be queried safely.",
        ) from exc
    if result.returncode != 0:
        return None
    if len(result.stdout) > 64 * 1024:
        raise ExecutorFailure(
            "snapshot_provider_output_invalid", "The ZFS snapshot response is unexpectedly large."
        )
    values: dict[str, Any] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[0] in {"guid", "creation", "used", "referenced"}:
            values[parts[0]] = parts[1]
    if not valid_pool_guid(values.get("guid")):
        raise ExecutorFailure(
            "snapshot_provider_output_invalid", "The ZFS snapshot identity was incomplete."
        )
    return values


def _smartctl(command: list[str], *, allow_unsupported_log: bool = False) -> str:
    try:
        result = subprocess.run(
            command,
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
    except subprocess.TimeoutExpired as exc:
        raise ExecutorFailure("smart_test_timeout", "A SMART self-test command timed out.") from exc
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    folded = output.casefold()
    # Some SAT/SCSI bridges reject the IEC mode page but explicitly support a
    # permissive retry. This remains a read/test-only smartctl invocation.
    if (
        result.returncode & 0b00000111
        and "-T" not in command
        and (
            "bad response to iec mode page" in folded
            or "add one or more '-t permissive' options" in folded
        )
    ):
        return _smartctl(
            [command[0], "-T", "permissive", *command[1:]],
            allow_unsupported_log=allow_unsupported_log,
        )
    unsupported_log = any(
        marker in folded
        for marker in (
            "device does not support self test logging",
            "device does not support self-test logging",
            "self-test log not supported",
            "self test logging is not supported",
        )
    )
    if allow_unsupported_log and unsupported_log:
        return output
    # smartctl uses high bits to report existing media/health findings. Bits
    # 0-2 mean the command itself could not be parsed, opened, or completed.
    if result.returncode & 0b00000111:
        raise ExecutorFailure("smart_test_failed", "The drive could not run a SMART self-test.")
    if len(result.stdout) > 1024 * 1024:
        raise ExecutorFailure("smart_result_invalid", "The SMART self-test result was invalid.")
    return output


def _smart_test_minutes(output: str) -> int | None:
    matches = [
        int(value)
        for value in re.findall(
            r"(?:please wait|recommended polling time:)\s*(?:\(\s*)?(\d+)\s*(?:\))?\s*minutes?",
            output,
            flags=re.IGNORECASE,
        )
    ]
    return max(matches) if matches else None


def _smart_test_remaining_percent(output: str) -> int | None:
    match = re.search(r"(\d{1,3})%\s+of\s+test\s+remaining", output, flags=re.IGNORECASE)
    if match is None:
        return None
    return min(max(int(match.group(1)), 0), 100)


def _run_smart_test(
    device: Path,
    kind: str,
    *,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    existing_log = _smartctl(
        [_tool("smartctl"), "-l", "selftest", os.fspath(device)],
        allow_unsupported_log=True,
    ).casefold()
    if any(
        marker in existing_log
        for marker in (
            "device does not support self test logging",
            "device does not support self-test logging",
            "self-test log not supported",
            "self test logging is not supported",
        )
    ):
        return {
            "outcome": "skipped",
            "code": "smart_self_test_unavailable",
            "message": (
                "This connection does not expose the drive's SMART self-test log, so "
                "Hoardarr skipped the SMART self-test and will use the other selected "
                "intake tests. Connect the drive directly to a SMART-capable controller "
                "to run and verify this test."
            ),
        }
    maximum_seconds = 3600 if kind == "short" else 13 * 24 * 3600
    started_at = time.time()
    start_output = _smartctl([_tool("smartctl"), "-t", kind, os.fspath(device)])
    expected_minutes = _smart_test_minutes(start_output)
    expected_seconds = expected_minutes * 60 if expected_minutes is not None else None
    if progress_callback is not None:
        progress_callback(
            {
                "kind": "smart_self_test",
                "device": os.fspath(device),
                "test_kind": "extended" if kind == "long" else kind,
                "state": "running",
                "percent": 0.0,
                "elapsed_seconds": 0,
                "estimated_seconds_remaining": expected_seconds,
                "expected_finish_at": started_at + expected_seconds
                if expected_seconds is not None
                else None,
            }
        )
    deadline = time.monotonic() + maximum_seconds
    time.sleep(5)
    while True:
        capabilities_output = _smartctl([_tool("smartctl"), "-c", os.fspath(device)])
        capabilities = capabilities_output.casefold()
        if "in progress" not in capabilities and "in_progress" not in capabilities:
            break
        remaining_percent = _smart_test_remaining_percent(capabilities_output)
        elapsed = max(0, int(time.time() - started_at))
        if remaining_percent is not None:
            percent = float(100 - remaining_percent)
            remaining_seconds = (
                round(elapsed * remaining_percent / max(percent, 1.0))
                if elapsed
                else expected_seconds
            )
        else:
            percent = (
                min(
                    99.0,
                    elapsed * 100 / expected_seconds,
                )
                if expected_seconds
                else 0.0
            )
            remaining_seconds = (
                max(expected_seconds - elapsed, 0) if expected_seconds is not None else None
            )
        if progress_callback is not None:
            progress_callback(
                {
                    "kind": "smart_self_test",
                    "device": os.fspath(device),
                    "test_kind": "extended" if kind == "long" else kind,
                    "state": "running",
                    "percent": round(percent, 1),
                    "elapsed_seconds": elapsed,
                    "estimated_seconds_remaining": remaining_seconds,
                    "expected_finish_at": time.time() + remaining_seconds
                    if remaining_seconds is not None
                    else None,
                }
            )
        if time.monotonic() >= deadline:
            raise ExecutorFailure(
                "smart_test_timeout", "The SMART self-test did not finish in its allowed time."
            )
        time.sleep(30)
    log = _smartctl([_tool("smartctl"), "-l", "selftest", os.fspath(device)]).casefold()
    successful_markers = (
        "completed without error",
        "completed_without_error",
        "self-test completed without error",
        "passed",
    )
    if not any(marker in log for marker in successful_markers):
        raise ExecutorFailure(
            "smart_test_result_failed",
            "The completed SMART self-test did not report a passing result.",
        )
    result = {
        "outcome": "passed",
        "code": "smart_self_test_passed",
        "message": "The SMART self-test completed without a reported error.",
        "test_kind": "extended" if kind == "long" else kind,
        "started_at": started_at,
        "finished_at": time.time(),
    }
    if progress_callback is not None:
        progress_callback(
            {
                "kind": "smart_self_test",
                "device": os.fspath(device),
                "test_kind": result["test_kind"],
                "state": "passed",
                "percent": 100.0,
                "elapsed_seconds": max(0, int(result["finished_at"] - started_at)),
                "estimated_seconds_remaining": 0,
                "expected_finish_at": result["finished_at"],
            }
        )
    return result


def _live_inventory(paths: Paths) -> dict[str, Any]:
    if not paths.detector.is_file():
        raise ExecutorFailure(
            "hardware_detector_unavailable", "Hardware identity cannot be revalidated."
        )
    try:
        result = subprocess.run(
            [
                sys.executable,
                os.fspath(paths.detector),
                "--format",
                "json",
                "--probe-block-signatures",
            ],
            check=False,
            shell=False,
            capture_output=True,
            timeout=60,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
    except subprocess.TimeoutExpired as exc:
        raise ExecutorFailure(
            "identity_scan_timeout", "Drive identity revalidation timed out."
        ) from exc
    if result.returncode != 0 or len(result.stdout) > 16 * 1024 * 1024:
        raise ExecutorFailure("identity_scan_failed", "Drive identity revalidation failed.")
    try:
        payload = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExecutorFailure(
            "identity_scan_invalid", "Drive identity revalidation was invalid."
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("disks"), list):
        raise ExecutorFailure("identity_scan_invalid", "Drive identity revalidation was invalid.")
    source = payload.get("source")
    if not isinstance(source, dict) or source.get("kind") == "fixture":
        raise ExecutorFailure(
            "identity_scan_invalid", "Live storage execution cannot use fixture data."
        )
    return payload


def _device_map(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    disks = payload.get("disks")
    if not isinstance(disks, list):
        raise ExecutorFailure("identity_scan_invalid", "Drive identity revalidation was invalid.")
    result: dict[str, Mapping[str, Any]] = {}
    for disk in disks:
        if not isinstance(disk, Mapping) or not isinstance(disk.get("id"), str):
            raise ExecutorFailure(
                "identity_scan_invalid", "Drive identity revalidation was invalid."
            )
        identifier = str(disk["id"])
        if identifier in result:
            raise ExecutorFailure(
                "drive_identity_ambiguous", "A selected drive identity is duplicated."
            )
        result[identifier] = disk
    return result


def _review_document(disk: Mapping[str, Any]) -> dict[str, Any]:
    identity = disk.get("identity") if isinstance(disk.get("identity"), Mapping) else {}
    sectors = disk.get("sector_sizes") if isinstance(disk.get("sector_sizes"), Mapping) else {}
    return {
        "id": disk.get("id"),
        "stable_identity": disk.get("stable_identity"),
        "vendor": disk.get("vendor"),
        "model": disk.get("model"),
        "serial": identity.get("serial"),
        "wwn": identity.get("wwn"),
        "eui64": identity.get("eui64"),
        "nguid": identity.get("nguid"),
        "capacity_bytes": disk.get("capacity_bytes"),
        "logical_sector_bytes": sectors.get("logical_bytes"),
        "physical_sector_bytes": sectors.get("physical_bytes"),
    }


def _discard_supported(disk: Mapping[str, Any]) -> bool:
    discard = disk.get("discard") if isinstance(disk.get("discard"), Mapping) else {}
    granularity = discard.get("granularity_bytes")
    maximum = discard.get("max_bytes")
    return (
        isinstance(granularity, int)
        and not isinstance(granularity, bool)
        and granularity > 0
        and isinstance(maximum, int)
        and not isinstance(maximum, bool)
        and maximum > 0
    )


def _kernel_path(disk: Mapping[str, Any]) -> Path:
    value = disk.get("kernel_path")
    if (
        not isinstance(value, str)
        or not value.startswith("/dev/")
        or "/../" in value
        or "\0" in value
    ):
        raise ExecutorFailure(
            "drive_path_invalid", "A selected drive has no safe current device path."
        )
    path = Path(value)
    if path.parent != Path("/dev"):
        raise ExecutorFailure(
            "drive_path_invalid", "A selected drive has no safe current device path."
        )
    return path


def _stable_path(paths: Paths, disk: Mapping[str, Any]) -> Path:
    """Resolve the live kernel path back to one unambiguous persistent alias."""
    kernel = _kernel_path(disk)
    try:
        target = kernel.resolve(strict=True)
        candidates = sorted(
            item
            for item in paths.dev_by_id.iterdir()
            if item.is_symlink()
            and item.resolve(strict=True) == target
            and "-part" not in item.name
        )
    except OSError as exc:
        raise ExecutorFailure(
            "stable_device_path_unavailable",
            "A selected drive could not be resolved through /dev/disk/by-id.",
        ) from exc
    if not candidates:
        raise ExecutorFailure(
            "stable_device_path_unavailable",
            "A selected drive has no persistent /dev/disk/by-id path.",
        )
    # Multiple aliases are normal (wwn/scsi/ata); using the sorted first alias
    # is deterministic because identity was already bound and revalidated.
    return candidates[0]


def _selected_live_devices(
    document: Mapping[str, Any], payload: Mapping[str, Any]
) -> dict[str, Mapping[str, Any]]:
    storage = document.get("storage")
    if not isinstance(storage, Mapping):
        raise ExecutorFailure("plan_invalid", "The plan does not contain a storage document.")
    selected = storage.get("selected_devices")
    if not isinstance(selected, list) or not selected:
        raise ExecutorFailure("plan_invalid", "The plan has no selected drives.")
    live = _device_map(payload)
    result: dict[str, Mapping[str, Any]] = {}
    for expected in selected:
        if not isinstance(expected, Mapping) or not isinstance(expected.get("id"), str):
            raise ExecutorFailure("plan_invalid", "The plan contains an invalid drive identity.")
        identifier = str(expected["id"])
        current = live.get(identifier)
        if current is None:
            raise ExecutorFailure(
                "drive_identity_changed", "A selected drive is no longer present."
            )
        current_review = _review_document(current)
        if (
            expected.get("stable_identity") is not True
            or current_review["stable_identity"] is not True
        ):
            raise ExecutorFailure(
                "drive_identity_unstable", "A selected drive has no stable hardware identity."
            )
        changed = [
            field for field in IDENTITY_FIELDS if expected.get(field) != current_review.get(field)
        ]
        if changed:
            raise ExecutorFailure(
                "drive_identity_changed",
                "A selected drive no longer matches the reviewed hardware snapshot.",
            )
        _kernel_path(current)
        result[identifier] = current
    binding = storage.get("snapshot_binding")
    if not isinstance(binding, Mapping) or binding.get("selected_device_ids") != list(result):
        raise ExecutorFailure(
            "plan_binding_invalid", "The plan's selected-drive binding is invalid."
        )
    if binding.get("device_binding_sha256") != document_hash(selected):
        raise ExecutorFailure(
            "plan_binding_invalid", "The plan's selected-drive binding is invalid."
        )
    return result


def _block_name(path: Path) -> str:
    return path.name


def _has_active_holder(paths: Paths, device: Path) -> bool:
    holders = paths.sys_class_block / _block_name(device) / "holders"
    try:
        return any(holders.iterdir())
    except OSError:
        return True


def _active_block_paths(paths: Paths) -> set[str]:
    try:
        result = subprocess.run(
            [_tool("lsblk"), "--json", "--paths", "--output", "NAME,PKNAME,TYPE,MOUNTPOINTS"],
            check=True,
            shell=False,
            capture_output=True,
            timeout=30,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
        tree = json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ExecutorFailure(
            "activation_state_unknown", "Active storage use could not be determined."
        ) from exc
    active: set[str] = set()

    def visit(node: object, parents: tuple[str, ...] = ()) -> None:
        if not isinstance(node, Mapping) or not isinstance(node.get("name"), str):
            raise ExecutorFailure(
                "activation_state_unknown", "Active storage use could not be determined."
            )
        name = str(node["name"])
        mountpoints = node.get("mountpoints")
        used = isinstance(mountpoints, list) and any(
            item for item in mountpoints if isinstance(item, str)
        )
        children = node.get("children", [])
        if not isinstance(children, list):
            raise ExecutorFailure(
                "activation_state_unknown", "Active storage use could not be determined."
            )
        before = len(active)
        for child in children:
            visit(child, (*parents, name))
        if (
            used
            or (len(active) > before and any(parent in active for parent in (name,)))
            or any(
                isinstance(child, Mapping) and str(child.get("name")) in active
                for child in children
            )
        ):
            active.add(name)
            active.update(parents)

    blockdevices = tree.get("blockdevices") if isinstance(tree, Mapping) else None
    if not isinstance(blockdevices, list):
        raise ExecutorFailure(
            "activation_state_unknown", "Active storage use could not be determined."
        )
    for node in blockdevices:
        visit(node)
    try:
        lines = paths.proc_swaps.read_text(encoding="utf-8").splitlines()[1:]
    except OSError as exc:
        raise ExecutorFailure(
            "activation_state_unknown", "Active swap could not be determined."
        ) from exc
    active.update(
        line.split()[0] for line in lines if line.split() and line.split()[0].startswith("/dev/")
    )
    return active


def _ensure_not_active(paths: Paths, devices: Mapping[str, Mapping[str, Any]]) -> None:
    active = _active_block_paths(paths)
    for disk in devices.values():
        path = _kernel_path(disk)
        partitions = disk.get("partitions") if isinstance(disk.get("partitions"), list) else []
        related = {str(path)} | {
            str(item.get("kernel_path"))
            for item in partitions
            if isinstance(item, Mapping) and isinstance(item.get("kernel_path"), str)
        }
        if related & active:
            raise ExecutorFailure(
                "drive_in_active_use",
                "A selected drive is mounted, used for swap, or in the boot chain.",
            )
        if any(_has_active_holder(paths, Path(item)) for item in related):
            raise ExecutorFailure(
                "drive_has_active_holder", "A selected drive belongs to an active storage stack."
            )


def _validate_plan(
    request: Mapping[str, Any],
    *,
    operation: str = "apply_storage_plan",
) -> tuple[str, str, dict[str, Any], dict[str, Any] | None]:
    if set(request) != {"operation", "operation_id", "plan_sha256", "document", "approval"}:
        raise ExecutorFailure("request_invalid", "The storage request is invalid.")
    operation_id = request.get("operation_id")
    plan_sha = request.get("plan_sha256")
    document = request.get("document")
    approval = request.get("approval")
    if (
        request.get("operation") != operation
        or not isinstance(operation_id, str)
        or not UUID_RE.fullmatch(operation_id)
    ):
        raise ExecutorFailure("request_invalid", "The storage request is invalid.")
    if not isinstance(plan_sha, str) or not SHA256_RE.fullmatch(plan_sha):
        raise ExecutorFailure("request_invalid", "The storage request is invalid.")
    if not isinstance(document, dict) or document_hash(document) != plan_sha:
        raise ExecutorFailure(
            "plan_hash_mismatch", "The immutable storage plan failed verification."
        )
    if document.get("apply_available") is not True or document.get("blockers") != []:
        raise ExecutorFailure("plan_not_executable", "This storage plan is not executable.")
    storage = document.get("storage")
    if not isinstance(storage, dict) or storage.get("topology") not in SUPPORTED_TOPOLOGIES:
        raise ExecutorFailure(
            "topology_not_supported", "This storage layout is not executable yet."
        )
    actions = storage.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ExecutorFailure("plan_invalid", "The storage plan has no typed actions.")
    binding = storage.get("snapshot_binding")
    selected_ids = binding.get("selected_device_ids") if isinstance(binding, Mapping) else None
    if (
        not isinstance(selected_ids, list)
        or not selected_ids
        or not all(isinstance(item, str) for item in selected_ids)
    ):
        raise ExecutorFailure("plan_binding_invalid", "The selected-drive binding is invalid.")
    presentation_root_value = document.get("presentation_root")
    if not isinstance(presentation_root_value, str):
        raise ExecutorFailure("mountpoint_invalid", "The storage presentation root is invalid.")
    presentation_root = _safe_mountpoint(presentation_root_value)
    outer_actions = document.get("actions")
    directories = outer_actions.get("directories") if isinstance(outer_actions, Mapping) else None
    if not isinstance(directories, list):
        raise ExecutorFailure("directory_action_invalid", "The directory plan is invalid.")
    for directory_action in directories:
        if (
            not isinstance(directory_action, Mapping)
            or set(directory_action) != {"action_id", "type", "path", "purpose", "destructive"}
            or directory_action.get("type") != "directory.ensure"
            or directory_action.get("destructive") is not False
            or not isinstance(directory_action.get("path"), str)
        ):
            raise ExecutorFailure("directory_action_invalid", "The directory plan is invalid.")
        directory = _safe_mountpoint(str(directory_action["path"]))
        if presentation_root != directory and presentation_root not in directory.parents:
            raise ExecutorFailure(
                "directory_outside_storage", "A planned directory is outside the storage root."
            )
    connectivity_actions = (
        outer_actions.get("connectivity", []) if isinstance(outer_actions, Mapping) else None
    )
    if not isinstance(connectivity_actions, list):
        raise ExecutorFailure("connectivity_action_invalid", "The connectivity plan is invalid.")
    account = storage.get("service_account")
    username = account.get("username") if isinstance(account, Mapping) else None
    if connectivity_actions and (
        not isinstance(username, str) or not SERVICE_USERNAME_RE.fullmatch(username)
    ):
        raise ExecutorFailure(
            "connectivity_account_invalid", "The planned file-access account is invalid."
        )
    for connectivity_action in connectivity_actions:
        if (
            not isinstance(connectivity_action, Mapping)
            or set(connectivity_action)
            != {"action_id", "type", "name", "path", "read_only", "guest", "destructive"}
            or connectivity_action.get("type") != "smb.share.ensure"
            or connectivity_action.get("destructive") is not False
            or connectivity_action.get("guest") is not False
            or not isinstance(connectivity_action.get("read_only"), bool)
            or not isinstance(connectivity_action.get("name"), str)
            or not SMB_SHARE_NAME_RE.fullmatch(str(connectivity_action["name"]))
            or not isinstance(connectivity_action.get("path"), str)
        ):
            raise ExecutorFailure("connectivity_action_invalid", "The SMB share plan is invalid.")
        share_path = _safe_mountpoint(str(connectivity_action["path"]))
        if presentation_root != share_path and presentation_root not in share_path.parents:
            raise ExecutorFailure(
                "connectivity_path_outside_storage", "An SMB share is outside the storage root."
            )
    destructive = False
    action_ids: set[str] = set()
    layout_count = 0
    partition_actions: set[str] = set()
    for action in actions:
        if not isinstance(action, dict) or action.get("type") not in ACTION_TYPES:
            raise ExecutorFailure(
                "action_not_supported", "The plan contains an unsupported action."
            )
        action_type = str(action["type"])
        if not set(action) <= ACTION_FIELDS[action_type]:
            raise ExecutorFailure(
                "action_fields_invalid", "A typed action contains unknown fields."
            )
        action_id = action.get("action_id")
        if not isinstance(action_id, str) or not action_id or action_id in action_ids:
            raise ExecutorFailure("plan_invalid", "The plan contains an invalid action identity.")
        action_ids.add(action_id)
        if not isinstance(action.get("destructive"), bool):
            raise ExecutorFailure(
                "plan_invalid", "The plan contains an invalid action risk marker."
            )
        if action_type != "storage.layout.ensure":
            if action.get("device_id") not in selected_ids:
                raise ExecutorFailure("plan_invalid", "An action names an unselected drive.")
        elif action.get("device_ids") != selected_ids:
            raise ExecutorFailure("plan_invalid", "The layout action changed the selected drives.")
        if (
            action_type
            in {
                "drive.identity.verify",
                "drive.surface.read",
                "drive.smart.short",
                "drive.smart.extended",
            }
            and action["destructive"]
        ):
            raise ExecutorFailure("plan_risk_invalid", "A read-only action is marked destructive.")
        if (
            action_type
            in {
                "drive.write_read.destructive",
                "disk.partition_table.create",
                "filesystem.create",
            }
            and not action["destructive"]
        ):
            raise ExecutorFailure("plan_risk_invalid", "A destructive action is marked read-only.")
        if action_type == "disk.partition_table.create" and (
            action.get("table") not in SAFE_TABLES
            or action.get("alignment_bytes") not in {1024 * 1024, 4 * 1024 * 1024}
        ):
            raise ExecutorFailure("partition_options_invalid", "The partition options are invalid.")
        if action_type == "disk.partition_table.create":
            partition_actions.add(str(action["device_id"]))
        if action_type == "filesystem.create":
            allocation = action.get("allocation_unit_bytes")
            if action.get("filesystem") not in SAFE_FILESYSTEMS or (
                allocation is not None
                and (
                    isinstance(allocation, bool)
                    or not isinstance(allocation, int)
                    or allocation < 512
                    or allocation > 1024 * 1024
                )
            ):
                raise ExecutorFailure(
                    "filesystem_options_invalid", "The filesystem options are invalid."
                )
            if str(action["device_id"]) not in partition_actions:
                raise ExecutorFailure(
                    "action_order_invalid", "Filesystem creation must follow partitioning."
                )
        if action_type == "storage.layout.ensure":
            layout_count += 1
            expected_destructive = storage["topology"] in {"zfs", "raid", "snapraid", "mixed"}
            if (
                action.get("topology") != storage["topology"]
                or action["destructive"] is not expected_destructive
            ):
                raise ExecutorFailure(
                    "plan_risk_invalid", "The supported layout action is invalid."
                )
        destructive = destructive or action["destructive"]
    if storage["topology"] == "test":
        if layout_count != 0 or any(
            action.get("type")
            in {"disk.partition_table.create", "filesystem.create", "storage.layout.ensure"}
            for action in actions
        ):
            raise ExecutorFailure("test_plan_destructive", "A test-only plan cannot build storage.")
        if directories or connectivity_actions:
            raise ExecutorFailure(
                "test_plan_invalid", "A test-only plan cannot create folders or shares."
            )
    elif layout_count != 1 or actions[-1].get("type") != "storage.layout.ensure":
        raise ExecutorFailure("plan_invalid", "The layout action must be final and unique.")
    if storage["topology"] in {"individual", "cache", "block", "import"} and len(selected_ids) != 1:
        raise ExecutorFailure(
            "individual_layout_ambiguous", "This storage layout requires exactly one drive."
        )
    if storage["topology"] == "import" and any(
        action.get("type") in {"disk.partition_table.create", "filesystem.create"}
        for action in actions
    ):
        raise ExecutorFailure("import_plan_destructive", "An import plan cannot format storage.")
    if storage["topology"] == "mergerfs":
        mergerfs = storage.get("mergerfs")
        if (
            not isinstance(mergerfs, Mapping)
            or mergerfs.get("mode") not in {"create", "existing"}
            or not isinstance(mergerfs.get("mountpoint"), str)
        ):
            raise ExecutorFailure("mergerfs_plan_incomplete", "The mergerFS plan is incomplete.")
        _safe_mountpoint(str(mergerfs["mountpoint"]))
        if mergerfs.get("mode") == "create" and (
            mergerfs.get("create_policy") not in {"mfs", "epmfs"}
            or mergerfs.get("search_policy") not in {"ff", "all"}
        ):
            raise ExecutorFailure("mergerfs_plan_incomplete", "The mergerFS plan is incomplete.")
    if storage["topology"] in {"zfs", "raid", "snapraid", "mixed"}:
        options = storage.get("layout_options")
        if not isinstance(options, Mapping) or options != next(
            action.get("layout_options")
            for action in actions
            if action.get("type") == "storage.layout.ensure"
        ):
            raise ExecutorFailure("layout_options_invalid", "The array layout options are invalid.")
    risk = storage.get("risk")
    if not isinstance(risk, dict) or risk.get("destructive") is not destructive:
        raise ExecutorFailure(
            "plan_risk_invalid", "The plan's destructive-risk declaration is invalid."
        )
    format_document = storage.get("format")
    if isinstance(format_document, Mapping) and "trim" in format_document:
        trim = format_document.get("trim")
        selected_devices = storage.get("selected_devices")
        if (
            not isinstance(trim, Mapping)
            or trim.get("mode") not in {"conditional", "periodic", "continuous", "disabled"}
            or not isinstance(trim.get("enabled"), bool)
            or not isinstance(selected_devices, list)
        ):
            raise ExecutorFailure("trim_plan_invalid", "The TRIM plan is invalid.")
        reviewed_support = all(
            isinstance(device, Mapping) and _discard_supported(device)
            for device in selected_devices
        )
        if trim.get("enabled") is True and (trim.get("mode") == "disabled" or not reviewed_support):
            raise ExecutorFailure(
                "trim_path_unsupported",
                "TRIM cannot be enabled because the complete reviewed path lacks discard support.",
            )
    if destructive:
        expected_approval_fields = {
            "approval_id",
            "plan_sha256",
            "wizard_revision",
            "hardware_snapshot_sha256",
            "device_binding_sha256",
            "selected_device_ids",
            "confirmation_phrase",
            "confirmation_sha256",
        }
        if (
            not isinstance(approval, dict)
            or set(approval) != expected_approval_fields
            or approval.get("plan_sha256") != plan_sha
            or approval.get("hardware_snapshot_sha256") != binding.get("snapshot_sha256")
            or approval.get("device_binding_sha256") != binding.get("device_binding_sha256")
            or approval.get("selected_device_ids") != selected_ids
        ):
            raise ExecutorFailure(
                "destructive_consent_missing", "Exact destructive approval is required."
            )
        if approval.get("confirmation_phrase") != "I AGREE":
            raise ExecutorFailure(
                "destructive_consent_missing", "Exact destructive approval is required."
            )
        if approval.get("confirmation_sha256") != document_hash({"confirmation": "I AGREE"}):
            raise ExecutorFailure(
                "destructive_consent_missing", "Exact destructive approval is required."
            )
    elif approval is not None and not isinstance(approval, dict):
        raise ExecutorFailure("request_invalid", "The storage request is invalid.")
    return operation_id, plan_sha, document, approval


@contextlib.contextmanager
def _device_locks(paths: Paths, device_ids: list[str]) -> Iterator[None]:
    if fcntl is None:
        raise ExecutorFailure("platform_not_supported", "Storage execution requires a Linux host.")
    paths.lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    handles: list[Any] = []
    try:
        for identifier in sorted(device_ids):
            lock_name = document_hash(identifier)
            handle = (paths.lock_root / f"{lock_name}.lock").open("a+b")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                handle.close()
                raise ExecutorFailure(
                    "drive_busy", "A selected drive is already being changed."
                ) from exc
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles):
            with contextlib.suppress(OSError):
                fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()


def _journal_path(paths: Paths, operation_id: str) -> Path:
    return paths.transaction_root / f"{operation_id}.json"


def _work_path(paths: Paths, operation_id: str) -> Path:
    return paths.transaction_root / f"{operation_id}.work.json"


def _active_surface_read_progress(expected_device: str | None = None) -> dict[str, Any] | None:
    """Return kernel-accounted progress for the active read-only badblocks process."""

    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return None
    try:
        clock_ticks = os.sysconf("SC_CLK_TCK")
        uptime = float((proc_root / "uptime").read_text(encoding="ascii").split()[0])
    except (OSError, ValueError, IndexError):
        return None
    for process in proc_root.iterdir():
        if not process.name.isdigit():
            continue
        try:
            arguments = (process / "cmdline").read_bytes().split(b"\0")
            arguments = [item.decode("utf-8") for item in arguments if item]
            if not arguments or Path(arguments[0]).name != "badblocks" or "-sv" not in arguments:
                continue
            device = Path(arguments[-1])
            if device.parent != Path("/dev"):
                continue
            if expected_device is not None and os.fspath(device) != expected_device:
                continue
            io_values = {}
            for line in (process / "io").read_text(encoding="ascii").splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    io_values[key] = int(value.strip())
            stat_fields = (process / "stat").read_text(encoding="ascii").split()
            started_after_boot = int(stat_fields[21]) / clock_ticks
            elapsed = max(1.0, uptime - started_after_boot)
            sectors = int(
                (Path("/sys/class/block") / device.name / "size")
                .read_text(encoding="ascii")
                .strip()
            )
            total = sectors * 512
            processed = min(total, max(0, io_values.get("read_bytes", 0)))
            rate = processed / elapsed
            remaining = max(0, total - processed)
            return {
                "kind": "surface_read",
                "device": os.fspath(device),
                "processed_bytes": processed,
                "total_bytes": total,
                "percent": round(processed * 100 / total, 1) if total else 0.0,
                "elapsed_seconds": int(elapsed),
                "bytes_per_second": int(rate),
                "estimated_seconds_remaining": int(remaining / rate) if rate > 0 else None,
            }
        except (OSError, UnicodeDecodeError, ValueError, IndexError):
            continue
    return None


def _work_action_device(paths: Paths, operation_id: str, action_id: object) -> str | None:
    if not isinstance(action_id, str):
        return None
    try:
        work = json.loads(_work_path(paths, operation_id).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    actions = work.get("actions") if isinstance(work, dict) else None
    if not isinstance(actions, list):
        return None
    for action in actions:
        if isinstance(action, dict) and action.get("id") == action_id:
            device = action.get("device")
            return device if isinstance(device, str) and device.startswith("/dev/") else None
    return None


def _work_estimate(
    paths: Paths,
    operation_id: str,
    completed_actions: list[str],
    action_progress: Mapping[str, Any] | None,
    current_action: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    try:
        work = json.loads(_work_path(paths, operation_id).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    actions = work.get("actions") if isinstance(work, dict) else None
    if not isinstance(actions, list):
        return None
    completed = set(completed_actions)
    if isinstance(action_progress, Mapping) and action_progress.get("kind") == "smart_self_test":
        remaining = action_progress.get("estimated_seconds_remaining")
        expected = action_progress.get("expected_finish_at")
        if isinstance(remaining, (int, float)) and remaining >= 0:
            return {
                "scope": f"SMART {action_progress.get('test_kind', 'self-test')}",
                "estimated_seconds_remaining": round(remaining),
                "estimated_completion_at": expected
                if isinstance(expected, (int, float))
                else time.time() + remaining,
                "remaining_bytes": None,
            }
    if not isinstance(action_progress, Mapping):
        remaining_seconds = 30
        for action in actions:
            if not isinstance(action, Mapping) or action.get("id") in completed:
                continue
            action_type = action.get("type")
            if action_type == "drive.surface.read":
                capacity = action.get("capacity_bytes")
                if isinstance(capacity, int) and capacity > 0:
                    remaining_seconds += int(capacity / INITIAL_SURFACE_READ_BYTES_PER_SECOND)
                else:
                    remaining_seconds += 3_600
            else:
                remaining_seconds += ACTION_ESTIMATED_SECONDS.get(str(action_type), 15)
        started_at = current_action.get("started_at") if current_action else None
        if isinstance(started_at, (int, float)):
            remaining_seconds = max(1, remaining_seconds - int(time.time() - started_at))
        return {
            "scope": "storage_build",
            "estimated_seconds_remaining": remaining_seconds,
            "estimated_completion_at": time.time() + remaining_seconds,
            "remaining_bytes": None,
        }
    rate = action_progress.get("bytes_per_second")
    current_device = action_progress.get("device")
    processed = action_progress.get("processed_bytes")
    if not isinstance(rate, (int, float)) or rate <= 0 or not isinstance(processed, int):
        return None
    remaining_bytes = 0
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_id = action.get("id")
        if action_id in completed:
            continue
        if action.get("type") != "drive.surface.read":
            continue
        capacity = action.get("capacity_bytes")
        device = action.get("device")
        if not isinstance(capacity, int):
            continue
        remaining_bytes += max(0, capacity - processed) if device == current_device else capacity
    if remaining_bytes <= 0:
        return None
    seconds = int(remaining_bytes / rate)
    seconds += sum(
        ACTION_ESTIMATED_SECONDS.get(str(action.get("type")), 15)
        for action in actions
        if isinstance(action, dict)
        and action.get("id") not in completed
        and action.get("type") != "drive.surface.read"
    )
    return {
        "scope": "intake_tests",
        "estimated_seconds_remaining": seconds,
        "estimated_completion_at": time.time() + seconds,
        "remaining_bytes": remaining_bytes,
    }


def storage_operation_status(operation_id: str, *, paths: Paths | None = None) -> dict[str, Any]:
    paths = paths or Paths()
    if not OPERATION_ID_RE.fullmatch(operation_id):
        raise ExecutorFailure("operation_id_invalid", "The storage operation identity is invalid.")
    journal_path = _journal_path(paths, operation_id)
    try:
        details = journal_path.lstat()
    except FileNotFoundError:
        return {
            "operation_id": operation_id,
            "state": "waiting",
            "phase": "Waiting for the storage executor",
            "completed_steps": 0,
            "total_steps": 0,
            "percent": 0,
            "completed_actions": [],
            "notices": [],
            "action_results": [],
            "current_action": None,
            "estimate": None,
            "updated_at": None,
        }
    if not stat.S_ISREG(details.st_mode) or (
        os.name != "nt" and (details.st_uid != _executor_uid() or details.st_mode & 0o022)
    ):
        raise ExecutorFailure(
            "transaction_journal_unsafe", "The storage transaction journal is unsafe."
        )
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExecutorFailure(
            "transaction_journal_invalid", "The storage transaction journal is invalid."
        ) from exc
    if not isinstance(journal, dict) or journal.get("operation_id") != operation_id:
        raise ExecutorFailure(
            "transaction_journal_invalid", "The storage transaction journal is invalid."
        )
    completed = journal.get("completed_steps", 0)
    total = journal.get("total_steps", 0)
    if not isinstance(completed, int) or not isinstance(total, int) or completed < 0 or total < 0:
        raise ExecutorFailure(
            "transaction_journal_invalid", "The storage transaction journal is invalid."
        )
    percent = (
        100
        if journal.get("state") == "succeeded"
        else (min(99, int(completed * 100 / total)) if total else 0)
    )
    current = journal.get("current_action")
    if current is not None and not isinstance(current, dict):
        current = None
    elif isinstance(current, dict):
        current = dict(current)
    completed_actions = journal.get("completed_actions")
    if not isinstance(completed_actions, list):
        completed_actions = []
    notices = journal.get("notices")
    if not isinstance(notices, list):
        notices = []
    action_progress = current.get("progress") if isinstance(current, dict) else None
    if not isinstance(action_progress, Mapping):
        action_progress = None
    if current and current.get("type") == "drive.surface.read":
        expected_device = _work_action_device(paths, operation_id, current.get("id"))
        action_progress = _active_surface_read_progress(expected_device)
        if action_progress is not None:
            current["progress"] = action_progress
            if total:
                percent = min(
                    99,
                    round(
                        (completed + float(action_progress.get("percent", 0)) / 100) * 100 / total
                    ),
                )
    elif action_progress is not None and action_progress.get("kind") == "smart_self_test":
        current_percent = action_progress.get("percent")
        if total and isinstance(current_percent, (int, float)):
            percent = min(99, round((completed + current_percent / 100) * 100 / total))
    estimate = _work_estimate(
        paths,
        operation_id,
        [item for item in completed_actions if isinstance(item, str)],
        action_progress,
        current,
    )
    result = journal.get("result")
    return {
        "operation_id": operation_id,
        "state": str(journal.get("state", "running")),
        "phase": str(journal.get("phase", "Preparing storage")),
        "completed_steps": completed,
        "total_steps": total,
        "percent": percent,
        "completed_actions": [item for item in completed_actions if isinstance(item, str)],
        "notices": [item for item in notices if isinstance(item, dict)],
        "action_results": [
            item for item in journal.get("action_results", []) if isinstance(item, dict)
        ]
        if isinstance(journal.get("action_results"), list)
        else [],
        "current_action": current,
        "estimate": estimate,
        "updated_at": journal.get("updated_at"),
        "result": dict(result)
        if journal.get("state") == "succeeded" and isinstance(result, dict)
        else None,
        "sanitization_report": (
            dict(journal["sanitization_report"])
            if isinstance(journal.get("sanitization_report"), dict)
            else None
        ),
    }


def _load_prior_journal(path: Path, plan_sha: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ExecutorFailure(
            "transaction_journal_invalid", "The prior transaction journal is invalid."
        ) from exc
    if not isinstance(document, dict) or document.get("plan_sha256") != plan_sha:
        raise ExecutorFailure(
            "operation_id_conflict", "This operation identity belongs to another plan."
        )
    if document.get("state") == "succeeded" and isinstance(document.get("result"), dict):
        return dict(document["result"])
    raise ExecutorFailure(
        "prior_operation_needs_attention",
        "A previous attempt has an uncertain outcome and must be inspected before retrying.",
        needs_attention=True,
    )


def _load_resume_journal(path: Path, plan_sha: str) -> dict[str, Any]:
    if not path.exists():
        raise ExecutorFailure(
            "resume_journal_missing",
            "The storage checkpoint is unavailable; the operation cannot be resumed safely.",
            needs_attention=True,
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ExecutorFailure(
            "transaction_journal_invalid", "The prior transaction journal is invalid."
        ) from exc
    if not isinstance(document, dict) or document.get("plan_sha256") != plan_sha:
        raise ExecutorFailure(
            "operation_id_conflict", "This operation identity belongs to another plan."
        )
    if document.get("state") != "needs_attention":
        raise ExecutorFailure(
            "operation_not_resumable",
            "Only a storage operation that needs attention can resume from its checkpoint.",
        )
    completed = document.get("completed_actions")
    if not isinstance(completed, list) or not all(isinstance(item, str) for item in completed):
        raise ExecutorFailure(
            "transaction_journal_invalid", "The storage checkpoint is incomplete."
        )
    return document


def _safe_mountpoint(value: str) -> Path:
    if (
        not isinstance(value, str)
        or any(character.isspace() or ord(character) < 32 for character in value)
        or "\\" in value
    ):
        raise ExecutorFailure(
            "mountpoint_invalid", "The storage mount path is outside approved roots."
        )
    path = PurePosixPath(value)
    allowed = (PurePosixPath("/data"), PurePosixPath("/mnt"), PurePosixPath("/srv"))
    if (
        not path.is_absolute()
        or path == PurePosixPath("/")
        or ".." in path.parts
        or not any(path == root or root in path.parents for root in allowed)
    ):
        raise ExecutorFailure(
            "mountpoint_invalid", "The storage mount path is outside approved roots."
        )
    result = Path(str(path))
    _assert_no_symlink_components(result)
    return result


def _service_account_group_id(username: str) -> int:
    if not SERVICE_USERNAME_RE.fullmatch(username) or pwd is None:
        raise ExecutorFailure(
            "storage_access_account_invalid",
            "The planned file-access account is unavailable.",
        )
    try:
        account = pwd.getpwnam(username)
    except KeyError as exc:
        raise ExecutorFailure(
            "storage_access_account_missing",
            "The planned file-access account does not exist. No permissions were changed.",
            needs_attention=True,
        ) from exc
    if account.pw_uid == 0:
        raise ExecutorFailure(
            "storage_access_account_invalid",
            "The root account cannot be used as a storage file-access account.",
        )
    return int(account.pw_gid)


def _apply_directory_mode(path: Path, group_id: int, *, mode: int = 0o770) -> None:
    """Set exact access through a no-follow descriptor, independent of service umask."""

    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ExecutorFailure(
            "storage_directory_unavailable",
            "A planned storage folder could not be opened safely.",
        ) from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISDIR(details.st_mode):
            raise ExecutorFailure(
                "storage_directory_invalid",
                "A planned storage folder is not a directory.",
            )
        os.fchown(descriptor, 0, group_id)
        os.fchmod(descriptor, mode)
    except ExecutorFailure:
        raise
    except OSError as exc:
        raise ExecutorFailure(
            "storage_directory_access_failed",
            "The planned storage folder permissions could not be applied.",
            needs_attention=True,
        ) from exc
    finally:
        os.close(descriptor)


def _ensure_storage_directory_access(
    presentation_root: Path,
    actions: list[Mapping[str, Any]],
    username: str,
) -> list[str]:
    """Create only approved folders and grant the reviewed service account access."""

    if not actions:
        return []
    group_id = _service_account_group_id(username)
    directories: set[Path] = set()
    for action in actions:
        if (
            not isinstance(action, Mapping)
            or action.get("type") != "directory.ensure"
            or not isinstance(action.get("path"), str)
        ):
            raise ExecutorFailure(
                "directory_action_invalid", "The plan contains an invalid directory action."
            )
        directory = _safe_mountpoint(str(action["path"]))
        if presentation_root != directory and presentation_root not in directory.parents:
            raise ExecutorFailure(
                "directory_outside_storage",
                "A planned directory is outside the mounted storage root.",
            )
        current = directory
        while current != presentation_root:
            directories.add(current)
            current = current.parent

    applied: list[str] = []
    for directory in sorted(directories, key=lambda item: (len(item.parts), str(item))):
        try:
            directory.mkdir(parents=False, exist_ok=True, mode=0o770)
        except OSError as exc:
            raise ExecutorFailure(
                "storage_directory_unavailable",
                "A planned storage folder could not be created.",
            ) from exc
        _assert_no_symlink_components(directory)
        _apply_directory_mode(directory, group_id)
        applied.append(os.fspath(directory))
    return applied


def _ensure_mergerfs_branch_traversal(mount_root: Path, username: str) -> list[str]:
    """Allow only the managed account group to traverse mergerFS branch parents."""

    group_id = _service_account_group_id(username)
    roots = (mount_root.parent, mount_root)
    applied: list[str] = []
    for path in roots:
        try:
            path.mkdir(parents=True, exist_ok=True, mode=0o710)
        except OSError as exc:
            raise ExecutorFailure(
                "storage_branch_root_unavailable",
                "The managed storage branch root could not be prepared.",
            ) from exc
        _assert_no_symlink_components(path)
        _apply_directory_mode(path, group_id, mode=0o710)
        applied.append(os.fspath(path))
    return applied


def _assert_no_symlink_components(path: Path) -> None:
    """Reject an existing symlink anywhere in a privileged storage path."""

    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            details = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ExecutorFailure(
                "mountpoint_unavailable", "The storage mount path could not be inspected."
            ) from exc
        if stat.S_ISLNK(details.st_mode):
            raise ExecutorFailure(
                "mountpoint_symlink", "The storage mount path contains a symbolic link."
            )


def _partition_path(device: Path) -> Path:
    if device.parent.name == "by-id":
        return device.with_name(f"{device.name}-part1")
    return Path(f"{device}p1" if device.name.startswith("nvme") else f"{device}1")


def _filesystem_command(
    filesystem: str, allocation: int | None, partition: Path, *, format_mode: str
) -> list[str]:
    if format_mode != "quick":
        raise ExecutorFailure("format_mode_invalid", "The filesystem format mode is unsupported.")
    if filesystem == "ext4":
        command = [
            _tool("mkfs.ext4"),
            "-F",
            "-E",
            "lazy_itable_init=1,lazy_journal_init=1,nodiscard",
        ]
        if allocation:
            command.extend(["-b", str(allocation)])
    elif filesystem == "xfs":
        command = [_tool("mkfs.xfs"), "-f", "-K"]
        if allocation:
            command.extend(["-s", f"size={allocation}"])
    elif filesystem == "btrfs":
        command = [_tool("mkfs.btrfs"), "-f", "-K"]
    elif filesystem == "ntfs":
        command = [_tool("mkfs.ntfs"), "-F"]
        if allocation:
            command.extend(["-c", str(allocation)])
    elif filesystem == "exfat":
        command = [_tool("mkfs.exfat")]
        if allocation:
            command.extend(["-c", str(allocation)])
    else:
        raise ExecutorFailure(
            "filesystem_not_supported", "The requested filesystem is unsupported."
        )
    return [*command, os.fspath(partition)]


def _revalidate(
    document: Mapping[str, Any], inventory_provider: InventoryProvider, paths: Paths
) -> dict[str, Mapping[str, Any]]:
    devices = _selected_live_devices(document, inventory_provider())
    _ensure_not_active(paths, devices)
    return devices


def _resume_revalidate(
    document: Mapping[str, Any],
    inventory_provider: InventoryProvider,
    paths: Paths,
    journal: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    """Allow only executor-created member mounts while resuming an exact plan.

    A failed storage build can have completed partitioning, formatting, and a
    managed member mount before a later layout command stops.  Normal plan
    validation quite correctly rejects any active selected disk.  Resumption
    is narrower: the active path must be the deterministic Hoardarr member
    mount, and the journal must prove that this operation completed both the
    partition and filesystem actions for that same stable device identity.
    """

    devices = _selected_live_devices(document, inventory_provider())
    storage = document.get("storage")
    completed = {
        item for item in journal.get("completed_actions", []) if isinstance(item, str)
    }
    actions = storage.get("actions") if isinstance(storage, Mapping) else None
    if not isinstance(actions, list):
        raise ExecutorFailure("plan_invalid", "The plan has no typed actions.")
    completed_types_by_device: dict[str, set[str]] = {}
    for action in actions:
        if not isinstance(action, Mapping) or action.get("action_id") not in completed:
            continue
        identifier = action.get("device_id")
        action_type = action.get("type")
        if isinstance(identifier, str) and isinstance(action_type, str):
            completed_types_by_device.setdefault(identifier, set()).add(action_type)

    for identifier, disk in devices.items():
        expected_mount = os.fspath(paths.mount_root / document_hash(identifier)[:16])
        observed_mounts = {
            mount
            for mount in disk.get("mountpoints", [])
            if isinstance(mount, str) and mount
        }
        partitions = disk.get("partitions") if isinstance(disk.get("partitions"), list) else []
        for partition in partitions:
            if not isinstance(partition, Mapping):
                continue
            observed_mounts.update(
                mount
                for mount in partition.get("mountpoints", [])
                if isinstance(mount, str) and mount
            )
        if observed_mounts:
            completed_types = completed_types_by_device.get(identifier, set())
            if observed_mounts != {expected_mount} or not {
                "disk.partition_table.create",
                "filesystem.create",
            }.issubset(completed_types):
                raise ExecutorFailure(
                    "resume_activation_changed",
                    "A selected drive is active outside the exact Hoardarr checkpoint. "
                    "No resume action was started.",
                    needs_attention=True,
                )
        else:
            _ensure_not_active(paths, {identifier: disk})
    return devices


def _preflight_storage_tools(document: Mapping[str, Any]) -> None:
    storage = document.get("storage")
    if not isinstance(storage, Mapping):
        return
    mergerfs = storage.get("mergerfs")
    expansion = storage.get("expansion")
    if (
        storage.get("topology") == "mergerfs"
        and isinstance(mergerfs, Mapping)
        and mergerfs.get("mode") == "existing"
        and isinstance(expansion, Mapping)
        and expansion.get("kind") in {"add_mergerfs_member", "add_snapraid_data"}
    ):
        # Live branch expansion is implemented by mergerFS' documented xattr
        # interface.  Resolve it before a surface test, partition, or format so
        # a missing appliance package cannot leave a partially applied plan.
        _tool("setfattr")


_FSTAB_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")


def _fstab_decode(value: str) -> str:
    return _FSTAB_OCTAL_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def _fstab_encode(value: str) -> str:
    return (
        value.replace("\\", "\\134")
        .replace(" ", "\\040")
        .replace("\t", "\\011")
        .replace("\n", "\\012")
    )


def _replace_mergerfs_source(content: str, mountpoint: str, branches: list[str]) -> str:
    matches: list[int] = []
    rows = content.splitlines()
    for index, row in enumerate(rows):
        stripped = row.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if (
            len(fields) >= 4
            and fields[2] in MERGERFS_TYPES
            and _fstab_decode(fields[1]) == mountpoint
        ):
            matches.append(index)
    if len(matches) != 1:
        raise ExecutorFailure(
            "mergerfs_fstab_drift",
            "The persistent mergerFS mount entry changed; runtime expansion was not persisted.",
            needs_attention=True,
        )
    fields = rows[matches[0]].strip().split()
    fields[0] = _fstab_encode(":".join(branches))
    options = [
        option
        for option in fields[3].split(",")
        if option and not option.startswith("x-systemd.requires=")
    ]
    options.extend(f"x-systemd.requires={_fstab_encode(branch)}" for branch in branches)
    fields[3] = ",".join(options)
    rows[matches[0]] = " ".join(fields)
    return "\n".join(rows) + ("\n" if content.endswith("\n") else "")


def _normalize_runtime_mergerfs_branches(
    runtime_branches: list[str], configured_branches: list[str]
) -> list[str]:
    configured_by_name: dict[str, list[str]] = {}
    for branch in configured_branches:
        if not branch.startswith("/"):
            raise ExecutorFailure(
                "mergerfs_instance_invalid", "The persistent mergerFS branch list is invalid."
            )
        configured_by_name.setdefault(Path(branch).name, []).append(branch)
    normalized: list[str] = []
    for branch in runtime_branches:
        if branch.startswith("/"):
            normalized.append(branch)
            continue
        matches = configured_by_name.get(branch, [])
        if len(matches) != 1:
            raise ExecutorFailure(
                "mergerfs_instance_invalid",
                "A runtime mergerFS branch could not be tied to one persistent member.",
            )
        normalized.append(matches[0])
    if not normalized or len(normalized) != len(set(normalized)):
        raise ExecutorFailure(
            "mergerfs_instance_invalid", "The existing branch list is invalid."
        )
    return normalized


def _ensure_mergerfs_allow_other(fstab_path: Path, mountpoint: str) -> bool:
    """Persist the libfuse access option for one exact reviewed mergerFS mount."""

    try:
        content = fstab_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExecutorFailure(
            "fstab_unavailable", "Automatic mount configuration could not be read."
        ) from exc
    matches: list[tuple[int, list[str]]] = []
    rows = content.splitlines()
    for index, row in enumerate(rows):
        stripped = row.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if (
            len(fields) == 6
            and fields[2] in MERGERFS_TYPES
            and _fstab_decode(fields[1]) == mountpoint
        ):
            matches.append((index, fields))
    if len(matches) != 1:
        raise ExecutorFailure(
            "mergerfs_fstab_ambiguous",
            "The managed combined-storage mount configuration could not be identified safely.",
            needs_attention=True,
        )
    index, fields = matches[0]
    options = fields[3].split(",")
    if "allow_other" in options:
        return False
    fields[3] = ",".join(["allow_other", *options])
    rows[index] = " ".join(fields)
    atomic_text(fstab_path, "\n".join(rows) + ("\n" if content.endswith("\n") else ""), mode=0o644)
    return True


def _append_fstab(
    paths: Paths,
    operation_id: str,
    lines: list[str],
    *,
    mergerfs_update: tuple[str, list[str]] | None = None,
) -> None:
    current = paths.fstab.read_text(encoding="utf-8") if paths.fstab.exists() else ""
    marker = f"# BEGIN HOARDARR {operation_id}"
    if marker in current:
        if mergerfs_update is not None:
            updated = _replace_mergerfs_source(current, *mergerfs_update)
            if updated != current:
                atomic_text(paths.fstab, updated, mode=0o644)
        return
    if mergerfs_update is not None:
        current = _replace_mergerfs_source(current, *mergerfs_update)
    content = (
        current.rstrip("\n")
        + "\n"
        + marker
        + "\n"
        + "\n".join(lines)
        + f"\n# END HOARDARR {operation_id}\n"
    )
    atomic_text(paths.fstab, content, mode=0o644)


def _remove_fstab_operation(paths: Paths, operation_id: str) -> None:
    if not paths.fstab.exists():
        return
    current = paths.fstab.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"(?m)^# BEGIN HOARDARR {re.escape(operation_id)}\n.*?^# END HOARDARR "
        rf"{re.escape(operation_id)}\n?",
        re.DOTALL,
    )
    changed, count = pattern.subn("", current)
    if count:
        atomic_text(paths.fstab, changed, mode=0o644)


def _install_storage_timer(
    paths: Paths,
    *,
    unit_name: str,
    description: str,
    command: list[str],
    schedule: str,
    runner: CommandRunner,
) -> None:
    calendars = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
    if schedule == "disabled":
        return
    calendar = calendars.get(schedule)
    if calendar is None or not re.fullmatch(r"hoardarr-[a-z0-9_-]{1,96}", unit_name):
        raise ExecutorFailure("schedule_invalid", "A storage maintenance schedule is invalid.")
    if not command or any("\n" in value or "\0" in value for value in command):
        raise ExecutorFailure("schedule_invalid", "A storage maintenance command is invalid.")
    service_path = paths.systemd_unit_root / f"{unit_name}.service"
    timer_path = paths.systemd_unit_root / f"{unit_name}.timer"
    if (
        service_path.parent != paths.systemd_unit_root
        or timer_path.parent != paths.systemd_unit_root
    ):
        raise ExecutorFailure("schedule_invalid", "A storage maintenance path is invalid.")
    escaped = " ".join(command)
    atomic_text(
        service_path,
        "\n".join(
            (
                "[Unit]",
                f"Description={description}",
                "",
                "[Service]",
                "Type=oneshot",
                f"ExecStart={escaped}",
                "Nice=10",
                "IOSchedulingClass=idle",
                "",
            )
        ),
        mode=0o644,
    )
    atomic_text(
        timer_path,
        "\n".join(
            (
                "[Unit]",
                f"Description={description} schedule",
                "",
                "[Timer]",
                f"OnCalendar={calendar}",
                "Persistent=true",
                "RandomizedDelaySec=30m",
                f"Unit={unit_name}.service",
                "",
                "[Install]",
                "WantedBy=timers.target",
                "",
            )
        ),
        mode=0o644,
    )
    runner([_tool("systemctl"), "daemon-reload"], 60)
    runner([_tool("systemctl"), "enable", "--now", f"{unit_name}.timer"], 60)


def _ensure_smb_shares(
    paths: Paths,
    operation_id: str,
    actions: list[Mapping[str, Any]],
    username: str,
    runner: CommandRunner,
) -> None:
    if not actions:
        return
    sections: list[str] = ["# Managed by Hoardarr. Changes will be replaced."]
    for action in actions:
        name = str(action["name"])
        share_path = _safe_mountpoint(str(action["path"]))
        sections.extend(
            [
                "",
                f"[{name}]",
                f"    path = {share_path}",
                "    browseable = yes",
                f"    read only = {'yes' if action['read_only'] else 'no'}",
                "    guest ok = no",
                f"    valid users = {username}",
                "    create mask = 0660",
                "    directory mask = 0770",
            ]
        )
    include_content = "\n".join(sections) + "\n"
    current_main = (
        paths.samba_config.read_text(encoding="utf-8")
        if paths.samba_config.exists()
        else "[global]\n"
    )
    include_line = f"include = {paths.samba_include}"
    main_content = (
        current_main
        if include_line in current_main
        else current_main.rstrip("\n") + "\n\n# Hoardarr managed shares\n" + include_line + "\n"
    )
    candidate_include = paths.samba_include.with_name(
        f".{paths.samba_include.name}.candidate-{operation_id}"
    )
    candidate_main = paths.samba_config.with_name(
        f".{paths.samba_config.name}.candidate-{operation_id}"
    )
    candidate_main_content = main_content.replace(include_line, f"include = {candidate_include}")
    try:
        atomic_text(candidate_include, include_content, mode=0o644)
        atomic_text(candidate_main, candidate_main_content, mode=0o644)
        runner([_tool("testparm"), "-s", os.fspath(candidate_main)], 60)
        atomic_text(paths.samba_include, include_content, mode=0o644)
        atomic_text(paths.samba_config, main_content, mode=0o644)
        runner([_tool("systemctl"), "reload", "smbd.service"], 60)
    finally:
        with contextlib.suppress(FileNotFoundError):
            candidate_include.unlink()
        with contextlib.suppress(FileNotFoundError):
            candidate_main.unlink()


def _blkid_value(partition: Path, field: str) -> str:
    try:
        result = subprocess.run(
            [_tool("blkid"), "-s", field, "-o", "value", os.fspath(partition)],
            check=True,
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
    except subprocess.SubprocessError as exc:
        raise ExecutorFailure(
            "filesystem_identity_unavailable",
            "A filesystem identity could not be read safely.",
            needs_attention=True,
        ) from exc
    value = result.stdout.strip()
    if not value or any(character.isspace() for character in value):
        raise ExecutorFailure(
            "filesystem_identity_unavailable",
            "A filesystem identity could not be read safely.",
            needs_attention=True,
        )
    return value


def _execute_actions(
    *,
    operation_id: str,
    document: dict[str, Any],
    paths: Paths,
    inventory_provider: InventoryProvider,
    runner: CommandRunner,
    journal: dict[str, Any],
    resume: bool = False,
    zfs_state_provider: ZfsStateProvider = _live_zfs_pool_state,
) -> dict[str, Any]:
    storage = document["storage"]
    topology = storage["topology"]
    devices = (
        _resume_revalidate(document, inventory_provider, paths, journal)
        if resume
        else _revalidate(document, inventory_provider, paths)
    )
    partitions: dict[str, Path] = {}
    completed: list[str] = [
        item for item in journal.get("completed_actions", []) if isinstance(item, str)
    ]

    def complete_checkpoint(checkpoint_id: str) -> None:
        if checkpoint_id not in completed:
            completed.append(checkpoint_id)
        journal["completed_actions"] = list(completed)
        journal["completed_steps"] = len(completed)
        total_steps = journal.get("total_steps")
        if isinstance(total_steps, int) and total_steps >= 0:
            journal["completed_steps"] = min(int(journal["completed_steps"]), total_steps)
        journal["current_action"] = None
        journal["updated_at"] = time.time()
        atomic_json(_journal_path(paths, operation_id), journal)

    # Older executors advanced this counter again while replaying idempotent
    # post-layout work. The durable checkpoint identifiers are authoritative.
    journal["completed_steps"] = len(completed)

    def smart_progress(progress: dict[str, Any]) -> None:
        current = journal.get("current_action")
        if isinstance(current, dict):
            current["progress"] = progress
        journal["updated_at"] = time.time()
        atomic_json(_journal_path(paths, operation_id), journal)

    for action_index, action in enumerate(storage["actions"]):
        if action["action_id"] in completed:
            continue
        action_type = action["type"]
        journal["phase"] = "Checking and preparing drives"
        journal["current_action"] = {
            "id": action["action_id"],
            "type": action_type,
            "number": action_index + 1,
            "count": len(storage["actions"]),
            "started_at": time.time(),
        }
        journal["updated_at"] = time.time()
        atomic_json(_journal_path(paths, operation_id), journal)
        identifier = action.get("device_id")
        if identifier is not None and identifier not in devices:
            raise ExecutorFailure("plan_invalid", "An action names an unselected drive.")
        if action["destructive"]:
            devices = _revalidate(document, inventory_provider, paths)
        device = _kernel_path(devices[identifier]) if isinstance(identifier, str) else None
        if action_type == "drive.identity.verify":
            pass
        elif action_type == "drive.surface.read":
            runner([_tool("badblocks"), "-sv", os.fspath(device)], 7 * 24 * 3600)
        elif action_type == "drive.smart.short":
            smart_outcome = _run_smart_test(device, "short", progress_callback=smart_progress)
            journal.setdefault("action_results", []).append(
                {"action_id": action["action_id"], "device_id": identifier, **smart_outcome}
            )
            if smart_outcome["outcome"] == "skipped":
                journal.setdefault("notices", []).append(
                    {
                        "action_id": action["action_id"],
                        "device_id": identifier,
                        "code": smart_outcome["code"],
                        "message": smart_outcome["message"],
                    }
                )
        elif action_type == "drive.smart.extended":
            smart_outcome = _run_smart_test(device, "long", progress_callback=smart_progress)
            journal.setdefault("action_results", []).append(
                {"action_id": action["action_id"], "device_id": identifier, **smart_outcome}
            )
            if smart_outcome["outcome"] == "skipped":
                journal.setdefault("notices", []).append(
                    {
                        "action_id": action["action_id"],
                        "device_id": identifier,
                        "code": smart_outcome["code"],
                        "message": smart_outcome["message"],
                    }
                )
        elif action_type == "drive.write_read.destructive":
            runner([_tool("badblocks"), "-wsv", os.fspath(device)], 14 * 24 * 3600)
        elif action_type == "disk.partition_table.create":
            table = action.get("table")
            if table not in SAFE_TABLES:
                raise ExecutorFailure(
                    "partition_table_not_supported", "The partition table is unsupported."
                )
            label = "msdos" if table == "mbr" else "gpt"
            runner([_tool("wipefs"), "--all", "--force", os.fspath(device)], 300)
            runner(
                [
                    _tool("parted"),
                    "--script",
                    os.fspath(device),
                    "mklabel",
                    label,
                    "mkpart",
                    "primary",
                    "1MiB",
                    "100%",
                ],
                300,
            )
            runner([_tool("partprobe"), os.fspath(device)], 60)
            runner([_tool("udevadm"), "settle", "--timeout=60"], 70)
            partitions[str(identifier)] = _partition_path(device)
        elif action_type == "filesystem.create":
            filesystem = action.get("filesystem")
            allocation = action.get("allocation_unit_bytes")
            format_mode = action.get("format_mode", "quick")
            if filesystem not in SAFE_FILESYSTEMS or (
                allocation is not None
                and (
                    not isinstance(allocation, int) or allocation < 512 or allocation > 1024 * 1024
                )
            ):
                raise ExecutorFailure(
                    "filesystem_options_invalid", "The filesystem options are invalid."
                )
            partition = partitions.get(str(identifier), _partition_path(device))
            runner(
                _filesystem_command(
                    str(filesystem), allocation, partition, format_mode=str(format_mode)
                ),
                24 * 3600,
            )
        elif action_type == "storage.layout.ensure" and action.get("topology") != topology:
            raise ExecutorFailure(
                "plan_invalid", "The layout action does not match the storage plan."
            )
        complete_checkpoint(str(action["action_id"]))

    if topology == "test":
        return {
            "operation_id": operation_id,
            "topology": topology,
            "selected_device_ids": list(devices),
            "mountpoint": None,
            "completed_actions": completed,
            "notices": list(journal.get("notices", [])),
            "action_results": list(journal.get("action_results", [])),
            "replayed": False,
        }

    format_document = storage.get("format") if isinstance(storage.get("format"), Mapping) else {}
    mount_options = (
        format_document.get("mount_options")
        if isinstance(format_document.get("mount_options"), list)
        else []
    )
    trim = format_document.get("trim") if isinstance(format_document.get("trim"), Mapping) else {}
    trim_enabled = trim.get("enabled") is True
    trim_mode = trim.get("mode") if trim_enabled else "disabled"
    if trim_enabled and not all(_discard_supported(device) for device in devices.values()):
        raise ExecutorFailure(
            "trim_path_changed",
            "TRIM support changed before storage execution. No discard command was issued.",
        )
    safe_options = [
        value for value in mount_options if value in {"noatime", "windows_names", "discard"}
    ]
    if trim_mode == "continuous" and "discard" not in safe_options:
        safe_options.append("discard")
    disk_mounts: list[Path] = []
    disk_mounts_by_id: dict[str, Path] = {}
    filesystem_uuids: dict[str, str] = {}
    fstab_lines: list[str] = []
    mergerfs_fstab_update: tuple[str, list[str]] | None = None
    mount_members = topology not in {"zfs", "raid", "mixed"}
    for mount_index, (identifier, disk) in enumerate(devices.items() if mount_members else ()):
        journal["phase"] = "Mounting prepared drives"
        journal["current_action"] = {
            "id": f"mount:{identifier}",
            "type": "filesystem.mount",
            "number": mount_index + 1,
            "count": len(devices),
        }
        journal["updated_at"] = time.time()
        atomic_json(_journal_path(paths, operation_id), journal)
        partition = partitions.get(identifier)
        created = partition is not None
        parts = disk.get("partitions") if isinstance(disk.get("partitions"), list) else []
        if partition is None:
            filesystem_parts = [
                part
                for part in parts
                if isinstance(part, Mapping) and isinstance(part.get("filesystem"), Mapping)
            ]
            if len(filesystem_parts) != 1 or not isinstance(
                filesystem_parts[0].get("kernel_path"), str
            ):
                raise ExecutorFailure(
                    "filesystem_identity_ambiguous",
                    "A selected drive does not have exactly one mountable filesystem.",
                )
            partition = Path(str(filesystem_parts[0]["kernel_path"]))
        filesystem_type = (
            str(format_document.get("filesystem")) if created else _blkid_value(partition, "TYPE")
        )
        if filesystem_type not in SAFE_FILESYSTEMS:
            raise ExecutorFailure(
                "filesystem_not_supported", "The selected filesystem cannot be mounted safely."
            )
        device_options = [
            option
            for option in safe_options
            if option != "windows_names" or filesystem_type == "ntfs"
        ]
        mountpoint = paths.mount_root / document_hash(identifier)[:16]
        mountpoint.mkdir(parents=True, exist_ok=True, mode=0o750)
        partition_mounts = {
            value
            for part in parts
            if isinstance(part, Mapping)
            and part.get("kernel_path") == os.fspath(partition)
            for value in part.get("mountpoints", [])
            if isinstance(value, str) and value
        }
        already_mounted = partition_mounts == {os.fspath(mountpoint)}
        if not already_mounted:
            runner(
                [
                    _tool("mount"),
                    "-t",
                    filesystem_type,
                    "-o",
                    ",".join(device_options) if device_options else "defaults",
                    os.fspath(partition),
                    os.fspath(mountpoint),
                ],
                120,
            )
        disk_mounts.append(mountpoint)
        disk_mounts_by_id[identifier] = mountpoint
        # UUID and type are resolved now; /dev/sdX names are never persisted.
        filesystem_uuid = _blkid_value(partition, "UUID")
        filesystem_uuids[identifier] = filesystem_uuid
        option_text = ",".join(device_options) if device_options else "defaults"
        fstab_lines.append(
            f"UUID={filesystem_uuid} {mountpoint} {filesystem_type} {option_text} 0 2"
        )
        complete_checkpoint(f"runtime:mount:{identifier}")

    presentation_root = _safe_mountpoint(str(document["presentation_root"]))
    layout_checkpoint = "runtime:layout"
    # ZFS persists its own mountpoint and pool geometry.  A post-layout failure
    # (for example while creating media folders) can therefore leave the pool
    # fully built while the generic fstab checkpoint is still pending.  Never
    # replay ``zpool create`` in that state: resume the remaining configuration
    # from the immutable executor journal and let the empty fstab phase complete
    # normally.  Other layouts still require their fstab checkpoint because the
    # branch/bind/md mount declarations are part of the durable layout.
    layout_is_persisted = layout_checkpoint in completed and (
        "runtime:fstab" in completed or topology == "zfs"
    )
    if not layout_is_persisted:
        journal["phase"] = "Building the selected storage layout"
        journal["current_action"] = {"id": "layout", "type": "storage.layout.apply"}
        journal["updated_at"] = time.time()
        atomic_json(_journal_path(paths, operation_id), journal)
    presentation_root.mkdir(parents=True, exist_ok=True, mode=0o750)
    if layout_is_persisted:
        pass
    elif topology in {"individual", "cache", "block", "import"}:
        if len(disk_mounts) != 1:
            raise ExecutorFailure(
                "individual_layout_ambiguous",
                "Individual storage requires exactly one selected drive.",
            )
        runner(
            [_tool("mount"), "--bind", os.fspath(disk_mounts[0]), os.fspath(presentation_root)], 120
        )
        fstab_lines.append(f"{disk_mounts[0]} {presentation_root} none bind 0 0")
    elif topology == "mergerfs":
        mergerfs = storage.get("mergerfs")
        if not isinstance(mergerfs, Mapping):
            raise ExecutorFailure(
                "mergerfs_plan_incomplete", "The combined-storage plan is incomplete."
            )
        combined = _safe_mountpoint(str(mergerfs.get("mountpoint")))
        runtime_combined = combined
        configured_branch_list: list[str] = []
        combined.mkdir(parents=True, exist_ok=True, mode=0o750)
        if mergerfs.get("mode") == "create":
            create_policy = mergerfs.get("create_policy")
            search_policy = mergerfs.get("search_policy")
            if create_policy not in {"mfs", "epmfs"} or search_policy not in {"ff", "all"}:
                raise ExecutorFailure(
                    "mergerfs_options_invalid", "The combined-storage policies are invalid."
                )
            prior_branches: list[str] = []
            options = (
                f"category.create={create_policy},category.search={search_policy},"
                "allow_other,use_ino,cache.files=off,dropcacheonclose=true"
            )
        else:
            discovery = discover_mergerfs(
                mountinfo_path=Path("/proc/self/mountinfo"), fstab_path=paths.fstab
            )
            matches = [
                item
                for item in discovery["items"]
                if item.get("id") == mergerfs.get("instance_id")
                and item.get("mountpoint") == str(combined)
                and item.get("active") is True
            ]
            if len(matches) != 1:
                raise ExecutorFailure(
                    "mergerfs_instance_drift",
                    "The existing combined-storage instance changed or is not active.",
                )
            selected_instance = matches[0]
            configured_instances = [
                item
                for item in discovery["items"]
                if item.get("active") is True
                and item.get("configured") is True
                and item.get("source") == selected_instance.get("source")
                and item.get("branches") == selected_instance.get("branches")
            ]
            if selected_instance.get("configured") is True:
                configured_instance = selected_instance
            elif len(configured_instances) == 1:
                # A public bind presentation of an active mergerFS mount appears in
                # mountinfo as another fuse.mergerfs mount with the same source and
                # branches.  Keep the reviewed public namespace immutable, but apply
                # runtime controls and persistent configuration to the one canonical
                # fstab-managed instance.
                configured_instance = configured_instances[0]
                runtime_combined = _safe_mountpoint(
                    str(configured_instance.get("mountpoint"))
                )
            else:
                raise ExecutorFailure(
                    "mergerfs_configuration_ambiguous",
                    "The reviewed combined storage alias could not be tied to one active "
                    "persistent mergerFS configuration.",
                )
            configured_branches = configured_instance.get("configured_branches")
            configured_branch_list = (
                [str(item) for item in configured_branches]
                if isinstance(configured_branches, list)
                else []
            )
            runtime_branch_list = [
                str(item) for item in selected_instance.get("branches", [])
            ]
            prior_branches = _normalize_runtime_mergerfs_branches(
                runtime_branch_list,
                configured_branch_list or runtime_branch_list,
            )
            options = ",".join(
                item
                for item in configured_instance.get("options", [])
                if isinstance(item, str) and "\x00" not in item
            )
            if not options:
                raise ExecutorFailure(
                    "mergerfs_options_invalid", "Existing mergerFS options are unavailable."
                )
        expansion = storage.get("expansion")
        expansion_configuration = (
            expansion.get("configuration") if isinstance(expansion, Mapping) else None
        )
        snapraid_role = (
            expansion_configuration.get("snapraid_role")
            if isinstance(expansion_configuration, Mapping)
            else None
        )
        if snapraid_role not in {None, "data", "parity"}:
            raise ExecutorFailure(
                "snapraid_expansion_invalid", "The reviewed SnapRAID role is invalid."
            )
        new_member_mounts = [os.fspath(item) for item in disk_mounts]
        if snapraid_role is not None and (
            mergerfs.get("mode") != "existing" or len(new_member_mounts) != 1
        ):
            raise ExecutorFailure(
                "snapraid_expansion_invalid",
                "SnapRAID expansion requires one new disk and an existing mergerFS target.",
            )
        new_branches = [] if snapraid_role == "parity" else new_member_mounts
        if set(prior_branches).intersection(new_branches):
            raise ExecutorFailure(
                "mergerfs_duplicate_branch",
                "A drive is already a member of this mergerFS instance.",
            )
        if configured_branch_list and configured_branch_list not in (
            prior_branches,
            [*prior_branches, *new_branches],
        ):
            raise ExecutorFailure(
                "mergerfs_fstab_drift",
                "The persistent mergerFS member list changed after review.",
                needs_attention=True,
            )
        branches = ":".join([*prior_branches, *new_branches])
        changed_runtime = False
        snapraid_sync_started = False
        snapraid_config_path: Path | None = None
        snapraid_original: str | None = None
        try:
            if snapraid_role is not None:
                instance_id = expansion_configuration.get("snapraid_instance_id")
                expected_digest = expansion_configuration.get("snapraid_config_sha256")
                match = (
                    re.fullmatch(r"snapraid:([A-Za-z0-9_.-]{1,128})", str(instance_id))
                    if isinstance(expected_digest, str)
                    and re.fullmatch(r"[0-9a-f]{64}", expected_digest)
                    else None
                )
                if match is None:
                    raise ExecutorFailure(
                        "snapraid_expansion_invalid",
                        "The reviewed SnapRAID configuration identity is invalid.",
                    )
                snapraid_config_path = paths.snapraid_config_root / f"{match.group(1)}.conf"
                if snapraid_config_path.parent != paths.snapraid_config_root:
                    raise ExecutorFailure(
                        "snapraid_config_invalid", "The SnapRAID configuration path is invalid."
                    )
                try:
                    snapraid_original = snapraid_config_path.read_text(encoding="utf-8")
                except OSError as exc:
                    raise ExecutorFailure(
                        "snapraid_config_unavailable",
                        "The reviewed SnapRAID configuration is unavailable.",
                    ) from exc
                if hashlib.sha256(snapraid_original.encode()).hexdigest() != expected_digest:
                    raise ExecutorFailure(
                        "snapraid_config_changed",
                        "The SnapRAID configuration changed after review.",
                    )
                updated = snapraid_expand_config(
                    snapraid_original,
                    role=snapraid_role,
                    mountpoint=new_member_mounts[0],
                )
                atomic_text(snapraid_config_path, updated, mode=0o640)
                _revalidate(document, inventory_provider, paths)
                runner(
                    [_tool("snapraid"), "-c", os.fspath(snapraid_config_path), "status"],
                    300,
                )
            if mergerfs.get("mode") == "existing" and new_branches:
                # Set the complete reviewed list instead of appending. The mergerFS
                # runtime interface is not durable, so a recovered operation may
                # revisit this phase after the branch was already activated. Exact
                # replacement makes recovery idempotent and also removes duplicate
                # entries left by an interrupted older executor.
                expand_commands = mergerfs_expand_commands(
                    str(runtime_combined), [*prior_branches, *new_branches]
                )
                for command in expand_commands:
                    runner([_tool(command.argv[0]), *command.argv[1:]], command.timeout_seconds)
                    changed_runtime = True
            elif mergerfs.get("mode") == "create":
                runner([_tool("mergerfs"), "-o", options, branches, os.fspath(combined)], 120)
            if snapraid_role is not None:
                persistent_update = (
                    (str(runtime_combined), [*prior_branches, *new_branches])
                    if new_branches
                    else None
                )
                _append_fstab(
                    paths,
                    operation_id,
                    fstab_lines,
                    mergerfs_update=persistent_update,
                )
                fstab_lines.clear()
                journal["phase"] = "Synchronizing SnapRAID protection"
                journal["current_action"] = {
                    "id": "snapraid-sync",
                    "type": "snapraid.sync",
                    "started_at": time.time(),
                }
                journal["updated_at"] = time.time()
                atomic_json(_journal_path(paths, operation_id), journal)
                _revalidate(document, inventory_provider, paths)
                snapraid_sync_started = True
                sync_arguments = [
                    _tool("snapraid"),
                    "-c",
                    os.fspath(snapraid_config_path),
                    *(["--force-full"] if snapraid_role == "parity" else []),
                    "sync",
                ]
                runner(
                    sync_arguments,
                    86_400,
                )
        except LayoutError as exc:
            if snapraid_config_path is not None and snapraid_original is not None:
                atomic_text(snapraid_config_path, snapraid_original, mode=0o640)
            if changed_runtime:
                runner(
                    [
                        _tool("setfattr"),
                        "-n",
                        "user.mergerfs.branches",
                        "-v",
                        ":".join(prior_branches),
                        os.fspath(runtime_combined / ".mergerfs"),
                    ],
                    120,
                )
            raise ExecutorFailure("snapraid_expansion_invalid", str(exc)) from exc
        except Exception as exc:
            if snapraid_sync_started:
                raise ExecutorFailure(
                    "snapraid_sync_incomplete",
                    "The expanded storage remains configured, but parity synchronization did "
                    "not complete. New files may not yet be protected.",
                    needs_attention=True,
                ) from exc
            if snapraid_config_path is not None and snapraid_original is not None:
                atomic_text(snapraid_config_path, snapraid_original, mode=0o640)
            if changed_runtime:
                runner(
                    [
                        _tool("setfattr"),
                        "-n",
                        "user.mergerfs.branches",
                        "-v",
                        ":".join(prior_branches),
                        os.fspath(runtime_combined / ".mergerfs"),
                    ],
                    120,
                )
            raise
        if (
            new_branches
            and mergerfs.get("mode") == "existing"
            and configured_instance.get("configured") is True
        ):
            mergerfs_fstab_update = (
                str(runtime_combined),
                [*prior_branches, *new_branches],
            )
        elif mergerfs.get("mode") == "create":
            dependencies = ",".join(
                f"x-systemd.requires={_fstab_encode(branch)}" for branch in new_member_mounts
            )
            fstab_lines.append(
                f"{branches} {combined} fuse.mergerfs {options},{dependencies},nofail 0 0"
            )
        if combined != presentation_root:
            runner(
                [_tool("mount"), "--bind", os.fspath(combined), os.fspath(presentation_root)], 120
            )
            fstab_lines.append(f"{combined} {presentation_root} none bind 0 0")
    elif topology in {"zfs", "raid", "mixed"}:
        options = storage.get("layout_options")
        if not isinstance(options, Mapping) or options.get("mountpoint") != str(
            document["presentation_root"]
        ):
            raise ExecutorFailure(
                "layout_mountpoint_invalid",
                "The array mountpoint does not match the reviewed storage root.",
            )
        devices = _revalidate(document, inventory_provider, paths)
        stable_paths = {
            identifier: os.fspath(_stable_path(paths, disk)) for identifier, disk in devices.items()
        }
        expansion = storage.get("expansion")
        expansion_kind = expansion.get("kind") if isinstance(expansion, Mapping) else None
        if topology == "zfs" and expansion_kind == "add_zfs_vdev":
            target = expansion.get("target")
            configuration = expansion.get("configuration")
            if not isinstance(target, Mapping) or not isinstance(configuration, Mapping):
                raise ExecutorFailure(
                    "zfs_expansion_invalid", "The reviewed ZFS expansion binding is incomplete."
                )
            instance_id = target.get("instance_id")
            pool_name = (
                str(instance_id).removeprefix("zfs:")
                if isinstance(instance_id, str) and instance_id.startswith("zfs:")
                else ""
            )
            expected_guid = configuration.get("zfs_pool_guid")
            expected_digest = configuration.get("zfs_config_sha256")
            expected_type = configuration.get("vdev_type")
            expected_width = configuration.get("vdev_width")
            expected_count = configuration.get("zfs_vdev_count")
            if (
                target.get("provider") != "zfs"
                or options.get("name") != pool_name
                or options.get("mountpoint") != target.get("mountpoint")
                or not valid_pool_guid(expected_guid)
                or not isinstance(expected_digest, str)
                or SHA256_RE.fullmatch(expected_digest) is None
                or expected_type not in {"mirror", "raidz1", "raidz2", "raidz3"}
                or not isinstance(expected_width, int)
                or not isinstance(expected_count, int)
            ):
                raise ExecutorFailure(
                    "zfs_expansion_invalid", "The reviewed ZFS pool or geometry binding is invalid."
                )

            def reviewed_state() -> dict[str, Any]:
                state = zfs_state_provider(pool_name)
                if (
                    state.get("pool_guid") != expected_guid
                    or state.get("config_sha256") != expected_digest
                    or state.get("vdev_type") != expected_type
                    or state.get("vdev_width") != expected_width
                    or state.get("vdev_count") != expected_count
                ):
                    raise ExecutorFailure(
                        "zfs_pool_changed",
                        "The existing ZFS pool identity or data-vdev topology changed "
                        "after review.",
                    )
                return state

            reviewed_state()
            try:
                commands = zfs_add_vdev_commands(
                    pool_name=pool_name,
                    vdev_type=str(expected_type),
                    device_ids=list(devices),
                    device_paths=stable_paths,
                )
            except LayoutError as exc:
                raise ExecutorFailure("zfs_expansion_invalid", str(exc)) from exc
            mutation_started = False
            try:
                for command_index, command in enumerate(commands):
                    journal["phase"] = command.phase
                    journal["current_action"] = {
                        "id": f"zfs-expand:{command_index + 1}",
                        "type": "zfs.vdev.add",
                        "pool": pool_name,
                    }
                    journal["updated_at"] = time.time()
                    atomic_json(_journal_path(paths, operation_id), journal)
                    _revalidate(document, inventory_provider, paths)
                    if command_index == 1:
                        reviewed_state()
                        mutation_started = True
                    runner([_tool(command.argv[0]), *command.argv[1:]], command.timeout_seconds)
                post_state = zfs_state_provider(pool_name)
                if (
                    post_state.get("pool_guid") != expected_guid
                    or post_state.get("vdev_type") != expected_type
                    or post_state.get("vdev_width") != expected_width
                    or post_state.get("vdev_count") != expected_count + 1
                    or post_state.get("config_sha256") == expected_digest
                ):
                    raise ExecutorFailure(
                        "zfs_expansion_verification_failed",
                        "ZFS did not report the expected additional matching data vdev.",
                        needs_attention=True,
                    )
            except ExecutorFailure as exc:
                if mutation_started and not exc.needs_attention:
                    raise ExecutorFailure(
                        "zfs_expansion_needs_attention",
                        "The ZFS add operation started, but final verification did not complete. "
                        "The existing pool was not recreated; inspect zpool status before "
                        "retrying.",
                        needs_attention=True,
                    ) from exc
                raise
            journal["current_action"] = None
        else:
            try:
                commands = layout_commands(topology, options, stable_paths)
            except LayoutError as exc:
                raise ExecutorFailure("layout_options_invalid", str(exc)) from exc
            for command in commands:
                devices = _revalidate(document, inventory_provider, paths)
                runner([_tool(command.argv[0]), *command.argv[1:]], command.timeout_seconds)
        if topology == "zfs":
            if expansion_kind != "add_zfs_vdev":
                _install_storage_timer(
                    paths,
                    unit_name=f"hoardarr-zfs-scrub-{options['name']}",
                    description=f"Scrub ZFS pool {options['name']}",
                    command=[_tool("zpool"), "scrub", str(options["name"])],
                    schedule=str(options["scrub_schedule"]),
                    runner=runner,
                )
                snapshots = options.get("snapshots", {})
                if snapshots.get("enabled") and int(snapshots.get("retention", 0)) > 0:
                    _install_storage_timer(
                        paths,
                        unit_name=f"hoardarr-zfs-snapshot-{options['name']}",
                        description=f"Snapshot ZFS pool {options['name']}",
                        command=[
                            _tool("hoardarr-zfs-snapshot"),
                            "--pool",
                            str(options["name"]),
                            "--retention",
                            str(snapshots["retention"]),
                        ],
                        schedule="daily",
                        runner=runner,
                    )
        elif topology == "raid":
            md_path = Path(f"/dev/md/{options['name']}")
            filesystem_uuid = _blkid_value(md_path, "UUID")
            filesystem_type = str(options["filesystem"])
            fstab_lines.append(
                f"UUID={filesystem_uuid} {presentation_root} {filesystem_type} noatime 0 2"
            )
        else:
            component_mounts: list[str] = []
            for component in options["components"]:
                component_options = component["options"]
                component_mount = str(component_options["mountpoint"])
                component_mounts.append(component_mount)
                if component["topology"] == "raid":
                    md_path = Path(f"/dev/md/{component_options['name']}")
                    filesystem_uuid = _blkid_value(md_path, "UUID")
                    fstab_lines.append(
                        f"UUID={filesystem_uuid} {component_mount} "
                        f"{component_options['filesystem']} noatime 0 2"
                    )
            mixed_options = (
                f"category.create={options['create_policy']},"
                f"category.search={options['search_policy']},"
                "allow_other,use_ino,cache.files=off,dropcacheonclose=true"
            )
            fstab_lines.append(
                f"{':'.join(component_mounts)} {presentation_root} "
                f"fuse.mergerfs {mixed_options},nofail 0 0"
            )
    elif topology == "snapraid":
        options = storage.get("layout_options")
        if not isinstance(options, Mapping) or options.get("mountpoint") != str(presentation_root):
            raise ExecutorFailure(
                "layout_mountpoint_invalid",
                "The SnapRAID mountpoint does not match the reviewed storage root.",
            )
        paths.snapraid_config_root.mkdir(parents=True, exist_ok=True, mode=0o750)
        config = paths.snapraid_config_root / f"{options['name']}.conf"
        if config.parent != paths.snapraid_config_root:
            raise ExecutorFailure(
                "snapraid_config_invalid", "The SnapRAID configuration path is invalid."
            )
        try:
            config_text = snapraid_config(
                options,
                {identifier: str(mount) for identifier, mount in disk_mounts_by_id.items()},
            )
        except LayoutError as exc:
            raise ExecutorFailure("snapraid_config_invalid", str(exc)) from exc
        atomic_text(config, config_text, mode=0o640)
        try:
            commands = layout_commands(topology, options, {})
        except LayoutError as exc:
            raise ExecutorFailure("layout_options_invalid", str(exc)) from exc
        for command in commands:
            devices = _revalidate(document, inventory_provider, paths)
            runner([_tool(command.argv[0]), *command.argv[1:]], command.timeout_seconds)
        data_branches = ":".join(str(disk_mounts_by_id[item]) for item in options["data"])
        runner(
            [
                _tool("mergerfs"),
                "-o",
                "category.create=mfs,category.search=ff,allow_other,use_ino",
                data_branches,
                str(presentation_root),
            ],
            120,
        )
        fstab_lines.append(
            f"{data_branches} {presentation_root} fuse.mergerfs "
            "category.create=mfs,category.search=ff,allow_other,use_ino,nofail 0 0"
        )
        _install_storage_timer(
            paths,
            unit_name=f"hoardarr-snapraid-sync-{options['name']}",
            description=f"Sync SnapRAID {options['name']}",
            command=[_tool("snapraid"), "-c", os.fspath(config), "sync"],
            schedule=str(options["sync_schedule"]),
            runner=runner,
        )
        _install_storage_timer(
            paths,
            unit_name=f"hoardarr-snapraid-scrub-{options['name']}",
            description=f"Scrub SnapRAID {options['name']}",
            command=[
                _tool("snapraid"),
                "-c",
                os.fspath(config),
                "-p",
                str(options["scrub_percent"]),
                "scrub",
            ],
            schedule=str(options["scrub_schedule"]),
            runner=runner,
        )

    if trim_enabled:
        if topology == "zfs":
            runner([_tool("zpool"), "set", "autotrim=on", str(options["name"])], 120)
        elif trim_mode == "continuous" and topology == "raid":
            runner([_tool("mount"), "-o", "remount,discard", str(presentation_root)], 120)
        elif trim_mode in {"conditional", "periodic"}:
            trim_targets = [presentation_root] if topology == "raid" else list(disk_mounts)
            for index, target in enumerate(trim_targets, start=1):
                _install_storage_timer(
                    paths,
                    unit_name=f"hoardarr-fstrim-{document_hash(str(target))[:16]}-{index}",
                    description=f"Trim storage mounted at {target}",
                    command=[_tool("fstrim"), "--verbose", str(target)],
                    schedule="weekly",
                    runner=runner,
                )

    complete_checkpoint(layout_checkpoint)
    account = storage.get("service_account", {})
    directories_checkpoint = "runtime:directories"
    if directories_checkpoint not in completed:
        journal["phase"] = "Creating media and download folders"
        journal["current_action"] = {"id": "directories", "type": "directory.ensure"}
        journal["updated_at"] = time.time()
        atomic_json(_journal_path(paths, operation_id), journal)
        _ensure_storage_directory_access(
            presentation_root,
            document.get("actions", {}).get("directories", []),
            str(account.get("username")),
        )
        if topology in {"mergerfs", "snapraid", "mixed"} and document.get("actions", {}).get(
            "directories"
        ):
            _ensure_mergerfs_branch_traversal(paths.mount_root, str(account.get("username")))
        complete_checkpoint(directories_checkpoint)

    fstab_checkpoint = "runtime:fstab"
    if fstab_checkpoint not in completed:
        journal["phase"] = "Saving automatic mount configuration"
        journal["current_action"] = {"id": "fstab", "type": "mount.configuration.save"}
        journal["updated_at"] = time.time()
        atomic_json(_journal_path(paths, operation_id), journal)
        _append_fstab(
            paths,
            operation_id,
            fstab_lines,
            mergerfs_update=mergerfs_fstab_update,
        )
        complete_checkpoint(fstab_checkpoint)
    quarantine_checkpoint = "runtime:managed-drive-allowlist"
    if (
        paths.managed_udev_rule is not None
        and paths.managed_storage_state is not None
        and quarantine_checkpoint not in completed
    ):
        journal["phase"] = "Releasing approved drives from startup quarantine"
        journal["current_action"] = {
            "id": "managed-drive-allowlist",
            "type": "drive.quarantine.release",
        }
        journal["updated_at"] = time.time()
        atomic_json(_journal_path(paths, operation_id), journal)
        persist_managed_identities(
            (managed_identity_from_device(device) for device in devices.values()),
            state_path=paths.managed_storage_state,
            rule_path=paths.managed_udev_rule,
        )
        runner([_tool("udevadm"), "control", "--reload-rules"], 60)
        trigger_paths = {os.fspath(_kernel_path(device)) for device in devices.values()}
        trigger_paths.update(os.fspath(partition) for partition in partitions.values())
        for kernel_path in sorted(trigger_paths):
            runner(
                [
                    _tool("udevadm"),
                    "trigger",
                    "--action=change",
                    "--settle",
                    f"--name-match={kernel_path}",
                ],
                120,
            )
        complete_checkpoint(quarantine_checkpoint)
    connectivity_actions = document.get("actions", {}).get("connectivity", [])
    connectivity_checkpoint = "runtime:smb-shares"
    if connectivity_actions and connectivity_checkpoint not in completed:
        journal["phase"] = "Configuring file access"
        journal["current_action"] = {"id": "smb-shares", "type": "smb.share.ensure"}
        journal["updated_at"] = time.time()
        atomic_json(_journal_path(paths, operation_id), journal)
        _ensure_smb_shares(
            paths,
            operation_id,
            connectivity_actions,
            str(account.get("username")),
            runner,
        )
        complete_checkpoint(connectivity_checkpoint)
    return {
        "operation_id": operation_id,
        "topology": topology,
        "selected_device_ids": list(devices),
        "mountpoint": os.fspath(presentation_root),
        "filesystem_uuids": filesystem_uuids,
        "member_mountpoints": {
            identifier: os.fspath(mountpoint)
            for identifier, mountpoint in disk_mounts_by_id.items()
        },
        "completed_actions": completed,
        "notices": list(journal.get("notices", [])),
        "replayed": False,
    }


def reconcile_storage_access(
    request: Mapping[str, Any],
    *,
    paths: Paths | None = None,
) -> dict[str, Any]:
    """Reapply an immutable storage plan's folder access without touching storage data."""

    paths = paths or Paths()
    expected = {"operation", "operation_id", "plan_sha256", "document"}
    operation_id = request.get("operation_id")
    plan_sha = request.get("plan_sha256")
    document = request.get("document")
    if (
        set(request) != expected
        or request.get("operation") != "reconcile_storage_access"
        or not isinstance(operation_id, str)
        or not UUID_RE.fullmatch(operation_id)
        or not isinstance(plan_sha, str)
        or not SHA256_RE.fullmatch(plan_sha)
        or not isinstance(document, dict)
        or document_hash(document) != plan_sha
    ):
        raise ExecutorFailure(
            "storage_access_request_invalid", "The storage access request is invalid."
        )
    presentation_value = document.get("presentation_root")
    storage = document.get("storage")
    actions = document.get("actions")
    if (
        not isinstance(presentation_value, str)
        or not isinstance(storage, Mapping)
        or not isinstance(actions, Mapping)
        or not isinstance(actions.get("directories"), list)
    ):
        raise ExecutorFailure(
            "storage_access_request_invalid", "The storage access request is invalid."
        )
    presentation_root = _safe_mountpoint(presentation_value)
    if not presentation_root.is_mount():
        raise ExecutorFailure(
            "storage_presentation_unavailable",
            "The managed storage is not mounted. No permissions were changed.",
        )
    account = storage.get("service_account")
    username = account.get("username") if isinstance(account, Mapping) else None
    if not isinstance(username, str):
        raise ExecutorFailure(
            "storage_access_account_invalid", "The planned file-access account is invalid."
        )
    applied = _ensure_storage_directory_access(
        presentation_root,
        actions["directories"],
        username,
    )
    topology = storage.get("topology")
    mergerfs_mountpoint: str | None = None
    if topology == "mergerfs":
        mergerfs = storage.get("mergerfs")
        value = mergerfs.get("mountpoint") if isinstance(mergerfs, Mapping) else None
        if isinstance(value, str):
            mergerfs_mountpoint = os.fspath(_safe_mountpoint(value))
    elif topology in {"snapraid", "mixed"}:
        mergerfs_mountpoint = os.fspath(presentation_root)
    mount_configuration_updated = False
    if mergerfs_mountpoint is not None:
        mount_configuration_updated = _ensure_mergerfs_allow_other(
            paths.fstab, mergerfs_mountpoint
        )
        branch_roots_reconciled = _ensure_mergerfs_branch_traversal(paths.mount_root, username)
    else:
        branch_roots_reconciled = []
    return {
        "operation_id": operation_id,
        "mountpoint": os.fspath(presentation_root),
        "username": username,
        "directories_reconciled": applied,
        "branch_roots_reconciled": branch_roots_reconciled,
        "mount_configuration_updated": mount_configuration_updated,
        "activation": "next_mount" if mount_configuration_updated else "active",
        "replayed": False,
    }


def apply_storage_plan(
    request: Mapping[str, Any],
    *,
    paths: Paths | None = None,
    inventory_provider: InventoryProvider | None = None,
    runner: CommandRunner = _run,
    zfs_state_provider: ZfsStateProvider = _live_zfs_pool_state,
) -> dict[str, Any]:
    paths = paths or Paths()
    resume = request.get("operation") == "resume_storage_plan"
    operation_id, plan_sha, document, _approval = _validate_plan(
        request,
        operation="resume_storage_plan" if resume else "apply_storage_plan",
    )
    validate_quarantine(paths.quarantine_marker)
    try:
        paths.transaction_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        details = paths.transaction_root.lstat()
    except OSError as exc:
        raise ExecutorFailure(
            "transaction_journal_unavailable",
            "Storage activity tracking could not be prepared. No storage action was started.",
            needs_attention=True,
        ) from exc
    if not stat.S_ISDIR(details.st_mode) or (
        os.name != "nt" and (details.st_uid != _executor_uid() or details.st_mode & 0o077)
    ):
        raise ExecutorFailure(
            "transaction_journal_unsafe",
            "Storage activity tracking has unsafe ownership or permissions. "
            "No storage action was started.",
            needs_attention=True,
        )
    provider = inventory_provider or (lambda: _live_inventory(paths))
    journal_path = _journal_path(paths, operation_id)
    if resume:
        resume_journal = _load_resume_journal(journal_path, plan_sha)
    else:
        prior = _load_prior_journal(journal_path, plan_sha)
        if prior is not None:
            return {**prior, "replayed": True}
        _preflight_storage_tools(document)
    storage = document["storage"]
    selected_ids = list(storage["snapshot_binding"]["selected_device_ids"])
    with _device_locks(paths, selected_ids):
        # Recheck after locks; another operation may have changed activation or identity.
        if resume:
            _resume_revalidate(document, provider, paths, resume_journal)
            journal = dict(resume_journal)
            journal["state"] = "running"
            journal["phase"] = "Resuming storage build from the last safe checkpoint"
            journal["current_action"] = None
            journal["updated_at"] = time.time()
            notices = journal.setdefault("notices", [])
            if not any(
                isinstance(item, Mapping) and item.get("code") == "storage_build_resumed"
                for item in notices
            ):
                notices.append(
                    {
                        "code": "storage_build_resumed",
                        "message": "Storage execution resumed from its durable checkpoint.",
                    }
                )
            atomic_json(journal_path, journal)
        else:
            _revalidate(document, provider, paths)
            journal = {
                "schema_version": 1,
                "operation_id": operation_id,
                "plan_sha256": plan_sha,
                "state": "running",
                "started_at": time.time(),
                "updated_at": time.time(),
                "completed_actions": [],
                "notices": [],
                "action_results": [],
                "phase": "Validating plan and drive identities",
                "completed_steps": 0,
                "total_steps": (
                    len(storage["actions"])
                    if storage["topology"] == "test"
                    else len(storage["actions"])
                    + len(selected_ids)
                    + 3
                    + (
                        1
                        if paths.managed_udev_rule is not None
                        and paths.managed_storage_state is not None
                        else 0
                    )
                    + (1 if document.get("actions", {}).get("connectivity") else 0)
                ),
                "current_action": None,
            }
            atomic_json(journal_path, journal)
        selected_devices = {
            str(item.get("id")): item
            for item in storage.get("selected_devices", [])
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        }
        atomic_json(
            _work_path(paths, operation_id),
            {
                "schema_version": 1,
                "operation_id": operation_id,
                "actions": [
                    {
                        "id": action["action_id"],
                        "type": action["type"],
                        "device_id": action.get("device_id"),
                        "device": selected_devices.get(str(action.get("device_id")), {}).get(
                            "kernel_path"
                        ),
                        "capacity_bytes": selected_devices.get(
                            str(action.get("device_id")), {}
                        ).get("capacity_bytes"),
                    }
                    for action in storage["actions"]
                ],
            },
        )
        try:
            result = _execute_actions(
                operation_id=operation_id,
                document=document,
                paths=paths,
                inventory_provider=provider,
                runner=runner,
                journal=journal,
                resume=resume,
                zfs_state_provider=zfs_state_provider,
            )
        except Exception:
            journal["state"] = "needs_attention"
            journal["updated_at"] = time.time()
            atomic_json(journal_path, journal)
            raise
        journal["state"] = "succeeded"
        journal["phase"] = "Storage build completed"
        journal["completed_steps"] = journal["total_steps"]
        journal["current_action"] = None
        journal["updated_at"] = time.time()
        journal["result"] = result
        atomic_json(journal_path, journal)
        return result


def apply_storage_volume(
    request: Mapping[str, Any],
    *,
    paths: Paths | None = None,
    runner: CommandRunner = _run,
    zfs_state_provider: ZfsStateProvider = _live_zfs_pool_state,
    zfs_resource_provider: ZfsResourceProvider = _live_zfs_resource_state,
) -> dict[str, Any]:
    expected = {"operation", "operation_id", "plan_sha256", "plan", "confirmation_sha256"}
    operation_id = request.get("operation_id")
    plan_sha = request.get("plan_sha256")
    raw_plan = request.get("plan")
    if (
        set(request) != expected
        or request.get("operation") != "apply_storage_volume"
        or not isinstance(operation_id, str)
        or not UUID_RE.fullmatch(operation_id)
        or not isinstance(plan_sha, str)
        or not SHA256_RE.fullmatch(plan_sha)
        or not isinstance(raw_plan, dict)
        or raw_plan.get("plan_sha256") != plan_sha
        or request.get("confirmation_sha256") != document_hash({"confirmation": "CREATE"})
    ):
        raise ExecutorFailure(
            "volume_confirmation_missing", "Exact volume creation confirmation is required."
        )
    try:
        plan = validate_guided_volume_plan(raw_plan)
        command = volume_create_command(plan)
    except VolumePlanError as exc:
        raise ExecutorFailure(exc.code, str(exc)) from exc

    paths = paths or Paths()
    validate_quarantine(paths.quarantine_marker)
    try:
        paths.transaction_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        details = paths.transaction_root.lstat()
    except OSError as exc:
        raise ExecutorFailure(
            "transaction_journal_unavailable",
            "Storage activity tracking could not be prepared. No volume was created.",
        ) from exc
    if not stat.S_ISDIR(details.st_mode) or (
        os.name != "nt" and (details.st_uid != _executor_uid() or details.st_mode & 0o077)
    ):
        raise ExecutorFailure(
            "transaction_journal_unsafe",
            "Storage activity tracking is unsafe. No volume was created.",
        )
    journal_path = _journal_path(paths, operation_id)
    prior = _load_prior_journal(journal_path, plan_sha)
    if prior is not None:
        return {**prior, "replayed": True}

    parent = plan["parent"]
    state = zfs_state_provider(str(parent["pool_name"]))
    if state.get("pool_guid") != parent["pool_guid"]:
        raise ExecutorFailure(
            "zfs_pool_identity_changed",
            "The ZFS pool identity changed after review. No volume was created.",
        )
    journal: dict[str, Any] = {
        "schema_version": 1,
        "operation_id": operation_id,
        "plan_sha256": plan_sha,
        "state": "running",
        "phase": "Creating logical storage",
        "completed_steps": 0,
        "total_steps": 2,
        "current_action": {"id": "volume:create", "type": "zfs.create"},
        "started_at": time.time(),
        "updated_at": time.time(),
    }
    atomic_json(journal_path, journal)
    try:
        runner([_tool(command[0]), *command[1:]], 300)
        journal.update(
            {
                "phase": "Verifying logical storage",
                "completed_steps": 1,
                "current_action": {"id": "volume:verify", "type": "zfs.verify"},
                "updated_at": time.time(),
            }
        )
        atomic_json(journal_path, journal)
        current = zfs_state_provider(str(parent["pool_name"]))
        if current.get("pool_guid") != parent["pool_guid"]:
            raise ExecutorFailure(
                "zfs_pool_identity_changed",
                "The parent ZFS pool identity changed during volume creation.",
                needs_attention=True,
            )
        resource = zfs_resource_provider(str(plan["provider_resource_id"]))
        expected_type = "volume" if plan["resource_type"] == "zvol" else "filesystem"
        if resource.get("type") != expected_type or not valid_pool_guid(resource.get("guid")):
            raise ExecutorFailure(
                "zfs_resource_verification_failed",
                "The created ZFS resource did not match the reviewed resource type.",
                needs_attention=True,
            )
    except Exception:
        journal.update({"state": "needs_attention", "updated_at": time.time()})
        atomic_json(journal_path, journal)
        raise

    properties = plan["properties"]
    volume = {
        "provider": "zfs",
        "resource_type": plan["resource_type"],
        "provider_resource_id": plan["provider_resource_id"],
        "name": plan["name"],
        "presentation": plan["presentation"],
        "mountpoint": properties.get("mountpoint"),
        "device_path": (
            f"/dev/zvol/{plan['provider_resource_id']}" if plan["resource_type"] == "zvol" else None
        ),
        "filesystem_type": "zfs" if plan["resource_type"] == "dataset" else None,
        "filesystem_uuid": resource["guid"] if plan["resource_type"] == "dataset" else None,
        "size_bytes": plan["size_bytes"],
        "lifecycle_state": "active",
        "config": {"purpose": plan["purpose"], "provider_guid": resource["guid"], **properties},
        "capabilities": {
            "snapshot": {"support": "supported", "availability": "available"},
            "quota": {"support": "supported", "availability": "available"},
            "reservation": {"support": "supported", "availability": "available"},
            "clone": {"support": "supported", "availability": "available"},
            **(
                {
                    "thin_provisioning": {
                        "support": "supported",
                        "availability": "available",
                    }
                }
                if plan["resource_type"] == "zvol"
                else {}
            ),
            "replication": {
                "support": "supported",
                "availability": "temporarily_unavailable",
                "constraints": {"reason": "No replication target is configured."},
            },
        },
    }
    result = {
        "operation_id": operation_id,
        "provider_resource_id": plan["provider_resource_id"],
        "volume": volume,
        "replayed": False,
    }
    journal.update(
        {
            "state": "succeeded",
            "phase": "Logical storage created",
            "completed_steps": 2,
            "current_action": None,
            "result": result,
            "updated_at": time.time(),
        }
    )
    atomic_json(journal_path, journal)
    return result


def apply_storage_volume_snapshot(
    request: Mapping[str, Any],
    *,
    paths: Paths | None = None,
    runner: CommandRunner = _run,
    zfs_resource_provider: ZfsResourceProvider = _live_zfs_resource_state,
    zfs_snapshot_provider: ZfsSnapshotProvider = _live_zfs_snapshot_state,
) -> dict[str, Any]:
    expected = {"operation", "operation_id", "plan_sha256", "plan", "confirmation_sha256"}
    operation_id = request.get("operation_id")
    plan_sha = request.get("plan_sha256")
    raw_plan = request.get("plan")
    if (
        set(request) != expected
        or request.get("operation") != "apply_storage_volume_snapshot"
        or not isinstance(operation_id, str)
        or not UUID_RE.fullmatch(operation_id)
        or not isinstance(plan_sha, str)
        or not SHA256_RE.fullmatch(plan_sha)
        or not isinstance(raw_plan, dict)
        or raw_plan.get("plan_sha256") != plan_sha
    ):
        raise ExecutorFailure("snapshot_confirmation_missing", "The snapshot request is invalid.")
    try:
        plan = validate_snapshot_plan(raw_plan)
        command = snapshot_command(plan)
    except SnapshotPlanError as exc:
        raise ExecutorFailure(exc.code, str(exc)) from exc
    if request.get("confirmation_sha256") != document_hash(
        {"confirmation": plan["confirmation"]}
    ):
        raise ExecutorFailure(
            "snapshot_confirmation_missing", "Exact snapshot confirmation is required."
        )

    paths = paths or Paths()
    validate_quarantine(paths.quarantine_marker)
    try:
        paths.transaction_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        details = paths.transaction_root.lstat()
    except OSError as exc:
        raise ExecutorFailure(
            "transaction_journal_unavailable",
            "Snapshot activity tracking could not be prepared. No provider action was started.",
        ) from exc
    if not stat.S_ISDIR(details.st_mode) or (
        os.name != "nt" and (details.st_uid != _executor_uid() or details.st_mode & 0o077)
    ):
        raise ExecutorFailure(
            "transaction_journal_unsafe",
            "Snapshot activity tracking is unsafe. No provider action was started.",
        )
    journal_path = _journal_path(paths, operation_id)
    prior = _load_prior_journal(journal_path, plan_sha)
    if prior is not None:
        return {**prior, "replayed": True}

    volume = plan["volume"]
    snapshot = plan["snapshot"]
    current = zfs_resource_provider(str(volume["provider_resource_id"]))
    if current.get("guid") != volume["provider_guid"]:
        raise ExecutorFailure(
            "snapshot_volume_identity_changed",
            "The ZFS storage identity changed after review. No snapshot action was run.",
        )
    before = zfs_snapshot_provider(str(snapshot["provider_snapshot_id"]))
    if plan["action"] == "create" and before is not None:
        raise ExecutorFailure("snapshot_exists", "A snapshot with this name already exists.")
    if plan["action"] != "create" and (
        before is None or before.get("guid") != snapshot["provider_guid"]
    ):
        raise ExecutorFailure(
            "snapshot_identity_changed",
            "The selected snapshot identity changed after review. No snapshot action was run.",
        )

    journal: dict[str, Any] = {
        "schema_version": 1,
        "operation_id": operation_id,
        "plan_sha256": plan_sha,
        "state": "running",
        "phase": f"Running snapshot {plan['action']}",
        "completed_steps": 0,
        "total_steps": 2,
        "current_action": {"id": f"snapshot:{plan['action']}", "type": command[1]},
        "started_at": time.time(),
        "updated_at": time.time(),
    }
    atomic_json(journal_path, journal)
    try:
        runner([_tool(command[0]), *command[1:]], 300)
        journal.update(
            {
                "phase": "Verifying provider state",
                "completed_steps": 1,
                "current_action": {"id": "snapshot:verify", "type": "zfs.verify"},
                "updated_at": time.time(),
            }
        )
        atomic_json(journal_path, journal)
        current = zfs_resource_provider(str(volume["provider_resource_id"]))
        if current.get("guid") != volume["provider_guid"]:
            raise ExecutorFailure(
                "snapshot_volume_identity_changed",
                "The ZFS storage identity changed during the snapshot action.",
                needs_attention=True,
            )
        observed = zfs_snapshot_provider(str(snapshot["provider_snapshot_id"]))
        if plan["action"] == "delete":
            if observed is not None:
                raise ExecutorFailure(
                    "snapshot_delete_verification_failed",
                    "The selected snapshot still exists after deletion.",
                    needs_attention=True,
                )
        elif observed is None or (
            plan["action"] != "create" and observed.get("guid") != snapshot["provider_guid"]
        ):
            raise ExecutorFailure(
                "snapshot_verification_failed",
                "The provider snapshot did not match the reviewed identity.",
                needs_attention=True,
            )
        clone_resource = None
        if plan["action"] == "clone":
            clone_resource = zfs_resource_provider(str(plan["target_resource_id"]))
            expected_type = "filesystem" if volume["resource_type"] == "dataset" else "volume"
            if clone_resource.get("type") != expected_type or (
                plan["target_mountpoint"] is not None
                and clone_resource.get("mountpoint") != plan["target_mountpoint"]
            ):
                raise ExecutorFailure(
                    "snapshot_clone_verification_failed",
                    "The provider clone presentation did not match the reviewed plan.",
                    needs_attention=True,
                )
    except Exception:
        journal.update({"state": "needs_attention", "updated_at": time.time()})
        atomic_json(journal_path, journal)
        raise

    result: dict[str, Any] = {
        "operation_id": operation_id,
        "action": plan["action"],
        "snapshot": {
            "provider_snapshot_id": snapshot["provider_snapshot_id"],
            "snapshot_name": snapshot["snapshot_name"],
            "provider_guid": (
                None if plan["action"] == "delete" else str(observed["guid"])
            ),
            "detail": {} if observed is None else dict(observed),
        },
        "replayed": False,
    }
    if clone_resource is not None:
        target = str(plan["target_resource_id"])
        result["clone_volume"] = {
            "provider": "zfs",
            "resource_type": volume["resource_type"],
            "provider_resource_id": target,
            "name": target.split("/", 1)[-1],
            "presentation": volume["presentation"],
            "mountpoint": clone_resource.get("mountpoint"),
            "device_path": f"/dev/zvol/{target}" if volume["resource_type"] == "zvol" else None,
            "filesystem_type": "zfs" if volume["resource_type"] == "dataset" else None,
            "filesystem_uuid": (
                clone_resource.get("guid") if volume["resource_type"] == "dataset" else None
            ),
            "size_bytes": None,
            "lifecycle_state": "active",
            "config": {
                "clone_of": snapshot["provider_snapshot_id"],
                "provider_guid": clone_resource.get("guid"),
            },
        }
    journal.update(
        {
            "state": "succeeded",
            "phase": "Snapshot action completed",
            "completed_steps": 2,
            "current_action": None,
            "result": result,
            "updated_at": time.time(),
        }
    )
    atomic_json(journal_path, journal)
    return result


def _zfs_bytes(value: object) -> int:
    text = str(value or "").strip().lower()
    if text in {"none", "-", "0"}:
        return 0
    if not text.isdigit():
        raise ExecutorFailure(
            "volume_capacity_verification_failed",
            "The provider returned a malformed capacity property.",
            needs_attention=True,
        )
    return int(text)


def apply_storage_volume_capacity(
    request: Mapping[str, Any],
    *,
    paths: Paths | None = None,
    runner: CommandRunner = _run,
    zfs_resource_provider: ZfsResourceProvider = _live_zfs_resource_state,
) -> dict[str, Any]:
    expected = {"operation", "operation_id", "plan_sha256", "plan", "confirmation_sha256"}
    operation_id = request.get("operation_id")
    plan_sha = request.get("plan_sha256")
    raw_plan = request.get("plan")
    if (
        set(request) != expected
        or request.get("operation") != "apply_storage_volume_capacity"
        or not isinstance(operation_id, str)
        or not UUID_RE.fullmatch(operation_id)
        or not isinstance(plan_sha, str)
        or not SHA256_RE.fullmatch(plan_sha)
        or not isinstance(raw_plan, dict)
        or raw_plan.get("plan_sha256") != plan_sha
    ):
        raise ExecutorFailure("volume_capacity_request_invalid", "The capacity request is invalid.")
    try:
        plan = validate_capacity_plan(raw_plan)
        command = capacity_command(plan)
    except CapacityPlanError as exc:
        raise ExecutorFailure(exc.code, str(exc)) from exc
    if request.get("confirmation_sha256") != document_hash(
        {"confirmation": plan["confirmation"]}
    ):
        raise ExecutorFailure(
            "volume_capacity_confirmation_missing", "Exact capacity confirmation is required."
        )

    paths = paths or Paths()
    validate_quarantine(paths.quarantine_marker)
    try:
        paths.transaction_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        details = paths.transaction_root.lstat()
    except OSError as exc:
        raise ExecutorFailure(
            "transaction_journal_unavailable",
            "Capacity activity tracking could not be prepared. No provider action was started.",
        ) from exc
    if not stat.S_ISDIR(details.st_mode) or (
        os.name != "nt" and (details.st_uid != _executor_uid() or details.st_mode & 0o077)
    ):
        raise ExecutorFailure(
            "transaction_journal_unsafe",
            "Capacity activity tracking is unsafe. No provider action was started.",
        )
    journal_path = _journal_path(paths, operation_id)
    prior = _load_prior_journal(journal_path, plan_sha)
    if prior is not None:
        return {**prior, "replayed": True}

    volume = plan["volume"]
    current = zfs_resource_provider(str(volume["provider_resource_id"]))
    expected_type = "filesystem" if volume["resource_type"] == "dataset" else "volume"
    if current.get("guid") != volume["provider_guid"] or current.get("type") != expected_type:
        raise ExecutorFailure(
            "volume_capacity_identity_changed",
            "The provider storage identity changed after review. No capacity setting was changed.",
        )
    journal: dict[str, Any] = {
        "schema_version": 1,
        "operation_id": operation_id,
        "plan_sha256": plan_sha,
        "state": "running",
        "phase": "Applying provider capacity limits",
        "completed_steps": 0,
        "total_steps": 2,
        "current_action": {"id": "capacity:set", "type": "zfs.set"},
        "started_at": time.time(),
        "updated_at": time.time(),
    }
    atomic_json(journal_path, journal)
    try:
        runner([_tool(command[0]), *command[1:]], 300)
        journal.update(
            {
                "phase": "Verifying provider capacity limits",
                "completed_steps": 1,
                "current_action": {"id": "capacity:verify", "type": "zfs.get"},
                "updated_at": time.time(),
            }
        )
        atomic_json(journal_path, journal)
        observed = zfs_resource_provider(str(volume["provider_resource_id"]))
        if observed.get("guid") != volume["provider_guid"] or observed.get("type") != expected_type:
            raise ExecutorFailure(
                "volume_capacity_identity_changed",
                "The provider storage identity changed during capacity verification.",
                needs_attention=True,
            )
        if volume["resource_type"] == "dataset":
            quota = _zfs_bytes(observed.get("quota"))
            reservation = _zfs_bytes(observed.get("reservation"))
            if (
                quota != plan["target"]["quota_bytes"]
                or reservation != plan["target"]["reservation_bytes"]
            ):
                raise ExecutorFailure(
                    "volume_capacity_verification_failed",
                    "The provider did not apply the reviewed quota and reservation.",
                    needs_attention=True,
                )
            limits = {
                "quota_bytes": quota,
                "reservation_bytes": reservation,
                "thin_provisioned": None,
            }
        else:
            reserved = _zfs_bytes(observed.get("refreservation"))
            thin = reserved == 0
            if thin != plan["target"]["thin_provisioned"]:
                raise ExecutorFailure(
                    "volume_capacity_verification_failed",
                    "The provider did not apply the reviewed allocation mode.",
                    needs_attention=True,
                )
            limits = {
                "quota_bytes": None,
                "reservation_bytes": reserved,
                "thin_provisioned": thin,
            }
    except Exception:
        journal.update({"state": "needs_attention", "updated_at": time.time()})
        atomic_json(journal_path, journal)
        raise
    result = {
        "operation_id": operation_id,
        "provider_resource_id": volume["provider_resource_id"],
        "capacity_limits": limits,
        "allocated_bytes": _zfs_bytes(observed.get("used")),
        "available_bytes": _zfs_bytes(observed.get("available")),
        "replayed": False,
    }
    journal.update(
        {
            "state": "succeeded",
            "phase": "Provider capacity limits verified",
            "completed_steps": 2,
            "current_action": None,
            "result": result,
            "updated_at": time.time(),
        }
    )
    atomic_json(journal_path, journal)
    return result


def apply_device_maintenance(
    request: Mapping[str, Any],
    *,
    paths: Paths | None = None,
    inventory_provider: InventoryProvider | None = None,
    runner: CommandRunner = _run,
    status_probe: CommandProbe = _capture,
) -> dict[str, Any]:
    if set(request) != {
        "operation",
        "operation_id",
        "plan_sha256",
        "plan",
        "confirmation_sha256",
    }:
        raise ExecutorFailure("request_invalid", "The storage request is invalid.")
    operation_id = request.get("operation_id")
    plan_sha = request.get("plan_sha256")
    plan = request.get("plan")
    if (
        request.get("operation") != "apply_device_maintenance"
        or not isinstance(operation_id, str)
        or not UUID_RE.fullmatch(operation_id)
        or not isinstance(plan_sha, str)
        or not SHA256_RE.fullmatch(plan_sha)
        or not isinstance(plan, dict)
        or document_hash(plan) != plan_sha
        or request.get("confirmation_sha256") != document_hash({"confirmation": "I AGREE"})
    ):
        raise ExecutorFailure(
            "destructive_consent_missing", "Exact destructive approval is required."
        )
    try:
        validate_maintenance_plan(plan)
    except MaintenanceError as exc:
        raise ExecutorFailure(exc.code, str(exc)) from exc
    paths = paths or Paths()
    validate_quarantine(paths.quarantine_marker)
    provider = inventory_provider or (lambda: _live_inventory(paths))
    device = dict(plan["device"])
    document = {
        "storage": {
            "selected_devices": [device],
            "snapshot_binding": {
                "selected_device_ids": [device["id"]],
                "device_binding_sha256": document_hash([device]),
            },
        }
    }

    def maintenance_live(*, converted: bool = False) -> dict[str, Mapping[str, Any]]:
        if not converted:
            return _selected_live_devices(document, provider())
        live_map = _device_map(provider())
        current = live_map.get(str(device["id"]))
        if current is None:
            raise ExecutorFailure(
                "drive_identity_changed", "The selected drive is no longer present."
            )
        review = _review_document(current)
        immutable_fields = [
            field
            for field in IDENTITY_FIELDS
            if field not in {"logical_sector_bytes", "physical_sector_bytes"}
        ]
        if any(device.get(field) != review.get(field) for field in immutable_fields):
            raise ExecutorFailure(
                "drive_identity_changed",
                "The selected drive no longer matches the reviewed identity.",
            )
        return {str(device["id"]): current}

    try:
        paths.transaction_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        transaction_details = paths.transaction_root.lstat()
    except OSError as exc:
        raise ExecutorFailure(
            "transaction_journal_unavailable",
            "Storage activity tracking could not be prepared. No storage action was started.",
            needs_attention=True,
        ) from exc
    if not stat.S_ISDIR(transaction_details.st_mode) or (
        os.name != "nt"
        and (transaction_details.st_uid != _executor_uid() or transaction_details.st_mode & 0o077)
    ):
        raise ExecutorFailure(
            "transaction_journal_unsafe",
            "Storage activity tracking has unsafe ownership or permissions. "
            "No storage action was started.",
            needs_attention=True,
        )
    journal_path = _journal_path(paths, operation_id)
    prior = _load_prior_journal(journal_path, plan_sha)
    if prior is not None:
        return {**prior, "replayed": True}
    with _device_locks(paths, [str(device["id"])]):
        live = maintenance_live()
        _ensure_not_active(paths, live)
        current = live[str(device["id"])]
        stable_path = _stable_path(paths, current)
        try:
            commands = (
                wipe_commands(plan["options"], stable_path.as_posix())
                if plan["action"] == "wipe"
                else sector_conversion_commands(plan["options"], stable_path.as_posix())
            )
        except LayoutError as exc:
            raise ExecutorFailure("maintenance_plan_invalid", str(exc)) from exc
        journal: dict[str, Any] = {
            "schema_version": 1,
            "operation_id": operation_id,
            "plan_sha256": plan_sha,
            "state": "running",
            "started_at": time.time(),
            "updated_at": time.time(),
            "completed_actions": [],
            "notices": [],
            "phase": "Revalidating drive identity",
            "completed_steps": 0,
            "total_steps": len(commands),
            "current_action": None,
        }
        atomic_json(journal_path, journal)
        sanitize_verification: dict[str, Any] | None = None
        try:
            for index, command in enumerate(commands):
                # Identity, capacity, sector geometry, activation, and stable alias are checked
                # again immediately before every destructive command.
                converted = plan["action"] == "sector_conversion" and index > 0
                live = maintenance_live(converted=converted)
                _ensure_not_active(paths, live)
                current_path = _stable_path(paths, live[str(device["id"])])
                if current_path != stable_path:
                    raise ExecutorFailure(
                        "drive_identity_changed",
                        "The persistent drive path changed before execution.",
                    )
                journal["phase"] = command.phase
                journal["current_action"] = {
                    "id": f"maintenance:{index + 1}",
                    "type": plan["action"],
                    "number": index + 1,
                    "count": len(commands),
                }
                journal["updated_at"] = time.time()
                atomic_json(journal_path, journal)
                runner([_tool(command.argv[0]), *command.argv[1:]], command.timeout_seconds)
                if plan["action"] == "wipe" and plan["options"]["method"] in {
                    "nvme_sanitize",
                    "nvme_crypto_erase",
                }:
                    journal["phase"] = "Verifying NVMe sanitize completion"
                    journal["updated_at"] = time.time()
                    atomic_json(journal_path, journal)
                    sanitize_verification = _wait_for_nvme_sanitize(
                        stable_path.as_posix(), probe=status_probe
                    )
                journal["completed_steps"] = index + 1
                journal["completed_actions"] = [
                    *journal["completed_actions"],
                    f"maintenance:{index + 1}",
                ]
                journal["current_action"] = None
                journal["updated_at"] = time.time()
                atomic_json(journal_path, journal)
            if plan["action"] == "sector_conversion":
                final_live = maintenance_live(converted=True)
                final_review = _review_document(final_live[str(device["id"])])
                if final_review["logical_sector_bytes"] != plan["options"]["target_logical_bytes"]:
                    raise ExecutorFailure(
                        "sector_conversion_not_verified",
                        "The requested sector geometry was not reported after conversion.",
                        needs_attention=True,
                    )
            result = {
                "operation_id": operation_id,
                "action": plan["action"],
                "device_id": device["id"],
                "completed_actions": list(journal["completed_actions"]),
                "replayed": False,
            }
            if plan["action"] == "wipe":
                result["sanitization_report"] = {
                    "device": device,
                    "method": plan["options"]["method"],
                    "scope": plan["options"]["scope"],
                    "capability_source": plan["options"]["capability_source"],
                    "started_at": journal["started_at"],
                    "finished_at": time.time(),
                    "result": "succeeded",
                    "verification": sanitize_verification
                    or {"status": "command_completed", "source": commands[-1].phase},
                }
        except Exception as exc:
            journal["state"] = "needs_attention"
            if plan["action"] == "wipe":
                safe_error = (
                    str(exc)
                    if isinstance(exc, ExecutorFailure)
                    else "An internal execution failure interrupted sanitization."
                )
                journal["sanitization_report"] = {
                    "device": device,
                    "method": plan["options"]["method"],
                    "scope": plan["options"]["scope"],
                    "capability_source": plan["options"]["capability_source"],
                    "started_at": journal["started_at"],
                    "finished_at": time.time(),
                    "result": "needs_attention",
                    "error": safe_error[:512],
                }
            journal["updated_at"] = time.time()
            atomic_json(journal_path, journal)
            raise
        journal["state"] = "succeeded"
        journal["phase"] = "Drive maintenance completed"
        journal["result"] = result
        journal["updated_at"] = time.time()
        atomic_json(journal_path, journal)
        return result


def _foreign_source_path(
    paths: Paths, live_disk: Mapping[str, Any], source: Mapping[str, Any]
) -> Path:
    stable_disk = _stable_path(paths, live_disk)
    if source.get("kind") == "whole_device":
        return stable_disk
    number = source.get("partition_number")
    partitions = live_disk.get("partitions")
    matches = (
        [
            item
            for item in partitions
            if isinstance(partitions, list)
            and isinstance(item, Mapping)
            and item.get("number") == number
        ]
        if isinstance(partitions, list)
        else []
    )
    if len(matches) != 1:
        raise ExecutorFailure(
            "foreign_partition_changed", "The reviewed source partition is no longer present."
        )
    live_path = matches[0].get("kernel_path")
    if not isinstance(live_path, str) or not live_path.startswith("/dev/"):
        raise ExecutorFailure("foreign_partition_changed", "The source partition path is invalid.")
    stable_partition = Path(f"{stable_disk}-part{number}")
    try:
        if not stable_partition.is_symlink() or stable_partition.resolve(strict=True) != Path(
            live_path
        ).resolve(strict=True):
            raise OSError("partition alias mismatch")
    except OSError as exc:
        raise ExecutorFailure(
            "stable_device_path_unavailable",
            "The source partition has no matching persistent /dev/disk/by-id path.",
        ) from exc
    return stable_partition


def _parse_wipefs_signatures(output: str) -> list[dict[str, str | None]]:
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ExecutorFailure(
            "foreign_probe_invalid", "The source signature report was malformed."
        ) from exc
    raw = payload.get("signatures") if isinstance(payload, dict) else None
    if not isinstance(raw, list) or len(raw) > 64:
        raise ExecutorFailure("foreign_probe_invalid", "The source signature report was malformed.")
    result: list[dict[str, str | None]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ExecutorFailure(
                "foreign_probe_invalid", "The source signature report was malformed."
            )
        folded = {str(key).casefold(): value for key, value in item.items()}
        signature_type = folded.get("type")
        if not isinstance(signature_type, str) or len(signature_type) > 64:
            raise ExecutorFailure(
                "foreign_probe_invalid", "The source signature report was malformed."
            )
        result.append(
            {
                "type": signature_type.casefold(),
                "uuid": str(folded["uuid"])[:256] if folded.get("uuid") not in {None, ""} else None,
                "label": str(folded["label"])[:256]
                if folded.get("label") not in {None, ""}
                else None,
                "usage": str(folded["usage"])[:64]
                if folded.get("usage") not in {None, ""}
                else None,
            }
        )
    return result


def _verify_foreign_signature(
    source: Path, expected: Mapping[str, Any], probe: CommandProbe
) -> None:
    output = probe(
        [
            _tool("wipefs"),
            "--no-act",
            "--json",
            "--output",
            "TYPE,UUID,LABEL,USAGE",
            os.fspath(source),
        ],
        60,
    )
    signatures = _parse_wipefs_signatures(output)
    expected_type = expected.get("filesystem_type", expected.get("signature_type"))
    matches = [item for item in signatures if item["type"] == expected_type]
    expected_uuid = expected.get("filesystem_uuid", expected.get("signature_uuid"))
    if expected_uuid is not None:
        matches = [item for item in matches if item["uuid"] == expected_uuid]
    if len(matches) != 1:
        raise ExecutorFailure(
            "foreign_signature_changed",
            "The source storage signature no longer matches the reviewed plan.",
        )


def _parse_export_lines(output: str, *, provider: str) -> dict[str, str]:
    if len(output) > MAXIMUM_RESPONSE_BYTES:
        raise ExecutorFailure("foreign_provider_invalid", f"The {provider} report was oversized.")
    result: dict[str, str] = {}
    for line in output.splitlines():
        if not line or line.startswith(" ") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key) or len(value) > 4096:
            raise ExecutorFailure(
                "foreign_provider_invalid", f"The {provider} report was malformed."
            )
        if key in result and result[key] != value:
            raise ExecutorFailure(
                "foreign_provider_invalid", f"The {provider} report was ambiguous."
            )
        result[key] = value.strip()
    return result


def _foreign_md_preview(sources: list[Path], probe: CommandProbe) -> dict[str, Any]:
    members: list[dict[str, Any]] = []
    for source in sources:
        fields = _parse_export_lines(
            probe([_tool("mdadm"), "--examine", "--export", os.fspath(source)], 60),
            provider="Linux MD",
        )
        array_uuid = fields.get("MD_UUID")
        if not array_uuid:
            raise ExecutorFailure(
                "foreign_provider_invalid", "Linux MD did not report an array UUID."
            )
        expected_devices = fields.get("MD_DEVICES")
        device_uuid = fields.get("MD_DEV_UUID")
        members.append(
            {
                "source": os.fspath(source),
                "array_uuid": array_uuid[:256],
                "array_name": fields.get("MD_NAME", "Not reported")[:256],
                "level": fields.get("MD_LEVEL", "Not reported")[:64],
                "expected_devices": int(expected_devices)
                if expected_devices and expected_devices.isdigit()
                else None,
                "device_uuid": device_uuid[:256] if device_uuid else None,
                "events": int(fields["MD_EVENTS"])
                if fields.get("MD_EVENTS", "").isdigit()
                else None,
                "metadata_version": fields.get("MD_METADATA", "Not reported")[:64],
            }
        )
    uuids = {item["array_uuid"] for item in members}
    levels = {item["level"] for item in members}
    expected = {
        item["expected_devices"] for item in members if item["expected_devices"] is not None
    }
    if len(uuids) != 1 or len(levels) != 1 or len(expected) > 1:
        raise ExecutorFailure("foreign_provider_conflict", "Linux MD member metadata conflicts.")
    expected_count = next(iter(expected), None)
    device_uuids = {item["device_uuid"] for item in members if isinstance(item["device_uuid"], str)}
    complete = (
        expected_count is not None
        and len(members) == expected_count
        and len(device_uuids) == expected_count
    )
    return {
        "provider": "linux_md",
        "identity": next(iter(uuids)),
        "name": members[0]["array_name"],
        "layout": members[0]["level"],
        "members": members,
        "completeness": {
            "quality": "available" if expected_count is not None else "not_reported",
            "state": "complete"
            if complete
            else "incomplete"
            if expected_count is not None
            else "not_reported",
            "expected_members": expected_count,
            "observed_members": len(device_uuids),
            "missing_members": max(0, expected_count - len(device_uuids))
            if expected_count is not None
            else None,
        },
        "health": {
            "quality": "not_reported",
            "state": None,
            "reason": "Inactive MD member metadata does not prove current array health.",
        },
        "mountability": {
            "quality": "derived" if complete else "temporarily_unavailable",
            "state": "read_only_assembly_candidate" if complete else "not_ready",
            "reason": "All expected unique member identities were observed."
            if complete
            else "All expected unique MD member identities were not observed.",
        },
    }


def _parse_lvm_report(output: str, report_name: str) -> list[dict[str, Any]]:
    try:
        document = json.loads(output)
        reports = document.get("report") if isinstance(document, dict) else None
        report = (
            reports[0].get(report_name)
            if isinstance(reports, list) and len(reports) == 1 and isinstance(reports[0], dict)
            else None
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ExecutorFailure(
            "foreign_provider_invalid", "The LVM metadata report was malformed."
        ) from exc
    if (
        not isinstance(report, list)
        or len(report) > 512
        or any(not isinstance(item, dict) for item in report)
    ):
        raise ExecutorFailure("foreign_provider_invalid", "The LVM metadata report was malformed.")
    return report


def _lvm_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.isdigit():
            return int(normalized)
    return None


def _foreign_lvm_preview(sources: list[Path], probe: CommandProbe) -> dict[str, Any]:
    device_list = ",".join(os.fspath(item) for item in sources)
    common = [
        "--readonly",
        "--foreign",
        "--devices",
        device_list,
        "--reportformat",
        "json",
        "--units",
        "b",
        "--nosuffix",
    ]
    pv_rows = _parse_lvm_report(
        probe(
            [
                _tool("pvs"),
                *common,
                "--options",
                "pv_uuid,pv_name,vg_uuid,vg_name,pv_size,pv_free,pv_attr",
            ],
            60,
        ),
        "pv",
    )
    vg_rows = _parse_lvm_report(
        probe(
            [
                _tool("vgs"),
                *common,
                "--options",
                "vg_uuid,vg_name,pv_count,vg_missing_pv_count,vg_attr,vg_size,vg_free",
            ],
            60,
        ),
        "vg",
    )
    if not pv_rows:
        raise ExecutorFailure(
            "foreign_provider_invalid", "LVM did not report any physical volumes."
        )
    groups = {str(item.get("vg_uuid", "")).strip() for item in pv_rows}
    if "" in groups or len(groups) != 1:
        raise ExecutorFailure(
            "foreign_provider_conflict", "LVM physical-volume metadata conflicts."
        )
    group_id = next(iter(groups))
    matching_vgs = [item for item in vg_rows if str(item.get("vg_uuid", "")).strip() == group_id]
    vg = matching_vgs[0] if len(matching_vgs) == 1 else {}
    expected = _lvm_int(vg.get("pv_count"))
    missing = _lvm_int(vg.get("vg_missing_pv_count"))
    complete = expected is not None and missing == 0 and expected == len(pv_rows)
    return {
        "provider": "lvm",
        "identity": group_id[:256],
        "name": str(vg.get("vg_name") or pv_rows[0].get("vg_name") or "Not reported")[:256],
        "layout": "volume_group",
        "members": [
            {
                "source": str(item.get("pv_name") or "Not reported")[:4096],
                "pv_uuid": str(item.get("pv_uuid") or "Not reported")[:256],
                "size_bytes": _lvm_int(item.get("pv_size")),
                "free_bytes": _lvm_int(item.get("pv_free")),
                "attributes": str(item.get("pv_attr") or "Not reported")[:64],
            }
            for item in pv_rows
        ],
        "completeness": {
            "quality": "available"
            if expected is not None and missing is not None
            else "not_reported",
            "state": "complete"
            if complete
            else "incomplete"
            if expected is not None and missing is not None
            else "not_reported",
            "expected_members": expected,
            "observed_members": len(pv_rows),
            "missing_members": missing,
        },
        "health": {
            "quality": "not_reported",
            "state": None,
            "reason": "Read-only LVM metadata does not prove filesystem or logical-volume health.",
        },
        "mountability": {
            "quality": "derived" if complete else "temporarily_unavailable",
            "state": "read_only_activation_candidate" if complete else "not_ready",
            "reason": "The volume group reports all physical volumes."
            if complete
            else "The volume group does not report a complete physical-volume set.",
        },
    }


def _foreign_zfs_preview(sources: list[Path], probe: CommandProbe) -> dict[str, Any]:
    members: list[dict[str, Any]] = []
    for source in sources:
        output = probe([_tool("zdb"), "-l", os.fspath(source)], 60)
        if len(output) > MAXIMUM_RESPONSE_BYTES:
            raise ExecutorFailure("foreign_provider_invalid", "The ZFS label report was oversized.")
        name = re.search(r"(?m)^\s*name:\s*'([^'\r\n]{1,255})'\s*$", output)
        pool_guid = re.search(r"(?m)^\s*pool_guid:\s*([0-9]{1,32})\s*$", output)
        guids = re.findall(r"(?m)^\s*guid:\s*([0-9]{1,32})\s*$", output)
        txg = re.findall(r"(?m)^\s*txg:\s*([0-9]{1,32})\s*$", output)
        if pool_guid is None or not guids:
            raise ExecutorFailure(
                "foreign_provider_invalid", "ZFS did not report a valid member label."
            )
        members.append(
            {
                "source": os.fspath(source),
                "pool_guid": pool_guid.group(1),
                "pool_name": name.group(1) if name else "Not reported",
                "reported_guids": sorted(set(guids))[:256],
                "maximum_txg": max((int(item) for item in txg), default=None),
            }
        )
    pool_guids = {item["pool_guid"] for item in members}
    if len(pool_guids) != 1:
        raise ExecutorFailure(
            "foreign_provider_conflict", "ZFS member labels report different pools."
        )
    return {
        "provider": "zfs",
        "identity": next(iter(pool_guids)),
        "name": next(
            (item["pool_name"] for item in members if item["pool_name"] != "Not reported"),
            "Not reported",
        ),
        "layout": "Not reported",
        "members": members,
        "completeness": {
            "quality": "not_reported",
            "state": "not_reported",
            "expected_members": None,
            "observed_members": len(members),
            "missing_members": None,
        },
        "health": {
            "quality": "not_reported",
            "state": None,
            "reason": "Offline ZFS labels identify the pool but do not prove import health.",
        },
        "mountability": {
            "quality": "not_reported",
            "state": "not_reported",
            "reason": "Hoardarr does not run zpool import during a metadata preview.",
        },
    }


def preview_foreign_stack(
    request: Mapping[str, Any],
    *,
    paths: Paths | None = None,
    inventory_provider: InventoryProvider | None = None,
    probe: CommandProbe = _capture_read_only,
) -> dict[str, Any]:
    if (
        set(request) != {"operation", "plan_sha256", "plan"}
        or request.get("operation") != "preview_foreign_stack"
    ):
        raise ExecutorFailure("request_invalid", "The foreign stack preview request is invalid.")
    plan = request.get("plan")
    if not isinstance(plan, dict) or request.get("plan_sha256") != plan.get("plan_sha256"):
        raise ExecutorFailure("foreign_stack_plan_invalid", "The stack preview is invalid.")
    try:
        validate_stack_preview_plan(plan)
    except ForeignStorageError as exc:
        raise ExecutorFailure(exc.code, str(exc)) from exc
    paths = paths or Paths()
    provider = inventory_provider or (lambda: _live_inventory(paths))
    devices = [dict(member["device"]) for member in plan["members"]]
    document = {
        "storage": {
            "selected_devices": devices,
            "snapshot_binding": {
                "selected_device_ids": [device["id"] for device in devices],
                "device_binding_sha256": document_hash(devices),
            },
        }
    }
    with _device_locks(paths, [str(device["id"]) for device in devices]):
        live = _selected_live_devices(document, provider())
        _ensure_not_active(paths, live)
        sources: list[Path] = []
        for member in plan["members"]:
            current = live[str(member["device"]["id"])]
            source = _foreign_source_path(paths, current, member["source"])
            _verify_foreign_signature(source, member["source"], probe)
            sources.append(source)
        profile = plan["profile"]
        if profile == "linux_md":
            result = _foreign_md_preview(sources, probe)
        elif profile == "lvm":
            result = _foreign_lvm_preview(sources, probe)
        else:
            result = _foreign_zfs_preview(sources, probe)
    return {
        "candidate_id": plan["candidate_id"],
        "plan_sha256": plan["plan_sha256"],
        "activation_performed": False,
        "mutation_performed": False,
        **result,
    }


def _verify_read_only_mount(
    target: Path, expected_filesystem: str, probe: CommandProbe
) -> dict[str, Any]:
    output = probe(
        [
            _tool("findmnt"),
            "--json",
            "--mountpoint",
            os.fspath(target),
            "--output",
            "SOURCE,FSTYPE,OPTIONS",
        ],
        30,
    )
    try:
        payload = json.loads(output)
        filesystems = payload.get("filesystems") if isinstance(payload, dict) else None
        item = filesystems[0] if isinstance(filesystems, list) and len(filesystems) == 1 else None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ExecutorFailure(
            "foreign_mount_unverified", "The read-only mount could not be verified."
        ) from exc
    if not isinstance(item, Mapping):
        raise ExecutorFailure(
            "foreign_mount_unverified", "The read-only mount could not be verified."
        )
    options = item.get("options")
    option_set = (
        {value.strip() for value in options.split(",")} if isinstance(options, str) else set()
    )
    if "ro" not in option_set or "rw" in option_set:
        raise ExecutorFailure(
            "foreign_mount_not_read_only",
            "The source did not mount read-only; it was immediately detached.",
            needs_attention=True,
        )
    reported_filesystem = item.get("fstype")
    allowed_filesystems = (
        {"ntfs", "ntfs3", "fuseblk"}
        if expected_filesystem in {"ntfs", "ntfs3"}
        else {expected_filesystem}
    )
    if (
        not isinstance(reported_filesystem, str)
        or reported_filesystem.casefold() not in allowed_filesystems
    ):
        raise ExecutorFailure(
            "foreign_mount_filesystem_changed",
            "The mounted filesystem type does not match the reviewed plan.",
            needs_attention=True,
        )
    return {
        "source": str(item.get("source"))[:4096] if item.get("source") is not None else None,
        "filesystem_type": str(item.get("fstype"))[:64] if item.get("fstype") is not None else None,
        "options": sorted(option_set),
    }


def _inventory_foreign_tree(root: Path, limits: Mapping[str, int]) -> dict[str, Any]:
    maximum_entries = int(limits["maximum_entries"])
    maximum_extensions = int(limits["maximum_extension_groups"])
    maximum_errors = int(limits["maximum_errors"])
    file_count = directory_count = total_bytes = 0
    oldest: float | None = None
    newest: float | None = None
    largest: dict[str, Any] | None = None
    extensions: Counter[str] = Counter()
    errors: list[dict[str, str]] = []
    case_names: dict[tuple[str, str], str] = {}
    unicode_names: dict[tuple[str, str], str] = {}
    case_collisions = unicode_collisions = 0
    permission_anomalies: Counter[str] = Counter()
    top_level_entries: list[dict[str, Any]] = []
    truncated = False

    def record_error(path: str, exc: OSError) -> None:
        if len(errors) < maximum_errors:
            errors.append({"path": path[:1024], "error": type(exc).__name__})

    def walk_error(exc: OSError) -> None:
        record_error(os.fspath(getattr(exc, "filename", "Not reported")), exc)

    for current, directories, files in os.walk(
        root, topdown=True, followlinks=False, onerror=walk_error
    ):
        directories.sort()
        files.sort()
        relative_parent = os.path.relpath(current, root)
        for name in [*directories, *files]:
            if file_count + directory_count >= maximum_entries:
                truncated = True
                directories[:] = []
                break
            relative = name if relative_parent == "." else f"{relative_parent}/{name}"
            key_parent = relative_parent.casefold()
            case_key = (key_parent, name.casefold())
            unicode_key = (key_parent, unicodedata.normalize("NFC", name).casefold())
            previous_case = case_names.setdefault(case_key, name)
            previous_unicode = unicode_names.setdefault(unicode_key, name)
            case_collisions += int(previous_case != name)
            unicode_collisions += int(previous_unicode != name)
            path = Path(current) / name
            try:
                details = path.lstat()
            except OSError as exc:
                record_error(relative, exc)
                continue
            if stat.S_ISDIR(details.st_mode):
                directory_count += 1
                if relative_parent == "." and len(top_level_entries) < 256:
                    top_level_entries.append({"name": name[:255], "type": "directory"})
                if details.st_mode & stat.S_IWOTH:
                    permission_anomalies["world_writable_directories"] += 1
                continue
            if not stat.S_ISREG(details.st_mode):
                continue
            file_count += 1
            total_bytes += details.st_size
            if relative_parent == "." and len(top_level_entries) < 256:
                top_level_entries.append(
                    {"name": name[:255], "type": "file", "bytes": details.st_size}
                )
            if details.st_mode & stat.S_ISUID:
                permission_anomalies["setuid_files"] += 1
            if details.st_mode & stat.S_ISGID:
                permission_anomalies["setgid_files"] += 1
            if details.st_mode & stat.S_IWOTH:
                permission_anomalies["world_writable_files"] += 1
            if not details.st_mode & stat.S_IRUSR:
                permission_anomalies["owner_unreadable_files"] += 1
            oldest = details.st_mtime if oldest is None else min(oldest, details.st_mtime)
            newest = details.st_mtime if newest is None else max(newest, details.st_mtime)
            if largest is None or details.st_size > largest["bytes"]:
                largest = {"path": relative[:1024], "bytes": details.st_size}
            suffix = path.suffix.casefold()[:64] or "[no extension]"
            extensions[suffix] += 1
        if truncated:
            break
    top_extensions = [
        {"extension": extension, "files": count}
        for extension, count in extensions.most_common(maximum_extensions)
    ]
    return {
        "file_count": file_count,
        "directory_count": directory_count,
        "total_bytes": total_bytes,
        "largest_file": largest,
        "oldest_mtime_unix": oldest,
        "newest_mtime_unix": newest,
        "extension_distribution": top_extensions,
        "case_collision_count": case_collisions,
        "unicode_collision_count": unicode_collisions,
        "read_errors": errors,
        "permission_anomalies": dict(sorted(permission_anomalies.items())),
        "top_level_entries": top_level_entries,
        "truncated": truncated,
        "maximum_entries": maximum_entries,
    }


def _prepare_private_executor_directory(path: Path, *, purpose: str) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        details = path.lstat()
    except OSError as exc:
        raise ExecutorFailure(
            "foreign_private_path_unavailable",
            f"The private {purpose} path could not be prepared.",
            needs_attention=True,
        ) from exc
    if not stat.S_ISDIR(details.st_mode) or (
        os.name != "nt" and (details.st_uid != _executor_uid() or details.st_mode & 0o077)
    ):
        raise ExecutorFailure(
            "foreign_private_path_unsafe",
            f"The private {purpose} path has unsafe ownership or permissions.",
            needs_attention=True,
        )
    _assert_no_symlink_components(path)


def apply_foreign_inspection(
    request: Mapping[str, Any],
    *,
    paths: Paths | None = None,
    inventory_provider: InventoryProvider | None = None,
    runner: CommandRunner = _run,
    probe: CommandProbe = _capture_read_only,
    tree_inventory: InspectionInventoryProvider = _inventory_foreign_tree,
) -> dict[str, Any]:
    if set(request) != {"operation", "operation_id", "plan_sha256", "plan", "confirmation_sha256"}:
        raise ExecutorFailure("request_invalid", "The storage request is invalid.")
    operation_id = request.get("operation_id")
    plan_sha = request.get("plan_sha256")
    plan = request.get("plan")
    if (
        request.get("operation") != "apply_foreign_inspection"
        or not isinstance(operation_id, str)
        or not UUID_RE.fullmatch(operation_id)
        or not isinstance(plan_sha, str)
        or not SHA256_RE.fullmatch(plan_sha)
        or not isinstance(plan, dict)
        or plan.get("plan_sha256") != plan_sha
        or request.get("confirmation_sha256")
        != document_hash({"confirmation": "INSPECT READ ONLY"})
    ):
        raise ExecutorFailure(
            "foreign_inspection_consent_missing", "Exact read-only inspection approval is required."
        )
    try:
        validate_inspection_plan(plan)
    except ForeignStorageError as exc:
        raise ExecutorFailure(exc.code, str(exc)) from exc
    paths = paths or Paths()
    validate_quarantine(paths.quarantine_marker)
    provider = inventory_provider or (lambda: _live_inventory(paths))
    device = dict(plan["device"])
    document = {
        "storage": {
            "selected_devices": [device],
            "snapshot_binding": {
                "selected_device_ids": [device["id"]],
                "device_binding_sha256": document_hash([device]),
            },
        }
    }
    _prepare_private_executor_directory(paths.transaction_root, purpose="inspection journal")
    journal_path = _journal_path(paths, operation_id)
    prior = _load_prior_journal(journal_path, plan_sha)
    if prior is not None:
        return {**prior, "replayed": True}
    target = paths.inspection_root / operation_id
    with _device_locks(paths, [str(device["id"])]):
        live = _selected_live_devices(document, provider())
        _ensure_not_active(paths, live)
        current = live[str(device["id"])]
        source = _foreign_source_path(paths, current, plan["source"])
        _verify_foreign_signature(source, plan["source"], probe)
        _prepare_private_executor_directory(paths.inspection_root, purpose="inspection mount")
        if target.exists():
            raise ExecutorFailure(
                "foreign_mountpoint_busy",
                "The private inspection path already exists.",
                needs_attention=True,
            )
        target.mkdir(mode=0o700)
        journal: dict[str, Any] = {
            "schema_version": 1,
            "operation_id": operation_id,
            "plan_sha256": plan_sha,
            "state": "running",
            "started_at": time.time(),
            "updated_at": time.time(),
            "phase": "Revalidating source identity and signature",
            "completed_steps": 1,
            "total_steps": 4,
            "completed_actions": ["identity-and-signature"],
            "current_action": None,
            "notices": [],
        }
        atomic_json(journal_path, journal)
        mounted = False
        mount_attempted = False
        result: dict[str, Any] | None = None
        failure: Exception | None = None
        try:
            options = ",".join(plan["source"]["read_only_options"])
            journal.update(
                phase="Mounting source read-only without recovery",
                current_action={"id": "mount-read-only", "type": "foreign.mount.read_only"},
                updated_at=time.time(),
            )
            atomic_json(journal_path, journal)
            mount_command = [
                _tool("mount"),
                "--read-only",
                "--types",
                plan["source"]["filesystem_type"],
                "--options",
                options,
                os.fspath(source),
                os.fspath(target),
            ]
            mount_attempted = True
            runner(mount_command, 120)
            mounted = True
            mount_evidence = _verify_read_only_mount(
                target, plan["source"]["filesystem_type"], probe
            )
            journal.update(
                phase="Inventorying files and metadata",
                completed_steps=2,
                completed_actions=[*journal["completed_actions"], "mount-read-only"],
                current_action={"id": "inventory", "type": "foreign.inventory"},
                updated_at=time.time(),
            )
            atomic_json(journal_path, journal)
            report = tree_inventory(target, plan["limits"])
            journal.update(
                phase="Detaching private read-only inspection",
                completed_steps=3,
                completed_actions=[*journal["completed_actions"], "inventory"],
                current_action={"id": "unmount", "type": "foreign.unmount"},
                updated_at=time.time(),
            )
            atomic_json(journal_path, journal)
            result = {
                "operation_id": operation_id,
                "candidate_id": plan["candidate_id"],
                "device_id": device["id"],
                "filesystem": {
                    "type": plan["source"]["filesystem_type"],
                    "uuid": plan["source"]["filesystem_uuid"],
                    "label": plan["source"]["filesystem_label"],
                },
                "mount_evidence": mount_evidence,
                "inventory": report,
                "access": "read_only",
                "persistent_mount": False,
                "mutation_performed": False,
                "replayed": False,
            }
        except Exception as exc:
            failure = exc
        finally:
            if mount_attempted and not mounted:
                mounted = os.path.ismount(target)
            if mounted:
                try:
                    runner([_tool("umount"), "--", os.fspath(target)], 120)
                    mounted = False
                except Exception as exc:
                    failure = ExecutorFailure(
                        "foreign_unmount_failed",
                        "The private read-only inspection mount could not be detached.",
                        needs_attention=True,
                    )
                    failure.__cause__ = exc
            if not mounted:
                try:
                    target.rmdir()
                except OSError as exc:
                    if target.exists():
                        failure = ExecutorFailure(
                            "foreign_cleanup_failed",
                            "The private inspection directory could not be removed.",
                            needs_attention=True,
                        )
                        failure.__cause__ = exc
        if failure is not None:
            journal.update(
                state="needs_attention" if getattr(failure, "needs_attention", False) else "failed",
                phase="Read-only inspection failed",
                current_action=None,
                updated_at=time.time(),
            )
            atomic_json(journal_path, journal)
            if isinstance(failure, ExecutorFailure):
                raise failure
            raise ExecutorFailure(
                "foreign_inventory_failed", "The read-only inventory could not be completed."
            ) from failure
        assert result is not None
        journal.update(
            state="succeeded",
            phase="Read-only inspection completed",
            completed_steps=4,
            completed_actions=[*journal["completed_actions"], "unmount"],
            current_action=None,
            updated_at=time.time(),
            result=result,
        )
        atomic_json(journal_path, journal)
        return result


def apply_snapraid_replacement(
    request: Mapping[str, Any],
    *,
    paths: Paths | None = None,
    inventory_provider: InventoryProvider | None = None,
    runner: CommandRunner = _run,
) -> dict[str, Any]:
    expected = {"operation", "operation_id", "plan_sha256", "plan", "confirmation_sha256"}
    operation_id = request.get("operation_id")
    plan_sha = request.get("plan_sha256")
    plan = request.get("plan")
    if (
        set(request) != expected
        or request.get("operation") != "apply_snapraid_replacement"
        or not isinstance(operation_id, str)
        or not UUID_RE.fullmatch(operation_id)
        or not isinstance(plan_sha, str)
        or not SHA256_RE.fullmatch(plan_sha)
        or not isinstance(plan, dict)
        or document_hash(plan) != plan_sha
        or request.get("confirmation_sha256") != document_hash({"confirmation": "I AGREE"})
    ):
        raise ExecutorFailure(
            "destructive_consent_missing", "Exact destructive approval is required."
        )
    try:
        validate_replacement_plan(plan)
    except SnapraidReplacementError as exc:
        raise ExecutorFailure(exc.code, str(exc)) from exc
    paths = paths or Paths()
    validate_quarantine(paths.quarantine_marker)
    provider = inventory_provider or (lambda: _live_inventory(paths))
    device = dict(plan["device"])
    identity_document = {
        "storage": {
            "selected_devices": [device],
            "snapshot_binding": {
                "selected_device_ids": [device["id"]],
                "device_binding_sha256": document_hash([device]),
            },
        }
    }
    try:
        paths.transaction_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        details = paths.transaction_root.lstat()
    except OSError as exc:
        raise ExecutorFailure(
            "transaction_journal_unavailable", "Storage activity tracking is unavailable."
        ) from exc
    if not stat.S_ISDIR(details.st_mode) or (
        os.name != "nt" and (details.st_uid != _executor_uid() or details.st_mode & 0o077)
    ):
        raise ExecutorFailure("transaction_journal_unsafe", "Storage activity tracking is unsafe.")
    journal_path = _journal_path(paths, operation_id)
    prior = _load_prior_journal(journal_path, plan_sha)
    if prior is not None:
        return {**prior, "replayed": True}
    config_path = paths.snapraid_config_root / f"{plan['pool_name']}.conf"
    if config_path.parent != paths.snapraid_config_root:
        raise ExecutorFailure(
            "snapraid_config_invalid", "The SnapRAID configuration path is invalid."
        )
    try:
        config_details = config_path.lstat()
        if not stat.S_ISREG(config_details.st_mode) or (
            os.name != "nt" and (config_details.st_uid != 0 or config_details.st_mode & 0o022)
        ):
            raise ExecutorFailure("snapraid_config_unsafe", "The SnapRAID configuration is unsafe.")
        original_config = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExecutorFailure(
            "snapraid_config_unavailable", "The SnapRAID configuration is unavailable."
        ) from exc
    if hashlib.sha256(original_config.encode()).hexdigest() != plan["config_sha256"]:
        raise ExecutorFailure(
            "snapraid_config_changed", "The SnapRAID configuration changed before execution."
        )
    replacement_mount = Path(str(plan["replacement_mount"]))
    if replacement_mount.parent != paths.mount_root:
        raise ExecutorFailure("snapraid_path_invalid", "The replacement mount path is invalid.")
    with _device_locks(paths, [str(device["id"])]):
        live = _selected_live_devices(identity_document, provider())
        _ensure_not_active(paths, live)
        if existing_data_summary(live[str(device["id"])]) != plan["existing_data"]:
            raise ExecutorFailure(
                "replacement_contents_changed",
                "The replacement drive contents changed after destructive review.",
            )
        stable_path = _stable_path(paths, live[str(device["id"])])
        partition = _partition_path(stable_path)
        try:
            updated_config = replace_data_entry(
                original_config,
                data_name=str(plan["data_name"]),
                new_path=str(replacement_mount),
            )
        except SnapraidReplacementError as exc:
            raise ExecutorFailure(exc.code, str(exc)) from exc
        commands: list[tuple[list[str], int, str]] = [
            (
                [_tool("wipefs"), "--all", stable_path.as_posix()],
                300,
                "Clearing replacement drive signatures",
            ),
            (
                [
                    _tool("parted"),
                    "--script",
                    stable_path.as_posix(),
                    "mklabel",
                    "gpt",
                    "mkpart",
                    "primary",
                    "1MiB",
                    "100%",
                ],
                300,
                "Partitioning replacement drive",
            ),
            (
                [_tool("partprobe"), stable_path.as_posix()],
                120,
                "Refreshing replacement drive partitions",
            ),
            ([_tool("udevadm"), "settle", "--timeout=60"], 90, "Waiting for replacement drive"),
            (
                _filesystem_command(str(plan["filesystem"]), 4096, partition, format_mode="quick"),
                3600,
                "Formatting replacement drive",
            ),
        ]
        journal: dict[str, Any] = {
            "schema_version": 1,
            "operation_id": operation_id,
            "plan_sha256": plan_sha,
            "state": "running",
            "started_at": time.time(),
            "updated_at": time.time(),
            "phase": "Revalidating replacement drive",
            "completed_steps": 0,
            "total_steps": len(commands) + 5,
            "current_action": None,
            "completed_actions": [],
            "notices": [],
        }
        atomic_json(journal_path, journal)
        fix_started = False
        try:
            for index, (command, timeout, phase) in enumerate(commands, start=1):
                live = _selected_live_devices(identity_document, provider())
                _ensure_not_active(paths, live)
                if _stable_path(paths, live[str(device["id"])]) != stable_path:
                    raise ExecutorFailure(
                        "drive_identity_changed", "The replacement drive identity changed."
                    )
                journal.update(
                    {
                        "phase": phase,
                        "current_action": {"id": f"replace:{index}"},
                        "updated_at": time.time(),
                    }
                )
                atomic_json(journal_path, journal)
                runner(command, timeout)
                journal["completed_steps"] = index
                journal["completed_actions"].append(f"replace:{index}")

            def recovery_step(
                *, step: int, action_id: str, phase: str, command: list[str], timeout: int
            ) -> None:
                journal.update(
                    {
                        "phase": phase,
                        "current_action": {"id": action_id},
                        "updated_at": time.time(),
                    }
                )
                atomic_json(journal_path, journal)
                runner(command, timeout)
                journal["completed_steps"] = step
                journal["completed_actions"].append(action_id)
                journal["updated_at"] = time.time()
                atomic_json(journal_path, journal)

            replacement_mount.mkdir(parents=True, exist_ok=False, mode=0o770)
            recovery_step(
                step=len(commands) + 1,
                action_id="replace:mount",
                phase="Mounting replacement drive",
                command=[_tool("mount"), partition.as_posix(), str(replacement_mount)],
                timeout=120,
            )
            filesystem_uuid = _blkid_value(partition, "UUID")
            _append_fstab(
                paths,
                operation_id,
                [f"UUID={filesystem_uuid} {replacement_mount} {plan['filesystem']} noatime 0 2"],
            )
            atomic_text(config_path, updated_config, mode=0o640)
            for recovery_index, command in enumerate(
                recovery_commands(config_path=str(config_path), data_name=str(plan["data_name"])),
                start=2,
            ):
                action_name = command.argv[-1]
                if action_name == "fix":
                    fix_started = True
                recovery_step(
                    step=len(commands) + recovery_index,
                    action_id=f"replace:{action_name}",
                    phase=command.phase,
                    command=[_tool(command.argv[0]), *command.argv[1:]],
                    timeout=command.timeout_seconds,
                )
        except Exception:
            if not fix_started:
                atomic_text(config_path, original_config, mode=0o640)
                with contextlib.suppress(Exception):
                    runner([_tool("umount"), str(replacement_mount)], 120)
                with contextlib.suppress(Exception):
                    _remove_fstab_operation(paths, operation_id)
                with contextlib.suppress(OSError):
                    replacement_mount.rmdir()
            journal.update({"state": "needs_attention", "updated_at": time.time()})
            atomic_json(journal_path, journal)
            raise
        result = {
            "operation_id": operation_id,
            "pool_name": plan["pool_name"],
            "data_name": plan["data_name"],
            "replacement_device_id": device["id"],
            "replacement_mount": str(replacement_mount),
            "parity_state": "current",
            "replayed": False,
        }
        journal.update(
            {
                "state": "succeeded",
                "phase": "SnapRAID replacement completed",
                "completed_steps": journal["total_steps"],
                "current_action": None,
                "result": result,
                "updated_at": time.time(),
            }
        )
        atomic_json(journal_path, journal)
        return result


def apply_array_replacement(
    request: Mapping[str, Any],
    *,
    paths: Paths | None = None,
    inventory_provider: InventoryProvider | None = None,
    runner: CommandRunner = _run,
    zfs_state_provider: ZfsStateProvider = _live_zfs_pool_state,
    md_state_provider: Callable[[str], dict[str, Any]] = _live_md_array_state,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    expected = {"operation", "operation_id", "plan_sha256", "plan", "confirmation_sha256"}
    operation_id = request.get("operation_id")
    plan_sha = request.get("plan_sha256")
    raw_plan = request.get("plan")
    if (
        set(request) != expected
        or request.get("operation") != "apply_array_replacement"
        or not isinstance(operation_id, str)
        or not UUID_RE.fullmatch(operation_id)
        or not isinstance(plan_sha, str)
        or not SHA256_RE.fullmatch(plan_sha)
        or not isinstance(raw_plan, dict)
        or document_hash(raw_plan) != plan_sha
        or request.get("confirmation_sha256") != document_hash({"confirmation": "I AGREE"})
    ):
        raise ExecutorFailure(
            "destructive_consent_missing", "Exact destructive approval is required."
        )
    try:
        plan = validate_array_replacement_plan(raw_plan)
    except ArrayReplacementError as exc:
        raise ExecutorFailure(exc.code, str(exc)) from exc
    paths = paths or Paths()
    validate_quarantine(paths.quarantine_marker)
    provider = inventory_provider or (lambda: _live_inventory(paths))
    device = dict(plan["device"])
    identity_document = {
        "storage": {
            "selected_devices": [device],
            "snapshot_binding": {
                "selected_device_ids": [device["id"]],
                "device_binding_sha256": document_hash([device]),
            },
        }
    }
    try:
        paths.transaction_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        details = paths.transaction_root.lstat()
    except OSError as exc:
        raise ExecutorFailure(
            "transaction_journal_unavailable", "Storage activity tracking is unavailable."
        ) from exc
    if not stat.S_ISDIR(details.st_mode) or (
        os.name != "nt" and (details.st_uid != _executor_uid() or details.st_mode & 0o077)
    ):
        raise ExecutorFailure("transaction_journal_unsafe", "Storage activity tracking is unsafe.")
    journal_path = _journal_path(paths, operation_id)
    prior = _load_prior_journal(journal_path, plan_sha)
    if prior is not None:
        return {**prior, "replayed": True}

    def state() -> dict[str, Any]:
        return (
            zfs_state_provider(str(plan["target_name"]))
            if plan["provider"] == "zfs"
            else md_state_provider(str(plan["target_name"]))
        )

    def verify_binding(current: Mapping[str, Any], *, post: bool = False) -> None:
        if plan["provider"] == "zfs":
            if current.get("pool_guid") != plan["target_identity"]:
                raise ExecutorFailure(
                    "zfs_pool_changed", "The ZFS pool identity changed after review."
                )
            if not post and current.get("config_sha256") != plan["configuration_sha256"]:
                raise ExecutorFailure("zfs_pool_changed", "The ZFS topology changed after review.")
        else:
            if (
                current.get("array_uuid") != plan["target_identity"]
                or current.get("level") != plan["level"]
                or current.get("raid_disks") != plan["member_count"]
            ):
                raise ExecutorFailure(
                    "md_array_changed", "The Linux MD identity or geometry changed after review."
                )
            if not post and current.get("config_sha256") != plan["configuration_sha256"]:
                raise ExecutorFailure(
                    "md_array_changed", "The Linux MD membership changed after review."
                )

    with _device_locks(paths, [str(device["id"])]):
        live = _selected_live_devices(identity_document, provider())
        _ensure_not_active(paths, live)
        if existing_data_summary(live[str(device["id"])]) != plan["existing_data"]:
            raise ExecutorFailure(
                "replacement_contents_changed", "The replacement contents changed after review."
            )
        replacement_path = _stable_path(paths, live[str(device["id"])])
        replacement_kernel_path = _kernel_path(live[str(device["id"])]).as_posix()
        minimum = plan.get("minimum_capacity_bytes")
        if isinstance(minimum, int) and int(device.get("capacity_bytes") or 0) < minimum:
            raise ExecutorFailure("replacement_too_small", "The replacement drive is too small.")
        initial_state = state()
        verify_binding(initial_state)
        journal: dict[str, Any] = {
            "schema_version": 1,
            "operation_id": operation_id,
            "plan_sha256": plan_sha,
            "state": "running",
            "started_at": time.time(),
            "updated_at": time.time(),
            "phase": "Revalidating storage identity",
            "completed_steps": 0,
            "total_steps": 5
            if plan["provider"] == "zfs"
            else (7 if plan["old_member_path"] else 5),
            "current_action": None,
            "completed_actions": [],
            "notices": [],
        }
        atomic_json(journal_path, journal)

        def step(action_id: str, phase: str, command: list[str], timeout: int) -> None:
            live_now = _selected_live_devices(identity_document, provider())
            _ensure_not_active(paths, live_now)
            if _stable_path(paths, live_now[str(device["id"])]) != replacement_path:
                raise ExecutorFailure(
                    "drive_identity_changed", "The replacement drive identity changed."
                )
            journal.update(
                {"phase": phase, "current_action": {"id": action_id}, "updated_at": time.time()}
            )
            atomic_json(journal_path, journal)
            runner(command, timeout)
            journal["completed_steps"] += 1
            journal["completed_actions"].append(action_id)
            journal["updated_at"] = time.time()
            atomic_json(journal_path, journal)

        mutation_started = False
        try:
            step(
                "replace:wipe-signatures",
                "Clearing replacement drive signatures",
                [_tool("wipefs"), "--all", replacement_path.as_posix()],
                300,
            )
            verify_binding(state())
            if plan["provider"] == "zfs":
                mutation_started = True
                step(
                    "replace:zfs-resilver",
                    "Replacing ZFS member and waiting for resilver",
                    [
                        _tool("zpool"),
                        "replace",
                        "-w",
                        str(plan["target_name"]),
                        str(plan["old_member_path"]),
                        replacement_path.as_posix(),
                    ],
                    86400,
                )
            else:
                array_path = f"/dev/{plan['target_name']}"
                old_member = plan.get("old_member_path")
                mutation_started = True
                if old_member:
                    step(
                        "replace:md-add-spare",
                        "Adding the replacement as an MD spare",
                        [_tool("mdadm"), array_path, "--add-spare", replacement_path.as_posix()],
                        300,
                    )
                    step(
                        "replace:md-start",
                        "Starting proactive Linux MD replacement",
                        [
                            _tool("mdadm"),
                            array_path,
                            "--replace",
                            str(old_member),
                            "--with",
                            replacement_path.as_posix(),
                        ],
                        300,
                    )
                else:
                    step(
                        "replace:md-add",
                        "Adding replacement to the degraded Linux MD array",
                        [_tool("mdadm"), array_path, "--add", replacement_path.as_posix()],
                        300,
                    )
                deadline = time.monotonic() + 86400
                while True:
                    current = state()
                    verify_binding(current, post=True)
                    if current.get("sync_action") in {"idle", "none"} and not current.get(
                        "degraded"
                    ):
                        break
                    if time.monotonic() >= deadline:
                        raise ExecutorFailure(
                            "md_recovery_timeout",
                            "Linux MD recovery did not finish in time.",
                            needs_attention=True,
                        )
                    journal.update(
                        {"phase": "Waiting for Linux MD recovery", "updated_at": time.time()}
                    )
                    atomic_json(journal_path, journal)
                    sleep(2)
                journal["completed_steps"] += 1
                journal["completed_actions"].append("replace:md-recovery-complete")
                if old_member:
                    step(
                        "replace:md-remove-old",
                        "Removing the replaced Linux MD member",
                        [_tool("mdadm"), array_path, "--remove", str(old_member)],
                        300,
                    )
            final_state = state()
            verify_binding(final_state, post=True)
            members = final_state.get("member_paths", [])
            if (
                final_state.get("degraded") is True
                or not {replacement_path.as_posix(), replacement_kernel_path}.intersection(members)
                or (plan["old_member_path"] is not None and plan["old_member_path"] in members)
            ):
                raise ExecutorFailure(
                    "array_replacement_verification_failed",
                    "The provider did not report the expected healthy replacement membership.",
                    needs_attention=True,
                )
            journal["completed_steps"] = journal["total_steps"]
        except Exception as exc:
            journal.update({"state": "needs_attention", "updated_at": time.time()})
            atomic_json(journal_path, journal)
            if mutation_started and isinstance(exc, ExecutorFailure) and not exc.needs_attention:
                raise ExecutorFailure(
                    "array_replacement_needs_attention",
                    "Replacement started, but final provider verification did not complete.",
                    needs_attention=True,
                ) from exc
            raise
        result = {
            "operation_id": operation_id,
            "provider": plan["provider"],
            "target_id": plan["target_id"],
            "target_identity": plan["target_identity"],
            "replacement_device_id": device["id"],
            "state": "healthy",
            "replayed": False,
        }
        journal.update(
            {
                "state": "succeeded",
                "phase": "Array replacement completed",
                "completed_steps": journal["total_steps"],
                "current_action": None,
                "result": result,
                "updated_at": time.time(),
            }
        )
        atomic_json(journal_path, journal)
        return result


def apply_storage_redundancy(
    request: Mapping[str, Any],
    *,
    paths: Paths | None = None,
    inventory_provider: InventoryProvider | None = None,
    runner: CommandRunner = _run,
    mapper_exists: Callable[[Path], bool] = Path.exists,
    filesystem_uuid_provider: Callable[[Path], str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Change only the access layer from one path to a verified multipath map."""

    paths = paths or Paths()
    expected = {"operation", "operation_id", "plan_sha256", "plan", "confirmation_sha256"}
    if set(request) != expected or request.get("operation") != "apply_storage_redundancy":
        raise ExecutorFailure("request_invalid", "The storage redundancy request is invalid.")
    operation_id = request.get("operation_id")
    raw_plan = request.get("plan")
    if (
        not isinstance(operation_id, str)
        or not UUID_RE.fullmatch(operation_id)
        or not isinstance(raw_plan, Mapping)
    ):
        raise ExecutorFailure("request_invalid", "The storage redundancy request is invalid.")
    if request.get("confirmation_sha256") != document_hash({"confirmation": "APPLY"}):
        raise ExecutorFailure("confirmation_invalid", "The redundancy change was not confirmed.")
    try:
        plan = validate_redundancy_plan(raw_plan)
    except RedundancyError as exc:
        raise ExecutorFailure(exc.code, str(exc)) from exc
    if plan["plan_sha256"] != request.get("plan_sha256"):
        raise ExecutorFailure("redundancy_plan_changed", "The redundancy plan changed.")
    provider = inventory_provider or (lambda: _live_inventory(paths))
    snapshot = provider()
    if not isinstance(snapshot, Mapping):
        raise ExecutorFailure("hardware_scan_invalid", "The current storage inventory is invalid.")
    observed = matching_devices(snapshot, str(plan["logical_storage_identity"]))
    selected = plan["selected_path"]
    selected_identity = str(selected["stable_path_identity"])
    selected_devices = [
        item for item in observed if stable_path_identity(item) == selected_identity
    ]
    if plan["operation"] in {"redundancy.add", "redundancy.replace"}:
        if len(selected_devices) != 1:
            raise ExecutorFailure(
                "path_identity_changed",
                "The reviewed redundant path is missing or ambiguous. "
                "No storage access was changed.",
            )
        device = selected_devices[0]
        if logical_storage_identity(device) != plan["logical_storage_identity"]:
            raise ExecutorFailure(
                "logical_identity_changed",
                "The new path identifies different storage.",
            )
        if int(device.get("capacity_bytes") or -1) <= 0:
            raise ExecutorFailure("capacity_not_reported", "The new path capacity is unavailable.")
    mapper_text = str(
        (
            plan["after"]
            if plan["operation"] in {"redundancy.add", "redundancy.replace"}
            else plan["before"]
        ).get("presentation_device")
        or ""
    )
    mapper_posix = PurePosixPath(mapper_text)
    if not mapper_text.startswith("/dev/mapper/") or ".." in mapper_posix.parts:
        raise ExecutorFailure(
            "mapper_path_invalid", "The reviewed multipath mapper path is invalid."
        )
    mapper = Path(mapper_text)
    wwid = str(plan["logical_storage_identity"]).split(":", 1)[-1]
    if re.fullmatch(r"[A-Za-z0-9_.:-]{3,512}", wwid) is None:
        raise ExecutorFailure("logical_identity_invalid", "The reviewed storage WWID is invalid.")
    mountpoint_text = str(plan["before"]["mountpoint"])
    device_mountpoint_text = str(plan["before"].get("device_mountpoint") or mountpoint_text)
    mountpoint = Path(mountpoint_text)
    device_mountpoint = Path(device_mountpoint_text)
    if (
        not mountpoint_text.startswith("/")
        or ".." in PurePosixPath(mountpoint_text).parts
        or not device_mountpoint_text.startswith("/")
        or ".." in PurePosixPath(device_mountpoint_text).parts
    ):
        raise ExecutorFailure("mountpoint_invalid", "The reviewed storage mountpoint is invalid.")
    try:
        paths.transaction_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        transaction_details = paths.transaction_root.lstat()
    except OSError as exc:
        raise ExecutorFailure(
            "transaction_journal_unavailable",
            "Storage activity tracking could not be prepared. No path change was started.",
            needs_attention=True,
        ) from exc
    if not stat.S_ISDIR(transaction_details.st_mode) or (
        os.name != "nt"
        and (transaction_details.st_uid != _executor_uid() or transaction_details.st_mode & 0o077)
    ):
        raise ExecutorFailure(
            "transaction_journal_unsafe",
            "Storage activity tracking has unsafe ownership or permissions. "
            "No path change was started.",
            needs_attention=True,
        )
    journal_path = _journal_path(paths, operation_id)
    prior = _load_prior_journal(journal_path, str(plan["plan_sha256"]))
    if prior is not None:
        return {**prior, "replayed": True}
    total_steps = (
        8
        if plan["operation"] in {"redundancy.add", "redundancy.replace"}
        else 3
        if plan["operation"] == "redundancy.configure"
        else 6
    )
    journal: dict[str, Any] = {
        "schema_version": 1,
        "operation_id": operation_id,
        "plan_sha256": plan["plan_sha256"],
        "state": "running",
        "started_at": time.time(),
        "updated_at": time.time(),
        "completed_actions": [],
        "notices": [],
        "phase": "Revalidating logical storage identity",
        "completed_steps": 0,
        "total_steps": total_steps,
        "current_action": None,
    }
    atomic_json(journal_path, journal)
    base_runner = runner

    def tracked_runner(command: list[str], timeout_seconds: float) -> None:
        number = int(journal["completed_steps"]) + 1
        command_name = PurePosixPath(command[0].replace("\\", "/")).name
        action_id = f"redundancy:{number}:{command_name}"
        journal["phase"] = (
            "Pausing storage access"
            if command_name == "systemctl" and len(command) > 1 and command[1] == "stop"
            else "Verifying shares"
            if command_name == "systemctl" and len(command) > 1 and command[1] == "start"
            else {
                "multipath": "Preparing redundant storage access",
                "multipathd": "Updating controller paths",
                "umount": "Switching the storage access layer",
                "mount": "Restoring the existing storage mount",
            }.get(command_name, "Updating controller redundancy")
        )
        journal["current_action"] = {
            "id": action_id,
            "type": "storage.redundancy",
            "number": min(number, total_steps),
            "count": total_steps,
        }
        journal["updated_at"] = time.time()
        atomic_json(journal_path, journal)
        try:
            base_runner(command, timeout_seconds)
        except Exception:
            journal["state"] = "needs_attention"
            journal["updated_at"] = time.time()
            atomic_json(journal_path, journal)
            raise
        journal["completed_steps"] = min(number, total_steps)
        journal["completed_actions"] = [*journal["completed_actions"], action_id]
        journal["current_action"] = None
        journal["updated_at"] = time.time()
        atomic_json(journal_path, journal)

    runner = tracked_runner

    def set_phase(phase: str) -> None:
        journal["phase"] = phase
        journal["updated_at"] = time.time()
        atomic_json(journal_path, journal)

    managed_services = plan.get("managed_access_services")
    managed_services = managed_services if isinstance(managed_services, list) else []
    service_units = sorted(
        {
            "smbd.service" if item.get("protocol") == "smb" else "nfs-server.service"
            for item in managed_services
            if isinstance(item, Mapping) and item.get("protocol") in {"smb", "nfs"}
        }
    )
    config_backup: tuple[Path, str | None] | None = None

    def coordinate_services(action: str) -> None:
        if not service_units:
            return
        set_phase("Pausing storage access" if action == "stop" else "Verifying shares")
        for unit in service_units:
            runner([_tool("systemctl"), action, unit], 120)

    def apply_multipath_settings() -> Path:
        nonlocal config_backup
        settings = plan["settings"]
        no_path_retry = settings["no_path_retry"]
        retry_value = "30" if no_path_retry == "queue_30" else no_path_retry
        alias = mapper.name
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", alias):
            raise ExecutorFailure("mapper_path_invalid", "The multipath alias is invalid.")
        config = (
            "# Managed by Hoardarr.\n"
            "multipaths {\n"
            "    multipath {\n"
            f'        wwid "{wwid}"\n'
            f'        alias "{alias}"\n'
            f"        path_grouping_policy {settings['path_grouping_policy']}\n"
            f'        path_selector "{settings["path_selector"]}"\n'
            f"        failback {settings['failback']}\n"
            f"        no_path_retry {retry_value}\n"
            "    }\n"
            "}\n"
        )
        config_root = paths.multipath_config_root
        if os.name == "nt" and config_root == Path("/etc/multipath/conf.d"):
            config_root = paths.transaction_root.parent / "multipath-config"
        config_root.mkdir(parents=True, exist_ok=True, mode=0o755)
        config_path = config_root / f"hoardarr-{alias}.conf"
        if config_backup is None:
            try:
                previous = config_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                previous = None
            except OSError as exc:
                raise ExecutorFailure(
                    "multipath_config_unavailable",
                    "The existing multipath configuration could not be read.",
                ) from exc
            config_backup = (config_path, previous)
        atomic_text(config_path, config, mode=0o644)
        return config_path

    def restore_multipath_settings() -> None:
        if config_backup is None:
            return
        config_path, previous = config_backup
        try:
            if previous is None:
                config_path.unlink(missing_ok=True)
            else:
                atomic_text(config_path, previous, mode=0o644)
            base_runner([_tool("multipathd"), "reconfigure"], 30)
        except (OSError, ExecutorFailure) as exc:
            raise ExecutorFailure(
                "multipath_config_rollback_failed",
                "The prior multipath configuration could not be restored.",
                needs_attention=True,
            ) from exc

    def create_and_verify_map(kernel_path: str) -> None:
        if not kernel_path.startswith("/dev/") or ".." in PurePosixPath(kernel_path).parts:
            raise ExecutorFailure("path_invalid", "The new kernel path is invalid.")
        set_phase("Preparing multipath")
        apply_multipath_settings()
        try:
            runner([_tool("multipath"), "-t"], 30)
            runner([_tool("multipath"), "-a", wwid], 30)
            create_command = [_tool("multipath"), "-v2"]
            create_command.append(kernel_path)
            runner(create_command, 120)
            runner([_tool("multipathd"), "reconfigure"], 30)
        except ExecutorFailure:
            restore_multipath_settings()
            raise
        # Device Mapper publishes the verified alias asynchronously through udev.
        # Keep the propagation wait bounded so a broken daemon cannot hold the
        # operation forever.
        for _ in range(50):
            if mapper_exists(mapper):
                break
            sleep(0.2)
        else:
            journal["state"] = "needs_attention"
            journal["updated_at"] = time.time()
            atomic_json(journal_path, journal)
            raise ExecutorFailure(
                "multipath_map_unavailable",
                "Linux did not create the reviewed multipath map. "
                "The existing filesystem was not remounted.",
            )
        expected_uuid = plan["before"].get("filesystem_uuid")
        set_phase("Verifying filesystem")
        uuid_reader = filesystem_uuid_provider or (lambda path: _blkid_value(path, "UUID"))
        if expected_uuid and uuid_reader(mapper) != expected_uuid:
            journal["state"] = "needs_attention"
            journal["updated_at"] = time.time()
            atomic_json(journal_path, journal)
            raise ExecutorFailure(
                "filesystem_identity_changed",
                "The multipath map does not expose the reviewed filesystem UUID. "
                "The existing filesystem was not remounted.",
            )

    def retire_path(path: Mapping[str, Any]) -> None:
        # A path that disappeared between review and execution is already absent
        # from the live map. Otherwise fail it first so multipath never attempts
        # additional IO while the provider detaches the old controller path.
        if path.get("present") is False:
            return
        kernel_name = PurePosixPath(str(path.get("kernel_path") or "")).name
        if not kernel_name or kernel_name in {".", ".."}:
            raise ExecutorFailure("path_invalid", "The path being removed is invalid.")
        runner([_tool("multipathd"), "fail", "path", kernel_name], 30)
        runner([_tool("multipathd"), "del", "path", kernel_name], 30)

    def flush_map_with_retry() -> None:
        runner([_tool("udevadm"), "settle", "--timeout=60"], 70)
        command = [_tool("multipath"), "-f", wwid]
        for attempt in range(1, 6):
            try:
                runner(command, 120)
                return
            except ExecutorFailure:
                if attempt == 5:
                    raise
                journal["state"] = "running"
                journal["current_action"] = None
                journal["notices"] = [
                    *journal["notices"],
                    {
                        "code": "multipath_flush_retry",
                        "message": "Waiting for Linux to release the unmounted multipath map.",
                        "attempt": attempt,
                    },
                ]
                journal["updated_at"] = time.time()
                atomic_json(journal_path, journal)
                sleep(0.2 * attempt)

    if plan["operation"] == "redundancy.configure":
        set_phase("Applying provider settings")
        apply_multipath_settings()
        try:
            runner([_tool("multipath"), "-t"], 30)
            runner([_tool("multipathd"), "reconfigure"], 30)
            if not mapper_exists(mapper):
                raise ExecutorFailure(
                    "multipath_map_unavailable",
                    "The redundant storage device disappeared while settings were applied.",
                    needs_attention=True,
                )
        except ExecutorFailure:
            restore_multipath_settings()
            raise
    elif plan["operation"] == "redundancy.replace":
        kernel_path = str(selected_devices[0].get("kernel_path") or "")
        create_and_verify_map(kernel_path)
        removed = plan.get("removed_path")
        if not isinstance(removed, Mapping):
            raise ExecutorFailure("path_invalid", "The path being replaced is invalid.")
        # The map and its mount remain online while the verified replacement is
        # added first and the stale path is removed afterward.
        retire_path(removed)
    elif plan["operation"] == "redundancy.add":
        # Multipath cannot safely claim every provider's already-mounted raw path.
        # Use one controlled transition window: stop using the direct path, build
        # and verify the map, then mount the same filesystem at the same public path.
        public_unmounted = False
        device_unmounted = False
        mapper_mounted = False
        services_stopped = False
        try:
            coordinate_services("stop")
            services_stopped = bool(service_units)
            set_phase("Pausing storage access")
            if mountpoint != device_mountpoint:
                runner([_tool("umount"), mountpoint_text], 120)
                public_unmounted = True
            runner([_tool("umount"), device_mountpoint_text], 120)
            device_unmounted = True
            kernel_path = str(selected_devices[0].get("kernel_path") or "")
            create_and_verify_map(kernel_path)
            set_phase("Activating redundant device")
            runner([_tool("mount"), mapper_text, device_mountpoint_text], 120)
            mapper_mounted = True
            if mountpoint != device_mountpoint:
                runner(
                    [_tool("mount"), "--bind", device_mountpoint_text, mountpoint_text],
                    120,
                )
                public_unmounted = False
            coordinate_services("start")
            services_stopped = False
            set_phase("Verifying applications")
        except ExecutorFailure as exc:
            # Return to the exact reviewed direct path. A failed rollback is
            # surfaced as needs-attention and is never reported as success.
            try:
                if mapper_mounted:
                    runner([_tool("umount"), device_mountpoint_text], 120)
                    device_unmounted = True
                if device_unmounted:
                    runner(
                        [
                            _tool("mount"),
                            str(plan["before"]["presentation_device"]),
                            device_mountpoint_text,
                        ],
                        120,
                    )
                    device_unmounted = False
                if mountpoint != device_mountpoint and public_unmounted:
                    runner(
                        [_tool("mount"), "--bind", device_mountpoint_text, mountpoint_text],
                        120,
                    )
                    public_unmounted = False
                if services_stopped:
                    coordinate_services("start")
                    services_stopped = False
            except ExecutorFailure as rollback_exc:
                raise ExecutorFailure(
                    "redundancy_rollback_failed",
                    "The multipath transition failed and the original mount could not "
                    "be restored automatically.",
                    needs_attention=True,
                ) from rollback_exc
            raise ExecutorFailure(
                "redundancy_transition_failed",
                "The multipath transition failed; the original storage path was restored.",
            ) from exc
    else:
        # Removing one path leaves the existing multipath map online. Transitioning
        # back to a direct device is allowed only when the reviewed result has one path.
        retire_path(selected)
        if len(plan["after"]["path_ids"]) == 1:
            direct_text = str(plan["after"].get("presentation_device") or "")
            if not direct_text.startswith("/dev/") or ".." in PurePosixPath(direct_text).parts:
                raise ExecutorFailure("path_invalid", "The remaining direct path is invalid.")
            public_unmounted = False
            device_unmounted = False
            map_flushed = False
            try:
                if mountpoint != device_mountpoint:
                    runner([_tool("umount"), mountpoint_text], 120)
                    public_unmounted = True
                runner([_tool("umount"), device_mountpoint_text], 120)
                device_unmounted = True
                flush_map_with_retry()
                map_flushed = True
                runner([_tool("mount"), direct_text, device_mountpoint_text], 120)
                device_unmounted = False
                if mountpoint != device_mountpoint:
                    runner(
                        [_tool("mount"), "--bind", device_mountpoint_text, mountpoint_text],
                        120,
                    )
                    public_unmounted = False
            except ExecutorFailure as exc:
                try:
                    if device_unmounted:
                        rollback_source = direct_text if map_flushed else mapper_text
                        runner([_tool("mount"), rollback_source, device_mountpoint_text], 120)
                        device_unmounted = False
                    if mountpoint != device_mountpoint and public_unmounted:
                        runner(
                            [_tool("mount"), "--bind", device_mountpoint_text, mountpoint_text],
                            120,
                        )
                        public_unmounted = False
                except ExecutorFailure as rollback_exc:
                    raise ExecutorFailure(
                        "redundancy_rollback_failed",
                        "The redundancy change failed and the existing mount could not "
                        "be restored automatically.",
                        needs_attention=True,
                    ) from rollback_exc
                raise ExecutorFailure(
                    "redundancy_transition_failed",
                    "The redundancy change failed; the existing storage mount was restored.",
                ) from exc
    result = {
        "operation_id": operation_id,
        "storage_entity_id": plan["storage_entity_id"],
        "logical_storage_identity": plan["logical_storage_identity"],
        "mountpoint": mountpoint_text,
        "filesystem_uuid": plan["after"].get("filesystem_uuid"),
        "presentation_device": plan["after"].get("presentation_device"),
        "path_ids": list(plan["after"]["path_ids"]),
        "topology_state": plan["after"]["topology_state"],
        "replayed": False,
    }
    journal["state"] = "succeeded"
    journal["phase"] = "Controller redundancy updated"
    journal["completed_steps"] = total_steps
    journal["current_action"] = None
    journal["result"] = result
    journal["updated_at"] = time.time()
    atomic_json(journal_path, journal)
    return result


def _read_request(connection: socket.socket) -> dict[str, Any]:
    payload = bytearray()
    while True:
        chunk = connection.recv(min(64 * 1024, MAXIMUM_REQUEST_BYTES + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > MAXIMUM_REQUEST_BYTES:
            raise ExecutorFailure("request_too_large", "The storage request is too large.")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExecutorFailure("request_invalid", "The storage request is invalid.") from exc
    if not isinstance(document, dict):
        raise ExecutorFailure("request_invalid", "The storage request is invalid.")
    return document


def _peer_is_allowed(connection: socket.socket) -> bool:
    import pwd

    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    _pid, uid, _gid = struct.unpack("3i", credentials)
    return uid in {0, pwd.getpwnam("hoardarr").pw_uid}


def _handle(connection: socket.socket, paths: Paths, *, status_only: bool = False) -> None:
    try:
        if not _peer_is_allowed(connection):
            raise ExecutorFailure("peer_forbidden", "The storage request was not authorized.")
        request = _read_request(connection)
        if request.get("operation") == "storage_operation_status":
            if set(request) != {"operation", "operation_id"} or not isinstance(
                request.get("operation_id"), str
            ):
                raise ExecutorFailure("request_invalid", "The storage request is invalid.")
            result = storage_operation_status(str(request["operation_id"]), paths=paths)
        elif status_only:
            raise ExecutorFailure(
                "storage_status_read_only",
                "The storage status service accepts progress requests only.",
            )
        elif request.get("operation") == "apply_device_maintenance":
            result = apply_device_maintenance(request, paths=paths)
        elif request.get("operation") == "apply_snapraid_replacement":
            result = apply_snapraid_replacement(request, paths=paths)
        elif request.get("operation") == "apply_array_replacement":
            result = apply_array_replacement(request, paths=paths)
        elif request.get("operation") == "apply_foreign_inspection":
            result = apply_foreign_inspection(request, paths=paths)
        elif request.get("operation") == "preview_foreign_stack":
            result = preview_foreign_stack(request, paths=paths)
        elif request.get("operation") == "reconcile_storage_access":
            result = reconcile_storage_access(request, paths=paths)
        elif request.get("operation") == "apply_storage_redundancy":
            result = apply_storage_redundancy(request, paths=paths)
        elif request.get("operation") == "apply_storage_volume":
            result = apply_storage_volume(request, paths=paths)
        elif request.get("operation") == "apply_storage_volume_snapshot":
            result = apply_storage_volume_snapshot(request, paths=paths)
        elif request.get("operation") == "apply_storage_volume_capacity":
            result = apply_storage_volume_capacity(request, paths=paths)
        else:
            result = apply_storage_plan(request, paths=paths)
        response = {"ok": True, "result": result}
    except (ExecutorFailure, QuarantineError) as exc:
        response = {
            "ok": False,
            "code": exc.code,
            "message": str(exc),
            "needs_attention": getattr(exc, "needs_attention", False),
        }
    except Exception:
        # Never serialize an unexpected exception to the unprivileged caller,
        # but retain its traceback in the root-only service journal.
        LOGGER.exception("Unexpected storage executor failure")
        response = {
            "ok": False,
            "code": "storage_executor_failed",
            "message": "The privileged storage service could not complete the request.",
            "needs_attention": True,
        }
    encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAXIMUM_RESPONSE_BYTES:
        encoded = (
            b'{"ok":false,"code":"executor_response_too_large",'
            b'"message":"The storage result was too large.","needs_attention":true}'
        )
    with contextlib.suppress(OSError):
        connection.sendall(encoded)


def serve(socket_path: Path, paths: Paths, *, status_only: bool = False) -> None:
    import grp

    if os.geteuid() != 0:
        raise SystemExit("hoardarr-storage-executor must run as root")
    if socket_path.exists() or socket_path.is_symlink():
        details = socket_path.lstat()
        if not stat.S_ISSOCK(details.st_mode) or details.st_uid != 0:
            raise SystemExit(f"refusing to replace unsafe socket path: {socket_path}")
        socket_path.unlink()
    socket_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    os.chown(socket_path, 0, grp.getgrnam("hoardarr").gr_gid)
    os.chmod(socket_path, 0o660)
    server.listen(8)
    server.settimeout(1.0)
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            with connection:
                connection.settimeout(None)
                _handle(connection, paths, status_only=status_only)
    finally:
        server.close()
        with contextlib.suppress(FileNotFoundError):
            socket_path.unlink()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Hoardarr privileged storage executor")
    parser.add_argument("--socket", type=Path, default=Path("/run/hoardarr/storage-executor.sock"))
    parser.add_argument("--detector", type=Path, default=Paths.detector)
    parser.add_argument("--quarantine-marker", type=Path, default=Paths.quarantine_marker)
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="serve read-only operation progress requests and reject storage changes",
    )
    args = parser.parse_args()
    serve(
        args.socket,
        Paths(
            detector=args.detector,
            quarantine_marker=args.quarantine_marker,
            managed_udev_rule=MANAGED_UDEV_RULE,
            managed_storage_state=MANAGED_STORAGE_STATE,
        ),
        status_only=args.status_only,
    )


if __name__ == "__main__":
    main()
