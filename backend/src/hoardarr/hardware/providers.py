from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

NOT_REPORTED = "Not reported"
PROVIDER_API_VERSION = 1


class ProviderError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ProviderDefinition:
    name: str
    family: str
    commands: tuple[str, ...]
    capability: str
    api_version: int = PROVIDER_API_VERSION
    execution_model: str = "in_process"
    trust: str = "built_in"


PROVIDERS = (
    ProviderDefinition(
        "storcli",
        "lsi_avago_broadcom_dell",
        ("storcli2", "storcli", "perccli"),
        "controller.health",
    ),
    ProviderDefinition("ssacli", "hpe_smart_array", ("ssacli", "hpssacli"), "controller.health"),
    ProviderDefinition("arcconf", "adaptec_microchip", ("arcconf",), "controller.health"),
    ProviderDefinition("areca", "areca", ("cli64",), "controller.health"),
    ProviderDefinition("mdadm", "linux_md", ("mdadm",), "pool.health"),
    ProviderDefinition("zpool", "zfs", ("zpool",), "pool.health"),
    ProviderDefinition("snapraid", "snapraid", ("snapraid",), "pool.health"),
    ProviderDefinition("ses", "generic_ses", ("sg_ses",), "enclosure.health"),
    ProviderDefinition("smp", "generic_sas_expander", ("smp_discover",), "enclosure.topology"),
)


def detect_providers(available_commands: Sequence[str]) -> list[dict[str, Any]]:
    available = set(available_commands)
    result = []
    for provider in PROVIDERS:
        command = next((item for item in provider.commands if item in available), None)
        result.append(
            {
                "name": provider.name,
                "family": provider.family,
                "capability": provider.capability,
                "api_version": provider.api_version,
                "execution_model": provider.execution_model,
                "trust": provider.trust,
                "available": command is not None,
                "command": command or NOT_REPORTED,
            }
        )
    return result


