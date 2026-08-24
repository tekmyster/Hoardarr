from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.db.models import Operation, StorageVolumeSnapshot
from hoardarr.storage.snapshots import (
    SnapshotLifecycleError,
    apply_snapshot_result,
    configure_schedule,
    queue_due_snapshots,
    snapshot_documents,
)
from hoardarr.storage.volumes import register_volume


@pytest.fixture
def snapshot_session(tmp_path: Path):  # type: ignore[no-untyped-def]
    database_url = f"sqlite:///{(tmp_path / 'snapshots.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)
    with factory() as session:
        yield session
    engine.dispose()


def zfs_volume(session):  # type: ignore[no-untyped-def]
    volume, _created = register_volume(
        session,
        {
            "provider": "zfs",
            "resource_type": "dataset",
            "provider_resource_id": "tank/media",
            "name": "media",
            "presentation": "file",
            "mountpoint": "/srv/media",
            "filesystem_type": "zfs",
            "filesystem_uuid": "123456789",
            "lifecycle_state": "active",
            "config": {"provider_guid": "123456789"},
            "capabilities": {
                "snapshot": {"support": "supported", "availability": "available"}
            },
        },
    )
    return volume


def test_bounded_schedule_queues_once_and_retention_creates_durable_delete(
    snapshot_session,
) -> None:
    start = datetime(2026, 8, 24, 12, tzinfo=UTC)
    volume = zfs_volume(snapshot_session)
    schedule = configure_schedule(
        snapshot_session,
        volume,
        enabled=True,
        interval_hours=1,
        retention_count=2,
        prefix="hourly",
        now=start,
    )
    for index in range(2):
        snapshot_session.add(
            StorageVolumeSnapshot(
                volume_id=volume.id,
                provider_snapshot_id=f"tank/media@hourly-old-{index}",
                snapshot_name=f"hourly-old-{index}",
                provider_guid=str(800 + index),
                state="available",
                created_at=start - timedelta(hours=2 - index),
                updated_at=start - timedelta(hours=2 - index),
            )
        )
    snapshot_session.flush()

    assert queue_due_snapshots(snapshot_session, now=start + timedelta(hours=1)) == 1
    assert queue_due_snapshots(snapshot_session, now=start + timedelta(hours=1)) == 0
    operation = snapshot_session.scalar(
        select(Operation).where(Operation.kind == "storage.volume.snapshot")
    )
    assert operation is not None
    plan = operation.request_json["plan"]
    result = apply_snapshot_result(
        snapshot_session,
        operation=operation,
        plan=plan,
        result={
            "operation_id": operation.id,
            "action": "create",
            "snapshot": {
                "provider_snapshot_id": plan["snapshot"]["provider_snapshot_id"],
                "snapshot_name": plan["snapshot"]["snapshot_name"],
                "provider_guid": "999",
                "detail": {"used": "0"},
            },
        },
    )
    assert result["snapshot_id"]
    assert schedule.last_run_at is not None
    operations = list(
        snapshot_session.scalars(
            select(Operation)
            .where(Operation.kind == "storage.volume.snapshot")
            .order_by(Operation.created_at)
        )
    )
    assert len(operations) == 2
    assert operations[1].request_json["plan"]["action"] == "delete"
    assert len(snapshot_documents(snapshot_session, volume.id)) == 3


def test_schedule_rejects_unsupported_provider(snapshot_session) -> None:
    volume, _created = register_volume(
        snapshot_session,
        {
            "provider": "filesystem",
            "resource_type": "filesystem",
            "provider_resource_id": "uuid-media",
            "name": "media",
            "presentation": "file",
            "mountpoint": "/srv/media",
            "filesystem_type": "xfs",
            "filesystem_uuid": "uuid-media",
            "lifecycle_state": "active",
            "config": {},
        },
    )
    with pytest.raises(SnapshotLifecycleError, match="does not support"):
        configure_schedule(
            snapshot_session,
            volume,
            enabled=True,
            interval_hours=24,
            retention_count=12,
            prefix="hoardarr-auto",
        )
