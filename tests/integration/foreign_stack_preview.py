#!/usr/bin/env python3
"""Exercise the production foreign-stack parsers against inactive loop metadata."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from hoardarr.storage.executor import (
    _foreign_lvm_preview,
    _foreign_md_preview,
    _foreign_zfs_preview,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--md-member", action="append", default=[])
    parser.add_argument("--lvm-member", action="append", default=[])
    parser.add_argument("--zfs-member", action="append", default=[])
    parser.add_argument("--evidence", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    command_log: list[list[str]] = []

    def probe(command: list[str], timeout: int) -> str:
        command_log.append(command)
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return completed.stdout

    results: dict[str, dict[str, Any]] = {}
    if args.md_member:
        results["linux_md"] = _foreign_md_preview(
            [Path(item) for item in args.md_member], probe
        )
    if args.lvm_member:
        results["lvm"] = _foreign_lvm_preview(
            [Path(item) for item in args.lvm_member], probe
        )
    if args.zfs_member:
        results["zfs"] = _foreign_zfs_preview(
            [Path(item) for item in args.zfs_member], probe
        )

    if set(results) != {"linux_md", "lvm", "zfs"}:
        raise SystemExit("all three inactive stack profiles are required")
    if results["linux_md"]["completeness"]["state"] != "complete":
        raise SystemExit("the stopped MD array was not recognized as complete")
    if results["lvm"]["completeness"]["state"] != "complete":
        raise SystemExit("the inactive LVM volume group was not recognized as complete")
    if results["zfs"]["identity"] == "" or results["zfs"]["mountability"]["state"] != "not_reported":
        raise SystemExit("the exported ZFS labels were not represented honestly")

    flattened = [" ".join(command) for command in command_log]
    prohibited = ("--assemble", "vgchange", "lvchange", "zpool import", "mount ")
    if any(token in command for command in flattened for token in prohibited):
        raise SystemExit("a read-only preview attempted stack activation")
    evidence = {
        "classification": "VERIFIED IN ISOLATION",
        "source": "disposable Ubuntu loop-backed inactive storage stacks",
        "profiles": results,
        "commands": command_log,
        "activation_performed": False,
        "mutation_performed_during_preview": False,
    }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
