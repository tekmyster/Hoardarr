from __future__ import annotations

import json
import subprocess
from pathlib import Path

from hoardarr.hardware.service import run_hardware_detector


def test_production_detector_requests_bounded_block_signature_probe(
    tmp_path: Path, monkeypatch
) -> None:
    detector = tmp_path / "detect-hardware.py"
    detector.write_text("# test detector\n", encoding="utf-8")
    payload = {"schema_version": 1, "source": {"kind": "sysfs"}, "disks": []}
    captured: dict[str, object] = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=json.dumps(payload).encode(),
            stderr=b"",
        )

    monkeypatch.setattr("hoardarr.hardware.service.subprocess.run", run)

    document, digest = run_hardware_detector(
        detector,
        timeout_seconds=30,
        output_limit_bytes=1024 * 1024,
        production=True,
    )

    assert document == payload
    assert len(digest) == 64
    command = captured["command"]
    assert "--probe-block-signatures" in command
    assert captured["kwargs"]["shell"] is False
