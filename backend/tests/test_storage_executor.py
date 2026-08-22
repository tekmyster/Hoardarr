from __future__ import annotations

import json
import os
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path, PurePosixPath

import pytest

from hoardarr.operations.service import document_hash
from hoardarr.storage import executor
from hoardarr.storage.executor import (
    ExecutorFailure,
    Paths,
    _assert_no_symlink_components,
    _run_smart_test,
    _safe_mountpoint,
    _selected_live_devices,
    _validate_plan,
    apply_storage_plan,
    storage_operation_status,
)


@pytest.mark.parametrize(
    ("filesystem", "allocation", "expected"),
    [
        (
            "ext4",
            4096,
            [
                "mkfs.ext4",
                "-F",
                "-E",
                "lazy_itable_init=1,lazy_journal_init=1,nodiscard",
                "-b",
                "4096",
            ],
        ),
        ("xfs", 4096, ["mkfs.xfs", "-f", "-K", "-s", "size=4096"]),
        ("btrfs", None, ["mkfs.btrfs", "-f", "-K"]),
        ("ntfs", 4096, ["mkfs.ntfs", "-F", "-c", "4096"]),
        ("exfat", 131072, ["mkfs.exfat", "-c", "131072"]),
    ],
)
def test_filesystem_commands_use_lightweight_quick_format(
    monkeypatch, filesystem: str, allocation: int | None, expected: list[str]
) -> None:
    monkeypatch.setattr(executor, "_tool", lambda name: name)
    command = executor._filesystem_command(
        filesystem, allocation, Path("/dev/sdb1"), format_mode="quick"
    )
    assert command[:-1] == expected
    assert Path(command[-1]).name == "sdb1"


def test_filesystem_command_rejects_unimplemented_write_heavy_mode() -> None:
    with pytest.raises(ExecutorFailure, match="format mode"):
        executor._filesystem_command("ext4", 4096, Path("/dev/sdb1"), format_mode="full")


def test_storage_timer_is_persistent_and_argv_is_fixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(executor, "_tool", lambda name: f"/usr/bin/{name}")
    paths = Paths(systemd_unit_root=tmp_path / "systemd")
    executor._install_storage_timer(
        paths,
        unit_name="hoardarr-zfs-scrub-media",
        description="Scrub ZFS pool media",
        command=["/usr/sbin/zpool", "scrub", "media"],
        schedule="monthly",
        runner=lambda command, _timeout: commands.append(command),
    )
    service = (paths.systemd_unit_root / "hoardarr-zfs-scrub-media.service").read_text()
    timer = (paths.systemd_unit_root / "hoardarr-zfs-scrub-media.timer").read_text()
    assert "ExecStart=/usr/sbin/zpool scrub media" in service
    assert "OnCalendar=monthly" in timer
    assert commands[-1] == [
        "/usr/bin/systemctl",
        "enable",
        "--now",
        "hoardarr-zfs-scrub-media.timer",
    ]
    with pytest.raises(ExecutorFailure) as failure:
        executor._install_storage_timer(
            paths,
            unit_name="../../bad",
            description="bad",
            command=["zpool", "scrub", "media"],
            schedule="daily",
            runner=lambda *_args: None,
        )
    assert failure.value.code == "schedule_invalid"


DEVICE_ID = "serial:vendor:model:stable-serial"


def _selected_device() -> dict[str, object]:
    return {
        "id": DEVICE_ID,
        "stable_identity": True,
        "kernel_path": "/dev/sdb",
        "vendor": "VENDOR",
        "model": "MODEL",
        "serial": "STABLE-SERIAL",
        "wwn": None,
        "eui64": None,
        "nguid": None,
        "capacity_bytes": 256_000_000_000,
        "logical_sector_bytes": 512,
        "physical_sector_bytes": 4096,
    }


def _live_disk(path: str = "/dev/sdz") -> dict[str, object]:
    return {
        "id": DEVICE_ID,
        "stable_identity": True,
        "kernel_path": path,
        "vendor": "VENDOR",
        "model": "MODEL",
        "identity": {
            "serial": "STABLE-SERIAL",
            "wwn": None,
            "eui64": None,
            "nguid": None,
        },
        "capacity_bytes": 256_000_000_000,
        "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
        "partitions": [],
    }


