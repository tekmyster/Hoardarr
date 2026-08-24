from __future__ import annotations

import hashlib
import json
import os
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path, PurePosixPath

import pytest

from hoardarr.operations.service import document_hash
from hoardarr.storage import executor
from hoardarr.storage.capacity_plans import build_capacity_plan
from hoardarr.storage.executor import (
    ExecutorFailure,
    Paths,
    _assert_no_symlink_components,
    _run_smart_test,
    _safe_mountpoint,
    _selected_live_devices,
    _validate_plan,
    apply_storage_plan,
    apply_storage_volume,
    apply_storage_volume_capacity,
    apply_storage_volume_snapshot,
    reconcile_storage_access,
    storage_operation_status,
)
from hoardarr.storage.layouts import CommandSpec, snapraid_expand_config
from hoardarr.storage.snapshot_plans import build_snapshot_plan
from hoardarr.storage.volume_plans import build_guided_volume_plan


def test_guided_zfs_volume_execution_revalidates_pool_and_replays_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = build_guided_volume_plan(
        [
            {
                "id": "zfs:tank",
                "name": "tank",
                "type": "ZFS",
                "status": "online",
                "pool_guid": "1234567890123456789",
                "free_bytes": 100_000_000_000,
                "degraded": False,
            }
        ],
        name="movies",
        purpose="media",
    )
    operation_id = "11111111-1111-4111-8111-111111111111"
    request = {
        "operation": "apply_storage_volume",
        "operation_id": operation_id,
        "plan_sha256": plan["plan_sha256"],
        "plan": plan,
        "confirmation_sha256": document_hash({"confirmation": "CREATE"}),
    }
    commands: list[list[str]] = []
    monkeypatch.setattr(executor, "_tool", lambda name: f"/usr/sbin/{name}")
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})
    paths = Paths(
        transaction_root=tmp_path / "transactions",
        quarantine_marker=tmp_path / "quarantine.json",
    )

    def state(_name: str) -> dict[str, str]:
        return {"pool_guid": "1234567890123456789"}

    first = apply_storage_volume(
        request,
        paths=paths,
        runner=lambda command, _timeout: commands.append(command),
        zfs_state_provider=state,
        zfs_resource_provider=lambda _name: {"guid": "1111222233334444", "type": "filesystem"},
    )
    assert commands == [
        [
            "/usr/sbin/zfs",
            "create",
            "-o",
            "atime=off",
            "-o",
            "compression=zstd",
            "-o",
            "mountpoint=/srv/hoardarr/volumes/movies",
            "-o",
            "recordsize=1M",
            "tank/movies",
        ]
    ]
    assert first["volume"]["provider_resource_id"] == "tank/movies"
    assert first["volume"]["config"]["provider_guid"] == "1111222233334444"
    replay = apply_storage_volume(
        request,
        paths=paths,
        runner=lambda *_args: pytest.fail("a replay must not run zfs again"),
        zfs_state_provider=state,
        zfs_resource_provider=lambda _name: {"guid": "1111222233334444", "type": "filesystem"},
    )
    assert replay["replayed"] is True


def test_guided_zfs_volume_execution_rejects_malformed_resource_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = build_guided_volume_plan(
        [
            {
                "id": "zfs:tank",
                "name": "tank",
                "type": "ZFS",
                "status": "online",
                "pool_guid": "1234567890123456789",
                "free_bytes": 100_000_000_000,
                "degraded": False,
            }
        ],
        name="movies",
        purpose="media",
    )
    monkeypatch.setattr(executor, "_tool", lambda name: f"/usr/sbin/{name}")
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})
    with pytest.raises(ExecutorFailure) as raised:
        apply_storage_volume(
            {
                "operation": "apply_storage_volume",
                "operation_id": "33333333-3333-4333-8333-333333333333",
                "plan_sha256": plan["plan_sha256"],
                "plan": plan,
                "confirmation_sha256": document_hash({"confirmation": "CREATE"}),
            },
            paths=Paths(
                transaction_root=tmp_path / "transactions",
                quarantine_marker=tmp_path / "quarantine.json",
            ),
            runner=lambda *_args: None,
            zfs_state_provider=lambda _name: {"pool_guid": "1234567890123456789"},
            zfs_resource_provider=lambda _name: {"type": "filesystem", "guid": "not-a-guid"},
        )
    assert raised.value.code == "zfs_resource_verification_failed"


def test_guided_zfs_volume_execution_refuses_pool_identity_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = build_guided_volume_plan(
        [
            {
                "id": "zfs:tank",
                "name": "tank",
                "type": "ZFS",
                "status": "online",
                "pool_guid": "1234567890123456789",
                "free_bytes": 100_000_000_000,
                "degraded": False,
            }
        ],
        name="movies",
        purpose="media",
    )
    monkeypatch.setattr(executor, "_tool", lambda name: f"/usr/sbin/{name}")
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})
    with pytest.raises(ExecutorFailure) as raised:
        apply_storage_volume(
            {
                "operation": "apply_storage_volume",
                "operation_id": "22222222-2222-4222-8222-222222222222",
                "plan_sha256": plan["plan_sha256"],
                "plan": plan,
                "confirmation_sha256": document_hash({"confirmation": "CREATE"}),
            },
            paths=Paths(
                transaction_root=tmp_path / "transactions",
                quarantine_marker=tmp_path / "quarantine.json",
            ),
            runner=lambda *_args: pytest.fail("identity drift must fail before zfs create"),
            zfs_state_provider=lambda _name: {"pool_guid": "9876543210987654321"},
        )
    assert raised.value.code == "zfs_pool_identity_changed"


