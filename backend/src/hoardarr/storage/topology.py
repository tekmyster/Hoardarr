from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

FC_DRIVERS = {"fnic", "lpfc", "qla2xxx"}
FCOE_DRIVERS = {"bnx2fc", "fcoe", "qedf"}
SAS_DRIVERS = {
    "aacraid",
    "hpsa",
    "megaraid_sas",
    "mpi3mr",
    "mpt2sas",
    "mpt3sas",
    "pm80xx",
    "smartpqi",
}
LIVE_STATE_CACHE_SECONDS = 30.0
_LIVE_STATE_CACHE_LOCK = threading.Lock()
_LIVE_STATE_CACHE: tuple[float, tuple[str, ...], dict[str, dict[str, object]]] | None = None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _speed(value: object) -> float | None:
    direct = _number(value)
    if direct is not None:
        return direct
    text = _text(value)
    if not text:
        return None
    values = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*(?:gbit|gb/s|gbps|gt/s)", text.casefold())
    return max((float(item) for item in values), default=None)


def _protocol(connection: Mapping[str, Any], driver: str | None) -> str:
    values = " ".join(
        filter(
            None,
            (
                _text(connection.get("transport")),
                _text(connection.get("protocol")),
                _text(connection.get("presentation")),
                driver,
            ),
        )
    ).casefold()
    if driver in FCOE_DRIVERS or "fcoe" in values:
        return "FCoE"
    if driver in FC_DRIVERS or re.search(r"\bfc\b|fibre", values):
        return "FC"
    if "sata" in values or "ata" in values:
        return "SATA"
    if driver in SAS_DRIVERS or "sas" in values:
        return "SAS"
    if "nvme" in values:
        return "NVMe"
    if "usb" in values or "uas" in values:
        return "USB"
    return "SCSI"


def _controller_label(controller: Mapping[str, Any]) -> str:
    provider = _mapping(controller.get("provider"))
    return (
        _text(provider.get("name"))
        or _text(controller.get("description"))
        or _text(controller.get("kernel_driver"))
        or _text(controller.get("address"))
        or "Unidentified controller"
    )


def _slot_key(value: object) -> tuple[tuple[int, object], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"([0-9]+)", _text(value) or "")
        if part
    )


def _lsblk_usage() -> dict[str, dict[str, int | None]]:
    executable = shutil.which("lsblk", path="/usr/sbin:/usr/bin:/sbin:/bin")
    if not executable:
        return {}
    try:
        completed = subprocess.run(
            [
                executable,
                "--json",
                "--bytes",
                "--paths",
                "--output",
                "NAME,PATH,TYPE,FSUSED,FSAVAIL",
            ],
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
        )
        document = json.loads(completed.stdout) if completed.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}
    devices = document.get("blockdevices") if isinstance(document, dict) else None
    if not isinstance(devices, list):
        return {}
    result: dict[str, dict[str, int | None]] = {}

    def visit(node: object, root: str | None) -> tuple[int, int]:
        item = _mapping(node)
        path = _text(item.get("path") or item.get("name"))
        if _text(item.get("type")) == "disk" and path:
            root = path
        used = int(_number(item.get("fsused")) or 0)
        available = int(_number(item.get("fsavail")) or 0)
        children = item.get("children")
        if isinstance(children, list):
            for child in children:
                child_used, child_available = visit(child, root)
                used += child_used
                available += child_available
        if root and _text(item.get("type")) == "disk":
            result[root] = {
                "used_bytes": used if used or available else None,
                "usable_bytes": used + available if used or available else None,
            }
        return used, available

    for device in devices:
        visit(device, None)
    return result


