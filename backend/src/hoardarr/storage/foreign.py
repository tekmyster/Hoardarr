from __future__ import annotations

import shutil
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.db.models import HardwareSnapshot, StorageBackend
from hoardarr.operations.service import document_hash

SUPPORTED_FILESYSTEMS = {
    "ext4": {"name": "ext4", "read_only_options": ["ro", "noload"]},
    "xfs": {"name": "XFS", "read_only_options": ["ro", "norecovery"]},
    "btrfs": {"name": "Btrfs", "read_only_options": ["ro", "nologreplay"]},
    "ntfs": {"name": "NTFS", "read_only_options": ["ro"]},
    "ntfs3": {"name": "NTFS", "read_only_options": ["ro"]},
    "exfat": {"name": "exFAT", "read_only_options": ["ro"]},
}
STACK_SIGNATURES = {
    "linux_raid_member": ("linux_md", "Linux MD", "mdadm"),
    "lvm2_member": ("lvm", "Linux LVM", "pvs"),
    "zfs_member": ("zfs", "ZFS", "zpool"),
}


def _text(value: Any, *, limit: int = 512) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:limit] if normalized else None


def _signature_documents(disk: dict[str, Any]) -> list[dict[str, str | None]]:
    documents: list[dict[str, str | None]] = []
    sources: list[Any] = [disk.get("signatures")]
    partitions = disk.get("partitions")
    if isinstance(partitions, list):
        for partition in partitions[:256]:
            if not isinstance(partition, dict):
                continue
            sources.append(partition.get("signatures"))
            filesystem = partition.get("filesystem")
            if isinstance(filesystem, dict):
                sources.append([filesystem])
    for source in sources:
        if not isinstance(source, list):
            continue
        for raw in source[:256]:
            if not isinstance(raw, dict):
                continue
            signature_type = _text(raw.get("type"), limit=64)
            if signature_type is None:
                continue
            document = {
                "type": signature_type,
                "usage": _text(raw.get("usage"), limit=64),
                "uuid": _text(raw.get("uuid"), limit=256),
                "label": _text(raw.get("label"), limit=256),
                "source": _text(raw.get("source"), limit=64) or "not_reported",
            }
            if document not in documents:
                documents.append(document)
    return sorted(
        documents,
        key=lambda item: (
            str(item["usage"] or ""),
            str(item["type"]),
            str(item["uuid"] or ""),
        ),
    )


def _confidence(disk: dict[str, Any], signatures: list[dict[str, str | None]]) -> str:
    scan = disk.get("signature_scan")
    status = scan.get("status") if isinstance(scan, dict) else None
    if status == "complete" and signatures:
        return "high"
    if status == "partial" and signatures:
        return "medium"
    if signatures:
        return "low"
    return "unknown"


def _member(disk: dict[str, Any]) -> dict[str, Any]:
    signatures = _signature_documents(disk)
    partitions = disk.get("partitions")
    mountpoints = {
        item
        for item in (disk.get("mountpoints") if isinstance(disk.get("mountpoints"), list) else [])
        if isinstance(item, str) and item.startswith("/")
    }
    if isinstance(partitions, list):
        for partition in partitions[:256]:
            if not isinstance(partition, dict):
                continue
            mountpoints.update(
                item
                for item in (
                    partition.get("mountpoints")
                    if isinstance(partition.get("mountpoints"), list)
                    else []
                )
                if isinstance(item, str) and item.startswith("/")
            )
    scan = disk.get("signature_scan")
    return {
        "device_id": _text(disk.get("id")) or "identity-not-reported",
        "kernel_path": _text(disk.get("kernel_path"), limit=4096),
        "model": _text(disk.get("model"), limit=256) or "Not reported",
        "capacity_bytes": disk.get("capacity_bytes")
        if isinstance(disk.get("capacity_bytes"), int) and disk["capacity_bytes"] >= 0
        else None,
        "stable_identity": disk.get("stable_identity") is True,
        "system_device": disk.get("system_disk") is True or disk.get("system_device") is True,
        "read_only": disk.get("read_only") is True,
        "removable": disk.get("removable") is True,
        "mounted": bool(mountpoints),
        "mountpoints": sorted(mountpoints)[:256],
        "signature_scan": {
            "status": _text(scan.get("status"), limit=32) if isinstance(scan, dict) else None,
            "source": _text(scan.get("source"), limit=64) if isinstance(scan, dict) else None,
            "reason": _text(scan.get("reason"), limit=1024) if isinstance(scan, dict) else None,
        },
        "confidence": _confidence(disk, signatures),
        "signatures": signatures,
    }


