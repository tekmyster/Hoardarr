from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from hoardarr.operations.service import document_hash
from hoardarr.storage import executor
from hoardarr.storage.executor import ExecutorFailure, Paths, preview_foreign_stack


def _device(identifier: str, serial: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "stable_identity": True,
        "vendor": "TEST",
        "model": "Foreign member",
        "serial": serial,
        "wwn": identifier.removeprefix("wwn:"),
        "eui64": None,
        "nguid": None,
        "capacity_bytes": 8_000_000_000,
        "logical_sector_bytes": 512,
        "physical_sector_bytes": 4096,
    }


def _live(device: dict[str, Any], kernel_path: str) -> dict[str, Any]:
    return {
        "id": device["id"],
        "stable_identity": True,
        "vendor": device["vendor"],
        "model": device["model"],
        "identity": {
            "serial": device["serial"],
            "wwn": device["wwn"],
            "eui64": None,
            "nguid": None,
        },
        "capacity_bytes": device["capacity_bytes"],
        "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
        "kernel_path": kernel_path,
        "partitions": [],
    }


def _plan(profile: str, signature_type: str, signature_uuid: str) -> dict[str, Any]:
    members = []
    for index in range(2):
        device = _device(f"wwn:500000000000000{index}", f"MEMBER-{index}")
        members.append(
            {
                "device": device,
                "device_binding_sha256": document_hash(device),
                "source": {
                    "kind": "whole_device",
                    "kernel_path_at_preview": f"/dev/sd{chr(98 + index)}",
                    "partition_number": None,
                    "signature_type": signature_type,
                    "signature_uuid": signature_uuid,
                },
            }
        )
    document = {
        "schema_version": 1,
        "operation": "foreign.preview_stack",
        "candidate_id": "foreign:0123456789abcdef01234567",
        "profile": profile,
        "hardware_snapshot_id": "snapshot-1",
        "hardware_snapshot_sha256": "a" * 64,
        "members": members,
        "activation_allowed": False,
        "mutation_performed": False,
    }
    return {**document, "plan_sha256": document_hash(document)}


@contextlib.contextmanager
def _locks(_paths: Paths, _ids: list[str]) -> Iterator[None]:
    yield


def _prepare(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, plan: dict[str, Any]
) -> tuple[Paths, dict[str, Any]]:
    paths = Paths()
    live = {
        member["device"]["id"]: _live(member["device"], member["source"]["kernel_path_at_preview"])
        for member in plan["members"]
    }
    monkeypatch.setattr(executor, "_device_locks", _locks)
    monkeypatch.setattr(executor, "_ensure_not_active", lambda *_args: None)
    monkeypatch.setattr(
        executor, "_stable_path", lambda _paths, disk: tmp_path / str(disk["id"]).replace(":", "-")
    )
    monkeypatch.setattr(executor, "_tool", lambda name: name)
    return paths, {"source": {"kind": "sysfs"}, "disks": list(live.values())}


def test_md_preview_proves_membership_without_assembling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _plan("linux_md", "linux_raid_member", "array-1")
    paths, inventory = _prepare(monkeypatch, tmp_path, plan)
    commands: list[list[str]] = []

    def probe(command: list[str], _timeout: int) -> str:
        commands.append(command)
        if command[0] == "wipefs":
            return json.dumps({"signatures": [{"type": "linux_raid_member", "uuid": "array-1"}]})
        role = "0" if command[-1].endswith("0") else "1"
        return (
            f"MD_UUID=array-1\nMD_LEVEL=raid1\nMD_DEVICES=2\nMD_DEVICE_ROLE={role}\nMD_EVENTS=42\n"
        )

    result = preview_foreign_stack(
        {"operation": "preview_foreign_stack", "plan_sha256": plan["plan_sha256"], "plan": plan},
        paths=paths,
        inventory_provider=lambda: inventory,
        probe=probe,
    )

    assert result["provider"] == "linux_md"
    assert result["completeness"]["state"] == "complete"
    assert result["mountability"]["state"] == "read_only_assembly_candidate"
    assert result["activation_performed"] is False
    assert result["mutation_performed"] is False
    assert all("--assemble" not in command for command in commands)


