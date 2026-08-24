from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _module() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "export-nas-source-evidence.py"
    spec = importlib.util.spec_from_file_location("nas_evidence_exporter", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _disk(root: Path, name: str, *, serial: str, wwid: str) -> None:
    disk = root / name
    (disk / "device").mkdir(parents=True)
    (disk / "queue").mkdir()
    (disk / "device" / "serial").write_text(serial, encoding="utf-8")
    (disk / "device" / "wwid").write_text(wwid, encoding="utf-8")
    (disk / "size").write_text("2048", encoding="utf-8")
    (disk / "queue" / "logical_block_size").write_text("512", encoding="utf-8")


def test_source_nas_exporter_uses_runtime_marker_and_whole_disk_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = _module()
    sys_block = tmp_path / "sys-block"
    sys_block.mkdir()
    _disk(sys_block, "sda", serial="SOURCE-1", wwid="naa.5000000000000001")
    _disk(sys_block, "loop0", serial="IGNORED", wwid="ignored")
    partition = sys_block / "sda1"
    _disk(sys_block, "sda1", serial="PARTITION", wwid="partition")
    (partition / "partition").write_text("1", encoding="utf-8")
    synology_version = tmp_path / "synology-version"
    synology_version.write_text('productversion="7.2.2"\n', encoding="utf-8")
    monkeypatch.setattr(exporter, "SYS_BLOCK", sys_block)
    monkeypatch.setattr(exporter, "SYNOLOGY_VERSION", synology_version)
    monkeypatch.setattr(exporter, "QNAP_CONFIG", tmp_path / "missing-qnap")

    document = exporter.build_document(allow_generic=False)

    assert document["platform"] == "synology"
    assert document["platform_marker"] == "synology_runtime"
    assert document["product_version"] == "7.2.2"
    assert document["members"] == [
        {
            "member": "sda",
            "serial": "SOURCE-1",
            "wwn": "naa.5000000000000001",
            "eui64": None,
            "nguid": None,
            "capacity_bytes": 1_048_576,
        }
    ]


def test_source_nas_exporter_requires_explicit_generic_linux_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = _module()
    monkeypatch.setattr(exporter, "SYNOLOGY_VERSION", tmp_path / "missing-synology")
    monkeypatch.setattr(exporter, "QNAP_CONFIG", tmp_path / "missing-qnap")

    with pytest.raises(SystemExit, match="allow-generic-linux"):
        exporter.platform_evidence(False)