def _candidate_document(
    *,
    profile: str,
    name: str,
    members: list[dict[str, Any]],
    managed_identities: set[str],
    tool: str | None,
) -> dict[str, Any]:
    signature_types = sorted(
        {str(item["type"]).casefold() for member in members for item in member["signatures"]}
    )
    filesystems = sorted(
        {
            SUPPORTED_FILESYSTEMS[item]["name"]
            for item in signature_types
            if item in SUPPORTED_FILESYSTEMS
        }
    )
    confidence_order = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
    confidence = min(
        (str(item["confidence"]) for item in members),
        key=lambda value: confidence_order[value],
        default="unknown",
    )
    blockers: list[str] = []
    warnings: list[str] = []
    if any(item["system_device"] for item in members):
        blockers.append("Protected system storage is never eligible for foreign import.")
    if any(not item["stable_identity"] for item in members):
        blockers.append("Every source member requires stable hardware identity before inspection.")
    if any(item["device_id"] in managed_identities for item in members):
        blockers.append("A source member is already assigned to managed Hoardarr storage.")
    if any(item["mounted"] for item in members):
        blockers.append(
            "A source member is already mounted; automatic foreign inspection is blocked."
        )
    if confidence == "unknown":
        blockers.append("The persisted scan did not report a recognized on-media signature.")
    elif confidence != "high":
        warnings.append(
            "Signature evidence is incomplete. Hoardarr will require a fresh privileged read-only "
            "fingerprint before any inspection plan can be approved."
        )
    if profile in {"linux_md", "lvm", "zfs"}:
        warnings.append(
            "Member completeness and health are not inferred from matching metadata alone; "
            "no array, volume group, or pool was activated."
        )
    if tool is not None and shutil.which(tool) is None:
        blockers.append(f"The required read-only {tool} provider is not installed on this host.")

    safety_blocked = bool(blockers)
    inspection_available = False
    if profile == "standalone_filesystem" and not blockers and filesystems:
        blockers.append(
            "The immutable provider-specific no-recovery inspection executor is not enabled yet."
        )
    elif profile != "standalone_filesystem" and not blockers:
        blockers.append(
            "A provider-specific no-activation member preview is required before this stack "
            "can be mounted."
        )
    state = "blocked" if safety_blocked else "degraded-review"
    capacity_values = [item["capacity_bytes"] for item in members]
    capacity = (
        sum(int(value) for value in capacity_values)
        if capacity_values and all(isinstance(value, int) for value in capacity_values)
        else None
    )
    fingerprint = {
        "profile": profile,
        "members": [item["device_id"] for item in members],
        "signatures": signature_types,
    }
    return {
        "id": f"foreign:{document_hash(fingerprint)[:24]}",
        "profile": profile,
        "profile_name": name,
        "origin": {
            "name": "Not reported",
            "confidence": "unknown",
            "reason": (
                "Filesystem and volume metadata do not reliably identify the prior NAS product."
            ),
        },
        "confidence": confidence,
        "state": state,
        "members": members,
        "filesystems": filesystems,
        "signature_types": signature_types,
        "capacity_bytes": capacity,
        "warnings": warnings,
        "blockers": blockers,
        "modes": [
            {
                "id": "inspect_read_only",
                "available": inspection_available,
                "reason": blockers[0] if blockers else "Read-only inspection is unavailable.",
            },
            {
                "id": "copy_into_hoardarr",
                "available": False,
                "reason": (
                    "Read-only inspection and inventory must complete before a copy plan "
                    "is offered."
                ),
            },
            {
                "id": "adopt_in_place",
                "available": False,
                "reason": "No persistent adoption change is made during foreign-storage discovery.",
            },
        ],
        "mutation_performed": False,
    }


def assess_foreign_storage(
    session: Session,
    *,
    snapshot: HardwareSnapshot,
) -> dict[str, Any]:
    managed_identities = set(session.scalars(select(StorageBackend.stable_identity)))
    raw_disks = snapshot.payload_json.get("disks")
    disks = (
        [item for item in raw_disks[:4096] if isinstance(item, dict)]
        if isinstance(raw_disks, list)
        else []
    )
    members = [_member(item) for item in disks]

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for member in members:
        signatures = member["signatures"]
        stack_items = [
            item for item in signatures if str(item["type"]).casefold() in STACK_SIGNATURES
        ]
        if stack_items:
            for signature in stack_items:
                signature_type = str(signature["type"]).casefold()
                stack, _name, _tool = STACK_SIGNATURES[signature_type]
                group_identity = str(signature["uuid"] or member["device_id"])
                grouped[(stack, group_identity)].append(member)
            continue
        filesystem_items = [
            item for item in signatures if str(item["type"]).casefold() in SUPPORTED_FILESYSTEMS
        ]
        if filesystem_items:
            grouped[("standalone_filesystem", str(member["device_id"]))].append(member)

    candidates: list[dict[str, Any]] = []
    for (profile, _identity), candidate_members in sorted(grouped.items()):
        if profile == "standalone_filesystem":
            candidates.append(
                _candidate_document(
                    profile=profile,
                    name="Standalone filesystem",
                    members=candidate_members,
                    managed_identities=managed_identities,
                    tool=None,
                )
            )
            continue
        signature_type = next(
            item
            for item, details in STACK_SIGNATURES.items()
            if details[0] == profile
        )
        _stack, name, tool = STACK_SIGNATURES[signature_type]
        candidates.append(
            _candidate_document(
                profile=profile,
                name=name,
                members=candidate_members,
                managed_identities=managed_identities,
                tool=tool,
            )
        )

    return {
        "snapshot": {
            "id": snapshot.id,
            "captured_at": snapshot.captured_at.isoformat(),
            "sha256": snapshot.sha256,
        },
        "policy": {
            "default_access": "read_only",
            "automatic_mount": False,
            "automatic_assembly": False,
            "mutation_performed": False,
        },
        "candidates": candidates,
        "unrecognized_device_count": sum(
            1
            for member in members
            if not member["signatures"] and not member["system_device"]
        ),
    }
