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
    parse_smp_discover,
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
        '{"element_type":"Temperature sensor","temperature_c":42},'
        '{"element_type":"Cooling","speed_rpm":8200},'
        '{"element_type":"Power supply","status":"OK"},'
        '{"element_type":"Voltage sensor","voltage_v":12.1},'
        '{"element_type":"SAS expander","status":"OK"},'
        '{"element_type":"Array device slot","slot":4,"status":"OK",'
        '"identify":false,"fault":false,"sas_address":"0x5000c50012345678",'
        '"attached_sas_address":"0x500a098000000424"}]}'
    )
    enclosure = ses["enclosures"][0]
    assert enclosure["slots"][0]["slot"] == 4
    assert enclosure["id"] == NOT_REPORTED
    assert enclosure["temperature_c"] == 42
    assert enclosure["fan_rpm"] == 8200
    assert enclosure["power_supplies"] == ["healthy"]
    assert enclosure["voltages"] == [12.1]
    assert enclosure["expanders"] == ["healthy"]
    assert enclosure["slots"][0]["sas_address"] == "5000c50012345678"
    assert enclosure["slots"][0]["attached_sas_address"] == "500a098000000424"
    assert enclosure["slots"][0]["mapping_confidence"] == "high"


def test_joined_sg_ses_json_correlates_aes_slot_sas_address() -> None:
    result = parse_ses(
        json.dumps(
            {
                "enclosure_status_diagnostic_page": {
                    "primary_enclosure_logical_identifier": "0x500a098000000424",
                    "enclosure_descriptor": "DS424IOM6",
                    "element_type_list": [
                        {
                            "element_type": {"i": 23, "meaning": "Array device slot"},
                            "individual_status_element_list": [
                                {
                                    "status_code": {"i": 1, "meaning": "OK"},
                                    "element_index": 3,
                                    "additional_element_status_descriptor": {
                                        "device_slot_number": 3,
                                        "phy_descriptor_list": [
                                            {
                                                "attached_sas_address": "0x500a098000000424",
                                                "sas_address": "0x5000c50012345678",
                                            }
                                        ],
                                    },
                                }
                            ],
                        }
                    ],
                }
            }
        )
    )
    enclosure = result["enclosures"][0]
    assert enclosure["id"] == "0x500a098000000424"
    assert enclosure["descriptor"] == "DS424IOM6"
    assert enclosure["slots"] == [
        {
            "slot": 3,
            "status": "healthy",
            "identify": NOT_REPORTED,
            "fault": NOT_REPORTED,
            "sas_address": "5000c50012345678",
            "attached_sas_address": "500a098000000424",
            "mapping_source": "SES Additional Element Status SAS address",
            "mapping_confidence": "high",
        }
    ]


def test_smp_discover_parser_preserves_reported_phy_state_and_slot() -> None:
    result = parse_smp_discover(
        "Expander at SAS address: 500a098000000424\n"
        " phy 0:D 12 Gbps attached:[5000c50012345678:01 t(SSP)] dsn=3\n"
        " phy 1:T reset problem attached:[0000000000000000:00]\n"
        " phy 2:S disabled\n"
    )
    assert result["expander_sas_address"] == "500a098000000424"
    assert result["phys"][0] == {
        "phy_id": 0,
        "routing": "D",
        "state": "attached",
        "negotiated_rate_gbps": 12.0,
        "attached_sas_address": "5000c50012345678",
        "attached_phy_id": 1,
        "attached_details": "t(SSP)",
        "device_slot_number": 3,
    }
    assert result["phys"][1]["state"] == "reset_problem"
    assert result["phys"][1]["attached_sas_address"] is None
    assert result["phys"][2]["state"] == "disabled"


def test_smp_discover_parser_rejects_unidentified_or_oversized_output() -> None:
    with pytest.raises(ProviderError, match="identity"):
        parse_smp_discover("phy 0:D disabled")
    with pytest.raises(ProviderError, match="exceeded"):
        parse_smp_discover("x" * (2 * 1024 * 1024 + 1))


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
