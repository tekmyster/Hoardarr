from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoardarr.api.networking_schemas import ManagedNetworkRequest
from hoardarr.system.network import discover_network_interfaces, normalized_hash

INTERFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
PROTOCOL_PORTS: dict[str, tuple[str, tuple[int, ...]]] = {
    "ssh": ("tcp", (22,)),
    "http": ("tcp", (80,)),
    "https": ("tcp", (443, 7877)),
    "smb": ("tcp", (139, 445)),
    "nfs": ("both", (2049,)),
    "iscsi": ("tcp", (3260,)),
}
RUNTIME_PYTHON = "/usr/lib/hoardarr/venv/bin/python"
MANAGED_SERVICES = (
    "systemd-timesyncd.service",
    "lldpd.service",
    "rsyslog.service",
    "snmpd.service",
    "nftables.service",
)
NETWORK_COMPONENTS = (
    "server",
    "network",
    "ntp",
    "discovery",
    "syslog",
    "snmp",
    "traps",
    "access_rules",
)
REDACTED_SECRET = "********"
SERVICE_COMPONENTS = {
    "systemd-timesyncd.service": {"ntp"},
    "lldpd.service": {"discovery"},
    "rsyslog.service": {"syslog"},
    "snmpd.service": {"snmp", "traps"},
    "nftables.service": {"access_rules"},
}


class NetworkFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Paths:
    state_root: Path = Path("/var/lib/hoardarr/networking")
    netplan: Path = Path("/etc/netplan/99-hoardarr.yaml")
    timesyncd: Path = Path("/etc/systemd/timesyncd.conf.d/60-hoardarr.conf")
    rsyslog: Path = Path("/etc/rsyslog.d/60-hoardarr-remote.conf")
    snmpd: Path = Path("/etc/snmp/snmpd.conf")
    nftables_main: Path = Path("/etc/nftables.conf")
    nftables: Path = Path("/etc/nftables.d/60-hoardarr.nft")
    lldpd: Path = Path("/etc/lldpd.d/60-hoardarr.conf")
    lldpd_service: Path = Path("/etc/systemd/system/lldpd.service.d/70-hoardarr-network.conf")
    fcoe_config_dir: Path = Path("/etc/fcoe")

    @property
    def state(self) -> Path:
        return self.state_root / "configuration.json"

    @property
    def pending(self) -> Path:
        return self.state_root / "pending.json"

    @property
    def rollback_root(self) -> Path:
        return self.state_root / "rollback"


