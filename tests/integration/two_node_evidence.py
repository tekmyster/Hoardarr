#!/usr/bin/env python3
"""Register and export real two-node integration evidence from a Hoardarr database."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hoardarr.auth.service import Principal, create_initial_owner
from hoardarr.core.config import Settings
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.models import (
    MetricEntity,
    MetricRollup,
    MetricSample,
    Operation,
    StorageEntity,
    StoragePath,
    StorageRedundancyEvent,
    User,
)
from hoardarr.operations.service import create_operation
from hoardarr.storage.redundancy import (
    apply_redundancy_result,
    build_redundancy_plan,
    register_single_path_storage,
)
from hoardarr.telemetry.entitlements import (
    KNOWN_CAPABILITIES,
    canonical_json,
    installation_id,
)
from hoardarr.telemetry.service import TelemetryService
from sqlalchemy import select


def _block_size(device: str) -> int:
    result = subprocess.run(
        ["/usr/sbin/blockdev", "--getsize64", device],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return int(result.stdout.strip())


def _device(node: str, path: str, wwid: str, index: int) -> dict[str, Any]:
    return {
        "id": f"wwn:{wwid}",
        "kernel_path": path,
        "capacity_bytes": _block_size(path),
        "identity": {
            "serial": wwid,
            "wwn": wwid,
            "eui64": None,
            "nguid": None,
        },
        "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
        "connection": {
            "transport": "scsi",
            "protocol": "iscsi-simulated",
            "controller_address": f"{node}-controller-{index}",
            "controller_model": "QEMU virtual storage controller",
            "controller_vendor": "QEMU",
            "controller_port": str(index),
            "initiator": node,
            "target": wwid,
        },
    }


def register_shared(args: argparse.Namespace) -> None:
    settings = Settings()
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    devices = [
        _device(args.node, path, args.wwid, index)
        for index, path in enumerate(args.path, 1)
    ]
    with factory() as session, session.begin():
        entity = register_single_path_storage(
            session,
            name=f"{args.node} Shared Media",
            device=devices[0],
            mountpoint=args.mountpoint,
            presentation_device=args.mapper,
            filesystem_uuid=args.filesystem_uuid,
        )
        entity.config_json = {
            **entity.config_json,
            "node_name": args.node,
            "storage_scope": "external_shared",
            "ownership_mode": "controlled_single_writer",
        }
        if len(devices) > 1 and len(
            list(
                session.scalars(
                    select(StoragePath).where(StoragePath.storage_entity_id == entity.id)
                )
            )
        ) == 1:
            plan = build_redundancy_plan(
                session,
                storage_entity_id=entity.id,
                hardware_snapshot_sha256="0" * 64,
                hardware_snapshot={"disks": devices},
                action="add",
            )
            apply_redundancy_result(session, plan=plan, observed_device=devices[1])
        entity.presentation_device = args.mapper
        entity.topology_state = "fully_redundant" if len(devices) > 1 else "single_path"
    engine.dispose()


def register_local(args: argparse.Namespace) -> None:
    settings = Settings()
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    device = _device(args.node, args.path[0], args.wwid, 1)
    device["connection"] = {
        **device["connection"],
        "protocol": "virtio",
        "transport": "virtual-ssd",
        "controller_address": f"{args.node}-local-{args.name}",
    }
    with factory() as session, session.begin():
        entity = register_single_path_storage(
            session,
            name=args.name,
            device=device,
            mountpoint=args.mountpoint,
            presentation_device=args.path[0],
            filesystem_uuid=args.filesystem_uuid,
        )
        entity.config_json = {
            **entity.config_json,
            "node_name": args.node,
            "storage_scope": "local",
        }
    engine.dispose()


def add_event(args: argparse.Namespace) -> None:
    settings = Settings()
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        storage = session.scalar(
            select(StorageEntity).where(StorageEntity.stable_identity == f"wwn:{args.wwid}")
        )
        if storage is None:
            raise SystemExit("shared storage is not registered")
        storage.config_json = {
            **storage.config_json,
            "ownership_state": args.resulting_state,
            "peer_node": args.peer_node,
        }
        session.add(
            StorageRedundancyEvent(
                storage_entity_id=storage.id,
                event_type=args.event_type,
                previous_state=args.previous_state,
                resulting_state=args.resulting_state,
                details_json={
                    "node": args.node,
                    "peer_node": args.peer_node,
                    "source": "two-node isolated validation",
                },
                occurred_at=datetime.now(UTC),
            )
        )
    engine.dispose()


def provision_ui(args: argparse.Namespace) -> None:
    """Provision only the isolated VM's owner and ephemeral test entitlement."""
    settings = Settings()
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        if session.scalar(select(User).limit(1)) is None:
            create_initial_owner(session, username=args.username, password=args.password)
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    now = datetime.now(UTC)
    payload = {
        "license_id": f"isolated-two-node-{args.node.lower().replace(' ', '-')}",
        "installation_id": installation_id(settings.installation_identity_file),
        "not_before": (now - timedelta(minutes=5)).isoformat(),
        "expires_at": (now + timedelta(hours=2)).isoformat(),
        "capabilities": sorted(KNOWN_CAPABILITIES),
    }
    key_id = "isolated-two-node-test-key"
    settings.telemetry_license_file.write_text(
        json.dumps(
            {
                "payload": payload,
                "key_id": key_id,
                "signature": base64.b64encode(private_key.sign(canonical_json(payload))).decode(),
            }
        ),
        encoding="utf-8",
    )
    settings.telemetry_license_trust_file.write_text(
        json.dumps({"keys": {key_id: base64.b64encode(public_key).decode()}}),
        encoding="utf-8",
    )
    os.chmod(settings.telemetry_license_file, 0o600)
    os.chmod(settings.telemetry_license_trust_file, 0o600)
    engine.dispose()