def test_snapshot_execution_revalidates_guid_verifies_provider_and_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = build_snapshot_plan(
        volume={
            "id": "volume-1",
            "stable_identity": "zfs:dataset:tank/movies",
            "name": "movies",
            "provider": "zfs",
            "resource_type": "dataset",
            "provider_resource_id": "tank/movies",
            "presentation": "file",
        },
        provider_guid="1111222233334444",
        action="create",
        snapshot_name="before-upgrade",
    )
    request = {
        "operation": "apply_storage_volume_snapshot",
        "operation_id": "44444444-4444-4444-8444-444444444444",
        "plan_sha256": plan["plan_sha256"],
        "plan": plan,
        "confirmation_sha256": document_hash({"confirmation": "CREATE SNAPSHOT"}),
    }
    observed = iter([None, {"guid": "5555666677778888", "used": "0"}])
    commands: list[list[str]] = []
    monkeypatch.setattr(executor, "_tool", lambda name: f"/usr/sbin/{name}")
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})
    paths = Paths(
        transaction_root=tmp_path / "transactions",
        quarantine_marker=tmp_path / "quarantine.json",
    )
    result = apply_storage_volume_snapshot(
        request,
        paths=paths,
        runner=lambda command, _timeout: commands.append(command),
        zfs_resource_provider=lambda _name: {
            "guid": "1111222233334444",
            "type": "filesystem",
        },
        zfs_snapshot_provider=lambda _name: next(observed),
    )
    assert commands == [["/usr/sbin/zfs", "snapshot", "tank/movies@before-upgrade"]]
    assert result["snapshot"]["provider_guid"] == "5555666677778888"
    replay = apply_storage_volume_snapshot(
        request,
        paths=paths,
        runner=lambda *_args: pytest.fail("a replay must not run zfs again"),
    )
    assert replay["replayed"] is True


def test_snapshot_execution_refuses_snapshot_identity_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = build_snapshot_plan(
        volume={
            "id": "volume-1",
            "stable_identity": "zfs:dataset:tank/movies",
            "name": "movies",
            "provider": "zfs",
            "resource_type": "dataset",
            "provider_resource_id": "tank/movies",
            "presentation": "file",
        },
        provider_guid="1111222233334444",
        action="delete",
        snapshot={
            "id": "snapshot-1",
            "provider_snapshot_id": "tank/movies@old",
            "snapshot_name": "old",
            "provider_guid": "2222333344445555",
        },
    )
    monkeypatch.setattr(executor, "_tool", lambda name: f"/usr/sbin/{name}")
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})
    with pytest.raises(ExecutorFailure) as raised:
        apply_storage_volume_snapshot(
            {
                "operation": "apply_storage_volume_snapshot",
                "operation_id": "55555555-5555-4555-8555-555555555555",
                "plan_sha256": plan["plan_sha256"],
                "plan": plan,
                "confirmation_sha256": document_hash({"confirmation": "DELETE SNAPSHOT"}),
            },
            paths=Paths(
                transaction_root=tmp_path / "transactions",
                quarantine_marker=tmp_path / "quarantine.json",
            ),
            runner=lambda *_args: pytest.fail("identity drift must fail before delete"),
            zfs_resource_provider=lambda _name: {"guid": "1111222233334444"},
            zfs_snapshot_provider=lambda _name: {"guid": "different"},
        )
    assert raised.value.code == "snapshot_identity_changed"


def test_dataset_capacity_execution_revalidates_applies_verifies_and_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = build_capacity_plan(
        volume={
            "id": "volume-1",
            "stable_identity": "zfs:dataset:tank/movies",
            "name": "movies",
            "provider": "zfs",
            "resource_type": "dataset",
            "provider_resource_id": "tank/movies",
        },
        provider_guid="1111222233334444",
        quota_bytes=20 * 1024**3,
        reservation_bytes=2 * 1024**3,
    )
    request = {
        "operation": "apply_storage_volume_capacity",
        "operation_id": "66666666-6666-4666-8666-666666666666",
        "plan_sha256": plan["plan_sha256"],
        "plan": plan,
        "confirmation_sha256": document_hash({"confirmation": "APPLY CAPACITY LIMITS"}),
    }
    observed = iter(
        [
            {"guid": "1111222233334444", "type": "filesystem"},
            {
                "guid": "1111222233334444",
                "type": "filesystem",
                "quota": str(20 * 1024**3),
                "reservation": str(2 * 1024**3),
                "used": "4096",
                "available": str(18 * 1024**3),
            },
        ]
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(executor, "_tool", lambda name: f"/usr/sbin/{name}")
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})
    paths = Paths(
        transaction_root=tmp_path / "transactions",
        quarantine_marker=tmp_path / "quarantine.json",
    )

    result = apply_storage_volume_capacity(
        request,
        paths=paths,
        runner=lambda command, _timeout: commands.append(command),
        zfs_resource_provider=lambda _name: next(observed),
    )

    assert commands == [[
        "/usr/sbin/zfs",
        "set",
        f"quota={20 * 1024**3}",
        f"reservation={2 * 1024**3}",
        "tank/movies",
    ]]
    assert result["capacity_limits"] == {
        "quota_bytes": 20 * 1024**3,
        "reservation_bytes": 2 * 1024**3,
        "thin_provisioned": None,
    }
    replay = apply_storage_volume_capacity(
        request,
        paths=paths,
        runner=lambda *_args: pytest.fail("a replay must not run zfs set again"),
    )
    assert replay["replayed"] is True


def test_capacity_execution_refuses_identity_drift_before_provider_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = build_capacity_plan(
        volume={
            "id": "volume-1",
            "stable_identity": "zfs:zvol:tank/vm",
            "name": "vm",
            "provider": "zfs",
            "resource_type": "zvol",
            "provider_resource_id": "tank/vm",
        },
        provider_guid="1111222233334444",
        thin_provisioned=True,
    )
    monkeypatch.setattr(executor, "_tool", lambda name: f"/usr/sbin/{name}")
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})

    with pytest.raises(ExecutorFailure) as raised:
        apply_storage_volume_capacity(
            {
                "operation": "apply_storage_volume_capacity",
                "operation_id": "77777777-7777-4777-8777-777777777777",
                "plan_sha256": plan["plan_sha256"],
                "plan": plan,
                "confirmation_sha256": document_hash(
                    {"confirmation": "APPLY CAPACITY LIMITS"}
                ),
            },
            paths=Paths(
                transaction_root=tmp_path / "transactions",
                quarantine_marker=tmp_path / "quarantine.json",
            ),
            runner=lambda *_args: pytest.fail("identity drift must fail before zfs set"),
            zfs_resource_provider=lambda _name: {
                "guid": "9999000011112222",
                "type": "volume",
            },
        )
    assert raised.value.code == "volume_capacity_identity_changed"


