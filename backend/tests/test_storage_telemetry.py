from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from hoardarr.storage.telemetry import StorageTelemetrySampler, _parse_smart_endurance


def _counter(
    *,
    read_count: int,
    write_count: int,
    read_bytes: int,
    write_bytes: int,
    read_time: int,
    write_time: int,
    busy_time: int,
) -> SimpleNamespace:
    return SimpleNamespace(
        read_count=read_count,
        write_count=write_count,
        read_bytes=read_bytes,
        write_bytes=write_bytes,
        read_time=read_time,
        write_time=write_time,
        busy_time=busy_time,
    )


def test_nvme_endurance_uses_standard_data_units() -> None:
    result = _parse_smart_endurance(
        {"nvme_smart_health_information_log": {"data_units_written": 10, "percentage_used": 7}},
        4096,
    )
    assert result == {
        "lifetime_writes_bytes": 5_120_000,
        "remaining_percent": 93,
        "percentage_used": 7,
        "source": "NVMe SMART",
    }


def test_ata_endurance_uses_reported_lbas_and_life_left() -> None:
    result = _parse_smart_endurance(
        {
            "ata_smart_attributes": {
                "table": [
                    {"name": "Total_LBAs_Written", "raw": {"value": 100}},
                    {"name": "SSD_Life_Left", "value": 88, "raw": {"value": 12}},
                ]
            }
        },
        4096,
    )
    assert result["lifetime_writes_bytes"] == 409_600
    assert result["remaining_percent"] == 88


def test_sampler_reports_live_drive_and_pool_metrics(tmp_path: Path) -> None:
    now = [1_700_000_000.0]
    samples = [
        {
            "sda": _counter(
                read_count=1_000,
                write_count=1_000,
                read_bytes=1_000_000,
                write_bytes=2_000_000,
                read_time=2_000,
                write_time=3_000,
                busy_time=4_000,
            ),
            "sdb": _counter(
                read_count=10,
                write_count=20,
                read_bytes=1_000,
                write_bytes=2_000,
                read_time=20,
                write_time=60,
                busy_time=200,
            ),
        },
        {
            "sda": _counter(
                read_count=2_000,
                write_count=2_000,
                read_bytes=3_000_000,
                write_bytes=5_000_000,
                read_time=5_000,
                write_time=7_000,
                busy_time=8_000,
            ),
            "sdb": _counter(
                read_count=14,
                write_count=22,
                read_bytes=5_000,
                write_bytes=6_000,
                read_time=28,
                write_time=68,
                busy_time=1_200,
            ),
        },
    ]
    sampler = StorageTelemetrySampler(
        state_path=tmp_path / "telemetry.json",
        counters=lambda: samples.pop(0),
        clock=lambda: now[0],
        smart_reader=lambda _device, _sector: {
            "lifetime_writes_bytes": 10_000,
            "remaining_percent": 95,
            "source": "test",
        },
    )
    snapshot = {
        "disks": [
            {
                "id": "serial:system",
                "kernel_name": "sda",
                "kernel_path": "/dev/sda",
                "vendor": "System",
                "model": "Disk",
                "rotational": False,
                "system_disk": True,
                "identity": {"serial": "SYSTEM1"},
                "sector_sizes": {"logical_bytes": 4096},
            },
            {
                "id": "serial:ssd",
                "kernel_name": "sdb",
                "kernel_path": "/dev/sdb",
                "vendor": "Test",
                "model": "SSD",
                "rotational": False,
                "system_disk": False,
                "identity": {"serial": "SSD1"},
                "sector_sizes": {"logical_bytes": 4096},
            },
        ]
    }
    pools = [{"id": "md:md0", "name": "media", "type": "Linux MD", "device_names": ["sdb"]}]

    first = sampler.sample(hardware_snapshot=snapshot, pools=pools)
    assert first["summary"]["sample_seconds"] is None
    now[0] += 2
    second = sampler.sample(hardware_snapshot=snapshot, pools=pools)

    assert second["summary"]["read_bytes_per_second"] == 2_000
    assert second["summary"]["write_iops"] == 1
    assert second["drives"][0]["metrics"]["write_wait_ms"] == 4
    assert second["drives"][0]["writes_today_bytes"] == 4_000
    assert second["drives"][0]["endurance"]["remaining_percent"] == 95
    assert [drive["device"] for drive in second["drives"]] == ["/dev/sdb"]
    assert second["drives"][0]["pool_ids"] == ["md:md0"]
    assert second["pools"][0]["status"] == "available"
    assert second["pools"][0]["writes_today_bytes"] == 4_000
