#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

from hoardarr.storage.layouts import snapraid_expand_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--role", choices=("data", "parity"), required=True)
    parser.add_argument("--mountpoint", required=True)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()
    original = args.config.read_text(encoding="utf-8")
    observed = hashlib.sha256(original.encode()).hexdigest()
    if observed != args.expected_sha256:
        raise SystemExit("SnapRAID configuration changed before expansion")
    updated = snapraid_expand_config(
        original,
        role=args.role,
        mountpoint=args.mountpoint,
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{args.config.name}.",
        dir=args.config.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o640)
        os.replace(temporary, args.config)
    finally:
        temporary.unlink(missing_ok=True)
    print(hashlib.sha256(updated.encode()).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