def _document(*, destructive: bool = False) -> dict[str, object]:
    selected = [_selected_device()]
    action = {
        "action_id": f"identity:{DEVICE_ID}",
        "type": "drive.write_read.destructive" if destructive else "drive.identity.verify",
        "device_id": DEVICE_ID,
        "destructive": destructive,
    }
    return {
        "schema_version": 2,
        "kind": "storage_setup",
        "apply_available": True,
        "blockers": [],
        "presentation_root": "/data",
        "actions": {"directories": []},
        "storage": {
            "topology": "individual",
            "selected_devices": selected,
            "snapshot_binding": {
                "snapshot_id": "snapshot",
                "snapshot_sha256": "a" * 64,
                "device_binding_sha256": document_hash(selected),
                "selected_device_ids": [DEVICE_ID],
            },
            "actions": [
                action,
                {
                    "action_id": "storage-layout",
                    "type": "storage.layout.ensure",
                    "topology": "individual",
                    "device_ids": [DEVICE_ID],
                    "purpose": "media",
                    "destructive": False,
                },
            ],
            "risk": {"destructive": destructive},
        },
    }


def _request(
    document: dict[str, object], approval: dict[str, object] | None = None
) -> dict[str, object]:
    return {
        "operation": "apply_storage_plan",
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "plan_sha256": document_hash(document),
        "document": document,
        "approval": approval,
    }


def test_plan_validation_accepts_only_hash_verified_typed_document() -> None:
    document = _document()
    operation_id, plan_sha, validated, approval = _validate_plan(_request(document))
    assert operation_id.startswith("11111111")
    assert plan_sha == document_hash(document)
    assert validated == document
    assert approval is None


@pytest.mark.parametrize("topology", ["cache", "block", "import"])
def test_single_drive_special_layouts_are_typed_and_executable(topology: str) -> None:
    document = _document()
    document["storage"]["topology"] = topology  # type: ignore[index]
    document["storage"]["actions"][-1]["topology"] = topology  # type: ignore[index]
    _validate_plan(_request(document))


def test_test_only_executor_finishes_without_mount_or_storage_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _document()
    document["storage"]["topology"] = "test"  # type: ignore[index]
    document["storage"]["actions"] = document["storage"]["actions"][:1]  # type: ignore[index]
    request = _request(document)
    paths = Paths(
        quarantine_marker=tmp_path / "quarantine.json",
        transaction_root=tmp_path / "transactions",
        lock_root=tmp_path / "locks",
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})
    monkeypatch.setattr(executor, "_device_locks", lambda _paths, _ids: nullcontext())
    monkeypatch.setattr(executor, "_revalidate", lambda *_args: {DEVICE_ID: _live_disk()})

    result = apply_storage_plan(
        request,
        paths=paths,
        inventory_provider=lambda: {"disks": [_live_disk()]},
        runner=lambda command, _timeout: commands.append(command),
    )

    assert result["topology"] == "test"
    assert result["mountpoint"] is None
    assert commands == []
    assert storage_operation_status(str(request["operation_id"]), paths=paths)["percent"] == 100


def test_mixed_layout_executor_revalidates_and_builds_component_pools_before_mergerfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = [f"serial:test:d{index}" for index in range(6)]
    live = {identifier: {**_live_disk(), "id": identifier} for identifier in ids}
    components = []
    for number, members in enumerate((ids[:3], ids[3:]), start=1):
        components.append(
            {
                "topology": "zfs",
                "device_ids": members,
                "options": {
                    "name": f"media_{number}",
                    "vdevs": [
                        {
                            "type": "raidz1",
                            "device_ids": members,
                            "tolerated_failures": 1,
                        }
                    ],
                    "ashift": 12,
                    "recordsize": "1M",
                    "compression": "lz4",
                    "mountpoint": f"/mnt/hoardarr/media-{number}",
                    "scrub_schedule": "monthly",
                    "snapshots": {"enabled": False, "retention": 0},
                    "special": [],
                    "cache": [],
                    "log": [],
                },
            }
        )
    options = {
        "name": "media_all",
        "components": components,
        "mountpoint": "/data",
        "create_policy": "mfs",
        "search_policy": "ff",
    }
    document = {
        "presentation_root": "/data",
        "actions": {"directories": [], "connectivity": []},
        "storage": {
            "topology": "mixed",
            "actions": [
                {
                    "action_id": "storage-layout",
                    "type": "storage.layout.ensure",
                    "topology": "mixed",
                    "device_ids": ids,
                    "purpose": "media",
                    "layout_options": options,
                    "destructive": True,
                }
            ],
            "layout_options": options,
        },
    }
    paths = Paths(
        transaction_root=tmp_path / "transactions",
        fstab=tmp_path / "fstab",
        mount_root=tmp_path / "mounts",
        systemd_unit_root=tmp_path / "systemd",
    )
    commands: list[list[str]] = []
    revalidations = 0

    def revalidate(*_args: object) -> dict[str, dict[str, object]]:
        nonlocal revalidations
        revalidations += 1
        return live

    monkeypatch.setattr(executor, "_revalidate", revalidate)
    monkeypatch.setattr(
        executor,
        "_safe_mountpoint",
        lambda value: tmp_path / "managed" / value.lstrip("/"),
    )
    monkeypatch.setattr(
        executor,
        "_stable_path",
        lambda _paths, disk: PurePosixPath(f"/dev/disk/by-id/{disk['id']}"),
    )
    monkeypatch.setattr(executor, "_tool", lambda name: name)
    journal = {"completed_steps": 0, "notices": []}
    result = executor._execute_actions(
        operation_id="11111111-1111-4111-8111-111111111111",
        document=document,
        paths=paths,
        inventory_provider=lambda: {"disks": list(live.values())},
        runner=lambda command, _timeout: commands.append(command),
        journal=journal,
    )

    tools = [command[0] for command in commands]
    assert tools.count("zpool") == 2
    assert tools.index("mergerfs") > max(
        index for index, tool in enumerate(tools) if tool == "zpool"
    )
    assert "findmnt" in tools
    assert revalidations >= len(commands)
    assert result["topology"] == "mixed"
    assert "fuse.mergerfs" in paths.fstab.read_text(encoding="utf-8")