def test_capacity_execution_fails_safe_on_malformed_provider_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = build_capacity_plan(
        volume={
            "id": "volume-1",
            "stable_identity": "zfs:dataset:tank/media",
            "name": "media",
            "provider": "zfs",
            "resource_type": "dataset",
            "provider_resource_id": "tank/media",
        },
        provider_guid="1111222233334444",
        quota_bytes=1024,
        reservation_bytes=0,
    )
    observed = iter(
        [
            {"guid": "1111222233334444", "type": "filesystem"},
            {
                "guid": "1111222233334444",
                "type": "filesystem",
                "quota": "not-a-number",
                "reservation": "0",
            },
        ]
    )
    monkeypatch.setattr(executor, "_tool", lambda name: f"/usr/sbin/{name}")
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})

    with pytest.raises(ExecutorFailure) as raised:
        apply_storage_volume_capacity(
            {
                "operation": "apply_storage_volume_capacity",
                "operation_id": "88888888-8888-4888-8888-888888888888",
                "plan_sha256": plan["plan_sha256"],
                "plan": plan,
                "confirmation_sha256": document_hash(
                    {"confirmation": "APPLY CAPACITY LIMITS"}
                ),
            },
            paths=Paths(
                transaction_root=tmp_path / "transactions",
                quarantine_marker=tmp_path / "quarantine.json",
            ),
            runner=lambda *_args: None,
            zfs_resource_provider=lambda _name: next(observed),
        )
    assert raised.value.code == "volume_capacity_verification_failed"
    assert raised.value.needs_attention is True


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


def test_mergerfs_fstab_update_is_atomic_and_replaces_the_existing_mount(
    tmp_path: Path,
) -> None:
    paths = Paths(fstab=tmp_path / "fstab")
    paths.fstab.write_text(
        "/mnt/disk\\040one:/mnt/disk2 /data/media fuse.mergerfs "
        "category.create=mfs,category.search=ff,nofail 0 0\n",
        encoding="utf-8",
    )
    executor._append_fstab(
        paths,
        "11111111-1111-4111-8111-111111111111",
        ["UUID=new-member /mnt/new-member ext4 defaults 0 2"],
        mergerfs_update=(
            "/data/media",
            ["/mnt/disk one", "/mnt/disk2", "/mnt/new-member"],
        ),
    )
    content = paths.fstab.read_text(encoding="utf-8")
    assert content.count("fuse.mergerfs") == 1
    assert "/mnt/disk\\040one:/mnt/disk2:/mnt/new-member /data/media" in content
    assert "UUID=new-member /mnt/new-member ext4 defaults 0 2" in content

    executor._append_fstab(
        paths,
        "11111111-1111-4111-8111-111111111111",
        ["UUID=new-member /mnt/new-member ext4 defaults 0 2"],
        mergerfs_update=(
            "/data/media",
            ["/mnt/disk one", "/mnt/disk2", "/mnt/new-member", "/mnt/reconciled"],
        ),
    )
    replayed = paths.fstab.read_text(encoding="utf-8")
    assert "/mnt/new-member:/mnt/reconciled /data/media" in replayed
    assert replayed.count("UUID=new-member") == 1


def test_runtime_mergerfs_member_names_require_exact_persistent_paths() -> None:
    assert executor._normalize_runtime_mergerfs_branches(
        ["member-a", "member-b"],
        [
            "/mnt/hoardarr/disks/member-a",
            "/mnt/hoardarr/disks/member-b",
            "/mnt/hoardarr/disks/new-member",
        ],
    ) == [
        "/mnt/hoardarr/disks/member-a",
        "/mnt/hoardarr/disks/member-b",
    ]
    with pytest.raises(ExecutorFailure, match="could not be tied"):
        executor._normalize_runtime_mergerfs_branches(
            ["member-a"],
            ["/one/member-a", "/two/member-a"],
        )