def _json_document(output: str, provider: str) -> Any:
    if len(output.encode(errors="replace")) > 16 * 1024 * 1024:
        raise ProviderError("output_too_large", f"{provider} output exceeded its limit")
    try:
        return json.loads(
            output,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProviderError("output_invalid", f"{provider} returned invalid JSON") from exc


def _status(value: Any) -> str:
    if not isinstance(value, str):
        return NOT_REPORTED
    folded = value.strip().casefold()
    if any(item in folded for item in ("optimal", "online", "ok", "healthy", "normal")):
        return "healthy"
    if any(item in folded for item in ("degraded", "rebuild", "resilver", "scrub", "warning")):
        return "needs_attention"
    if any(item in folded for item in ("failed", "offline", "critical", "fault")):
        return "failed"
    return NOT_REPORTED


def parse_storcli(output: str) -> dict[str, Any]:
    document = _json_document(output, "storcli")
    controllers = document.get("Controllers") if isinstance(document, Mapping) else None
    if not isinstance(controllers, list):
        raise ProviderError("output_invalid", "storcli controller list is missing")
    result = []
    for item in controllers:
        if not isinstance(item, Mapping):
            continue
        response = item.get("Response Data")
        status_doc = item.get("Command Status")
        if not isinstance(response, Mapping):
            response = {}
        if not isinstance(status_doc, Mapping):
            status_doc = {}
        basics = response.get("Basics") if isinstance(response.get("Basics"), Mapping) else {}
        drives = response.get("PD LIST") if isinstance(response.get("PD LIST"), list) else []
        normalized_drives = []
        for drive in drives:
            if not isinstance(drive, Mapping):
                continue
            enclosure_slot = drive.get("EID:Slt")
            enclosure = slot = NOT_REPORTED
            if isinstance(enclosure_slot, str) and ":" in enclosure_slot:
                enclosure, slot = enclosure_slot.split(":", 1)
            normalized_drives.append(
                {
                    "enclosure": enclosure,
                    "slot": slot,
                    "state": _status(drive.get("State")),
                    "serial": drive.get("SN") if isinstance(drive.get("SN"), str) else NOT_REPORTED,
                    "model": drive.get("Model")
                    if isinstance(drive.get("Model"), str)
                    else NOT_REPORTED,
                    "temperature_c": drive.get("Temp")
                    if isinstance(drive.get("Temp"), int)
                    else NOT_REPORTED,
                }
            )
        result.append(
            {
                "id": basics.get("Controller")
                if isinstance(basics.get("Controller"), int)
                else len(result),
                "model": basics.get("Model")
                if isinstance(basics.get("Model"), str)
                else NOT_REPORTED,
                "serial": basics.get("Serial Number")
                if isinstance(basics.get("Serial Number"), str)
                else NOT_REPORTED,
                "health": _status(status_doc.get("Status")),
                "drives": normalized_drives,
            }
        )
    return {"provider": "storcli", "controllers": result}


def _bounded_text(output: str, provider: str, *, limit: int = 4 * 1024 * 1024) -> str:
    if len(output.encode(errors="replace")) > limit:
        raise ProviderError("output_too_large", f"{provider} output exceeded its limit")
    if "\x00" in output:
        raise ProviderError("output_invalid", f"{provider} returned invalid text")
    return output


def _ses_scalar(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    for key in ("meaning", "name", "value", "i", "hex"):
        if key in value and not isinstance(value[key], (Mapping, list)):
            return value[key]
    return None


def _ses_walk(value: Any) -> list[Mapping[str, Any]]:
    """Return a bounded view of mappings in untrusted sg_ses JSON."""

    result: list[Mapping[str, Any]] = []
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack and len(result) < 16_384:
        current, depth = stack.pop()
        if depth > 16:
            continue
        if isinstance(current, Mapping):
            result.append(current)
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current[:4096])
    return result


def _ses_nested(item: Mapping[str, Any], *keys: str) -> Any:
    wanted = {key.casefold() for key in keys}
    for candidate in _ses_walk(item):
        for key, value in candidate.items():
            if str(key).casefold() in wanted:
                scalar = _ses_scalar(value)
                if scalar is not None:
                    return scalar
    return None


def _ses_elements(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Accept Hoardarr-normalized fixtures and joined sg_ses JSON structures."""

    direct = document.get("elements")
    if isinstance(direct, list):
        return [dict(item) for item in direct[:4096] if isinstance(item, Mapping)]
    result: list[dict[str, Any]] = []
    for group in _ses_walk(document):
        element_type = _ses_scalar(group.get("element_type"))
        if not isinstance(element_type, str):
            continue
        individual = group.get("individual_status_element_list")
        records = individual if isinstance(individual, list) else [group]
        for record in records[:4096]:
            if not isinstance(record, Mapping):
                continue
            result.append(
                {
                    "element_type": element_type,
                    "status": _ses_nested(record, "status", "status_code"),
                    "slot": _ses_nested(
                        record, "slot", "device_slot_number", "element_index"
                    ),
                    "identify": _ses_nested(record, "identify", "ident"),
                    "fault": _ses_nested(record, "fault", "fault_requested"),
                    "temperature_c": _ses_nested(
                        record, "temperature_c", "temperature", "temperature_in_celsius"
                    ),
                    "speed_rpm": _ses_nested(record, "speed_rpm", "actual_speed"),
                    "voltage_v": _ses_nested(record, "voltage_v", "voltage"),
                    "sas_address": _ses_nested(record, "sas_address"),
                    "attached_sas_address": _ses_nested(record, "attached_sas_address"),
                }
            )
    # Joined pages can repeat a type group. Keep bounded unique records without
    # treating absence as a zero-valued sensor.
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in result:
        key = json.dumps(item, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def parse_ssacli(output: str) -> dict[str, Any]:
    text = _bounded_text(output, "ssacli")
    controllers: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.lower().startswith(("smart array ", "dynamic smart array ")):
            current = {
                "id": len(controllers),
                "model": line.split(" in Slot ", 1)[0],
                "slot": line.split(" in Slot ", 1)[1] if " in Slot " in line else NOT_REPORTED,
                "health": NOT_REPORTED,
                "drives": [],
            }
            controllers.append(current)
        elif current is not None and line.casefold().startswith("controller status:"):
            current["health"] = _status(line.split(":", 1)[1])
    if not controllers:
        raise ProviderError("output_invalid", "ssacli controller output is incomplete")
    return {"provider": "ssacli", "controllers": controllers}


def parse_arcconf(output: str) -> dict[str, Any]:
    text = _bounded_text(output, "arcconf")
    model = re.search(r"^\s*Controller Model\s*:\s*(.+)$", text, re.M | re.I)
    status = re.search(r"^\s*Controller Status\s*:\s*(.+)$", text, re.M | re.I)
    serial = re.search(r"^\s*Controller Serial Number\s*:\s*(.+)$", text, re.M | re.I)
    if not model or not status:
        raise ProviderError("output_invalid", "arcconf controller output is incomplete")
    return {
        "provider": "arcconf",
        "controllers": [
            {
                "id": 1,
                "model": model.group(1).strip(),
                "serial": serial.group(1).strip() if serial else NOT_REPORTED,
                "health": _status(status.group(1)),
                "drives": [],
            }
        ],
    }


def parse_areca(output: str) -> dict[str, Any]:
    text = _bounded_text(output, "areca")
    model = re.search(r"^\s*(?:Controller Name|Model)\s*:\s*(.+)$", text, re.M | re.I)
    status = re.search(r"^\s*(?:Controller Status|System Health)\s*:\s*(.+)$", text, re.M | re.I)
    if not model or not status:
        raise ProviderError("output_invalid", "Areca controller output is incomplete")
    return {
        "provider": "areca",
        "controllers": [
            {
                "id": 1,
                "model": model.group(1).strip(),
                "serial": NOT_REPORTED,
                "health": _status(status.group(1)),
                "drives": [],
            }
        ],
    }


def parse_ses(output: str) -> dict[str, Any]:
    document = _json_document(output, "sg_ses")
    if not isinstance(document, Mapping):
        raise ProviderError("output_invalid", "sg_ses enclosure output is incomplete")
    elements = _ses_elements(document)
    if not elements and not isinstance(document.get("elements"), list):
        raise ProviderError("output_invalid", "sg_ses enclosure elements are missing")
    slots: list[dict[str, Any]] = []
    temperatures: list[float] = []
    fans: list[int] = []
    power_supplies: list[str] = []
    voltages: list[float] = []
    expanders: list[str] = []
    locate = False
    fault = False
    for item in elements:
        if not isinstance(item, Mapping):
            continue
        element_type = str(item.get("element_type") or "").casefold()
        status = _status(item.get("status"))
        if element_type in {"array device slot", "device slot"}:
            slot = item.get("slot")
            identify = item.get("identify")
            slot_fault = item.get("fault")
            sas_address_value = str(item.get("sas_address") or "")
            sas_address = (
                sas_address_value.removeprefix("0x").casefold()
                if re.fullmatch(r"(?:0x)?[0-9A-Fa-f]{16}", sas_address_value)
                else None
            )
            attached_value = str(item.get("attached_sas_address") or "")
            attached_sas_address = (
                attached_value.removeprefix("0x").casefold()
                if re.fullmatch(r"(?:0x)?[0-9A-Fa-f]{16}", attached_value)
                else None
            )
            locate = locate or identify is True
            fault = fault or slot_fault is True
            slots.append(
                {
                    "slot": slot if isinstance(slot, (int, str)) else NOT_REPORTED,
                    "status": status,
                    "identify": identify if isinstance(identify, bool) else NOT_REPORTED,
                    "fault": slot_fault if isinstance(slot_fault, bool) else NOT_REPORTED,
                    "sas_address": sas_address or NOT_REPORTED,
                    "attached_sas_address": attached_sas_address or NOT_REPORTED,
                    "mapping_source": (
                        "SES Additional Element Status SAS address"
                        if sas_address is not None
                        else NOT_REPORTED
                    ),
                    "mapping_confidence": "high" if sas_address is not None else "unknown",
                }
            )
        elif "temperature" in element_type and isinstance(
            item.get("temperature_c"), (int, float)
        ):
            temperatures.append(float(item["temperature_c"]))
        elif "cooling" in element_type and isinstance(item.get("speed_rpm"), (int, float)):
            fans.append(max(0, int(item["speed_rpm"])))
        elif "power supply" in element_type:
            power_supplies.append(status)
        elif "voltage" in element_type and isinstance(item.get("voltage_v"), (int, float)):
            voltages.append(float(item["voltage_v"]))
        elif "expander" in element_type:
            expanders.append(status)
    descriptor = document.get("enclosure_descriptor") or _ses_nested(
        document, "enclosure_descriptor", "enclosure_name"
    )
    logical_id = (
        document.get("enclosure_logical_identifier")
        or document.get("primary_enclosure_logical_identifier")
        or _ses_nested(
            document,
            "enclosure_logical_identifier",
            "primary_enclosure_logical_identifier",
        )
    )
    if not isinstance(logical_id, str) or re.fullmatch(
        r"(?:0x|naa\.)?[0-9A-Fa-f]{16,64}", logical_id
    ) is None:
        logical_id = None
    return {
        "provider": "sg_ses",
        "enclosures": [
            {
                "id": logical_id.casefold() if logical_id else NOT_REPORTED,
                "descriptor": (
                    descriptor[:256]
                    if isinstance(descriptor, str) and descriptor
                    else NOT_REPORTED
                ),
                "health": _status(document.get("status")),
                "slots": slots,
                "temperature_c": max(temperatures) if temperatures else NOT_REPORTED,
                "fan_rpm": max(fans) if fans else NOT_REPORTED,
                "fan_count": len(fans) if fans else NOT_REPORTED,
                "power_supplies": power_supplies or NOT_REPORTED,
                "voltages": voltages or NOT_REPORTED,
                "locate": locate,
                "fault": fault,
                "expanders": expanders or NOT_REPORTED,
            }
        ],
    }


def parse_smp_discover(output: str) -> dict[str, Any]:
    """Normalize the documented smp_discover summary format without guessing phys."""

    text = _bounded_text(output, "smp_discover", limit=2 * 1024 * 1024)
    expander_match = re.search(
        r"(?:expander|SMP\s+target)[^\r\n]*?\b(?:0x)?([0-9A-Fa-f]{16})\b", text, re.I
    )
    if expander_match is None:
        raise ProviderError("output_invalid", "smp_discover expander identity is missing")
    phys: list[dict[str, Any]] = []
    for raw in text.splitlines()[:4096]:
        phy_match = re.match(r"^\s*phy\s+(\d+)\s*:\s*([DSTU])?\s*(.*)$", raw, re.I)
        if phy_match is None:
            continue
        phy_id = int(phy_match.group(1))
        if phy_id > 254:
            continue
        detail = phy_match.group(3).strip()
        rate_match = re.search(r"\b([0-9]+(?:\.[0-9]+)?)\s*Gbps\b", detail, re.I)
        attached_match = re.search(
            r"attached:\[([0-9A-Fa-f]{16}):([0-9A-Fa-f]{2})\b([^\]]*)\]", detail, re.I
        )
        slot_match = re.search(r"\bdsn=(\d{1,3})\b", detail, re.I)
        state = next(
            (
                value
                for value in ("disabled", "reset problem", "spinup hold")
                if value in detail.casefold()
            ),
            "attached" if attached_match is not None else "not_reported",
        )
        attached_address = attached_match.group(1).casefold() if attached_match else None
        phys.append(
            {
                "phy_id": phy_id,
                "routing": (phy_match.group(2) or "Not reported").upper(),
                "state": state.replace(" ", "_"),
                "negotiated_rate_gbps": float(rate_match.group(1)) if rate_match else None,
                "attached_sas_address": (
                    attached_address
                    if attached_address and attached_address != "0000000000000000"
                    else None
                ),
                "attached_phy_id": int(attached_match.group(2), 16)
                if attached_match
                else None,
                "attached_details": attached_match.group(3).strip()[:256]
                if attached_match
                else None,
                "device_slot_number": (
                    int(slot_match.group(1))
                    if slot_match and int(slot_match.group(1)) < 255
                    else None
                ),
            }
        )
    return {
        "provider": "smp_discover",
        "expander_sas_address": expander_match.group(1).casefold(),
        "phys": sorted(phys, key=lambda item: int(item["phy_id"])),
    }


def parse_snapraid_status(output: str) -> dict[str, Any]:
    text = _bounded_text(output, "snapraid")
    if not re.search(r"snapraid|array|parity|sync", text, re.I):
        raise ProviderError("output_invalid", "SnapRAID status output is incomplete")
    unsynced = re.search(r"(\d+)\s+(?:file|files|block|blocks).*not synced", text, re.I)
    bad = re.search(r"(\d+)\s+(?:bad|damaged)\s+(?:block|blocks)", text, re.I)
    sync = re.search(r"(?:last sync|sync)\s*:\s*(.+)$", text, re.M | re.I)
    unsynced_count = int(unsynced.group(1)) if unsynced else 0
    bad_count = int(bad.group(1)) if bad else 0
    return {
        "provider": "snapraid",
        "state": "failed" if bad_count else "needs_attention" if unsynced_count else "healthy",
        "parity_fresh": unsynced_count == 0,
        "unsynced_items": unsynced_count,
        "bad_blocks": bad_count,
        "last_sync": sync.group(1).strip() if sync else NOT_REPORTED,
    }


def parse_mdadm_detail(output: str) -> dict[str, Any]:
    if len(output) > 2 * 1024 * 1024:
        raise ProviderError("output_too_large", "mdadm output exceeded its limit")
    values: dict[str, str] = {}
    for raw in output.splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        values[key.strip()] = value.strip()
    if "Raid Level" not in values or "State" not in values:
        raise ProviderError("output_invalid", "mdadm detail output is incomplete")
    progress_match = re.search(r"(?:Rebuild|Resync) Status\s*:\s*([0-9.]+)%", output, re.I)
    state = values["State"]
    return {
        "provider": "mdadm",
        "level": values["Raid Level"],
        "state": _status(state),
        "degraded": "degraded" in state.casefold(),
        "rebuild_percent": float(progress_match.group(1)) if progress_match else NOT_REPORTED,
        "active_devices": int(values["Active Devices"])
        if values.get("Active Devices", "").isdigit()
        else NOT_REPORTED,
        "working_devices": int(values["Working Devices"])
        if values.get("Working Devices", "").isdigit()
        else NOT_REPORTED,
    }


def parse_zpool_status(output: str) -> dict[str, Any]:
    if len(output) > 4 * 1024 * 1024:
        raise ProviderError("output_too_large", "zpool output exceeded its limit")
    pool = re.search(r"^\s*pool:\s*(\S+)", output, re.M)
    state = re.search(r"^\s*state:\s*(\S+)", output, re.M)
    scan = re.search(r"^\s*scan:\s*(.+)$", output, re.M)
    if not pool or not state:
        raise ProviderError("output_invalid", "zpool status output is incomplete")
    progress = re.search(r"([0-9.]+)%\s+done", scan.group(1) if scan else "")
    scan_text = scan.group(1) if scan else NOT_REPORTED
    return {
        "provider": "zpool",
        "name": pool.group(1),
        "state": _status(state.group(1)),
        "degraded": state.group(1).casefold() != "online",
        "scan": scan_text,
        "scan_percent": float(progress.group(1)) if progress else NOT_REPORTED,
    }


def aggregate_health(values: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    states = [value.get("state", value.get("health")) for value in values]
    if "failed" in states:
        health = "failed"
    elif "needs_attention" in states:
        health = "needs_attention"
    elif states and all(item == "healthy" for item in states):
        health = "healthy"
    else:
        health = NOT_REPORTED
    return {
        "health": health,
        "providers": len(values),
        "unreported": sum(item == NOT_REPORTED for item in states),
    }