def collect(_args: argparse.Namespace) -> None:
    settings = Settings()
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    service = TelemetryService(settings)
    try:
        with factory() as session, session.begin():
            service.collect(session, force=True)
    finally:
        service.close()
        engine.dispose()


def queue_hardware(args: argparse.Namespace) -> None:
    """Queue the production durable hardware detector for this isolated node."""

    settings = Settings()
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        owner = session.scalar(select(User).where(User.username == args.username))
        if owner is None:
            raise SystemExit("isolated validation owner is missing")
        operation, _created = create_operation(
            session,
            kind="hardware.scan",
            principal=Principal(
                user_id=owner.id,
                username=owner.username,
                is_admin=True,
                auth_type="session",
                scopes=frozenset({"read", "operate", "admin"}),
            ),
            request={"schema_version": 1},
            idempotency_key=f"two-node-hardware-{args.node.casefold().replace(' ', '-')}",
            resource_type="hardware_snapshot",
        )
        operation_id = operation.id
    engine.dispose()
    print(operation_id)


def wait_operation(args: argparse.Namespace) -> None:
    settings = Settings()
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    deadline = time.monotonic() + args.timeout
    status = "unknown"
    while time.monotonic() < deadline:
        with factory() as session:
            operation = session.get(Operation, args.operation_id)
            if operation is None:
                engine.dispose()
                raise SystemExit("isolated validation operation is missing")
            status = operation.status
            if status == "succeeded":
                print(status)
                engine.dispose()
                return
            if status in {"failed", "cancelled", "needs_attention"}:
                engine.dispose()
                raise SystemExit(f"isolated validation operation ended in {status}")
        time.sleep(0.25)
    engine.dispose()
    raise SystemExit(f"isolated validation operation timed out in {status}")


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
    return value


