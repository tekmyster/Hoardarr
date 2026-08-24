from __future__ import annotations

import pytest

from hoardarr.storage.volume_plans import (
    VolumePlanError,
    build_guided_volume_plan,
    validate_guided_volume_plan,
    volume_create_command,
)


def pool(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "zfs:tank",
        "name": "tank",
        "type": "ZFS",
        "status": "online",
        "pool_guid": "1234567890123456789",
        "free_bytes": 100_000_000_000,
        "degraded": False,
    }
    value.update(updates)
    return value


def test_guided_media_plan_uses_plain_safe_defaults_and_is_immutable() -> None:
    plan = build_guided_volume_plan([pool()], name="Movies", purpose="media")
    assert plan["provider_resource_id"] == "tank/movies"
    assert plan["resource_type"] == "dataset"
    assert plan["properties"] == {
        "compression": "zstd",
        "recordsize": "1M",
        "atime": "off",
        "mountpoint": "/srv/hoardarr/volumes/movies",
    }
    assert plan["ready"] is True
    assert validate_guided_volume_plan(plan) == plan
    changed = {**plan, "name": "tv"}
    with pytest.raises(VolumePlanError) as raised:
        validate_guided_volume_plan(changed)
    assert raised.value.code == "volume_plan_changed"


def test_guided_vm_plan_requires_bounded_capacity_and_never_hides_blockers() -> None:
    missing = build_guided_volume_plan([pool()], name="vm-storage", purpose="vm")
    assert missing["ready"] is False
    assert missing["blockers"][0]["code"] == "volume_size_required"

    planned = build_guided_volume_plan(
        [pool()], name="vm-storage", purpose="vm", size_bytes=20_000_000_000
    )
    assert planned["resource_type"] == "zvol"
    assert planned["presentation"] == "block"
    assert planned["properties"]["sparse"] is True

    too_large = build_guided_volume_plan(
        [pool(free_bytes=5_000_000_000)],
        name="vm-storage",
        purpose="vm",
        size_bytes=5_000_000_000,
    )
    assert too_large["ready"] is False
    assert "volume_capacity_insufficient" in {blocker["code"] for blocker in too_large["blockers"]}


def test_guided_plan_rejects_unproven_or_unhealthy_backend_identity() -> None:
    with pytest.raises(VolumePlanError) as raised:
        build_guided_volume_plan([pool(pool_guid=None)], name="media", purpose="media")
    assert raised.value.code == "volume_backend_unavailable"

    degraded = build_guided_volume_plan(
        [pool(status="degraded", degraded=True)], name="media", purpose="media"
    )
    assert degraded["ready"] is False
    assert degraded["blockers"][0]["code"] == "volume_pool_not_healthy"


def test_advanced_dataset_plan_applies_only_supported_exact_zfs_properties() -> None:
    plan = build_guided_volume_plan(
        [pool()],
        name="metadata",
        purpose="general",
        advanced=True,
        resource_type="dataset",
        compression="lz4",
        recordsize="32K",
        atime="on",
        mountpoint="/mnt/tank/metadata",
    )
    assert plan["mode"] == "advanced"
    assert plan["properties"] == {
        "compression": "lz4",
        "recordsize": "32K",
        "atime": "on",
        "mountpoint": "/mnt/tank/metadata",
    }
    assert validate_guided_volume_plan(plan) == plan
    assert volume_create_command(plan) == [
        "zfs",
        "create",
        "-o",
        "atime=on",
        "-o",
        "compression=lz4",
        "-o",
        "mountpoint=/mnt/tank/metadata",
        "-o",
        "recordsize=32K",
        "tank/metadata",
    ]

    with pytest.raises(VolumePlanError) as raised:
        build_guided_volume_plan(
            [pool()],
            name="unsafe",
            purpose="general",
            advanced=True,
            resource_type="dataset",
            mountpoint="/etc/hoardarr-overwrite",
        )
    assert raised.value.code == "volume_mountpoint_invalid"


def test_advanced_zvol_plan_preserves_exact_geometry_and_requires_size() -> None:
    plan = build_guided_volume_plan(
        [pool()],
        name="vm-fast",
        purpose="vm",
        size_bytes=30_000_000_000,
        advanced=True,
        resource_type="zvol",
        compression="zstd-3",
        volblocksize="8K",
        sparse=False,
    )
    assert plan["properties"] == {
        "compression": "zstd-3",
        "volblocksize": "8K",
        "sparse": False,
    }
    assert validate_guided_volume_plan(plan) == plan
    assert volume_create_command(plan) == [
        "zfs",
        "create",
        "-V",
        "30000000000",
        "-o",
        "compression=zstd-3",
        "-o",
        "sparse=off",
        "-o",
        "volblocksize=8K",
        "tank/vm-fast",
    ]

    with pytest.raises(VolumePlanError) as raised:
        build_guided_volume_plan([pool()], name="vm-fast", purpose="vm", compression="lz4")
    assert raised.value.code == "volume_advanced_settings_disabled"