def _smart_state(path: str) -> tuple[str, dict[str, object]]:
    executable = shutil.which("smartctl", path="/usr/sbin:/usr/bin:/sbin:/bin")
    if not executable:
        return path, {}
    try:
        completed = subprocess.run(
            [executable, "-aj", "-n", "standby,0", path],
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=2,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
        )
        document = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return path, {}
    if not isinstance(document, dict):
        return path, {}
    temperature = _number(_mapping(document.get("temperature")).get("current"))
    if temperature is None:
        temperature = _number(
            _mapping(document.get("nvme_smart_health_information_log")).get("temperature")
        )
    if temperature is None:
        temperature = _number(_mapping(document.get("scsi_temperature")).get("current"))
    passed = _mapping(document.get("smart_status")).get("passed")
    critical_warning = _number(
        _mapping(document.get("nvme_smart_health_information_log")).get("critical_warning")
    )
    health = "unknown"
    if passed is True:
        health = "healthy"
    elif passed is False or (critical_warning is not None and critical_warning > 0):
        health = "critical"
    if temperature is not None and temperature >= 65:
        health = "critical"
    elif temperature is not None and temperature >= 55 and health != "critical":
        health = "warning"
    return path, {
        "health_status": health,
        "smart_available": passed is not None or temperature is not None,
        "temperature_c": temperature,
    }


def _live_states(paths: list[str]) -> dict[str, dict[str, object]]:
    global _LIVE_STATE_CACHE
    cache_key = tuple(sorted(set(paths)))
    now = time.monotonic()
    with _LIVE_STATE_CACHE_LOCK:
        if (
            _LIVE_STATE_CACHE is not None
            and _LIVE_STATE_CACHE[1] == cache_key
            and now - _LIVE_STATE_CACHE[0] < LIVE_STATE_CACHE_SECONDS
        ):
            return {path: dict(values) for path, values in _LIVE_STATE_CACHE[2].items()}
    states: dict[str, dict[str, object]] = {
        path: dict(values) for path, values in _lsblk_usage().items()
    }
    if not paths:
        return states
    with ThreadPoolExecutor(max_workers=min(24, len(paths))) as pool:
        for path, values in pool.map(_smart_state, paths):
            states.setdefault(path, {}).update(values)
    with _LIVE_STATE_CACHE_LOCK:
        _LIVE_STATE_CACHE = (
            time.monotonic(),
            cache_key,
            {path: dict(values) for path, values in states.items()},
        )
    return states


def _sysfs_enclosures(root: Path = Path("/sys/class/enclosure")) -> list[dict[str, object]]:
    try:
        enclosure_paths = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return []
    result: list[dict[str, object]] = []
    for enclosure_path in enclosure_paths:
        try:
            resolved_parts = enclosure_path.resolve(strict=False).parts
            components = sorted(enclosure_path.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        address = next(
            (
                part.casefold()
                for part in reversed(resolved_parts)
                if re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]", part)
            ),
            None,
        )
        transport_host = next(
            (part for part in reversed(resolved_parts) if re.fullmatch(r"host[0-9]+", part)),
            None,
        )
        bays: list[dict[str, str | None]] = []
        for component in components:
            try:
                slot = (component / "slot").read_text(encoding="utf-8").strip()
            except OSError:
                continue
            try:
                status = (component / "status").read_text(encoding="utf-8").strip()
            except OSError:
                status = None

            def component_flag(name: str, *, component_path: Path = component) -> bool | None:
                try:
                    value = (component_path / name).read_text(encoding="utf-8").strip()
                except OSError:
                    return None
                if value in {"0", "1"}:
                    return value == "1"
                return None

            bays.append(
                {
                    "slot": slot or component.name,
                    "status": status,
                    "locate": component_flag("locate"),
                    "fault": component_flag("fault"),
                }
            )

        def read(relative: str, base: Path = enclosure_path) -> str | None:
            try:
                value = (base / relative).read_text(encoding="utf-8").strip()
            except OSError:
                return None
            return value or None

        result.append(
            {
                "id": enclosure_path.name,
                "vendor": read("device/vendor"),
                "model": read("device/model"),
                "controller_address": transport_host or address,
                "bays": bays,
            }
        )
    return result


