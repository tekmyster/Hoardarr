from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json
import os
import re
import shutil
import stat
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hoardarr.connectivity.lio_readback import (
    RTSLIB_SAVECONFIG_PATH,
    LioReadbackError,
    classify_managed_graph,
    managed_backstore_name,
    read_saveconfig,
    verify_managed_absent,
    verify_managed_apply,
)
from hoardarr.connectivity.service import (
    ManagedZvolBindingError,
    validate_managed_zvol_binding,
)

STATE_FILE = Path("/var/lib/hoardarr/connectivity/services.json")
SMB_FILE = Path("/etc/samba/hoardarr-connectivity.conf")
SMB_MAIN = Path("/etc/samba/smb.conf")
NFS_FILE = Path("/etc/exports.d/hoardarr.exports")
FCOE_CONFIG_DIR = Path("/etc/fcoe")
SYS_CLASS_NET = Path("/sys/class/net")
ALLOWED_ROOTS = (Path("/data"), Path("/mnt"), Path("/srv"))
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
IQN_RE = re.compile(r"^iqn\.\d{4}-\d{2}\.[a-z0-9.-]+:[A-Za-z0-9_.:-]{1,128}$")
WWPN_RE = re.compile(r"^(?:[0-9a-f]{2}:){7}[0-9a-f]{2}$")
INTERFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}$")
MANAGED_PATH_RE = re.compile(r"^/(?:[A-Za-z0-9._@+-]+)(?:/[A-Za-z0-9._@+-]+)*$")
FCOE_NETWORK_DRIVERS = {
    "bnx2x",
    "enic",
    "i40e",
    "ice",
    "ixgbe",
    "mlx4_en",
    "mlx5_core",
    "qede",
}
FIRMWARE_DCB_DRIVERS = {"bnx2x", "qede"}


class ExecutorFailure(RuntimeError):
    def __init__(self, code: str, message: str, *, needs_attention: bool = False) -> None:
        self.code = code
        self.needs_attention = needs_attention
        super().__init__(message)


class _ManagedApplyReadbackFailure(ExecutorFailure):
    pass


def _read_lio_saveconfig() -> dict[str, Any]:
    try:
        return read_saveconfig(RTSLIB_SAVECONFIG_PATH)
    except LioReadbackError as exc:
        raise ExecutorFailure(exc.code, str(exc), needs_attention=True) from exc


def _managed_apply_readback(
    service_id: str, config: Mapping[str, Any], secret: str | None
) -> dict[str, Any]:
    try:
        return verify_managed_apply(
            _read_lio_saveconfig(),
            service_id=service_id,
            config=config,
            secret=secret,
        )
    except LioReadbackError as exc:
        raise ExecutorFailure(exc.code, str(exc), needs_attention=True) from exc


