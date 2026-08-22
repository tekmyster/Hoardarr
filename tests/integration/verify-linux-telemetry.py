#!/usr/bin/env python3
"""Read-only Linux telemetry smoke test for repository-controlled CI/VM execution."""

from __future__ import annotations

import platform
from datetime import UTC, datetime
from pathlib import Path

from hoardarr.telemetry.collectors import HostCollector
from hoardarr.telemetry.platform_collectors import (
    LinuxStoragePlatformCollector,
    parse_mdstat,
)


def main() -> int:
    if platform.system() != "Linux":
        raise SystemExit("This read-only validation requires Linux.")
    now = datetime.now(UTC)
    readings = HostCollector().collect(observed_at=now)
    ids = {reading.metric_id for reading in readings if reading.quality == "available"}
    required = {
        "host.cpu.utilization",
        "host.memory.used",
        "host.memory.available",
        "host.uptime",
        "capacity.total",
    }
    missing = required - ids
    if missing:
        raise SystemExit(f"Linux host telemetry missing: {sorted(missing)}")
    mdstat = Path("/proc/mdstat").read_text(encoding="utf-8", errors="replace")
    parse_mdstat(mdstat)
    LinuxStoragePlatformCollector().collect(observed_at=now)
    print(f"validated {len(readings)} live Linux host/interface readings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
