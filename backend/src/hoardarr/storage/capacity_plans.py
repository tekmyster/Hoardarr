from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from hoardarr.operations.service import document_hash

MAX_CAPACITY_BYTES = 1 << 60


class CapacityPlanError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _bounded_bytes(value: object, field: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= MAX_CAPACITY_BYTES
    ):
        raise CapacityPlanError("volume_capacity_invalid", f"{field} is outside safe bounds.")
    return value


def build_capacity_plan(
    *,
    volume: Mapping[str, Any],
    provider_guid: str,
    quota_bytes: int | None = None,
    reservation_bytes: int | None = None,
    thin_provisioned: bool | None = None,
) -> dict[str, Any]:
    if volume.get("provider") != "zfs" or volume.get("resource_type") not in {"dataset", "zvol"}:
        raise CapacityPlanError(
            "volume_capacity_provider_unsupported",
            "This provider does not expose a production capacity-limit operation.",
        )
    provider_resource_id = str(volume.get("provider_resource_id") or "")
    if not provider_resource_id or "@" in provider_resource_id or "/" not in provider_resource_id:
        raise CapacityPlanError("volume_capacity_identity_invalid", "The ZFS identity is invalid.")
    if re.fullmatch(r"[0-9]+", provider_guid) is None:
        raise CapacityPlanError(
            "volume_capacity_identity_unavailable",
            "The live provider identity is unavailable; no capacity change was planned.",
        )

    resource_type = str(volume["resource_type"])
    if resource_type == "dataset":
        if thin_provisioned is not None or quota_bytes is None or reservation_bytes is None:
            raise CapacityPlanError(
                "volume_capacity_invalid", "Datasets require an explicit quota and reservation."
            )
        quota = _bounded_bytes(quota_bytes, "Quota")
        reservation = _bounded_bytes(reservation_bytes, "Reservation")
        if quota and reservation > quota:
            raise CapacityPlanError(
                "volume_capacity_invalid", "The reservation cannot exceed the quota."
            )
        target = {
            "quota_bytes": quota,
            "reservation_bytes": reservation,
            "thin_provisioned": None,
        }
        properties = {
            "quota": "none" if quota == 0 else str(quota),
            "reservation": "none" if reservation == 0 else str(reservation),
        }
    else:
        if (
            quota_bytes is not None
            or reservation_bytes is not None
            or not isinstance(thin_provisioned, bool)
        ):
            raise CapacityPlanError(
                "volume_capacity_invalid", "Zvols require an explicit thin-allocation choice."
            )
        target = {
            "quota_bytes": None,
            "reservation_bytes": None,
            "thin_provisioned": thin_provisioned,
        }
        properties = {"refreservation": "none" if thin_provisioned else "auto"}

    plan = {
        "schema_version": 1,
        "kind": "storage.volume.capacity",
        "volume": {
            "id": volume.get("id"),
            "stable_identity": volume.get("stable_identity"),
            "name": volume.get("name"),
            "provider": "zfs",
            "resource_type": resource_type,
            "provider_resource_id": provider_resource_id,
            "provider_guid": provider_guid,
        },
        "target": target,
        "properties": properties,
        "confirmation": "APPLY CAPACITY LIMITS",
        "risk": (
            "Existing data is not deleted. A quota can cause future writes to fail "
            "when the limit is reached."
            if resource_type == "dataset"
            else "Existing data is not deleted. Thin allocation can fail future writes "
            "if the pool fills."
        ),
    }
    return {**plan, "plan_sha256": document_hash(plan)}


def validate_capacity_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(plan))
    supplied_hash = raw.pop("plan_sha256", None)
    if not isinstance(supplied_hash, str) or document_hash(raw) != supplied_hash:
        raise CapacityPlanError(
            "volume_capacity_plan_changed", "The capacity plan changed after review."
        )
    volume = raw.get("volume")
    target = raw.get("target")
    if not isinstance(volume, Mapping) or not isinstance(target, Mapping):
        raise CapacityPlanError("volume_capacity_plan_invalid", "The capacity plan is incomplete.")
    rebuilt = build_capacity_plan(
        volume=volume,
        provider_guid=str(volume.get("provider_guid") or ""),
        quota_bytes=target.get("quota_bytes"),
        reservation_bytes=target.get("reservation_bytes"),
        thin_provisioned=target.get("thin_provisioned"),
    )
    if rebuilt != dict(plan):
        raise CapacityPlanError(
            "volume_capacity_plan_changed", "The capacity plan changed after review."
        )
    return rebuilt


def capacity_command(plan: Mapping[str, Any]) -> list[str]:
    validated = validate_capacity_plan(plan)
    properties = validated["properties"]
    return [
        "zfs",
        "set",
        *[f"{name}={value}" for name, value in sorted(properties.items())],
        str(validated["volume"]["provider_resource_id"]),
    ]
