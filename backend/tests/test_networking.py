from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hoardarr.api.networking_schemas import ManagedNetworkRequest
from hoardarr.networking import executor


def configuration(**overrides) -> ManagedNetworkRequest:  # type: ignore[no-untyped-def]
    value = {
        "host": {
            "experience": "guided",
            "server": {"hostname": "hoardarr", "timezone": "UTC"},
            "network": {
                "mode": "single",
                "interface_ids": ["enp5s0f0"],
                "addressing": "static",
                "addresses": ["10.81.200.20/24"],
                "gateway": "10.81.200.1",
                "dns_servers": ["10.81.200.1"],
                "mtu": 9000,
            },
            "ntp": {"servers": ["time.cloudflare.com"]},
            "discovery": {
                "lldp": {"enabled": True, "mode": "rx_tx"},
                "cdp": {"receive": True, "smart_transmit": True},
            },
        },
        "syslog": {"enabled": True, "server": "10.81.200.5", "transport": "tcp"},
        "snmp": {
            "enabled": True,
            "community": "homelab",
            "allowed_managers": ["10.81.0.0/16"],
            "location": "Basement",
        },
        "traps": {
            "enabled": True,
            "destinations": [{"server": "10.81.200.5", "community": "homelab", "port": 162}],
        },
        "access_rules": [
            {
                "source": "10.81.0.0/16",
                "destination": "this_server",
                "protocol": "ssh",
                "action": "allow",
            }
        ],
    }
    value.update(overrides)
    return ManagedNetworkRequest.model_validate(value)


def test_renders_static_netplan() -> None:
    rendered = json.loads(executor.render_netplan(configuration()))

    interface = rendered["network"]["ethernets"]["enp5s0f0"]
    assert interface["addresses"] == ["10.81.200.20/24"]
    assert interface["routes"] == [{"to": "default", "via": "10.81.200.1"}]
    assert interface["mtu"] == 9000


def test_renders_lacp_bond() -> None:
    value = configuration().model_dump(mode="json")
    value["host"]["network"].update({"mode": "lacp", "interface_ids": ["enp5s0f0", "enp5s0f1"]})
    rendered = json.loads(executor.render_netplan(ManagedNetworkRequest.model_validate(value)))

    bond = rendered["network"]["bonds"]["bond0"]
    assert bond["interfaces"] == ["enp5s0f0", "enp5s0f1"]
    assert bond["parameters"]["mode"] == "802.3ad"
    assert bond["parameters"]["lacp-rate"] == "fast"


def test_renders_host_access_rules() -> None:
    rendered = executor._render_nftables(configuration())

    assert "ip saddr 10.81.0.0/16 tcp dport 22 accept" in rendered
    assert "tcp dport 22 drop" in rendered
    assert "iifname lo accept" in rendered
    assert "policy accept" in rendered


