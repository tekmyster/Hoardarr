from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from hoardarr.operations.service import document_hash

_SNAPSHOT_NAME = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,95}$")
_RESOURCE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_ACTIONS = frozenset({"create", "delete", "restore", "clone"})


class SnapshotPlanError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def build_snapshot_plan(
    *,
    volume: Mapping[str, Any],
    provider_guid: str,
    action: str,
    snapshot_name: str | None = None,
    snapshot: Mapping[str, Any] | None = None,
    clone_name: str | None = None,
    scheduled: bool = False,
) -> dict[str, Any]:
    action = action.strip().lower()
    if action not in _ACTIONS:
        raise SnapshotPlanError("snapshot_action_invalid", "The snapshot action is unsupported.")
    if volume.get("provider") != "zfs" or volume.get("resource_type") not in {
        "dataset",
        "zvol",
    }:
        raise SnapshotPlanError(
            "snapshot_provider_unsupported",
            "This storage provider does not expose a production snapshot operation.",
        )
    provider_resource_id = str(volume.get("provider_resource_id") or "")
    if not provider_resource_id or "@" in provider_resource_id or "/" not in provider_resource_id:
        raise SnapshotPlanError("snapshot_volume_invalid", "The ZFS storage identity is invalid.")
    if not re.fullmatch(r"[0-9]+", provider_guid):
        raise SnapshotPlanError(
            "snapshot_provider_identity_unavailable",
            "The live ZFS resource identity is unavailable; no snapshot change was planned.",
        )

    selected_name = str(snapshot_name or (snapshot or {}).get("snapshot_name") or "").strip()
    if not _SNAPSHOT_NAME.fullmatch(selected_name):
        raise SnapshotPlanError(
            "snapshot_name_invalid",
            (
                "Use a bounded snapshot name containing letters, numbers, dots, dashes, "
                "or underscores."
            ),
        )
    provider_snapshot_id = f"{provider_resource_id}@{selected_name}"
    snapshot_document: dict[str, object] | None = None
    if action != "create":
        if snapshot is None or snapshot.get("provider_snapshot_id") != provider_snapshot_id:
            raise SnapshotPlanError(
                "snapshot_identity_invalid", "The selected snapshot identity is invalid."
            )
        snapshot_guid = snapshot.get("provider_guid")
        if not isinstance(snapshot_guid, str) or not re.fullmatch(r"[0-9]+", snapshot_guid):
            raise SnapshotPlanError(
                "snapshot_identity_unavailable",
                "The live snapshot identity is unavailable; no change was planned.",
            )
        snapshot_document = {
            "id": snapshot.get("id"),
            "provider_snapshot_id": provider_snapshot_id,
            "snapshot_name": selected_name,
            "provider_guid": snapshot_guid,
        }

    target_resource_id = None
    target_mountpoint = None
    if action == "clone":
        candidate = str(clone_name or "").strip().lower()
        if not _RESOURCE_NAME.fullmatch(candidate):
            raise SnapshotPlanError(
                "snapshot_clone_name_invalid",
                "Use a lower-case clone name containing letters, numbers, dashes, or underscores.",
            )
        target_resource_id = f"{provider_resource_id.split('/', 1)[0]}/{candidate}"
        if target_resource_id == provider_resource_id:
            raise SnapshotPlanError(
                "snapshot_clone_target_invalid", "The clone must use a new provider resource name."
            )
        if volume.get("resource_type") == "dataset":
            target_mountpoint = f"/srv/hoardarr/volumes/{candidate}"
    confirmation = {
        "create": "CREATE SNAPSHOT",
        "delete": "DELETE SNAPSHOT",
        "restore": "RESTORE SNAPSHOT",
        "clone": "CREATE CLONE",
    }[action]
    plan = {
        "schema_version": 1,
        "kind": "storage.volume.snapshot",
        "action": action,
        "scheduled": bool(scheduled),
        "volume": {
            "id": volume.get("id"),
            "stable_identity": volume.get("stable_identity"),
            "name": volume.get("name"),
            "provider": "zfs",
            "resource_type": volume.get("resource_type"),
            "provider_resource_id": provider_resource_id,
            "provider_guid": provider_guid,
            "presentation": volume.get("presentation"),
        },
        "snapshot": snapshot_document
        or {
            "id": None,
            "provider_snapshot_id": provider_snapshot_id,
            "snapshot_name": selected_name,
            "provider_guid": None,
        },
        "target_resource_id": target_resource_id,
        "target_mountpoint": target_mountpoint,
        "confirmation": confirmation,
        "risk": (
            "Restoring replaces current data with the selected point in time."
            if action == "restore"
            else "This deletes only the selected provider snapshot, not the live storage."
            if action == "delete"
            else "The live storage is not modified."
        ),
    }
    return {**plan, "plan_sha256": document_hash(plan)}


def validate_snapshot_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(plan))
    supplied_hash = raw.pop("plan_sha256", None)
    if not isinstance(supplied_hash, str) or document_hash(raw) != supplied_hash:
        raise SnapshotPlanError("snapshot_plan_changed", "The snapshot plan changed after review.")
    volume = raw.get("volume")
    snapshot = raw.get("snapshot")
    if not isinstance(volume, Mapping) or not isinstance(snapshot, Mapping):
        raise SnapshotPlanError("snapshot_plan_invalid", "The snapshot plan is incomplete.")
    rebuilt = build_snapshot_plan(
        volume=volume,
        provider_guid=str(volume.get("provider_guid") or ""),
        action=str(raw.get("action") or ""),
        snapshot_name=str(snapshot.get("snapshot_name") or ""),
        snapshot=snapshot if raw.get("action") != "create" else None,
        clone_name=(
            str(raw.get("target_resource_id") or "").split("/", 1)[-1]
            if raw.get("action") == "clone"
            else None
        ),
        scheduled=raw.get("scheduled") is True,
    )
    if rebuilt != dict(plan):
        raise SnapshotPlanError("snapshot_plan_changed", "The snapshot plan changed after review.")
    return rebuilt


def snapshot_command(plan: Mapping[str, Any]) -> list[str]:
    validated = validate_snapshot_plan(plan)
    snapshot_id = str(validated["snapshot"]["provider_snapshot_id"])
    action = validated["action"]
    if action == "create":
        return ["zfs", "snapshot", snapshot_id]
    if action == "delete":
        return ["zfs", "destroy", snapshot_id]
    if action == "restore":
        return ["zfs", "rollback", snapshot_id]
    if validated["target_mountpoint"] is not None:
        return [
            "zfs",
            "clone",
            "-o",
            f"mountpoint={validated['target_mountpoint']}",
            snapshot_id,
            str(validated["target_resource_id"]),
        ]
    return ["zfs", "clone", snapshot_id, str(validated["target_resource_id"])]
