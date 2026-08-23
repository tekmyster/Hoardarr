from __future__ import annotations

from pathlib import Path

from hoardarr.hardware.smp import enrich_smp_topology


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "disks": [
            {
                "id": "wwn:5000c50012345678",
                "connection": {"expander_id": "expander-6:0"},
            },
            {
                "id": "wwn:5000c50087654321",
                "connection": {"expander_id": "expander-6:0"},
            },
        ],
    }


def test_smp_enrichment_probes_each_expander_once_and_reuses_evidence(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def probe(command: list[str]) -> str:
        commands.append(command)
        return (
            "Expander at SAS address: 500a098000000424\n"
            " phy 0:D 12 Gbps attached:[5000c50012345678:01 t(SSP)] dsn=3\n"
        )

    result = enrich_smp_topology(
        _payload(), probe=probe, bsg_root=tmp_path, bsg_exists=lambda _name: True
    )

    assert commands == [
        ["smp_discover", "--summary", "--dsn", "/dev/bsg/expander-6:0"]
    ]
    connections = [item["connection"] for item in result["disks"]]
    assert connections[0]["smp"] == connections[1]["smp"]
    assert connections[0]["smp"]["quality"] == "available"
    assert connections[0]["smp"]["phys"][0]["device_slot_number"] == 3


def test_smp_enrichment_reports_missing_timeout_and_malformed_without_guessing(
    tmp_path: Path,
) -> None:
    missing = enrich_smp_topology(
        _payload(),
        probe=lambda _command: "unused",
        bsg_root=tmp_path,
        bsg_exists=lambda _name: False,
    )
    assert missing["disks"][0]["connection"]["smp"]["quality"] == "not_reported"

    timed_out = enrich_smp_topology(
        _payload(),
        probe=lambda _command: None,
        bsg_root=tmp_path,
        bsg_exists=lambda _name: True,
    )
    assert timed_out["disks"][0]["connection"]["smp"]["quality"] == "temporarily_unavailable"
    malformed = enrich_smp_topology(
        _payload(),
        probe=lambda _command: "hostile output",
        bsg_root=tmp_path,
        bsg_exists=lambda _name: True,
    )
    evidence = malformed["disks"][0]["connection"]["smp"]
    assert evidence["quality"] == "temporarily_unavailable"
    assert evidence["expander_sas_address"] is None
    assert evidence["phys"] == []
