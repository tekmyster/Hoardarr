#!/usr/bin/env python3
"""Read-only storage hardware discovery for the Hoardarr bootstrap.

The detector deliberately reads Linux sysfs and the read-only udev database. It
does not load drivers, open block devices, probe devices with vendor utilities,
or issue commands that can change storage. Recorded fixtures use the same
normalized controller and disk records as live discovery.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
HARDWARE_ROOT = REPO_ROOT / "packaging" / "hardware"
# A built release keeps these manifests at ``hardware/`` until install.sh
# stages the final ``packaging/hardware/`` layout.  Supporting both locations
# makes the read-only detector usable for release verification and prevents a
# staging path from turning every scan into a generic worker failure.
BUNDLE_HARDWARE_ROOT = REPO_ROOT / "hardware"
if not HARDWARE_ROOT.is_dir() and BUNDLE_HARDWARE_ROOT.is_dir():
    HARDWARE_ROOT = BUNDLE_HARDWARE_ROOT
PROVIDERS_FILE = HARDWARE_ROOT / "providers.json"
VENDOR_TOOLS_FILE = HARDWARE_ROOT / "vendor-tools.json"

DMI_FILES = {
    "system_vendor": "sys_vendor",
    "product_name": "product_name",
    "product_version": "product_version",
    "board_vendor": "board_vendor",
    "board_name": "board_name",
    "chassis_vendor": "chassis_vendor",
}
DMI_ALIASES = {value: key for key, value in DMI_FILES.items()}
DMI_ALIASES.update({key: key for key in DMI_FILES})

HOST_TRANSPORT_DRIVERS = {
    "bnx2fc",
    "fcoe",
    "fnic",
    "hv_storvsc",
    "lpfc",
    "qedf",
    "qla2xxx",
    "storvsc",
    "storvsc_host",
    "storvsc_host_t",
}

VMBUS_STORAGE_DRIVERS = {
    "hv_storvsc",
    "storvsc",
    "storvsc_host",
    "storvsc_host_t",
}

MATCH_FIELDS = {
    "bus_types",
    "class_prefixes",
    "devices",
    "drivers",
    "subsystem_devices",
    "subsystem_vendors",
    "vendors",
}

KERNEL_SECTOR_BYTES = 512
PCI_ADDRESS_RE = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$")
VMBUS_ADDRESS_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
NON_PHYSICAL_BLOCK_PREFIXES = ("dm-", "fd", "loop", "md", "nbd", "ram", "rbd", "zd", "zram")
HEALTH_STATUSES = {"available", "conflicting", "unavailable"}
HEALTH_CONFIDENCE = {"high", "medium", "low", "conflicting", "unavailable"}
MAPPING_CONFIDENCE = {"high", "medium", "low", "unknown"}
VPD_QUALITY = {"available", "not_reported", "temporarily_unavailable"}


class DetectionError(RuntimeError):
    """A deterministic, user-facing detector input or configuration error."""


def _read_text(path: pathlib.Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8", errors="replace").strip("\x00\r\n \t")
    except (OSError, UnicodeError):
        return None
    return value or None


def _read_binary(path: pathlib.Path, *, limit: int = 64 * 1024) -> bytes | None:
    """Read one bounded sysfs binary attribute without opening the block device."""

    try:
        with path.open("rb") as handle:
            value = handle.read(limit + 1)
    except OSError:
        return None
    return value if 0 < len(value) <= limit else None


_VPD_ASSOCIATIONS = {
    0: "logical_unit",
    1: "target_port",
    2: "target_device",
}
_VPD_DESIGNATOR_TYPES = {
    1: "t10_vendor_id",
    2: "eui",
    3: "naa",
    4: "relative_target_port",
    5: "target_port_group",
    6: "logical_unit_group",
    7: "md5_logical_unit",
    8: "scsi_name",
    9: "protocol_specific_port",
    10: "uuid",
}
_VPD_PROTOCOLS = {
    0: "fibre_channel",
    5: "iscsi",
    6: "sas",
    8: "ata",
    9: "usb",
    14: "pcie",
}


def _vpd_identifier(code_set: int, value: bytes) -> str | None:
    if not value or len(value) > 256:
        return None
    if code_set == 1:
        return value.hex()
    if code_set not in {2, 3}:
        return None
    try:
        text = value.decode("ascii" if code_set == 2 else "utf-8", errors="strict").strip()
    except UnicodeError:
        return None
    if not text or any(ord(character) < 0x20 for character in text):
        return None
    return text[:256]


def _parse_vpd_page_83(value: bytes | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "quality": "not_reported" if value is None else "temporarily_unavailable",
        "source": "sysfs vpd_pg83",
        "designators": [],
        "logical_unit_identifier": None,
        "logical_unit_identifier_type": None,
        "target_port_identifier": None,
        "target_port_identifier_type": None,
    }
    if value is None:
        return result
    if len(value) < 4 or value[1] != 0x83:
        return result
    end = 4 + int.from_bytes(value[2:4], "big")
    if end != len(value) or end > 64 * 1024:
        return result
    designators: list[dict[str, Any]] = []
    offset = 4
    while offset < end:
        if offset + 4 > end:
            return result
        descriptor_length = value[offset + 3]
        descriptor_end = offset + 4 + descriptor_length
        if descriptor_length == 0 or descriptor_end > end:
            return result
        code_set = value[offset] & 0x0F
        protocol_number = value[offset] >> 4
        association_number = (value[offset + 1] >> 4) & 0x03
        designator_number = value[offset + 1] & 0x0F
        identifier = _vpd_identifier(code_set, value[offset + 4 : descriptor_end])
        if identifier is not None:
            designators.append(
                {
                    "association": _VPD_ASSOCIATIONS.get(
                        association_number, f"reserved_{association_number}"
                    ),
                    "designator_type": _VPD_DESIGNATOR_TYPES.get(
                        designator_number, f"reserved_{designator_number}"
                    ),
                    "identifier": identifier,
                    "protocol": _VPD_PROTOCOLS.get(
                        protocol_number, f"protocol_{protocol_number}"
                    )
                    if value[offset + 1] & 0x80
                    else None,
                }
            )
        offset = descriptor_end
    if not designators:
        return result
    result["quality"] = "available"
    result["designators"] = designators[:128]
    for association, field_prefix in (
        ("logical_unit", "logical_unit"),
        ("target_port", "target_port"),
    ):
        selected = next(
            (
                item
                for kind in ("naa", "eui", "scsi_name", "t10_vendor_id")
                for item in designators
                if item["association"] == association and item["designator_type"] == kind
            ),
            None,
        )
        if selected is not None:
            result[f"{field_prefix}_identifier"] = selected["identifier"]
            result[f"{field_prefix}_identifier_type"] = selected["designator_type"]
    return result


def _parse_vpd_page_80(value: bytes | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "quality": "not_reported" if value is None else "temporarily_unavailable",
        "source": "sysfs vpd_pg80",
        "unit_serial": None,
    }
    if value is None or len(value) < 4 or value[1] != 0x80:
        return result
    end = 4 + int.from_bytes(value[2:4], "big")
    if end != len(value) or end > 64 * 1024:
        return result
    try:
        serial = value[4:end].decode("ascii", errors="strict").strip("\x00 \t\r\n")
    except UnicodeError:
        return result
    if not serial or len(serial) > 256 or any(ord(character) < 0x20 for character in serial):
        return result
    result.update({"quality": "available", "unit_serial": serial})
    return result


def _read_driver(path: pathlib.Path) -> str | None:
    try:
        if path.is_symlink():
            return path.resolve(strict=False).name.lower()
    except OSError:
        pass
    value = _read_text(path)
    if value:
        return pathlib.PurePath(value).name.lower()
    return None


def _load_json(path: pathlib.Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DetectionError(f"Cannot read {label} {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DetectionError(f"Invalid JSON in {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DetectionError(f"{label} must be a JSON object")
    return value


def _normalize_hex(value: Any, width: int, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise DetectionError(f"{field} must be a hexadecimal string or integer")
    text = str(value).strip().lower()
    if not text:
        return None
    text = text.removeprefix("0x")
    try:
        number = int(text, 16)
    except ValueError as exc:
        raise DetectionError(f"{field} is not hexadecimal: {value!r}") from exc
    if number < 0 or number >= 16**width:
        raise DetectionError(f"{field} is outside the {width}-digit hexadecimal range")
    return f"0x{number:0{width}x}"


def _normalize_driver(value: Any, field: str = "kernel_driver") -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DetectionError(f"{field} must be a string")
    text = pathlib.PurePath(value.strip()).name.lower()
    return text or None


def _normalize_dmi(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise DetectionError("fixture dmi must be an object")
    result: dict[str, str] = {}
    for key, raw in value.items():
        normalized_key = DMI_ALIASES.get(str(key).lower())
        if normalized_key is None or raw is None:
            continue
        if not isinstance(raw, str):
            raise DetectionError(f"fixture dmi.{key} must be a string")
        text = raw.strip()
        if text:
            result[normalized_key] = text
    return result


def _optional_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DetectionError(f"{field} must be a string")
    text = value.strip()
    return text or None


def _optional_nonnegative_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise DetectionError(f"{field} must be a non-negative integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DetectionError(f"{field} must be a non-negative integer") from exc
    if number < 0:
        raise DetectionError(f"{field} must be a non-negative integer")
    return number


def _optional_nonnegative_number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise DetectionError(f"{field} must be a non-negative number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DetectionError(f"{field} must be a non-negative number") from exc
    if number < 0:
        raise DetectionError(f"{field} must be a non-negative number")
    return number


def _optional_bool(value: Any, field: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int)):
        text = str(value).strip().lower()
        if text in {"0", "false", "no"}:
            return False
        if text in {"1", "true", "yes"}:
            return True
    raise DetectionError(f"{field} must be a boolean")


def _identity_component(value: str) -> str:
    """Return a predictable, non-path stable-ID component."""

    return re.sub(r"[^a-z0-9._-]+", "-", value.strip().lower()).strip("-")


def _canonical_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().lower()
    text = text.removeprefix("0x")
    text = re.sub(r"[^a-z0-9._:-]+", "", text)
    return text or None


def _normalize_vpd_evidence(raw: Any, field: str) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise DetectionError(f"{field} must be an object")
    quality = _optional_string(raw.get("quality", "not_reported"), f"{field}.quality")
    if quality not in VPD_QUALITY:
        raise DetectionError(f"{field}.quality must be one of {sorted(VPD_QUALITY)}")
    raw_designators = raw.get("designators", [])
    if not isinstance(raw_designators, list) or len(raw_designators) > 128:
        raise DetectionError(f"{field}.designators must be a bounded list")
    designators: list[dict[str, Any]] = []
    for index, item in enumerate(raw_designators):
        if not isinstance(item, dict):
            raise DetectionError(f"{field}.designators[{index}] must be an object")
        association = _optional_string(
            item.get("association"), f"{field}.designators[{index}].association"
        )
        designator_type = _optional_string(
            item.get("designator_type"), f"{field}.designators[{index}].designator_type"
        )
        identifier = _optional_string(
            item.get("identifier"), f"{field}.designators[{index}].identifier"
        )
        if not association or not designator_type or not identifier or len(identifier) > 256:
            raise DetectionError(f"{field}.designators[{index}] is incomplete")
        designators.append(
            {
                "association": association,
                "designator_type": designator_type,
                "identifier": identifier,
                "protocol": _optional_string(
                    item.get("protocol"), f"{field}.designators[{index}].protocol"
                ),
            }
        )
    return {
        "quality": quality,
        "source": _optional_string(raw.get("source"), f"{field}.source") or "Not reported",
        "designators": designators,
        "logical_unit_identifier": _optional_string(
            raw.get("logical_unit_identifier"), f"{field}.logical_unit_identifier"
        ),
        "logical_unit_identifier_type": _optional_string(
            raw.get("logical_unit_identifier_type"), f"{field}.logical_unit_identifier_type"
        ),
        "target_port_identifier": _optional_string(
            raw.get("target_port_identifier"), f"{field}.target_port_identifier"
        ),
        "target_port_identifier_type": _optional_string(
            raw.get("target_port_identifier_type"), f"{field}.target_port_identifier_type"
        ),
        "identity_conflict": _optional_bool(
            raw.get("identity_conflict", False), f"{field}.identity_conflict"
        )
        is True,
    }


def _normalize_unit_serial_evidence(raw: Any, field: str) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise DetectionError(f"{field} must be an object")
    quality = _optional_string(raw.get("quality", "not_reported"), f"{field}.quality")
    if quality not in VPD_QUALITY:
        raise DetectionError(f"{field}.quality must be one of {sorted(VPD_QUALITY)}")
    return {
        "quality": quality,
        "source": _optional_string(raw.get("source"), f"{field}.source") or "Not reported",
        "unit_serial": _optional_string(raw.get("unit_serial"), f"{field}.unit_serial"),
        "identity_conflict": _optional_bool(
            raw.get("identity_conflict", False), f"{field}.identity_conflict"
        )
        is True,
    }


def _normalize_smp_evidence(raw: Any, field: str) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise DetectionError(f"{field} must be an object")
    quality = _optional_string(raw.get("quality", "not_reported"), f"{field}.quality")
    if quality not in VPD_QUALITY:
        raise DetectionError(f"{field}.quality must be one of {sorted(VPD_QUALITY)}")
    raw_phys = raw.get("phys", [])
    if not isinstance(raw_phys, list) or len(raw_phys) > 255:
        raise DetectionError(f"{field}.phys must be a bounded list")
    phys: list[dict[str, Any]] = []
    for index, item in enumerate(raw_phys):
        if not isinstance(item, dict):
            raise DetectionError(f"{field}.phys[{index}] must be an object")
        phys.append(
            {
                "phy_id": _optional_nonnegative_int(
                    item.get("phy_id"), f"{field}.phys[{index}].phy_id"
                ),
                "routing": _optional_string(
                    item.get("routing"), f"{field}.phys[{index}].routing"
                ),
                "state": _optional_string(item.get("state"), f"{field}.phys[{index}].state"),
                "negotiated_rate_gbps": _optional_nonnegative_number(
                    item.get("negotiated_rate_gbps"),
                    f"{field}.phys[{index}].negotiated_rate_gbps",
                ),
                "attached_sas_address": _canonical_identifier(
                    _optional_string(
                        item.get("attached_sas_address"),
                        f"{field}.phys[{index}].attached_sas_address",
                    )
                ),
                "attached_phy_id": _optional_nonnegative_int(
                    item.get("attached_phy_id"), f"{field}.phys[{index}].attached_phy_id"
                ),
                "attached_details": _optional_string(
                    item.get("attached_details"), f"{field}.phys[{index}].attached_details"
                ),
                "device_slot_number": _optional_nonnegative_int(
                    item.get("device_slot_number"),
                    f"{field}.phys[{index}].device_slot_number",
                ),
            }
        )
    return {
        "quality": quality,
        "source": _optional_string(raw.get("source"), f"{field}.source") or "Not reported",
        "expander_sas_address": _canonical_identifier(
            _optional_string(
                raw.get("expander_sas_address"), f"{field}.expander_sas_address"
            )
        ),
        "phys": phys,
    }


def _disk_id(
    identity: Mapping[str, Any], vendor: str | None, model: str | None, kernel_name: str
) -> tuple[str, bool]:
    for field, prefix in (("wwn", "wwn"), ("nguid", "nguid"), ("eui64", "eui")):
        value = _canonical_identifier(identity.get(field))
        if value:
            return f"{prefix}:{value}", True
    serial = _optional_string(identity.get("serial"), "disk identity.serial")
    if serial:
        components = [
            _identity_component(item) for item in (vendor or "unknown", model or "unknown", serial)
        ]
        return "serial:" + ":".join(components), True
    # Kernel names are deliberately labelled volatile.  They remain useful for
    # display and diagnostics, but must never silently authorize a later write.
    return f"kernel:{_identity_component(kernel_name)}", False


def _normalize_signature(raw: Any, field: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DetectionError(f"{field} must be an object")
    signature_type = _optional_string(raw.get("type"), f"{field}.type")
    usage = _optional_string(raw.get("usage"), f"{field}.usage")
    if signature_type is None:
        raise DetectionError(f"{field}.type must be a non-empty string")
    return {
        "label": _optional_string(raw.get("label"), f"{field}.label"),
        "source": _optional_string(raw.get("source", "fixture"), f"{field}.source") or "fixture",
        "type": signature_type,
        "usage": usage,
        "uuid": _optional_string(raw.get("uuid"), f"{field}.uuid"),
    }


def _normalize_health_metric(raw: Any, field: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DetectionError(f"{field} must be an object")
    status = _optional_string(raw.get("status"), f"{field}.status")
    confidence = _optional_string(raw.get("confidence"), f"{field}.confidence")
    if status not in HEALTH_STATUSES:
        raise DetectionError(f"{field}.status must be one of {sorted(HEALTH_STATUSES)}")
    if confidence not in HEALTH_CONFIDENCE:
        raise DetectionError(f"{field}.confidence must be one of {sorted(HEALTH_CONFIDENCE)}")
    value = raw.get("value")
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float, str))):
        raise DetectionError(f"{field}.value must be a scalar or null")
    if status != "available" and value is not None:
        raise DetectionError(f"{field}.value must be null when status is {status}")
    if status == "available" and value is None:
        raise DetectionError(f"{field}.value is required when status is available")
    source = _optional_string(raw.get("source"), f"{field}.source")
    captured_at = _optional_string(raw.get("captured_at"), f"{field}.captured_at")
    transport = _optional_string(raw.get("transport"), f"{field}.transport")
    if source is None or captured_at is None or transport is None:
        raise DetectionError(
            f"{field} must record non-empty source, captured_at, and transport provenance"
        )
    observations = raw.get("observations", [])
    if not isinstance(observations, list):
        raise DetectionError(f"{field}.observations must be a list")
    normalized_observations: list[dict[str, Any]] = []
    for index, observation in enumerate(observations):
        observation_field = f"{field}.observations[{index}]"
        if not isinstance(observation, dict):
            raise DetectionError(f"{observation_field} must be an object")
        observation_confidence = _optional_string(
            observation.get("confidence"), f"{observation_field}.confidence"
        )
        if observation_confidence not in HEALTH_CONFIDENCE:
            raise DetectionError(f"{observation_field}.confidence is invalid")
        observation_value = observation.get("value")
        if observation_value is not None and (
            isinstance(observation_value, bool)
            or not isinstance(observation_value, (int, float, str))
        ):
            raise DetectionError(f"{observation_field}.value must be a scalar or null")
        qualifies_as_lifetime = _optional_bool(
            observation.get("qualifies_as_lifetime", False),
            f"{observation_field}.qualifies_as_lifetime",
        )
        normalized_observations.append(
            {
                "captured_at": _optional_string(
                    observation.get("captured_at"), f"{observation_field}.captured_at"
                ),
                "confidence": observation_confidence,
                "qualifies_as_lifetime": bool(qualifies_as_lifetime),
                "reason": _optional_string(
                    observation.get("reason"), f"{observation_field}.reason"
                ),
                "source": _optional_string(
                    observation.get("source"), f"{observation_field}.source"
                ),
                "transport": _optional_string(
                    observation.get("transport"), f"{observation_field}.transport"
                ),
                "unit": _optional_string(observation.get("unit"), f"{observation_field}.unit"),
                "value": observation_value,
            }
        )
        if any(
            normalized_observations[-1][name] is None
            for name in ("captured_at", "source", "transport")
        ):
            raise DetectionError(
                f"{observation_field} must record source, captured_at, and transport provenance"
            )
    return {
        "captured_at": captured_at,
        "confidence": confidence,
        "observations": normalized_observations,
        "reason": _optional_string(raw.get("reason"), f"{field}.reason"),
        "source": source,
        "status": status,
        "transport": transport,
        "unit": _optional_string(raw.get("unit"), f"{field}.unit"),
        "value": value,
    }


def _normalize_partition(raw: Any, disk_index: int, index: int) -> dict[str, Any]:
    field = f"fixture disks[{disk_index}].partitions[{index}]"
    if not isinstance(raw, dict):
        raise DetectionError(f"{field} must be an object")
    kernel_name = _optional_string(raw.get("kernel_name"), f"{field}.kernel_name")
    if kernel_name is None:
        raise DetectionError(f"{field}.kernel_name must be a non-empty string")
    signatures = raw.get("signatures", [])
    if not isinstance(signatures, list):
        raise DetectionError(f"{field}.signatures must be a list")
    normalized_signatures = [
        _normalize_signature(item, f"{field}.signatures[{signature_index}]")
        for signature_index, item in enumerate(signatures)
    ]
    filesystem = raw.get("filesystem")
    if filesystem is not None:
        filesystem = _normalize_signature(filesystem, f"{field}.filesystem")
    signature_scan = raw.get("signature_scan", {})
    if not isinstance(signature_scan, dict):
        raise DetectionError(f"{field}.signature_scan must be an object")
    scan_status = _optional_string(
        signature_scan.get("status", "unavailable"), f"{field}.signature_scan.status"
    )
    if scan_status not in {"complete", "partial", "unavailable"}:
        raise DetectionError(f"{field}.signature_scan.status is invalid")
    mountpoints = raw.get("mountpoints", [])
    if not isinstance(mountpoints, list) or len(mountpoints) > 256:
        raise DetectionError(f"{field}.mountpoints must be a bounded list")
    return {
        "filesystem": filesystem,
        "kernel_name": kernel_name,
        "kernel_path": _optional_string(raw.get("kernel_path"), f"{field}.kernel_path")
        or f"/dev/{kernel_name}",
        "number": _optional_nonnegative_int(raw.get("number"), f"{field}.number"),
        "mountpoints": sorted(
            {
                value
                for item_index, item in enumerate(mountpoints)
                if (
                    value := _optional_string(
                        item, f"{field}.mountpoints[{item_index}]"
                    )
                )
                is not None
            }
        ),
        "signatures": sorted(
            normalized_signatures,
            key=lambda item: (str(item["usage"] or ""), str(item["type"]), str(item["uuid"] or "")),
        ),
        "signature_scan": {
            "reason": _optional_string(
                signature_scan.get("reason"), f"{field}.signature_scan.reason"
            ),
            "source": _optional_string(
                signature_scan.get("source", "fixture"), f"{field}.signature_scan.source"
            )
            or "fixture",
            "status": scan_status,
        },
        "size_bytes": _optional_nonnegative_int(raw.get("size_bytes"), f"{field}.size_bytes"),
        "start_bytes": _optional_nonnegative_int(raw.get("start_bytes"), f"{field}.start_bytes"),
    }


def _normalize_disk(raw: Any, index: int) -> dict[str, Any]:
    field = f"fixture disks[{index}]"
    if not isinstance(raw, dict):
        raise DetectionError(f"{field} must be an object")
    kernel_name = _optional_string(raw.get("kernel_name"), f"{field}.kernel_name")
    if kernel_name is None:
        raise DetectionError(f"{field}.kernel_name must be a non-empty string")
    vendor = _optional_string(raw.get("vendor"), f"{field}.vendor")
    model = _optional_string(raw.get("model"), f"{field}.model")
    raw_identity = raw.get("identity", {})
    if not isinstance(raw_identity, dict):
        raise DetectionError(f"{field}.identity must be an object")
    identity = {
        "eui64": _canonical_identifier(
            _optional_string(raw_identity.get("eui64"), f"{field}.identity.eui64")
        ),
        "nguid": _canonical_identifier(
            _optional_string(raw_identity.get("nguid"), f"{field}.identity.nguid")
        ),
        "serial": _optional_string(raw_identity.get("serial"), f"{field}.identity.serial"),
        "wwn": _canonical_identifier(
            _optional_string(raw_identity.get("wwn"), f"{field}.identity.wwn")
        ),
    }
    raw_identity_evidence = raw.get("identity_evidence", {})
    if not isinstance(raw_identity_evidence, dict):
        raise DetectionError(f"{field}.identity_evidence must be an object")
    identity_evidence = {
        "scsi_vpd_page_83": _normalize_vpd_evidence(
            raw_identity_evidence.get("scsi_vpd_page_83"),
            f"{field}.identity_evidence.scsi_vpd_page_83",
        ),
        "scsi_vpd_page_80": _normalize_unit_serial_evidence(
            raw_identity_evidence.get("scsi_vpd_page_80"),
            f"{field}.identity_evidence.scsi_vpd_page_80",
        ),
    }
    generated_id, generated_stable = _disk_id(identity, vendor, model, kernel_name)
    disk_id = _optional_string(raw.get("id"), f"{field}.id") or generated_id
    stable_identity = _optional_bool(raw.get("stable_identity"), f"{field}.stable_identity")
    if stable_identity is None:
        stable_identity = generated_stable

    sector_sizes = raw.get("sector_sizes", {})
    if not isinstance(sector_sizes, dict):
        raise DetectionError(f"{field}.sector_sizes must be an object")
    connection = raw.get("connection", {})
    if not isinstance(connection, dict):
        raise DetectionError(f"{field}.connection must be an object")
    normalized_connection = {
        key: _optional_string(connection.get(key), f"{field}.connection.{key}")
        for key in (
            "controller_address",
            "enclosure_id",
            "presentation",
            "protocol",
            "slot",
            "transport",
            "transport_host",
            "enclosure_vendor",
            "enclosure_model",
            "enclosure_status",
            "hba_port",
            "phy_id",
            "phy_sas_address",
            "phy_identifier",
            "expander_id",
            "expander_sas_address",
            "path_id",
            "mapping_source",
            "mapping_confidence",
            "mapping_last_confirmed_at",
            "target_port_identifier",
            "target_port_identifier_type",
        )
    }
    normalized_connection["mapping_confidence"] = (
        normalized_connection["mapping_confidence"] or "unknown"
    )
    if normalized_connection["mapping_confidence"] not in MAPPING_CONFIDENCE:
        raise DetectionError(
            f"{field}.connection.mapping_confidence must be one of "
            f"{sorted(MAPPING_CONFIDENCE)}"
        )
    path_components = connection.get("path_components", [])
    if not isinstance(path_components, list) or len(path_components) > 64:
        raise DetectionError(f"{field}.connection.path_components must be a bounded list")
    normalized_path_components: list[str] = []
    for item_index, item in enumerate(path_components):
        normalized_item = _optional_string(
            item, f"{field}.connection.path_components[{item_index}]"
        )
        if normalized_item is None:
            raise DetectionError(
                f"{field}.connection.path_components[{item_index}] must not be empty"
            )
        normalized_path_components.append(normalized_item)
    normalized_connection["path_components"] = normalized_path_components
    normalized_connection["smp"] = _normalize_smp_evidence(
        connection.get("smp"), f"{field}.connection.smp"
    )
    normalized_connection["capable_speed_gbps"] = _optional_nonnegative_number(
        connection.get("capable_speed_gbps"), f"{field}.connection.capable_speed_gbps"
    )
    normalized_connection["negotiated_speed_gbps"] = _optional_nonnegative_number(
        connection.get("negotiated_speed_gbps"),
        f"{field}.connection.negotiated_speed_gbps",
    )
    normalized_connection["minimum_speed_gbps"] = _optional_nonnegative_number(
        connection.get("minimum_speed_gbps"), f"{field}.connection.minimum_speed_gbps"
    )
    for counter_name in (
        "phy_invalid_dwords",
        "phy_disparity_errors",
        "phy_loss_of_sync",
        "phy_reset_problems",
    ):
        normalized_connection[counter_name] = _optional_nonnegative_int(
            connection.get(counter_name), f"{field}.connection.{counter_name}"
        )
    partitions = raw.get("partitions", [])
    signatures = raw.get("signatures", [])
    if not isinstance(partitions, list):
        raise DetectionError(f"{field}.partitions must be a list")
    if not isinstance(signatures, list):
        raise DetectionError(f"{field}.signatures must be a list")
    normalized_partitions = [
        _normalize_partition(item, index, partition_index)
        for partition_index, item in enumerate(partitions)
    ]
    normalized_signatures = [
        _normalize_signature(item, f"{field}.signatures[{signature_index}]")
        for signature_index, item in enumerate(signatures)
    ]
    signature_scan = raw.get("signature_scan", {})
    if not isinstance(signature_scan, dict):
        raise DetectionError(f"{field}.signature_scan must be an object")
    scan_status = _optional_string(
        signature_scan.get("status", "unavailable"), f"{field}.signature_scan.status"
    )
    if scan_status not in {"complete", "partial", "unavailable"}:
        raise DetectionError(f"{field}.signature_scan.status is invalid")
    health = raw.get("health", {})
    if not isinstance(health, dict):
        raise DetectionError(f"{field}.health must be an object")
    if "power_on_hours" not in health:
        raise DetectionError(
            f"{field}.health.power_on_hours is required so unavailable lifetime data is explicit"
        )
    power_on_hours = _normalize_health_metric(
        health["power_on_hours"], f"{field}.health.power_on_hours"
    )
    maintenance = raw.get("maintenance_capabilities", {})
    if not isinstance(maintenance, dict):
        raise DetectionError(f"{field}.maintenance_capabilities must be an object")
    discard = raw.get("discard", {})
    if not isinstance(discard, dict):
        raise DetectionError(f"{field}.discard must be an object")
    supported_formats = maintenance.get("supported_logical_sector_bytes", [])
    if not isinstance(supported_formats, list) or any(
        value not in {512, 520, 528, 4096} for value in supported_formats
    ):
        raise DetectionError(
            f"{field}.maintenance_capabilities.supported_logical_sector_bytes is invalid"
        )
    smart_self_test = maintenance.get("smart_self_test", {})
    if not isinstance(smart_self_test, dict):
        raise DetectionError(f"{field}.maintenance_capabilities.smart_self_test is invalid")
    smart_status = _optional_string(
        smart_self_test.get("status", "not_reported"),
        f"{field}.maintenance_capabilities.smart_self_test.status",
    )
    if smart_status not in {"available", "unsupported", "not_reported"}:
        raise DetectionError(
            f"{field}.maintenance_capabilities.smart_self_test.status is invalid"
        )
    disk_mountpoints = raw.get("mountpoints", [])
    if not isinstance(disk_mountpoints, list) or len(disk_mountpoints) > 256:
        raise DetectionError(f"{field}.mountpoints must be a bounded list")
    return {
        "capacity_bytes": _optional_nonnegative_int(
            raw.get("capacity_bytes"), f"{field}.capacity_bytes"
        ),
        "connection": normalized_connection,
        "discard": {
            "granularity_bytes": _optional_nonnegative_int(
                discard.get("granularity_bytes"), f"{field}.discard.granularity_bytes"
            ),
            "max_bytes": _optional_nonnegative_int(
                discard.get("max_bytes"), f"{field}.discard.max_bytes"
            ),
            "zeroes_data": _optional_bool(
                discard.get("zeroes_data"), f"{field}.discard.zeroes_data"
            ),
        },
        "firmware_revision": _optional_string(
            raw.get("firmware_revision"), f"{field}.firmware_revision"
        ),
        "health": {"power_on_hours": power_on_hours},
        "id": disk_id,
        "identity": identity,
        "identity_evidence": identity_evidence,
        "kernel_name": kernel_name,
        "kernel_path": _optional_string(raw.get("kernel_path"), f"{field}.kernel_path")
        or f"/dev/{kernel_name}",
        "model": model,
        "mountpoints": sorted(
            {
                value
                for item_index, item in enumerate(disk_mountpoints)
                if (
                    value := _optional_string(
                        item, f"{field}.mountpoints[{item_index}]"
                    )
                )
                is not None
            }
        ),
        "maintenance_capabilities": {
            "ata_secure_erase": maintenance.get("ata_secure_erase") is True,
            "nvme_block_erase": maintenance.get("nvme_block_erase") is True,
            "sector_format_passthrough": maintenance.get("sector_format_passthrough") is True,
            "supported_logical_sector_bytes": sorted(set(supported_formats)),
            "source": _optional_string(
                maintenance.get("source"), f"{field}.maintenance_capabilities.source"
            )
            or "Not reported",
            "smart_self_test": {
                "status": smart_status,
                "short_minutes": _optional_nonnegative_int(
                    smart_self_test.get("short_minutes"),
                    f"{field}.maintenance_capabilities.smart_self_test.short_minutes",
                ),
                "extended_minutes": _optional_nonnegative_int(
                    smart_self_test.get("extended_minutes"),
                    f"{field}.maintenance_capabilities.smart_self_test.extended_minutes",
                ),
                "source": _optional_string(
                    smart_self_test.get("source"),
                    f"{field}.maintenance_capabilities.smart_self_test.source",
                )
                or "Not reported",
            },
        },
        "partitions": sorted(
            normalized_partitions,
            key=lambda item: (
                item["start_bytes"] if item["start_bytes"] is not None else -1,
                item["kernel_name"],
            ),
        ),
        "read_only": _optional_bool(raw.get("read_only"), f"{field}.read_only"),
        "removable": _optional_bool(raw.get("removable"), f"{field}.removable"),
        "rotational": _optional_bool(raw.get("rotational"), f"{field}.rotational"),
        "sector_sizes": {
            "logical_bytes": _optional_nonnegative_int(
                sector_sizes.get("logical_bytes"), f"{field}.sector_sizes.logical_bytes"
            ),
            "physical_bytes": _optional_nonnegative_int(
                sector_sizes.get("physical_bytes"), f"{field}.sector_sizes.physical_bytes"
            ),
        },
        "signature_scan": {
            "reason": _optional_string(
                signature_scan.get("reason"), f"{field}.signature_scan.reason"
            ),
            "source": _optional_string(
                signature_scan.get("source", "fixture"), f"{field}.signature_scan.source"
            )
            or "fixture",
            "status": scan_status,
        },
        "signatures": sorted(
            normalized_signatures,
            key=lambda item: (str(item["usage"] or ""), str(item["type"]), str(item["uuid"] or "")),
        ),
        "stable_identity": stable_identity,
        "system_disk": _optional_bool(raw.get("system_disk"), f"{field}.system_disk") is True,
        "vendor": vendor,
        "volatile_locator": True,
    }


def _normalize_controller(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DetectionError(f"fixture controllers[{index}] must be an object")

    aliases = {
        "vendor_id": ("vendor_id", "vendor"),
        "device_id": ("device_id", "device"),
        "subsystem_vendor_id": ("subsystem_vendor_id", "subsystem_vendor"),
        "subsystem_device_id": ("subsystem_device_id", "subsystem_device"),
        "class_code": ("class_code", "class"),
        "kernel_driver": ("kernel_driver", "driver"),
    }

    def first(names: Sequence[str]) -> Any:
        for name in names:
            if name in raw:
                return raw[name]
        return None

    address = raw.get("address")
    if not isinstance(address, str) or not address.strip():
        raise DetectionError(f"fixture controllers[{index}].address must be a non-empty string")
    bus_type = raw.get("bus_type", raw.get("bus", "pci"))
    if not isinstance(bus_type, str) or not bus_type.strip():
        raise DetectionError(f"fixture controllers[{index}].bus_type must be a non-empty string")

    record: dict[str, Any] = {
        "address": address.strip(),
        "bus_type": bus_type.strip().lower(),
        "class_code": _normalize_hex(first(aliases["class_code"]), 6, "class_code"),
        "device_id": _normalize_hex(first(aliases["device_id"]), 4, "device_id"),
        "kernel_driver": _normalize_driver(first(aliases["kernel_driver"])),
        "subsystem_device_id": _normalize_hex(
            first(aliases["subsystem_device_id"]), 4, "subsystem_device_id"
        ),
        "subsystem_vendor_id": _normalize_hex(
            first(aliases["subsystem_vendor_id"]), 4, "subsystem_vendor_id"
        ),
        "vendor_id": _normalize_hex(first(aliases["vendor_id"]), 4, "vendor_id"),
    }
    description = raw.get("description")
    if description is not None:
        if not isinstance(description, str):
            raise DetectionError(f"fixture controllers[{index}].description must be a string")
        if description.strip():
            record["description"] = description.strip()
    attributes = raw.get("attributes")
    if attributes is not None:
        if not isinstance(attributes, dict):
            raise DetectionError(f"fixture controllers[{index}].attributes must be an object")
        normalized_attributes: dict[str, str] = {}
        for key, value in attributes.items():
            if not isinstance(key, str) or not isinstance(value, (str, int, float, bool)):
                raise DetectionError(
                    f"fixture controllers[{index}].attributes must contain scalar values"
                )
            normalized_attributes[key] = str(value)
        if normalized_attributes:
            record["attributes"] = normalized_attributes
    return record


def _load_fixture(
    path: pathlib.Path,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, str],
    list[str],
]:
    value = _load_json(path, "hardware fixture")
    if value.get("schema_version", 1) != 1:
        raise DetectionError("hardware fixture schema_version must be 1")
    raw_controllers = value.get("controllers", [])
    if not isinstance(raw_controllers, list):
        raise DetectionError("fixture controllers must be a list")
    normalized = [_normalize_controller(item, index) for index, item in enumerate(raw_controllers)]
    controllers = [item for item in normalized if item["bus_type"] != "scsi_host"]
    transport_hosts = [item for item in normalized if item["bus_type"] == "scsi_host"]
    raw_transport_hosts = value.get("transport_hosts", [])
    if not isinstance(raw_transport_hosts, list):
        raise DetectionError("fixture transport_hosts must be a list")
    for index, item in enumerate(raw_transport_hosts):
        host = _normalize_controller(item, index)
        if host["bus_type"] != "scsi_host":
            raise DetectionError("fixture transport_hosts entries must use bus_type scsi_host")
        transport_hosts.append(host)
    raw_disks = value.get("disks", [])
    if not isinstance(raw_disks, list):
        raise DetectionError("fixture disks must be a list")
    disks = [_normalize_disk(item, index) for index, item in enumerate(raw_disks)]
    dmi = _normalize_dmi(value.get("dmi", {}))
    raw_bmc = value.get("bmc_interfaces", [])
    if not isinstance(raw_bmc, list) or any(not isinstance(item, str) for item in raw_bmc):
        raise DetectionError("fixture bmc_interfaces must be a list of strings")
    bmc = sorted({item.strip() for item in raw_bmc if item.strip()})
    return controllers, transport_hosts, disks, dmi, bmc


def _discover_pci(
    sysfs_root: pathlib.Path, known_provider_drivers: set[str]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    devices = sysfs_root / "bus" / "pci" / "devices"
    try:
        children = sorted(devices.iterdir(), key=lambda item: item.name)
    except OSError:
        return records
    for child in children:
        if not child.is_dir():
            continue
        try:
            record = {
                "address": child.name,
                "bus_type": "pci",
                "class_code": _normalize_hex(_read_text(child / "class"), 6, "class"),
                "device_id": _normalize_hex(_read_text(child / "device"), 4, "device"),
                "kernel_driver": _read_driver(child / "driver"),
                "subsystem_device_id": _normalize_hex(
                    _read_text(child / "subsystem_device"), 4, "subsystem_device"
                ),
                "subsystem_vendor_id": _normalize_hex(
                    _read_text(child / "subsystem_vendor"), 4, "subsystem_vendor"
                ),
                "vendor_id": _normalize_hex(_read_text(child / "vendor"), 4, "vendor"),
            }
        except DetectionError:
            # A partially populated or concurrently removed sysfs device is not
            # allowed to make the whole host inventory disappear.
            continue
        class_code = record["class_code"]
        if (
            not (
                isinstance(class_code, str)
                and class_code.startswith(("0x01", "0x0c04"))
            )
            and record["kernel_driver"] not in known_provider_drivers
        ):
            # sysfs exposes every PCI function.  Keep mass-storage and Fibre
            # Channel classes plus explicitly supported provider drivers such
            # as Intel VMD, whose PCI class is a system peripheral.
            continue
        records.append(record)
    return records


def _discover_vmbus(sysfs_root: pathlib.Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    devices = sysfs_root / "bus" / "vmbus" / "devices"
    try:
        children = sorted(devices.iterdir(), key=lambda item: item.name)
    except OSError:
        return records
    for child in children:
        driver = _read_driver(child / "driver")
        if driver not in VMBUS_STORAGE_DRIVERS:
            continue
        records.append(
            {
                "address": child.name,
                "bus_type": "vmbus",
                "class_code": None,
                "device_id": None,
                "kernel_driver": driver,
                "subsystem_device_id": None,
                "subsystem_vendor_id": None,
                "vendor_id": None,
            }
        )
    return records


def _discover_transport_hosts(sysfs_root: pathlib.Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    hosts = sysfs_root / "class" / "scsi_host"
    try:
        children = sorted(hosts.iterdir(), key=lambda item: item.name)
    except OSError:
        return records
    for child in children:
        driver = _normalize_driver(_read_text(child / "proc_name"), "proc_name")
        if driver not in HOST_TRANSPORT_DRIVERS:
            continue
        attributes: dict[str, str] = {}
        fc_host = sysfs_root / "class" / "fc_host" / child.name
        for attribute in (
            "node_name",
            "port_name",
            "port_state",
            "speed",
            "supported_speeds",
        ):
            value = _read_text(fc_host / attribute)
            if value:
                attributes[attribute] = value
        record: dict[str, Any] = {
            "address": child.name,
            "bus_type": "scsi_host",
            "class_code": None,
            "device_id": None,
            "kernel_driver": driver,
            "subsystem_device_id": None,
            "subsystem_vendor_id": None,
            "vendor_id": None,
        }
        if attributes:
            record["attributes"] = attributes
        records.append(record)
    return records


def _discover_dmi(sysfs_root: pathlib.Path) -> dict[str, str]:
    base = sysfs_root / "class" / "dmi" / "id"
    result: dict[str, str] = {}
    for output_name, filename in DMI_FILES.items():
        value = _read_text(base / filename)
        if value:
            result[output_name] = value
    return result


def _maintenance_capabilities(
    kernel_path: str, *, protocol: str, transport: str, logical_bytes: int | None
) -> dict[str, Any]:
    """Record sysfs facts; the API worker performs bounded read-only tool probes."""

    del kernel_path, protocol, transport
    return {
        "ata_secure_erase": False,
        "nvme_block_erase": False,
        "sector_format_passthrough": False,
        "supported_logical_sector_bytes": [logical_bytes]
        if logical_bytes in {512, 520, 528, 4096}
        else [],
        "source": "Not reported",
        "smart_self_test": {
            "status": "not_reported",
            "short_minutes": None,
            "extended_minutes": None,
            "source": "Not reported",
        },
    }


def _discover_bmc(sysfs_root: pathlib.Path) -> list[str]:
    base = sysfs_root / "class" / "ipmi"
    try:
        return sorted(item.name for item in base.iterdir() if item.name.startswith("ipmi"))
    except OSError:
        return []


def _read_int(path: pathlib.Path) -> int | None:
    value = _read_text(path)
    if value is None:
        return None
    try:
        result = int(value, 10)
    except ValueError:
        return None
    return result if result >= 0 else None


def _read_bool(path: pathlib.Path) -> bool | None:
    value = _read_text(path)
    if value is None:
        return None
    normalized = value.lower()
    if normalized in {"0", "false", "no"}:
        return False
    if normalized in {"1", "true", "yes"}:
        return True
    return None


def _unescape_udev(value: str) -> str:
    return re.sub(
        r"\\x([0-9a-fA-F]{2})",
        lambda match: chr(int(match.group(1), 16)),
        value,
    ).strip()


def _read_udev_properties(
    block_path: pathlib.Path, udev_data_root: pathlib.Path
) -> tuple[dict[str, str], bool]:
    major_minor = _read_text(block_path / "dev")
    if major_minor is None or not re.fullmatch(r"[0-9]+:[0-9]+", major_minor):
        return {}, False
    path = udev_data_root / f"b{major_minor}"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}, False
    result: dict[str, str] = {}
    for line in lines:
        if not line.startswith("E:") or "=" not in line:
            continue
        key, value = line[2:].split("=", 1)
        if re.fullmatch(r"[A-Z0-9_]+", key):
            result[key] = _unescape_udev(value)
    return result, True


def _udev_signatures(properties: Mapping[str, str]) -> list[dict[str, Any]]:
    signatures: list[dict[str, Any]] = []
    filesystem_type = properties.get("ID_FS_TYPE")
    if filesystem_type:
        signatures.append(
            {
                "label": properties.get("ID_FS_LABEL"),
                "source": "udev",
                "type": filesystem_type,
                "usage": properties.get("ID_FS_USAGE"),
                "uuid": properties.get("ID_FS_UUID"),
            }
        )
    partition_table_type = properties.get("ID_PART_TABLE_TYPE")
    if partition_table_type:
        signatures.append(
            {
                "label": None,
                "source": "udev",
                "type": partition_table_type,
                "usage": "partition_table",
                "uuid": properties.get("ID_PART_TABLE_UUID"),
            }
        )
    return sorted(
        signatures,
        key=lambda item: (str(item["usage"] or ""), str(item["type"]), str(item["uuid"] or "")),
    )


def _signature_scan(udev_available: bool) -> dict[str, Any]:
    if udev_available:
        return {
            "reason": (
                "udev reports recognized active signatures only; a privileged, "
                "read-only on-media signature scan has not run."
            ),
            "source": "udev",
            "status": "partial",
        }
    return {
        "reason": "No udev signature metadata was available; no block device was opened.",
        "source": "sysfs",
        "status": "unavailable",
    }


def _resolved_path(path: pathlib.Path) -> pathlib.Path:
    try:
        return path.resolve(strict=False)
    except OSError:
        return path


def _topology_components(path: pathlib.Path, sysfs_root: pathlib.Path) -> list[pathlib.Path]:
    resolved = _resolved_path(path)
    root = _resolved_path(sysfs_root)
    components: list[pathlib.Path] = []
    current = resolved
    while True:
        components.append(current)
        if current == root or current.parent == current:
            break
        current = current.parent
    return list(reversed(components))


def _ancestor_drivers(path: pathlib.Path, sysfs_root: pathlib.Path) -> set[str]:
    return {
        driver
        for component in _topology_components(path, sysfs_root)
        if (driver := _read_driver(component / "driver")) is not None
    }


def _parse_speed_gbps(value: str | None) -> float | None:
    if not value or value.casefold() in {"unknown", "not negotiated"}:
        return None
    matches = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*(?:gbit|gb/s|gbps|gt/s)", value.casefold())
    return max((float(item) for item in matches), default=None)


def _speed_from_mask(value: str | None, rates: Sequence[float]) -> float | None:
    if not value:
        return None
    try:
        mask = int(value, 0)
    except ValueError:
        return _parse_speed_gbps(value)
    enabled = [rate for index, rate in enumerate(rates) if mask & (1 << index)]
    return max(enabled, default=None)


def _discover_enclosure(
    block_path: pathlib.Path, sysfs_root: pathlib.Path
) -> dict[str, str | None]:
    device = block_path / "device"
    try:
        candidates = sorted(device.glob("enclosure_device:*"), key=lambda item: item.name)
    except OSError:
        candidates = []
    for candidate in candidates:
        resolved = _resolved_path(candidate)
        parts = resolved.parts
        enclosure_id: str | None = None
        try:
            enclosure_index = parts.index("enclosure")
        except ValueError:
            enclosure_index = -1
        if enclosure_index >= 0 and len(parts) > enclosure_index + 1:
            enclosure_id = parts[enclosure_index + 1]
        slot = _read_text(candidate / "slot") or resolved.name
        enclosure_root = (
            sysfs_root / "class" / "enclosure" / enclosure_id if enclosure_id else None
        )
        return {
            "id": enclosure_id,
            "slot": slot,
            "vendor": _read_text(enclosure_root / "device" / "vendor")
            if enclosure_root
            else None,
            "model": _read_text(enclosure_root / "device" / "model")
            if enclosure_root
            else None,
            "status": _read_text(candidate / "status"),
            "mapping_source": "sysfs enclosure_device",
            "mapping_confidence": "high",
        }
    return {
        "id": None,
        "slot": None,
        "vendor": None,
        "model": None,
        "status": None,
        "mapping_source": None,
        "mapping_confidence": "unknown",
    }


def _discover_link_speeds(
    topology_names: Sequence[str], transport_host: str | None, sysfs_root: pathlib.Path
) -> tuple[float | None, float | None]:
    if transport_host:
        fc_host = sysfs_root / "class" / "fc_host" / transport_host
        negotiated = _parse_speed_gbps(_read_text(fc_host / "speed"))
        capable = _parse_speed_gbps(_read_text(fc_host / "supported_speeds"))
        if negotiated is not None or capable is not None:
            return capable, negotiated

    for name in reversed(topology_names):
        if name.startswith("phy-"):
            phy = sysfs_root / "class" / "sas_phy" / name
            negotiated = _parse_speed_gbps(_read_text(phy / "negotiated_linkrate"))
            capable = _parse_speed_gbps(_read_text(phy / "maximum_linkrate_hw"))
            if negotiated is not None or capable is not None:
                return capable, negotiated
        if name.startswith("port-"):
            port_device = sysfs_root / "class" / "sas_port" / name / "device"
            try:
                phys = sorted(port_device.glob("phy-*"), key=lambda item: item.name)
            except OSError:
                phys = []
            rates = [
                (
                    _parse_speed_gbps(
                        _read_text(
                            sysfs_root / "class" / "sas_phy" / phy.name / "maximum_linkrate_hw"
                        )
                    ),
                    _parse_speed_gbps(
                        _read_text(
                            sysfs_root / "class" / "sas_phy" / phy.name / "negotiated_linkrate"
                        )
                    ),
                )
                for phy in phys
            ]
            capable = max((item[0] for item in rates if item[0] is not None), default=None)
            negotiated = max((item[1] for item in rates if item[1] is not None), default=None)
            if capable is not None or negotiated is not None:
                return capable, negotiated

    for name in reversed(topology_names):
        if re.fullmatch(r"link[0-9]+", name):
            link = sysfs_root / "class" / "ata_link" / name
            negotiated = _parse_speed_gbps(_read_text(link / "sata_spd"))
            capable = _speed_from_mask(
                _read_text(link / "hw_sata_spd_limit"), (1.5, 3.0, 6.0, 12.0, 24.0)
            )
            if negotiated is not None or capable is not None:
                return capable, negotiated
    return None, None


def _discover_sas_phy(
    topology_names: Sequence[str], sysfs_root: pathlib.Path
) -> dict[str, Any]:
    """Return only counters reported for the exact SAS PHY in this device path."""
    phy_name = next((name for name in reversed(topology_names) if name.startswith("phy-")), None)
    if phy_name is None:
        return {
            "id": None,
            "sas_address": None,
            "identifier": None,
            "minimum_speed_gbps": None,
            "invalid_dwords": None,
            "disparity_errors": None,
            "loss_of_sync": None,
            "reset_problems": None,
        }
    phy = sysfs_root / "class" / "sas_phy" / phy_name
    return {
        "id": phy_name,
        "sas_address": _read_text(phy / "sas_address")
        or _read_text(phy / "device" / "sas_address"),
        "identifier": _read_text(phy / "phy_identifier"),
        "minimum_speed_gbps": _parse_speed_gbps(_read_text(phy / "minimum_linkrate_hw")),
        "invalid_dwords": _read_int(phy / "invalid_dword_count"),
        "disparity_errors": _read_int(phy / "running_disparity_error_count"),
        "loss_of_sync": _read_int(phy / "loss_of_dword_sync_count"),
        "reset_problems": _read_int(phy / "phy_reset_problem_count"),
    }


def _nvme_controller_name(kernel_name: str) -> str | None:
    match = re.match(r"^(nvme[0-9]+)n[0-9]+", kernel_name)
    return match.group(1) if match else None


def _discover_connection(
    block_path: pathlib.Path,
    kernel_name: str,
    sysfs_root: pathlib.Path,
    properties: Mapping[str, str],
    captured_at: str,
) -> dict[str, Any]:
    topology = _topology_components(block_path, sysfs_root)
    topology_names = [item.name for item in topology]
    drivers = _ancestor_drivers(block_path, sysfs_root)

    controller_address = next(
        (name.lower() for name in reversed(topology_names) if PCI_ADDRESS_RE.fullmatch(name)),
        None,
    )
    if controller_address is None:
        controller_address = next(
            (name.lower() for name in reversed(topology_names) if VMBUS_ADDRESS_RE.fullmatch(name)),
            None,
        )

    transport_host = next(
        (name for name in reversed(topology_names) if re.fullmatch(r"host[0-9]+", name)), None
    )
    hba_port = next(
        (
            name
            for name in reversed(topology_names)
            if re.fullmatch(r"port-[0-9]+(?::[0-9]+)+", name)
        ),
        None,
    )
    expander_id = next(
        (name for name in reversed(topology_names) if name.startswith("expander-")),
        None,
    )
    expander_sas_address = (
        _read_text(sysfs_root / "class" / "sas_device" / expander_id / "sas_address")
        or _read_text(
            sysfs_root / "class" / "sas_device" / str(expander_id) / "device" / "sas_address"
        )
        if expander_id
        else None
    )
    path_id = next(
        (
            name
            for name in reversed(topology_names)
            if re.fullmatch(r"(?:rport|end_device|target)-.+", name)
        ),
        None,
    )

    presentation: str | None = None
    if drivers & VMBUS_STORAGE_DRIVERS or any("vmbus" in name.lower() for name in topology_names):
        presentation = "hyperv-scsi"

    transport = properties.get("ID_BUS", "").lower() or None
    protocol: str | None = None
    if "uas" in drivers:
        transport, protocol = "usb", "uas"
    elif "usb-storage" in drivers or any(name.startswith("usb") for name in topology_names):
        transport, protocol = "usb", "usb-storage"
    elif kernel_name.startswith("nvme") or "nvme" in drivers:
        controller = _nvme_controller_name(kernel_name)
        nvme_transport = _read_text(sysfs_root / "class" / "nvme" / str(controller) / "transport")
        transport = (nvme_transport or "nvme").lower()
        protocol = "nvme"
    elif presentation == "hyperv-scsi":
        transport, protocol = "scsi", "storvsc"
    elif properties.get("ID_ATA") == "1":
        transport, protocol = transport or "ata", "ata"
    elif properties.get("ID_SCSI") == "1" or "sd" in drivers:
        transport, protocol = transport or "scsi", "scsi"

    enclosure = _discover_enclosure(block_path, sysfs_root)
    capable_speed, negotiated_speed = _discover_link_speeds(
        topology_names, transport_host, sysfs_root
    )
    sas_phy = _discover_sas_phy(topology_names, sysfs_root)
    return {
        "capable_speed_gbps": capable_speed,
        "controller_address": controller_address,
        "enclosure_id": enclosure["id"],
        "enclosure_model": enclosure["model"],
        "enclosure_status": enclosure["status"],
        "enclosure_vendor": enclosure["vendor"],
        "expander_id": expander_id,
        "expander_sas_address": expander_sas_address,
        "hba_port": hba_port,
        "phy_id": sas_phy["id"],
        "phy_sas_address": sas_phy["sas_address"],
        "phy_identifier": sas_phy["identifier"],
        "minimum_speed_gbps": sas_phy["minimum_speed_gbps"],
        "phy_invalid_dwords": sas_phy["invalid_dwords"],
        "phy_disparity_errors": sas_phy["disparity_errors"],
        "phy_loss_of_sync": sas_phy["loss_of_sync"],
        "phy_reset_problems": sas_phy["reset_problems"],
        "negotiated_speed_gbps": negotiated_speed,
        "mapping_confidence": enclosure["mapping_confidence"],
        "mapping_last_confirmed_at": (
            captured_at if enclosure["mapping_confidence"] != "unknown" else None
        ),
        "mapping_source": enclosure["mapping_source"],
        "presentation": presentation,
        "path_components": topology_names[-64:],
        "path_id": path_id,
        "protocol": protocol,
        "slot": enclosure["slot"],
        "smp": {
            "quality": "not_reported",
            "source": "Not reported",
            "expander_sas_address": None,
            "phys": [],
        },
        "target_port_identifier": None,
        "target_port_identifier_type": None,
        "transport": transport or "unknown",
        "transport_host": transport_host,
    }


def _discover_scsi_identity(
    block_path: pathlib.Path, identity: dict[str, str | None]
) -> dict[str, dict[str, Any]]:
    page_83 = _parse_vpd_page_83(_read_binary(block_path / "device" / "vpd_pg83"))
    page_80 = _parse_vpd_page_80(_read_binary(block_path / "device" / "vpd_pg80"))

    logical_unit = _canonical_identifier(page_83.get("logical_unit_identifier"))
    logical_unit_type = page_83.get("logical_unit_identifier_type")
    if logical_unit and logical_unit_type in {"naa", "eui"}:
        current = _canonical_identifier(identity.get("wwn"))
        if current is None:
            identity["wwn"] = logical_unit
        elif current != logical_unit:
            page_83["identity_conflict"] = True
    else:
        page_83["identity_conflict"] = False

    unit_serial = page_80.get("unit_serial")
    if isinstance(unit_serial, str):
        current_serial = identity.get("serial")
        if current_serial is None:
            identity["serial"] = unit_serial
        elif current_serial.strip().casefold() != unit_serial.strip().casefold():
            page_80["identity_conflict"] = True
    page_80.setdefault("identity_conflict", False)
    page_83.setdefault("identity_conflict", False)
    return {"scsi_vpd_page_83": page_83, "scsi_vpd_page_80": page_80}


def _connection_description(connection: Mapping[str, str | None]) -> str:
    physical = (
        "/".join(
            item
            for item in (connection.get("transport"), connection.get("protocol"))
            if item and item != "unknown"
        )
        or "unknown"
    )
    presentation = connection.get("presentation")
    return f"{physical} -> {presentation}" if presentation else physical


def _disk_health(captured_at: str, connection: Mapping[str, str | None]) -> dict[str, Any]:
    # Linux sysfs has no authoritative SMART/NVMe lifetime counter.  In
    # particular, device attachment uptime is not drive power-on hours.
    return {
        "power_on_hours": {
            "captured_at": captured_at,
            "confidence": "unavailable",
            "observations": [],
            "reason": (
                "Lifetime SMART/NVMe power-on hours are not exposed by sysfs; "
                "OS attachment duration is intentionally not substituted."
            ),
            "source": "sysfs",
            "status": "unavailable",
            "transport": _connection_description(connection),
            "unit": "hours",
            "value": None,
        }
    }


def _partition_parent_name(path: pathlib.Path) -> str | None:
    resolved = _resolved_path(path)
    parent = resolved.parent
    if parent.name and parent.name != "block":
        return parent.name
    return None


def _discover_partitions(
    disk_path: pathlib.Path,
    kernel_name: str,
    block_entries: Sequence[pathlib.Path],
    udev_data_root: pathlib.Path,
    mountpoints_by_device: Mapping[str, list[str]],
) -> list[dict[str, Any]]:
    candidates: dict[str, pathlib.Path] = {}
    try:
        for child in disk_path.iterdir():
            if _read_text(child / "partition") is not None:
                candidates[child.name] = child
    except OSError:
        pass
    for child in block_entries:
        if _read_text(child / "partition") is None:
            continue
        if _partition_parent_name(child) == kernel_name:
            candidates[child.name] = child

    partitions: list[dict[str, Any]] = []
    for name, path in sorted(candidates.items()):
        properties, udev_available = _read_udev_properties(path, udev_data_root)
        signatures = _udev_signatures(properties)
        filesystem = next((item for item in signatures if item.get("usage") == "filesystem"), None)
        start = _read_int(path / "start")
        sectors = _read_int(path / "size")
        partitions.append(
            {
                "filesystem": filesystem,
                "kernel_name": name,
                "kernel_path": f"/dev/{name}",
                "mountpoints": mountpoints_by_device.get(_read_text(path / "dev") or "", []),
                "number": _read_int(path / "partition"),
                "signatures": signatures,
                "signature_scan": _signature_scan(udev_available),
                "size_bytes": sectors * KERNEL_SECTOR_BYTES if sectors is not None else None,
                "start_bytes": start * KERNEL_SECTOR_BYTES if start is not None else None,
            }
        )
    return sorted(
        partitions,
        key=lambda item: (
            item["start_bytes"] if item["start_bytes"] is not None else -1,
            item["kernel_name"],
        ),
    )


def _first_text(
    paths: Sequence[pathlib.Path], properties: Mapping[str, str], property_names: Sequence[str] = ()
) -> str | None:
    for path in paths:
        value = _read_text(path)
        if value:
            return value
    for name in property_names:
        value = properties.get(name)
        if value:
            return value
    return None


SYSTEM_MOUNTPOINTS = frozenset({"/", "/boot", "/boot/efi", "/efi", "/recovery"})


def _system_mount_devices(mountinfo_path: pathlib.Path) -> set[str]:
    try:
        lines = mountinfo_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    devices: set[str] = set()
    for line in lines:
        fields = line.split()
        if len(fields) < 6:
            continue
        mountpoint = fields[4].replace("\\040", " ")
        if mountpoint in SYSTEM_MOUNTPOINTS or mountpoint.startswith("/boot/"):
            devices.add(fields[2])
    return devices


def _swap_device_names(proc_swaps_path: pathlib.Path) -> set[str]:
    try:
        lines = proc_swaps_path.read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        return set()
    result: set[str] = set()
    for line in lines:
        fields = line.split()
        if fields and fields[0].startswith("/dev/"):
            result.add(pathlib.Path(fields[0]).name)
    return result


def _system_backing_disks(
    sysfs_root: pathlib.Path, entries: Sequence[pathlib.Path]
) -> set[str]:
    mountinfo_path = (
        pathlib.Path("/proc/self/mountinfo")
        if sysfs_root == pathlib.Path("/sys")
        else sysfs_root / "proc" / "self" / "mountinfo"
    )
    proc_swaps_path = (
        pathlib.Path("/proc/swaps")
        if sysfs_root == pathlib.Path("/sys")
        else sysfs_root / "proc" / "swaps"
    )
    system_devices = _system_mount_devices(mountinfo_path)

    nodes: dict[str, pathlib.Path] = {}
    for entry in entries:
        nodes[entry.name] = entry
        try:
            children = tuple(entry.iterdir())
        except OSError:
            children = ()
        for child in children:
            if _read_text(child / "partition") is not None:
                nodes[child.name] = child

    def resolve(name: str, visited: set[str]) -> set[str]:
        if name in visited:
            return set()
        visited.add(name)
        path = nodes.get(name)
        if path is None:
            return set()
        try:
            slaves = tuple((path / "slaves").iterdir())
        except OSError:
            slaves = ()
        if slaves:
            result: set[str] = set()
            for slave in slaves:
                result.update(resolve(slave.name, visited))
            return result
        if _read_text(path / "partition") is not None:
            parent = _partition_parent_name(path)
            return resolve(parent, visited) if parent else set()
        return {name}

    roots = {
        name
        for name, path in nodes.items()
        if _read_text(path / "dev") in system_devices
    }
    roots.update(name for name in _swap_device_names(proc_swaps_path) if name in nodes)
    result: set[str] = set()
    for name in roots:
        result.update(resolve(name, set()))
    return result


def _discover_disks(
    sysfs_root: pathlib.Path, udev_data_root: pathlib.Path, captured_at: str
) -> list[dict[str, Any]]:
    block_root = sysfs_root / "class" / "block"
    try:
        entries = sorted(block_root.iterdir(), key=lambda item: item.name)
    except OSError:
        return []
    system_disks = _system_backing_disks(sysfs_root, entries)
    mountinfo_path = (
        pathlib.Path("/proc/self/mountinfo")
        if sysfs_root == pathlib.Path("/sys")
        else sysfs_root / "proc" / "self" / "mountinfo"
    )
    mountpoints_by_device: dict[str, list[str]] = {}
    try:
        mountinfo_lines = mountinfo_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        mountinfo_lines = []
    for line in mountinfo_lines:
        fields = line.split()
        if len(fields) < 5:
            continue
        mountpoints_by_device.setdefault(fields[2], []).append(
            fields[4].replace("\\040", " ").replace("\\011", "\t")
        )

    disks: list[dict[str, Any]] = []
    for block_path in entries:
        kernel_name = block_path.name
        if kernel_name.startswith(NON_PHYSICAL_BLOCK_PREFIXES):
            continue
        if _read_text(block_path / "partition") is not None:
            continue
        scsi_type = _read_int(block_path / "device" / "type")
        if scsi_type is not None and scsi_type != 0:
            continue
        sectors = _read_int(block_path / "size")
        if sectors is None:
            continue

        properties, udev_available = _read_udev_properties(block_path, udev_data_root)
        vendor = _first_text(
            (block_path / "device" / "vendor",),
            properties,
            ("ID_VENDOR", "ID_VENDOR_FROM_DATABASE"),
        )
        model = _first_text((block_path / "device" / "model",), properties, ("ID_MODEL",))
        firmware = _first_text(
            (block_path / "device" / "rev", block_path / "device" / "firmware_rev"),
            properties,
            ("ID_REVISION",),
        )
        identity = {
            "eui64": _canonical_identifier(
                _first_text((block_path / "device" / "eui",), properties, ("ID_NVME_EUI64",))
            ),
            "nguid": _canonical_identifier(
                _first_text((block_path / "device" / "nguid",), properties, ("ID_NVME_NGUID",))
            ),
            "serial": _first_text(
                (block_path / "device" / "serial",),
                properties,
                ("ID_SERIAL_SHORT", "ID_SERIAL"),
            ),
            "wwn": _canonical_identifier(
                _first_text(
                    (block_path / "device" / "wwid", block_path / "wwid"),
                    properties,
                    ("ID_WWN_WITH_EXTENSION", "ID_WWN"),
                )
            ),
        }
        identity_evidence = _discover_scsi_identity(block_path, identity)
        disk_id, stable_identity = _disk_id(identity, vendor, model, kernel_name)
        connection = _discover_connection(
            block_path, kernel_name, sysfs_root, properties, captured_at
        )
        vpd_page_83 = identity_evidence["scsi_vpd_page_83"]
        connection["target_port_identifier"] = vpd_page_83.get("target_port_identifier")
        connection["target_port_identifier_type"] = vpd_page_83.get(
            "target_port_identifier_type"
        )
        signatures = _udev_signatures(properties)
        kernel_path = f"/dev/{kernel_name}"
        disks.append(
            {
                "capacity_bytes": sectors * KERNEL_SECTOR_BYTES,
                "connection": connection,
                "discard": {
                    "granularity_bytes": _read_int(
                        block_path / "queue" / "discard_granularity"
                    ),
                    "max_bytes": _read_int(block_path / "queue" / "discard_max_bytes"),
                    "zeroes_data": _read_bool(block_path / "queue" / "discard_zeroes_data"),
                },
                "firmware_revision": firmware,
                "health": _disk_health(captured_at, connection),
                "id": disk_id,
                "identity": identity,
                "identity_evidence": identity_evidence,
                "kernel_name": kernel_name,
                "kernel_path": kernel_path,
                "maintenance_capabilities": _maintenance_capabilities(
                    kernel_path,
                    protocol=str(connection.get("protocol") or ""),
                    transport=str(connection.get("transport") or ""),
                    logical_bytes=_read_int(block_path / "queue" / "logical_block_size"),
                ),
                "model": model,
                "mountpoints": mountpoints_by_device.get(_read_text(block_path / "dev") or "", []),
                "partitions": _discover_partitions(
                    block_path, kernel_name, entries, udev_data_root, mountpoints_by_device
                ),
                "read_only": _read_bool(block_path / "ro"),
                "removable": _read_bool(block_path / "removable"),
                "rotational": _read_bool(block_path / "queue" / "rotational"),
                "sector_sizes": {
                    "logical_bytes": _read_int(block_path / "queue" / "logical_block_size"),
                    "physical_bytes": _read_int(block_path / "queue" / "physical_block_size"),
                },
                "signature_scan": _signature_scan(udev_available),
                "signatures": signatures,
                "stable_identity": stable_identity,
                "system_disk": kernel_name in system_disks,
                "vendor": vendor,
                "volatile_locator": True,
            }
        )
    return sorted(disks, key=lambda item: (str(item["id"]), item["kernel_name"]))


def _discover_sysfs(
    sysfs_root: pathlib.Path,
    udev_data_root: pathlib.Path,
    providers: Sequence[Mapping[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, str],
    list[str],
]:
    known_provider_drivers = {
        driver for provider in providers for driver in provider.get("match", {}).get("drivers", [])
    }
    controllers = [
        *_discover_pci(sysfs_root, known_provider_drivers),
        *_discover_vmbus(sysfs_root),
    ]
    transport_hosts = _discover_transport_hosts(sysfs_root)
    captured_at = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
    disks = _discover_disks(sysfs_root, udev_data_root, captured_at)
    return (
        controllers,
        transport_hosts,
        disks,
        _discover_dmi(sysfs_root),
        _discover_bmc(sysfs_root),
    )


def _controller_sort_key(record: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        str(record.get("bus_type") or ""),
        str(record.get("address") or ""),
        str(record.get("kernel_driver") or ""),
        str(record.get("vendor_id") or ""),
        str(record.get("device_id") or ""),
    )


def _validate_registry(
    providers_document: dict[str, Any], vendor_document: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if providers_document.get("schema_version") != 1:
        raise DetectionError("provider registry schema_version must be 1")
    providers = providers_document.get("providers")
    platforms = providers_document.get("platform_recommendations", [])
    if not isinstance(providers, list) or any(not isinstance(item, dict) for item in providers):
        raise DetectionError("provider registry providers must be a list of objects")
    if not isinstance(platforms, list) or any(not isinstance(item, dict) for item in platforms):
        raise DetectionError("platform_recommendations must be a list of objects")

    provider_ids: set[str] = set()
    platform_ids: set[str] = set()
    for label, entries, seen in (
        ("provider", providers, provider_ids),
        ("platform recommendation", platforms, platform_ids),
    ):
        for entry in entries:
            identifier = entry.get("id")
            if not isinstance(identifier, str) or not identifier:
                raise DetectionError(f"every {label} needs a non-empty id")
            if identifier in seen:
                raise DetectionError(f"duplicate {label} id: {identifier}")
            seen.add(identifier)
            if not isinstance(entry.get("packages", []), list):
                raise DetectionError(f"{label} {identifier} packages must be a list")
            if not isinstance(entry.get("vendor_tools", []), list):
                raise DetectionError(f"{label} {identifier} vendor_tools must be a list")
            if not isinstance(entry.get("warnings", []), list):
                raise DetectionError(f"{label} {identifier} warnings must be a list")

    for provider in providers:
        match = provider.get("match")
        if not isinstance(match, dict) or not match:
            raise DetectionError(f"provider {provider['id']} match must be a non-empty object")
        unknown = set(match) - MATCH_FIELDS
        if unknown:
            raise DetectionError(
                f"provider {provider['id']} has unsupported match fields: {sorted(unknown)}"
            )
        for field, values in match.items():
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise DetectionError(f"provider {provider['id']} match.{field} must be strings")

    if vendor_document.get("schema_version") != 1:
        raise DetectionError("vendor tool catalog schema_version must be 1")
    raw_tools = vendor_document.get("tools", [])
    if not isinstance(raw_tools, list) or any(not isinstance(item, dict) for item in raw_tools):
        raise DetectionError("vendor tool catalog tools must be a list of objects")
    tools: dict[str, dict[str, Any]] = {}
    for tool in raw_tools:
        identifier = tool.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise DetectionError("every vendor tool needs a non-empty id")
        if identifier in tools:
            raise DetectionError(f"duplicate vendor tool id: {identifier}")
        tools[identifier] = tool

    referenced = {
        tool_id for entry in [*providers, *platforms] for tool_id in entry.get("vendor_tools", [])
    }
    missing = referenced - set(tools)
    if missing:
        raise DetectionError(
            f"provider registry references unknown vendor tools: {sorted(missing)}"
        )
    return providers, platforms, tools


def _provider_matches(provider: Mapping[str, Any], controller: Mapping[str, Any]) -> bool:
    match = provider["match"]
    field_values = {
        "bus_types": controller.get("bus_type"),
        "devices": controller.get("device_id"),
        "drivers": controller.get("kernel_driver"),
        "subsystem_devices": controller.get("subsystem_device_id"),
        "subsystem_vendors": controller.get("subsystem_vendor_id"),
        "vendors": controller.get("vendor_id"),
    }
    for field, expected in match.items():
        if field == "class_prefixes":
            actual_class = controller.get("class_code")
            if not isinstance(actual_class, str) or not any(
                actual_class.startswith(prefix) for prefix in expected
            ):
                return False
            continue
        if field_values.get(field) not in expected:
            return False
    return True


def _match_rank(provider: Mapping[str, Any]) -> tuple[int, int]:
    fields = set(provider["match"])
    if {"subsystem_vendors", "drivers"} <= fields:
        base = 500
    elif "subsystem_vendors" in fields:
        base = 450
    elif {"vendors", "drivers"} <= fields:
        base = 400
    elif {"vendors", "devices"} <= fields:
        base = 380
    elif "drivers" in fields:
        base = 300
    elif "vendors" in fields or "devices" in fields:
        base = 200
    elif "class_prefixes" in fields:
        base = 100
    else:
        base = 50
    return base, len(fields)


def _match_tier(provider: Mapping[str, Any]) -> str:
    fields = set(provider["match"])
    if {"subsystem_vendors", "drivers"} <= fields:
        return "oem-driver"
    if "subsystem_vendors" in fields:
        return "oem"
    if {"vendors", "drivers"} <= fields:
        return "vendor-driver"
    if "drivers" in fields:
        return "driver"
    if "vendors" in fields or "devices" in fields:
        return "hardware-id"
    if "class_prefixes" in fields:
        return "class"
    return "bus"


def _resolve_provider(
    controller: Mapping[str, Any], providers: Sequence[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str | None]:
    candidates = [provider for provider in providers if _provider_matches(provider, controller)]
    if not candidates:
        return None, None
    # Python's max retains the first registry entry on equal keys.  Registry
    # order is therefore the final, explicit tie breaker.
    selected = max(candidates, key=_match_rank)
    return selected, _match_tier(selected)


def _platform_matches(
    recommendation: Mapping[str, Any], dmi: Mapping[str, str], bmc_detected: bool
) -> bool:
    match = recommendation.get("match", {})
    if not isinstance(match, dict):
        return False
    conditions: list[bool] = []
    for key, values in match.items():
        if key == "requires_bmc":
            conditions.append(bool(values) == bmc_detected)
            continue
        if not key.endswith("_contains") or not isinstance(values, list):
            continue
        field = key[: -len("_contains")]
        haystack = dmi.get(field, "").lower()
        conditions.append(any(str(needle).lower() in haystack for needle in values))
    # DMI recommendations intentionally allow either a vendor string or a
    # product-family string to match.  Firmware frequently leaves one blank.
    return bool(conditions) and any(conditions)


def _provider_summary(provider: Mapping[str, Any], tier: str) -> dict[str, Any]:
    return {
        "capabilities": sorted(set(provider.get("capabilities", []))),
        "generation": provider.get("generation"),
        "id": provider["id"],
        "kind": provider["kind"],
        "match_tier": tier,
        "name": provider["name"],
        "support_level": provider["support_level"],
    }


def detect(
    controllers: Sequence[dict[str, Any]],
    transport_hosts: Sequence[dict[str, Any]],
    disks: Sequence[dict[str, Any]],
    dmi: dict[str, str],
    bmc_interfaces: Sequence[str],
    providers: Sequence[dict[str, Any]],
    platform_recommendations: Sequence[dict[str, Any]],
    tools: Mapping[str, dict[str, Any]],
    source: dict[str, str],
) -> dict[str, Any]:
    normalized_controllers: list[dict[str, Any]] = []
    normalized_transport_hosts: list[dict[str, Any]] = []
    normalized_disks = sorted(
        (dict(item) for item in disks),
        key=lambda item: (str(item.get("id") or ""), str(item.get("kernel_name") or "")),
    )
    provider_usage: dict[str, dict[str, set[str]]] = {}
    packages: set[str] = set()
    warnings: set[str] = set()
    recommended_tools: dict[str, set[str]] = {}

    def resolve_records(
        records: Sequence[dict[str, Any]],
        output_records: list[dict[str, Any]],
        *,
        label: str,
        usage_field: str,
    ) -> None:
        for record in sorted(records, key=_controller_sort_key):
            output = dict(record)
            provider, tier = _resolve_provider(record, providers)
            if provider is None or tier is None:
                output["provider"] = None
                warnings.add(
                    f"No provider matched {label} {record['address']} "
                    f"({record.get('kernel_driver') or 'no kernel driver'})."
                )
            else:
                output["provider"] = _provider_summary(provider, tier)
                usage = provider_usage.setdefault(
                    provider["id"],
                    {
                        "controller_addresses": set(),
                        "tiers": set(),
                        "transport_host_addresses": set(),
                    },
                )
                usage[usage_field].add(str(record["address"]))
                usage["tiers"].add(tier)
                packages.update(provider.get("packages", []))
                warnings.update(provider.get("warnings", []))
                for tool_id in provider.get("vendor_tools", []):
                    recommended_tools.setdefault(tool_id, set()).add(provider["id"])
            output_records.append(output)

    resolve_records(
        controllers,
        normalized_controllers,
        label="controller",
        usage_field="controller_addresses",
    )
    resolve_records(
        transport_hosts,
        normalized_transport_hosts,
        label="transport host",
        usage_field="transport_host_addresses",
    )

    matched_platforms = [
        recommendation
        for recommendation in platform_recommendations
        if _platform_matches(recommendation, dmi, bool(bmc_interfaces))
    ]
    platform_output: list[dict[str, Any]] = []
    for recommendation in matched_platforms:
        identifier = recommendation["id"]
        packages.update(recommendation.get("packages", []))
        warnings.update(recommendation.get("warnings", []))
        for tool_id in recommendation.get("vendor_tools", []):
            recommended_tools.setdefault(tool_id, set()).add(identifier)
        platform_output.append(
            {
                "capabilities": sorted(set(recommendation.get("capabilities", []))),
                "id": identifier,
                "name": recommendation["name"],
            }
        )

    if not normalized_controllers and not normalized_transport_hosts:
        warnings.add("No storage controller or storage transport host was found in sysfs.")

    for disk in normalized_disks:
        if not disk.get("stable_identity"):
            warnings.add(
                f"Disk {disk.get('kernel_path') or disk.get('kernel_name')} has no stable serial, "
                "WWN, EUI-64, or NGUID; its kernel locator must not authorize writes."
            )
    identities: dict[str, list[str]] = {}
    for disk in normalized_disks:
        identities.setdefault(str(disk.get("id")), []).append(str(disk.get("kernel_path")))
    for disk_id, paths in identities.items():
        if disk_id and len(paths) > 1:
            warnings.add(
                f"Stable disk identity {disk_id} is visible through multiple paths: "
                f"{', '.join(sorted(paths))}. Treat it as one device until multipath is resolved."
            )

    providers_output: list[dict[str, Any]] = []
    for provider in providers:
        usage = provider_usage.get(provider["id"])
        if usage is None:
            continue
        item = _provider_summary(provider, min(usage["tiers"]))
        item["controller_addresses"] = sorted(usage["controller_addresses"])
        item["match_tiers"] = sorted(usage["tiers"])
        item["transport_host_addresses"] = sorted(usage["transport_host_addresses"])
        providers_output.append(item)

    tools_output: list[dict[str, Any]] = []
    for identifier in sorted(recommended_tools):
        item = dict(tools[identifier])
        item["recommended_by"] = sorted(recommended_tools[identifier])
        tools_output.append(item)

    return {
        "controllers": normalized_controllers,
        "disks": normalized_disks,
        "platform": {
            "bmc_detected": bool(bmc_interfaces),
            "bmc_interfaces": sorted(set(bmc_interfaces)),
            "dmi": dict(sorted(dmi.items())),
            "recommendations": platform_output,
        },
        "providers": providers_output,
        "recommendations": {
            "packages": sorted(packages),
            "vendor_tools": sorted(recommended_tools),
        },
        "schema_version": 1,
        "source": source,
        "transport_hosts": normalized_transport_hosts,
        "vendor_tools": tools_output,
        "warnings": sorted(warnings),
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover storage controllers through read-only sysfs metadata."
    )
    parser.add_argument("--format", choices=("json",), default="json")
    parser.add_argument("--fixture", type=pathlib.Path, help="read a recorded JSON fixture")
    parser.add_argument(
        "--sysfs-root",
        type=pathlib.Path,
        default=pathlib.Path("/sys"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--udev-data-root",
        type=pathlib.Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        providers_document = _load_json(PROVIDERS_FILE, "provider registry")
        vendor_document = _load_json(VENDOR_TOOLS_FILE, "vendor tool catalog")
        providers, platforms, tools = _validate_registry(providers_document, vendor_document)
        if args.fixture is not None:
            controllers, transport_hosts, disks, dmi, bmc = _load_fixture(args.fixture)
            source = {"kind": "fixture", "name": args.fixture.name}
        else:
            if args.udev_data_root is not None:
                udev_data_root = args.udev_data_root
            elif args.sysfs_root == pathlib.Path("/sys"):
                udev_data_root = pathlib.Path("/run/udev/data")
            else:
                udev_data_root = args.sysfs_root / "run" / "udev" / "data"
            controllers, transport_hosts, disks, dmi, bmc = _discover_sysfs(
                args.sysfs_root, udev_data_root, providers
            )
            source = {"kind": "sysfs"}
        result = detect(
            controllers,
            transport_hosts,
            disks,
            dmi,
            bmc,
            providers,
            platforms,
            tools,
            source,
        )
    except DetectionError as exc:
        print(f"detect-hardware: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
