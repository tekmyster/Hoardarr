from __future__ import annotations

from pathlib import Path

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
