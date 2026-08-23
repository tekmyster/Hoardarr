from __future__ import annotations

from hoardarr.hardware.maintenance import detect_capability, enrich_maintenance_capabilities


def test_nvme_sanitize_capability_is_parsed_and_malformed_output_fails_closed() -> None:
    disk = {
        "kernel_path": "/dev/nvme0n1",
        "connection": {"protocol": "nvme", "transport": "pcie"},
    }
    commands: list[list[str]] = []

    def probe(command: list[str]) -> str:
        commands.append(command)
        return '{"sanicap": 2}'

    result = detect_capability(disk, probe=probe)
    assert result["nvme_block_erase"] is True
    assert commands == [
        ["nvme", "id-ctrl", "/dev/nvme0n1", "--output-format=json"],
        ["smartctl", "-j", "-c", "/dev/nvme0n1"],
    ]
    malformed = detect_capability(disk, probe=lambda _command: '{"sanicap": "oops"}')
    assert malformed["nvme_block_erase"] is False
    assert malformed["source"] == "Not reported"


def test_ata_security_probe_rejects_usb_bridge_and_parses_security_section() -> None:
    direct = {
        "kernel_path": "/dev/sda",
        "connection": {"protocol": "ata", "transport": "sata"},
    }
    assert (
        detect_capability(
            direct,
            probe=lambda _command: "Security:\n\tsupported\n\tnot frozen\n",
        )["ata_secure_erase"]
        is True
    )
    usb = {
        "kernel_path": "/dev/sdb",
        "connection": {"protocol": "ata", "transport": "usb"},
    }
    commands: list[list[str]] = []

    def smart_only(command: list[str]) -> str:
        commands.append(command)
        return "Security: supported"

    assert detect_capability(usb, probe=smart_only)["ata_secure_erase"] is False
    assert commands == [["smartctl", "-j", "-c", "/dev/sdb"]]


def test_smart_self_test_capability_and_durations_are_parsed_without_guessing() -> None:
    disk = {
        "kernel_path": "/dev/sdz",
        "connection": {"protocol": "scsi", "transport": "sas"},
    }
    output = (
        '{"smart_support":{"available":true},"ata_smart_data":{"self_test":'
        '{"polling_minutes":{"short":2,"extended":381}}}}'
    )
    capability = detect_capability(disk, probe=lambda _command: output)["smart_self_test"]
    assert capability == {
        "status": "available",
        "short_minutes": 2,
        "extended_minutes": 381,
        "source": "smartctl -j -c",
    }

    unsupported = detect_capability(
        disk, probe=lambda _command: '{"smart_support":{"available":false}}'
    )["smart_self_test"]
    assert unsupported["status"] == "unsupported"


def test_payload_enrichment_preserves_source_and_never_invents_sector_conversion() -> None:
    payload = {
        "disks": [
            {
                "kernel_path": "/dev/sda",
                "connection": {"protocol": "ata", "transport": "sata"},
                "maintenance_capabilities": {
                    "supported_logical_sector_bytes": [512],
                    "sector_format_passthrough": False,
                },
            }
        ]
    }
    result = enrich_maintenance_capabilities(
        payload, probe=lambda _command: "Security:\n\tsupported\n"
    )
    capability = result["disks"][0]["maintenance_capabilities"]
    assert capability["ata_secure_erase"] is True
    assert capability["sector_format_passthrough"] is False
    assert capability["supported_logical_sector_bytes"] == [512]