def _managed_absence_readback(service_id: str, config: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return verify_managed_absent(
            _read_lio_saveconfig(),
            service_id=service_id,
            target_iqn=str(config["target_iqn"]),
        )
    except LioReadbackError as exc:
        raise ExecutorFailure(exc.code, str(exc), needs_attention=True) from exc


def _managed_preflight(
    service_id: str, config: Mapping[str, Any], secret: str | None
) -> dict[str, Any]:
    try:
        return classify_managed_graph(
            _read_lio_saveconfig(),
            service_id=service_id,
            config=config,
            secret=secret,
        )
    except LioReadbackError as exc:
        raise ExecutorFailure(exc.code, str(exc), needs_attention=True) from exc


def _command(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ExecutorFailure("connectivity_tool_missing", f"{name} is not installed.")
    return path


def _run(
    command: list[str], *, input_text: str | None = None, timeout: int = 60, capture: bool = False
) -> str:
    try:
        result = subprocess.run(
            command,
            check=True,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
        )
    except subprocess.TimeoutExpired as exc:
        raise ExecutorFailure(
            "connectivity_tool_timeout", "Connectivity timed out.", needs_attention=True
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise ExecutorFailure(
            "connectivity_tool_failed", "Connectivity could not be applied.", needs_attention=True
        ) from exc
    return result.stdout or ""


def _atomic_write(path: Path, text: str, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _load_state() -> dict[str, dict[str, Any]]:
    if not STATE_FILE.exists():
        return {}
    try:
        document = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutorFailure(
            "connectivity_state_invalid", "Connectivity state is invalid.", needs_attention=True
        ) from exc
    if (
        not isinstance(document, dict)
        or document.get("schema") != 1
        or not isinstance(document.get("services"), dict)
    ):
        raise ExecutorFailure(
            "connectivity_state_invalid", "Connectivity state is invalid.", needs_attention=True
        )
    return document["services"]


def _save_state(services: Mapping[str, Mapping[str, Any]]) -> None:
    _atomic_write(
        STATE_FILE, json.dumps({"schema": 1, "services": services}, sort_keys=True) + "\n", 0o600
    )


def _require_canonical_path(path: Path, resolved: Path) -> None:
    if path != resolved:
        raise ExecutorFailure(
            "connectivity_path_invalid",
            "Path must be a canonical managed-storage path without aliases.",
        )


def _safe_path(value: object, *, directory: bool) -> Path:
    if not isinstance(value, str) or MANAGED_PATH_RE.fullmatch(value) is None:
        raise ExecutorFailure("connectivity_path_invalid", "Path is invalid.")
    path = Path(value)
    try:
        resolved = path.resolve(strict=directory)
    except (OSError, RuntimeError) as exc:
        raise ExecutorFailure("connectivity_path_invalid", "Path is unavailable.") from exc
    if not any(resolved == root or root in resolved.parents for root in ALLOWED_ROOTS):
        raise ExecutorFailure("connectivity_path_invalid", "Path is outside managed storage.")
    _require_canonical_path(path, resolved)
    if directory and not resolved.is_dir():
        raise ExecutorFailure("connectivity_path_invalid", "Folder is unavailable.")
    return resolved


def _validate_common(config: object) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ExecutorFailure("connectivity_request_invalid", "Connectivity request is invalid.")
    protocol = config.get("protocol")
    name = config.get("name")
    if (
        protocol not in {"smb", "nfs", "iscsi", "fcoe"}
        or not isinstance(name, str)
        or not NAME_RE.fullmatch(name)
    ):
        raise ExecutorFailure("connectivity_request_invalid", "Connectivity request is invalid.")
    allowed: dict[str, set[str]] = {
        "smb": {
            "protocol",
            "name",
            "path",
            "read_only",
            "valid_users",
            "write_users",
            "read_users",
            "browseable",
            "acl",
        },
        "nfs": {"protocol", "name", "path", "read_only", "clients"},
        "iscsi": {
            "protocol",
            "name",
            "backing_path",
            "size_bytes",
            "target_iqn",
            "portal_ips",
            "initiator_iqns",
            "chap_username",
            "chap_enabled",
        },
        "fcoe": {
            "protocol",
            "name",
            "backing_path",
            "size_bytes",
            "interfaces",
            "target_wwpns",
            "fcoe_mode",
            "dcb_mode",
            "auto_vlan",
            "fip_responder",
            "initiator_wwpns",
        },
    }
    legacy_smb_keys = {"protocol", "name", "path", "read_only", "valid_users", "browseable"}
    previous_smb_keys = legacy_smb_keys | {"write_users", "read_users"}
    managed_iscsi_keys = (allowed["iscsi"] - {"backing_path", "size_bytes"}) | {
        "managed_zvol_binding"
    }
    if (
        set(config) != allowed[protocol]
        and not (protocol == "smb" and set(config) in (legacy_smb_keys, previous_smb_keys))
        and not (protocol == "iscsi" and set(config) == managed_iscsi_keys)
    ):
        raise ExecutorFailure("connectivity_request_invalid", "Connectivity request is invalid.")
    if protocol == "smb":
        _safe_path(config["path"], directory=True)
        users = config["valid_users"]
        write_users = config.get("write_users", [] if config["read_only"] else users)
        read_users = config.get("read_users", users if config["read_only"] else [])
        if (
            not isinstance(config["read_only"], bool)
            or not isinstance(config["browseable"], bool)
            or not isinstance(users, list)
            or not users
            or len(users) > 64
            or any(
                not isinstance(user, str)
                or not USERNAME_RE.fullmatch(user[1:] if user.startswith("@") else user)
                for user in users
            )
            or not isinstance(write_users, list)
            or not isinstance(read_users, list)
            or any(user not in users for user in [*write_users, *read_users])
            or bool(set(write_users) & set(read_users))
        ):
            raise ExecutorFailure("connectivity_request_invalid", "SMB settings are invalid.")
        if any(
            not (
                _group_exists(identity[1:])
                if identity.startswith("@")
                else _account_exists(identity)
            )
            for identity in users
        ):
            raise ExecutorFailure("connectivity_account_missing", "An SMB user does not exist.")
        if "acl" in config:
            _validate_acl(config["acl"], config["path"])
    elif protocol == "nfs":
        _safe_path(config["path"], directory=True)
        clients = config["clients"]
        if (
            not isinstance(config["read_only"], bool)
            or not isinstance(clients, list)
            or not clients
            or len(clients) > 64
        ):
            raise ExecutorFailure("connectivity_request_invalid", "NFS settings are invalid.")
        try:
            for client in clients:
                ipaddress.ip_network(client, strict=False)
        except (TypeError, ValueError) as exc:
            raise ExecutorFailure(
                "connectivity_request_invalid", "NFS client network is invalid."
            ) from exc
    elif protocol == "iscsi":
        if "managed_zvol_binding" in config:
            try:
                validate_managed_zvol_binding(config["managed_zvol_binding"])
            except ManagedZvolBindingError as exc:
                raise ExecutorFailure(exc.code, str(exc), needs_attention=True) from exc
            _validate_block_names(config, "target_iqn", IQN_RE, "initiator_iqns", IQN_RE)
        else:
            _validate_block(config, "target_iqn", IQN_RE, "initiator_iqns", IQN_RE)
        portals = config["portal_ips"]
        if not isinstance(portals, list) or not portals or len(portals) > 16:
            raise ExecutorFailure("connectivity_request_invalid", "iSCSI portals are invalid.")
        try:
            for address in portals:
                ipaddress.ip_address(address)
        except (TypeError, ValueError) as exc:
            raise ExecutorFailure(
                "connectivity_request_invalid", "iSCSI portal is invalid."
            ) from exc
        if not isinstance(config["chap_enabled"], bool):
            raise ExecutorFailure(
                "connectivity_request_invalid", "iSCSI authentication is invalid."
            )
        username = config["chap_username"]
        if config["chap_enabled"] and (
            not isinstance(username, str) or not NAME_RE.fullmatch(username)
        ):
            raise ExecutorFailure(
                "connectivity_request_invalid", "iSCSI authentication is invalid."
            )
        if not config["chap_enabled"] and username is not None:
            raise ExecutorFailure(
                "connectivity_request_invalid", "iSCSI authentication is invalid."
            )
    else:
        interfaces = config["interfaces"]
        targets = config["target_wwpns"]
        if (
            not isinstance(interfaces, list)
            or not interfaces
            or len(interfaces) > 8
            or len(set(interfaces)) != len(interfaces)
            or any(
                not isinstance(item, str) or not INTERFACE_RE.fullmatch(item) for item in interfaces
            )
            or not isinstance(targets, list)
            or len(targets) != len(interfaces)
        ):
            raise ExecutorFailure("connectivity_request_invalid", "FCoE ports are invalid.")
        _validate_block(config, "target_wwpns", WWPN_RE, "initiator_wwpns", WWPN_RE)
        if (
            config["fcoe_mode"] not in {"fabric", "vn2vn"}
            or config["dcb_mode"] not in {"auto", "host", "firmware", "none"}
            or not isinstance(config["auto_vlan"], bool)
            or not isinstance(config["fip_responder"], bool)
            or (config["fcoe_mode"] == "fabric" and config["fip_responder"])
            or (config["fcoe_mode"] == "vn2vn" and config["auto_vlan"])
        ):
            raise ExecutorFailure("connectivity_request_invalid", "FCoE settings are invalid.")
    return config


def _account_exists(username: str) -> bool:
    import pwd

    try:
        pwd.getpwnam(username)
    except KeyError:
        return False
    return True


def _group_exists(name: str) -> bool:
    import grp

    try:
        grp.getgrnam(name)
    except KeyError:
        return False
    return True


def _validate_acl(value: object, expected_path: object) -> None:
    if not isinstance(value, dict) or set(value) != {"path", "entries", "inherit", "anonymous"}:
        raise ExecutorFailure("connectivity_request_invalid", "SMB permissions are invalid.")
    if (
        value.get("path") != expected_path
        or value.get("anonymous") != "deny"
        or not isinstance(value.get("inherit"), bool)
    ):
        raise ExecutorFailure("connectivity_request_invalid", "SMB permissions are invalid.")
    entries = value.get("entries")
    if not isinstance(entries, list) or not entries or len(entries) > 128:
        raise ExecutorFailure("connectivity_request_invalid", "SMB permissions are invalid.")
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"kind", "name", "role", "posix"}:
            raise ExecutorFailure("connectivity_request_invalid", "SMB permissions are invalid.")
        kind, name, role, posix = (entry.get(key) for key in ("kind", "name", "role", "posix"))
        expected = {"administrator": "rwx", "media_application": "rwx", "media_user": "r-x"}.get(
            role
        )
        if (
            kind not in {"user", "group"}
            or not isinstance(name, str)
            or not USERNAME_RE.fullmatch(name)
            or expected != posix
            or (kind, name) in seen
        ):
            raise ExecutorFailure("connectivity_request_invalid", "SMB permissions are invalid.")
        seen.add((kind, name))
        exists = _account_exists(name) if kind == "user" else _group_exists(name)
        if not exists:
            raise ExecutorFailure(
                "connectivity_account_missing", "An SMB permission identity does not exist."
            )


def _apply_acl(value: Mapping[str, Any]) -> str:
    path = str(_safe_path(value["path"], directory=True))
    previous = _run(
        [_command("getfacl"), "--physical", "--absolute-names", path], timeout=60, capture=True
    )
    access = ["o::---"]
    defaults = ["d:o::---"]
    for entry in value["entries"]:
        prefix = "u" if entry["kind"] == "user" else "g"
        item = f"{prefix}:{entry['name']}:{entry['posix']}"
        access.append(item)
        if value["inherit"]:
            defaults.append(f"d:{item}")
    try:
        _run([_command("setfacl"), "--physical", "-m", ",".join(access), path], timeout=300)
        if value["inherit"]:
            _run([_command("setfacl"), "--physical", "-m", ",".join(defaults), path], timeout=300)
        _run(
            [_command("getfacl"), "--physical", "--absolute-names", path], timeout=60, capture=True
        )
    except Exception:
        with contextlib.suppress(Exception):
            _run([_command("setfacl"), "--restore=-"], input_text=previous, timeout=300)
        raise
    return previous


def _validate_block(
    config: Mapping[str, Any],
    target_field: str,
    target_re: re.Pattern[str],
    initiators_field: str,
    initiator_re: re.Pattern[str],
) -> None:
    path = _safe_path(config["backing_path"], directory=False)
    if path.exists() and not path.is_file():
        raise ExecutorFailure("connectivity_path_invalid", "Backing path is invalid.")
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ExecutorFailure("connectivity_path_invalid", "Backing folder is unavailable.")
    size = config["size_bytes"]
    if not isinstance(size, int) or isinstance(size, bool) or size < 1024**3 or size > 8 * 1024**5:
        raise ExecutorFailure("connectivity_request_invalid", "Block size is invalid.")
    _validate_block_names(config, target_field, target_re, initiators_field, initiator_re)


def _validate_block_names(
    config: Mapping[str, Any],
    target_field: str,
    target_re: re.Pattern[str],
    initiators_field: str,
    initiator_re: re.Pattern[str],
) -> None:
    target = config[target_field]
    initiators = config[initiators_field]
    targets = target if isinstance(target, list) else [target]
    if (
        not targets
        or len(targets) > 8
        or len(set(targets)) != len(targets)
        or any(
            not isinstance(item, str) or not target_re.fullmatch(item.lower()) for item in targets
        )
    ):
        raise ExecutorFailure("connectivity_request_invalid", "Target name is invalid.")
    if (
        not isinstance(initiators, list)
        or not initiators
        or len(initiators) > 64
        or any(
            not isinstance(item, str) or not initiator_re.fullmatch(item.lower())
            for item in initiators
        )
    ):
        raise ExecutorFailure("connectivity_request_invalid", "Initiator list is invalid.")


def _read_sysfs(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _network_driver(interface: Path) -> str | None:
    try:
        return (interface / "device" / "driver").resolve(strict=True).name
    except OSError:
        return None


def _fcoe_target_wwpn(mac: str) -> str:
    parts = mac.strip().lower().split(":")
    if len(parts) != 6 or any(
        len(part) != 2 or not re.fullmatch(r"[0-9a-f]{2}", part) for part in parts
    ):
        raise ExecutorFailure("fcoe_interface_invalid", "FCoE port address is unavailable.")
    return ":".join(["20", "00", *parts])


def fcoe_interface_inventory() -> list[dict[str, Any]]:
    online = ""
    if shutil.which("fcoeadm"):
        with contextlib.suppress(ExecutorFailure):
            online = _run([_command("fcoeadm"), "-i"], timeout=15, capture=True).lower()
    interfaces: list[dict[str, Any]] = []
    try:
        candidates = sorted(SYS_CLASS_NET.iterdir(), key=lambda path: path.name)
    except OSError:
        return interfaces
    for interface in candidates:
        if not INTERFACE_RE.fullmatch(interface.name) or interface.name == "lo":
            continue
        driver = _network_driver(interface)
        if driver not in FCOE_NETWORK_DRIVERS:
            continue
        mac = _read_sysfs(interface / "address")
        if mac is None:
            continue
        try:
            target_wwpn = _fcoe_target_wwpn(mac)
        except ExecutorFailure:
            continue
        state = _read_sysfs(interface / "operstate") or "unknown"
        speed_text = _read_sysfs(interface / "speed")
        speed_mbps = int(speed_text) if speed_text and speed_text.isdigit() else None
        compact_wwpn = target_wwpn.replace(":", "")
        interfaces.append(
            {
                "name": interface.name,
                "driver": driver,
                "mac": mac.lower(),
                "state": state,
                "speed_mbps": speed_mbps,
                "target_wwpn": target_wwpn,
                "dcb_owner": "firmware" if driver in FIRMWARE_DCB_DRIVERS else "host",
                "online": interface.name.lower() in online
                and compact_wwpn in online.replace(":", ""),
            }
        )
    return interfaces


def resolve_fcoe_interfaces(requested: list[str]) -> dict[str, Any]:
    inventory = {item["name"]: item for item in fcoe_interface_inventory()}
    if not requested or any(item not in inventory for item in requested):
        raise ExecutorFailure(
            "fcoe_interface_unavailable",
            "A selected FCoE network port is unavailable.",
            needs_attention=True,
        )
    selected = [inventory[item] for item in sorted(set(requested))]
    return {
        "interfaces": [item["name"] for item in selected],
        "target_wwpns": [item["target_wwpn"] for item in selected],
        "details": selected,
    }


def capabilities() -> dict[str, Any]:
    commands = {
        name: shutil.which(name) is not None
        for name in (
            "smbd",
            "testparm",
            "exportfs",
            "targetcli",
            "fcoeadm",
            "fcoemon",
            "fipvlan",
            "lldptool",
            "dcbtool",
            "modprobe",
            "modinfo",
        )
    }
    fcoe_modules: dict[str, bool] = {}
    for module in ("fcoe", "libfcoe", "tcm_fc"):
        try:
            _run([_command("modinfo"), module], timeout=15)
        except ExecutorFailure:
            fcoe_modules[module] = False
        else:
            fcoe_modules[module] = True
    fabrics = ""
    if commands["targetcli"]:
        try:
            fabrics = _run(
                [str(shutil.which("targetcli")), "ls", "/"], timeout=15, capture=True
            ).lower()
        except ExecutorFailure:
            fabrics = ""
    fcoe_interfaces = fcoe_interface_inventory()
    fcoe_tools = all(
        commands[name]
        for name in (
            "targetcli",
            "fcoeadm",
            "fcoemon",
            "fipvlan",
            "lldptool",
            "dcbtool",
            "modprobe",
            "modinfo",
        )
    )
    fcoe_installed = fcoe_tools and all(fcoe_modules.values())
    return {
        "protocols": {
            "smb": {"available": commands["smbd"] and commands["testparm"]},
            "nfs": {"available": commands["exportfs"]},
            "iscsi": {"available": commands["targetcli"] and "iscsi" in fabrics},
            "fcoe": {
                "available": fcoe_installed and bool(fcoe_interfaces),
                "installed": fcoe_installed,
                "online": any(item["online"] for item in fcoe_interfaces),
            },
        },
        "tools": commands,
        "modules": fcoe_modules,
        "fcoe_interfaces_detected": bool(fcoe_interfaces),
        "fcoe_interfaces": fcoe_interfaces,
    }


def _render_file_services(services: Mapping[str, Mapping[str, Any]]) -> None:
    smb: list[str] = ["# Managed by Hoardarr.\n"]
    nfs: list[str] = ["# Managed by Hoardarr.\n"]
    for service in sorted(
        services.values(), key=lambda item: (str(item.get("protocol")), str(item.get("name")))
    ):
        protocol = service.get("protocol")
        if protocol == "smb":
            write_users = service.get(
                "write_users", [] if service["read_only"] else service["valid_users"]
            )
            read_users = service.get(
                "read_users", service["valid_users"] if service["read_only"] else []
            )
            smb.extend(
                [
                    f"[{service['name']}]\n",
                    f"    path = {service['path']}\n",
                    "    read only = yes\n",
                    f"    browseable = {'yes' if service['browseable'] else 'no'}\n",
                    "    guest ok = no\n",
                    f"    valid users = {' '.join(service['valid_users'])}\n",
                    *([f"    write list = {' '.join(write_users)}\n"] if write_users else []),
                    *([f"    read list = {' '.join(read_users)}\n"] if read_users else []),
                    "    force group = hoardarr-media\n\n",
                ]
            )
        elif protocol == "nfs":
            options = "ro" if service["read_only"] else "rw"
            clients = " ".join(
                f"{client}({options},sync,no_subtree_check,root_squash)"
                for client in service["clients"]
            )
            nfs.append(f"{service['path']} {clients}\n")
    _atomic_write(SMB_FILE, "".join(smb))
    _atomic_write(NFS_FILE, "".join(nfs))


def _ensure_smb_include() -> None:
    include = f"include = {SMB_FILE}"
    text = SMB_MAIN.read_text(encoding="utf-8")
    if include not in text.splitlines():
        _atomic_write(SMB_MAIN, text.rstrip() + f"\n\n{include}\n", 0o644)


def _reload_file_services(protocol: str) -> None:
    if protocol == "smb":
        _ensure_smb_include()
        _run([_command("testparm"), "-s", str(SMB_MAIN)], timeout=30)
        _run([_command("systemctl"), "reload-or-restart", "smbd.service"], timeout=60)
    elif protocol == "nfs":
        _run(
            [_command("systemctl"), "enable", "--now", "nfs-server.service"],
            timeout=60,
        )
        _run([_command("exportfs"), "-ra"], timeout=60)


def _targetcli(commands: list[str]) -> None:
    script = "\n".join([*commands, "saveconfig", "exit"]) + "\n"
    _run([_command("targetcli")], input_text=script, timeout=120)


def _trusted_backing_parent(path: Path) -> int:
    """Open a root-controlled Linux directory tree without following links."""

    if os.name != "posix" or not path.is_absolute():
        raise ExecutorFailure("connectivity_path_invalid", "Backing folder is unavailable.")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            facts = os.fstat(descriptor)
            if facts.st_uid != 0 or facts.st_mode & 0o022:
                raise ExecutorFailure(
                    "connectivity_backing_parent_untrusted",
                    "Block-storage backing folders must be root controlled.",
                )
        return descriptor
    except (OSError, ExecutorFailure):
        os.close(descriptor)
        raise


def _ensure_backing_file(config: Mapping[str, Any]) -> tuple[Path, bool]:
    path = _safe_path(config["backing_path"], directory=False)
    parent_fd = _trusted_backing_parent(path.parent)
    descriptor = -1
    created = False
    try:
        try:
            descriptor = os.open(
                path.name,
                os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            usage = shutil.disk_usage(path.parent)
            reserve = max(1024**3, int(usage.total * 0.05))
            if usage.free < int(config["size_bytes"]) + reserve:
                raise ExecutorFailure(
                    "connectivity_space_insufficient", "Not enough free space."
                ) from None
            try:
                descriptor = os.open(
                    path.name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent_fd,
                )
            except FileExistsError as exc:
                raise ExecutorFailure(
                    "connectivity_backing_file_changed",
                    "Backing file identity changed.",
                    needs_attention=True,
                ) from exc
            created = True
            try:
                os.posix_fallocate(descriptor, 0, int(config["size_bytes"]))
            except AttributeError:
                os.ftruncate(descriptor, int(config["size_bytes"]))
            except OSError as exc:
                raise ExecutorFailure(
                    "connectivity_backing_allocation_failed",
                    "Backing storage could not be allocated.",
                    needs_attention=True,
                ) from exc
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
        facts = os.fstat(descriptor)
        if not stat.S_ISREG(facts.st_mode) or facts.st_size != config["size_bytes"]:
            raise ExecutorFailure(
                "connectivity_backing_file_changed",
                "Backing file identity or size does not match.",
                needs_attention=True,
            )
        named = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (named.st_dev, named.st_ino) != (facts.st_dev, facts.st_ino):
            raise ExecutorFailure(
                "connectivity_backing_file_changed",
                "Backing file identity changed.",
                needs_attention=True,
            )
        return path, created
    except Exception:
        if created:
            with contextlib.suppress(FileNotFoundError, OSError):
                current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                opened = os.fstat(descriptor) if descriptor >= 0 else None
                if opened is not None and (current.st_dev, current.st_ino) == (
                    opened.st_dev,
                    opened.st_ino,
                ):
                    os.unlink(path.name, dir_fd=parent_fd)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def _unlink_backing_file(value: object, *, missing_ok: bool) -> bool:
    path = _safe_path(value, directory=False)
    parent_fd = _trusted_backing_parent(path.parent)
    try:
        try:
            facts = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            if missing_ok:
                return False
            raise ExecutorFailure(
                "connectivity_backing_file_changed", "Backing file is missing."
            ) from None
        if not stat.S_ISREG(facts.st_mode):
            raise ExecutorFailure(
                "connectivity_backing_file_changed", "Backing file identity changed."
            )
        os.unlink(path.name, dir_fd=parent_fd)
        return True
    finally:
        os.close(parent_fd)


def _apply_iscsi(service_id: str, config: Mapping[str, Any], secret: str | None) -> dict[str, Any]:
    if not capabilities()["protocols"]["iscsi"]["available"]:
        raise ExecutorFailure("iscsi_unavailable", "iSCSI is unavailable on this server.")
    if config["chap_enabled"] and (
        not secret or re.fullmatch(r"[A-Za-z0-9._~-]{12,255}", secret) is None
    ):
        raise ExecutorFailure("connectivity_secret_invalid", "iSCSI password is invalid.")
    binding = config.get("managed_zvol_binding")
    managed = isinstance(binding, Mapping)
    if managed:
        try:
            binding = validate_managed_zvol_binding(binding)
        except ManagedZvolBindingError as exc:
            raise ExecutorFailure(exc.code, str(exc), needs_attention=True) from exc
        path = binding["device_path"]
        created = False
        backstore_kind = "block"
        backstore = managed_backstore_name(service_id)
    else:
        path, created = _ensure_backing_file(config)
        backstore_kind = "fileio"
        backstore = f"hoardarr-{service_id[:12]}"
    target = config["target_iqn"]
    commands = [
        f"/backstores/{backstore_kind} create {backstore} {path}",
        f"/iscsi create {target}",
        f"/iscsi/{target}/tpg1/luns create /backstores/{backstore_kind}/{backstore}",
        f"/iscsi/{target}/tpg1 set attribute generate_node_acls=0 "
        f"demo_mode_write_protect=1 authentication={1 if config['chap_enabled'] else 0}",
    ]
    if config["portal_ips"] != ["0.0.0.0"]:
        commands.append(f"/iscsi/{target}/tpg1/portals delete 0.0.0.0 3260")
        for portal in config["portal_ips"]:
            commands.append(f"/iscsi/{target}/tpg1/portals create {portal} 3260")
    for initiator in config["initiator_iqns"]:
        commands.append(f"/iscsi/{target}/tpg1/acls create {initiator}")
        if config["chap_enabled"]:
            commands.append(
                f"/iscsi/{target}/tpg1/acls/{initiator} set auth "
                f"userid={config['chap_username']} password={secret}"
            )
    try:
        _targetcli(commands)
    except Exception as exc:
        if created:
            with contextlib.suppress(ExecutorFailure, OSError):
                _unlink_backing_file(config["backing_path"], missing_ok=True)
        if managed:
            try:
                _remove_iscsi(service_id, config)
            except Exception as cleanup_exc:
                raise ExecutorFailure(
                    "connectivity_iscsi_rollback_failed",
                    "Managed iSCSI setup failed and cleanup requires attention.",
                    needs_attention=True,
                ) from cleanup_exc
        raise exc
    if managed:
        try:
            return {"readback": _managed_apply_readback(service_id, config, secret)}
        except ExecutorFailure as readback_exc:
            try:
                _remove_iscsi(service_id, config)
                _managed_absence_readback(service_id, config)
            except Exception as cleanup_exc:
                raise _ManagedApplyReadbackFailure(
                    "connectivity_lio_readback_cleanup_uncertain",
                    "iSCSI readback failed and cleanup could not be verified.",
                    needs_attention=True,
                ) from cleanup_exc
            raise _ManagedApplyReadbackFailure(
                readback_exc.code,
                "The current iSCSI target state could not be verified.",
                needs_attention=True,
            ) from readback_exc
    return {}


def _remove_iscsi(service_id: str, config: Mapping[str, Any]) -> None:
    target = config["target_iqn"]
    if "managed_zvol_binding" in config:
        backstore_kind = "block"
        backstore = managed_backstore_name(service_id)
    else:
        backstore_kind = "fileio"
        backstore = f"hoardarr-{service_id[:12]}"
    _targetcli([f"/iscsi delete {target}", f"/backstores/{backstore_kind} delete {backstore}"])


def _fcoe_config_path(interface: str) -> Path:
    if not INTERFACE_RE.fullmatch(interface):
        raise ExecutorFailure("fcoe_interface_invalid", "FCoE network port is invalid.")
    return FCOE_CONFIG_DIR / f"cfg-{interface}"


def _configure_fcoe_interfaces(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    resolved = resolve_fcoe_interfaces(list(config["interfaces"]))
    if resolved["target_wwpns"] != list(config["target_wwpns"]):
        raise ExecutorFailure(
            "fcoe_interface_changed",
            "An FCoE network port changed.",
            needs_attention=True,
        )
    for module in ("fcoe", "libfcoe", "tcm_fc"):
        _run([_command("modprobe"), module], timeout=30)
    _run([_command("systemctl"), "enable", "--now", "lldpad.service"], timeout=60)
    for details in resolved["details"]:
        interface = details["name"]
        requested_dcb = config["dcb_mode"]
        dcb_mode = details["dcb_owner"] if requested_dcb == "auto" else requested_dcb
        path = _fcoe_config_path(interface)
        if path.exists():
            try:
                existing = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ExecutorFailure(
                    "fcoe_config_unavailable", "FCoE settings are unavailable."
                ) from exc
            if not existing.startswith("# Managed by Hoardarr."):
                raise ExecutorFailure(
                    "fcoe_config_conflict",
                    "This FCoE network port is managed outside Hoardarr.",
                    needs_attention=True,
                )
        _atomic_write(
            path,
            "\n".join(
                (
                    "# Managed by Hoardarr.",
                    'FCOE_ENABLE="yes"',
                    f'DCB_REQUIRED="{"yes" if dcb_mode == "host" else "no"}"',
                    f'AUTO_VLAN="{"yes" if config["auto_vlan"] else "no"}"',
                    f'MODE="{config["fcoe_mode"]}"',
                    f'FIP_RESP="{"yes" if config["fip_responder"] else "no"}"',
                    "",
                )
            ),
            0o640,
        )
        if dcb_mode == "host":
            _run([_command("lldptool"), "set-lldp", "-i", interface, "adminStatus=rxtx"])
            _run([_command("dcbtool"), "sc", interface, "dcb", "on"])
            _run([_command("dcbtool"), "sc", interface, "app:fcoe", "e:1"])
        elif dcb_mode == "firmware":
            _run([_command("lldptool"), "set-lldp", "-i", interface, "adminStatus=disabled"])
    _run([_command("systemctl"), "enable", "--now", "fcoe-utils.service"], timeout=60)
    _run([_command("systemctl"), "restart", "fcoe-utils.service"], timeout=60)
    expected = {item.replace(":", "") for item in config["target_wwpns"]}
    deadline = time.monotonic() + 30
    output = ""
    while time.monotonic() < deadline:
        try:
            output = _run([_command("fcoeadm"), "-i"], timeout=15, capture=True).lower()
        except ExecutorFailure:
            output = ""
        compact = output.replace(":", "")
        if all(interface.lower() in output for interface in config["interfaces"]) and all(
            target in compact for target in expected
        ):
            return resolved["details"]
        time.sleep(1)
    raise ExecutorFailure(
        "fcoe_fabric_offline",
        "FCoE ports could not connect to the fabric.",
        needs_attention=True,
    )


def _apply_fcoe(service_id: str, config: Mapping[str, Any]) -> dict[str, Any]:
    if not capabilities()["protocols"]["fcoe"]["available"]:
        raise ExecutorFailure("fcoe_unavailable", "FCoE is unavailable on this server.")
    interface_details = _configure_fcoe_interfaces(config)
    fabrics = _run([_command("targetcli"), "ls", "/"], timeout=15, capture=True).lower()
    if "tcm_fc" not in fabrics:
        raise ExecutorFailure("fcoe_target_unavailable", "The FCoE target service is unavailable.")
    path, created = _ensure_backing_file(config)
    backstore = f"hoardarr-{service_id[:12]}"
    commands = [f"/backstores/fileio create {backstore} {path}"]
    for target in config["target_wwpns"]:
        commands.extend(
            [
                f"/tcm_fc create {target}",
                f"/tcm_fc/{target}/luns create /backstores/fileio/{backstore}",
            ]
        )
        for initiator in config["initiator_wwpns"]:
            commands.append(f"/tcm_fc/{target}/acls create {initiator}")
    try:
        _targetcli(commands)
    except Exception:
        with contextlib.suppress(Exception):
            _remove_fcoe(service_id, config)
        if created:
            with contextlib.suppress(ExecutorFailure, OSError):
                _unlink_backing_file(config["backing_path"], missing_ok=True)
        raise
    return {
        "interfaces": interface_details,
        "target_wwpns": list(config["target_wwpns"]),
        "mode": config["fcoe_mode"],
    }


def _remove_fcoe(service_id: str, config: Mapping[str, Any]) -> None:
    backstore = f"hoardarr-{service_id[:12]}"
    _targetcli(
        [
            *(f"/tcm_fc delete {target}" for target in config["target_wwpns"]),
            f"/backstores/fileio delete {backstore}",
        ]
    )


def apply(
    service_id: str, config_sha256: str, raw_config: object, secret: object
) -> dict[str, Any]:
    config = _validate_common(raw_config)
    if (
        hashlib.sha256(
            json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        != config_sha256
    ):
        raise ExecutorFailure("connectivity_config_changed", "Connectivity settings changed.")
    services = _load_state()
    previous = dict(services)
    previous_service = previous.get(service_id)
    incoming_secret = secret if isinstance(secret, str) else None
    if "managed_zvol_binding" in config and (
        previous_service is None or previous_service == config
    ):
        preflight = _managed_preflight(service_id, config, incoming_secret)
        classification = preflight["classification"]
        if classification == "exact_active":
            if previous_service is None:
                services[service_id] = config
                _save_state(services)
                return {
                    "service_id": service_id,
                    "protocol": "iscsi",
                    "state": "active",
                    "reconciled_existing": True,
                    "readback": preflight["evidence"],
                }
            return {
                "service_id": service_id,
                "protocol": "iscsi",
                "state": "active",
                "already_active": True,
                "readback": preflight["evidence"],
            }
        if previous_service is not None:
            raise ExecutorFailure(
                "connectivity_lio_preflight_absent",
                "The managed iSCSI target is unexpectedly absent.",
                needs_attention=True,
            )
    services[service_id] = config
    protocol = config["protocol"]
    result_details: dict[str, Any] = {}
    acl_previous: str | None = None
    try:
        if protocol in {"smb", "nfs"}:
            if protocol == "smb" and "acl" in config:
                acl_previous = _apply_acl(config["acl"])
            _render_file_services(services)
            _reload_file_services(protocol)
        elif protocol == "iscsi":
            if previous_service is not None:
                if previous_service.get("protocol") == "iscsi":
                    _remove_iscsi(service_id, previous_service)
                elif previous_service.get("protocol") == "fcoe":
                    _remove_fcoe(service_id, previous_service)
            result_details = _apply_iscsi(service_id, config, incoming_secret)
        else:
            if previous_service is not None:
                if previous_service.get("protocol") == "iscsi":
                    _remove_iscsi(service_id, previous_service)
                elif previous_service.get("protocol") == "fcoe":
                    _remove_fcoe(service_id, previous_service)
            result_details = _apply_fcoe(service_id, config)
        _save_state(services)
    except Exception as exc:
        if acl_previous is not None:
            with contextlib.suppress(Exception):
                _run([_command("setfacl"), "--restore=-"], input_text=acl_previous, timeout=300)
        if protocol in {"smb", "nfs"}:
            with contextlib.suppress(Exception):
                _render_file_services(previous)
                _reload_file_services(protocol)
        elif previous_service is not None and not isinstance(exc, _ManagedApplyReadbackFailure):
            with contextlib.suppress(Exception):
                if previous_service.get("protocol") == "iscsi":
                    _apply_iscsi(
                        service_id,
                        previous_service,
                        secret if isinstance(secret, str) else None,
                    )
                elif previous_service.get("protocol") == "fcoe":
                    _apply_fcoe(service_id, previous_service)
        raise
    return {
        "service_id": service_id,
        "protocol": protocol,
        "state": "active",
        **result_details,
    }


def remove(
    service_id: str, config_sha256: str, raw_config: object, delete_backing_data: object
) -> dict[str, Any]:
    config = _validate_common(raw_config)
    if not isinstance(delete_backing_data, bool):
        raise ExecutorFailure("connectivity_request_invalid", "Connectivity request is invalid.")
    if delete_backing_data and "managed_zvol_binding" in config:
        raise ExecutorFailure(
            "connectivity_managed_zvol_delete_forbidden",
            "Managed ZFS volume data cannot be deleted through connectivity removal.",
        )
    if (
        hashlib.sha256(
            json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        != config_sha256
    ):
        raise ExecutorFailure("connectivity_config_changed", "Connectivity settings changed.")
    services = _load_state()
    managed = services.get(service_id)
    if managed is None:
        readback = (
            _managed_absence_readback(service_id, config)
            if "managed_zvol_binding" in config
            else None
        )
        return {
            "service_id": service_id,
            "state": "removed",
            "already_absent": True,
            "backing_data_deleted": False,
            **({"readback": readback} if readback is not None else {}),
        }
    if managed != config:
        raise ExecutorFailure(
            "connectivity_state_changed", "Connectivity state changed.", needs_attention=True
        )
    protocol = config["protocol"]
    previous = dict(services)
    services.pop(service_id)
    result_details: dict[str, Any] = {}
    try:
        if protocol in {"smb", "nfs"}:
            _render_file_services(services)
            _reload_file_services(protocol)
        elif protocol == "iscsi":
            _remove_iscsi(service_id, config)
            if "managed_zvol_binding" in config:
                result_details = {"readback": _managed_absence_readback(service_id, config)}
        else:
            _remove_fcoe(service_id, config)
        if delete_backing_data and protocol in {"iscsi", "fcoe"}:
            _unlink_backing_file(config["backing_path"], missing_ok=True)
        _save_state(services)
    except Exception:
        if protocol in {"smb", "nfs"}:
            with contextlib.suppress(Exception):
                _render_file_services(previous)
                _reload_file_services(protocol)
        raise
    return {
        "service_id": service_id,
        "protocol": protocol,
        "state": "removed",
        "backing_data_deleted": bool(delete_backing_data and protocol in {"iscsi", "fcoe"}),
        **result_details,
    }
