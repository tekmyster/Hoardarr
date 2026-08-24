from __future__ import annotations

import json
import sys
from pathlib import Path

document = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert document["schema_version"] == 1
assert document["source"] == "unraid_runtime_state"
assert document["unraid_version"] == "7.2.0"
assert document["assignments"] == [
    {
        "slot": "disk1",
        "role": "data",
        "serial": "SANITIZED-DATA-1",
        "wwn": "0x5000000000000002",
        "eui64": None,
        "nguid": None,
        "capacity_bytes": 8_000_000_000,
        "filesystem_type": "xfs",
    },
    {
        "slot": "parity",
        "role": "parity",
        "serial": "SANITIZED-PARITY",
        "wwn": "0x5000000000000001",
        "eui64": None,
        "nguid": None,
        "capacity_bytes": 8_000_000_000,
        "filesystem_type": None,
    },
]
