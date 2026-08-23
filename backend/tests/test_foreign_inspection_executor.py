from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from hoardarr.operations.service import document_hash
from hoardarr.storage import executor
from hoardarr.storage.executor import (
    ExecutorFailure,
    Paths,
    _inventory_foreign_tree,
    apply_foreign_inspection,
)

DEVICE_ID = "wwn:foreign-archive"
OPERATION_ID = "11111111-1111-4111-8111-111111111111"


def _device() -> dict[str, Any]:
    return {
        "id": DEVICE_ID,
        "stable_identity": True,
        "vendor": "TEST",
        "model": "Archive",
        "serial": "ARCHIVE-1",
        "wwn": "5000000000000001",
        "eui64": None,
        "nguid": None,
        "capacity_bytes": 8_000_000_000,
        "logical_sector_bytes": 512,
        "physical_sector_bytes": 4096,
    }


def _live_disk() -> dict[str, Any]:
    device = _device()
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
        "kernel_path": "/dev/sdz",
        "partitions": [],
    }


def _plan() -> dict[str, Any]:
    device = _device()
    document = {
        "schema_version": 1,
        "operation": "foreign.inspect_read_only",
        "candidate_id": "foreign:0123456789abcdef01234567",
        "hardware_snapshot_id": "snapshot-1",
        "hardware_snapshot_sha256": "a" * 64,
        "device": device,
        "device_binding_sha256": document_hash(device),
        "source": {
            "kind": "whole_device",
            "kernel_path_at_preview": "/dev/sdz",
            "partition_number": None,
            "filesystem_type": "ext4",
            "filesystem_uuid": "archive-fs",
            "filesystem_label": "Archive",
            "signature_source": "wipefs",
            "read_only_options": ["ro", "noload", "nodev", "nosuid", "noexec"],
        },
        "limits": {
            "maximum_entries": 100_000,
            "maximum_extension_groups": 256,
            "maximum_errors": 100,
        },
        "access": "read_only",
        "persistent_mount": False,
        "automatic_activation": False,
        "mutation_performed": False,
    }
    return {**document, "plan_sha256": document_hash(document)}


def _request(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation": "apply_foreign_inspection",
        "operation_id": OPERATION_ID,
        "plan_sha256": plan["plan_sha256"],
        "plan": plan,
        "confirmation_sha256": document_hash({"confirmation": "INSPECT READ ONLY"}),
    }


@contextlib.contextmanager
def _locks(_paths: Paths, _ids: list[str]) -> Iterator[None]:
    yield


def _prepare(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Paths:
    paths = Paths(
        transaction_root=tmp_path / "transactions",
        inspection_root=tmp_path / "imports",
        quarantine_marker=tmp_path / "quarantine.json",
    )
    monkeypatch.setattr(executor, "_device_locks", _locks)
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})
    monkeypatch.setattr(executor, "_ensure_not_active", lambda *_args: None)
    monkeypatch.setattr(executor, "_stable_path", lambda *_args: tmp_path / "by-id-archive")
    monkeypatch.setattr(executor, "_tool", lambda name: name)
    return paths


def _probe(command: list[str], _timeout: int) -> str:
    if command[0] == "wipefs":
        return json.dumps(
            {
                "signatures": [
                    {
                        "type": "ext4",
                        "uuid": "archive-fs",
                        "label": "Archive",
                        "usage": "filesystem",
                    }
                ]
            }
        )
    return json.dumps(
        {
            "filesystems": [
                {
                    "source": "/dev/disk/by-id/archive",
                    "fstype": "ext4",
                    "options": "ro,noload,nodev,nosuid,noexec",
                }
            ]
        }
    )