def test_plan_validation_accepts_bound_non_guest_smb_share() -> None:
    document = _document()
    document["storage"]["service_account"] = {"username": "media"}  # type: ignore[index]
    document["actions"]["connectivity"] = [  # type: ignore[index]
        {
            "action_id": "smb-share:1",
            "type": "smb.share.ensure",
            "name": "media",
            "path": "/data/media",
            "read_only": False,
            "guest": False,
            "destructive": False,
        }
    ]
    _validate_plan(_request(document))

    changed = deepcopy(document)
    changed["actions"]["connectivity"][0]["path"] = "/srv/outside"  # type: ignore[index]
    with pytest.raises(ExecutorFailure) as failure:
        _validate_plan(_request(changed))
    assert failure.value.code == "connectivity_path_outside_storage"


def test_smb_configuration_is_validated_before_install_and_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = Paths(
        samba_config=tmp_path / "samba" / "smb.conf",
        samba_include=tmp_path / "samba" / "hoardarr-shares.conf",
    )
    paths.samba_config.parent.mkdir(parents=True)
    paths.samba_config.write_text("[global]\n", encoding="utf-8")
    commands: list[list[str]] = []
    monkeypatch.setattr(executor, "_tool", lambda name: f"/usr/bin/{name}")
    executor._ensure_smb_shares(
        paths,
        "11111111-1111-4111-8111-111111111111",
        [
            {
                "name": "media",
                "path": "/data/media",
                "read_only": False,
            }
        ],
        "media",
        lambda command, _timeout: commands.append(command),
    )
    assert "[media]" in paths.samba_include.read_text(encoding="utf-8")
    assert "valid users = media" in paths.samba_include.read_text(encoding="utf-8")
    assert f"include = {paths.samba_include}" in paths.samba_config.read_text(encoding="utf-8")
    assert commands[0][0].endswith("testparm")
    assert commands[-1] == ["/usr/bin/systemctl", "reload", "smbd.service"]


def test_plan_validation_rejects_unknown_action_fields() -> None:
    document = _document()
    document["storage"]["actions"][0]["command"] = "rm -rf /"  # type: ignore[index]
    with pytest.raises(ExecutorFailure, match="unknown fields") as failure:
        _validate_plan(_request(document))
    assert failure.value.code == "action_fields_invalid"


def test_plan_validation_requires_exact_bound_destructive_approval() -> None:
    document = _document(destructive=True)
    binding = document["storage"]["snapshot_binding"]  # type: ignore[index]
    approval = {
        "approval_id": "approval",
        "wizard_revision": 4,
        "plan_sha256": document_hash(document),
        "hardware_snapshot_sha256": binding["snapshot_sha256"],
        "device_binding_sha256": binding["device_binding_sha256"],
        "selected_device_ids": binding["selected_device_ids"],
        "confirmation_phrase": "I AGREE",
        "confirmation_sha256": document_hash({"confirmation": "I AGREE"}),
    }
    _validate_plan(_request(document, approval))
    changed = deepcopy(approval)
    changed["selected_device_ids"] = ["serial:another-drive"]
    with pytest.raises(ExecutorFailure) as failure:
        _validate_plan(_request(document, changed))
    assert failure.value.code == "destructive_consent_missing"


