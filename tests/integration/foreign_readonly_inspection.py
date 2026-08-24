#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

from hoardarr.operations.service import document_hash
from hoardarr.storage import executor
from hoardarr.storage.executor import Paths, apply_foreign_inspection


def capture(*arguments: str) -> str:
    return subprocess.run(
        list(arguments), check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()


def mounted_targets(source: Path) -> str:
    result = subprocess.run(
        [
            "findmnt",
            "--noheadings",
            "--raw",
            "--source",
            str(source),
            "--output",
            "TARGET",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode not in {0, 1}:
        raise SystemExit("findmnt could not verify source activation")
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or not Path("/.hoardarr-disposable-runner").is_file():
        raise SystemExit("requires a marked disposable root runner")
    backing = Path(
        capture("losetup", "--noheadings", "--output", "BACK-FILE", str(args.loop))
    )
    if args.work_root.resolve() not in backing.resolve().parents:
        raise SystemExit("loop backing file is outside the test-created work root")
    if mounted_targets(args.loop):
        raise SystemExit("foreign source must begin unmounted")

    filesystem_uuid = capture("blkid", "-s", "UUID", "-o", "value", str(args.loop))
    filesystem_type = capture("blkid", "-s", "TYPE", "-o", "value", str(args.loop))
    device_id = f"loop-test:{document_hash(str(backing))[:24]}"
    device = {
        "id": device_id,
        "stable_identity": True,
        "vendor": "HOARDARR-CI",
        "model": "Disposable loop filesystem",
        "serial": document_hash(str(backing))[:32],
        "wwn": None,
        "eui64": None,
        "nguid": None,
        "capacity_bytes": int(capture("blockdev", "--getsize64", str(args.loop))),
        "logical_sector_bytes": int(capture("blockdev", "--getss", str(args.loop))),
        "physical_sector_bytes": int(capture("blockdev", "--getpbsz", str(args.loop))),
    }
    live = {
        "id": device_id,
        "stable_identity": True,
        "vendor": device["vendor"],
        "model": device["model"],
        "identity": {
            "serial": device["serial"],
            "wwn": None,
            "eui64": None,
            "nguid": None,
        },
        "capacity_bytes": device["capacity_bytes"],
        "sector_sizes": {
            "logical_bytes": device["logical_sector_bytes"],
            "physical_bytes": device["physical_sector_bytes"],
        },
        "kernel_path": str(args.loop),
        "partitions": [],
    }
    base_plan: dict[str, Any] = {
        "schema_version": 1,
        "operation": "foreign.inspect_read_only",
        "candidate_id": f"foreign:{document_hash(device_id)[:24]}",
        "hardware_snapshot_id": str(uuid.uuid4()),
        "hardware_snapshot_sha256": document_hash({"disks": [live]}),
        "device": device,
        "device_binding_sha256": document_hash(device),
        "source": {
            "kind": "whole_device",
            "kernel_path_at_preview": str(args.loop),
            "partition_number": None,
            "filesystem_type": filesystem_type,
            "filesystem_uuid": filesystem_uuid,
            "filesystem_label": None,
            "signature_source": "blkid",
            "read_only_options": ["ro", "noload", "nodev", "nosuid", "noexec"],
        },
        "limits": {
            "maximum_entries": 100_000,
            "maximum_extension_groups": 256,
            "maximum_errors": 100,
        },
        "access": "read_only",
        "persistent_mount": False,
        "automatic_activation": False,
        "mutation_performed": False,
    }
    plan = {**base_plan, "plan_sha256": document_hash(base_plan)}
    alias_root = Path("/dev/disk/by-id")
    alias_root.mkdir(parents=True, exist_ok=True)
    alias = alias_root / f"hoardarr-ci-foreign-{uuid.uuid4().hex}"
    alias.symlink_to(args.loop)
    operation_id = str(uuid.uuid4())
    paths = Paths(
        quarantine_marker=args.work_root / "quarantine.json",
        transaction_root=args.work_root / "foreign-transactions",
        lock_root=args.work_root / "foreign-locks",
        inspection_root=args.work_root / "foreign-mounts",
        dev_by_id=alias_root,
    )
    original = executor.validate_quarantine
    executor.validate_quarantine = lambda _marker: {"ready": True}
    try:
        result = apply_foreign_inspection(
            {
                "operation": "apply_foreign_inspection",
                "operation_id": operation_id,
                "plan_sha256": plan["plan_sha256"],
                "plan": plan,
                "confirmation_sha256": document_hash(
                    {"confirmation": "INSPECT READ ONLY"}
                ),
            },
            paths=paths,
            inventory_provider=lambda: {
                "source": {"kind": "sysfs"},
                "disks": [live],
            },
        )
    finally:
        executor.validate_quarantine = original
        alias.unlink(missing_ok=True)
    if mounted_targets(args.loop):
        raise SystemExit("inspection left the disposable source mounted")
    journal = json.loads(
        (paths.transaction_root / f"{operation_id}.json").read_text(encoding="utf-8")
    )
    inventory = result["inventory"]
    if inventory["file_count"] < 2 or inventory["read_errors"]:
        raise SystemExit("read-only inventory did not report the deterministic dataset")
    top_level_names = {item["name"] for item in inventory["top_level_entries"]}
    if not {"Movies", "TV"}.issubset(top_level_names):
        raise SystemExit(
            "archive preview did not preserve the top-level source inventory"
        )
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(
        json.dumps(
            {
                "classification": "VERIFIED IN ISOLATION",
                "source": "disposable Linux loop-backed ext4 filesystem",
                "device_id": device_id,
                "filesystem_uuid": filesystem_uuid,
                "filesystem_type": filesystem_type,
                "access": result["access"],
                "persistent_mount": result["persistent_mount"],
                "mutation_performed": result["mutation_performed"],
                "file_count": inventory["file_count"],
                "directory_count": inventory["directory_count"],
                "total_bytes": inventory["total_bytes"],
                "read_errors": len(inventory["read_errors"]),
                "top_level_entries": inventory["top_level_entries"],
                "permission_anomalies": inventory["permission_anomalies"],
                "truncated": inventory["truncated"],
                "journal_state": journal["state"],
                "private_mount_removed": not (
                    paths.inspection_root / operation_id
                ).exists(),
                "source_unmounted_after": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
