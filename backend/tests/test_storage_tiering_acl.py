from __future__ import annotations

import os
from pathlib import Path

import pytest

from hoardarr.storage.acl import AclError, acl_commands, assert_no_symlink, normalize_acl
from hoardarr.storage.tiering import (
    TieringError,
    cleanup_retained_transfer,
    execute_transfer,
    plan_transfer,
    transfer_phases,
)


def test_torrent_policy_hardlinks_only_on_same_filesystem() -> None:
    plan = plan_transfer(
        {
            "workload": "torrent",
            "source": "/mnt/hoardarr/cache/file.mkv",
            "destination": "/mnt/hoardarr/media/Movies/file.mkv",
            "source_identity": "cache",
            "destination_identity": "media",
            "same_filesystem": True,
            "method": "auto",
            "required_bytes": 12,
        }
    )
    assert plan.method == "hardlink"
    assert plan.retain_until == "seeding_complete"
    assert transfer_phases(plan)[-2:] == ["retain_while_seeding", "cleanup"]
    with pytest.raises(TieringError, match="hardlinks cannot cross"):
        plan_transfer({**plan.document(), "method": "hardlink", "same_filesystem": False})


def test_usenet_policy_is_move_after_repair_unpack_and_verify() -> None:
    plan = plan_transfer(
        {
            "workload": "usenet",
            "source": "/mnt/hoardarr/cache/job",
            "destination": "/mnt/hoardarr/media/TV/job",
            "source_identity": "cache",
            "destination_identity": "media",
            "same_filesystem": False,
            "required_bytes": 100,
            "completed_steps": ["download", "repair", "unpack", "verify"],
        }
    )
    assert plan.method == "move"
    assert plan.completed_steps == ("download", "repair", "unpack", "verify")
    assert transfer_phases(plan) == [
        "verify_source",
        "copy_or_move",
        "verify_destination",
        "cleanup",
    ]
    with pytest.raises(TieringError, match="requires download, repair, unpack"):
        plan_transfer(
            {
                **plan.document(),
                "completed_steps": ["download", "repair"],
                "sha256": None,
            }
        )