def export(args: argparse.Namespace) -> None:
    settings = Settings()
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    with factory() as session:
        entities = {item.id: item for item in session.scalars(select(MetricEntity))}
        samples = list(
            session.scalars(
                select(MetricSample).order_by(MetricSample.observed_at.desc()).limit(100_000)
            )
        )
        samples.reverse()
        document = {
            "node": args.node,
            "exported_at": datetime.now(UTC).isoformat(),
            "worker_pid": _worker_pid(),
            "storages": [
                {
                    "id": item.id,
                    "name": item.name,
                    "stable_identity": item.stable_identity,
                    "mountpoint": item.mountpoint,
                    "presentation_device": item.presentation_device,
                    "filesystem_uuid": item.filesystem_uuid,
                    "topology_state": item.topology_state,
                    "config": item.config_json,
                }
                for item in session.scalars(select(StorageEntity))
            ],
            "paths": [
                {
                    "id": item.id,
                    "storage_entity_id": item.storage_entity_id,
                    "stable_path_identity": item.stable_path_identity,
                    "kernel_path": item.kernel_path,
                    "state": item.state,
                    "active": item.active,
                    "optimized": item.optimized,
                }
                for item in session.scalars(select(StoragePath))
            ],
            "events": [
                {
                    "id": item.id,
                    "storage_entity_id": item.storage_entity_id,
                    "event_type": item.event_type,
                    "previous_state": item.previous_state,
                    "resulting_state": item.resulting_state,
                    "details": item.details_json,
                    "occurred_at": _json_value(item.occurred_at),
                }
                for item in session.scalars(
                    select(StorageRedundancyEvent).order_by(StorageRedundancyEvent.occurred_at)
                )
            ],
            "telemetry": [
                {
                    "entity_type": entities[item.entity_id].entity_type,
                    "entity_stable_id": entities[item.entity_id].stable_id,
                    "entity_name": entities[item.entity_id].display_name,
                    "metric_id": item.metric_id,
                    "value": item.value if item.value is not None else item.value_text,
                    "quality": item.quality,
                    "source": item.source,
                    "observed_at": _json_value(item.observed_at),
                }
                for item in samples
                if item.entity_id in entities
            ],
            "rollups": [
                {
                    "entity_stable_id": entities[item.entity_id].stable_id,
                    "metric_id": item.metric_id,
                    "resolution": item.resolution,
                    "period_start": _json_value(item.period_start),
                    "minimum": item.minimum,
                    "maximum": item.maximum,
                    "mean": item.mean,
                    "count": item.sample_count,
                }
                for item in session.scalars(select(MetricRollup))
                if item.entity_id in entities
            ],
        }
    Path(args.output).write_text(json.dumps(document, indent=2), encoding="utf-8")
    engine.dispose()


def _worker_pid() -> int | None:
    result = subprocess.run(
        ["/usr/bin/systemctl", "show", "--property=MainPID", "--value", "hoardarr-worker"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    try:
        value = int(result.stdout.strip())
    except ValueError:
        return None
    return value or None


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    commands = result.add_subparsers(dest="command", required=True)
    for name, handler in (("register-shared", register_shared), ("register-local", register_local)):
        item = commands.add_parser(name)
        item.add_argument("--node", required=True)
        item.add_argument("--name", default="Local SSD")
        item.add_argument("--wwid", required=True)
        item.add_argument("--path", action="append", required=True)
        item.add_argument("--mountpoint", required=True)
        item.add_argument("--mapper", default="")
        item.add_argument("--filesystem-uuid", required=True)
        item.set_defaults(handler=handler)
    event = commands.add_parser("event")
    event.add_argument("--node", required=True)
    event.add_argument("--peer-node", required=True)
    event.add_argument("--wwid", required=True)
    event.add_argument("--event-type", required=True)
    event.add_argument("--previous-state")
    event.add_argument("--resulting-state", required=True)
    event.set_defaults(handler=add_event)
    ui = commands.add_parser("provision-ui")
    ui.add_argument("--node", required=True)
    ui.add_argument("--username", default="validation-owner")
    ui.add_argument("--password", required=True)
    ui.set_defaults(handler=provision_ui)
    collection = commands.add_parser("collect")
    collection.set_defaults(handler=collect)
    hardware = commands.add_parser("queue-hardware")
    hardware.add_argument("--node", required=True)
    hardware.add_argument("--username", default="validation-owner")
    hardware.set_defaults(handler=queue_hardware)
    waiting = commands.add_parser("wait-operation")
    waiting.add_argument("--operation-id", required=True)
    waiting.add_argument("--timeout", type=float, default=60.0)
    waiting.set_defaults(handler=wait_operation)
    evidence = commands.add_parser("export")
    evidence.add_argument("--node", required=True)
    evidence.add_argument("--output", required=True)
    evidence.set_defaults(handler=export)
    return result


def main() -> None:
    args = parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    os.umask(0o077)
    main()
