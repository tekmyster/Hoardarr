from __future__ import annotations

import pytest

from hoardarr.storage.snapshot_plans import (
    SnapshotPlanError,
    build_snapshot_plan,
    snapshot_command,
    validate_snapshot_plan,
)


def volume() -> dict[str, object]:
    return {
        "id": "volume-1",
        "stable_identity": "zfs:dataset:tank/media",
        "name": "media",
        "provider": "zfs",
        "resource_type": "dataset",
        "provider_resource_id": "tank/media",
        "presentation": "file",
    }


def test_create_restore_delete_and_clone_commands_are_fixed_argv() -> None:
    created = build_snapshot_plan(
        volume=volume(), provider_guid="12345", action="create", snapshot_name="before-upgrade"
    )
    assert validate_snapshot_plan(created) == created
    assert snapshot_command(created) == ["zfs", "snapshot", "tank/media@before-upgrade"]
    existing = {
        "id": "snapshot-1",
        "snapshot_name": "before-upgrade",
        "provider_snapshot_id": "tank/media@before-upgrade",
        "provider_guid": "67890",
    }
    for action, expected in {
        "delete": ["zfs", "destroy", "tank/media@before-upgrade"],
        "restore": ["zfs", "rollback", "tank/media@before-upgrade"],
    }.items():
        plan = build_snapshot_plan(
            volume=volume(), provider_guid="12345", action=action, snapshot=existing
        )
        assert snapshot_command(plan) == expected
    clone = build_snapshot_plan(
        volume=volume(),
        provider_guid="12345",
        action="clone",
        snapshot=existing,
        clone_name="media-test",
    )
    assert snapshot_command(clone) == [
        "zfs",
        "clone",
        "-o",
        "mountpoint=/srv/hoardarr/volumes/media-test",
        "tank/media@before-upgrade",
        "tank/media-test",
    ]


@pytest.mark.parametrize(
    ("name", "code"),
    [("bad/name", "snapshot_name_invalid"), ("$(touch x)", "snapshot_name_invalid")],
)
def test_invalid_snapshot_names_fail_closed(name: str, code: str) -> None:
    with pytest.raises(SnapshotPlanError) as raised:
        build_snapshot_plan(
            volume=volume(), provider_guid="12345", action="create", snapshot_name=name
        )
    assert raised.value.code == code


def test_unsupported_provider_and_mutated_plan_fail_closed() -> None:
    unsupported = {**volume(), "provider": "filesystem", "resource_type": "filesystem"}
    with pytest.raises(SnapshotPlanError, match="does not expose"):
        build_snapshot_plan(
            volume=unsupported, provider_guid="12345", action="create", snapshot_name="safe"
        )
    plan = build_snapshot_plan(
        volume=volume(), provider_guid="12345", action="create", snapshot_name="safe"
    )
    plan["snapshot"]["snapshot_name"] = "changed"
    with pytest.raises(SnapshotPlanError, match="changed"):
        validate_snapshot_plan(plan)
