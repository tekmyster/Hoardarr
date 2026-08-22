from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from hoardarr.api.app import create_app
from hoardarr.auth.service import issue_setup_token
from hoardarr.core.config import Settings
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.system.network import discover_network_interfaces


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _network_fixture(root: Path) -> None:
    interface = root / "class" / "net" / "ens1f0"
    _write(interface / "address", "00:11:22:33:44:55\n")
    _write(interface / "mtu", "1500\n")
    _write(interface / "operstate", "up\n")
    _write(interface / "carrier", "1\n")
    _write(interface / "speed", "40000\n")
    _write(interface / "device" / "driver", "i40e\n")
    _write(interface / "device" / "model", "Intel Ethernet Controller XL710\n")
    _write(
        interface / "device" / "uevent",
        "PCI_ID=8086:1583\nPCI_SLOT_NAME=0000:18:00.0\n",
    )
    _write(interface / "phys_port_name", "p0\n")


def _runtime(tmp_path: Path) -> tuple[TestClient, Any, str]:
    database = tmp_path / "onboarding.db"
    sysfs = tmp_path / "sys"
    frontend = tmp_path / "frontend"
    _network_fixture(sysfs)
    _write(frontend / "index.html", "<!doctype html><title>Hoardarr</title>\n")
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{database.as_posix()}",
        secret_key_file=tmp_path / "secret.key",
        secure_cookies=False,
        frontend_dir=frontend,
        network_sysfs_root=sysfs,
    )
    upgrade_database(settings.database_url)
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        setup_token = issue_setup_token(session)
    app = create_app(settings)
    return TestClient(app, base_url="http://testserver"), app, setup_token


