from __future__ import annotations

from pathlib import Path

from hoardarr.storage import inventory
from hoardarr.storage.zfs import parse_zpool_data_topology


def test_live_inventory_reports_md_arrays_and_managed_shares(tmp_path: Path, monkeypatch) -> None:
    sys_block = tmp_path / "sys" / "class" / "block"
    md = sys_block / "md0" / "md"
    md.mkdir(parents=True)
    (md / "array_state").write_text("clean\n", encoding="utf-8")
    (md / "raid_disks").write_text("4\n", encoding="utf-8")
    (md / "degraded").write_text("0\n", encoding="utf-8")
    (md / "level").write_text("raid6\n", encoding="utf-8")
    samba = tmp_path / "hoardarr-shares.conf"
    samba.write_text("[media]\n    path = /data/media\n", encoding="utf-8")
    exports = tmp_path / "exports"
    exports.write_text("/data/media 10.0.0.0/8(ro)\n", encoding="utf-8")
    monkeypatch.setattr(inventory, "_command", lambda _name, _arguments: None)
    monkeypatch.setattr(
        inventory,
        "discover_mergerfs",
        lambda: {"available": True, "status": "available_not_configured", "items": []},
    )

    result = inventory.discover_storage_inventory(
        sys_class_block=sys_block,
        samba_config=samba,
        nfs_exports=exports,
        target_config=tmp_path / "targets.json",
    )

    assert result["pools"]["items"][0]["type"] == "Linux MD raid6"
    assert result["pools"]["items"][0]["status"] == "clean"
    assert [item["protocol"] for item in result["shares"]["items"]] == ["SMB", "NFS"]


def test_enclosure_health_collects_reported_ses_sensors_and_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    enclosure_root = tmp_path / "sys/class/enclosure"
    generic = enclosure_root / "enclosure0/device/scsi_generic/sg4"
    generic.mkdir(parents=True)
    monkeypatch.setattr(inventory.shutil, "which", lambda name, **_kwargs: f"/usr/bin/{name}")
    monkeypatch.setattr(
        inventory,
        "_command",
        lambda name, arguments: (
            '{"status":"OK","enclosure_descriptor":"DS424IOM6",'
            '"enclosure_logical_identifier":"0x500a098000000424","elements":['
            '{"element_type":"Temperature sensor","temperature_c":38},'
            '{"element_type":"Cooling","speed_rpm":7600},'
            '{"element_type":"Power supply","status":"OK"},'
            '{"element_type":"Voltage sensor","voltage_v":12.0},'
            '{"element_type":"SAS expander","status":"OK"},'
            '{"element_type":"Array device slot","slot":3,"status":"OK",'
            '"identify":true,"fault":false,"sas_address":"5000c50012345678"}]}'
            if name == "sg_ses"
            and arguments == ["--join", "--json", "--readonly", "/dev/sg4"]
            else None
        ),
    )

    result = inventory._enclosure_health(enclosure_root)

    assert result["status"] == "healthy"
    assert result["unavailable"] == []
    assert result["items"] == [
        {
            "id": "0x500a098000000424",
            "descriptor": "DS424IOM6",
            "health": "healthy",
            "slots": [
                {
                    "slot": 3,
                    "status": "healthy",
                    "identify": True,
                    "fault": False,
                    "sas_address": "5000c50012345678",
                    "attached_sas_address": "Not reported",
                    "mapping_source": "SES Additional Element Status SAS address",
                    "mapping_confidence": "high",
                }
            ],
            "temperature_c": 38.0,
            "fan_rpm": 7600,
            "fan_count": 1,
            "power_supplies": ["healthy"],
            "voltages": [12.0],
            "locate": True,
            "fault": False,
            "expanders": ["healthy"],
            "provider": "sg_ses",
            "path": "sg4",
        }
    ]

    monkeypatch.setattr(inventory, "_command", lambda _name, _arguments: "{malformed")
    failed = inventory._enclosure_health(enclosure_root)
    assert failed["items"] == []
    assert failed["unavailable"] == [
        {"provider": "sg_ses", "path": "sg4", "status": "Not reported"}
    ]


