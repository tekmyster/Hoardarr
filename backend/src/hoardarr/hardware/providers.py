from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

NOT_REPORTED = "Not reported"


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
    elements = document.get("elements")
    if not isinstance(elements, list):
        raise ProviderError("output_invalid", "sg_ses enclosure elements are missing")
    slots: list[dict[str, Any]] = []
    for item in elements:
        if not isinstance(item, Mapping) or item.get("element_type") not in {
            "Array device slot",
            "Device slot",
        }:
            continue
        slot = item.get("slot")
        slots.append(
            {
                "slot": slot if isinstance(slot, (int, str)) else NOT_REPORTED,
                "status": _status(item.get("status")),
                "identify": item.get("identify")
                if isinstance(item.get("identify"), bool)
                else NOT_REPORTED,
                "fault": item.get("fault") if isinstance(item.get("fault"), bool) else NOT_REPORTED,
            }
        )
    descriptor = document.get("enclosure_descriptor")
    return {
        "provider": "sg_ses",
        "enclosures": [
            {
                "id": descriptor if isinstance(descriptor, str) and descriptor else NOT_REPORTED,
                "health": _status(document.get("status")),
                "slots": slots,
            }
        ],
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
