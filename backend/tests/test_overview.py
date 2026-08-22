from __future__ import annotations

from hoardarr.system.overview import parse_lldpcli_neighbors, summarize_storage


def test_storage_summary_never_invents_missing_inventory() -> None:
    assert summarize_storage(None) == {
        "drive_count": None,
        "raw_capacity_bytes": None,
        "health": None,
    }
    assert summarize_storage({"platform": {"product": "unknown"}}) == {
        "drive_count": None,
        "raw_capacity_bytes": None,
        "health": None,
    }


def test_storage_summary_uses_only_reported_drive_values() -> None:
    summary = summarize_storage(
        {
            "disks": [
                {
                    "capacity_bytes": 100_000_000_000,
                    "health": {"status": "critical"},
                    "system_disk": True,
                },
                {"capacity_bytes": 4_000_000_000, "health": {"passed": True}},
                {"capacity_bytes": 8_000_000_000, "health": {"status": "warning"}},
                {"capacity_bytes": 8_000_000_000},
            ]
        }
    )
    assert summary == {
        "drive_count": 3,
        "raw_capacity_bytes": 20_000_000_000,
        "health": {"healthy": 1, "warning": 1, "critical": 0, "unknown": 1},
    }


def test_partial_capacity_is_reported_as_unavailable() -> None:
    summary = summarize_storage({"disks": [{"capacity_bytes": 4_000}, {}]})
    assert summary["drive_count"] == 2
    assert summary["raw_capacity_bytes"] is None


def test_lldp_and_cdp_neighbors_are_parsed_from_bounded_keyvalue_records() -> None:
    neighbors = parse_lldpcli_neighbors(
        "\n".join(
            (
                "lldp.eth0.via=LLDP",
                "lldp.eth0.rid=1",
                "lldp.eth0.age=0 day, 00:00:18",
                "lldp.eth0.chassis.mac=00:11:22:33:44:55",
                "lldp.eth0.chassis.name=core-9500",
                "lldp.eth0.chassis.mgmt-ip=10.81.200.1",
                "lldp.eth0.port.ifname=FortyGigabitEthernet1/0/1",
                "lldp.eth0.port.descr=Hoardarr storage host",
                "lldp.eth0.ttl.ttl=120",
                "lldp.eth1.via=CDPv2",
                "lldp.eth1.chassis.local=access-switch",
                "lldp.eth1.chassis.name=access-01",
                "lldp.eth1.port.ifname=GigabitEthernet1/0/24",
            )
        )
    )

    assert neighbors == [
        {
            "local_interface": "eth0",
            "protocol": "LLDP",
            "protocol_variant": "LLDP",
            "device_name": "core-9500",
            "chassis_id": "00:11:22:33:44:55",
            "port_id": "FortyGigabitEthernet1/0/1",
            "port_description": "Hoardarr storage host",
            "management_addresses": ["10.81.200.1"],
            "system_description": None,
            "age": "0 day, 00:00:18",
            "ttl_seconds": 120,
        },
        {
            "local_interface": "eth1",
            "protocol": "CDP",
            "protocol_variant": "CDPv2",
            "device_name": "access-01",
            "chassis_id": "access-switch",
            "port_id": "GigabitEthernet1/0/24",
            "port_description": None,
            "management_addresses": [],
            "system_description": None,
            "age": None,
            "ttl_seconds": None,
        },
    ]


def test_neighbor_parser_does_not_invent_records_without_a_protocol_marker() -> None:
    assert parse_lldpcli_neighbors("lldp.eth0.chassis.name=not-a-neighbor") == []
