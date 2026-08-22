#!/usr/bin/env python3
"""Drive Hoardarr's real redundancy planner/executor against disposable iSCSI paths."""

from __future__ import annotations

import argparse
import json
import subprocess
import uuid
from pathlib import Path

from hoardarr.db.models import Base, StorageEntity, StoragePath
from hoardarr.operations.service import document_hash
from hoardarr.storage.executor import Paths, apply_storage_redundancy
from hoardarr.storage.redundancy import (
    apply_redundancy_result,
    build_redundancy_plan,
    register_single_path_storage,
    stable_path_identity,
    storage_documents,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("register", "add", "replace", "remove", "inspect")
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--transaction-root", type=Path, required=True)
    parser.add_argument("--wwid", required=True)
    parser.add_argument("--mountpoint", type=Path, required=True)
    parser.add_argument("--device-mountpoint", type=Path, required=True)
    parser.add_argument("--filesystem-uuid", required=True)
    parser.add_argument(
        "--path", action="append", default=[], metavar="CONTROLLER=/dev/sdX"
    )
    parser.add_argument("--remove-controller")
    return parser.parse_args()


def device(wwid: str, value: str) -> dict[str, object]:
    controller, kernel_path = value.split("=", 1)
    capacity = int(
        subprocess.run(
            ["blockdev", "--getsize64", kernel_path],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    logical = int(
        subprocess.run(
            ["blockdev", "--getss", kernel_path],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    physical = int(
        subprocess.run(
            ["blockdev", "--getpbsz", kernel_path],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    return {
        "id": f"wwn:{wwid}:{controller}",
        "kernel_path": kernel_path,
        "capacity_bytes": capacity,
        "identity": {"serial": "HOARDARR-CI-LUN", "wwn": wwid},
        "sector_sizes": {"logical_bytes": logical, "physical_bytes": physical},
        "connection": {
            "transport": "iscsi",
            "protocol": "iscsi",
            "controller_address": controller,
            "target_port_wwn": controller,
            "controller_model": "Disposable LIO target",
        },
    }


def main() -> None:
    args = arguments()
    args.transaction_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    engine = create_engine(f"sqlite+pysqlite:///{args.database}")
    Base.metadata.create_all(engine)
    devices = [device(args.wwid, value) for value in args.path]
    with Session(engine) as session, session.begin():
        if args.action == "register":
            if len(devices) != 1:
                raise SystemExit("register requires exactly one path")
            entity = register_single_path_storage(
                session,
                name="MediaPool",
                device=devices[0],
                mountpoint=str(args.mountpoint),
                presentation_device=str(devices[0]["kernel_path"]),
                filesystem_uuid=args.filesystem_uuid,
            )
            entity.config_json = {
                **entity.config_json,
                "device_mountpoint": str(args.device_mountpoint),
            }
        else:
            entity = session.scalar(select(StorageEntity).limit(1))
            if entity is None:
                raise SystemExit("logical storage is not registered")
            if args.action != "inspect":
                path_by_controller = {
                    str(item["connection"]["controller_address"]): item
                    for item in devices
                }
                remove_identity = None
                candidate_identity = None
                existing = list(
                    session.scalars(
                        select(StoragePath).where(
                            StoragePath.storage_entity_id == entity.id
                        )
                    )
                )
                existing_ids = {item.stable_path_identity for item in existing}
                for item in devices:
                    identity = stable_path_identity(item)
                    if identity not in existing_ids:
                        candidate_identity = identity
                        break
                if args.remove_controller:
                    selected = path_by_controller.get(args.remove_controller)
                    if selected is not None:
                        remove_identity = stable_path_identity(selected)
                    else:
                        remove_identity = next(
                            (
                                item.stable_path_identity
                                for item in existing
                                if item.stable_path_identity.startswith(
                                    f"iscsi:{args.remove_controller}:"
                                )
                            ),
                            None,
                        )
                plan = build_redundancy_plan(
                    session,
                    storage_entity_id=entity.id,
                    hardware_snapshot_sha256="a" * 64,
                    hardware_snapshot={"disks": devices},
                    action=args.action,
                    candidate_path_identity=(
                        remove_identity
                        if args.action == "remove"
                        else candidate_identity
                    ),
                    remove_path_identity=remove_identity,
                    policy="recommended",
                )
                result = apply_storage_redundancy(
                    {
                        "operation": "apply_storage_redundancy",
                        "operation_id": str(uuid.uuid4()),
                        "plan_sha256": plan["plan_sha256"],
                        "plan": plan,
                        "confirmation_sha256": document_hash({"confirmation": "APPLY"}),
                    },
                    paths=Paths(transaction_root=args.transaction_root),
                    inventory_provider=lambda: {"disks": devices},
                )
                observed = (
                    next(
                        (
                            item
                            for item in devices
                            if stable_path_identity(item)
                            == plan["selected_path"]["stable_path_identity"]
                        ),
                        None,
                    )
                    if args.action in {"add", "replace"}
                    else None
                )
                entity = apply_redundancy_result(
                    session, plan=plan, observed_device=observed
                )
                if result["storage_entity_id"] != entity.id:
                    raise SystemExit("executor changed the logical storage identity")
        session.flush()
        document = storage_documents(session)[0]
        print(
            json.dumps(
                {
                    "storage_entity_id": document["id"],
                    "filesystem_uuid": document["filesystem_uuid"],
                    "mountpoint": document["mountpoint"],
                    "presentation_device": document["presentation_device"],
                    "topology_state": document["topology_state"],
                    "path_count": len(document["paths"]),
                    "paths": [item["kernel_path"] for item in document["paths"]],
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