def test_apply_requires_confirmation_before_committing(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    paths = executor.Paths(
        state_root=tmp_path / "state",
        netplan=tmp_path / "etc/netplan/99-hoardarr.yaml",
        timesyncd=tmp_path / "etc/systemd/timesyncd.conf.d/60-hoardarr.conf",
        rsyslog=tmp_path / "etc/rsyslog.d/60-hoardarr.conf",
        snmpd=tmp_path / "etc/snmp/snmpd.conf",
        nftables_main=tmp_path / "etc/nftables.conf",
        nftables=tmp_path / "etc/nftables.d/60-hoardarr.nft",
        lldpd=tmp_path / "etc/lldpd.d/60-hoardarr.conf",
        lldpd_service=tmp_path / "etc/systemd/lldpd.conf",
    )
    commands: list[list[str]] = []

    def runner(command, **_kwargs):  # type: ignore[no-untyped-def]
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(executor, "_command", lambda name: name)
    monkeypatch.setattr(executor, "capabilities", lambda: {"available": True, "tools": {}})
    monkeypatch.setattr(
        executor,
        "_selected_interfaces",
        lambda _configuration: [{"id": "enp5s0f0", "mac_address": "00:11:22:33:44:55"}],
    )
    planned = executor.build_plan(configuration())

    result = executor.apply(configuration(), planned["sha256"], paths=paths, runner=runner)

    assert result["state"] == "pending_confirmation"
    assert paths.pending.exists()
    assert not paths.state.exists()
    assert any(command[0] == "systemd-run" for command in commands)

    executor.confirm(result["token"], paths=paths, runner=runner)

    assert paths.state.exists()
    assert not paths.pending.exists()


def test_ntp_only_apply_does_not_touch_network_or_other_services(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    paths = executor.Paths(
        state_root=tmp_path / "state",
        netplan=tmp_path / "etc/netplan/99-hoardarr.yaml",
        timesyncd=tmp_path / "etc/systemd/timesyncd.conf.d/60-hoardarr.conf",
        rsyslog=tmp_path / "etc/rsyslog.d/60-hoardarr.conf",
        snmpd=tmp_path / "etc/snmp/snmpd.conf",
        nftables_main=tmp_path / "etc/nftables.conf",
        nftables=tmp_path / "etc/nftables.d/60-hoardarr.nft",
        lldpd=tmp_path / "etc/lldpd.d/60-hoardarr.conf",
        lldpd_service=tmp_path / "etc/systemd/lldpd.conf",
    )
    commands: list[list[str]] = []

    def runner(command, **_kwargs):  # type: ignore[no-untyped-def]
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(executor, "_command", lambda name: name)
    monkeypatch.setattr(executor, "capabilities", lambda: {"available": True, "tools": {}})
    monkeypatch.setattr(
        executor,
        "_selected_interfaces",
        lambda _configuration: [{"id": "enp5s0f0", "mac_address": "00:11:22:33:44:55"}],
    )
    planned = executor.build_plan(configuration(), ["ntp"])

    result = executor.apply(
        configuration(),
        planned["sha256"],
        ["ntp"],
        paths=paths,
        runner=runner,
    )

    flattened = [" ".join(command) for command in commands]
    assert result["changed_components"] == ["ntp"]
    assert paths.timesyncd.exists()
    assert not paths.netplan.exists()
    assert not paths.rsyslog.exists()
    assert not paths.snmpd.exists()
    assert not paths.nftables.exists()
    assert not paths.lldpd.exists()
    assert not any(command.startswith("netplan ") for command in flattened)
    assert not any("restart lldpd.service" in command for command in flattened)
    assert not any("restart rsyslog.service" in command for command in flattened)
    assert not any("restart snmpd.service" in command for command in flattened)
    assert not any("disable --now snmpd.service" in command for command in flattened)
    assert not any(command.startswith("nft ") for command in flattened)
    assert any("restart systemd-timesyncd.service" in command for command in flattened)


def test_plan_omits_a_touched_component_when_its_value_did_not_change(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    paths = executor.Paths(state_root=tmp_path / "state")
    paths.state.parent.mkdir(parents=True)
    paths.state.write_text(configuration().model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(executor, "capabilities", lambda: {"available": True, "tools": {}})
    monkeypatch.setattr(
        executor,
        "_selected_interfaces",
        lambda _configuration: [{"id": "enp5s0f0", "mac_address": "00:11:22:33:44:55"}],
    )

    planned = executor.build_plan(configuration(), ["network"], paths=paths)

    assert planned["plan"]["changed_components"] == []
    assert planned["plan"]["apply_available"] is False
    assert planned["plan"]["blockers"] == [
        {"code": "network_no_changes", "message": "No settings have changed."}
    ]


def test_network_status_and_plan_never_return_snmp_secrets(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    paths = executor.Paths(state_root=tmp_path / "state")
    paths.state.parent.mkdir(parents=True)
    original = configuration()
    paths.state.write_text(original.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(executor, "capabilities", lambda: {"available": True, "tools": {}})
    monkeypatch.setattr(
        executor,
        "_selected_interfaces",
        lambda _configuration: [{"id": "enp5s0f0", "mac_address": "00:11:22:33:44:55"}],
    )

    public = executor.status(paths)
    assert public["configuration"]["snmp"]["community"] == executor.REDACTED_SECRET
    assert (
        public["configuration"]["traps"]["destinations"][0]["community"] == executor.REDACTED_SECRET
    )
    serialized = json.dumps(public)
    assert "homelab" not in serialized

    public_request = ManagedNetworkRequest.model_validate(public["configuration"])
    value = public_request.model_dump(mode="json")
    value["host"]["ntp"]["servers"] = ["time.nist.gov"]
    planned = executor.build_plan(ManagedNetworkRequest.model_validate(value), ["ntp"], paths=paths)
    assert "homelab" not in json.dumps(planned)
    assert planned["plan"]["configuration"]["snmp"]["community"] == executor.REDACTED_SECRET
    assert planned["plan"]["changed_components"] == ["ntp"]


def test_network_command_failures_do_not_echo_tool_output(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    secret = "community-that-must-not-leak"

    def runner(command, **_kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(command, 1, "", f"invalid community {secret}")

    with pytest.raises(executor.NetworkFailure) as failure:
        executor._run(["snmpd", "-H"], runner=runner)
    assert secret not in failure.value.message
