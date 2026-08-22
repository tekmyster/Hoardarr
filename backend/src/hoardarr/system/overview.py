from __future__ import annotations

import shutil
import socket
import subprocess
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

NEIGHBOR_OUTPUT_LIMIT_BYTES = 1024 * 1024


def _neighbor_text(value: str, maximum: int = 512) -> str | None:
    cleaned = " ".join(value.replace("\x00", "").split()).strip()
    return cleaned[:maximum] if cleaned else None


def parse_lldpcli_neighbors(output: str) -> list[dict[str, Any]]:
    """Parse lldpcli keyvalue output without trusting neighbor-advertised text."""

    neighbors: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    prefix = ""

    def finish() -> None:
        nonlocal current
        if current is not None:
            neighbors.append(current)
        current = None

    for raw_line in output.splitlines():
        if "=" not in raw_line:
            continue
        key, raw_value = raw_line.split("=", 1)
        if key.startswith("lldp.") and key.endswith(".via"):
            finish()
            local_interface = _neighbor_text(key[len("lldp.") : -len(".via")], 128)
            via = _neighbor_text(raw_value, 64)
            if local_interface is None or via is None:
                prefix = ""
                continue
            normalized = via.casefold()
            protocol = (
                "CDP"
                if normalized.startswith("cdp")
                else "LLDP"
                if normalized.startswith("lldp")
                else via
            )
            current = {
                "local_interface": local_interface,
                "protocol": protocol,
                "protocol_variant": via,
                "device_name": None,
                "chassis_id": None,
                "port_id": None,
                "port_description": None,
                "management_addresses": [],
                "system_description": None,
                "age": None,
                "ttl_seconds": None,
            }
            prefix = f"lldp.{local_interface}."
            continue
        if current is None or not prefix or not key.startswith(prefix):
            continue
        field = key[len(prefix) :]
        value = _neighbor_text(raw_value, 2048 if field == "chassis.descr" else 512)
        if value is None:
            continue
        if field == "chassis.name":
            current["device_name"] = value
        elif field == "chassis.descr":
            current["system_description"] = value
        elif field == "chassis.mgmt-ip":
            addresses = current["management_addresses"]
            if isinstance(addresses, list) and value not in addresses and len(addresses) < 8:
                addresses.append(value)
        elif field.startswith("chassis.") and field.removeprefix("chassis.") in {
            "mac",
            "local",
            "ifname",
            "chassis-id",
        }:
            current["chassis_id"] = current["chassis_id"] or value
        elif field == "port.descr":
            current["port_description"] = value
        elif field.startswith("port.") and field.removeprefix("port.") in {
            "ifname",
            "local",
            "mac",
            "id",
        }:
            current["port_id"] = current["port_id"] or value
        elif field == "age":
            current["age"] = value
        elif field == "ttl.ttl":
            with suppress(ValueError):
                current["ttl_seconds"] = max(0, int(value))
    finish()
    return neighbors


def collect_neighbor_discovery() -> dict[str, Any]:
    captured_at = datetime.now(UTC)
    command = shutil.which("lldpcli", path="/usr/sbin:/usr/bin:/sbin:/bin")
    if command is None:
        return {
            "status": "tool_unavailable",
            "source": None,
            "captured_at": captured_at,
            "neighbors": [],
            "detail": "LLDP/CDP discovery tools are not installed.",
        }
    try:
        result = subprocess.run(
            [command, "-f", "keyvalue", "show", "neighbors", "details"],
            check=False,
            shell=False,
            capture_output=True,
            timeout=5,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "status": "unavailable",
            "source": "lldpcli",
            "captured_at": captured_at,
            "neighbors": [],
            "detail": "The LLDP/CDP daemon could not be queried.",
        }
    if result.returncode != 0 or len(result.stdout) > NEIGHBOR_OUTPUT_LIMIT_BYTES:
        return {
            "status": "unavailable",
            "source": "lldpcli",
            "captured_at": captured_at,
            "neighbors": [],
            "detail": "The LLDP/CDP daemon did not return a usable reading.",
        }
    try:
        output = result.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return {
            "status": "unavailable",
            "source": "lldpcli",
            "captured_at": captured_at,
            "neighbors": [],
            "detail": "The LLDP/CDP daemon returned an invalid reading.",
        }
    neighbors = parse_lldpcli_neighbors(output)
    return {
        "status": "available" if neighbors else "no_neighbors",
        "source": "lldpcli",
        "captured_at": captured_at,
        "neighbors": neighbors,
        "detail": None
        if neighbors
        else "Discovery is running, but no switch or device has been observed.",
    }


