from __future__ import annotations

from hoardarr.storage.topology import add_logical_topology, build_storage_topology


def test_sas_shelf_topology_keeps_bays_speeds_and_drive_identity() -> None:
    hardware = {
        "controllers": [
            {
                "address": "0000:01:00.0",
                "bus_type": "pci",
                "kernel_driver": "mpt3sas",
                "provider": {"name": "Broadcom SAS HBA"},
            }
        ],
        "disks": [
            {
                "id": "wwn:5000c50012345678",
                "kernel_path": "/dev/sdb",
                "vendor": "SEAGATE",
                "model": "ST8000NM",
                "capacity_bytes": 8_000_000_000_000,
                "identity": {"serial": "ZA123456"},
                "connection": {
                    "controller_address": "0000:01:00.0",
                    "transport": "sas",
                    "protocol": "sas",
                    "enclosure_id": "6:0:0:0",
                    "enclosure_vendor": "NETAPP",
                    "enclosure_model": "DS4246",
                    "enclosure_status": "OK",
                    "hba_port": "port-6:0",
                    "expander_id": "expander-6:0",
                    "path_id": "end_device-6:0:12",
                    "path_components": ["host6", "port-6:0", "expander-6:0", "end_device-6:0:12"],
                    "slot": "12",
                    "capable_speed_gbps": 12.0,
                    "negotiated_speed_gbps": 6.0,
                },
            }
        ],
    }

    result = build_storage_topology(hardware, include_live_state=False)

    assert result["status"] == "available"
    assert result["enclosures"][0]["label"] == "NETAPP DS4246"
    assert result["enclosures"][0]["bays"] == [
        {
            "slot": "12",
            "drive_id": "drive:wwn:5000c50012345678",
            "status": "OK",
            "locate": None,
            "fault": None,
        }
    ]
    drive = next(item for item in result["nodes"] if item["kind"] == "drive")
    assert drive["serial"] == "ZA123456"
    assert drive["capable_speed_gbps"] == 12.0
    assert drive["negotiated_speed_gbps"] == 6.0
    assert {item["protocol"] for item in result["links"]} == {"SAS"}
    assert {item["kind"] for item in result["nodes"]} >= {
        "controller",
        "port",
        "expander",
        "path",
        "enclosure",
        "drive",
    }
    assert any(
        item["source"].startswith("port:") and item["target"].startswith("expander:")
        for item in result["links"]
    )


def test_fcoe_transport_host_is_the_visible_controller() -> None:
    hardware = {
        "transport_hosts": [
            {
                "address": "host7",
                "bus_type": "scsi_host",
                "kernel_driver": "bnx2fc",
                "attributes": {"speed": "10 Gbit", "supported_speeds": "10 Gbit"},
            }
        ],
        "disks": [
            {
                "id": "wwn:5000fcoe",
                "kernel_path": "/dev/sdc",
                "model": "SAN LUN",
                "identity": {"serial": "FCOE001"},
                "connection": {
                    "transport_host": "host7",
                    "transport": "scsi",
                    "protocol": "scsi",
                },
            }
        ],
    }

    result = build_storage_topology(hardware, include_live_state=False)

    controller = next(item for item in result["nodes"] if item["kind"] == "controller")
    assert controller["address"] == "host7"
    assert controller["protocol"] == "FCoE"
    assert controller["negotiated_speed_gbps"] == 10.0
    assert result["links"][0]["protocol"] == "FCoE"


def test_logical_topology_connects_reported_pool_filesystem_and_share() -> None:
    physical = build_storage_topology(
        {
            "disks": [
                {
                    "id": "wwn:5000c50012345678",
                    "kernel_path": "/dev/sdb",
                    "model": "DATA",
                    "identity": {"serial": "SERIAL1"},
                    "connection": {"protocol": "sas"},
                }
            ]
        },
        include_live_state=False,
    )
    result = add_logical_topology(
        physical,
        [
            {
                "id": "zfs:tank",
                "name": "tank",
                "type": "ZFS",
                "status": "mounted",
                "mountpoint": "/mnt/hoardarr/tank",
                "device_names": ["sdb"],
                "total_bytes": 1000,
                "used_bytes": 250,
                "degraded": False,
            }
        ],
        [
            {
                "id": "smb:media",
                "name": "Media",
                "protocol": "SMB",
                "path": "/mnt/hoardarr/tank/Media",
            }
        ],
    )

    kinds = {item["kind"] for item in result["nodes"]}
    assert {"controller", "drive", "pool", "filesystem", "share"} <= kinds
    edges = {(item["source"], item["target"]) for item in result["links"]}
    assert ("drive:wwn:5000c50012345678", "pool:zfs:tank") in edges
    assert ("pool:zfs:tank", "filesystem:zfs:tank") in edges
    assert ("filesystem:zfs:tank", "share:smb:media") in edges


def test_logical_topology_does_not_guess_a_share_parent() -> None:
    result = add_logical_topology(
        {
            "status": "not_available",
            "nodes": [],
            "links": [],
            "enclosures": [],
            "direct_attached_drive_ids": [],
        },
        [],
        [{"id": "smb:outside", "name": "Outside", "protocol": "SMB", "path": "/srv/outside"}],
    )
    assert result["status"] == "available"
    assert result["links"] == []
