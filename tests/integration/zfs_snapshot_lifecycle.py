#!/usr/bin/env python3
"""Exercise Hoardarr's production snapshot executor against disposable ZFS."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from hoardarr.operations.service import document_hash
from hoardarr.storage import executor
from hoardarr.storage.capacity_plans import build_capacity_plan
from hoardarr.storage.executor import (
    Paths,
    _live_zfs_resource_state,
    _live_zfs_snapshot_state,
    _run,
    apply_storage_volume_capacity,
    apply_storage_volume_snapshot,
)
from hoardarr.storage.snapshot_plans import build_snapshot_plan


def execute(plan: dict[str, object], paths: Paths) -> tuple[dict[str, object], dict[str, object]]:
    operation_id = str(uuid.uuid4())
    result = apply_storage_volume_snapshot(
        {
            "operation": "apply_storage_volume_snapshot",
            "operation_id": operation_id,
            "plan_sha256": plan["plan_sha256"],
            "plan": plan,
            "confirmation_sha256": document_hash({"confirmation": plan["confirmation"]}),
        },
        paths=paths,
    )
    journal = json.loads(
        (paths.transaction_root / f"{operation_id}.json").read_text(encoding="utf-8")
    )
    return result, journal


def execute_capacity(
    plan: dict[str, object], paths: Paths
) -> tuple[dict[str, object], dict[str, object]]:
    operation_id = str(uuid.uuid4())
    result = apply_storage_volume_capacity(
        {
            "operation": "apply_storage_volume_capacity",
            "operation_id": operation_id,
            "plan_sha256": plan["plan_sha256"],
            "plan": plan,
            "confirmation_sha256": document_hash({"confirmation": plan["confirmation"]}),
        },
        paths=paths,
    )
    journal = json.loads(
        (paths.transaction_root / f"{operation_id}.json").read_text(encoding="utf-8")
    )
    return result, journal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--mountpoint", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()

    live = _live_zfs_resource_state(args.dataset)
    provider_guid = str(live["guid"])
    volume = {
        "id": str(uuid.uuid4()),
        "stable_identity": f"zfs:dataset:{args.dataset}",
        "name": args.dataset.split("/", 1)[-1],
        "provider": "zfs",
        "resource_type": "dataset",
        "provider_resource_id": args.dataset,
        "provider_guid": provider_guid,
        "presentation": "file",
    }
    paths = Paths(
        quarantine_marker=args.work_root / "quarantine.json",
        transaction_root=args.work_root / "snapshot-transactions",
    )
    data = args.mountpoint / "snapshot-lifecycle.txt"
    data.write_text("before snapshot\n", encoding="utf-8")

    original_quarantine = executor.validate_quarantine
    executor.validate_quarantine = lambda _marker: {"ready": True}
    journals: list[dict[str, object]] = []
    capacity_journals: list[dict[str, object]] = []
    clone_resource = f"{args.dataset.split('/', 1)[0]}/hoardarr-snapshot-clone"
    try:
        create_plan = build_snapshot_plan(
            volume=volume,
            provider_guid=provider_guid,
            action="create",
            snapshot_name="executor-validation",
        )
        create_result, journal = execute(create_plan, paths)
        journals.append(journal)
        snapshot = dict(create_result["snapshot"])
        snapshot["id"] = str(uuid.uuid4())

        data.write_text("after snapshot\n", encoding="utf-8")
        restore_plan = build_snapshot_plan(
            volume=volume,
            provider_guid=provider_guid,
            action="restore",
            snapshot=snapshot,
        )
        _restore_result, journal = execute(restore_plan, paths)
        journals.append(journal)
        restored = data.read_text(encoding="utf-8") == "before snapshot\n"

        clone_plan = build_snapshot_plan(
            volume=volume,
            provider_guid=provider_guid,
            action="clone",
            snapshot=snapshot,
            clone_name="hoardarr-snapshot-clone",
        )
        clone_result, journal = execute(clone_plan, paths)
        journals.append(journal)
        clone_state = _live_zfs_resource_state(clone_resource)
        clone_mountpoint = Path(str(clone_state["mountpoint"]))
        clone_verified = (
            (clone_mountpoint / "snapshot-lifecycle.txt").read_text(encoding="utf-8")
            == "before snapshot\n"
            and clone_result["clone_volume"]["provider_resource_id"] == clone_resource
        )

        _run(["/usr/sbin/zfs", "destroy", clone_resource], 120)
        delete_plan = build_snapshot_plan(
            volume=volume,
            provider_guid=provider_guid,
            action="delete",
            snapshot=snapshot,
        )
        _delete_result, journal = execute(delete_plan, paths)
        journals.append(journal)
        deleted = _live_zfs_snapshot_state(
            str(snapshot["provider_snapshot_id"])
        ) is None

        capacity_plan = build_capacity_plan(
            volume=volume,
            provider_guid=provider_guid,
            quota_bytes=64 * 1024**2,
            reservation_bytes=8 * 1024**2,
        )
        capacity_result, journal = execute_capacity(capacity_plan, paths)
        capacity_journals.append(journal)
        limited = _live_zfs_resource_state(args.dataset)
        limits_verified = (
            capacity_result["capacity_limits"]["quota_bytes"] == 64 * 1024**2
            and capacity_result["capacity_limits"]["reservation_bytes"] == 8 * 1024**2
            and str(limited["quota"]) == str(64 * 1024**2)
            and str(limited["reservation"]) == str(8 * 1024**2)
        )
        clear_plan = build_capacity_plan(
            volume=volume,
            provider_guid=provider_guid,
            quota_bytes=0,
            reservation_bytes=0,
        )
        clear_result, journal = execute_capacity(clear_plan, paths)
        capacity_journals.append(journal)
        limits_cleared = (
            clear_result["capacity_limits"]["quota_bytes"] == 0
            and clear_result["capacity_limits"]["reservation_bytes"] == 0
        )
    finally:
        executor.validate_quarantine = original_quarantine
        try:
            _run(["/usr/sbin/zfs", "destroy", clone_resource], 120)
        except executor.ExecutorFailure:
            pass

    evidence = {
        "classification": "VERIFIED IN ISOLATION",
        "source": "disposable Ubuntu ZFS loop devices",
        "provider": "zfs",
        "dataset": args.dataset,
        "provider_guid_before": provider_guid,
        "provider_guid_after": str(_live_zfs_resource_state(args.dataset)["guid"]),
        "create_verified": str(snapshot.get("provider_guid") or "").isdigit(),
        "restore_verified": restored,
        "clone_verified": clone_verified,
        "delete_verified": deleted,
        "journal_states": [item["state"] for item in journals],
        "capacity_limits_verified": limits_verified,
        "capacity_limits_cleared": limits_cleared,
        "capacity_journal_states": [item["state"] for item in capacity_journals],
        "production_executor_used": True,
        "physical_hardware_used": False,
    }
    if not (
        evidence["provider_guid_before"] == evidence["provider_guid_after"]
        and evidence["create_verified"]
        and evidence["restore_verified"]
        and evidence["clone_verified"]
        and evidence["delete_verified"]
        and evidence["journal_states"] == ["succeeded"] * 4
        and evidence["capacity_limits_verified"]
        and evidence["capacity_limits_cleared"]
        and evidence["capacity_journal_states"] == ["succeeded"] * 2
    ):
        raise SystemExit(f"snapshot lifecycle validation failed: {evidence}")
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
