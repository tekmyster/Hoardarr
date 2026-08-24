from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from hoardarr.operations.service import document_hash


class HardwareScanError(RuntimeError):
    pass


def detector_environment() -> dict[str, str]:
    environment = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    if os.name == "nt":
        system_root = os.environ.get("SYSTEMROOT")
        if system_root:
            environment["SystemRoot"] = system_root
    return environment


def run_hardware_detector(
    detector: Path,
    *,
    timeout_seconds: int,
    output_limit_bytes: int,
    production: bool,
) -> tuple[dict[str, Any], str]:
    if not detector.is_file():
        raise HardwareScanError(f"hardware detector is unavailable: {detector}")
    try:
        result = subprocess.run(
            [
                sys.executable,
                os.fspath(detector),
                "--format",
                "json",
                "--probe-block-signatures",
            ],
            shell=False,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=detector_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        raise HardwareScanError("hardware detector exceeded its time limit") from exc
    if len(result.stdout) > output_limit_bytes or len(result.stderr) > output_limit_bytes:
        raise HardwareScanError("hardware detector exceeded its output limit")
    if result.returncode != 0:
        raise HardwareScanError(f"hardware detector failed with exit code {result.returncode}")
    try:
        payload = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HardwareScanError("hardware detector returned invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("schema_version"), int):
        raise HardwareScanError("hardware detector response has an unsupported schema")
    source = payload.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("kind"), str):
        raise HardwareScanError("hardware detector did not identify its capture source")
    if production and source["kind"] == "fixture":
        raise HardwareScanError("fixture hardware data is forbidden in production")
    return payload, document_hash(payload)