def test_lvm_preview_uses_readonly_device_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _plan("lvm", "lvm2_member", "vg-1")
    paths, inventory = _prepare(monkeypatch, tmp_path, plan)
    commands: list[list[str]] = []

    def probe(command: list[str], _timeout: int) -> str:
        commands.append(command)
        if command[0] == "wipefs":
            return json.dumps({"signatures": [{"type": "lvm2_member", "uuid": "vg-1"}]})
        if command[0] == "pvs":
            return json.dumps(
                {
                    "report": [
                        {
                            "pv": [
                                {
                                    "pv_uuid": "pv-1",
                                    "pv_name": command[command.index("--devices") + 1].split(",")[
                                        0
                                    ],
                                    "vg_uuid": "vg-1",
                                    "vg_name": "archive",
                                    "pv_size": "8000000000",
                                    "pv_free": "0",
                                    "pv_attr": "a--",
                                },
                                {
                                    "pv_uuid": "pv-2",
                                    "pv_name": command[command.index("--devices") + 1].split(",")[
                                        1
                                    ],
                                    "vg_uuid": "vg-1",
                                    "vg_name": "archive",
                                    "pv_size": "8000000000",
                                    "pv_free": "0",
                                    "pv_attr": "a--",
                                },
                            ]
                        }
                    ]
                }
            )
        return json.dumps(
            {
                "report": [
                    {
                        "vg": [
                            {
                                "vg_uuid": "vg-1",
                                "vg_name": "archive",
                                "pv_count": "2",
                                "vg_missing_pv_count": "0",
                                "vg_attr": "wz--n-",
                                "vg_size": "16000000000",
                                "vg_free": "0",
                            }
                        ]
                    }
                ]
            }
        )

    result = preview_foreign_stack(
        {"operation": "preview_foreign_stack", "plan_sha256": plan["plan_sha256"], "plan": plan},
        paths=paths,
        inventory_provider=lambda: inventory,
        probe=probe,
    )

    assert result["provider"] == "lvm"
    assert result["completeness"]["state"] == "complete"
    lvm_commands = [item for item in commands if item[0] in {"pvs", "vgs"}]
    assert all("--readonly" in item and "--devices" in item for item in lvm_commands)
    assert all("vgchange" not in item and "lvchange" not in item for item in commands)


def test_zfs_preview_identifies_pool_but_does_not_infer_mountability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _plan("zfs", "zfs_member", "12345")
    paths, inventory = _prepare(monkeypatch, tmp_path, plan)
    commands: list[list[str]] = []

    def probe(command: list[str], _timeout: int) -> str:
        commands.append(command)
        if command[0] == "wipefs":
            return json.dumps({"signatures": [{"type": "zfs_member", "uuid": "12345"}]})
        return (
            """LABEL 0\n    name: 'archive'\n    pool_guid: 12345\n    guid: 67890\n    txg: 22\n"""
        )

    result = preview_foreign_stack(
        {"operation": "preview_foreign_stack", "plan_sha256": plan["plan_sha256"], "plan": plan},
        paths=paths,
        inventory_provider=lambda: inventory,
        probe=probe,
    )

    assert result["provider"] == "zfs"
    assert result["identity"] == "12345"
    assert result["completeness"]["quality"] == "not_reported"
    assert result["mountability"]["quality"] == "not_reported"
    assert all("import" not in item for item in commands)


def test_malformed_provider_output_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _plan("linux_md", "linux_raid_member", "array-1")
    paths, inventory = _prepare(monkeypatch, tmp_path, plan)

    def probe(command: list[str], _timeout: int) -> str:
        if command[0] == "wipefs":
            return json.dumps({"signatures": [{"type": "linux_raid_member", "uuid": "array-1"}]})
        return "not-provider-output"

    with pytest.raises(ExecutorFailure, match="array UUID"):
        preview_foreign_stack(
            {
                "operation": "preview_foreign_stack",
                "plan_sha256": plan["plan_sha256"],
                "plan": plan,
            },
            paths=paths,
            inventory_provider=lambda: inventory,
            probe=probe,
        )