def _claim(client: TestClient, token: str) -> str:
    response = client.post(
        "/api/v1/setup/claim",
        headers={"Origin": "http://testserver"},
        json={
            "token": token,
            "username": "owner",
            "password": "a-long-unique-test-password",
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["csrf_token"])


def _payload(**network_changes: Any) -> dict[str, Any]:
    network: dict[str, Any] = {
        "mode": "single",
        "interface_ids": ["ens1f0"],
        "addressing": "dhcp",
        "mtu": 1500,
        "bridge": {"enabled": False, "stp": True, "prefer_rstp": True},
    }
    network.update(network_changes)
    return {
        "experience": "guided",
        "server": {
            "hostname": "hoardarr",
            "timezone": "America/New_York",
            "dst_mode": "automatic",
        },
        "network": network,
        "ntp": {"servers": ["pool.ntp.org"]},
        "discovery": {
            "lldp": {"enabled": True, "mode": "rx_tx"},
            "cdp": {"receive": True, "smart_transmit": True},
        },
    }


def test_network_inventory_is_predictable_and_flags_intel_lldp(tmp_path: Path) -> None:
    root = tmp_path / "sys"
    _network_fixture(root)
    interfaces = discover_network_interfaces(root)
    assert [item["id"] for item in interfaces] == ["ens1f0"]
    assert interfaces[0]["mac_address"] == "00:11:22:33:44:55"
    assert interfaces[0]["driver"] == "i40e"
    assert interfaces[0]["speed_mbps"] == 40000
    assert interfaces[0]["model"] == "Intel Ethernet Controller XL710"
    assert interfaces[0]["device_address"] == "0000:18:00.0"
    assert interfaces[0]["pci_id"] == "8086:1583"
    assert interfaces[0]["fact_sources"] == {
        "speed_mbps": "sysfs:class/net/speed",
        "model": "sysfs:device/model",
    }
    assert interfaces[0]["unknown_fields"] == []
    assert interfaces[0]["lldp"]["firmware_ownership"] == "verify_before_transmit"
    assert "X710/E810" in interfaces[0]["warnings"][0]


def test_network_inventory_uses_udev_model_and_marks_unknown_facts(tmp_path: Path) -> None:
    root = tmp_path / "sys"
    udev = tmp_path / "udev"
    interface = root / "class" / "net" / "ens2f0"
    _write(interface / "address", "00:11:22:33:44:77\n")
    _write(interface / "mtu", "1500\n")
    _write(interface / "operstate", "down\n")
    _write(interface / "carrier", "0\n")
    _write(interface / "ifindex", "7\n")
    _write(interface / "device" / "driver", "ixgbe\n")
    _write(
        interface / "device" / "uevent",
        "PCI_ID=8086:10FB\nPCI_SLOT_NAME=0000:19:00.0\n",
    )
    _write(
        udev / "n7",
        "E:ID_MODEL_FROM_DATABASE=82599ES 10-Gigabit SFI/SFP+ Network Connection\n",
    )

    inventory = discover_network_interfaces(root, udev)

    assert inventory[0]["model"] == "82599ES 10-Gigabit SFI/SFP+ Network Connection"
    assert inventory[0]["fact_sources"]["model"] == "udev:net/ID_MODEL_FROM_DATABASE"
    assert inventory[0]["speed_mbps"] is None
    assert inventory[0]["unknown_fields"] == ["speed_mbps"]


def test_network_inventory_does_not_guess_unavailable_model_or_speed(tmp_path: Path) -> None:
    root = tmp_path / "sys"
    interface = root / "class" / "net" / "enp1s0"
    _write(interface / "address", "00:11:22:33:44:88\n")
    _write(interface / "speed", "-1\n")
    _write(interface / "device" / "driver", "virtio_net\n")

    inventory = discover_network_interfaces(root, tmp_path / "missing-udev")

    assert inventory[0]["model"] is None
    assert inventory[0]["speed_mbps"] is None
    assert inventory[0]["fact_sources"] == {"speed_mbps": None, "model": None}
    assert inventory[0]["unknown_fields"] == ["speed_mbps", "model"]


def test_guided_network_plan_uses_managed_executor(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hoardarr.networking.executor.capabilities",
        lambda: {"available": True, "tools": {}},
    )
    client, _app, token = _runtime(tmp_path)
    with client:
        csrf = _claim(client, token)
        definition = client.get("/api/v1/onboarding")
        assert definition.status_code == 200
        assert definition.json()["steps"][-1] == "storage_discovery"
        assert definition.json()["defaults"] == {
            "experience": "guided",
            "server": {
                "hostname": "hoardarr",
                "timezone": "UTC",
                "dst_mode": "automatic",
            },
            "network": {
                "mode": "single",
                "interface_ids": [],
                "addressing": "dhcp",
                "addresses": [],
                "gateway": None,
                "dns_servers": [],
                "vlan_id": None,
                "mtu": 1500,
                "bridge": {"enabled": False, "stp": True, "prefer_rstp": True},
            },
            "ntp": {"servers": ["pool.ntp.org"]},
            "discovery": {
                "lldp": {"enabled": True, "mode": "rx_tx"},
                "cdp": {"receive": True, "smart_transmit": True},
            },
        }
        home = client.get("/")
        assert home.status_code == 200
        assert "Hoardarr" in home.text
        assert "frame-ancestors 'none'" in home.headers["content-security-policy"]
        discovery = client.get("/api/v1/onboarding/network/interfaces")
        assert discovery.status_code == 200
        assert discovery.json()["items"][0]["id"] == "ens1f0"
        assert discovery.json()["items"][0]["speed_mbps"] == 40000
        assert discovery.json()["items"][0]["model"] == "Intel Ethernet Controller XL710"
        assert "null means unknown" in discovery.json()["field_semantics"]["model"]

        plan = client.post(
            "/api/v1/onboarding/network/plan",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
            json=_payload(),
        )
        assert plan.status_code == 200, plan.text
        document = plan.json()["plan"]
        assert document["configuration"]["host"]["server"] == {
            "hostname": "hoardarr",
            "timezone": "America/New_York",
            "dst_mode": "automatic",
        }
        assert document["derived"]["lldp_daemon"] == "lldpd"
        assert document["derived"]["cdp_policy"] == (
            "receive_and_transmit_after_neighbor_detection"
        )
        assert document["apply_available"] is True
        assert document["blockers"] == []
        assert document["changed_components"] == ["server", "network", "ntp", "discovery"]
        assert len(plan.json()["sha256"]) == 64


def test_guided_bridge_is_rejected_and_lacp_warns(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hoardarr.networking.executor.capabilities",
        lambda: {"available": True, "tools": {}},
    )
    client, _app, token = _runtime(tmp_path)
    with client:
        csrf = _claim(client, token)
        headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf}
        bridge = client.post(
            "/api/v1/onboarding/network/plan",
            headers=headers,
            json=_payload(
                mode="bridge",
                bridge={"enabled": True, "stp": True, "prefer_rstp": True},
            ),
        )
        assert bridge.status_code == 422

        # Two physical interfaces are required before a LACP plan can exist.
        second = client.app.state.settings.network_sysfs_root / "class" / "net" / "ens1f1"
        _write(second / "address", "00:11:22:33:44:66\n")
        _write(second / "mtu", "1500\n")
        _write(second / "operstate", "down\n")
        _write(second / "carrier", "0\n")
        _write(second / "device" / "driver", "ixgbe\n")
        lacp = client.post(
            "/api/v1/onboarding/network/plan",
            headers=headers,
            json=_payload(mode="lacp", interface_ids=["ens1f0", "ens1f1"]),
        )
        assert lacp.status_code == 200, lacp.text
        assert lacp.json()["plan"]["warnings"][0]["code"] == ("lacp_switch_configuration_required")


def test_network_plan_rejects_removed_interface(tmp_path: Path) -> None:
    client, _app, token = _runtime(tmp_path)
    with client:
        csrf = _claim(client, token)
        response = client.post(
            "/api/v1/onboarding/network/plan",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
            json=_payload(interface_ids=["eno999"]),
        )
        assert response.status_code == 409
        assert response.json()["code"] == "network_interface_changed"