def test_read_only_inspection_mounts_inventories_and_always_detaches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = _prepare(monkeypatch, tmp_path)
    commands: list[list[str]] = []

    result = apply_foreign_inspection(
        _request(_plan()),
        paths=paths,
        inventory_provider=lambda: {"source": {"kind": "sysfs"}, "disks": [_live_disk()]},
        runner=lambda command, _timeout: commands.append(command),
        probe=_probe,
        tree_inventory=lambda target, limits: {
            "file_count": 12,
            "total_bytes": 4096,
            "read_errors": [],
            "truncated": False,
            "target_was_private": target.parent == paths.inspection_root,
            "limit": limits["maximum_entries"],
        },
    )

    assert result["access"] == "read_only"
    assert result["persistent_mount"] is False
    assert result["mutation_performed"] is False
    assert result["inventory"]["file_count"] == 12
    assert commands[0][:6] == [
        "mount",
        "--read-only",
        "--types",
        "ext4",
        "--options",
        "ro,noload,nodev,nosuid,noexec",
    ]
    assert commands[-1][0] == "umount"
    assert not (paths.inspection_root / OPERATION_ID).exists()
    journal = json.loads((paths.transaction_root / f"{OPERATION_ID}.json").read_text())
    assert journal["state"] == "succeeded"
    status = executor.storage_operation_status(OPERATION_ID, paths=paths)
    assert status["state"] == "succeeded"
    assert status["phase"] == "Read-only inspection completed"
    assert status["percent"] == 100
    assert status["result"]["inventory"]["file_count"] == 12


def test_signature_drift_fails_before_mount(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = _prepare(monkeypatch, tmp_path)
    commands: list[list[str]] = []

    with pytest.raises(ExecutorFailure, match="signature") as raised:
        apply_foreign_inspection(
            _request(_plan()),
            paths=paths,
            inventory_provider=lambda: {"source": {"kind": "sysfs"}, "disks": [_live_disk()]},
            runner=lambda command, _timeout: commands.append(command),
            probe=lambda _command, _timeout: json.dumps(
                {"signatures": [{"type": "xfs", "uuid": "another-filesystem"}]}
            ),
        )

    assert raised.value.code == "foreign_signature_changed"
    assert commands == []


def test_rw_mount_is_detached_and_reported_as_needs_attention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = _prepare(monkeypatch, tmp_path)
    commands: list[list[str]] = []

    def probe(command: list[str], timeout: int) -> str:
        value = _probe(command, timeout)
        return value.replace("ro,noload", "rw,noload") if command[0] == "findmnt" else value

    with pytest.raises(ExecutorFailure) as raised:
        apply_foreign_inspection(
            _request(_plan()),
            paths=paths,
            inventory_provider=lambda: {"source": {"kind": "sysfs"}, "disks": [_live_disk()]},
            runner=lambda command, _timeout: commands.append(command),
            probe=probe,
        )

    assert raised.value.code == "foreign_mount_not_read_only"
    assert commands[-1][0] == "umount"
    assert not (paths.inspection_root / OPERATION_ID).exists()


def test_unmount_failure_is_needs_attention_and_never_claims_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = _prepare(monkeypatch, tmp_path)

    def runner(command: list[str], _timeout: int) -> None:
        if command[0] == "umount":
            raise ExecutorFailure("storage_tool_failed", "unmount failed")

    with pytest.raises(ExecutorFailure) as raised:
        apply_foreign_inspection(
            _request(_plan()),
            paths=paths,
            inventory_provider=lambda: {"source": {"kind": "sysfs"}, "disks": [_live_disk()]},
            runner=runner,
            probe=_probe,
            tree_inventory=lambda _target, _limits: {"file_count": 0},
        )

    assert raised.value.code == "foreign_unmount_failed"
    assert raised.value.needs_attention is True
    journal = json.loads((paths.transaction_root / f"{OPERATION_ID}.json").read_text())
    assert journal["state"] == "needs_attention"


def test_inventory_is_bounded_and_does_not_follow_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.mkv").write_bytes(b"a" * 32)
    (source / "b.mkv").write_bytes(b"b" * 64)
    (source / "c.txt").write_bytes(b"c" * 128)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "must-not-be-counted.bin").write_bytes(b"secret")
    with contextlib.suppress(OSError):  # Windows may lack developer-mode symlink permission.
        (source / "outside-link").symlink_to(outside, target_is_directory=True)

    report = _inventory_foreign_tree(
        source,
        {"maximum_entries": 2, "maximum_extension_groups": 256, "maximum_errors": 100},
    )

    assert report["file_count"] == 2
    assert report["total_bytes"] == 96
    assert report["truncated"] is True
    assert all(item["extension"] != ".bin" for item in report["extension_distribution"])