def test_existing_mergerfs_expansion_preserves_mount_and_persists_one_updated_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation_id = "11111111-1111-4111-8111-111111111111"
    public_combined = tmp_path / "managed" / "data" / "media"
    configured_combined = tmp_path / "managed" / "mnt" / "hoardarr" / "media"
    new_member = tmp_path / "mounts" / document_hash(DEVICE_ID)[:16]
    paths = Paths(
        transaction_root=tmp_path / "transactions",
        fstab=tmp_path / "fstab",
        mount_root=tmp_path / "mounts",
    )
    paths.fstab.write_text(
        f"/mnt/member-a:/mnt/member-b {configured_combined} fuse.mergerfs "
        "category.create=mfs,category.search=ff,use_ino,nofail 0 0\n",
        encoding="utf-8",
    )
    live = {
        **_live_disk(),
        "partitions": [
            {
                "kernel_path": "/dev/sdz1",
                "filesystem": {"type": "ext4", "uuid": "member-uuid"},
            }
        ],
    }
    document = {
        "presentation_root": "/data/media",
        "actions": {"directories": [], "connectivity": []},
        "storage": {
            "topology": "mergerfs",
            "selected_devices": [_selected_device()],
            "actions": [
                {
                    "action_id": "storage-layout",
                    "type": "storage.layout.ensure",
                    "topology": "mergerfs",
                    "device_ids": [DEVICE_ID],
                    "purpose": "media",
                    "destructive": False,
                }
            ],
            "format": {"mount_options": [], "trim": {"enabled": False}},
            "mergerfs": {
                "mode": "existing",
                "instance_id": "mergerfs:0123456789abcdef",
                "name": "media",
                "mountpoint": "/data/media",
            },
        },
    }
    commands: list[list[str]] = []
    expansion_targets: list[str] = []

    def safe_test_mountpoint(value: str) -> Path:
        candidate = Path(value)
        if candidate.is_absolute() and candidate.is_relative_to(tmp_path):
            return candidate
        return tmp_path / "managed" / value.lstrip("/")

    monkeypatch.setattr(executor, "_revalidate", lambda *_args: {DEVICE_ID: live})
    monkeypatch.setattr(
        executor,
        "_safe_mountpoint",
        safe_test_mountpoint,
    )
    monkeypatch.setattr(
        executor,
        "_blkid_value",
        lambda _partition, field: "ext4" if field == "TYPE" else "member-uuid",
    )
    monkeypatch.setattr(executor, "_tool", lambda name: name)
    monkeypatch.setattr(
        executor,
        "mergerfs_expand_commands",
        lambda mountpoint, _branches: (
            expansion_targets.append(mountpoint)
            or [
                CommandSpec(
                    ("setfattr", "-n", "user.mergerfs.branches", "runtime"),
                    120,
                    "expand",
                )
            ]
        ),
    )
    monkeypatch.setattr(
        executor,
        "discover_mergerfs",
        lambda **_kwargs: {
            "items": [
                {
                    "id": "mergerfs:0123456789abcdef",
                    "mountpoint": str(public_combined),
                    "source": "/mnt/member-a:/mnt/member-b",
                    "branches": ["/mnt/member-a", "/mnt/member-b"],
                    "options": ["category.create=mfs", "category.search=ff", "use_ino"],
                    "active": True,
                    "configured": False,
                },
                {
                    "id": "mergerfs:fedcba9876543210",
                    "mountpoint": str(configured_combined),
                    "source": "/mnt/member-a:/mnt/member-b",
                    "branches": ["/mnt/member-a", "/mnt/member-b"],
                    "options": ["category.create=mfs", "category.search=ff", "use_ino"],
                    "active": True,
                    "configured": True,
                },
            ]
        },
    )
    result = executor._execute_actions(
        operation_id=operation_id,
        document=document,
        paths=paths,
        inventory_provider=lambda: {"disks": [live]},
        runner=lambda command, _timeout: commands.append(command),
        journal={"completed_steps": 0, "notices": []},
    )

    content = paths.fstab.read_text(encoding="utf-8")
    assert content.count("fuse.mergerfs") == 1
    assert (
        f"/mnt/member-a:/mnt/member-b:{executor._fstab_encode(str(new_member))} "
        f"{configured_combined}"
        in content
    )
    assert f"UUID=member-uuid {new_member} ext4 defaults 0 2" in content
    assert not any(command[0].startswith("mkfs") for command in commands)
    assert [command[0] for command in commands].count("setfattr") == 1
    assert expansion_targets == [str(configured_combined)]
    assert result["mountpoint"] == str(public_combined)


def test_storage_resume_checkpoints_post_layout_work_without_progress_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation_id = "11111111-1111-4111-8111-111111111111"
    combined = tmp_path / "managed" / "data"
    new_member = tmp_path / "mounts" / document_hash(DEVICE_ID)[:16]
    paths = Paths(
        transaction_root=tmp_path / "transactions",
        fstab=tmp_path / "fstab",
        mount_root=tmp_path / "mounts",
        managed_udev_rule=tmp_path / "managed.rules",
        managed_storage_state=tmp_path / "managed.json",
    )
    paths.fstab.write_text(
        f"/mnt/member-a:/mnt/member-b {combined} fuse.mergerfs "
        "category.create=mfs,category.search=ff,use_ino,nofail 0 0\n",
        encoding="utf-8",
    )
    live = {
        **_live_disk(),
        "partitions": [
            {
                "kernel_path": "/dev/sdz1",
                "filesystem": {"type": "ext4", "uuid": "member-uuid"},
                "mountpoints": [str(new_member)],
            }
        ],
    }
    document = {
        "presentation_root": "/data",
        "actions": {"directories": [], "connectivity": []},
        "storage": {
            "topology": "mergerfs",
            "selected_devices": [_selected_device()],
            "actions": [
                {
                    "action_id": "storage-layout",
                    "type": "storage.layout.ensure",
                    "topology": "mergerfs",
                    "device_ids": [DEVICE_ID],
                    "purpose": "media",
                    "destructive": False,
                }
            ],
            "format": {"mount_options": [], "trim": {"enabled": False}},
            "mergerfs": {
                "mode": "existing",
                "instance_id": "mergerfs:0123456789abcdef",
                "name": "data",
                "mountpoint": "/data",
            },
        },
    }
    journal = {
        "completed_steps": 0,
        "total_steps": 6,
        "completed_actions": [],
        "notices": [],
    }
    commands: list[list[str]] = []
    fail_trigger = True

    def run(command: list[str], _timeout: int) -> None:
        nonlocal fail_trigger
        commands.append(command)
        if fail_trigger and command[:2] == ["udevadm", "trigger"]:
            fail_trigger = False
            raise ExecutorFailure("storage_tool_failed", "trigger failed", needs_attention=True)

    monkeypatch.setattr(executor, "_revalidate", lambda *_args: {DEVICE_ID: live})
    monkeypatch.setattr(executor, "_resume_revalidate", lambda *_args: {DEVICE_ID: live})
    monkeypatch.setattr(
        executor,
        "_safe_mountpoint",
        lambda value: tmp_path / "managed" / value.lstrip("/"),
    )
    monkeypatch.setattr(
        executor,
        "_blkid_value",
        lambda _partition, field: "ext4" if field == "TYPE" else "member-uuid",
    )
    monkeypatch.setattr(executor, "_tool", lambda name: name)
    monkeypatch.setattr(
        executor,
        "mergerfs_expand_commands",
        lambda _mountpoint, _branches: [CommandSpec(("setfattr", "runtime"), 120, "expand")],
    )
    monkeypatch.setattr(
        executor,
        "discover_mergerfs",
        lambda **_kwargs: {
            "items": [
                {
                    "id": "mergerfs:0123456789abcdef",
                    "mountpoint": str(combined),
                    "source": "/mnt/member-a:/mnt/member-b",
                    "branches": ["/mnt/member-a", "/mnt/member-b"],
                    "options": ["category.create=mfs", "category.search=ff", "use_ino"],
                    "active": True,
                    "configured": True,
                }
            ]
        },
    )

    with pytest.raises(ExecutorFailure, match="trigger failed"):
        executor._execute_actions(
            operation_id=operation_id,
            document=document,
            paths=paths,
            inventory_provider=lambda: {"disks": [live]},
            runner=run,
            journal=journal,
        )
    assert journal["completed_steps"] == 5
    assert journal["completed_steps"] <= journal["total_steps"]
    assert "runtime:fstab" in journal["completed_actions"]
    assert "runtime:managed-drive-allowlist" not in journal["completed_actions"]
    assert [command[0] for command in commands].count("setfattr") == 1

    result = executor._execute_actions(
        operation_id=operation_id,
        document=document,
        paths=paths,
        inventory_provider=lambda: {"disks": [live]},
        runner=run,
        journal=journal,
        resume=True,
    )
    assert result["replayed"] is False
    assert journal["completed_steps"] == journal["total_steps"]
    assert "runtime:managed-drive-allowlist" in journal["completed_actions"]
    assert [command[0] for command in commands].count("setfattr") == 1


