from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from hoardarr.operations.service import document_hash
from hoardarr.storage import executor
from hoardarr.storage.executor import Paths, apply_snapraid_replacement
from hoardarr.storage.snapraid import build_replacement_plan


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exercise Hoardarr's real SnapRAID data-disk replacement executor."
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--pool", required=True)
    parser.add_argument("--data-name", required=True)
    parser.add_argument("--replacement-loop", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--expected-file", required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    arguments = parser.parse_args()
    if os.geteuid() != 0 or not Path("/.hoardarr-disposable-runner").is_file():
        raise SystemExit("replacement integration requires the disposable-runner marker and root")
    work_root = arguments.work_root.resolve(strict=True)
    config = arguments.config.resolve(strict=True)
    if config.parent.parent != work_root or config.name != f"{arguments.pool}.conf":
        raise SystemExit("SnapRAID test configuration is outside the disposable work root")
    loop = arguments.replacement_loop.resolve(strict=True)
    backing = Path(
        subprocess.run(
            ["losetup", "--noheadings", "--output", "BACK-FILE", str(loop)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    ).resolve(strict=True)
    if work_root not in backing.parents:
        raise SystemExit("replacement device is not backed by this disposable test")

    alias_root = work_root / "by-id"
    alias_root.mkdir(mode=0o700)
    alias = alias_root / f"scsi-hoardarr-ci-{uuid.uuid4().hex}"
    alias.symlink_to(loop)
    capacity = int(
        subprocess.run(
            ["blockdev", "--getsize64", str(loop)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    disk = {
        "id": f"wwn:hoardarr-ci-replacement-{uuid.uuid4().hex}",
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
        "signature_scan": {"status": "complete", "source": "wipefs", "reason": None},
    }
    snapshot = {"schema_version": 1, "source": {"kind": "disposable-loop"}, "disks": [disk]}
    plan = build_replacement_plan(
        pool_name=arguments.pool,
        data_name=arguments.data_name,
        config=config.read_text(encoding="utf-8"),
        disk=disk,
        hardware_snapshot_sha256=document_hash(snapshot),
        filesystem="ext4",
    )
    plan_sha = document_hash(plan)
    operation_id = str(uuid.uuid4())
    transaction_root = work_root / "transactions"
    paths = Paths(
        quarantine_marker=work_root / "quarantine.json",
        transaction_root=transaction_root,
        lock_root=work_root / "locks",
        fstab=work_root / "fstab",
        mount_root=Path("/mnt/hoardarr/disks"),
        dev_by_id=alias_root,
        snapraid_config_root=config.parent,
    )
    paths.mount_root.mkdir(parents=True, exist_ok=True, mode=0o755)
    commands: list[list[str]] = []

    def run(command: list[str], timeout: int) -> None:
        commands.append(command)
        executor._run(command, timeout)
        if Path(command[0]).name == "partprobe":
            kernel_partition = Path(f"{loop}p1")
            for _ in range(100):
                if kernel_partition.exists():
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("replacement partition did not appear")
            alias.with_name(f"{alias.name}-part1").symlink_to(kernel_partition)

    original_quarantine = executor.validate_quarantine
    executor.validate_quarantine = lambda _marker: {"ready": True}
    try:
        result = apply_snapraid_replacement(
            {
                "operation": "apply_snapraid_replacement",
                "operation_id": operation_id,
                "plan_sha256": plan_sha,
                "plan": plan,
                "confirmation_sha256": document_hash({"confirmation": "I AGREE"}),
            },
            paths=paths,
            inventory_provider=lambda: snapshot,
            runner=run,
        )
    finally:
        executor.validate_quarantine = original_quarantine
    recovered = Path(str(result["replacement_mount"])) / arguments.expected_file
    recovered_sha = _sha256(recovered)
    if recovered_sha != arguments.expected_sha256:
        raise SystemExit("recovered SnapRAID file hash did not match")
    action_names = [Path(command[0]).name + ":" + command[-1] for command in commands]
    expected_order = ["snapraid:status", "snapraid:fix", "snapraid:check", "snapraid:sync"]
    observed_recovery = [item for item in action_names if item.startswith("snapraid:")]
    if observed_recovery != expected_order:
        raise SystemExit(f"unexpected SnapRAID recovery order: {observed_recovery}")
    journal = json.loads((transaction_root / f"{operation_id}.json").read_text(encoding="utf-8"))
    arguments.evidence.parent.mkdir(parents=True, exist_ok=True)
    arguments.evidence.write_text(
        json.dumps(
            {
                "classification": "VERIFIED IN ISOLATION",
                "source": "disposable Linux loop devices",
                "operation_id": operation_id,
                "replacement_device_id": result["replacement_device_id"],
                "replacement_mount": result["replacement_mount"],
                "old_path": plan["old_path"],
                "filesystem": plan["filesystem"],
                "existing_data_review": plan["existing_data"],
                "recovered_sha256": recovered_sha,
                "expected_sha256": arguments.expected_sha256,
                "command_order": observed_recovery,
                "journal_state": journal["state"],
                "journal_phase": journal["phase"],
                "parity_state": result["parity_state"],
                "stable_alias_partition_used": any("-part1" in part for cmd in commands for part in cmd),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(["umount", str(result["replacement_mount"])], check=True)
    Path(str(result["replacement_mount"])).rmdir()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
