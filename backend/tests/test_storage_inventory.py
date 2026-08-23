from __future__ import annotations

from pathlib import Path

from hoardarr.storage import inventory


def test_live_inventory_reports_md_arrays_and_managed_shares(tmp_path: Path, monkeypatch) -> None:
    sys_block = tmp_path / "sys" / "class" / "block"
    md = sys_block / "md0" / "md"
    md.mkdir(parents=True)
    (md / "array_state").write_text("clean\n", encoding="utf-8")
    (md / "raid_disks").write_text("4\n", encoding="utf-8")
    (md / "degraded").write_text("0\n", encoding="utf-8")
    (md / "level").write_text("raid6\n", encoding="utf-8")
    samba = tmp_path / "hoardarr-shares.conf"
    samba.write_text("[media]\n    path = /data/media\n", encoding="utf-8")
    exports = tmp_path / "exports"
    exports.write_text("/data/media 10.0.0.0/8(ro)\n", encoding="utf-8")
    monkeypatch.setattr(inventory, "_command", lambda _name, _arguments: None)
    monkeypatch.setattr(
        inventory,
        "discover_mergerfs",
        lambda: {"available": True, "status": "available_not_configured", "items": []},
    )

    result = inventory.discover_storage_inventory(
        sys_class_block=sys_block,
        samba_config=samba,
        nfs_exports=exports,
        target_config=tmp_path / "targets.json",
    )

    assert result["pools"]["items"][0]["type"] == "Linux MD raid6"
    assert result["pools"]["items"][0]["status"] == "clean"
    assert [item["protocol"] for item in result["shares"]["items"]] == ["SMB", "NFS"]


def test_snapraid_inventory_exposes_bounded_role_evidence(tmp_path: Path, monkeypatch) -> None:
    config_root = tmp_path / "snapraid"
    config_root.mkdir()
    config = config_root / "media.conf"
    config.write_text(
        "\n".join(
            (
                "parity /srv/hoardarr/backends/parity/snapraid.parity",
                "2-parity /srv/hoardarr/backends/parity2/snapraid.parity",
                "content /var/lib/hoardarr/snapraid/media.content",
                "content /srv/hoardarr/backends/data-a/snapraid.content",
                "data movies_a /srv/hoardarr/backends/data-a",
                "data movies_b /srv/hoardarr/backends/data-b",
                "exclude *.tmp",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        inventory,
        "_command",
        lambda name, _arguments: (
            "SnapRAID array\nEverything is OK\n" if name == "snapraid" else None
        ),
    )

    item = inventory._snapraid_arrays(config_root)[0]

    assert item["id"] == "snapraid:media"
    assert item["configuration"]["quality"] == "available"
    assert item["configuration"]["data_disks"] == [
        {"name": "movies_a", "path": "/srv/hoardarr/backends/data-a"},
        {"name": "movies_b", "path": "/srv/hoardarr/backends/data-b"},
    ]
    assert [entry["level"] for entry in item["configuration"]["parity_disks"]] == [1, 2]
    assert len(item["configuration"]["config_sha256"]) == 64
    assert "*.tmp" not in str(item["configuration"])


def test_snapraid_inventory_fails_closed_on_malformed_or_oversized_config(
    tmp_path: Path,
) -> None:
    config = tmp_path / "unsafe.conf"
    config.write_text(
        "parity relative/path\ndata duplicate /srv/a\ndata duplicate /srv/b\n",
        encoding="utf-8",
    )
    parsed = inventory._snapraid_configuration(config)
    assert parsed["quality"] == "temporarily_unavailable"
    assert parsed["errors"]

    config.write_bytes(b"x" * (1024 * 1024 + 1))
    oversized = inventory._snapraid_configuration(config)
    assert oversized["quality"] == "temporarily_unavailable"
    assert oversized["config_sha256"] is None