@pytest.mark.parametrize(
    ("role", "fail_command"),
    [("data", None), ("parity", None), ("data", "status"), ("data", "sync")],
)
def test_existing_mergerfs_snapraid_expansion_applies_explicit_role(
    role: str,
    fail_command: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_id = "11111111-1111-4111-8111-111111111111"
    combined = tmp_path / "managed" / "data" / "media"
    new_member = tmp_path / "mounts" / document_hash(DEVICE_ID)[:16]
    config_root = tmp_path / "snapraid"
    config_root.mkdir()
    config_path = config_root / "media.conf"
    original = (
        "parity /mnt/parity/snapraid.parity\n"
        "content /mnt/member-a/snapraid.content\n"
        "data d1 /mnt/member-a\n"
    )
    config_path.write_text(original, encoding="utf-8")
    paths = Paths(
        transaction_root=tmp_path / "transactions",
        fstab=tmp_path / "fstab",
        mount_root=tmp_path / "mounts",
        snapraid_config_root=config_root,
    )
    paths.fstab.write_text(
        f"/mnt/member-a:/mnt/member-b {combined} fuse.mergerfs "
        "category.create=mfs,category.search=ff,use_ino,nofail 0 0\n",
        encoding="utf-8",
    )
    live = {
        **_live_disk(),
        "partitions": [
            {
                "kernel_path": "/dev/sdz1",
                "filesystem": {"type": "ext4", "uuid": "member-uuid"},
            }
        ],
    }
    document = {
        "presentation_root": "/data/media",
        "actions": {"directories": [], "connectivity": []},
        "storage": {
            "topology": "mergerfs",
            "selected_devices": [_selected_device()],
            "actions": [
                {
                    "action_id": "storage-layout",
                    "type": "storage.layout.ensure",
                    "topology": "mergerfs",
                    "device_ids": [DEVICE_ID],
                    "purpose": "media",
                    "destructive": False,
                }
            ],
            "format": {"mount_options": [], "trim": {"enabled": False}},
            "mergerfs": {
                "mode": "existing",
                "instance_id": "mergerfs:0123456789abcdef",
                "name": "media",
                "mountpoint": "/data/media",
            },
            "expansion": {
                "kind": "add_snapraid_parity" if role == "parity" else "add_mergerfs_member",
                "configuration": {
                    "topology": "mergerfs",
                    "snapraid_role": role,
                    "snapraid_instance_id": "snapraid:media",
                    "snapraid_config_sha256": hashlib.sha256(original.encode()).hexdigest(),
                },
            },
        },
    }
    commands: list[list[str]] = []
    monkeypatch.setattr(executor, "_revalidate", lambda *_args: {DEVICE_ID: live})
    monkeypatch.setattr(
        executor,
        "_safe_mountpoint",
        lambda value: tmp_path / "managed" / value.lstrip("/"),
    )
    monkeypatch.setattr(
        executor,
        "_blkid_value",
        lambda _partition, field: "ext4" if field == "TYPE" else "member-uuid",
    )
    monkeypatch.setattr(executor, "_tool", lambda name: name)
    monkeypatch.setattr(
        executor,
        "snapraid_expand_config",
        lambda content, *, role, mountpoint: snapraid_expand_config(
            content,
            role=role,
            mountpoint="/mnt/hoardarr/new-member",
        ),
    )
    monkeypatch.setattr(
        executor,
        "mergerfs_expand_commands",
        lambda _mountpoint, _branches: [CommandSpec(("setfattr", "runtime"), 120, "expand")],
    )
    monkeypatch.setattr(
        executor,
        "discover_mergerfs",
        lambda **_kwargs: {
            "items": [
                {
                    "id": "mergerfs:0123456789abcdef",
                    "mountpoint": str(combined),
                    "branches": ["/mnt/member-a", "/mnt/member-b"],
                    "options": ["category.create=mfs", "category.search=ff", "use_ino"],
                    "active": True,
                    "configured": True,
                }
            ]
        },
    )

    def run(command: list[str], _timeout: int) -> None:
        commands.append(command)
        if fail_command and command[0] == "snapraid" and command[-1] == fail_command:
            raise RuntimeError(f"injected SnapRAID {fail_command} failure")

    if fail_command:
        expected_error = executor.ExecutorFailure if fail_command == "sync" else RuntimeError
        with pytest.raises(expected_error) as failure:
            executor._execute_actions(
                operation_id=operation_id,
                document=document,
                paths=paths,
                inventory_provider=lambda: {"disks": [live]},
                runner=run,
                journal={"completed_steps": 0, "notices": []},
            )
        if fail_command == "sync":
            assert failure.value.code == "snapraid_sync_incomplete"
            assert failure.value.needs_attention is True
            assert " /mnt/hoardarr/new-member" in config_path.read_text(encoding="utf-8")
            assert [command[0] for command in commands].count("setfattr") == 1
            assert str(new_member) in paths.fstab.read_text(encoding="utf-8")
        else:
            assert config_path.read_text(encoding="utf-8") == original
            assert not any(command[0] == "setfattr" for command in commands)
            assert str(new_member) not in paths.fstab.read_text(encoding="utf-8")
        return

    executor._execute_actions(
        operation_id=operation_id,
        document=document,
        paths=paths,
        inventory_provider=lambda: {"disks": [live]},
        runner=run,
        journal={"completed_steps": 0, "notices": []},
    )

    updated = config_path.read_text(encoding="utf-8")
    assert [command[-1] for command in commands if command[0] == "snapraid"] == [
        "status",
        "sync",
    ]
    if role == "data":
        assert " /mnt/hoardarr/new-member" in updated
        assert any(command[0] == "setfattr" for command in commands)
        assert str(new_member) in paths.fstab.read_text(encoding="utf-8")
    else:
        assert "2-parity /mnt/hoardarr/new-member/snapraid.parity" in updated
        parity_sync = next(
            command for command in commands if command[0] == "snapraid" and command[-1] == "sync"
        )
        assert "--force-full" in parity_sync
        assert not any(command[0] == "setfattr" for command in commands)
        mergerfs_line = next(
            line
            for line in paths.fstab.read_text(encoding="utf-8").splitlines()
            if "fuse.mergerfs" in line
        )
        assert str(new_member) not in mergerfs_line


def test_existing_zfs_expansion_adds_vdev_without_recreating_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation_id = "22222222-2222-4222-8222-222222222222"
    device_ids = ["wwn:zfs-new-a", "wwn:zfs-new-b"]
    live_devices = {
        identity: {
            **_live_disk(),
            "id": identity,
            "stable_identity": identity,
            "kernel_path": f"/dev/sd{suffix}",
        }
        for identity, suffix in zip(device_ids, ("y", "z"), strict=True)
    }
    expected_digest = "a" * 64
    base_state = {
        "pool_guid": "1234567890123456789",
        "config_sha256": expected_digest,
        "vdev_type": "mirror",
        "vdev_width": 2,
        "vdev_count": 1,
    }
    states = [base_state, base_state, {**base_state, "config_sha256": "b" * 64, "vdev_count": 2}]
    document = {
        "presentation_root": "/data",
        "actions": {"directories": [], "connectivity": []},
        "storage": {
            "topology": "zfs",
            "selected_devices": [
                {**_selected_device(), "id": identity, "stable_identity": identity}
                for identity in device_ids
            ],
            "actions": [
                {
                    "action_id": "storage-layout",
                    "type": "storage.layout.ensure",
                    "topology": "zfs",
                    "device_ids": device_ids,
                    "purpose": "media",
                    "destructive": True,
                }
            ],
            "format": {"mount_options": [], "trim": {"enabled": False}},
            "layout_options": {
                "name": "media",
                "mountpoint": "/data",
                "vdevs": [{"type": "mirror", "device_ids": device_ids}],
                "ashift": 12,
                "recordsize": "1M",
                "compression": "lz4",
                "scrub_schedule": "monthly",
                "snapshots": {"enabled": False, "retention": 0},
            },
            "expansion": {
                "kind": "add_zfs_vdev",
                "target": {
                    "provider": "zfs",
                    "instance_id": "zfs:media",
                    "mountpoint": "/data",
                },
                "configuration": {
                    "topology": "zfs",
                    "vdev_type": "mirror",
                    "vdev_width": 2,
                    "zfs_pool_guid": "1234567890123456789",
                    "zfs_config_sha256": expected_digest,
                    "zfs_vdev_count": 1,
                },
            },
        },
    }
    commands: list[list[str]] = []
    paths = Paths(transaction_root=tmp_path / "transactions", fstab=tmp_path / "fstab")
    monkeypatch.setattr(executor, "_revalidate", lambda *_args: live_devices)
    monkeypatch.setattr(executor, "_safe_mountpoint", lambda _value: tmp_path / "managed")
    monkeypatch.setattr(
        executor,
        "_stable_path",
        lambda _paths, disk: PurePosixPath(f"/dev/disk/by-id/scsi-{disk['id'].rsplit('-', 1)[-1]}"),
    )
    monkeypatch.setattr(executor, "_tool", lambda name: name)

    result = executor._execute_actions(
        operation_id=operation_id,
        document=document,
        paths=paths,
        inventory_provider=lambda: {"disks": list(live_devices.values())},
        runner=lambda command, _timeout: commands.append(command),
        journal={"completed_steps": 0, "notices": []},
        zfs_state_provider=lambda _pool: states.pop(0),
    )

    zpool_commands = [command for command in commands if command[0] == "zpool"]
    assert zpool_commands[0][:4] == ["zpool", "add", "-n", "media"]
    assert zpool_commands[1][:3] == ["zpool", "add", "media"]
    assert "-f" not in zpool_commands[1]
    assert all(command[1] != "create" for command in zpool_commands)
    assert result["mountpoint"] == str(tmp_path / "managed")
    assert states == []


def test_existing_zfs_expansion_fails_before_commands_on_pool_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device_ids = ["wwn:zfs-new-a", "wwn:zfs-new-b"]
    live = {identity: {**_live_disk(), "id": identity} for identity in device_ids}
    document = {
        "presentation_root": "/data",
        "actions": {"directories": [], "connectivity": []},
        "storage": {
            "topology": "zfs",
            "selected_devices": [],
            "actions": [],
            "format": {"mount_options": [], "trim": {"enabled": False}},
            "layout_options": {
                "name": "media",
                "mountpoint": "/data",
                "vdevs": [{"type": "mirror", "device_ids": device_ids}],
            },
            "expansion": {
                "kind": "add_zfs_vdev",
                "target": {"provider": "zfs", "instance_id": "zfs:media", "mountpoint": "/data"},
                "configuration": {
                    "vdev_type": "mirror",
                    "vdev_width": 2,
                    "zfs_pool_guid": "1234567890123456789",
                    "zfs_config_sha256": "a" * 64,
                    "zfs_vdev_count": 1,
                },
            },
        },
    }
    commands: list[list[str]] = []
    monkeypatch.setattr(executor, "_revalidate", lambda *_args: live)
    monkeypatch.setattr(executor, "_safe_mountpoint", lambda _value: tmp_path / "managed")
    monkeypatch.setattr(
        executor, "_stable_path", lambda *_args: PurePosixPath("/dev/disk/by-id/scsi-x")
    )
    monkeypatch.setattr(executor, "_tool", lambda name: name)

    with pytest.raises(executor.ExecutorFailure, match="changed after review") as failure:
        executor._execute_actions(
            operation_id="33333333-3333-4333-8333-333333333333",
            document=document,
            paths=Paths(transaction_root=tmp_path / "transactions"),
            inventory_provider=lambda: {"disks": list(live.values())},
            runner=lambda command, _timeout: commands.append(command),
            journal={"completed_steps": 0, "notices": []},
            zfs_state_provider=lambda _pool: {
                "pool_guid": "9999999999999999999",
                "config_sha256": "a" * 64,
                "vdev_type": "mirror",
                "vdev_width": 2,
                "vdev_count": 1,
            },
        )
    assert failure.value.code == "zfs_pool_changed"
    assert commands == []


@pytest.mark.parametrize(
    ("device", "partition"),
    [
        ("/dev/disk/by-id/wwn-test", "/dev/disk/by-id/wwn-test-part1"),
        ("/tmp/by-id/wwn-test", "/tmp/by-id/wwn-test-part1"),
        ("/dev/nvme0n1", "/dev/nvme0n1p1"),
        ("/dev/sdz", "/dev/sdz1"),
    ],
)
def test_partition_path_uses_linux_names_for_stable_device_aliases(
    device: str, partition: str
) -> None:
    assert executor._partition_path(Path(device)) == Path(partition)


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
    fstab_content = paths.fstab.read_text(encoding="utf-8")
    assert "fuse.mergerfs" in fstab_content
    assert "allow_other" in fstab_content


def test_mergerfs_periodic_trim_never_installs_snapraid_timers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = ["serial:test:member-a", "serial:test:member-b"]
    live = {
        identifier: {
            **_live_disk(f"/dev/sd{letter}"),
            "id": identifier,
            "partitions": [
                {
                    "kernel_path": f"/dev/sd{letter}1",
                    "filesystem": {"type": "ext4", "uuid": f"uuid-{letter}"},
                }
            ],
        }
        for identifier, letter in zip(ids, ("y", "z"), strict=True)
    }
    document = {
        "presentation_root": "/data",
        "actions": {"directories": [], "connectivity": []},
        "storage": {
            "topology": "mergerfs",
            "actions": [
                {
                    "action_id": f"identity:{identifier}",
                    "type": "drive.identity.verify",
                    "device_id": identifier,
                    "destructive": False,
                }
                for identifier in ids
            ],
            "format": {
                "filesystem": "ext4",
                "mount_options": ["noatime"],
                "trim": {"enabled": True, "mode": "periodic"},
            },
            "mergerfs": {
                "mode": "create",
                "mountpoint": "/data",
                "create_policy": "mfs",
                "search_policy": "ff",
            },
        },
    }
    paths = Paths(
        transaction_root=tmp_path / "transactions",
        fstab=tmp_path / "fstab",
        mount_root=tmp_path / "mounts",
        systemd_unit_root=tmp_path / "systemd",
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(executor, "_revalidate", lambda *_args: live)
    monkeypatch.setattr(
        executor,
        "_safe_mountpoint",
        lambda value: tmp_path / "managed" / value.lstrip("/"),
    )
    monkeypatch.setattr(executor, "_tool", lambda name: name)
    monkeypatch.setattr(executor, "_discard_supported", lambda _disk: True)
    monkeypatch.setattr(
        executor,
        "_blkid_value",
        lambda path, field: "ext4" if field == "TYPE" else f"uuid-{path.stem}",
    )

    result = executor._execute_actions(
        operation_id="11111111-1111-4111-8111-111111111111",
        document=document,
        paths=paths,
        inventory_provider=lambda: {"disks": list(live.values())},
        runner=lambda command, _timeout: commands.append(command),
        journal={"completed_steps": 0, "notices": []},
    )

    assert result["topology"] == "mergerfs"
    mergerfs_command = next(command for command in commands if command[0] == "mergerfs")
    assert "allow_other" in mergerfs_command[2]
    assert "allow_other" in paths.fstab.read_text(encoding="utf-8")
    assert list(paths.systemd_unit_root.glob("hoardarr-fstrim-*.timer"))
    assert not list(paths.systemd_unit_root.glob("hoardarr-snapraid-*.timer"))


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


def test_directory_access_includes_intermediate_folders_and_ignores_umask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    presentation = tmp_path / "data"
    presentation.mkdir()
    applied: list[tuple[Path, int]] = []
    monkeypatch.setattr(executor, "_service_account_group_id", lambda username: 1234)
    monkeypatch.setattr(
        executor,
        "_apply_directory_mode",
        lambda path, group_id: applied.append((path, group_id)),
    )
    monkeypatch.setattr(
        executor,
        "_safe_mountpoint",
        lambda value: presentation / PurePosixPath(value).relative_to("/data"),
    )

    previous = os.umask(0o077)
    try:
        result = executor._ensure_storage_directory_access(
            presentation,
            [
                {
                    "type": "directory.ensure",
                    "path": "/data/downloads/torrents/incomplete",
                }
            ],
            "media",
        )
    finally:
        os.umask(previous)

    assert result == [
        str(presentation / "downloads"),
        str(presentation / "downloads" / "torrents"),
        str(presentation / "downloads" / "torrents" / "incomplete"),
    ]
    assert applied == [(Path(path), 1234) for path in result]


def test_mergerfs_branch_traversal_is_group_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mount_root = tmp_path / "hoardarr" / "disks"
    applied: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(executor, "_service_account_group_id", lambda username: 1234)
    monkeypatch.setattr(
        executor,
        "_apply_directory_mode",
        lambda path, group_id, *, mode=0o770: applied.append((path, group_id, mode)),
    )

    result = executor._ensure_mergerfs_branch_traversal(mount_root, "media")

    assert result == [str(mount_root.parent), str(mount_root)]
    assert applied == [
        (mount_root.parent, 1234, 0o710),
        (mount_root, 1234, 0o710),
    ]


def test_access_reconciliation_is_bound_to_hashed_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = {
        "presentation_root": "/data",
        "storage": {
            "topology": "mergerfs",
            "mergerfs": {"mountpoint": "/mnt/hoardarr/media"},
            "service_account": {"username": "media"},
        },
        "actions": {
            "directories": [{"type": "directory.ensure", "path": "/data/media/Movies"}]
        },
    }
    operation_id = "11111111-1111-4111-8111-111111111111"
    request = {
        "operation": "reconcile_storage_access",
        "operation_id": operation_id,
        "plan_sha256": document_hash(document),
        "document": document,
    }
    presentation = tmp_path / "data"
    presentation.mkdir()
    monkeypatch.setattr(executor, "_safe_mountpoint", lambda _value: presentation)
    monkeypatch.setattr(Path, "is_mount", lambda _path: True)
    monkeypatch.setattr(
        executor,
        "_ensure_storage_directory_access",
        lambda root, actions, username: [f"{root}/media", f"{root}/media/Movies"],
    )
    monkeypatch.setattr(
        executor,
        "_ensure_mergerfs_branch_traversal",
        lambda root, username: [str(root.parent), str(root)],
    )
    fstab = tmp_path / "fstab"
    fstab.write_text(
        f"/mnt/a:/mnt/b {presentation} fuse.mergerfs category.create=mfs,nofail 0 0\n",
        encoding="utf-8",
    )

    result = reconcile_storage_access(request, paths=Paths(fstab=fstab))
    assert result["operation_id"] == operation_id
    assert result["username"] == "media"
    assert result["directories_reconciled"] == [
        f"{presentation}/media",
        f"{presentation}/media/Movies",
    ]
    assert result["mount_configuration_updated"] is True
    assert result["activation"] == "next_mount"
    assert result["branch_roots_reconciled"] == [
        str(Paths().mount_root.parent),
        str(Paths().mount_root),
    ]
    assert "allow_other" in fstab.read_text(encoding="utf-8")

    changed = deepcopy(request)
    changed["document"]["presentation_root"] = "/srv/different"
    with pytest.raises(ExecutorFailure) as failure:
        reconcile_storage_access(changed, paths=Paths(fstab=fstab))
    assert failure.value.code == "storage_access_request_invalid"


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


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/etc/storage",
        "/var/lib/hoardarr/data",
        "/tmp/data",
        "/mnt/media\n/dev/sdz /root none bind 0 0",
        "/mnt/media with-spaces",
        "/mnt/media\\escape",
    ],
)
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


