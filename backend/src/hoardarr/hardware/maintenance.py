from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

Probe = Callable[[list[str]], str | None]


def _smart_self_test_capability(output: str | None) -> dict[str, Any]:
    capability: dict[str, Any] = {
        "status": "not_reported",
        "short_minutes": None,
        "extended_minutes": None,
        "source": "Not reported",
    }
    if output is None:
        return capability
    try:
        document = json.loads(output)
    except json.JSONDecodeError:
        return capability
    if not isinstance(document, Mapping):
        return capability
    smart_support = document.get("smart_support")
    if isinstance(smart_support, Mapping) and smart_support.get("available") is False:
        capability.update({"status": "unsupported", "source": "smartctl -j -c"})
        return capability
    ata_data = document.get("ata_smart_data")
    self_test = ata_data.get("self_test") if isinstance(ata_data, Mapping) else None
    polling = self_test.get("polling_minutes") if isinstance(self_test, Mapping) else None
    if isinstance(polling, Mapping):
        short = polling.get("short")
        extended = polling.get("extended")
        capability.update(
            {
                "status": "available"
                if any(isinstance(value, int) and value > 0 for value in (short, extended))
                else "not_reported",
                "short_minutes": short if isinstance(short, int) and short > 0 else None,
                "extended_minutes": (
                    extended if isinstance(extended, int) and extended > 0 else None
                ),
                "source": "smartctl -j -c",
            }
        )
    return capability


def bounded_probe(command: list[str]) -> str | None:
    executable = shutil.which(command[0], path="/usr/sbin:/usr/bin:/sbin:/bin")
    if executable is None:
        return None
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
        return None
    if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def detect_capability(disk: Mapping[str, Any], *, probe: Probe = bounded_probe) -> dict[str, Any]:
    current = disk.get("maintenance_capabilities")
    result = deepcopy(current) if isinstance(current, Mapping) else {}
    result.update(
        {
            "ata_secure_erase": result.get("ata_secure_erase") is True,
            "nvme_block_erase": result.get("nvme_block_erase") is True,
            "sector_format_passthrough": result.get("sector_format_passthrough") is True,
            "supported_logical_sector_bytes": list(
                result.get("supported_logical_sector_bytes", [])
            ),
            "source": str(result.get("source") or "Not reported"),
            "smart_self_test": _smart_self_test_capability(None),
        }
    )
    path = disk.get("kernel_path")
    connection = disk.get("connection") if isinstance(disk.get("connection"), Mapping) else {}
    protocol = str(connection.get("protocol") or "").casefold()
    transport = str(connection.get("transport") or "").casefold()
    if not isinstance(path, str) or not path.startswith("/dev/") or "\x00" in path:
        return result
    if protocol == "nvme" or "nvme" in transport:
        output = probe(["nvme", "id-ctrl", path, "--output-format=json"])
        if output is None:
            result["smart_self_test"] = _smart_self_test_capability(
                probe(["smartctl", "-j", "-c", path])
            )
            return result
        try:
            document = json.loads(output)
            raw = document.get("sanicap")
            sanicap = int(raw, 0) if isinstance(raw, str) else int(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            result["smart_self_test"] = _smart_self_test_capability(
                probe(["smartctl", "-j", "-c", path])
            )
            return result
        result["nvme_block_erase"] = bool(sanicap & 0x2)
        result["source"] = "nvme id-ctrl"
    elif protocol in {"ata", "sata"} and transport not in {"usb", "uas"}:
        output = probe(["hdparm", "-I", path])
        if output is None:
            result["smart_self_test"] = _smart_self_test_capability(
                probe(["smartctl", "-j", "-c", path])
            )
            return result
        security = output.casefold().partition("security:")[2][:2048]
        result["ata_secure_erase"] = bool(
            security and "supported" in security and "not\tsupported" not in security
        )
        result["source"] = "hdparm -I"
    result["smart_self_test"] = _smart_self_test_capability(
        probe(["smartctl", "-j", "-c", path])
    )
    return result


def enrich_maintenance_capabilities(
    payload: dict[str, Any], *, probe: Probe = bounded_probe
) -> dict[str, Any]:
    result = deepcopy(payload)
    disks = result.get("disks")
    if not isinstance(disks, list):
        return result
    effective_probe = (
        (lambda _command: None) if os.name == "nt" and probe is bounded_probe else probe
    )
    for disk in disks:
        if isinstance(disk, dict):
            disk["maintenance_capabilities"] = detect_capability(disk, probe=effective_probe)
    return result
