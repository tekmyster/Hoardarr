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
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from hoardarr.operations.service import document_hash
from hoardarr.storage.layouts import (
    LayoutError,
    layout_commands,
    mergerfs_expand_commands,
    sector_conversion_commands,
    snapraid_config,
    wipe_commands,
)
from hoardarr.storage.maintenance import (
    MaintenanceError,
)
from hoardarr.storage.maintenance import (
    validate_plan as validate_maintenance_plan,
)
from hoardarr.storage.mergerfs import discover_mergerfs
from hoardarr.storage.quarantine import (
    QuarantineError,
    atomic_json,
    atomic_text,
    validate_quarantine,
)
from hoardarr.storage.redundancy import (
    RedundancyError,
    logical_storage_identity,
    matching_devices,
    stable_path_identity,
    validate_redundancy_plan,
)
from hoardarr.storage.snapraid import (
    SnapraidReplacementError,
    replace_data_entry,
    validate_replacement_plan,
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
    sys_class_block: Path = Path("/sys/class/block")
    proc_swaps: Path = Path("/proc/swaps")
    samba_config: Path = Path("/etc/samba/smb.conf")
    samba_include: Path = Path("/etc/samba/hoardarr-shares.conf")
    dev_by_id: Path = Path("/dev/disk/by-id")
    snapraid_config_root: Path = Path("/etc/snapraid")
    systemd_unit_root: Path = Path("/etc/systemd/system")


CommandRunner = Callable[[list[str], int], None]
InventoryProvider = Callable[[], dict[str, Any]]
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
        raise ExecutorFailure(
            "storage_tool_failed",
            "A storage tool reported a failure. The operation stopped and requires inspection.",
            needs_attention=True,
        ) from exc


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


def _run_smart_test(device: Path, kind: str) -> dict[str, str]:
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
    _smartctl([_tool("smartctl"), "-t", kind, os.fspath(device)])
    deadline = time.monotonic() + maximum_seconds
    time.sleep(5)
    while True:
        capabilities = _smartctl([_tool("smartctl"), "-c", os.fspath(device)]).casefold()
        if "in progress" not in capabilities and "in_progress" not in capabilities:
            break
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
    return {"outcome": "passed", "code": "smart_self_test_passed", "message": ""}


def _live_inventory(paths: Paths) -> dict[str, Any]:
    if not paths.detector.is_file():
        raise ExecutorFailure(
            "hardware_detector_unavailable", "Hardware identity cannot be revalidated."
        )
    try:
        result = subprocess.run(
            [sys.executable, os.fspath(paths.detector), "--format", "json"],
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
) -> tuple[str, str, dict[str, Any], dict[str, Any] | None]:
    if set(request) != {"operation", "operation_id", "plan_sha256", "document", "approval"}:
        raise ExecutorFailure("request_invalid", "The storage request is invalid.")
    operation_id = request.get("operation_id")
    plan_sha = request.get("plan_sha256")
    document = request.get("document")
    approval = request.get("approval")
    if (
        request.get("operation") != "apply_storage_plan"
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
        "current_action": current,
        "estimate": estimate,
        "updated_at": journal.get("updated_at"),
        "result": dict(result)
        if journal.get("state") == "succeeded" and isinstance(result, dict)
        else None,
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


def _append_fstab(paths: Paths, operation_id: str, lines: list[str]) -> None:
    current = paths.fstab.read_text(encoding="utf-8") if paths.fstab.exists() else ""
    marker = f"# BEGIN HOARDARR {operation_id}"
    if marker in current:
        return
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
) -> dict[str, Any]:
    storage = document["storage"]
    topology = storage["topology"]
    devices = _revalidate(document, inventory_provider, paths)
    partitions: dict[str, Path] = {}
    completed: list[str] = []

    for action_index, action in enumerate(storage["actions"]):
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
            smart_outcome = _run_smart_test(device, "short")
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
            smart_outcome = _run_smart_test(device, "long")
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
        completed.append(action["action_id"])
        journal["completed_actions"] = list(completed)
        journal["completed_steps"] = int(journal["completed_steps"]) + 1
        journal["current_action"] = None
        journal["updated_at"] = time.time()
        atomic_json(_journal_path(paths, operation_id), journal)

    if topology == "test":
        return {
            "operation_id": operation_id,
            "topology": topology,
            "selected_device_ids": list(devices),
            "mountpoint": None,
            "completed_actions": completed,
            "notices": list(journal.get("notices", [])),
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
        if partition is None:
            parts = disk.get("partitions") if isinstance(disk.get("partitions"), list) else []
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
        journal["completed_steps"] = int(journal["completed_steps"]) + 1
        journal["current_action"] = None
        journal["updated_at"] = time.time()
        atomic_json(_journal_path(paths, operation_id), journal)

    presentation_root = _safe_mountpoint(str(document["presentation_root"]))
    journal["phase"] = "Building the selected storage layout"
    journal["current_action"] = {"id": "layout", "type": "storage.layout.apply"}
    journal["updated_at"] = time.time()
    atomic_json(_journal_path(paths, operation_id), journal)
    presentation_root.mkdir(parents=True, exist_ok=True, mode=0o750)
    if topology in {"individual", "cache", "block", "import"}:
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
                "use_ino,cache.files=off,dropcacheonclose=true"
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
            prior_branches = [str(item) for item in matches[0].get("branches", [])]
            if len(prior_branches) != len(set(prior_branches)):
                raise ExecutorFailure(
                    "mergerfs_instance_invalid", "The existing branch list is invalid."
                )
            options = ",".join(
                item
                for item in matches[0].get("options", [])
                if isinstance(item, str) and "\x00" not in item
            )
            if not options:
                raise ExecutorFailure(
                    "mergerfs_options_invalid", "Existing mergerFS options are unavailable."
                )
        new_branches = [os.fspath(item) for item in disk_mounts]
        if set(prior_branches).intersection(new_branches):
            raise ExecutorFailure(
                "mergerfs_duplicate_branch",
                "A drive is already a member of this mergerFS instance.",
            )
        branches = ":".join([*prior_branches, *new_branches])
        if mergerfs.get("mode") == "existing":
            try:
                expand_commands = mergerfs_expand_commands(str(combined), new_branches)
            except LayoutError as exc:
                raise ExecutorFailure("mergerfs_options_invalid", str(exc)) from exc
            changed_runtime = False
            try:
                for command in expand_commands:
                    runner([_tool(command.argv[0]), *command.argv[1:]], command.timeout_seconds)
                    changed_runtime = True
            except Exception:
                if changed_runtime:
                    runner(
                        [
                            _tool("setfattr"),
                            "-n",
                            "user.mergerfs.branches",
                            "-v",
                            ":".join(prior_branches),
                            os.fspath(combined / ".mergerfs"),
                        ],
                        120,
                    )
                raise
        else:
            runner([_tool("mergerfs"), "-o", options, branches, os.fspath(combined)], 120)
        fstab_lines.append(f"{branches} {combined} fuse.mergerfs {options},nofail 0 0")
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
        try:
            commands = layout_commands(topology, options, stable_paths)
        except LayoutError as exc:
            raise ExecutorFailure("layout_options_invalid", str(exc)) from exc
        for command in commands:
            devices = _revalidate(document, inventory_provider, paths)
            runner([_tool(command.argv[0]), *command.argv[1:]], command.timeout_seconds)
        if topology == "zfs":
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
                "use_ino,cache.files=off,dropcacheonclose=true"
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
                "category.create=mfs,category.search=ff,use_ino",
                data_branches,
                str(presentation_root),
            ],
            120,
        )
        fstab_lines.append(
            f"{data_branches} {presentation_root} fuse.mergerfs "
            "category.create=mfs,category.search=ff,use_ino,nofail 0 0"
        )
        _install_storage_timer(
            paths,
            unit_name=f"hoardarr-snapraid-sync-{options['name']}",
            description=f"Sync SnapRAID {options['name']}",
            command=[_tool("snapraid"), "-c", os.fspath(config), "sync"],
            schedule=str(options["sync_schedule"]),
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

    journal["completed_steps"] = int(journal["completed_steps"]) + 1
    journal["phase"] = "Creating media and download folders"
    journal["current_action"] = {"id": "directories", "type": "directory.ensure"}
    journal["updated_at"] = time.time()
    atomic_json(_journal_path(paths, operation_id), journal)

    for action in document.get("actions", {}).get("directories", []):
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
        directory.mkdir(parents=True, exist_ok=True, mode=0o770)
    journal["completed_steps"] = int(journal["completed_steps"]) + 1
    journal["phase"] = "Saving automatic mount configuration"
    journal["current_action"] = {"id": "fstab", "type": "mount.configuration.save"}
    journal["updated_at"] = time.time()
    atomic_json(_journal_path(paths, operation_id), journal)
    _append_fstab(paths, operation_id, fstab_lines)
    journal["completed_steps"] = int(journal["completed_steps"]) + 1
    journal["current_action"] = None
    journal["updated_at"] = time.time()
    atomic_json(_journal_path(paths, operation_id), journal)
    connectivity_actions = document.get("actions", {}).get("connectivity", [])
    if connectivity_actions:
        journal["phase"] = "Configuring file access"
        journal["current_action"] = {"id": "smb-shares", "type": "smb.share.ensure"}
        journal["updated_at"] = time.time()
        atomic_json(_journal_path(paths, operation_id), journal)
        account = storage.get("service_account", {})
        _ensure_smb_shares(
            paths,
            operation_id,
            connectivity_actions,
            str(account.get("username")),
            runner,
        )
        journal["completed_steps"] = int(journal["completed_steps"]) + 1
        journal["current_action"] = None
        journal["updated_at"] = time.time()
        atomic_json(_journal_path(paths, operation_id), journal)
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


def apply_storage_plan(
    request: Mapping[str, Any],
    *,
    paths: Paths | None = None,
    inventory_provider: InventoryProvider | None = None,
    runner: CommandRunner = _run,
) -> dict[str, Any]:
    paths = paths or Paths()
    operation_id, plan_sha, document, _approval = _validate_plan(request)
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
    prior = _load_prior_journal(journal_path, plan_sha)
    if prior is not None:
        return {**prior, "replayed": True}
    storage = document["storage"]
    selected_ids = list(storage["snapshot_binding"]["selected_device_ids"])
    with _device_locks(paths, selected_ids):
        # Recheck after locks; another operation may have changed activation or identity.
        _revalidate(document, provider, paths)
        journal: dict[str, Any] = {
            "schema_version": 1,
            "operation_id": operation_id,
            "plan_sha256": plan_sha,
            "state": "running",
            "started_at": time.time(),
            "updated_at": time.time(),
            "completed_actions": [],
            "notices": [],
            "phase": "Validating plan and drive identities",
            "completed_steps": 0,
            "total_steps": (
                len(storage["actions"])
                if storage["topology"] == "test"
                else len(storage["actions"])
                + len(selected_ids)
                + 3
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


def apply_device_maintenance(
    request: Mapping[str, Any],
    *,
    paths: Paths | None = None,
    inventory_provider: InventoryProvider | None = None,
    runner: CommandRunner = _run,
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
        except Exception:
            journal["state"] = "needs_attention"
            journal["updated_at"] = time.time()
            atomic_json(journal_path, journal)
            raise
        journal["state"] = "succeeded"
        journal["phase"] = "Drive maintenance completed"
        journal["result"] = result
        journal["updated_at"] = time.time()
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
            "total_steps": len(commands) + 4,
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
            replacement_mount.mkdir(parents=True, exist_ok=False, mode=0o770)
            runner([_tool("mount"), partition.as_posix(), str(replacement_mount)], 120)
            filesystem_uuid = _blkid_value(partition, "UUID")
            _append_fstab(
                paths,
                operation_id,
                [f"UUID={filesystem_uuid} {replacement_mount} {plan['filesystem']} noatime 0 2"],
            )
            atomic_text(config_path, updated_config, mode=0o640)
            runner([_tool("snapraid"), "-c", str(config_path), "status"], 300)
            fix_started = True
            runner(
                [_tool("snapraid"), "-c", str(config_path), "-d", str(plan["data_name"]), "fix"],
                86400,
            )
            runner([_tool("snapraid"), "-c", str(config_path), "sync"], 86400)
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
    total_steps = 8 if plan["operation"] in {"redundancy.add", "redundancy.replace"} else 6
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
        journal["phase"] = {
            "multipath": "Preparing redundant storage access",
            "multipathd": "Updating controller paths",
            "umount": "Switching the storage access layer",
            "mount": "Restoring the existing storage mount",
        }.get(command_name, "Updating controller redundancy")
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
    if plan["operation"] in {"redundancy.add", "redundancy.replace"}:
        kernel_path = str(selected_devices[0].get("kernel_path") or "")
        if not kernel_path.startswith("/dev/") or ".." in PurePosixPath(kernel_path).parts:
            raise ExecutorFailure("path_invalid", "The new kernel path is invalid.")
        runner([_tool("multipath"), "-a", wwid], 30)
        create_command = [_tool("multipath"), "-v2"]
        policy = str(plan.get("policy") or "recommended")
        if policy != "recommended":
            create_command.extend(["-p", policy])
        create_command.append(kernel_path)
        runner(create_command, 120)
        runner([_tool("multipathd"), "reconfigure"], 30)
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
        if plan["operation"] == "redundancy.replace":
            removed = plan.get("removed_path")
            removed_kernel_path = PurePosixPath(
                str(removed.get("kernel_path") if isinstance(removed, Mapping) else "")
            ).name
            if not removed_kernel_path:
                raise ExecutorFailure("path_invalid", "The path being replaced is invalid.")
            # The map and its mount remain online while the verified replacement is
            # added first and the stale path is removed afterward.
            runner([_tool("multipathd"), "del", "path", removed_kernel_path], 30)
        else:
            # The stable application path is briefly unmounted while its lower device is
            # changed. Share definitions, ACLs, UUIDs, and application paths are untouched.
            mapper_mounted = False
            try:
                if mountpoint != device_mountpoint:
                    runner([_tool("umount"), mountpoint_text], 120)
                runner([_tool("umount"), device_mountpoint_text], 120)
                runner([_tool("mount"), mapper_text, device_mountpoint_text], 120)
                mapper_mounted = True
                if mountpoint != device_mountpoint:
                    runner(
                        [
                            _tool("mount"),
                            "--bind",
                            device_mountpoint_text,
                            mountpoint_text,
                        ],
                        120,
                    )
            except ExecutorFailure as exc:
                # Best-effort return to the exact reviewed direct path. A failed rollback
                # is surfaced as needs-attention and never reported as success.
                try:
                    if mapper_mounted:
                        runner([_tool("umount"), device_mountpoint_text], 120)
                    runner(
                        [
                            _tool("mount"),
                            str(plan["before"]["presentation_device"]),
                            device_mountpoint_text,
                        ],
                        120,
                    )
                    if mountpoint != device_mountpoint:
                        runner(
                            [
                                _tool("mount"),
                                "--bind",
                                device_mountpoint_text,
                                mountpoint_text,
                            ],
                            120,
                        )
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
        removed_kernel_path = PurePosixPath(str(selected.get("kernel_path") or "")).name
        if not removed_kernel_path:
            raise ExecutorFailure("path_invalid", "The path to remove is invalid.")
        runner([_tool("multipathd"), "del", "path", removed_kernel_path], 30)
        if len(plan["after"]["path_ids"]) == 1:
            direct_text = str(plan["after"].get("presentation_device") or "")
            if not direct_text.startswith("/dev/") or ".." in PurePosixPath(direct_text).parts:
                raise ExecutorFailure("path_invalid", "The remaining direct path is invalid.")
            if mountpoint != device_mountpoint:
                runner([_tool("umount"), mountpoint_text], 120)
            runner([_tool("umount"), device_mountpoint_text], 120)
            runner([_tool("multipath"), "-f", wwid], 120)
            runner([_tool("mount"), direct_text, device_mountpoint_text], 120)
            if mountpoint != device_mountpoint:
                runner(
                    [_tool("mount"), "--bind", device_mountpoint_text, mountpoint_text],
                    120,
                )
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
        elif request.get("operation") == "apply_storage_redundancy":
            result = apply_storage_redundancy(request, paths=paths)
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
        Paths(detector=args.detector, quarantine_marker=args.quarantine_marker),
        status_only=args.status_only,
    )


if __name__ == "__main__":
    main()