def test_existing_mergerfs_expansion_requires_setfattr_before_any_storage_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document()
    storage = document["storage"]
    assert isinstance(storage, dict)
    storage["topology"] = "mergerfs"
    storage["mergerfs"] = {
        "mode": "existing",
        "instance_id": "mergerfs:0123456789abcdef",
        "name": "data",
        "mountpoint": "/data",
    }
    storage["expansion"] = {"kind": "add_mergerfs_member"}
    requested: list[str] = []

    def missing(name: str) -> str:
        requested.append(name)
        raise ExecutorFailure(
            "storage_tool_missing", f"A required storage tool is unavailable: {name}."
        )

    monkeypatch.setattr(executor, "_tool", missing)
    with pytest.raises(ExecutorFailure) as failure:
        executor._preflight_storage_tools(document)
    assert failure.value.code == "storage_tool_missing"
    assert requested == ["setfattr"]


def test_storage_build_resumes_exact_needs_attention_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _document()
    request = _request(document)
    request["operation"] = "resume_storage_plan"
    paths = Paths(
        quarantine_marker=tmp_path / "quarantine.json",
        transaction_root=tmp_path / "transactions",
        lock_root=tmp_path / "locks",
    )
    paths.transaction_root.mkdir(mode=0o700)
    journal = {
        "schema_version": 1,
        "operation_id": request["operation_id"],
        "plan_sha256": request["plan_sha256"],
        "state": "needs_attention",
        "started_at": 1.0,
        "updated_at": 2.0,
        "completed_actions": [f"identity:{DEVICE_ID}"],
        "completed_steps": 1,
        "total_steps": 6,
        "notices": [],
        "action_results": [],
        "current_action": {"id": "layout", "type": "storage.layout.apply"},
    }
    (paths.transaction_root / f"{request['operation_id']}.json").write_text(
        json.dumps(journal), encoding="utf-8"
    )
    monkeypatch.setattr(executor, "validate_quarantine", lambda _marker: {"ready": True})
    monkeypatch.setattr(executor, "_device_locks", lambda _paths, _ids: nullcontext())
    monkeypatch.setattr(
        executor,
        "_resume_revalidate",
        lambda *_args: {DEVICE_ID: _live_disk()},
    )
    observed: dict[str, object] = {}

    def execute(**kwargs: object) -> dict[str, object]:
        observed.update(kwargs)
        return {
            "operation_id": request["operation_id"],
            "topology": "individual",
            "selected_device_ids": [DEVICE_ID],
            "replayed": False,
        }

    monkeypatch.setattr(executor, "_execute_actions", execute)
    result = apply_storage_plan(request, paths=paths, inventory_provider=lambda: {"disks": []})
    assert observed["resume"] is True
    resumed_journal = observed["journal"]
    assert isinstance(resumed_journal, dict)
    assert resumed_journal["completed_actions"] == [f"identity:{DEVICE_ID}"]
    assert resumed_journal["notices"][-1]["code"] == "storage_build_resumed"
    assert result["operation_id"] == request["operation_id"]
    status = storage_operation_status(str(request["operation_id"]), paths=paths)
    assert status["state"] == "succeeded"


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


def test_smart_action_reports_drive_estimate_progress_and_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            "# 1 Short offline Completed without error",
            "Please wait 10 minutes for test to complete.",
            "Self-test routine in progress... 90% of test remaining.",
            "Self-test status: complete",
            "# 1 Short offline Completed without error",
        ]
    )
    progress: list[dict[str, object]] = []
    monkeypatch.setattr(executor, "_tool", lambda name: f"/usr/sbin/{name}")
    monkeypatch.setattr(executor, "_smartctl", lambda _command, **_kwargs: next(responses))
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    result = _run_smart_test(Path("/dev/sdz"), "short", progress_callback=progress.append)

    assert result["outcome"] == "passed"
    assert result["test_kind"] == "short"
    assert progress[0]["estimated_seconds_remaining"] == 600
    assert progress[1]["percent"] == 10.0
    assert progress[-1]["state"] == "passed"
    assert progress[-1]["percent"] == 100.0


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