def test_live_identity_uses_current_path_but_requires_all_stable_fields() -> None:
    document = _document()
    current = _selected_live_devices(document, {"disks": [_live_disk("/dev/sdz")]})
    assert current[DEVICE_ID]["kernel_path"] == "/dev/sdz"
    changed = _live_disk()
    changed["capacity_bytes"] = 1
    with pytest.raises(ExecutorFailure) as failure:
        _selected_live_devices(document, {"disks": [changed]})
    assert failure.value.code == "drive_identity_changed"


@pytest.mark.parametrize("path", ["/", "/etc/storage", "/var/lib/hoardarr/data", "/tmp/data"])
def test_mountpoints_are_restricted(path: str) -> None:
    with pytest.raises(ExecutorFailure) as failure:
        _safe_mountpoint(path)
    assert failure.value.code == "mountpoint_invalid"


@pytest.mark.parametrize("path", ["/data", "/data/media", "/mnt/combined", "/srv/archive"])
def test_approved_mount_roots(path: str) -> None:
    assert str(_safe_mountpoint(path)).replace("\\", "/") == path


def test_privileged_paths_reject_a_symlink_in_any_existing_component(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    real = tmp_path / "real"
    managed.mkdir()
    real.mkdir()
    (managed / "link").symlink_to(real, target_is_directory=True)

    with pytest.raises(ExecutorFailure) as failure:
        _assert_no_symlink_components(managed / "link" / "media")

    assert failure.value.code == "mountpoint_symlink"


def test_executor_journals_success_and_replays_without_executing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _document()
    request = _request(document)
    paths = Paths(
        quarantine_marker=tmp_path / "quarantine.json",
        transaction_root=tmp_path / "transactions",
        lock_root=tmp_path / "locks",
    )
    calls: list[str] = []
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})
    monkeypatch.setattr(executor, "_device_locks", lambda _paths, _ids: nullcontext())
    monkeypatch.setattr(executor, "_revalidate", lambda *_args: {DEVICE_ID: _live_disk()})

    def execute(**kwargs: object) -> dict[str, object]:
        calls.append("execute")
        return {
            "operation_id": request["operation_id"],
            "topology": "individual",
            "selected_device_ids": [DEVICE_ID],
            "replayed": False,
        }

    monkeypatch.setattr(executor, "_execute_actions", execute)
    first = apply_storage_plan(request, paths=paths, inventory_provider=lambda: {"disks": []})
    progress = storage_operation_status(str(request["operation_id"]), paths=paths)
    second = apply_storage_plan(request, paths=paths, inventory_provider=lambda: {"disks": []})
    assert first["replayed"] is False
    assert progress["state"] == "succeeded"
    assert progress["percent"] == 100
    assert progress["completed_steps"] == progress["total_steps"]
    assert progress["result"] == first
    assert second["replayed"] is True
    assert calls == ["execute"]


def test_storage_progress_waits_safely_before_the_executor_starts(tmp_path: Path) -> None:
    paths = Paths(transaction_root=tmp_path / "transactions")
    progress = storage_operation_status("11111111-1111-4111-8111-111111111111", paths=paths)
    assert progress["state"] == "waiting"
    assert progress["percent"] == 0
    assert progress["estimate"] is None

    with pytest.raises(ExecutorFailure) as failure:
        storage_operation_status("../../etc/passwd", paths=paths)
    assert failure.value.code == "operation_id_invalid"


