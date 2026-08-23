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
    assert result["nvme_crypto_erase"] is False
    assert commands == [
        ["nvme", "id-ctrl", "/dev/nvme0n1", "--output-format=json"],
        ["smartctl", "-j", "-c", "/dev/nvme0n1"],
    ]
    malformed = detect_capability(disk, probe=lambda _command: '{"sanicap": "oops"}')
    assert malformed["nvme_block_erase"] is False
    assert malformed["source"] == "Not reported"


def test_nvme_crypto_sanitize_capability_uses_the_controller_bit() -> None:
    disk = {
        "kernel_path": "/dev/nvme0n1",
        "connection": {"protocol": "nvme", "transport": "pcie"},
    }

    def probe(command: list[str]) -> str:
        return '{"sanicap": 1}' if command[0] == "nvme" else "{}"

    result = detect_capability(disk, probe=probe)
    assert result["nvme_block_erase"] is False
    assert result["nvme_crypto_erase"] is True


def test_scsi_sanitize_service_actions_are_detected_independently() -> None:
    disk = {
        "kernel_path": "/dev/sdz",
        "connection": {"protocol": "scsi", "transport": "sas"},
    }
    commands: list[list[str]] = []

    def probe(command: list[str]) -> str:
        commands.append(command)
        if command[:2] == ["sg_opcodes", "--opcode=0x48,0x2"]:
            return "Support: supported"
        if command[:2] == ["sg_opcodes", "--opcode=0x48,0x3"]:
            return "Support: not supported"
        return "{}"

    result = detect_capability(disk, probe=probe)
    assert result["scsi_block_erase"] is True
    assert result["scsi_crypto_erase"] is False
    assert result["source"] == "sg_opcodes REPORT SUPPORTED OPERATION CODES"
    assert commands[:2] == [
        ["sg_opcodes", "--opcode=0x48,0x2", "--no-inquiry", "/dev/sdz"],
        ["sg_opcodes", "--opcode=0x48,0x3", "--no-inquiry", "/dev/sdz"],
    ]


def test_scsi_sanitize_is_not_offered_through_an_unverified_usb_bridge() -> None:
    disk = {
        "kernel_path": "/dev/sdz",
        "connection": {"protocol": "scsi", "transport": "usb"},
    }
    commands: list[list[str]] = []

    def probe(command: list[str]) -> str:
        commands.append(command)
        return "Support: supported"

    result = detect_capability(disk, probe=probe)
    assert result["scsi_block_erase"] is False
    assert result["scsi_crypto_erase"] is False
    assert commands == [["smartctl", "-j", "-c", "/dev/sdz"]]


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

    unavailable = detect_capability(
        direct,
        probe=lambda command: (
            "Security:\n\tnot supported\n" if command[0] == "hdparm" else "{}"
        ),
    )
    assert unavailable["ata_secure_erase"] is False


def test_destructive_capability_is_not_carried_across_a_failed_probe() -> None:
    disk = {
        "kernel_path": "/dev/nvme0n1",
        "connection": {"protocol": "nvme", "transport": "pcie"},
        "maintenance_capabilities": {
            "nvme_block_erase": True,
            "source": "older scan",
        },
    }
    result = detect_capability(disk, probe=lambda _command: None)
    assert result["nvme_block_erase"] is False
    assert result["source"] == "Not reported"


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
