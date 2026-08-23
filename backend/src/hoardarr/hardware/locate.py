from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from hoardarr.operations.service import document_hash

_ENCLOSURE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SG_NAME = re.compile(r"^sg[0-9]+$")
CommandRunner = Callable[[list[str]], int]


class LocateError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _disk(payload: Mapping[str, Any], device_id: str) -> Mapping[str, Any]:
    disks = payload.get("disks")
    if not isinstance(disks, list):
        raise LocateError("hardware_snapshot_invalid", "The hardware snapshot has no disk list.")
    matches = [
        item
        for item in disks[:8192]
        if isinstance(item, Mapping) and item.get("id") == device_id
    ]
    if len(matches) != 1:
        raise LocateError("drive_not_found", "The selected drive is not present exactly once.")
    return matches[0]


def _binding(disk: Mapping[str, Any]) -> dict[str, Any]:
    connection = disk.get("connection")
    identity = disk.get("identity")
    if not isinstance(connection, Mapping) or not isinstance(identity, Mapping):
        raise LocateError("locate_not_supported", "The drive has no enclosure identity evidence.")
    slot = connection.get("slot")
    try:
        slot_number = int(str(slot), 10)
    except (TypeError, ValueError) as exc:
        raise LocateError(
            "locate_not_supported", "The enclosure did not report a numeric slot."
        ) from exc
    enclosure_id = connection.get("enclosure_id")
    if (
        disk.get("stable_identity") is not True
        or not isinstance(disk.get("id"), str)
        or not isinstance(enclosure_id, str)
        or _ENCLOSURE_ID.fullmatch(enclosure_id) is None
        or connection.get("mapping_confidence") != "high"
        or not 0 <= slot_number <= 255
    ):
        raise LocateError(
            "locate_not_supported",
            "Locate requires stable drive identity and a confirmed SES device-slot mapping.",
        )
    return {
        "device_id": disk["id"],
        "serial": identity.get("serial"),
        "wwn": identity.get("wwn"),
        "enclosure_id": enclosure_id,
        "slot": slot_number,
        "mapping_source": connection.get("mapping_source"),
    }


def build_locate_plan(
    hardware: Mapping[str, Any], *, device_id: str, enabled: bool
) -> dict[str, Any]:
    binding = _binding(_disk(hardware, device_id))
    return {
        "schema_version": 1,
        "action": "locate",
        "enabled": enabled,
        "binding": binding,
        "binding_sha256": document_hash(binding),
    }


def validate_locate_plan(plan: object) -> dict[str, Any]:
    if not isinstance(plan, dict) or set(plan) != {
        "schema_version",
        "action",
        "enabled",
        "binding",
        "binding_sha256",
    }:
        raise LocateError("locate_plan_invalid", "The locate plan is invalid.")
    binding = plan.get("binding")
    if (
        plan.get("schema_version") != 1
        or plan.get("action") != "locate"
        or not isinstance(plan.get("enabled"), bool)
        or not isinstance(binding, dict)
        or set(binding) != {
            "device_id",
            "serial",
            "wwn",
            "enclosure_id",
            "slot",
            "mapping_source",
        }
        or document_hash(binding) != plan.get("binding_sha256")
    ):
        raise LocateError("locate_plan_invalid", "The locate plan binding is invalid.")
    return plan


def revalidate_locate_plan(plan: dict[str, Any], hardware: Mapping[str, Any]) -> None:
    validate_locate_plan(plan)
    current = build_locate_plan(
        hardware,
        device_id=str(plan["binding"]["device_id"]),
        enabled=bool(plan["enabled"]),
    )
    if current["binding_sha256"] != plan["binding_sha256"]:
        raise LocateError(
            "locate_identity_changed",
            "Drive identity or confirmed enclosure location changed before Locate could run.",
        )


def _enclosure_device(sysfs_root: Path, enclosure_id: str) -> str:
    generic_root = sysfs_root / "class" / "enclosure" / enclosure_id / "device" / "scsi_generic"
    try:
        candidates = sorted(
            item.name for item in generic_root.iterdir() if _SG_NAME.fullmatch(item.name)
        )
    except OSError as exc:
        raise LocateError(
            "enclosure_control_unavailable", "The enclosure control endpoint is unavailable."
        ) from exc
    if len(candidates) != 1:
        raise LocateError(
            "enclosure_control_ambiguous",
            "Hoardarr could not identify exactly one SES control endpoint for this enclosure.",
        )
    return f"/dev/{candidates[0]}"


def _command_runner(command: list[str]) -> int:
    executable = shutil.which(command[0], path="/usr/sbin:/usr/bin:/sbin:/bin")
    if executable is None:
        return 127
    try:
        result = subprocess.run(
            [executable, *command[1:]],
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=15,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return 126
    if len(result.stdout) > 1024 * 1024 or len(result.stderr) > 1024 * 1024:
        return 125
    return result.returncode


def execute_locate_plan(
    plan: dict[str, Any],
    hardware: Mapping[str, Any],
    *,
    sysfs_root: Path = Path("/sys"),
    runner: CommandRunner = _command_runner,
) -> dict[str, Any]:
    revalidate_locate_plan(plan, hardware)
    binding = plan["binding"]
    endpoint = _enclosure_device(sysfs_root, str(binding["enclosure_id"]))
    slot_argument = f"--dev-slot-num={binding['slot']}"
    if runner(["sg_ses", slot_argument, "--get=ident", "--readonly", endpoint]) != 0:
        raise LocateError(
            "enclosure_slot_unverified",
            "The SES endpoint did not confirm that the reviewed device slot is addressable.",
        )
    control = "--set=ident" if plan["enabled"] else "--clear=ident"
    if runner(["sg_ses", slot_argument, control, endpoint]) != 0:
        raise LocateError(
            "enclosure_indicator_failed", "The enclosure did not accept the Locate request."
        )
    return {
        "device_id": binding["device_id"],
        "enclosure_id": binding["enclosure_id"],
        "slot": binding["slot"],
        "enabled": plan["enabled"],
        "provider": "sg_ses",
        "verification": "command accepted after read-only slot query",
    }
