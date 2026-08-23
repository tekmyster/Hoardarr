from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hoardarr.operations.service import document_hash
from hoardarr.storage.layouts import LayoutError, normalize_sector_conversion, normalize_wipe


class MaintenanceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


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


def reviewed_device(disk: Mapping[str, Any]) -> dict[str, Any]:
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


def _capabilities(disk: Mapping[str, Any]) -> Mapping[str, Any]:
    value = disk.get("maintenance_capabilities")
    return value if isinstance(value, Mapping) else {}


def build_plan(
    *,
    disk: Mapping[str, Any],
    hardware_snapshot_sha256: str,
    action: str,
    method: str | None = None,
    passes: int = 1,
    target_logical_bytes: int | None = None,
) -> dict[str, Any]:
    device = reviewed_device(disk)
    if device["stable_identity"] is not True or not isinstance(device["id"], str):
        raise MaintenanceError("drive_identity_unstable", "The drive has no stable identity.")
    if (
        disk.get("system_device") is True
        or disk.get("system_disk") is True
        or disk.get("selectable") is False
        or disk.get("read_only") is True
    ):
        raise MaintenanceError("system_device_forbidden", "System storage cannot be changed.")
    capabilities = _capabilities(disk)
    try:
        if action == "wipe":
            method_name = str(method)
            capability = {
                "hdd_overwrite": disk.get("rotational") is True,
                "ata_secure_erase": capabilities.get("ata_secure_erase") is True,
                "nvme_sanitize": capabilities.get("nvme_block_erase") is True,
                "nvme_crypto_erase": capabilities.get("nvme_crypto_erase") is True,
                "scsi_sanitize": capabilities.get("scsi_block_erase") is True,
                "scsi_crypto_erase": capabilities.get("scsi_crypto_erase") is True,
            }.get(method_name, True)
            capability_source = capabilities.get("source")
            if method_name == "hdd_overwrite" and capability:
                capability_source = "Linux rotational-drive classification"
            options = normalize_wipe(
                {
                    "method": method,
                    "passes": passes,
                    "capability": capability,
                    "capability_source": capability_source,
                }
            )
        elif action == "sector_conversion":
            supported = capabilities.get("supported_logical_sector_bytes")
            options = normalize_sector_conversion(
                {
                    "current_logical_bytes": device["logical_sector_bytes"],
                    "target_logical_bytes": target_logical_bytes,
                    "drive_support": isinstance(supported, list)
                    and target_logical_bytes in supported,
                    "controller_passthrough": capabilities.get("sector_format_passthrough") is True,
                }
            )
        else:
            raise MaintenanceError("maintenance_action_invalid", "The action is unsupported.")
    except LayoutError as exc:
        raise MaintenanceError("maintenance_capability_unavailable", str(exc)) from exc
    return {
        "schema_version": 1,
        "action": action,
        "options": options,
        "device": device,
        "device_binding_sha256": document_hash(device),
        "hardware_snapshot_sha256": hardware_snapshot_sha256,
        "destructive": True,
        "advanced_only": action == "sector_conversion" or method != "quick",
    }


def validate_plan(plan: object) -> dict[str, Any]:
    if not isinstance(plan, dict) or set(plan) != {
        "schema_version",
        "action",
        "options",
        "device",
        "device_binding_sha256",
        "hardware_snapshot_sha256",
        "destructive",
        "advanced_only",
    }:
        raise MaintenanceError("maintenance_plan_invalid", "The maintenance plan is invalid.")
    if plan.get("schema_version") != 1 or plan.get("destructive") is not True:
        raise MaintenanceError("maintenance_plan_invalid", "The maintenance plan is invalid.")
    device = plan.get("device")
    if (
        not isinstance(device, dict)
        or set(device) != set(IDENTITY_FIELDS)
        or device.get("stable_identity") is not True
        or document_hash(device) != plan.get("device_binding_sha256")
    ):
        raise MaintenanceError("maintenance_plan_invalid", "The drive binding is invalid.")
    try:
        if plan.get("action") == "wipe":
            options = normalize_wipe(plan.get("options"))
        elif plan.get("action") == "sector_conversion":
            options = normalize_sector_conversion(plan.get("options"))
        else:
            raise MaintenanceError("maintenance_plan_invalid", "The action is unsupported.")
    except LayoutError as exc:
        raise MaintenanceError("maintenance_plan_invalid", str(exc)) from exc
    if options != plan.get("options"):
        raise MaintenanceError("maintenance_plan_changed", "The maintenance plan changed.")
    return plan
