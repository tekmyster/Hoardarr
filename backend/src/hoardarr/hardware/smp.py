from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from hoardarr.hardware.providers import ProviderError, parse_smp_discover

Probe = Callable[[list[str]], str | None]
BsgExists = Callable[[str], bool]
_EXPANDER_ID = re.compile(r"^expander-[0-9]+:[0-9]+$")


def bounded_smp_probe(command: list[str]) -> str | None:
    executable = shutil.which(command[0], path="/usr/sbin:/usr/bin:/sbin:/bin")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, *command[1:]],
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=15,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or len(result.stdout) > 2 * 1024 * 1024:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def enrich_smp_topology(
    payload: dict[str, Any],
    *,
    probe: Probe = bounded_smp_probe,
    bsg_root: Path = Path("/sys/class/bsg"),
    bsg_exists: BsgExists | None = None,
) -> dict[str, Any]:
    """Add read-only SMP evidence to already discovered expander paths."""

    result = deepcopy(payload)
    disks = result.get("disks")
    if not isinstance(disks, list):
        return result
    effective_probe = (
        (lambda _command: None) if os.name == "nt" and probe is bounded_smp_probe else probe
    )
    effective_exists = bsg_exists or (lambda expander_id: (bsg_root / expander_id).exists())
    observations: dict[str, dict[str, Any]] = {}
    for disk in disks:
        if not isinstance(disk, Mapping):
            continue
        connection = disk.get("connection")
        if not isinstance(connection, Mapping):
            continue
        expander_id = connection.get("expander_id")
        if not isinstance(expander_id, str) or _EXPANDER_ID.fullmatch(expander_id) is None:
            continue
        if expander_id in observations:
            continue
        if not effective_exists(expander_id):
            observations[expander_id] = {
                "quality": "not_reported",
                "source": "smp_discover --summary --dsn",
                "expander_sas_address": None,
                "phys": [],
            }
            continue
        output = effective_probe(
            ["smp_discover", "--summary", "--dsn", f"/dev/bsg/{expander_id}"]
        )
        if output is None:
            observations[expander_id] = {
                "quality": "temporarily_unavailable",
                "source": "smp_discover --summary --dsn",
                "expander_sas_address": None,
                "phys": [],
            }
            continue
        try:
            parsed = parse_smp_discover(output)
        except ProviderError:
            observations[expander_id] = {
                "quality": "temporarily_unavailable",
                "source": "smp_discover --summary --dsn",
                "expander_sas_address": None,
                "phys": [],
            }
        else:
            observations[expander_id] = {
                **parsed,
                "quality": "available",
                "source": "smp_discover --summary --dsn",
            }
    for disk in disks:
        if not isinstance(disk, dict):
            continue
        connection = disk.get("connection")
        if not isinstance(connection, dict):
            continue
        expander_id = connection.get("expander_id")
        if isinstance(expander_id, str) and expander_id in observations:
            connection["smp"] = deepcopy(observations[expander_id])
    return result