def _number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _disk_documents(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    storage = payload.get("storage")
    candidates = (
        payload.get("disks"),
        payload.get("drives"),
        payload.get("block_devices"),
        storage.get("drives") if isinstance(storage, dict) else None,
    )
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return None


def _drive_health(drive: dict[str, Any]) -> str:
    health = drive.get("health")
    health_document = health if isinstance(health, dict) else {}
    raw = (
        drive.get("health_status")
        or health_document.get("status")
        or health_document.get("overall")
    )
    if isinstance(raw, str):
        normalized = raw.strip().lower().replace("_", " ")
        if normalized in {"healthy", "good", "ok", "passed", "pass"}:
            return "healthy"
        if normalized in {"warning", "warn", "degraded", "prefail", "pre-fail"}:
            return "warning"
        if normalized in {"critical", "failed", "fail", "bad"}:
            return "critical"
    passed = health_document.get("passed")
    if passed is True:
        return "healthy"
    if passed is False:
        return "critical"
    return "unknown"


def summarize_storage(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {
            "drive_count": None,
            "raw_capacity_bytes": None,
            "health": None,
        }
    drives = _disk_documents(payload)
    if drives is None:
        return {
            "drive_count": None,
            "raw_capacity_bytes": None,
            "health": None,
        }
    drives = [drive for drive in drives if drive.get("system_disk") is not True]
    capacity = 0
    complete_capacity = True
    health = {"healthy": 0, "warning": 0, "critical": 0, "unknown": 0}
    for drive in drives:
        value = _number(
            drive.get("capacity_bytes") or drive.get("capacityBytes") or drive.get("size_bytes")
        )
        if value is None:
            complete_capacity = False
        else:
            capacity += int(value)
        health[_drive_health(drive)] += 1
    return {
        "drive_count": len(drives),
        "raw_capacity_bytes": capacity if complete_capacity else None,
        "health": health,
    }


def _boot_volume_document() -> dict[str, Any] | None:
    try:
        root = Path.cwd().anchor or "/"
        boot_volume = psutil.disk_usage(root)
        return {
            "mountpoint": root,
            "total_bytes": boot_volume.total,
            "used_bytes": boot_volume.used,
            "free_bytes": boot_volume.free,
            "used_percent": boot_volume.percent,
        }
    except (OSError, RuntimeError):
        return None


def collect_resource_metrics() -> dict[str, Any]:
    """Collect the small, fast-changing resource document used by live polling."""

    captured_at = datetime.now(UTC)
    memory = psutil.virtual_memory()
    interface_stats = psutil.net_if_stats()
    interface_io = psutil.net_io_counters(pernic=True)
    network_interfaces: list[dict[str, Any]] = []
    for name in sorted(set(interface_stats) | set(interface_io)):
        stats = interface_stats.get(name)
        counters = interface_io.get(name)
        network_interfaces.append(
            {
                "name": name,
                "up": stats.isup if stats is not None else None,
                "bytes_received": counters.bytes_recv if counters is not None else None,
                "bytes_sent": counters.bytes_sent if counters is not None else None,
            }
        )
    return {
        "captured_at": captured_at,
        "cpu": {
            "used_percent": psutil.cpu_percent(interval=0.1),
            "logical_processors": psutil.cpu_count(logical=True),
            "physical_cores": psutil.cpu_count(logical=False),
        },
        "memory": {
            "total_bytes": memory.total,
            "available_bytes": memory.available,
            "used_bytes": memory.used,
            "used_percent": memory.percent,
        },
        "boot_volume": _boot_volume_document(),
        "network_interfaces": network_interfaces,
    }


def collect_host_metrics() -> dict[str, Any]:
    resources = collect_resource_metrics()

    temperatures: list[dict[str, Any]] = []
    try:
        for source, readings in psutil.sensors_temperatures().items():
            for reading in readings:
                temperatures.append(
                    {
                        "source": source,
                        "label": reading.label or source,
                        "current_c": reading.current,
                        "high_c": reading.high,
                        "critical_c": reading.critical,
                    }
                )
    except (AttributeError, OSError, RuntimeError):
        pass

    interface_stats = psutil.net_if_stats()
    interface_io = psutil.net_io_counters(pernic=True)
    interfaces = []
    for name in sorted(set(interface_stats) | set(interface_io)):
        stats = interface_stats.get(name)
        counters = interface_io.get(name)
        interfaces.append(
            {
                "name": name,
                "up": stats.isup if stats is not None else None,
                "speed_mbps": stats.speed if stats is not None and stats.speed > 0 else None,
                "mtu": stats.mtu if stats is not None else None,
                "bytes_received": counters.bytes_recv if counters is not None else None,
                "bytes_sent": counters.bytes_sent if counters is not None else None,
                "errors_received": counters.errin if counters is not None else None,
                "errors_sent": counters.errout if counters is not None else None,
                "drops_received": counters.dropin if counters is not None else None,
                "drops_sent": counters.dropout if counters is not None else None,
            }
        )

    captured_at = resources["captured_at"]
    booted_at = datetime.fromtimestamp(psutil.boot_time(), UTC)
    return {
        **resources,
        "hostname": socket.gethostname(),
        "booted_at": booted_at,
        "uptime_seconds": max(0, int((captured_at - booted_at).total_seconds())),
        "temperatures": temperatures,
        "network_interfaces": interfaces,
    }