def build_storage_topology(
    hardware: object, *, include_live_state: bool = True
) -> dict[str, object]:
    document = _mapping(hardware)
    storage = _mapping(document.get("storage"))
    raw_disks = document.get("disks") or storage.get("drives") or []
    disks = (
        [item for item in raw_disks if isinstance(item, Mapping)]
        if isinstance(raw_disks, list)
        else []
    )
    raw_controllers = [
        *(document.get("controllers") if isinstance(document.get("controllers"), list) else []),
        *(
            document.get("transport_hosts")
            if isinstance(document.get("transport_hosts"), list)
            else []
        ),
    ]
    controller_records = [item for item in raw_controllers if isinstance(item, Mapping)]
    controllers_by_address = {
        str(item["address"]): item for item in controller_records if _text(item.get("address"))
    }
    live = (
        _live_states([path for item in disks if (path := _text(item.get("kernel_path")))])
        if include_live_state
        else {}
    )
    live_enclosures = _sysfs_enclosures() if include_live_state else []
    nodes: dict[str, dict[str, object]] = {}
    links: dict[str, dict[str, object]] = {}
    enclosure_records: dict[str, dict[str, object]] = {}
    direct_attached: list[str] = []

    def controller_node(address: str, protocol: str) -> str:
        node_id = f"controller:{address}"
        if node_id in nodes:
            return node_id
        controller = controllers_by_address.get(address, {})
        attributes = _mapping(controller.get("attributes"))
        nodes[node_id] = {
            "id": node_id,
            "kind": "controller",
            "label": _controller_label(controller) if controller else "Controller not reported",
            "address": address,
            "bus": _text(controller.get("bus_type")),
            "driver": _text(controller.get("kernel_driver")),
            "protocol": protocol,
            "capable_speed_gbps": _speed(attributes.get("supported_speeds")),
            "negotiated_speed_gbps": _speed(attributes.get("speed")),
            "status": _text(attributes.get("port_state")) or "detected",
        }
        return node_id

    for index, disk in enumerate(disks):
        connection = _mapping(disk.get("connection"))
        transport_host = _text(connection.get("transport_host"))
        controller_address = _text(connection.get("controller_address"))
        # A SCSI host is a child transport object of a PCI HBA when both are
        # reported; it must not replace the controller's stable PCI identity.
        # FC/FCoE hosts without a reported PCI ancestor remain valid top-level
        # controllers so their transport state is still visible.
        chosen_address = controller_address or transport_host or "unreported"
        driver = _text(_mapping(controllers_by_address.get(chosen_address)).get("kernel_driver"))
        protocol = _protocol(connection, driver)
        controller_id = controller_node(chosen_address, protocol)
        physical_parent_id = controller_id
        if controller_address and transport_host:
            host_id = f"sas_host:{controller_address}:{transport_host}"
            nodes.setdefault(
                host_id,
                {
                    "id": host_id,
                    "kind": "sas_host",
                    "label": transport_host,
                    "address": transport_host,
                    "protocol": protocol,
                    "status": "detected",
                },
            )
            links.setdefault(
                f"{controller_id}->{host_id}",
                {
                    "id": f"{controller_id}->{host_id}",
                    "source": controller_id,
                    "target": host_id,
                    "protocol": protocol,
                    "capable_speed_gbps": None,
                    "negotiated_speed_gbps": None,
                },
            )
            physical_parent_id = host_id
        for kind, value in (
            ("port", _text(connection.get("hba_port"))),
            ("phy", _text(connection.get("phy_id"))),
            ("expander", _text(connection.get("expander_id"))),
            ("path", _text(connection.get("path_id"))),
        ):
            if not value:
                continue
            physical_node_id = f"{kind}:{chosen_address}:{value}"
            nodes.setdefault(
                physical_node_id,
                {
                    "id": physical_node_id,
                    "kind": kind,
                    "label": value,
                    "address": value,
                    "protocol": protocol,
                    "status": "detected",
                    "path_components": connection.get("path_components")
                    if isinstance(connection.get("path_components"), list)
                    else [],
                },
            )
            if kind == "phy":
                nodes[physical_node_id].update(
                    {
                        "sas_address": _text(connection.get("phy_sas_address")),
                        "phy_identifier": _text(connection.get("phy_identifier")),
                        "minimum_speed_gbps": _number(
                            connection.get("minimum_speed_gbps")
                        ),
                        "capable_speed_gbps": _number(
                            connection.get("capable_speed_gbps")
                        ),
                        "negotiated_speed_gbps": _number(
                            connection.get("negotiated_speed_gbps")
                        ),
                        "invalid_dwords": _number(connection.get("phy_invalid_dwords")),
                        "disparity_errors": _number(
                            connection.get("phy_disparity_errors")
                        ),
                        "loss_of_sync": _number(connection.get("phy_loss_of_sync")),
                        "reset_problems": _number(
                            connection.get("phy_reset_problems")
                        ),
                    }
                )
            elif kind == "expander":
                smp = _mapping(connection.get("smp"))
                smp_phys = smp.get("phys") if isinstance(smp.get("phys"), list) else []
                nodes[physical_node_id].update(
                    {
                        "sas_address": _text(smp.get("expander_sas_address"))
                        or _text(connection.get("expander_sas_address")),
                        "smp_quality": _text(smp.get("quality")) or "not_reported",
                        "smp_source": _text(smp.get("source")),
                        "smp_phy_count": len(smp_phys),
                        "smp_attached_phy_count": sum(
                            1
                            for item in smp_phys
                            if isinstance(item, Mapping)
                            and _text(item.get("attached_sas_address"))
                        ),
                        "smp_phys": smp_phys,
                    }
                )
            elif kind == "path":
                nodes[physical_node_id].update(
                    {
                        "target_port_identifier": _text(
                            connection.get("target_port_identifier")
                        ),
                        "target_port_identifier_type": _text(
                            connection.get("target_port_identifier_type")
                        ),
                    }
                )
            physical_link_id = f"{physical_parent_id}->{physical_node_id}"
            links.setdefault(
                physical_link_id,
                {
                    "id": physical_link_id,
                    "source": physical_parent_id,
                    "target": physical_node_id,
                    "protocol": protocol,
                    "capable_speed_gbps": _number(connection.get("capable_speed_gbps")),
                    "negotiated_speed_gbps": _number(connection.get("negotiated_speed_gbps")),
                },
            )
            physical_parent_id = physical_node_id
        disk_identity = _mapping(disk.get("identity"))
        identity_evidence = _mapping(disk.get("identity_evidence"))
        vpd_page_83 = _mapping(identity_evidence.get("scsi_vpd_page_83"))
        stable_id = _text(disk.get("id")) or f"disk-{index + 1}"
        disk_id = f"drive:{stable_id}"
        path = _text(disk.get("kernel_path"))
        live_state = live.get(path or "", {})
        serial = _text(disk_identity.get("serial")) or "Serial not reported"
        vendor = _text(disk.get("vendor"))
        model = _text(disk.get("model"))
        capacity = int(_number(disk.get("capacity_bytes")) or 0)
        used = live_state.get("used_bytes")
        usable = live_state.get("usable_bytes")
        health = _text(live_state.get("health_status")) or "unknown"
        nodes[disk_id] = {
            "id": disk_id,
            "kind": "drive",
            "stable_identity": stable_id,
            "label": " ".join(filter(None, (vendor, model))) or "Unidentified drive",
            "vendor": vendor,
            "model": model,
            "serial": serial,
            "identity_evidence_quality": _text(vpd_page_83.get("quality")),
            "identity_evidence_source": _text(vpd_page_83.get("source")),
            "identity_evidence_conflict": vpd_page_83.get("identity_conflict") is True,
            "path": path,
            "slot": _text(connection.get("slot")),
            "mapping_source": _text(connection.get("mapping_source")),
            "mapping_confidence": _text(connection.get("mapping_confidence")) or "unknown",
            "mapping_last_confirmed_at": _text(connection.get("mapping_last_confirmed_at")),
            "controller_id": controller_id,
            "enclosure_id": (
                f"enclosure:{_text(connection.get('enclosure_id'))}"
                if _text(connection.get("enclosure_id"))
                else None
            ),
            "capacity_bytes": capacity,
            "used_bytes": used,
            "usable_bytes": usable,
            "health_status": health,
            "smart_available": bool(live_state.get("smart_available")),
            "temperature_c": live_state.get("temperature_c"),
            "protocol": protocol,
            "capable_speed_gbps": _number(connection.get("capable_speed_gbps")),
            "negotiated_speed_gbps": _number(connection.get("negotiated_speed_gbps")),
            "system_disk": disk.get("system_disk") is True,
        }
        filesystem_records: list[tuple[str, str | None, str]] = []
        disk_mountpoints = disk.get("mountpoints")
        if isinstance(disk_mountpoints, list):
            filesystem_records.extend(
                (str(mountpoint), None, stable_id)
                for mountpoint in disk_mountpoints
                if isinstance(mountpoint, str) and mountpoint.startswith("/")
            )
        partitions = disk.get("partitions")
        if isinstance(partitions, list):
            for partition in partitions:
                partition_document = _mapping(partition)
                raw_mountpoints = partition_document.get("mountpoints")
                if not isinstance(raw_mountpoints, list):
                    continue
                filesystem_type = _text(_mapping(partition_document.get("filesystem")).get("type"))
                partition_identity = _text(partition_document.get("kernel_name")) or stable_id
                filesystem_records.extend(
                    (str(mountpoint), filesystem_type, partition_identity)
                    for mountpoint in raw_mountpoints
                    if isinstance(mountpoint, str) and mountpoint.startswith("/")
                )
        for mountpoint, filesystem_type, filesystem_identity in filesystem_records:
            filesystem_id = f"filesystem:{stable_id}:{filesystem_identity}:{mountpoint}"
            nodes[filesystem_id] = {
                "id": filesystem_id,
                "kind": "filesystem",
                "label": mountpoint,
                "path": mountpoint,
                "protocol": "Logical",
                "status": "mounted",
                "filesystem_type": filesystem_type,
            }
            links[f"{disk_id}->{filesystem_id}"] = {
                "id": f"{disk_id}->{filesystem_id}",
                "source": disk_id,
                "target": filesystem_id,
                "protocol": "Logical",
                "capable_speed_gbps": None,
                "negotiated_speed_gbps": None,
            }
        enclosure_id = _text(connection.get("enclosure_id"))
        parent_id = physical_parent_id
        if enclosure_id:
            enclosure_node_id = f"enclosure:{enclosure_id}"
            nodes.setdefault(
                enclosure_node_id,
                {
                    "id": enclosure_node_id,
                    "kind": "enclosure",
                    "label": " ".join(
                        filter(
                            None,
                            (
                                _text(connection.get("enclosure_vendor")),
                                _text(connection.get("enclosure_model")),
                            ),
                        )
                    )
                    or enclosure_id,
                    "vendor": _text(connection.get("enclosure_vendor")),
                    "model": _text(connection.get("enclosure_model")),
                    "address": enclosure_id,
                    "status": _text(connection.get("enclosure_status")) or "detected",
                    "protocol": protocol,
                },
            )
            enclosure = enclosure_records.setdefault(
                enclosure_node_id,
                {
                    "id": enclosure_node_id,
                    "label": nodes[enclosure_node_id]["label"],
                    "vendor": nodes[enclosure_node_id].get("vendor"),
                    "model": nodes[enclosure_node_id].get("model"),
                    "address": enclosure_id,
                    "status": nodes[enclosure_node_id]["status"],
                    "protocols": [],
                    "controller_ids": [],
                    "bays": [],
                },
            )
            if protocol not in enclosure["protocols"]:
                enclosure["protocols"].append(protocol)
            if controller_id not in enclosure["controller_ids"]:
                enclosure["controller_ids"].append(controller_id)
            enclosure["bays"].append(
                {
                    "slot": nodes[disk_id]["slot"],
                    "drive_id": disk_id,
                    "status": _text(connection.get("enclosure_status")),
                    "locate": connection.get("locate")
                    if isinstance(connection.get("locate"), bool)
                    else None,
                    "fault": connection.get("fault")
                    if isinstance(connection.get("fault"), bool)
                    else None,
                    "mapping_source": nodes[disk_id]["mapping_source"],
                    "mapping_confidence": nodes[disk_id]["mapping_confidence"],
                    "mapping_last_confirmed_at": nodes[disk_id]["mapping_last_confirmed_at"],
                }
            )
            parent_id = enclosure_node_id
            enclosure_link_id = f"{physical_parent_id}->{enclosure_node_id}"
            links.setdefault(
                enclosure_link_id,
                {
                    "id": enclosure_link_id,
                    "source": physical_parent_id,
                    "target": enclosure_node_id,
                    "protocol": protocol,
                    "capable_speed_gbps": nodes[disk_id]["capable_speed_gbps"],
                    "negotiated_speed_gbps": nodes[disk_id]["negotiated_speed_gbps"],
                },
            )
        else:
            direct_attached.append(disk_id)
        link_id = f"{parent_id}->{disk_id}"
        links[link_id] = {
            "id": link_id,
            "source": parent_id,
            "target": disk_id,
            "protocol": protocol,
            "capable_speed_gbps": nodes[disk_id]["capable_speed_gbps"],
            "negotiated_speed_gbps": nodes[disk_id]["negotiated_speed_gbps"],
        }

    for live_enclosure in live_enclosures:
        enclosure_id = _text(live_enclosure.get("id"))
        if not enclosure_id:
            continue
        enclosure_node_id = f"enclosure:{enclosure_id}"
        label = (
            " ".join(
                filter(
                    None,
                    (_text(live_enclosure.get("vendor")), _text(live_enclosure.get("model"))),
                )
            )
            or enclosure_id
        )
        nodes.setdefault(
            enclosure_node_id,
            {
                "id": enclosure_node_id,
                "kind": "enclosure",
                "label": label,
                "vendor": _text(live_enclosure.get("vendor")),
                "model": _text(live_enclosure.get("model")),
                "address": enclosure_id,
                "status": "detected",
                "protocol": "SCSI",
            },
        )
        enclosure = enclosure_records.setdefault(
            enclosure_node_id,
            {
                "id": enclosure_node_id,
                "label": label,
                "vendor": _text(live_enclosure.get("vendor")),
                "model": _text(live_enclosure.get("model")),
                "address": enclosure_id,
                "status": "detected",
                "protocols": [],
                "controller_ids": [],
                "bays": [],
            },
        )
        existing_by_slot = {
            _text(item.get("slot")): item for item in enclosure["bays"] if isinstance(item, dict)
        }
        raw_bays = live_enclosure.get("bays")
        if isinstance(raw_bays, list):
            for raw_bay in raw_bays:
                bay = _mapping(raw_bay)
                slot = _text(bay.get("slot"))
                if slot in existing_by_slot:
                    existing_by_slot[slot]["status"] = _text(bay.get("status"))
                    existing_by_slot[slot]["locate"] = bay.get("locate")
                    existing_by_slot[slot]["fault"] = bay.get("fault")
                else:
                    enclosure["bays"].append(
                        {
                            "slot": slot,
                            "drive_id": None,
                            "status": _text(bay.get("status")),
                            "locate": bay.get("locate"),
                            "fault": bay.get("fault"),
                            "mapping_source": "sysfs enclosure slot",
                            "mapping_confidence": "high",
                            "mapping_last_confirmed_at": None,
                        }
                    )
        controller_address = _text(live_enclosure.get("controller_address"))
        if controller_address:
            controller_id = controller_node(controller_address, "SCSI")
            if controller_id not in enclosure["controller_ids"]:
                enclosure["controller_ids"].append(controller_id)
            link_id = f"{controller_id}->{enclosure_node_id}"
            links.setdefault(
                link_id,
                {
                    "id": link_id,
                    "source": controller_id,
                    "target": enclosure_node_id,
                    "protocol": "SCSI",
                    "capable_speed_gbps": None,
                    "negotiated_speed_gbps": None,
                },
            )

    enclosures = list(enclosure_records.values())
    for enclosure in enclosures:
        enclosure["protocols"].sort()
        enclosure["controller_ids"].sort()
        enclosure["bays"].sort(key=lambda item: _slot_key(item.get("slot")))
    return {
        "status": "available" if nodes else "not_available",
        "nodes": sorted(nodes.values(), key=lambda item: (str(item["kind"]), str(item["id"]))),
        "links": sorted(links.values(), key=lambda item: str(item["id"])),
        "enclosures": sorted(enclosures, key=lambda item: str(item["id"])),
        "direct_attached_drive_ids": sorted(set(direct_attached)),
    }


