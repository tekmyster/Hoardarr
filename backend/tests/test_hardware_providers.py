from __future__ import annotations

import json

import pytest

from hoardarr.hardware.providers import (
    NOT_REPORTED,
    ProviderError,
    aggregate_health,
    detect_providers,
    parse_arcconf,
    parse_areca,
    parse_mdadm_detail,
    parse_ses,
    parse_snapraid_status,
    parse_ssacli,
    parse_storcli,
    parse_zpool_status,
)


def test_provider_detection_covers_controller_pool_and_enclosure_families() -> None:
    values = detect_providers(["storcli", "mdadm", "zpool", "sg_ses"])
    assert {item["family"] for item in values if item["available"]} == {
        "lsi_avago_broadcom_dell",
        "linux_md",
        "zfs",
        "generic_ses",
    }
    assert all(item["command"] == NOT_REPORTED for item in values if not item["available"])


def test_storcli_parser_preserves_only_reported_slot_mapping() -> None:
    output = json.dumps(
        {
            "Controllers": [
                {
                    "Command Status": {"Status": "Success"},
                    "Response Data": {
                        "Basics": {"Controller": 0, "Model": "HBA 9500", "Serial Number": "ABC"},
                        "PD LIST": [
                            {
                                "EID:Slt": "32:4",
                                "State": "Onln",
                                "SN": "DISK1",
                                "Model": "SSD",
                                "Temp": 31,
                            },
                            {"State": "Onln", "SN": "DISK2"},
                        ],
                    },
                }
            ]
        }
    )
    result = parse_storcli(output)
    assert result["controllers"][0]["drives"][0]["slot"] == "4"
    assert result["controllers"][0]["drives"][1]["slot"] == NOT_REPORTED


def test_pool_parsers_report_degraded_rebuild_and_scrub() -> None:
    md = parse_mdadm_detail(
        "Raid Level : raid6\n"
        "State : clean, degraded, recovering\n"
        "Active Devices : 10\nWorking Devices : 11\n"
        "Rebuild Status : 47.5% complete"
    )
    assert md["degraded"] is True
    assert md["rebuild_percent"] == 47.5
    zfs = parse_zpool_status(
        "  pool: media\n state: DEGRADED\n  scan: resilver in progress, 22.3% done"
    )
    assert zfs["degraded"] is True
    assert zfs["scan_percent"] == 22.3
    assert aggregate_health([md, zfs])["health"] == "needs_attention"


def test_vendor_and_enclosure_parsers_keep_missing_values_explicit() -> None:
    hpe = parse_ssacli("Smart Array P408i-a in Slot 0\n   Controller Status: OK\n")
    assert hpe["controllers"][0]["health"] == "healthy"
    adaptec = parse_arcconf("Controller Model : SmartRAID 3154-8i\nController Status : Optimal\n")
    assert adaptec["controllers"][0]["serial"] == "Not reported"
    areca = parse_areca("Controller Name : ARC-1886\nSystem Health : Warning\n")
    assert areca["controllers"][0]["health"] == "needs_attention"
    ses = parse_ses(
        '{"status":"OK","enclosure_descriptor":"shelf-1","elements":['
        '{"element_type":"Array device slot","slot":4,"status":"OK",'
        '"identify":false,"fault":false}]}'
    )
    assert ses["enclosures"][0]["slots"][0]["slot"] == 4


def test_snapraid_status_never_reports_stale_parity_as_current() -> None:
    status = parse_snapraid_status("SnapRAID array\n2 files are not synced\nLast sync: yesterday\n")
    assert status["parity_fresh"] is False
    assert status["state"] == "needs_attention"


@pytest.mark.parametrize(
    "parser,output",
    [
        (parse_storcli, "{bad"),
        (parse_mdadm_detail, "nonsense"),
        (parse_zpool_status, "nonsense"),
        (parse_ssacli, "nonsense"),
        (parse_arcconf, "nonsense"),
        (parse_areca, "nonsense"),
        (parse_ses, "nonsense"),
        (parse_snapraid_status, "nonsense"),
    ],
)
def test_provider_parsers_fail_closed(parser, output: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ProviderError):
        parser(output)
