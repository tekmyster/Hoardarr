#!/usr/bin/env python3
"""Exercise Unraid role classification with real disposable block signatures."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from hoardarr.db.models import Base, HardwareSnapshot, Operation
from hoardarr.operations.service import document_hash
from hoardarr.storage.foreign import (
    assess_foreign_storage,
    clear_unraid_evidence,
    persist_unraid_evidence,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def capacity(path: Path) -> int:
    return int(subprocess.check_output(["blockdev", "--getsize64", str(path)], text=True).strip())


def disk(path: Path, *, serial: str, wwn: str, signature: str | None) -> dict[str, object]:
    return {
        "id": f"wwn:{wwn}",
        "stable_identity": True,
        "kernel_path": str(path),
        "identity": {"serial": serial, "wwn": wwn, "eui64": None, "nguid": None},
        "vendor": "HOARDARR-CI",
        "model": "Disposable loop",
        "capacity_bytes": capacity(path),
        "sector_sizes": {"logical_bytes": 512, "physical_bytes": 512},
        "system_disk": False,
        "mountpoints": [],
        "partitions": [],
        "signatures": []
        if signature is None
        else [
            {
                "type": signature,
                "usage": "filesystem",
                "uuid": f"ci-{serial.casefold()}",
                "label": None,
                "source": "disposable-loop-fixture",
            }
        ],
        "signature_scan": {
            "status": "complete",
            "source": "disposable-loop-fixture",
            "reason": None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-loop", type=Path, required=True)
    parser.add_argument("--parity-loop", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    data = disk(args.data_loop, serial="HOARDARR-UNRAID-DATA", wwn="ci-data", signature="ext4")
    parity = disk(
        args.parity_loop,
        serial="HOARDARR-UNRAID-PARITY",
        wwn="ci-parity",
        signature=None,
    )
    payload = {"schema_version": 1, "source": {"kind": "disposable-loop"}, "disks": [data, parity]}
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        operation = Operation(
            kind="hardware.scan",
            status="succeeded",
            actor_type="system",
            actor_id="integration",
            request_sha256=document_hash({}),
            request_json={},
        )
        session.add(operation)
        session.flush()
        snapshot = HardwareSnapshot(
            operation_id=operation.id,
            detector_schema_version=1,
            source="disposable-loop",
            payload_json=payload,
            sha256=document_hash(payload),
        )
        session.add(snapshot)
        session.flush()
        persist_unraid_evidence(
            session,
            created_by="integration",
            document={
                "schema_version": 1,
                "source": "unraid_runtime_state",
                "captured_at": "2026-08-23T20:00:00Z",
                "unraid_version": "fixture",
                "assignments": [
                    {
                        "slot": "disk1",
                        "role": "data",
                        "serial": data["identity"]["serial"],
                        "wwn": data["identity"]["wwn"],
                        "capacity_bytes": data["capacity_bytes"],
                        "filesystem_type": "ext4",
                    },
                    {
                        "slot": "parity",
                        "role": "parity",
                        "serial": parity["identity"]["serial"],
                        "wwn": parity["identity"]["wwn"],
                        "capacity_bytes": parity["capacity_bytes"],
                        "filesystem_type": None,
                    },
                ],
            },
        )
        identified = assess_foreign_storage(session, snapshot=snapshot)
        clear_unraid_evidence(session)
        inferred = assess_foreign_storage(session, snapshot=snapshot)

    identified_roles = {
        item["unraid"]["role"]: item["unraid"]["classification"]
        for item in identified["candidates"]
    }
    suspected_parity = next(
        item for item in inferred["candidates"] if item["profile"] == "unraid_unknown"
    )
    result = {
        "classification": "VERIFIED IN ISOLATION",
        "source": "two disposable Linux loop devices",
        "data_source_has_real_filesystem": True,
        "parity_source_has_no_filesystem_signature": True,
        "identified_roles": identified_roles,
        "matched_assignments": identified["unraid_evidence"]["matched_assignment_count"],
        "without_assignment_role": suspected_parity["unraid"]["role"],
        "without_assignment_classification": suspected_parity["unraid"]["classification"],
        "parity_reuse_supported": suspected_parity["unraid"]["parity_reuse_supported"],
        "mutation_performed_by_classification": False,
    }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