def add_logical_topology(
    topology: dict[str, object],
    pools: list[dict[str, Any]],
    shares: list[dict[str, Any]],
) -> dict[str, object]:
    """Add only logical relationships that live inventory explicitly reports."""

    nodes = [dict(item) for item in topology.get("nodes", []) if isinstance(item, dict)]
    links = [dict(item) for item in topology.get("links", []) if isinstance(item, dict)]
    drives_by_name = {
        Path(str(item["path"])).name: item
        for item in nodes
        if item.get("kind") == "drive" and item.get("path")
    }
    filesystems: list[tuple[Path, str]] = [
        (Path(str(item["path"])), str(item["id"]))
        for item in nodes
        if item.get("kind") == "filesystem"
        and isinstance(item.get("path"), str)
        and str(item["path"]).startswith("/")
    ]

    def link(source: str, target: str) -> None:
        identifier = f"{source}->{target}"
        if any(item.get("id") == identifier for item in links):
            return
        links.append(
            {
                "id": identifier,
                "source": source,
                "target": target,
                "protocol": "Logical",
                "capable_speed_gbps": None,
                "negotiated_speed_gbps": None,
            }
        )

    for pool in pools:
        pool_id = str(pool.get("id") or "").strip()
        if not pool_id:
            continue
        node_id = f"pool:{pool_id}"
        nodes.append(
            {
                "id": node_id,
                "kind": "pool",
                "label": str(pool.get("name") or pool_id),
                "protocol": "Logical",
                "status": str(pool.get("status") or "Not reported"),
                "pool_type": str(pool.get("type") or "Not reported"),
                "capacity_bytes": pool.get("total_bytes"),
                "used_bytes": pool.get("used_bytes"),
                "usable_bytes": pool.get("total_bytes"),
                "health_status": (
                    "warning"
                    if pool.get("degraded") is True
                    else "healthy"
                    if pool.get("degraded") is False
                    else "unknown"
                ),
            }
        )
        device_names = pool.get("device_names")
        if isinstance(device_names, list):
            for device_name in device_names:
                drive = drives_by_name.get(Path(str(device_name)).name)
                if drive:
                    link(str(drive["id"]), node_id)
        mountpoint = pool.get("mountpoint")
        if isinstance(mountpoint, str) and mountpoint.startswith("/"):
            fs_id = f"filesystem:{pool_id}"
            nodes.append(
                {
                    "id": fs_id,
                    "kind": "filesystem",
                    "label": mountpoint,
                    "path": mountpoint,
                    "protocol": "Logical",
                    "status": "mounted"
                    if pool.get("status") == "mounted"
                    else str(pool.get("status") or "Not reported"),
                }
            )
            link(node_id, fs_id)
            filesystems.append((Path(mountpoint), fs_id))

    for share in shares:
        share_id = str(share.get("id") or "").strip()
        if not share_id:
            continue
        path = share.get("path")
        node_id = f"share:{share_id}"
        nodes.append(
            {
                "id": node_id,
                "kind": "share",
                "label": str(share.get("name") or share_id),
                "path": path if isinstance(path, str) else None,
                "protocol": str(share.get("protocol") or "Not reported"),
                "status": "configured",
            }
        )
        if isinstance(path, str) and path.startswith("/"):
            share_path = Path(path)
            candidates = [
                (mountpoint, filesystem_id)
                for mountpoint, filesystem_id in filesystems
                if share_path == mountpoint or mountpoint in share_path.parents
            ]
            if candidates:
                _mountpoint, filesystem_id = max(candidates, key=lambda item: len(item[0].parts))
                link(filesystem_id, node_id)

    return {
        **topology,
        "status": "available" if nodes else topology.get("status", "not_available"),
        "nodes": sorted(nodes, key=lambda item: (str(item.get("kind")), str(item.get("id")))),
        "links": sorted(links, key=lambda item: str(item.get("id"))),
    }