DEFAULT_PATHS = Paths()


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(
    command: Sequence[str],
    *,
    runner: Runner = subprocess.run,
    check: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NetworkFailure("network_command_failed", f"{command[0]} could not run.") from exc
    if check and result.returncode != 0:
        raise NetworkFailure("network_command_failed", f"{Path(command[0]).name} failed.")
    return result


def _command(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise NetworkFailure("network_tool_missing", f"{name} is not installed.")
    return path


def _atomic_write(path: Path, content: str, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def capabilities() -> dict[str, Any]:
    names = (
        "hostnamectl",
        "ip",
        "lldpcli",
        "netplan",
        "nft",
        "rsyslogd",
        "snmpd",
        "systemctl",
        "systemd-run",
        "timedatectl",
        "unshare",
    )
    tools = {name: shutil.which(name) is not None for name in names}
    return {"available": all(tools.values()), "tools": tools}


def _selected_interfaces(
    configuration: ManagedNetworkRequest,
    sysfs_root: Path | None = None,
) -> list[dict[str, Any]]:
    inventory = (
        {
            str(item["id"]): item
            for item in discover_network_interfaces(sysfs_root)
            if sysfs_root is not None
        }
        if sysfs_root is not None
        else {str(item["id"]): item for item in discover_network_interfaces()}
    )
    requested = configuration.host.network.interface_ids
    missing = [name for name in requested if name not in inventory]
    if missing:
        raise NetworkFailure("network_interface_changed", "A selected network port is unavailable.")
    return [inventory[name] for name in requested]


def _changed_components(values: Sequence[str] | None) -> tuple[str, ...]:
    if values is None:
        return NETWORK_COMPONENTS
    invalid = sorted(set(values) - set(NETWORK_COMPONENTS))
    if invalid:
        raise NetworkFailure(
            "network_component_invalid", "An unknown networking section was selected."
        )
    return tuple(component for component in NETWORK_COMPONENTS if component in values)


def _component_value(configuration: Mapping[str, Any], component: str) -> Any:
    if component in {"server", "network", "ntp", "discovery"}:
        return configuration.get("host", {}).get(component)
    return configuration.get(component)


def _load_saved_configuration(paths: Paths) -> dict[str, Any] | None:
    if not paths.state.exists():
        return None
    try:
        value = json.loads(paths.state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _configuration_with_preserved_secrets(
    configuration: ManagedNetworkRequest,
    paths: Paths,
) -> ManagedNetworkRequest:
    current = configuration.model_dump(mode="json")
    snmp = current.get("snmp", {})
    traps = current.get("traps", {})
    needs_previous = snmp.get("community") == REDACTED_SECRET or any(
        item.get("community") == REDACTED_SECRET
        for item in traps.get("destinations", [])
        if isinstance(item, dict)
    )
    if not needs_previous:
        return configuration
    previous = _load_saved_configuration(paths)
    if previous is None:
        raise NetworkFailure(
            "network_secret_missing",
            "A saved network credential is no longer available; enter it again.",
        )
    previous_snmp = previous.get("snmp", {})
    if snmp.get("community") == REDACTED_SECRET:
        saved = previous_snmp.get("community") if isinstance(previous_snmp, dict) else None
        if not isinstance(saved, str) or not saved:
            raise NetworkFailure(
                "network_secret_missing",
                "The saved SNMP credential is no longer available; enter it again.",
            )
        snmp["community"] = saved
    previous_destinations = {
        (item.get("server"), item.get("port")): item.get("community")
        for item in previous.get("traps", {}).get("destinations", [])
        if isinstance(item, dict)
    }
    for destination in traps.get("destinations", []):
        if not isinstance(destination, dict) or destination.get("community") != REDACTED_SECRET:
            continue
        saved = previous_destinations.get((destination.get("server"), destination.get("port")))
        if not isinstance(saved, str) or not saved:
            raise NetworkFailure(
                "network_secret_missing",
                "A saved SNMP trap credential is no longer available; enter it again.",
            )
        destination["community"] = saved
    return ManagedNetworkRequest.model_validate(current)


def _redacted_configuration(configuration: Mapping[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps(configuration))
    snmp = value.get("snmp", {})
    if isinstance(snmp, dict) and snmp.get("community"):
        snmp["community"] = REDACTED_SECRET
    traps = value.get("traps", {})
    if isinstance(traps, dict):
        for destination in traps.get("destinations", []):
            if isinstance(destination, dict) and destination.get("community"):
                destination["community"] = REDACTED_SECRET
    return value


def _secret_binding(configuration: Mapping[str, Any]) -> str:
    snmp = configuration.get("snmp", {})
    traps = configuration.get("traps", {})
    secret_values = {
        "snmp": snmp.get("community") if isinstance(snmp, dict) else None,
        "traps": [
            {
                "server": item.get("server"),
                "port": item.get("port"),
                "community": item.get("community"),
            }
            for item in traps.get("destinations", [])
            if isinstance(item, dict)
        ]
        if isinstance(traps, dict)
        else [],
    }
    encoded = json.dumps(secret_values, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _effective_components(
    configuration: ManagedNetworkRequest,
    requested: Sequence[str],
    paths: Paths,
) -> tuple[str, ...]:
    if not paths.state.exists():
        return tuple(requested)
    previous = _load_saved_configuration(paths)
    if previous is None:
        return tuple(requested)
    current = configuration.model_dump(mode="json")
    return tuple(
        component
        for component in requested
        if _component_value(current, component) != _component_value(previous, component)
    )


def build_plan(
    configuration: ManagedNetworkRequest,
    changed_components: Sequence[str] | None = None,
    *,
    paths: Paths = DEFAULT_PATHS,
    network_sysfs_root: Path | None = None,
) -> dict[str, Any]:
    configuration = _configuration_with_preserved_secrets(configuration, paths)
    selected = (
        _selected_interfaces(configuration, network_sysfs_root)
        if network_sysfs_root is not None
        else _selected_interfaces(configuration)
    )
    support = capabilities()
    requested_components = _changed_components(changed_components)
    components = _effective_components(configuration, requested_components, paths)
    document: dict[str, Any] = {
        "schema_version": 1,
        "kind": "managed_network",
        "configuration": _redacted_configuration(configuration.model_dump(mode="json")),
        "secret_binding_sha256": _secret_binding(configuration.model_dump(mode="json")),
        "changed_components": list(components),
        "selected_interfaces": selected,
        "apply_available": support["available"] and bool(components),
        "warnings": [],
        "blockers": [],
        "rollback_timeout_seconds": 120,
    }
    if "network" in components and configuration.host.network.mode == "lacp":
        document["warnings"].append(
            {
                "code": "lacp_switch_configuration_required",
                "message": "The switch ports must be in the same LACP port channel.",
            }
        )
    if "network" in components and configuration.host.network.mode == "bridge":
        document["warnings"].append(
            {
                "code": "bridge_switch_stp_required",
                "message": "Spanning tree must also be enabled on the connected switch port.",
            }
        )
    if not support["available"]:
        document["blockers"].append(
            {
                "code": "network_tools_missing",
                "message": "Required networking tools are not installed.",
            }
        )
    if not components:
        document["blockers"].append(
            {
                "code": "network_no_changes",
                "message": "No settings have changed.",
            }
        )
    for protocol in ("ssh", "https") if "access_rules" in components else ():
        protocol_rules = [
            rule
            for rule in configuration.access_rules
            if rule.protocol == protocol and rule.destination == "this_server"
        ]
        if protocol_rules and not any(rule.action == "allow" for rule in protocol_rules):
            document["blockers"].append(
                {
                    "code": f"{protocol}_access_missing",
                    "message": f"Add an allow rule for {protocol.upper()} before applying.",
                }
            )
            document["apply_available"] = False
    return {"plan": document, "sha256": normalized_hash(document)}


def _addressing(configuration: ManagedNetworkRequest) -> dict[str, Any]:
    settings = configuration.host.network
    value: dict[str, Any] = {
        "dhcp4": settings.addressing == "dhcp",
        "dhcp6": False,
        "mtu": settings.mtu,
    }
    if settings.addressing == "static":
        value["addresses"] = settings.addresses
        if settings.gateway:
            value["routes"] = [{"to": "default", "via": settings.gateway}]
    if settings.dns_servers:
        value["nameservers"] = {"addresses": settings.dns_servers}
    return value


def render_netplan(configuration: ManagedNetworkRequest) -> str:
    settings = configuration.host.network
    physical = {
        name: {"dhcp4": False, "dhcp6": False, "mtu": settings.mtu}
        for name in settings.interface_ids
    }
    network: dict[str, Any] = {"version": 2, "renderer": "networkd", "ethernets": physical}
    endpoint: str
    if settings.mode == "single":
        endpoint = settings.interface_ids[0]
        network["ethernets"][endpoint] = _addressing(configuration)
    elif settings.mode in {"active_passive", "lacp"}:
        endpoint = "bond0"
        parameters: dict[str, Any] = {
            "mode": "active-backup" if settings.mode == "active_passive" else "802.3ad",
            "mii-monitor-interval": 100,
        }
        if settings.mode == "lacp":
            parameters["lacp-rate"] = "fast"
            parameters["transmit-hash-policy"] = "layer3+4"
        network["bonds"] = {
            endpoint: {
                "interfaces": settings.interface_ids,
                "parameters": parameters,
                **_addressing(configuration),
            }
        }
    else:
        endpoint = "br0"
        network["bridges"] = {
            endpoint: {
                "interfaces": settings.interface_ids,
                "parameters": {"stp": True, "forward-delay": 4},
                **_addressing(configuration),
            }
        }
    if settings.vlan_id is not None:
        address_settings = _addressing(configuration)
        if settings.mode == "single":
            network["ethernets"][endpoint] = physical[endpoint]
        else:
            container = "bonds" if settings.mode in {"active_passive", "lacp"} else "bridges"
            for key in ("dhcp4", "dhcp6", "addresses", "routes", "nameservers"):
                network[container][endpoint].pop(key, None)
        network["vlans"] = {
            f"vlan{settings.vlan_id}": {
                "id": settings.vlan_id,
                "link": endpoint,
                **address_settings,
            }
        }
    return json.dumps({"network": network}, indent=2) + "\n"


def _render_timesyncd(configuration: ManagedNetworkRequest) -> str:
    servers = " ".join(configuration.host.ntp.servers)
    return f"# Managed by Hoardarr.\n[Time]\nNTP={servers}\nFallbackNTP=\n"


def _render_rsyslog(configuration: ManagedNetworkRequest) -> str:
    settings = configuration.syslog
    if not settings.enabled:
        return "# Managed by Hoardarr. Remote logging is disabled.\n"
    prefix = "@" if settings.transport == "udp" else "@@"
    return f"# Managed by Hoardarr.\n*.* {prefix}{settings.server}:{settings.port}\n"


def _render_snmpd(configuration: ManagedNetworkRequest) -> str:
    settings = configuration.snmp
    if not settings.enabled:
        return "# Managed by Hoardarr. SNMP is disabled.\n"
    lines = [
        "# Managed by Hoardarr.",
        "agentAddress udp:161,udp6:[::]:161",
        f"sysName {configuration.host.server.hostname}",
    ]
    if settings.location:
        lines.append(f"sysLocation {settings.location}")
    if settings.contact:
        lines.append(f"sysContact {settings.contact}")
    lines.extend(
        f"rocommunity {settings.community} {manager}" for manager in settings.allowed_managers
    )
    if configuration.traps.enabled:
        lines.extend(
            f"trap2sink {item.server} {item.community} {item.port}"
            for item in configuration.traps.destinations
        )
    return "\n".join(lines) + "\n"


def _active_fcoe_interfaces(config_dir: Path) -> set[str]:
    interfaces: set[str] = set()
    for path in config_dir.glob("cfg-*"):
        interface = path.name.removeprefix("cfg-")
        if interface == "ethx" or not INTERFACE_RE.fullmatch(interface):
            continue
        with contextlib.suppress(OSError):
            if 'FCOE_ENABLE="yes"' in path.read_text(encoding="utf-8"):
                interfaces.add(interface)
    return interfaces


def _render_lldpd(
    configuration: ManagedNetworkRequest, fcoe_interfaces: set[str] | None = None
) -> tuple[str, str]:
    discovery = configuration.host.discovery
    excluded = fcoe_interfaces or set()
    selected = [
        interface
        for interface in configuration.host.network.interface_ids
        if interface not in excluded
    ]
    interfaces = ",".join(selected) if selected else "!*"
    status = (
        "disabled"
        if not discovery.lldp.enabled
        else "rx-only"
        if discovery.lldp.mode == "receive_only"
        else "rx-and-tx"
    )
    commands = (
        "# Managed by Hoardarr.\n"
        f"configure system interface pattern {interfaces}\n"
        f"configure ports {interfaces} lldp status {status}\n"
    )
    daemon_args = "-c" if discovery.cdp.receive else ""
    service = f'[Service]\nEnvironment="DAEMON_ARGS="\nEnvironment="DAEMON_ARGS={daemon_args}"\n'
    return commands, service


def _nft_endpoint_match(value: str, direction: str) -> str:
    if value in {"any", "this_server"}:
        return ""
    family = "ip6" if ":" in value else "ip"
    return f"{family} {direction} {value} "


def _nft_ports(ports: tuple[int, ...]) -> str:
    return str(ports[0]) if len(ports) == 1 else "{ " + ", ".join(map(str, ports)) + " }"


def _render_nftables(configuration: ManagedNetworkRequest) -> str:
    chain_rules: dict[str, dict[str, list[str]]] = {
        "input": {},
        "output": {},
        "forward": {},
    }
    fcoe_rules: list[str] = []
    for rule in configuration.access_rules:
        verdict = "accept" if rule.action == "allow" else "drop"
        if rule.protocol == "fcoe":
            fcoe_rules.append(f"ether type 0x8906 {verdict}")
            continue
        transport, ports = PROTOCOL_PORTS[rule.protocol]
        port_set = _nft_ports(ports)
        protocols = ("tcp", "udp") if transport == "both" else (transport,)
        if rule.destination == "this_server":
            chain = "input"
            endpoint_match = _nft_endpoint_match(rule.source, "saddr")
        elif rule.source == "this_server":
            chain = "output"
            endpoint_match = _nft_endpoint_match(rule.destination, "daddr")
        else:
            chain = "forward"
            endpoint_match = _nft_endpoint_match(rule.source, "saddr") + _nft_endpoint_match(
                rule.destination, "daddr"
            )
        for protocol in protocols:
            chain_rules[chain].setdefault(rule.protocol, []).append(
                f"        {endpoint_match}{protocol} dport {port_set} {verdict}"
            )
    rendered_chains: list[str] = []
    for chain, grouped_rules in chain_rules.items():
        rendered_chains.extend(
            [
                f"    chain {chain} {{",
                f"        type filter hook {chain} priority filter; policy accept;",
            ]
        )
        if chain == "input":
            rendered_chains.append("        iifname lo accept")
        elif chain == "output":
            rendered_chains.append("        oifname lo accept")
        rendered_chains.append("        ct state established,related accept")
        for protocol, rules in grouped_rules.items():
            rendered_chains.extend(rules)
            transport, ports = PROTOCOL_PORTS[protocol]
            for item in ("tcp", "udp") if transport == "both" else (transport,):
                rendered_chains.append(f"        {item} dport {_nft_ports(ports)} drop")
        rendered_chains.append("    }")
    lines = [
        "# Managed by Hoardarr.",
        "table inet hoardarr {",
        *rendered_chains,
        "}",
    ]
    if fcoe_rules:
        fcoe_rules.append("ether type 0x8906 drop")
        lines.extend(["table netdev hoardarr_fcoe {"])
        for index, interface in enumerate(configuration.host.network.interface_ids):
            lines.extend(
                [
                    f"    chain ingress_{index} {{",
                    (
                        f'        type filter hook ingress device "{interface}" '
                        "priority filter; policy accept;"
                    ),
                    *(f"        {rule}" for rule in fcoe_rules),
                    "    }",
                ]
            )
        lines.append("}")
    return "\n".join(lines) + "\n"


def _managed_files(paths: Paths) -> tuple[Path, ...]:
    return (
        paths.netplan,
        paths.timesyncd,
        paths.rsyslog,
        paths.snmpd,
        paths.nftables_main,
        paths.nftables,
        paths.lldpd,
        paths.lldpd_service,
    )


def _backup(paths: Paths, token: str) -> Path:
    root = paths.rollback_root / token
    root.mkdir(parents=True, mode=0o700)
    manifest: dict[str, Any] = {}
    for path in _managed_files(paths):
        if path.exists():
            manifest[str(path)] = {
                "exists": True,
                "mode": path.stat().st_mode & 0o777,
                "content": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
        else:
            manifest[str(path)] = {"exists": False}
    _atomic_write(root / "manifest.json", json.dumps(manifest, sort_keys=True), 0o600)
    return root


def _restore(paths: Paths, token: str) -> None:
    manifest_path = paths.rollback_root / token / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for raw_path, item in manifest.items():
        path = Path(raw_path)
        if item["exists"]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(item["content"]))
            os.chmod(path, int(item["mode"]))
        else:
            path.unlink(missing_ok=True)


def _ensure_nftables_include(paths: Paths) -> None:
    include = 'include "/etc/nftables.d/*.nft"'
    current = (
        paths.nftables_main.read_text(encoding="utf-8")
        if paths.nftables_main.exists()
        else "#!/usr/sbin/nft -f\n\nflush ruleset\n"
    )
    if include not in current.splitlines():
        _atomic_write(paths.nftables_main, current.rstrip() + f"\n\n{include}\n", 0o755)


def _write_configuration(
    configuration: ManagedNetworkRequest,
    paths: Paths,
    components: Sequence[str],
) -> None:
    if "network" in components:
        _atomic_write(paths.netplan, render_netplan(configuration), 0o600)
    if "ntp" in components:
        _atomic_write(paths.timesyncd, _render_timesyncd(configuration))
    if "syslog" in components:
        _atomic_write(paths.rsyslog, _render_rsyslog(configuration))
    if {"snmp", "traps"}.intersection(components):
        _atomic_write(paths.snmpd, _render_snmpd(configuration), 0o600)
    if "access_rules" in components:
        _atomic_write(paths.nftables, _render_nftables(configuration), 0o600)
        _ensure_nftables_include(paths)
    if "discovery" in components:
        lldpd, service = _render_lldpd(
            configuration, _active_fcoe_interfaces(paths.fcoe_config_dir)
        )
        _atomic_write(paths.lldpd, lldpd)
        _atomic_write(paths.lldpd_service, service)


def _validate_configuration(
    paths: Paths,
    components: Sequence[str],
    *,
    runner: Runner,
) -> None:
    if "network" in components:
        with tempfile.TemporaryDirectory(prefix="hoardarr-netplan-") as temporary:
            root = Path(temporary)
            staged = root / "etc/netplan/99-hoardarr.yaml"
            staged.parent.mkdir(parents=True)
            staged.write_bytes(paths.netplan.read_bytes())
            os.chmod(staged, 0o600)
            _run(
                [_command("netplan"), "generate", "--root-dir", str(root)],
                runner=runner,
            )
    if "syslog" in components:
        _run([_command("rsyslogd"), "-N1"], runner=runner)
    if {"snmp", "traps"}.intersection(components):
        _run(
            [_command("snmpd"), "-H", "-C", "-c", str(paths.snmpd)],
            runner=runner,
        )
    if "access_rules" in components:
        _run(
            [_command("unshare"), "--net", _command("nft"), "-c", "-f", str(paths.nftables)],
            runner=runner,
        )


def _host_properties(*, runner: Runner) -> dict[str, str]:
    hostname = _run([_command("hostnamectl"), "--static"], runner=runner).stdout.strip()
    timezone = _run(
        [_command("timedatectl"), "show", "--property=Timezone", "--value"],
        runner=runner,
    ).stdout.strip()
    return {"hostname": hostname, "timezone": timezone}


def _restore_host_properties(properties: Mapping[str, str], *, runner: Runner) -> None:
    hostname = properties.get("hostname")
    timezone = properties.get("timezone")
    if hostname:
        _run(
            [_command("hostnamectl"), "set-hostname", hostname],
            runner=runner,
            check=False,
        )
    if timezone:
        _run(
            [_command("timedatectl"), "set-timezone", timezone],
            runner=runner,
            check=False,
        )


def _service_states(*, runner: Runner) -> dict[str, dict[str, bool]]:
    states: dict[str, dict[str, bool]] = {}
    for service in MANAGED_SERVICES:
        active = (
            _run(
                [_command("systemctl"), "is-active", "--quiet", service],
                runner=runner,
                check=False,
            ).returncode
            == 0
        )
        enabled = (
            _run(
                [_command("systemctl"), "is-enabled", "--quiet", service],
                runner=runner,
                check=False,
            ).returncode
            == 0
        )
        states[service] = {"active": active, "enabled": enabled}
    return states


def _restore_service_states(
    states: Mapping[str, Mapping[str, bool]],
    components: Sequence[str],
    *,
    runner: Runner,
) -> None:
    if "discovery" in components:
        _run([_command("systemctl"), "daemon-reload"], runner=runner, check=False)
    for service in MANAGED_SERVICES:
        if not SERVICE_COMPONENTS[service].intersection(components):
            continue
        state = states.get(service, {})
        enable_action = "enable" if state.get("enabled") else "disable"
        active_action = "restart" if state.get("active") else "stop"
        _run(
            [_command("systemctl"), enable_action, service],
            runner=runner,
            check=False,
        )
        _run(
            [_command("systemctl"), active_action, service],
            runner=runner,
            check=False,
        )


def _active_ipv4_addresses(
    configuration: ManagedNetworkRequest, *, runner: Runner
) -> list[dict[str, str]]:
    """Capture globally routable IPv4 addresses that may carry confirmation.

    A managed address change can otherwise remove the address used by the
    browser before it can confirm the rollback-protected plan.  Only addresses
    already assigned to the selected endpoint are eligible for the bounded
    confirmation bridge.
    """

    endpoint_names = set(configuration.host.network.interface_ids)
    if configuration.host.network.mode in {"active_passive", "lacp"}:
        endpoint_names.add("bond0")
    result = _run(
        [_command("ip"), "-j", "address", "show"],
        runner=runner,
        check=False,
    )
    try:
        inventory = json.loads(result.stdout or "[]")
    except (TypeError, ValueError):
        return []
    captured: list[dict[str, str]] = []
    for interface in inventory if isinstance(inventory, list) else []:
        if not isinstance(interface, Mapping):
            continue
        name = interface.get("ifname")
        if (
            not isinstance(name, str)
            or name not in endpoint_names
            or not INTERFACE_RE.fullmatch(name)
        ):
            continue
        address_info = interface.get("addr_info")
        for address in address_info if isinstance(address_info, list) else []:
            if not isinstance(address, Mapping):
                continue
            local = address.get("local")
            prefix = address.get("prefixlen")
            if (
                address.get("family") != "inet"
                or address.get("scope") != "global"
                or not isinstance(local, str)
                or not isinstance(prefix, int)
                or not 0 <= prefix <= 32
            ):
                continue
            try:
                parsed = ipaddress.ip_address(local)
            except ValueError:
                continue
            if parsed.version != 4 or parsed.is_loopback or parsed.is_link_local:
                continue
            captured.append({"interface": name, "address": f"{parsed}/{prefix}"})
    return captured


def _retain_confirmation_addresses(
    pending: dict[str, Any], configuration: ManagedNetworkRequest, *, runner: Runner
) -> None:
    desired = {str(ipaddress.ip_interface(value)) for value in configuration.host.network.addresses}
    retained: list[dict[str, str]] = []
    for item in pending.get("previous_ipv4_addresses", []):
        if not isinstance(item, Mapping):
            continue
        interface = item.get("interface")
        address = item.get("address")
        if (
            not isinstance(interface, str)
            or INTERFACE_RE.fullmatch(interface) is None
            or not isinstance(address, str)
            or address in desired
        ):
            continue
        try:
            normalized = str(ipaddress.ip_interface(address))
        except ValueError:
            continue
        result = _run(
            [_command("ip"), "address", "add", normalized, "dev", interface],
            runner=runner,
            check=False,
        )
        if result.returncode == 0:
            retained.append({"interface": interface, "address": normalized})
    pending["confirmation_ipv4_addresses"] = retained


def _remove_confirmation_addresses(pending: Mapping[str, Any], *, runner: Runner) -> None:
    for item in pending.get("confirmation_ipv4_addresses", []):
        if not isinstance(item, Mapping):
            continue
        interface = item.get("interface")
        address = item.get("address")
        if not isinstance(interface, str) or INTERFACE_RE.fullmatch(interface) is None:
            continue
        if not isinstance(address, str):
            continue
        try:
            normalized = str(ipaddress.ip_interface(address))
        except ValueError:
            continue
        _run(
            [_command("ip"), "address", "del", normalized, "dev", interface],
            runner=runner,
            check=False,
        )


def _activate(
    configuration: ManagedNetworkRequest,
    paths: Paths,
    components: Sequence[str],
    *,
    runner: Runner,
) -> None:
    if "server" in components:
        _run(
            [_command("hostnamectl"), "set-hostname", configuration.host.server.hostname],
            runner=runner,
        )
        _run(
            [_command("timedatectl"), "set-timezone", configuration.host.server.timezone],
            runner=runner,
        )
    if "network" in components:
        _run([_command("netplan"), "apply"], runner=runner, timeout=180)
    if "ntp" in components:
        _run([_command("systemctl"), "restart", "systemd-timesyncd.service"], runner=runner)
    if "discovery" in components:
        _run([_command("systemctl"), "daemon-reload"], runner=runner)
        _run([_command("systemctl"), "restart", "lldpd.service"], runner=runner)
        _run([_command("lldpcli"), "-c", str(paths.lldpd)], runner=runner)
    if "syslog" in components:
        _run([_command("systemctl"), "restart", "rsyslog.service"], runner=runner)
    if {"snmp", "traps"}.intersection(components):
        if configuration.snmp.enabled:
            _run([_command("systemctl"), "enable", "--now", "snmpd.service"], runner=runner)
            _run([_command("systemctl"), "restart", "snmpd.service"], runner=runner)
        else:
            _run(
                [_command("systemctl"), "disable", "--now", "snmpd.service"],
                runner=runner,
                check=False,
            )
    if "access_rules" in components:
        _run(
            [_command("nft"), "delete", "table", "inet", "hoardarr"],
            runner=runner,
            check=False,
        )
        _run(
            [_command("nft"), "delete", "table", "netdev", "hoardarr_fcoe"],
            runner=runner,
            check=False,
        )
        _run([_command("nft"), "-f", str(paths.nftables)], runner=runner)
        _run([_command("systemctl"), "enable", "nftables.service"], runner=runner)


def apply(
    configuration: ManagedNetworkRequest,
    plan_sha256: str,
    changed_components: Sequence[str] | None = None,
    *,
    paths: Paths = DEFAULT_PATHS,
    runner: Runner = subprocess.run,
    activate: bool = True,
) -> dict[str, Any]:
    configuration = _configuration_with_preserved_secrets(configuration, paths)
    requested_components = _changed_components(changed_components)
    planned = build_plan(configuration, requested_components, paths=paths)
    components = tuple(planned["plan"]["changed_components"])
    if planned["sha256"] != plan_sha256:
        raise NetworkFailure("network_plan_changed", "The network plan changed. Review it again.")
    if not planned["plan"]["apply_available"]:
        raise NetworkFailure("network_unavailable", "Required networking tools are unavailable.")
    if paths.pending.exists():
        raise NetworkFailure(
            "network_confirmation_pending", "A network change is awaiting confirmation."
        )
    _selected_interfaces(configuration)
    token = uuid.uuid4().hex
    previous_host = _host_properties(runner=runner)
    previous_services = _service_states(runner=runner)
    previous_ipv4_addresses = (
        _active_ipv4_addresses(configuration, runner=runner) if "network" in components else []
    )
    _backup(paths, token)
    pending = {
        "token": token,
        "configuration": configuration.model_dump(mode="json"),
        "plan_sha256": plan_sha256,
        "changed_components": list(components),
        "previous_host": previous_host,
        "previous_services": previous_services,
        "previous_ipv4_addresses": previous_ipv4_addresses,
        "activation_state": "prepared",
    }
    _atomic_write(paths.pending, json.dumps(pending, sort_keys=True), 0o600)
    try:
        _write_configuration(configuration, paths, components)
        _validate_configuration(paths, components, runner=runner)
        unit = f"hoardarr-network-rollback-{token}"
        _run(
            [
                _command("systemd-run"),
                f"--unit={unit}",
                "--on-active=120",
                RUNTIME_PYTHON,
                "-m",
                "hoardarr.networking.executor",
                "rollback",
                "--token",
                token,
            ],
            runner=runner,
        )
        if activate:
            activate_pending(token, paths=paths, runner=runner)
    except Exception:
        with contextlib.suppress(Exception):
            if "network" in components:
                _remove_confirmation_addresses(pending, runner=runner)
            _restore(paths, token)
            if "server" in components:
                _restore_host_properties(pending["previous_host"], runner=runner)
            if "network" in components:
                _run([_command("netplan"), "apply"], runner=runner, check=False, timeout=180)
            _restore_service_states(pending["previous_services"], components, runner=runner)
        paths.pending.unlink(missing_ok=True)
        shutil.rmtree(paths.rollback_root / token, ignore_errors=True)
        raise
    return {
        "state": "pending_confirmation",
        "token": token,
        "confirm_within_seconds": 120,
        "changed_components": list(components),
    }


def activate_pending(
    token: str, *, paths: Paths = DEFAULT_PATHS, runner: Runner = subprocess.run
) -> dict[str, Any]:
    """Activate a prepared network change after its API response is deliverable.

    Applying an address synchronously inside the request can tear down the TCP
    connection before the browser receives its confirmation token.  The API
    prepares and validates the configuration first, returns the token, and then
    calls this bounded background step.  Direct executor callers retain the
    original synchronous behavior through ``apply(activate=True)``.
    """

    try:
        pending = json.loads(paths.pending.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NetworkFailure(
            "network_confirmation_missing", "No network change is awaiting activation."
        ) from exc
    if pending.get("token") != token:
        raise NetworkFailure(
            "network_confirmation_invalid", "The network activation token is invalid."
        )
    if pending.get("activation_state") == "active":
        return {"state": "active"}
    try:
        configuration = ManagedNetworkRequest.model_validate(pending["configuration"])
        components = _changed_components(pending.get("changed_components"))
    except (KeyError, TypeError, ValueError) as exc:
        raise NetworkFailure(
            "network_pending_invalid", "The pending network change is invalid."
        ) from exc
    try:
        _activate(configuration, paths, components, runner=runner)
        if "network" in components:
            _retain_confirmation_addresses(pending, configuration, runner=runner)
    except Exception:
        with contextlib.suppress(Exception):
            _restore(paths, token)
            if "server" in components:
                _restore_host_properties(pending.get("previous_host", {}), runner=runner)
            if "network" in components:
                _run([_command("netplan"), "apply"], runner=runner, check=False, timeout=180)
            _restore_service_states(pending.get("previous_services", {}), components, runner=runner)
        paths.pending.unlink(missing_ok=True)
        shutil.rmtree(paths.rollback_root / token, ignore_errors=True)
        raise
    pending["activation_state"] = "active"
    _atomic_write(paths.pending, json.dumps(pending, sort_keys=True), 0o600)
    return {"state": "active"}


def finalize_confirmation(
    token: str, *, paths: Paths = DEFAULT_PATHS, runner: Runner = subprocess.run
) -> dict[str, Any]:
    try:
        pending = json.loads(paths.pending.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NetworkFailure(
            "network_confirmation_missing", "No confirmed network change is awaiting cleanup."
        ) from exc
    if pending.get("token") != token:
        raise NetworkFailure(
            "network_confirmation_invalid", "The network confirmation token is invalid."
        )
    if pending.get("activation_state") != "confirmed":
        raise NetworkFailure(
            "network_confirmation_pending", "The network change has not been confirmed."
        )
    _remove_confirmation_addresses(pending, runner=runner)
    paths.pending.unlink(missing_ok=True)
    shutil.rmtree(paths.rollback_root / token, ignore_errors=True)
    return {"state": "active"}


def confirm(
    token: str,
    *,
    paths: Paths = DEFAULT_PATHS,
    runner: Runner = subprocess.run,
    finalize: bool = True,
) -> dict[str, Any]:
    try:
        pending = json.loads(paths.pending.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NetworkFailure(
            "network_confirmation_missing", "No network change is awaiting confirmation."
        ) from exc
    if pending.get("token") != token:
        raise NetworkFailure(
            "network_confirmation_invalid", "The network confirmation token is invalid."
        )
    if pending.get("activation_state") != "active":
        raise NetworkFailure(
            "network_activation_pending",
            "The network change is still activating. Retry confirmation shortly.",
        )
    unit = f"hoardarr-network-rollback-{token}"
    _run([_command("systemctl"), "stop", f"{unit}.timer"], runner=runner, check=False)
    _run([_command("systemctl"), "reset-failed", f"{unit}.service"], runner=runner, check=False)
    _atomic_write(paths.state, json.dumps(pending["configuration"], sort_keys=True), 0o600)
    pending["activation_state"] = "confirmed"
    _atomic_write(paths.pending, json.dumps(pending, sort_keys=True), 0o600)
    if finalize:
        finalize_confirmation(token, paths=paths, runner=runner)
    return {"state": "active"}


def rollback(token: str, *, paths: Paths = DEFAULT_PATHS, runner: Runner = subprocess.run) -> None:
    try:
        pending = json.loads(paths.pending.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if pending.get("token") != token:
        return
    components = _changed_components(pending.get("changed_components"))
    if "network" in components:
        _remove_confirmation_addresses(pending, runner=runner)
    _restore(paths, token)
    if "server" in components:
        _restore_host_properties(pending.get("previous_host", {}), runner=runner)
    if "network" in components:
        _run([_command("netplan"), "generate"], runner=runner, check=False)
        _run([_command("netplan"), "apply"], runner=runner, check=False, timeout=180)
    _restore_service_states(pending.get("previous_services", {}), components, runner=runner)
    paths.pending.unlink(missing_ok=True)
    shutil.rmtree(paths.rollback_root / token, ignore_errors=True)


def status(paths: Paths = DEFAULT_PATHS) -> dict[str, Any]:
    configuration = None
    if paths.state.exists():
        with contextlib.suppress(OSError, ValueError):
            loaded = json.loads(paths.state.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                configuration = _redacted_configuration(loaded)
    pending = False
    if paths.pending.exists():
        pending = True
    current: dict[str, Any] = {
        "hostname": socket.gethostname(),
        "timezone": "UTC",
        "addresses": {},
        "default_interface": None,
        "default_gateway": None,
    }
    timezone_file = Path("/etc/timezone")
    with contextlib.suppress(OSError):
        current["timezone"] = timezone_file.read_text(encoding="utf-8").strip()
    if shutil.which("ip"):
        with contextlib.suppress(NetworkFailure, ValueError):
            addresses = json.loads(_run([_command("ip"), "-j", "address", "show"]).stdout)
            current["addresses"] = {
                item["ifname"]: [
                    f"{address['local']}/{address['prefixlen']}"
                    for address in item.get("addr_info", [])
                    if address.get("scope") == "global"
                ]
                for item in addresses
            }
        with contextlib.suppress(NetworkFailure, ValueError):
            routes = json.loads(_run([_command("ip"), "-j", "route", "show", "default"]).stdout)
            if routes:
                current["default_interface"] = routes[0].get("dev")
                current["default_gateway"] = routes[0].get("gateway")
    return {
        "configuration": configuration,
        "pending_confirmation": pending,
        "capabilities": capabilities(),
        "interfaces": discover_network_interfaces(),
        "current": current,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    rollback_parser = subparsers.add_parser("rollback")
    rollback_parser.add_argument("--token", required=True)
    args = parser.parse_args(argv)
    if args.command == "rollback":
        rollback(args.token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
