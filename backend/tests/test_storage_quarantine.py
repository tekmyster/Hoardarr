from __future__ import annotations

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
