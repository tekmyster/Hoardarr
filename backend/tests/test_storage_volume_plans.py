from __future__ import annotations

import pytest

from hoardarr.storage.volume_plans import (
    VolumePlanError,
    build_guided_volume_plan,
    validate_guided_volume_plan,
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
