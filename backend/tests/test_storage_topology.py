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
                    "phy_id": "phy-6:2",
                    "phy_sas_address": "0x5000c500abcd",
                    "phy_identifier": "2",
                    "minimum_speed_gbps": 1.5,
                    "phy_invalid_dwords": 11,
                    "phy_disparity_errors": 7,
                    "phy_loss_of_sync": 3,
                    "phy_reset_problems": 1,
                    "expander_id": "expander-6:0",
                    "path_id": "end_device-6:0:12",
                    "path_components": ["host6", "port-6:0", "expander-6:0", "end_device-6:0:12"],
                    "slot": "12",
                    "mapping_source": "sysfs enclosure_device",
                    "mapping_confidence": "high",
                    "mapping_last_confirmed_at": "2026-08-23T12:00:00Z",
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
            "mapping_source": "sysfs enclosure_device",
            "mapping_confidence": "high",
            "mapping_last_confirmed_at": "2026-08-23T12:00:00Z",
        }
    ]
    drive = next(item for item in result["nodes"] if item["kind"] == "drive")
    assert drive["serial"] == "ZA123456"
    assert drive["capable_speed_gbps"] == 12.0
    assert drive["negotiated_speed_gbps"] == 6.0
    assert drive["mapping_confidence"] == "high"
    assert drive["mapping_source"] == "sysfs enclosure_device"
    assert {item["protocol"] for item in result["links"]} == {"SAS"}
    assert {item["kind"] for item in result["nodes"]} >= {
        "controller",
        "port",
        "phy",
        "expander",
        "path",
        "enclosure",
        "drive",
    }
    phy = next(item for item in result["nodes"] if item["kind"] == "phy")
    assert phy["sas_address"] == "0x5000c500abcd"
    assert phy["invalid_dwords"] == 11
    assert phy["disparity_errors"] == 7
    assert phy["loss_of_sync"] == 3
    assert phy["reset_problems"] == 1
    assert any(
        item["source"].startswith("port:") and item["target"].startswith("phy:")
        for item in result["links"]
    )
    assert any(
        item["source"].startswith("phy:") and item["target"].startswith("expander:")
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