def test_snapraid_inventory_exposes_bounded_role_evidence(tmp_path: Path, monkeypatch) -> None:
    config_root = tmp_path / "snapraid"
    config_root.mkdir()
    config = config_root / "media.conf"
    config.write_text(
        "\n".join(
            (
                "parity /srv/hoardarr/backends/parity/snapraid.parity",
                "2-parity /srv/hoardarr/backends/parity2/snapraid.parity",
                "content /var/lib/hoardarr/snapraid/media.content",
                "content /srv/hoardarr/backends/data-a/snapraid.content",
                "data movies_a /srv/hoardarr/backends/data-a",
                "data movies_b /srv/hoardarr/backends/data-b",
                "exclude *.tmp",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        inventory,
        "_command",
        lambda name, _arguments: (
            "SnapRAID array\nEverything is OK\n" if name == "snapraid" else None
        ),
    )

    item = inventory._snapraid_arrays(config_root)[0]

    assert item["id"] == "snapraid:media"
    assert item["configuration"]["quality"] == "available"
    assert item["configuration"]["data_disks"] == [
        {"name": "movies_a", "path": "/srv/hoardarr/backends/data-a"},
        {"name": "movies_b", "path": "/srv/hoardarr/backends/data-b"},
    ]
    assert [entry["level"] for entry in item["configuration"]["parity_disks"]] == [1, 2]
    assert len(item["configuration"]["config_sha256"]) == 64
    assert "*.tmp" not in str(item["configuration"])


def test_snapraid_inventory_fails_closed_on_malformed_or_oversized_config(
    tmp_path: Path,
) -> None:
    config = tmp_path / "unsafe.conf"
    config.write_text(
        "parity relative/path\ndata duplicate /srv/a\ndata duplicate /srv/b\n",
        encoding="utf-8",
    )
    parsed = inventory._snapraid_configuration(config)
    assert parsed["quality"] == "temporarily_unavailable"
    assert parsed["errors"]

    config.write_bytes(b"x" * (1024 * 1024 + 1))
    oversized = inventory._snapraid_configuration(config)
    assert oversized["quality"] == "temporarily_unavailable"
    assert oversized["config_sha256"] is None


def test_zfs_inventory_exposes_guid_and_uniform_data_vdev_binding(monkeypatch) -> None:
    status = """
  pool: media
 state: ONLINE
  scan: none requested
config:

        NAME                                      STATE     READ WRITE CKSUM
        media                                     ONLINE       0     0     0
          mirror-0                                ONLINE       0     0     0
            /dev/disk/by-id/scsi-a                ONLINE       0     0     0
            /dev/disk/by-id/scsi-b                ONLINE       0     0     0

errors: No known data errors
"""
    topology = parse_zpool_data_topology(status, "media")

    def command(name: str, arguments: list[str]) -> str | None:
        if name == "zpool" and arguments[:2] == ["list", "-Hp"]:
            return "media\t20000000000\t1000000000\t19000000000\tONLINE\n"
        if name == "zpool" and arguments[:2] == ["status", "-P"]:
            return status
        if name == "zpool" and arguments[:2] == ["get", "-Hp"]:
            return "1234567890123456789\n"
        if name == "zfs":
            return "media\t/srv/hoardarr/media\n"
        return None

    monkeypatch.setattr(inventory, "_command", command)
    item = inventory._zfs_pools()[0]

    assert item["pool_guid"] == "1234567890123456789"
    assert item["configuration"] == {**topology.document(), "member_capacities": {}}
    assert item["configuration"]["vdev_type"] == "mirror"
    assert item["configuration"]["vdev_width"] == 2
    assert item["configuration"]["vdev_count"] == 1
    assert item["mountpoint"] == "/srv/hoardarr/media"
