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
