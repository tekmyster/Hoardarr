from __future__ import annotations

from copy import deepcopy

import pytest

from hoardarr.storage.capacity_plans import (
    CapacityPlanError,
    build_capacity_plan,
    capacity_command,
    validate_capacity_plan,
)


def volume(resource_type: str = "dataset") -> dict[str, object]:
    return {
        "id": "volume-id",
        "stable_identity": f"zfs:{resource_type}:tank/media",
        "name": "media",
        "provider": "zfs",
        "resource_type": resource_type,
        "provider_resource_id": "tank/media",
    }


def test_dataset_capacity_plan_is_immutable_and_constructs_fixed_argv() -> None:
    plan = build_capacity_plan(
        volume=volume(),
        provider_guid="12345",
        quota_bytes=20 * 1024**3,
        reservation_bytes=2 * 1024**3,
    )

    assert validate_capacity_plan(plan) == plan
    assert capacity_command(plan) == [
        "zfs",
        "set",
        f"quota={20 * 1024**3}",
        f"reservation={2 * 1024**3}",
        "tank/media",
    ]
    assert plan["confirmation"] == "APPLY CAPACITY LIMITS"


def test_zvol_allocation_plan_uses_refreservation_without_fake_quota() -> None:
    thin = build_capacity_plan(
        volume=volume("zvol"), provider_guid="12345", thin_provisioned=True
    )
    thick = build_capacity_plan(
        volume=volume("zvol"), provider_guid="12345", thin_provisioned=False
    )

    assert capacity_command(thin) == ["zfs", "set", "refreservation=none", "tank/media"]
    assert capacity_command(thick) == ["zfs", "set", "refreservation=auto", "tank/media"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"quota_bytes": 1, "reservation_bytes": 2}, "cannot exceed"),
        ({"quota_bytes": -1, "reservation_bytes": 0}, "safe bounds"),
        ({"quota_bytes": 1, "reservation_bytes": 0, "thin_provisioned": True}, "explicit"),
    ],
)
def test_dataset_capacity_plan_rejects_invalid_inputs(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(CapacityPlanError, match=message):
        build_capacity_plan(volume=volume(), provider_guid="12345", **kwargs)  # type: ignore[arg-type]


def test_capacity_plan_rejects_tampering_and_unstable_identity() -> None:
    plan = build_capacity_plan(
        volume=volume(), provider_guid="12345", quota_bytes=0, reservation_bytes=0
    )
    changed = deepcopy(plan)
    changed["properties"]["quota"] = "1"

    with pytest.raises(CapacityPlanError, match="changed"):
        validate_capacity_plan(changed)
    with pytest.raises(CapacityPlanError, match="identity"):
        build_capacity_plan(
            volume=volume(), provider_guid="not-reported", quota_bytes=0, reservation_bytes=0
        )
