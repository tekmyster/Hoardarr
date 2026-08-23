from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import subprocess
import uuid
from pathlib import Path

from hoardarr.operations.service import document_hash
from hoardarr.storage import executor
from hoardarr.storage.executor import (
    Paths,
    _live_md_array_state,
    _live_zfs_pool_state,
    apply_array_replacement,
)
from hoardarr.storage.replacement import build_md_replacement_plan, build_zfs_replacement_plan


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exercise Hoardarr's production ZFS/MD replacement executor."
    )
    parser.add_argument("--provider", required=True, choices=("zfs", "linux_md"))
    parser.add_argument("--target", required=True)
    parser.add_argument("--old-member")
    parser.add_argument("--replacement-loop", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--mounted-file", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0 or not Path("/.hoardarr-disposable-runner").is_file():
        raise SystemExit("array replacement requires the disposable-runner marker and root")
    work_root = args.work_root.resolve(strict=True)
    loop = args.replacement_loop.resolve(strict=True)
    backing = Path(
        subprocess.run(
            ["losetup", "--noheadings", "--output", "BACK-FILE", str(loop)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    ).resolve(strict=True)
    if work_root not in backing.parents:
        raise SystemExit("replacement is not a loop backed by this disposable test")
    capacity = int(
        subprocess.run(
            ["blockdev", "--getsize64", str(loop)], check=True, capture_output=True, text=True
        ).stdout
    )
    disk = {
        "id": f"wwn:hoardarr-ci-{uuid.uuid4().hex}",
        "stable_identity": True,
        "system_device": False,
        "system_disk": False,
        "selectable": True,
        "read_only": False,
        "kernel_path": str(loop),
        "vendor": "HOARDARR-CI",
        "model": "DISPOSABLE-LOOP",
        "identity": {
            "serial": f"CI-{uuid.uuid4().hex}",
            "wwn": f"ci-{uuid.uuid4().hex}",
            "eui64": None,
            "nguid": None,
        },
        "capacity_bytes": capacity,
        "sector_sizes": {"logical_bytes": 512, "physical_bytes": 512},
        "partitions": [],
        "signatures": [],
        "signature_scan": {"status": "complete"},
    }
    initial = (
        _live_zfs_pool_state(args.target)
        if args.provider == "zfs"
        else _live_md_array_state(args.target)
    )
    if args.provider == "zfs":
        if args.old_member is None:
            raise SystemExit("ZFS replacement requires --old-member")
        pool = {
            "name": args.target,
            "pool_guid": initial["pool_guid"],
            "degraded": True,
            "configuration": {
                **initial,
                "member_capacities": {
                    args.old_member: int(
                        subprocess.run(
                            ["blockdev", "--getsize64", args.old_member],
                            check=True,
                            capture_output=True,
                            text=True,
                        ).stdout
                    )
                },
            },
        }
        plan = build_zfs_replacement_plan(
            pool=pool, member_path=args.old_member, disk=disk, hardware_snapshot_sha256="a" * 64
        )
    else:
        array = {
            "name": args.target,
            "degraded": initial["degraded"],
            "configuration": {
                "array_path": initial["array_path"],
                "array_uuid": initial["array_uuid"],
                "level": initial["level"],
                "raid_disks": initial["raid_disks"],
                "member_paths": initial["member_paths"],
                "config_sha256": initial["config_sha256"],
            },
        }
        plan = build_md_replacement_plan(
            array=array, member_path=args.old_member, disk=disk, hardware_snapshot_sha256="a" * 64
        )
    alias_root = Path("/dev/disk/by-id")
    alias_root.mkdir(parents=True, exist_ok=True)
    alias = alias_root / f"hoardarr-ci-replacement-{uuid.uuid4().hex}"
    alias.symlink_to(loop)
    atexit.register(lambda: alias.unlink(missing_ok=True))
    operation_id = str(uuid.uuid4())
    transaction_root = work_root / "array-transactions"
    paths = Paths(
        quarantine_marker=work_root / "quarantine.json",
        transaction_root=transaction_root,
        lock_root=work_root / "array-locks",
        dev_by_id=alias_root,
    )
    commands: list[list[str]] = []
    original = executor.validate_quarantine
    executor.validate_quarantine = lambda _marker: {"ready": True}
    try:
        result = apply_array_replacement(
            {
                "operation": "apply_array_replacement",
                "operation_id": operation_id,
                "plan_sha256": document_hash(plan),
                "plan": plan,
                "confirmation_sha256": document_hash({"confirmation": "I AGREE"}),
            },
            paths=paths,
            inventory_provider=lambda: {"disks": [disk]},
            runner=lambda command, timeout: (
                commands.append(command),
                executor._run(command, timeout),
            )[1],
        )
    finally:
        executor.validate_quarantine = original
    observed_hash = sha256(args.mounted_file)
    if observed_hash != args.expected_sha256:
        raise SystemExit("array replacement changed test data")
    final = (
        _live_zfs_pool_state(args.target)
        if args.provider == "zfs"
        else _live_md_array_state(args.target)
    )
    journal = json.loads((transaction_root / f"{operation_id}.json").read_text(encoding="utf-8"))
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(
        json.dumps(
            {
                "classification": "VERIFIED IN ISOLATION",
                "source": "disposable Linux loop devices",
                "provider": args.provider,
                "target": args.target,
                "operation_id": operation_id,
                "target_identity_before": plan["target_identity"],
                "target_identity_after": final.get("pool_guid")
                if args.provider == "zfs"
                else final.get("array_uuid"),
                "replacement_device_id": result["replacement_device_id"],
                "old_member": args.old_member,
                "data_sha256": observed_hash,
                "command_argv": commands,
                "journal_state": journal["state"],
                "journal_phase": journal["phase"],
                "final_member_paths": final["member_paths"],
                "final_degraded": bool(final.get("degraded", False)),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    alias.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
