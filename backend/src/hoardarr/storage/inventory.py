from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from hoardarr.hardware.providers import (
    NOT_REPORTED,
    ProviderError,
    aggregate_health,
    parse_arcconf,
    parse_areca,
    parse_ses,
    parse_snapraid_status,
    parse_ssacli,
    parse_storcli,
    parse_zpool_status,
)
from hoardarr.storage.mergerfs import discover_mergerfs
from hoardarr.storage.topology import add_logical_topology, build_storage_topology
from hoardarr.storage.zfs import parse_zpool_data_topology, valid_pool_guid

_SNAPRAID_PARITY_RE = re.compile(r"^(?:(\d+)-)?parity$")


def _snapraid_path(value: str) -> str | None:
    candidate = value.strip()
    path = PurePosixPath(candidate)
    if not candidate.startswith("/") or "\x00" in candidate or ".." in path.parts:
        return None
    return str(path)


def _snapraid_configuration(config: Path) -> dict[str, Any]:
    """Read the bounded, non-secret topology portion of a SnapRAID configuration."""

    try:
        if config.is_symlink() or config.stat().st_size > 1024 * 1024:
            raise OSError("unsafe SnapRAID configuration")
        raw = config.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return {
            "quality": "temporarily_unavailable",
            "data_disks": [],
            "parity_disks": [],
            "content_files": [],
            "config_sha256": None,
            "errors": ["SnapRAID configuration could not be safely read."],
        }
    data_disks: list[dict[str, str]] = []
    parity_disks: list[dict[str, object]] = []
    content_files: list[str] = []
    errors: list[str] = []
    for line_number, raw_line in enumerate(raw.splitlines()[:16_384], start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(maxsplit=2)
        parity_match = _SNAPRAID_PARITY_RE.fullmatch(fields[0])
        if parity_match:
            value = _snapraid_path(line[len(fields[0]) :].strip())
            if value is None:
                errors.append(f"Invalid parity path on line {line_number}.")
                continue
            level = int(parity_match.group(1) or "1")
            parity_disks.append({"level": level, "path": value})
        elif fields[0] == "content":
            value = _snapraid_path(line[len("content") :].strip())
            if value is None:
                errors.append(f"Invalid content path on line {line_number}.")
                continue
            content_files.append(value)
        elif fields[0] == "data":
            if len(fields) != 3 or not fields[1] or len(fields[1]) > 128:
                errors.append(f"Invalid data declaration on line {line_number}.")
                continue
            value = _snapraid_path(fields[2])
            if value is None:
                errors.append(f"Invalid data path on line {line_number}.")
                continue
            data_disks.append({"name": fields[1], "path": value})
    if len(data_disks) != len({item["name"] for item in data_disks}):
        errors.append("SnapRAID data names are not unique.")
    if len(parity_disks) != len({item["level"] for item in parity_disks}):
        errors.append("SnapRAID parity levels are not unique.")
    return {
        "quality": "available" if not errors else "temporarily_unavailable",
        "data_disks": data_disks,
        "parity_disks": sorted(parity_disks, key=lambda item: int(item["level"])),
        "content_files": sorted(set(content_files)),
        "config_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "errors": errors[:32],
    }


def _command(name: str, arguments: list[str]) -> str | None:
    executable = shutil.which(name, path="/usr/sbin:/usr/bin:/sbin:/bin")
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [executable, *arguments],
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _zfs_pools() -> list[dict[str, Any]]:
    output = _command("zpool", ["list", "-Hp", "-o", "name,size,alloc,free,health"])
    if output is None:
        return []
    mountpoints: dict[str, str] = {}
    dataset_output = _command("zfs", ["list", "-Hp", "-o", "name,mountpoint"])
    if dataset_output:
        for dataset_line in dataset_output.splitlines():
            dataset_fields = dataset_line.split("\t")
            if len(dataset_fields) == 2 and "/" not in dataset_fields[0]:
                candidate = dataset_fields[1]
                if candidate.startswith("/"):
                    mountpoints[dataset_fields[0]] = candidate
    items: list[dict[str, Any]] = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 5:
            continue
        name, size, allocated, free, health = fields
        status_output = _command("zpool", ["status", "-P", name]) or ""
        topology = parse_zpool_data_topology(status_output, name)
        guid_output = _command("zpool", ["get", "-Hp", "-o", "value", "guid", name])
        pool_guid = guid_output.strip() if guid_output else None
        if not valid_pool_guid(pool_guid):
            pool_guid = None
        device_names = [Path(candidate).name for candidate in topology.member_paths]
        member_capacities: dict[str, int] = {}
        for member_path in topology.member_paths:
            capacity_output = _command("blockdev", ["--getsize64", member_path])
            try:
                capacity = int((capacity_output or "").strip())
            except ValueError:
                continue
            if capacity > 0:
                member_capacities[member_path] = capacity
        try:
            provider = parse_zpool_status(status_output) if status_output else None
            items.append(
                {
                    "id": f"zfs:{name}",
                    "name": name,
                    "type": "ZFS",
                    "status": health.lower(),
                    "total_bytes": int(size),
                    "used_bytes": int(allocated),
                    "free_bytes": int(free),
                    "members": None,
                    "mountpoint": mountpoints.get(name),
                    "device_names": sorted(set(device_names)),
                    "pool_guid": pool_guid,
                    "configuration": {
                        **topology.document(),
                        "member_capacities": member_capacities,
                    },
                    "degraded": provider["degraded"] if provider else health.casefold() != "online",
                    "maintenance": provider["scan"] if provider else NOT_REPORTED,
                    "progress_percent": provider["scan_percent"] if provider else NOT_REPORTED,
                }
            )
        except (ValueError, ProviderError):
            continue
    return items


def _md_arrays(sys_class_block: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not sys_class_block.exists():
        return items
    for device in sorted(sys_class_block.glob("md*")):
        metadata = device / "md"
        if not metadata.is_dir():
            continue
        try:
            state = (metadata / "array_state").read_text(encoding="utf-8").strip()
            members = int((metadata / "raid_disks").read_text(encoding="utf-8").strip())
            degraded = int((metadata / "degraded").read_text(encoding="utf-8").strip())
            level = (metadata / "level").read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            continue
        progress: float | str = NOT_REPORTED
        action = "idle"
        try:
            action = (metadata / "sync_action").read_text(encoding="utf-8").strip()
            completed, total = (metadata / "sync_completed").read_text(encoding="utf-8").split()
            progress = round(int(completed) / int(total) * 100, 2) if int(total) else 0.0
        except (OSError, ValueError, ZeroDivisionError):
            pass
        mount_output = _command("lsblk", ["-npo", "MOUNTPOINT", f"/dev/{device.name}"])
        mountpoint = next(
            (
                line.strip()
                for line in (mount_output or "").splitlines()
                if line.strip().startswith("/")
            ),
            None,
        )
        member_paths = sorted(f"/dev/{path.name}" for path in device.glob("slaves/*"))
        array_path = f"/dev/{device.name}"
        detail_output = _command("mdadm", ["--detail", "--export", array_path])
        detail: dict[str, str] = {}
        for line in (detail_output or "").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.startswith("MD_") and len(value) <= 512:
                detail[key] = value.strip()
        array_uuid = detail.get("MD_UUID")
        configuration = {
            "array_path": array_path,
            "array_uuid": array_uuid,
            "level": level,
            "raid_disks": members,
            "member_paths": member_paths,
        }
        canonical = {
            **configuration,
            "member_paths": sorted(member_paths),
        }
        configuration["config_sha256"] = (
            hashlib.sha256(
                json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if array_uuid
            else None
        )
        items.append(
            {
                "id": f"md:{device.name}",
                "name": device.name,
                "type": f"Linux MD {level}",
                "status": "degraded" if degraded else state,
                "total_bytes": None,
                "used_bytes": None,
                "free_bytes": None,
                "members": members,
                "mountpoint": mountpoint,
                "device_names": sorted(path.name for path in device.glob("slaves/*")),
                "configuration": configuration,
                "degraded": bool(degraded),
                "maintenance": action,
                "progress_percent": progress,
            }
        )
    return items


def _snapraid_arrays(config_root: Path) -> list[dict[str, Any]]:
    if not config_root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for config in sorted(config_root.glob("*.conf")):
        configuration = _snapraid_configuration(config)
        output = _command("snapraid", ["-c", str(config), "status"])
        if output is None:
            status = {
                "state": NOT_REPORTED,
                "parity_fresh": NOT_REPORTED,
                "unsynced_items": NOT_REPORTED,
                "bad_blocks": NOT_REPORTED,
                "last_sync": NOT_REPORTED,
            }
        else:
            try:
                status = parse_snapraid_status(output)
            except ProviderError:
                status = {
                    "state": NOT_REPORTED,
                    "parity_fresh": NOT_REPORTED,
                    "unsynced_items": NOT_REPORTED,
                    "bad_blocks": NOT_REPORTED,
                    "last_sync": NOT_REPORTED,
                }
        items.append(
            {
                "id": f"snapraid:{config.stem}",
                "name": config.stem,
                "type": "SnapRAID",
                "status": status["state"],
                "total_bytes": None,
                "used_bytes": None,
                "free_bytes": None,
                "members": None,
                "mountpoint": None,
                "device_names": [],
                "degraded": status["state"] in {"failed", "needs_attention"},
                "maintenance": "parity sync",
                "progress_percent": NOT_REPORTED,
                "parity_fresh": status["parity_fresh"],
                "unsynced_items": status["unsynced_items"],
                "bad_blocks": status["bad_blocks"],
                "last_sync": status["last_sync"],
                "configuration": configuration,
            }
        )
    return items


def _controller_health() -> dict[str, Any]:
    probes = (
        (("storcli2", "storcli", "perccli"), ["/call", "show", "all", "J"], parse_storcli),
        (("ssacli", "hpssacli"), ["ctrl", "all", "show", "status"], parse_ssacli),
        (("arcconf",), ["GETCONFIG", "1", "AD"], parse_arcconf),
        (("cli64",), ["sys", "info"], parse_areca),
    )
    values: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for commands, arguments, parser in probes:
        command = next((item for item in commands if shutil.which(item)), None)
        if command is None:
            continue
        output = _command(command, arguments)
        if output is None:
            errors.append({"provider": command, "status": NOT_REPORTED})
            continue
        try:
            document = parser(output)
        except ProviderError:
            errors.append({"provider": command, "status": NOT_REPORTED})
            continue
        for controller in document.get("controllers", []):
            if isinstance(controller, dict):
                values.append({**controller, "provider": document["provider"]})
    summary = aggregate_health(values)
    return {"status": summary["health"], "items": values, "unavailable": errors}


def _enclosure_health(sys_class_enclosure: Path) -> dict[str, Any]:
    """Collect bounded, read-only SES health from each reported enclosure path."""

    if not sys_class_enclosure.is_dir() or shutil.which("sg_ses") is None:
        return {"status": NOT_REPORTED, "items": [], "unavailable": []}
    values: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for enclosure in sorted(sys_class_enclosure.iterdir(), key=lambda item: item.name):
        generic = next(iter(enclosure.glob("device/scsi_generic/*")), None)
        if generic is None:
            errors.append({"provider": "sg_ses", "path": enclosure.name, "status": NOT_REPORTED})
            continue
        output = _command("sg_ses", ["--json", f"/dev/{generic.name}"])
        if output is None:
            errors.append({"provider": "sg_ses", "path": generic.name, "status": NOT_REPORTED})
            continue
        try:
            document = parse_ses(output)
        except ProviderError:
            errors.append({"provider": "sg_ses", "path": generic.name, "status": NOT_REPORTED})
            continue
        for item in document.get("enclosures", []):
            if isinstance(item, dict):
                values.append({**item, "provider": "sg_ses", "path": generic.name})
    summary = aggregate_health(values)
    return {"status": summary["health"], "items": values, "unavailable": errors}


def _smb_shares(config: Path) -> list[dict[str, Any]]:
    if not config.is_file():
        return []
    try:
        lines = config.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            current = {"id": f"smb:{name}", "name": name, "protocol": "SMB", "path": None}
            items.append(current)
        elif current is not None and "=" in line:
            key, value = (part.strip() for part in line.split("=", 1))
            if key.casefold() == "path":
                current["path"] = value
    return items


def _nfs_exports(exports: Path) -> list[dict[str, Any]]:
    if not exports.is_file():
        return []
    try:
        lines = exports.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    items = []
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        path = line.split(maxsplit=1)[0]
        items.append({"id": f"nfs:{index + 1}", "name": path, "protocol": "NFS", "path": path})
    return items


def _block_targets(config: Path) -> list[dict[str, Any]]:
    if not config.is_file():
        return []
    try:
        document = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    targets = document.get("targets") if isinstance(document, dict) else None
    if not isinstance(targets, list):
        return []
    items = []
    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            continue
        fabric = str(target.get("fabric", "unknown")).lower()
        wwn = str(target.get("wwn", f"target-{index + 1}"))
        protocol = "iSCSI" if fabric == "iscsi" else "FCoE" if "fc" in fabric else fabric
        items.append(
            {"id": f"target:{fabric}:{wwn}", "name": wwn, "protocol": protocol, "path": None}
        )
    return items


def discover_storage_inventory(
    *,
    sys_class_block: Path = Path("/sys/class/block"),
    sys_class_enclosure: Path = Path("/sys/class/enclosure"),
    samba_config: Path = Path("/etc/samba/hoardarr-shares.conf"),
    nfs_exports: Path = Path("/etc/exports"),
    target_config: Path = Path("/etc/rtslib-fb-target/saveconfig.json"),
    snapraid_config_root: Path = Path("/etc/snapraid"),
    hardware_snapshot: object | None = None,
) -> dict[str, Any]:
    mergerfs = discover_mergerfs()
    pools = [*_zfs_pools(), *_md_arrays(sys_class_block), *_snapraid_arrays(snapraid_config_root)]
    for instance in mergerfs["items"]:
        pools.append(
            {
                "id": instance["id"],
                "name": instance["name"],
                "type": "mergerFS",
                "status": "mounted" if instance["active"] else "configured",
                "total_bytes": None,
                "used_bytes": None,
                "free_bytes": None,
                "members": len(instance["branches"]),
                "mountpoint": instance["mountpoint"],
                "branches": instance["branches"],
                "device_names": [],
            }
        )
    shares = [
        *_smb_shares(samba_config),
        *_nfs_exports(nfs_exports),
        *_block_targets(target_config),
    ]
    topology = (
        add_logical_topology(build_storage_topology(hardware_snapshot), pools, shares)
        if hardware_snapshot is not None
        else {
            "status": "not_available",
            "nodes": [],
            "links": [],
            "enclosures": [],
            "direct_attached_drive_ids": [],
        }
    )
    return {
        "captured_from": "live_host",
        "topology": topology,
        "pools": {"status": "configured" if pools else "not_configured", "items": pools},
        "shares": {"status": "configured" if shares else "not_configured", "items": shares},
        "controllers": _controller_health(),
        "enclosures": _enclosure_health(sys_class_enclosure),
    }