def test_storage_progress_includes_live_drive_work_and_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation_id = "11111111-1111-4111-8111-111111111111"
    paths = Paths(transaction_root=tmp_path / "transactions")
    paths.transaction_root.mkdir(mode=0o700)
    action_id = f"surface:{DEVICE_ID}"
    (paths.transaction_root / f"{operation_id}.json").write_text(
        json.dumps(
            {
                "operation_id": operation_id,
                "state": "running",
                "phase": "Checking and preparing drives",
                "completed_steps": 1,
                "total_steps": 5,
                "completed_actions": ["identity"],
                "notices": [],
                "current_action": {
                    "id": action_id,
                    "type": "drive.surface.read",
                    "number": 2,
                    "count": 3,
                },
                "updated_at": 123.0,
            }
        ),
        encoding="utf-8",
    )
    (paths.transaction_root / f"{operation_id}.work.json").write_text(
        json.dumps(
            {
                "operation_id": operation_id,
                "actions": [
                    {
                        "id": action_id,
                        "type": "drive.surface.read",
                        "device": "/dev/sdb",
                        "capacity_bytes": 1000,
                    },
                    {
                        "id": "surface:other",
                        "type": "drive.surface.read",
                        "device": "/dev/sdc",
                        "capacity_bytes": 1000,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        executor,
        "_active_surface_read_progress",
        lambda expected_device: {
            "kind": "surface_read",
            "device": expected_device,
            "processed_bytes": 500,
            "total_bytes": 1000,
            "percent": 50.0,
            "elapsed_seconds": 10,
            "bytes_per_second": 100,
            "estimated_seconds_remaining": 5,
        },
    )

    progress = storage_operation_status(operation_id, paths=paths)

    assert progress["current_action"]["progress"]["device"] == "/dev/sdb"
    assert progress["percent"] == 30
    assert progress["estimate"]["remaining_bytes"] == 1500
    assert progress["estimate"]["estimated_seconds_remaining"] == 15


def test_executor_reports_unavailable_transaction_journal_before_drive_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _document()
    transaction_root = tmp_path / "transactions"
    transaction_root.write_text("not a directory", encoding="utf-8")
    paths = Paths(
        quarantine_marker=tmp_path / "quarantine.json",
        transaction_root=transaction_root,
        lock_root=tmp_path / "locks",
    )
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})

    with pytest.raises(ExecutorFailure) as failure:
        apply_storage_plan(
            _request(document), paths=paths, inventory_provider=lambda: {"disks": []}
        )

    assert failure.value.code == "transaction_journal_unavailable"
    assert failure.value.needs_attention is True
    assert not paths.lock_root.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and mode enforcement")
def test_executor_rejects_group_or_world_accessible_transaction_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _document()
    paths = Paths(
        quarantine_marker=tmp_path / "quarantine.json",
        transaction_root=tmp_path / "transactions",
        lock_root=tmp_path / "locks",
    )
    paths.transaction_root.mkdir(mode=0o755)
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})

    with pytest.raises(ExecutorFailure) as failure:
        apply_storage_plan(
            _request(document), paths=paths, inventory_provider=lambda: {"disks": []}
        )

    assert failure.value.code == "transaction_journal_unsafe"
    assert failure.value.needs_attention is True
    assert not paths.lock_root.exists()


def test_executor_marks_uncertain_failure_and_refuses_automatic_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _document()
    request = _request(document)
    paths = Paths(
        quarantine_marker=tmp_path / "quarantine.json",
        transaction_root=tmp_path / "transactions",
        lock_root=tmp_path / "locks",
    )
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})
    monkeypatch.setattr(executor, "_device_locks", lambda _paths, _ids: nullcontext())
    monkeypatch.setattr(executor, "_revalidate", lambda *_args: {DEVICE_ID: _live_disk()})

    def fail(**_kwargs: object) -> dict[str, object]:
        raise ExecutorFailure("storage_tool_failed", "failed", needs_attention=True)

    monkeypatch.setattr(executor, "_execute_actions", fail)
    with pytest.raises(ExecutorFailure) as first:
        apply_storage_plan(request, paths=paths, inventory_provider=lambda: {"disks": []})
    assert first.value.code == "storage_tool_failed"
    with pytest.raises(ExecutorFailure) as retry:
        apply_storage_plan(request, paths=paths, inventory_provider=lambda: {"disks": []})
    assert retry.value.code == "prior_operation_needs_attention"


def test_smart_action_waits_for_a_passing_completed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            "# 1 Short offline Completed without error",
            "Self-test started",
            "Self-test routine in progress",
            "Self-test status: complete",
            "# 1 Short offline Completed without error",
        ]
    )
    monkeypatch.setattr(executor, "_tool", lambda name: f"/usr/sbin/{name}")
    monkeypatch.setattr(executor, "_smartctl", lambda _command, **_kwargs: next(responses))
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)
    _run_smart_test(Path("/dev/sdz"), "short")


def test_smart_action_rejects_an_unknown_or_failed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            "# 1 Short offline Completed without error",
            "Self-test started",
            "Self-test status: complete",
            "Completed: read failure",
        ]
    )
    monkeypatch.setattr(executor, "_tool", lambda name: f"/usr/sbin/{name}")
    monkeypatch.setattr(executor, "_smartctl", lambda _command, **_kwargs: next(responses))
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)
    with pytest.raises(ExecutorFailure) as failure:
        _run_smart_test(Path("/dev/sdz"), "short")
    assert failure.value.code == "smart_test_result_failed"


def test_smart_action_skips_when_transport_hides_self_test_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(executor, "_tool", lambda name: f"/usr/sbin/{name}")
    monkeypatch.setattr(
        executor,
        "_smartctl",
        lambda _command, **_kwargs: "Device does not support Self Test logging",
    )
    result = _run_smart_test(Path("/dev/sdz"), "short")
    assert result["outcome"] == "skipped"
    assert result["code"] == "smart_self_test_unavailable"
