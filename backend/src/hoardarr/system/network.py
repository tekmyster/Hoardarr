from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

INTEL_FIRMWARE_LLDP_DRIVERS = frozenset({"i40e", "ice"})


def _read_text(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return value or None


def _linked_name(path: Path) -> str | None:
    if path.is_file():
        return _read_text(path)
    try:
        return path.resolve(strict=True).name
    except OSError:
        return None


def _integer(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _positive_integer(value: str | None) -> int | None:
    parsed = _integer(value)
    return parsed if parsed is not None and parsed > 0 else None


def _properties(path: Path) -> dict[str, str]:
    """Read sysfs ``KEY=value`` or udev database ``E:KEY=value`` properties."""

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    properties: dict[str, str] = {}
    for line in lines:
        if line.startswith("E:"):
            line = line[2:]
        key, separator, value = line.partition("=")
        if separator and key and value:
            properties[key] = value
    return properties


def _model_from_inventory(
    entry: Path,
    device: Path,
    udev_data_root: Path,
    device_properties: dict[str, str],
) -> tuple[str | None, str | None]:
    # Some platform and virtual NIC drivers publish an unambiguous model in
    # sysfs. PCI NICs normally get their human-readable name from the local
    # udev hardware database instead.
    for filename in ("model", "product_name"):
        model = _read_text(device / filename)
        if model:
            return model, f"sysfs:device/{filename}"

    database_records: list[tuple[Path, str]] = []
    interface_index = _positive_integer(_read_text(entry / "ifindex"))
    if interface_index is not None:
        database_records.append((udev_data_root / f"n{interface_index}", "udev:net"))

    pci_slot = device_properties.get("PCI_SLOT_NAME")
    if not pci_slot:
        linked_name = _linked_name(device)
        if linked_name and linked_name != "device":
            pci_slot = linked_name
    if pci_slot:
        database_records.append((udev_data_root / f"+pci:{pci_slot}", "udev:pci"))

    for database_path, source in database_records:
        properties = _properties(database_path)
        for key in ("ID_MODEL_FROM_DATABASE", "ID_MODEL"):
            model = properties.get(key)
            if model:
                return model, f"{source}/{key}"
    return None, None


def discover_network_interfaces(
    sysfs_root: Path = Path("/sys"),
    udev_data_root: Path = Path("/run/udev/data"),
) -> list[dict[str, Any]]:
    """Return predictable, read-only NIC inventory from sysfs.

    Interface names are locators, not friendly aliases.  PCI address, permanent
    MAC and driver are included so a future apply operation can re-check that
    the selected hardware has not changed. Link speed is read directly from
    sysfs and a display model is read from sysfs or the local udev database;
    no executable or shell is invoked. A fact that cannot be proven is returned
    as ``null`` and named in ``unknown_fields`` rather than guessed.
    """

    base = sysfs_root / "class" / "net"
    try:
        entries = sorted(base.iterdir(), key=lambda item: item.name)
    except OSError:
        return []
    interfaces: list[dict[str, Any]] = []
    for entry in entries:
        if entry.name == "lo" or not entry.is_dir():
            continue
        device = entry / "device"
        device_properties = _properties(device / "uevent")
        driver = _linked_name(device / "driver")
        device_address = device_properties.get("PCI_SLOT_NAME") or _linked_name(device)
        if device_address == "device":
            device_address = None
        model, model_source = _model_from_inventory(
            entry,
            device,
            udev_data_root,
            device_properties,
        )
        speed_mbps = _positive_integer(_read_text(entry / "speed"))
        carrier_raw = _read_text(entry / "carrier")
        interface: dict[str, Any] = {
            "id": entry.name,
            "name": entry.name,
            "mac_address": _read_text(entry / "address"),
            "mtu": _integer(_read_text(entry / "mtu")),
            "operational_state": _read_text(entry / "operstate") or "unknown",
            "carrier": carrier_raw == "1" if carrier_raw in {"0", "1"} else None,
            "speed_mbps": speed_mbps,
            "model": model,
            "driver": driver,
            "device_address": device_address,
            "pci_id": device_properties.get("PCI_ID"),
            "physical_port_name": _read_text(entry / "phys_port_name"),
            "physical_port_id": _read_text(entry / "phys_port_id"),
            "is_physical": device.exists(),
            "fact_sources": {
                "speed_mbps": "sysfs:class/net/speed" if speed_mbps is not None else None,
                "model": model_source,
            },
            "lldp": {
                "default": "rx_tx",
                "firmware_ownership": (
                    "verify_before_transmit"
                    if driver in INTEL_FIRMWARE_LLDP_DRIVERS
                    else "host_managed"
                ),
            },
        }
        interface["unknown_fields"] = [
            field for field in ("speed_mbps", "model") if interface[field] is None
        ]
        warnings: list[str] = []
        if driver in INTEL_FIRMWARE_LLDP_DRIVERS:
            warnings.append(
                "Intel X710/E810-class adapters can let firmware own LLDP for DCB/FCoE; "
                "Hoardarr will verify ownership before enabling host LLDP transmission."
            )
        if not interface["is_physical"]:
            warnings.append("This is a virtual interface and is hidden from Guided setup.")
        interface["warnings"] = warnings
        interfaces.append(interface)
    return interfaces


def normalized_hash(document: dict[str, Any]) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