def test_transfer_revalidates_identity_and_space(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "out" / "destination"
    source.write_bytes(b"payload")
    plan = plan_transfer(
        {
            "workload": "usenet",
            "source": "/mnt/hoardarr/cache/source",
            "destination": "/mnt/hoardarr/media/destination",
            "source_identity": "a",
            "destination_identity": "b",
            "required_bytes": 7,
            "completed_steps": ["download", "repair", "unpack", "verify"],
        }
    )
    object.__setattr__(plan, "source", str(source))
    object.__setattr__(plan, "destination", str(destination))
    with pytest.raises(TieringError, match="identity changed"):
        execute_transfer(
            plan, identity_provider=lambda _path: "wrong", free_space_provider=lambda _path: 100
        )
    with pytest.raises(TieringError, match="insufficient"):
        execute_transfer(
            plan,
            identity_provider=lambda path: "a" if path == source else "b",
            free_space_provider=lambda _path: 0,
        )
    result = execute_transfer(
        plan,
        identity_provider=lambda path: "a" if path == source else "b",
        free_space_provider=lambda _path: 100,
    )
    assert result["state"] == "completed"
    assert destination.read_bytes() == b"payload"
    assert not source.exists()


def test_transfer_resumes_verified_partial_and_never_overwrites_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    output.mkdir()
    destination = output / "movie.mkv"
    source.write_bytes(b"abcdefgh")
    plan = plan_transfer(
        {
            "workload": "torrent",
            "source": "/data/downloads/movie.mkv",
            "destination": "/data/media/movie.mkv",
            "source_identity": "source",
            "destination_identity": "destination",
            "same_filesystem": False,
            "method": "copy",
            "retain_until": "seeding_complete",
            "required_bytes": 8,
            "completed_steps": ["download_complete"],
        }
    )
    object.__setattr__(plan, "source", str(source))
    object.__setattr__(plan, "destination", str(destination))
    partial = output / f".{destination.name}.hoardarr-{plan.sha256[:16]}.part"
    partial.write_bytes(b"abcd")

    def identity(path: Path) -> str:
        return "source" if path == source else "destination"

    result = execute_transfer(plan, identity_provider=identity, free_space_provider=lambda _path: 4)
    assert result["state"] == "retained"
    assert destination.read_bytes() == b"abcdefgh"
    assert source.exists()
    with pytest.raises(TieringError, match="destination already exists"):
        execute_transfer(plan, identity_provider=identity, free_space_provider=lambda _path: 100)
    cleaned = cleanup_retained_transfer(plan, identity_provider=identity)
    assert cleaned["source_removed"] is True


def test_transfer_rejects_staging_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    output.mkdir()
    source.write_bytes(b"payload")
    plan = plan_transfer(
        {
            "workload": "torrent",
            "source": "/data/downloads/source",
            "destination": "/data/media/destination",
            "source_identity": "source",
            "destination_identity": "destination",
            "same_filesystem": False,
            "method": "copy",
            "required_bytes": 7,
        }
    )
    destination = output / "destination"
    object.__setattr__(plan, "source", str(source))
    object.__setattr__(plan, "destination", str(destination))
    partial = output / f".{destination.name}.hoardarr-{plan.sha256[:16]}.part"
    partial.symlink_to(source)
    with pytest.raises(TieringError, match="symbolic link"):
        execute_transfer(
            plan,
            identity_provider=lambda path: "source" if path == source else "destination",
            free_space_provider=lambda _path: 100,
        )


@pytest.mark.skipif(os.name != "posix", reason="descriptor-relative Linux path test")
def test_transfer_does_not_replace_destination_created_during_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    destination_root = tmp_path / "destination"
    source_root.mkdir()
    destination_root.mkdir()
    source = source_root / "movie.bin"
    destination = destination_root / "movie.bin"
    source.write_bytes(b"source data")
    plan = plan_transfer(
        {
            "workload": "usenet",
            "source": "/data/downloads/movie.bin",
            "destination": "/data/media/movie.bin",
            "source_identity": "source-volume",
            "destination_identity": "destination-volume",
            "method": "copy",
            "retain_until": "never",
            "cleanup": False,
            "required_bytes": source.stat().st_size,
            "completed_steps": ["download", "repair", "unpack", "verify"],
        }
    )
    object.__setattr__(plan, "source", str(source))
    object.__setattr__(plan, "destination", str(destination))
    real_link = os.link

    def competing_link(source_name: str, destination_name: str, **kwargs: object) -> None:
        destination.write_bytes(b"competing data")
        real_link(source_name, destination_name, **kwargs)

    monkeypatch.setattr(os, "link", competing_link)
    with pytest.raises(TieringError) as error:
        execute_transfer(
            plan,
            identity_provider=lambda path: (
                "source-volume" if path == source else "destination-volume"
            ),
        )
    assert error.value.code == "transfer_io_failed"
    assert destination.read_bytes() == b"competing data"
    assert source.read_bytes() == b"source data"


def test_acl_maps_roles_and_prevents_unmanaged_paths() -> None:
    plan = normalize_acl(
        {
            "path": "/mnt/hoardarr/media",
            "entries": [
                {"kind": "group", "name": "admins", "role": "administrator"},
                {"kind": "user", "name": "media", "role": "media_application"},
                {"kind": "group", "name": "viewers", "role": "media_user"},
            ],
        }
    )
    commands = acl_commands(plan)
    assert "g:admins:rwx" in commands[0].argv[-2]
    assert "d:u:media:rwx" in commands[1].argv[-2]
    assert commands[-1].argv[0] == "getfacl"
    with pytest.raises(AclError):
        normalize_acl(
            {
                "path": "/etc",
                "entries": [{"kind": "user", "name": "media", "role": "media_application"}],
            }
        )


def test_acl_rejects_symlink_component(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / "real").mkdir()
    (managed / "link").symlink_to(managed / "real", target_is_directory=True)
    with pytest.raises(AclError, match="symlink"):
        assert_no_symlink(managed / "link" / "child", managed)
