from __future__ import annotations

from pathlib import Path

import pytest

from hoardarr.storage import mergerfs as mergerfs_module
from hoardarr.storage.mergerfs import discover_mergerfs


def test_mergerfs_discovery_combines_active_mount_and_fstab(tmp_path: Path) -> None:
    mountinfo = tmp_path / "mountinfo"
    fstab = tmp_path / "fstab"
    mountinfo.write_text(
        "36 25 0:32 / /mnt/combined rw,relatime - fuse.mergerfs "
        "/mnt/disk1:/mnt/disk\\0402 rw,allow_other,category.create=mfs\n",
        encoding="utf-8",
    )
    fstab.write_text(
        "/mnt/disk1:/mnt/disk\\0402 /mnt/combined fuse.mergerfs "
        "defaults,allow_other,category.create=mfs 0 0\n",
        encoding="utf-8",
    )

    inventory = discover_mergerfs(
        mountinfo_path=mountinfo,
        fstab_path=fstab,
        executable="/usr/bin/mergerfs",
    )

    assert inventory["available"] is True
    assert inventory["status"] == "configured"
    assert inventory["items"] == [
        {
            "id": inventory["items"][0]["id"],
            "name": "combined",
            "mountpoint": "/mnt/combined",
            "source": "/mnt/disk1:/mnt/disk 2",
            "branches": ["/mnt/disk1", "/mnt/disk 2"],
            "configured_source": "/mnt/disk1:/mnt/disk 2",
            "configured_branches": ["/mnt/disk1", "/mnt/disk 2"],
            "options": ["allow_other", "category.create=mfs", "defaults", "relatime", "rw"],
            "active": True,
            "configured": True,
        }
    ]
    assert str(inventory["items"][0]["id"]).startswith("mergerfs:")


def test_mergerfs_discovery_preserves_absolute_configured_branches(
    tmp_path: Path,
) -> None:
    mountinfo = tmp_path / "mountinfo"
    fstab = tmp_path / "fstab"
    mountinfo.write_text(
        "36 25 0:32 / /mnt/combined rw,relatime - fuse.mergerfs "
        "disk1:disk2 rw,allow_other,category.create=mfs\n",
        encoding="utf-8",
    )
    fstab.write_text(
        "/mnt/hoardarr/disks/disk1:/mnt/hoardarr/disks/disk2 /mnt/combined "
        "fuse.mergerfs defaults,allow_other,category.create=mfs 0 0\n",
        encoding="utf-8",
    )

    item = discover_mergerfs(
        mountinfo_path=mountinfo,
        fstab_path=fstab,
        executable="/usr/bin/mergerfs",
    )["items"][0]

    assert item["branches"] == ["disk1", "disk2"]
    assert item["configured_branches"] == [
        "/mnt/hoardarr/disks/disk1",
        "/mnt/hoardarr/disks/disk2",
    ]


def test_mergerfs_discovery_uses_dynamic_runtime_branch_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mountinfo = tmp_path / "mountinfo"
    fstab = tmp_path / "fstab"
    mountinfo.write_text(
        "36 25 0:32 / /mnt/combined rw - fuse.mergerfs /mnt/a:/mnt/b rw\n",
        encoding="utf-8",
    )
    fstab.write_text(
        "/mnt/a:/mnt/b:/mnt/c /mnt/combined fuse.mergerfs defaults 0 0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        mergerfs_module.os,
        "getxattr",
        lambda path, name: b"/mnt/a=RW:/mnt/b=RO:/mnt/c=NC"
        if str(path).replace("\\", "/").endswith("/mnt/combined/.mergerfs")
        and name == "user.mergerfs.branches"
        else (_ for _ in ()).throw(OSError("unexpected xattr")),
        raising=False,
    )

    item = discover_mergerfs(
        mountinfo_path=mountinfo,
        fstab_path=fstab,
        executable="/usr/bin/mergerfs",
    )["items"][0]

    assert item["branches"] == ["/mnt/a", "/mnt/b", "/mnt/c"]
    assert item["runtime_branches"] == ["/mnt/a", "/mnt/b", "/mnt/c"]
    assert item["runtime_branch_modes"] == {"/mnt/a": "RW", "/mnt/b": "RO", "/mnt/c": "NC"}
    assert item["runtime_source"] == "/mnt/a:/mnt/b"
    assert item["branch_evidence"] == "mergerfs runtime control xattr"


def test_mergerfs_discovery_reports_an_empty_unavailable_host(tmp_path: Path) -> None:
    inventory = discover_mergerfs(
        mountinfo_path=tmp_path / "missing-mountinfo",
        fstab_path=tmp_path / "missing-fstab",
        executable=None,
    )

    assert inventory["items"] == []
    assert inventory["status"] in {"unavailable", "available_not_configured"}
