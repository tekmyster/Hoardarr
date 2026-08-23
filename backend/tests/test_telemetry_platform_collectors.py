from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hoardarr.telemetry.platform_collectors import (
    MAX_PROVIDER_OUTPUT,
    LinuxStoragePlatformCollector,
    _command,
    parse_mdstat,
    parse_multipath_json,
    parse_ses_metrics,
    parse_zpool_list,
    parse_zpool_status_errors,
    read_sas_phys,
)


def test_provider_command_bounds_output_before_returning_it() -> None:
    assert _command(sys.executable, ["-c", "print('bounded')"]).strip() == "bounded"
    assert (
        _command(
            sys.executable,
            ["-c", f"import sys; sys.stdout.write('x' * {MAX_PROVIDER_OUTPUT + 1})"],
        )
        is None
    )


def test_zpool_parser_rejects_noise_and_preserves_reported_values() -> None:
    rows = parse_zpool_list("tank\t1000\t400\t600\t12%\tONLINE\ninvalid\n")
    assert rows == [
        {
            "name": "tank",
            "size": 1000,
            "allocated": 400,
            "free": 600,
            "fragmentation": 12.0,
            "health": "ONLINE",
        }
    ]
    with pytest.raises(ValueError, match="too large"):
        parse_zpool_list("x" * (4 * 1024 * 1024 + 1))


def test_mdstat_parser_reports_degraded_and_progress() -> None:
    arrays = parse_mdstat(
        """Personalities : [raid6]
md0 : active raid6 sda1[0] sdb1[1] sdc1[2]
 100 blocks super 1.2 [3/2] [UU_]
 [====>........] recovery = 25.5% finish=1.0min
"""
    )
    assert arrays[0]["state"] == "degraded"
    assert arrays[0]["active"] == 2
    assert arrays[0]["failed"] == 1
    assert arrays[0]["progress"] == 25.5


def test_multipath_parser_counts_paths_and_rejects_malformed_output() -> None:
    rows = parse_multipath_json(
        '{"maps":[{"uuid":"3600a","name":"data","selector":"service-time 0",'
        '"paths":[{"dm_st":"active"},{"dm_st":"failed"}]}]}'
    )
    assert rows[0]["active"] == 1
    assert rows[0]["failed"] == 1
    with pytest.raises(ValueError, match="valid JSON"):
        parse_multipath_json("not-json")


def test_multipath_parser_reports_one_active_path_group_identity() -> None:
    rows = parse_multipath_json(
        '{"maps":[{"uuid":"3600a","path_groups":['
        '{"id":"group-a","status":"active","paths":[{"dm_st":"ready"}]},'
        '{"id":"group-b","status":"disabled","paths":[{"dm_st":"failed"}]}]}]}'
    )
    assert rows[0]["active_group"] == "group-a"
    assert rows[0]["active"] == 1
    assert rows[0]["failed"] == 1


def test_zpool_error_parser_uses_only_pool_summary_counters() -> None:
    values = parse_zpool_status_errors(
        """  pool: tank
 state: ONLINE
config:

        NAME        STATE     READ WRITE CKSUM
        tank        ONLINE       2     3     4
          sda       ONLINE       9     9     9

errors: No known data errors
"""
    )
    assert values == {"tank": {"read": 2, "write": 3, "checksum": 4}}


def test_sas_transport_and_ses_parsers_preserve_reported_values(tmp_path: Path) -> None:
    phy = tmp_path / "class/sas_phy/phy-6-0"
    phy.mkdir(parents=True)
    for name, value in {
        "sas_address": "0x5000c500abcd",
        "phy_identifier": "2",
        "invalid_dword_count": "11",
        "running_disparity_error_count": "7",
        "loss_of_dword_sync_count": "3",
        "phy_reset_problem_count": "1",
        "negotiated_linkrate": "12.0 Gbit",
        "maximum_linkrate_hw": "12.0 Gbit",
    }.items():
        (phy / name).write_text(value, encoding="utf-8")
    rows = read_sas_phys(tmp_path)
    assert rows[0]["invalid_dwords"] == 11
    assert rows[0]["disparity_errors"] == 7
    assert rows[0]["loss_of_sync"] == 3
    assert rows[0]["reset_problems"] == 1

    ses = parse_ses_metrics(
        '{"status":"OK","enclosure_descriptor":"shelf-1",'
        '"enclosure_logical_identifier":"0x5000c50012345678","elements":['
        '{"element_type":"Temperature sensor","temperature_c":42},'
        '{"element_type":"Cooling","speed_rpm":8200},'
        '{"element_type":"Power supply","status":"OK"},'
        '{"element_type":"Voltage sensor","voltage_v":12.1},'
        '{"element_type":"SAS expander","status":"OK"},'
        '{"element_type":"Array device slot","slot":1,"identify":true,"fault":false}]}'
    )
    assert ses["temperature_c"] == 42
    assert ses["fan_rpm"] == 8200
    assert ses["psu_states"] == ["healthy"]
    assert ses["locate"] is True
    assert ses["id"] == "0x5000c50012345678"
    descriptor_only = parse_ses_metrics(
        '{"status":"OK","enclosure_descriptor":"duplicate-name","elements":[]}'
    )
    assert descriptor_only["id"] is None
    assert descriptor_only["descriptor"] == "duplicate-name"
    with pytest.raises(ValueError, match="elements"):
        parse_ses_metrics("{}")


def test_fc_sysfs_collector_uses_wwpn_and_reset_safe_rates(tmp_path: Path) -> None:
    host = tmp_path / "class/fc_host/host6"
    stats = host / "statistics"
    stats.mkdir(parents=True)
    for name, value in {
        "port_name": "0x10000090fa123456",
        "node_name": "0x20000090fa123456",
        "port_state": "Online",
        "speed": "16 Gbit",
    }.items():
        (host / name).write_text(value, encoding="utf-8")
    for name, value in {
        "rx_words": "100",
        "tx_words": "200",
        "invalid_crc_count": "2",
        "link_failure_count": "1",
        "loss_of_signal_count": "0",
        "loss_of_sync_count": "0",
    }.items():
        (stats / name).write_text(value, encoding="utf-8")
    collector = LinuxStoragePlatformCollector(sys_root=tmp_path, proc_root=tmp_path)
    readings = collector._fc(datetime.now(UTC))
    by_id = {item.metric_id: item for item in readings}
    assert by_id["network.link.speed"].value == 16_000_000_000
    assert by_id["health.overall"].value == "online"
    assert by_id["network.receive.bytes_per_second"].quality == "not_reported"
    assert by_id["network.errors"].value == 2
