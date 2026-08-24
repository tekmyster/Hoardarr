from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from hoardarr.storage import quarantine


def test_boot_volume_group_inspection_skips_absent_optional_efi_mount(
    monkeypatch,
) -> None:
    commands: list[list[str]] = []

    monkeypatch.setattr(
        Path,
        "exists",
        lambda path: not path.as_posix().endswith("/boot/efi"),
    )

    def command(argv: list[str], *, timeout: int = 60) -> str:
        del timeout
        commands.append(argv)
        if argv[0] == "findmnt":
            return "/dev/sda2\n"
        if argv[0] == "lvs":
            return ""
        raise AssertionError(argv)

    monkeypatch.setattr(quarantine, "_command", command)

    assert quarantine._boot_volume_groups() == []
    assert [argv[-1] for argv in commands if argv[0] == "findmnt"] == ["/", "/boot"]


def test_boot_volume_group_inspection_keeps_existing_efi_mount(
    monkeypatch,
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(Path, "exists", lambda _path: True)

    def command(argv: list[str], *, timeout: int = 60) -> str:
        del timeout
        commands.append(argv)
        if argv[0] == "findmnt":
            return "/dev/mapper/system-root\n"
        if argv[0] == "lvs":
            return "system|/dev/mapper/system-root\n"
        raise AssertionError(argv)

    monkeypatch.setattr(quarantine, "_command", command)

    assert quarantine._boot_volume_groups() == ["system"]
    assert [argv[-1] for argv in commands if argv[0] == "findmnt"] == [
        "/",
        "/boot",
        "/boot/efi",
    ]


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode and umask behavior")
def test_atomic_text_applies_requested_mode_despite_restrictive_umask(tmp_path: Path) -> None:
    target = tmp_path / "fstab"
    previous = os.umask(0o077)
    try:
        quarantine.atomic_text(target, "managed\n", mode=0o644)
    finally:
        os.umask(previous)

    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_managed_mergerfs_fstab_adds_member_dependencies() -> None:
    operation_id = "11111111-1111-4111-8111-111111111111"
    content = (
        f"# BEGIN HOARDARR {operation_id}\n"
        "UUID=member-a /mnt/hoardarr/disks/a ext4 noatime 0 2\n"
        "UUID=member-b /mnt/hoardarr/disks/b ext4 noatime 0 2\n"
        "/mnt/hoardarr/disks/a:/mnt/hoardarr/disks/b /data fuse.mergerfs "
        "category.create=mfs,nofail 0 0\n"
        f"# END HOARDARR {operation_id}\n"
    )

    normalized = quarantine._with_mergerfs_dependencies(content)

    assert "x-systemd.requires=/mnt/hoardarr/disks/a" in normalized
    assert "x-systemd.requires=/mnt/hoardarr/disks/b" in normalized
    assert quarantine._with_mergerfs_dependencies(normalized) == normalized


def test_managed_identity_state_is_bounded_to_valid_exact_udev_fields(tmp_path: Path) -> None:
    state = tmp_path / "state" / "managed-storage.json"
    rule = tmp_path / "udev" / "98-hoardarr-managed-storage.rules"

    first = quarantine.persist_managed_identities(
        [("ID_SERIAL_SHORT", "disk-a")], state_path=state, rule_path=rule
    )
    second = quarantine.persist_managed_identities(
        [("ID_SERIAL_SHORT", "disk-b"), ("ID_SERIAL_SHORT", "disk-a")],
        state_path=state,
        rule_path=rule,
    )

    assert first == [("ID_SERIAL_SHORT", "disk-a")]
    assert second == [("ID_SERIAL_SHORT", "disk-a"), ("ID_SERIAL_SHORT", "disk-b")]
    document = json.loads(state.read_text(encoding="utf-8"))
    assert len(document["identities"]) == 2
    policy = rule.read_text(encoding="utf-8")
    assert policy.count('ENV{SYSTEMD_READY}="1"') == 2
    assert 'ENV{DM_MULTIPATH_DEVICE_PATH}=="1"' in policy


@pytest.mark.skipif(os.name == "nt", reason="POSIX device links and mount paths")
def test_reconcile_managed_storage_releases_only_exact_managed_filesystems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation_id = "11111111-1111-4111-8111-111111111111"
    member = tmp_path / "mounts" / "member-a"
    pool = tmp_path / "mounts" / "pool"
    fstab = tmp_path / "fstab"
    fstab.write_text(
        f"# BEGIN HOARDARR {operation_id}\n"
        f"UUID=member-a {member} ext4 noatime 0 2\n"
        f"{member} {pool} fuse.mergerfs category.create=mfs,nofail 0 0\n"
        f"# END HOARDARR {operation_id}\n",
        encoding="utf-8",
    )
    device_root = tmp_path / "dev"
    by_uuid = device_root / "disk" / "by-uuid"
    by_uuid.mkdir(parents=True)
    partition = device_root / "sda1"
    partition.touch()
    (by_uuid / "member-a").symlink_to(partition)
    commands: list[list[str]] = []

    def command(argv: list[str], *, timeout: int = 60) -> str:
        del timeout
        commands.append(argv)
        if argv[0] == "lsblk":
            return "sda\n"
        if argv[:3] == ["udevadm", "info", "--query=property"]:
            return "ID_SERIAL_SHORT=disk-a\n"
        return ""

    monkeypatch.setattr(quarantine, "_command", command)
    result = quarantine.reconcile_managed_storage(
        fstab_path=fstab,
        state_path=tmp_path / "state.json",
        rule_path=tmp_path / "managed.rules",
        dev_by_uuid=by_uuid,
    )

    assert result["managed_filesystems"] == 1
    assert result["activated_mounts"] == 0
    assert member.is_dir()
    assert pool.is_dir()
    assert stat.S_IMODE(fstab.stat().st_mode) == 0o644
    assert "x-systemd.requires=" in fstab.read_text(encoding="utf-8")
    assert ["udevadm", "control", "--reload-rules"] in commands
    assert any(command[:2] == ["udevadm", "trigger"] for command in commands)
