from __future__ import annotations

import shutil
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hoardarr.db.models import (
    ForeignImportEvidence,
    HardwareSnapshot,
    Operation,
    StorageBackend,
    StorageEntity,
    StorageGroup,
)
from hoardarr.operations.service import document_hash
from hoardarr.storage.maintenance import IDENTITY_FIELDS, reviewed_device

SUPPORTED_FILESYSTEMS = {
    "ext4": {"name": "ext4", "read_only_options": ["ro", "noload", "nodev", "nosuid", "noexec"]},
    "xfs": {"name": "XFS", "read_only_options": ["ro", "norecovery", "nodev", "nosuid", "noexec"]},
    "btrfs": {
        "name": "Btrfs",
        "read_only_options": ["ro", "nologreplay", "nodev", "nosuid", "noexec"],
    },
    "ntfs": {"name": "NTFS", "read_only_options": ["ro", "nodev", "nosuid", "noexec"]},
    "ntfs3": {"name": "NTFS", "read_only_options": ["ro", "nodev", "nosuid", "noexec"]},
    "exfat": {"name": "exFAT", "read_only_options": ["ro", "nodev", "nosuid", "noexec"]},
}
STACK_SIGNATURES = {
    "linux_raid_member": ("linux_md", "Linux MD", "mdadm"),
    "lvm2_member": ("lvm", "Linux LVM", "pvs"),
    "zfs_member": ("zfs", "ZFS", "zdb"),
}
SELECTION_MODES = {"full", "selected_folders", "filtered"}


def _selection_value(value: str, *, pattern: bool = False) -> str:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or len(normalized) > 256
        or normalized.startswith("/")
        or "\0" in normalized
        or any(ord(character) < 32 for character in normalized)
        or any(part in {"", ".", ".."} for part in path.parts)
        or (not pattern and any(character in normalized for character in "*?[]"))
    ):
        raise ForeignStorageError(
            "foreign_selection_invalid", "An archive selection path or pattern is invalid."
        )
    return normalized


def normalize_archive_selection(value: object | None) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {"mode": "full"}
    if set(raw) - {"mode", "include_paths", "include_extensions", "include_globs", "exclude_globs"}:
        raise ForeignStorageError("foreign_selection_invalid", "The archive selection is invalid.")
    mode = raw.get("mode", "full")
    lists: dict[str, list[str]] = {}
    for field in ("include_paths", "include_extensions", "include_globs", "exclude_globs"):
        items = raw.get(field, [])
        if (
            not isinstance(items, list)
            or len(items) > 64
            or not all(isinstance(item, str) for item in items)
        ):
            raise ForeignStorageError(
                "foreign_selection_invalid", "The archive selection exceeds safe limits."
            )
        if field == "include_extensions":
            normalized = []
            for item in items:
                extension = item.strip().casefold()
                if (
                    not extension.startswith(".")
                    or len(extension) > 32
                    or any(character in extension for character in "/\\\0*?[]")
                ):
                    raise ForeignStorageError(
                        "foreign_selection_invalid", "An archive extension filter is invalid."
                    )
                normalized.append(extension)
        else:
            normalized = [_selection_value(item, pattern=field.endswith("globs")) for item in items]
        lists[field] = sorted(set(normalized))
    valid = (
        mode in SELECTION_MODES
        and (mode != "full" or not any(lists.values()))
        and (
            mode != "selected_folders"
            or (
                bool(lists["include_paths"])
                and not any(
                    lists[key] for key in ("include_extensions", "include_globs", "exclude_globs")
                )
            )
        )
        and (
            mode != "filtered"
            or bool(lists["include_extensions"] or lists["include_globs"] or lists["exclude_globs"])
        )
    )
    if not valid:
        raise ForeignStorageError("foreign_selection_invalid", "The archive selection is invalid.")
    return {"mode": mode, **lists}


def _text(value: Any, *, limit: int = 512) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:limit] if normalized else None


def _identity_text(value: Any) -> str | None:
    text = _text(value, limit=256)
    return text.casefold() if text is not None else None


class ForeignStorageError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def persist_unraid_evidence(
    session: Session,
    *,
    document: dict[str, Any],
    created_by: str,
) -> ForeignImportEvidence:
    """Persist a bounded assignment snapshot; it contains identity metadata, never credentials."""

    normalized_assignments = []
    for item in document["assignments"]:
        normalized_assignments.append(
            {
                "slot": str(item["slot"]),
                "role": str(item["role"]),
                "serial": str(item["serial"]).strip(),
                "wwn": _text(item.get("wwn"), limit=256),
                "eui64": _text(item.get("eui64"), limit=256),
                "nguid": _text(item.get("nguid"), limit=256),
                "capacity_bytes": item.get("capacity_bytes"),
                "filesystem_type": _text(item.get("filesystem_type"), limit=64),
            }
        )
    normalized = {
        "schema_version": 1,
        "source": "unraid_runtime_state",
        "captured_at": str(document["captured_at"]),
        "unraid_version": _text(document.get("unraid_version"), limit=64),
        "assignments": sorted(normalized_assignments, key=lambda item: str(item["slot"])),
    }
    digest = document_hash(normalized)
    for previous in session.scalars(
        select(ForeignImportEvidence).where(
            ForeignImportEvidence.source_type == "unraid_runtime_state",
            ForeignImportEvidence.active.is_(True),
        )
    ):
        previous.active = False
    evidence = session.scalar(
        select(ForeignImportEvidence).where(ForeignImportEvidence.document_sha256 == digest)
    )
    if evidence is None:
        evidence = ForeignImportEvidence(
            source_type="unraid_runtime_state",
            document_sha256=digest,
            evidence_json=normalized,
            active=True,
            created_by=created_by,
        )
        session.add(evidence)
    else:
        evidence.active = True
    session.flush()
    return evidence


def clear_unraid_evidence(session: Session) -> int:
    cleared = 0
    for evidence in session.scalars(
        select(ForeignImportEvidence).where(
            ForeignImportEvidence.source_type == "unraid_runtime_state",
            ForeignImportEvidence.active.is_(True),
        )
    ):
        evidence.active = False
        cleared += 1
    session.flush()
    return cleared


NAS_PLATFORM_NAMES = {
    "synology": "Synology DSM",
    "qnap": "QNAP QTS / QuTS",
    "generic_linux_nas": "Generic Linux NAS",
}


def persist_nas_evidence(
    session: Session,
    *,
    document: dict[str, Any],
    created_by: str,
) -> ForeignImportEvidence:
    """Persist a bounded source-NAS manifest without opening any current source disk."""

    normalized_members = [
        {
            "member": str(item["member"]),
            "serial": str(item["serial"]).strip(),
            "wwn": _text(item.get("wwn"), limit=256),
            "eui64": _text(item.get("eui64"), limit=256),
            "nguid": _text(item.get("nguid"), limit=256),
            "capacity_bytes": item.get("capacity_bytes"),
        }
        for item in document["members"]
    ]
    normalized = {
        "schema_version": 1,
        "source": "nas_runtime_state",
        "captured_at": str(document["captured_at"]),
        "platform": str(document["platform"]),
        "platform_marker": str(document["platform_marker"]),
        "product_version": _text(document.get("product_version"), limit=64),
        "members": sorted(normalized_members, key=lambda item: str(item["member"])),
    }
    digest = document_hash(normalized)
    for previous in session.scalars(
        select(ForeignImportEvidence).where(
            ForeignImportEvidence.source_type == "nas_runtime_state",
            ForeignImportEvidence.active.is_(True),
        )
    ):
        previous.active = False
    evidence = session.scalar(
        select(ForeignImportEvidence).where(ForeignImportEvidence.document_sha256 == digest)
    )
    if evidence is None:
        evidence = ForeignImportEvidence(
            source_type="nas_runtime_state",
            document_sha256=digest,
            evidence_json=normalized,
            active=True,
            created_by=created_by,
        )
        session.add(evidence)
    else:
        evidence.active = True
    session.flush()
    return evidence


def clear_nas_evidence(session: Session) -> int:
    cleared = 0
    for evidence in session.scalars(
        select(ForeignImportEvidence).where(
            ForeignImportEvidence.source_type == "nas_runtime_state",
            ForeignImportEvidence.active.is_(True),
        )
    ):
        evidence.active = False
        cleared += 1
    session.flush()
    return cleared


def _active_unraid_evidence(session: Session) -> ForeignImportEvidence | None:
    return session.scalar(
        select(ForeignImportEvidence)
        .where(
            ForeignImportEvidence.source_type == "unraid_runtime_state",
            ForeignImportEvidence.active.is_(True),
        )
        .order_by(ForeignImportEvidence.created_at.desc())
        .limit(1)
    )


def _active_nas_evidence(session: Session) -> ForeignImportEvidence | None:
    return session.scalar(
        select(ForeignImportEvidence)
        .where(
            ForeignImportEvidence.source_type == "nas_runtime_state",
            ForeignImportEvidence.active.is_(True),
        )
        .order_by(ForeignImportEvidence.created_at.desc())
        .limit(1)
    )


def _latest_inspection_reports(
    session: Session, candidate_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Return one bounded durable report per candidate, newest first."""

    if not candidate_ids:
        return {}
    reports: dict[str, dict[str, Any]] = {}
    ranked = (
        select(
            Operation.id.label("operation_id"),
            func.row_number()
            .over(partition_by=Operation.resource_id, order_by=Operation.created_at.desc())
            .label("candidate_rank"),
        )
        .where(
            Operation.kind == "storage.foreign.inspect",
            Operation.status == "succeeded",
            Operation.resource_type == "foreign_storage",
            Operation.resource_id.in_(candidate_ids),
            Operation.result_json.is_not(None),
        )
        .subquery()
    )
    operations = session.scalars(
        select(Operation)
        .join(ranked, ranked.c.operation_id == Operation.id)
        .where(ranked.c.candidate_rank == 1)
        .limit(256)
    )
    for operation in operations:
        candidate_id = operation.resource_id
        result = operation.result_json
        if not candidate_id or candidate_id in reports or not isinstance(result, dict):
            continue
        inventory = result.get("inventory")
        plan = operation.request_json.get("plan")
        if not isinstance(inventory, dict) or not isinstance(plan, dict):
            continue
        reports[candidate_id] = {
            "operation_id": operation.id,
            "completed_at": operation.updated_at.isoformat(),
            "hardware_snapshot_sha256": plan.get("hardware_snapshot_sha256"),
            "filesystem": result.get("filesystem"),
            "inventory": inventory,
            "access": result.get("access"),
            "persistent_mount": result.get("persistent_mount"),
            "mutation_performed": result.get("mutation_performed"),
        }
    return reports


def _assignment_matches(member: dict[str, Any], assignment: dict[str, Any]) -> bool:
    device = member["reviewed_device"]
    matched = False
    for field in ("serial", "wwn", "eui64", "nguid"):
        expected = _identity_text(assignment.get(field))
        observed = _identity_text(device.get(field))
        if expected is None or observed is None:
            continue
        if expected != observed:
            return False
        matched = True
    expected_capacity = assignment.get("capacity_bytes")
    observed_capacity = device.get("capacity_bytes")
    if isinstance(expected_capacity, int) and isinstance(observed_capacity, int):
        tolerance = max(1_048_576, expected_capacity // 10_000)
        if abs(expected_capacity - observed_capacity) > tolerance:
            return False
    return matched


def _unraid_classification(
    session: Session,
    members: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    evidence = _active_unraid_evidence(session)
    classifications: dict[str, dict[str, Any]] = {}
    matched_assignments: set[str] = set()
    ambiguous_assignments: list[str] = []
    if evidence is not None:
        for assignment in evidence.evidence_json.get("assignments", [])[:30]:
            if not isinstance(assignment, dict):
                continue
            matches = [item for item in members if _assignment_matches(item, assignment)]
            slot = str(assignment.get("slot") or "unknown")
            if len(matches) != 1:
                if len(matches) > 1:
                    ambiguous_assignments.append(slot)
                continue
            member = matches[0]
            matched_assignments.add(slot)
            role = str(assignment["role"])
            classifications[member["device_id"]] = {
                "role": role,
                "classification": "identified",
                "slot": slot,
                "reason": (f"Stable identity matches the persisted Unraid {slot} assignment."),
                "evidence_sha256": evidence.document_sha256,
                "parity_reuse_supported": False,
            }

    recognized_data_capacities = [
        item["capacity_bytes"]
        for item in members
        if any(
            str(signature["type"]).casefold() in SUPPORTED_FILESYSTEMS
            for signature in item["signatures"]
        )
        and isinstance(item["capacity_bytes"], int)
    ]
    largest_data = max(recognized_data_capacities, default=None)
    for member in members:
        if member["device_id"] in classifications or member["system_device"]:
            continue
        supported_filesystem = any(
            str(signature["type"]).casefold() in SUPPORTED_FILESYSTEMS
            for signature in member["signatures"]
        )
        scan_complete = member["signature_scan"]["status"] == "complete"
        capacity = member["capacity_bytes"]
        if supported_filesystem:
            classifications[member["device_id"]] = {
                "role": "data",
                "classification": "suspected",
                "slot": None,
                "reason": (
                    "This independently readable filesystem is compatible with an Unraid data "
                    "disk, but its format does not prove the source system."
                ),
                "evidence_sha256": None,
                "parity_reuse_supported": False,
            }
        elif (
            scan_complete
            and isinstance(capacity, int)
            and isinstance(largest_data, int)
            and capacity >= largest_data
        ):
            classifications[member["device_id"]] = {
                "role": "parity",
                "classification": "suspected",
                "slot": None,
                "reason": (
                    "A complete scan found no filesystem and capacity is not smaller than the "
                    "largest readable data disk. This is compatible with Unraid parity, but it "
                    "could also be blank, unsupported, or damaged."
                ),
                "evidence_sha256": None,
                "parity_reuse_supported": False,
            }
        else:
            classifications[member["device_id"]] = {
                "role": "unknown",
                "classification": "unknown",
                "slot": None,
                "reason": (
                    "Available evidence cannot safely assign this disk a data or parity role."
                ),
                "evidence_sha256": None,
                "parity_reuse_supported": False,
            }
    summary = None
    if evidence is not None:
        assignments = evidence.evidence_json.get("assignments", [])
        summary = {
            "id": evidence.id,
            "source": evidence.source_type,
            "document_sha256": evidence.document_sha256,
            "captured_at": evidence.evidence_json.get("captured_at"),
            "unraid_version": evidence.evidence_json.get("unraid_version"),
            "assignment_count": len(assignments) if isinstance(assignments, list) else 0,
            "matched_assignment_count": len(matched_assignments),
            "unmatched_slots": sorted(
                str(item.get("slot"))
                for item in assignments
                if isinstance(item, dict) and str(item.get("slot")) not in matched_assignments
            ),
            "ambiguous_slots": sorted(ambiguous_assignments),
        }
    return classifications, summary


def _nas_classification(
    session: Session,
    members: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    evidence = _active_nas_evidence(session)
    classifications: dict[str, dict[str, Any]] = {}
    if evidence is None:
        return classifications, None
    matched_members: set[str] = set()
    ambiguous_members: list[str] = []
    assignments = evidence.evidence_json.get("members", [])
    for assignment in assignments[:256] if isinstance(assignments, list) else []:
        if not isinstance(assignment, dict):
            continue
        matches = [item for item in members if _assignment_matches(item, assignment)]
        member_name = str(assignment.get("member") or "unknown")
        if len(matches) != 1:
            if len(matches) > 1:
                ambiguous_members.append(member_name)
            continue
        member = matches[0]
        matched_members.add(member_name)
        classifications[member["device_id"]] = {
            "platform": evidence.evidence_json["platform"],
            "platform_name": NAS_PLATFORM_NAMES[evidence.evidence_json["platform"]],
            "member": member_name,
            "classification": "identified",
            "reason": (
                f"Stable identity matches member {member_name} in the source NAS runtime export."
            ),
            "evidence_sha256": evidence.document_sha256,
        }
    return classifications, {
        "id": evidence.id,
        "source": evidence.source_type,
        "document_sha256": evidence.document_sha256,
        "captured_at": evidence.evidence_json.get("captured_at"),
        "platform": evidence.evidence_json.get("platform"),
        "platform_name": NAS_PLATFORM_NAMES[evidence.evidence_json["platform"]],
        "platform_marker": evidence.evidence_json.get("platform_marker"),
        "product_version": evidence.evidence_json.get("product_version"),
        "member_count": len(assignments) if isinstance(assignments, list) else 0,
        "matched_member_count": len(matched_members),
        "unmatched_members": sorted(
            str(item.get("member"))
            for item in assignments
            if isinstance(item, dict) and str(item.get("member")) not in matched_members
        )
        if isinstance(assignments, list)
        else [],
        "ambiguous_members": sorted(ambiguous_members),
    }


def _signature_documents(disk: dict[str, Any]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    sources: list[tuple[Any, str | None, int | None]] = [
        (disk.get("signatures"), _text(disk.get("kernel_path"), limit=4096), None)
    ]
    partitions = disk.get("partitions")
    if isinstance(partitions, list):
        for partition in partitions[:256]:
            if not isinstance(partition, dict):
                continue
            partition_path = _text(partition.get("kernel_path"), limit=4096)
            partition_number = (
                partition.get("number") if isinstance(partition.get("number"), int) else None
            )
            sources.append((partition.get("signatures"), partition_path, partition_number))
            filesystem = partition.get("filesystem")
            if isinstance(filesystem, dict):
                sources.append(([filesystem], partition_path, partition_number))
    for source, kernel_path, partition_number in sources:
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
                "kernel_path": kernel_path,
                "partition_number": partition_number,
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
    connection = disk.get("connection") if isinstance(disk.get("connection"), dict) else {}
    transport = _text(connection.get("transport"), limit=64)
    protocol = _text(connection.get("protocol"), limit=64)
    external = disk.get("removable") is True or transport in {"usb", "mmc", "sd", "firewire"}
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
        "connection": {"transport": transport, "protocol": protocol},
        "external": external,
        "mounted": bool(mountpoints),
        "mountpoints": sorted(mountpoints)[:256],
        "signature_scan": {
            "status": _text(scan.get("status"), limit=32) if isinstance(scan, dict) else None,
            "source": _text(scan.get("source"), limit=64) if isinstance(scan, dict) else None,
            "reason": _text(scan.get("reason"), limit=1024) if isinstance(scan, dict) else None,
        },
        "confidence": _confidence(disk, signatures),
        "signatures": signatures,
        "reviewed_device": reviewed_device(disk),
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

    inspection_available = False
    stack_preview_available = False
    if profile == "standalone_filesystem" and not blockers and filesystems:
        filesystem_signatures = [
            signature
            for member in members
            for signature in member["signatures"]
            if str(signature["type"]).casefold() in SUPPORTED_FILESYSTEMS
        ]
        signature = filesystem_signatures[0] if len(filesystem_signatures) == 1 else {}
        is_whole_device = signature.get("kernel_path") == members[0].get("kernel_path")
        is_numbered_partition = isinstance(signature.get("partition_number"), int)
        if len(members) == 1 and (is_whole_device or is_numbered_partition):
            inspection_available = True
        else:
            blockers.append("Automatic inspection requires one unambiguous filesystem source path.")
    elif profile in {"linux_md", "lvm", "zfs"} and not blockers:
        stack_preview_available = True
    safety_blocked = bool(blockers)
    state = (
        "ready"
        if inspection_available or stack_preview_available
        else ("blocked" if safety_blocked else "degraded-review")
    )
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
        "archive_intake": {
            "state": "discovered_external" if any(item["external"] for item in members) else None,
            "default_access": "read_only",
            "reason": (
                "The connection is reported as removable or external. Hoardarr will treat it "
                "as archive intake and keep the source read-only."
                if any(item["external"] for item in members)
                else "This source is not reported as removable or externally attached."
            ),
        },
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
        "health": {
            "quality": "not_reported",
            "state": None,
            "reason": (
                "Foreign filesystem signatures do not prove current drive or pool health. "
                "Review physical-drive health and provider metadata separately."
            ),
        },
        "warnings": warnings,
        "blockers": blockers,
        "modes": [
            {
                "id": "inspect_read_only",
                "available": inspection_available,
                "reason": (
                    "A bounded read-only inventory can be reviewed and queued."
                    if inspection_available
                    else blockers[0]
                    if blockers
                    else "Review provider metadata before any stack activation is considered."
                ),
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
                "id": "preview_stack",
                "available": stack_preview_available,
                "reason": blockers[0]
                if blockers
                else (
                    "Read provider metadata without assembling, activating, or importing the stack."
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


def build_stack_preview_plan(
    session: Session,
    *,
    snapshot: HardwareSnapshot,
    candidate_id: str,
) -> dict[str, Any]:
    assessment = assess_foreign_storage(session, snapshot=snapshot)
    matches = [item for item in assessment["candidates"] if item["id"] == candidate_id]
    if len(matches) != 1:
        raise ForeignStorageError(
            "foreign_candidate_not_found", "Run discovery and select the source again."
        )
    candidate = matches[0]
    if candidate["profile"] not in {"linux_md", "lvm", "zfs"}:
        raise ForeignStorageError(
            "foreign_stack_preview_unsupported", "This source is not a supported storage stack."
        )
    mode = next(item for item in candidate["modes"] if item["id"] == "preview_stack")
    if mode["available"] is not True:
        raise ForeignStorageError("foreign_stack_preview_blocked", str(mode["reason"]))
    expected_signature = next(
        signature
        for signature, details in STACK_SIGNATURES.items()
        if details[0] == candidate["profile"]
    )
    members: list[dict[str, Any]] = []
    for member in candidate["members"]:
        signatures = [
            item
            for item in member["signatures"]
            if str(item["type"]).casefold() == expected_signature
        ]
        if len(signatures) != 1:
            raise ForeignStorageError(
                "foreign_stack_source_ambiguous", "A stack member source is not unambiguous."
            )
        signature = signatures[0]
        device = member["reviewed_device"]
        members.append(
            {
                "device": device,
                "device_binding_sha256": document_hash(device),
                "source": {
                    "kind": (
                        "whole_device"
                        if signature["kernel_path"] == member["kernel_path"]
                        else "partition"
                    ),
                    "kernel_path_at_preview": signature["kernel_path"],
                    "partition_number": signature["partition_number"],
                    "signature_type": expected_signature,
                    "signature_uuid": signature["uuid"],
                },
            }
        )
    plan = {
        "schema_version": 1,
        "operation": "foreign.preview_stack",
        "candidate_id": candidate_id,
        "profile": candidate["profile"],
        "hardware_snapshot_id": snapshot.id,
        "hardware_snapshot_sha256": snapshot.sha256,
        "members": members,
        "activation_allowed": False,
        "mutation_performed": False,
    }
    return {**plan, "plan_sha256": document_hash(plan)}


def validate_stack_preview_plan(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "operation",
        "candidate_id",
        "profile",
        "hardware_snapshot_id",
        "hardware_snapshot_sha256",
        "members",
        "activation_allowed",
        "mutation_performed",
        "plan_sha256",
    }:
        raise ForeignStorageError("foreign_stack_plan_invalid", "The stack preview is invalid.")
    plan = dict(value)
    expected_hash = plan.pop("plan_sha256", None)
    profile = plan.get("profile")
    members = plan.get("members")
    expected_signature = next(
        (key for key, details in STACK_SIGNATURES.items() if details[0] == profile), None
    )
    valid = (
        plan.get("schema_version") == 1
        and plan.get("operation") == "foreign.preview_stack"
        and isinstance(plan.get("candidate_id"), str)
        and len(plan["candidate_id"]) == 32
        and plan["candidate_id"].startswith("foreign:")
        and all(character in "0123456789abcdef" for character in plan["candidate_id"][8:])
        and profile in {"linux_md", "lvm", "zfs"}
        and isinstance(plan.get("hardware_snapshot_id"), str)
        and isinstance(plan.get("hardware_snapshot_sha256"), str)
        and len(plan["hardware_snapshot_sha256"]) == 64
        and all(character in "0123456789abcdef" for character in plan["hardware_snapshot_sha256"])
        and isinstance(members, list)
        and 1 <= len(members) <= 256
        and plan.get("activation_allowed") is False
        and plan.get("mutation_performed") is False
        and expected_hash == document_hash(plan)
    )
    if not valid:
        raise ForeignStorageError("foreign_stack_plan_invalid", "The stack preview is invalid.")
    for member in members:
        if not isinstance(member, dict) or set(member) != {
            "device",
            "device_binding_sha256",
            "source",
        }:
            raise ForeignStorageError("foreign_stack_plan_invalid", "A stack member is invalid.")
        device = member.get("device")
        source = member.get("source")
        if (
            not isinstance(device, dict)
            or set(device) != set(IDENTITY_FIELDS)
            or device.get("stable_identity") is not True
            or document_hash(device) != member.get("device_binding_sha256")
            or not isinstance(source, dict)
            or set(source)
            != {
                "kind",
                "kernel_path_at_preview",
                "partition_number",
                "signature_type",
                "signature_uuid",
            }
            or source.get("kind") not in {"whole_device", "partition"}
            or not isinstance(source.get("kernel_path_at_preview"), str)
            or not source["kernel_path_at_preview"].startswith("/dev/")
            or "\0" in source["kernel_path_at_preview"]
            or source.get("signature_type") != expected_signature
            or not isinstance(source.get("signature_uuid"), str)
            or not source["signature_uuid"]
            or (source.get("kind") == "whole_device" and source.get("partition_number") is not None)
            or (
                source.get("kind") == "partition"
                and (
                    not isinstance(source.get("partition_number"), int)
                    or isinstance(source.get("partition_number"), bool)
                    or not 1 <= source["partition_number"] <= 4096
                )
            )
        ):
            raise ForeignStorageError("foreign_stack_plan_invalid", "A stack member is invalid.")
    if len({member["device"]["id"] for member in members}) != len(members):
        raise ForeignStorageError("foreign_stack_plan_invalid", "Stack members are duplicated.")
    return value


def build_inspection_plan(
    session: Session,
    *,
    snapshot: HardwareSnapshot,
    candidate_id: str,
) -> dict[str, Any]:
    assessment = assess_foreign_storage(session, snapshot=snapshot)
    matches = [item for item in assessment["candidates"] if item["id"] == candidate_id]
    if len(matches) != 1:
        raise ForeignStorageError(
            "foreign_candidate_not_found", "Run discovery and select the source again."
        )
    candidate = matches[0]
    mode = next(item for item in candidate["modes"] if item["id"] == "inspect_read_only")
    if mode["available"] is not True:
        raise ForeignStorageError("foreign_inspection_blocked", str(mode["reason"]))
    member = candidate["members"][0]
    signatures = [
        item
        for item in member["signatures"]
        if str(item["type"]).casefold() in SUPPORTED_FILESYSTEMS
    ]
    if len(signatures) != 1:
        raise ForeignStorageError(
            "foreign_source_ambiguous", "The filesystem source is not unambiguous."
        )
    signature = signatures[0]
    filesystem_type = str(signature["type"]).casefold()
    device = member["reviewed_device"]
    source = {
        "kind": (
            "whole_device" if signature["kernel_path"] == member["kernel_path"] else "partition"
        ),
        "kernel_path_at_preview": signature["kernel_path"],
        "partition_number": signature["partition_number"],
        "filesystem_type": filesystem_type,
        "filesystem_uuid": signature["uuid"],
        "filesystem_label": signature["label"],
        "signature_source": signature["source"],
        "read_only_options": list(SUPPORTED_FILESYSTEMS[filesystem_type]["read_only_options"]),
    }
    plan = {
        "schema_version": 1,
        "operation": "foreign.inspect_read_only",
        "candidate_id": candidate_id,
        "hardware_snapshot_id": snapshot.id,
        "hardware_snapshot_sha256": snapshot.sha256,
        "device": device,
        "device_binding_sha256": document_hash(device),
        "source": source,
        "limits": {
            "maximum_entries": 100_000,
            "maximum_extension_groups": 256,
            "maximum_errors": 100,
        },
        "access": "read_only",
        "persistent_mount": False,
        "automatic_activation": False,
        "mutation_performed": False,
    }
    return {**plan, "plan_sha256": document_hash(plan)}


def validate_inspection_plan(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "operation",
        "candidate_id",
        "hardware_snapshot_id",
        "hardware_snapshot_sha256",
        "device",
        "device_binding_sha256",
        "source",
        "limits",
        "access",
        "persistent_mount",
        "automatic_activation",
        "mutation_performed",
        "plan_sha256",
    }:
        raise ForeignStorageError("foreign_plan_invalid", "The inspection plan is invalid.")
    plan = dict(value)
    expected_hash = plan.pop("plan_sha256", None)
    device = plan.get("device")
    source = plan.get("source")
    limits = plan.get("limits")
    candidate_id = plan.get("candidate_id")
    snapshot_sha256 = plan.get("hardware_snapshot_sha256")
    source_fields = {
        "kind",
        "kernel_path_at_preview",
        "partition_number",
        "filesystem_type",
        "filesystem_uuid",
        "filesystem_label",
        "signature_source",
        "read_only_options",
    }
    source_path = source.get("kernel_path_at_preview") if isinstance(source, dict) else None
    partition_number = source.get("partition_number") if isinstance(source, dict) else None
    if (
        plan.get("schema_version") != 1
        or plan.get("operation") != "foreign.inspect_read_only"
        or plan.get("access") != "read_only"
        or plan.get("persistent_mount") is not False
        or plan.get("automatic_activation") is not False
        or plan.get("mutation_performed") is not False
        or not isinstance(candidate_id, str)
        or not candidate_id.startswith("foreign:")
        or len(candidate_id) != 32
        or any(character not in "0123456789abcdef" for character in candidate_id[8:])
        or not isinstance(plan.get("hardware_snapshot_id"), str)
        or not isinstance(snapshot_sha256, str)
        or len(snapshot_sha256) != 64
        or any(character not in "0123456789abcdef" for character in snapshot_sha256)
        or not isinstance(device, dict)
        or set(device) != set(IDENTITY_FIELDS)
        or not isinstance(device.get("id"), str)
        or device.get("stable_identity") is not True
        or document_hash(device) != plan.get("device_binding_sha256")
        or not isinstance(source, dict)
        or set(source) != source_fields
        or not isinstance(source_path, str)
        or not source_path.startswith("/dev/")
        or "\0" in source_path
        or source.get("kind") not in {"whole_device", "partition"}
        or (
            source.get("kind") == "partition"
            and (
                not isinstance(partition_number, int)
                or isinstance(partition_number, bool)
                or not 1 <= partition_number <= 4096
            )
        )
        or (source.get("kind") == "whole_device" and partition_number is not None)
        or str(source.get("filesystem_type")).casefold() not in SUPPORTED_FILESYSTEMS
        or source.get("read_only_options")
        != SUPPORTED_FILESYSTEMS[str(source.get("filesystem_type")).casefold()]["read_only_options"]
        or not isinstance(limits, dict)
        or limits
        != {"maximum_entries": 100_000, "maximum_extension_groups": 256, "maximum_errors": 100}
        or expected_hash != document_hash(plan)
    ):
        raise ForeignStorageError("foreign_plan_invalid", "The inspection plan is invalid.")
    return value


def _managed_backend_path(session: Session, backend: StorageBackend) -> str:
    value = backend.namespace_path
    if not value and backend.storage_entity_id:
        entity = session.get(StorageEntity, backend.storage_entity_id)
        value = entity.mountpoint if entity is not None else None
    if not isinstance(value, str):
        raise ForeignStorageError(
            "foreign_destination_path_missing",
            "The selected managed destination does not have a storage path.",
        )
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts or "\0" in value or len(value) > 4096:
        raise ForeignStorageError(
            "foreign_destination_path_invalid", "The managed destination path is invalid."
        )
    return value


def migration_destinations(session: Session) -> list[dict[str, Any]]:
    """Return only real, writable managed backends that can receive imported files."""

    documents: list[dict[str, Any]] = []
    for backend in session.scalars(select(StorageBackend).order_by(StorageBackend.created_at)):
        if backend.lifecycle_state not in {"active", "preferred_write"}:
            continue
        try:
            path = _managed_backend_path(session, backend)
            details = Path(path).stat()
            usage = shutil.disk_usage(path)
        except (ForeignStorageError, OSError):
            continue
        if not Path(path).is_dir():
            continue
        group = session.get(StorageGroup, backend.storage_group_id)
        documents.append(
            {
                "id": backend.id,
                "storage_group_id": backend.storage_group_id,
                "name": group.name if group is not None else "Managed storage",
                "path": path,
                "stable_identity": backend.stable_identity,
                "lifecycle_state": backend.lifecycle_state,
                "device_number": details.st_dev,
                "free_bytes": usage.free,
            }
        )
    return documents


def build_migration_plan(
    session: Session,
    *,
    snapshot: HardwareSnapshot,
    candidate_id: str,
    destination_backend_id: str,
    verification_mode: str,
    collision_policy: str,
    reserve_bytes: int,
    selection: object | None = None,
) -> dict[str, Any]:
    """Bind a current foreign inventory to one managed destination without adopting the source."""

    if verification_mode not in {"fast", "accurate"}:
        raise ForeignStorageError(
            "foreign_verification_invalid", "Choose fast or accurate copy verification."
        )
    if collision_policy not in {"stop", "reuse_identical"}:
        raise ForeignStorageError(
            "foreign_collision_policy_invalid", "Choose how existing destination files are handled."
        )
    if reserve_bytes < 0 or reserve_bytes > 10**15:
        raise ForeignStorageError(
            "foreign_reserve_invalid", "The destination reserve is outside safe bounds."
        )
    normalized_selection = normalize_archive_selection(selection)
    assessment = assess_foreign_storage(session, snapshot=snapshot)
    candidate = next(
        (item for item in assessment["candidates"] if item["id"] == candidate_id), None
    )
    if candidate is None:
        raise ForeignStorageError(
            "foreign_candidate_missing", "The reviewed source is unavailable."
        )
    role = candidate.get("unraid")
    if isinstance(role, dict) and role.get("role") == "parity":
        raise ForeignStorageError(
            "foreign_parity_not_importable",
            "Parity is never copied as file content and parity reuse is not supported.",
        )
    report = candidate.get("latest_inventory")
    if not isinstance(report, dict) or report.get("current_snapshot_match") is not True:
        raise ForeignStorageError(
            "foreign_inventory_required", "Run a current read-only inventory before migration."
        )
    inventory = report.get("inventory")
    if not isinstance(inventory, dict) or inventory.get("truncated") is True:
        raise ForeignStorageError(
            "foreign_inventory_incomplete", "The bounded source inventory must be complete."
        )
    if inventory.get("read_errors"):
        raise ForeignStorageError(
            "foreign_inventory_has_errors",
            "Resolve or explicitly isolate source read errors before migration.",
        )
    destination = session.get(StorageBackend, destination_backend_id)
    if destination is None or destination.lifecycle_state not in {"active", "preferred_write"}:
        raise ForeignStorageError(
            "foreign_destination_unavailable", "Choose an active managed storage destination."
        )
    destination_path = _managed_backend_path(session, destination)
    destination_group = session.get(StorageGroup, destination.storage_group_id)
    try:
        destination_details = Path(destination_path).stat()
        destination_usage = shutil.disk_usage(destination_path)
    except OSError as exc:
        raise ForeignStorageError(
            "foreign_destination_unavailable", "The managed destination is unavailable."
        ) from exc
    total_bytes = inventory.get("total_bytes")
    file_count = inventory.get("file_count")
    if (
        not isinstance(total_bytes, int)
        or isinstance(total_bytes, bool)
        or total_bytes < 0
        or not isinstance(file_count, int)
        or isinstance(file_count, bool)
        or file_count < 0
    ):
        raise ForeignStorageError(
            "foreign_inventory_invalid", "The persisted source inventory is invalid."
        )
    free_bytes = destination_usage.free
    required_at_review = (
        total_bytes + reserve_bytes if normalized_selection["mode"] == "full" else reserve_bytes
    )
    if free_bytes < required_at_review:
        raise ForeignStorageError(
            "foreign_destination_full",
            "The destination does not have enough free space plus the requested reserve.",
        )
    source_plan = build_inspection_plan(session, snapshot=snapshot, candidate_id=candidate_id)
    plan = {
        "schema_version": 2,
        "operation": "foreign.migrate_files",
        "candidate_id": candidate_id,
        "hardware_snapshot_id": snapshot.id,
        "hardware_snapshot_sha256": snapshot.sha256,
        "source_inventory_operation_id": report["operation_id"],
        "source_inventory_sha256": document_hash(inventory),
        "device": source_plan["device"],
        "device_binding_sha256": source_plan["device_binding_sha256"],
        "source": source_plan["source"],
        "destination": {
            "backend_id": destination.id,
            "storage_group_id": destination.storage_group_id,
            "name": destination_group.name if destination_group is not None else "Managed storage",
            "path": destination_path,
            "stable_identity": destination.stable_identity,
            "device_number": destination_details.st_dev,
            "free_bytes_at_preview": free_bytes,
            "reserve_bytes": reserve_bytes,
        },
        "inventory": {"file_count": file_count, "total_bytes": total_bytes},
        "selection": {
            **normalized_selection,
            "capacity_upper_bound_bytes": total_bytes,
            "exact_selected_bytes_at_review": (
                total_bytes if normalized_selection["mode"] == "full" else None
            ),
        },
        "verification": {
            "mode": verification_mode,
            "algorithm": "blake3" if verification_mode == "accurate" else "size_mtime",
        },
        "collision_policy": collision_policy,
        "source_access": "read_only",
        "source_retained": True,
        "parity_reuse_supported": False,
    }
    return {**plan, "plan_sha256": document_hash(plan)}


def validate_migration_plan(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ForeignStorageError(
            "foreign_migration_plan_invalid", "The migration plan is invalid."
        )
    plan = dict(value)
    expected = plan.pop("plan_sha256", None)
    destination = plan.get("destination")
    inventory = plan.get("inventory")
    verification = plan.get("verification")
    source = plan.get("source")
    source_fields = {
        "kind",
        "kernel_path_at_preview",
        "partition_number",
        "filesystem_type",
        "filesystem_uuid",
        "filesystem_label",
        "signature_source",
        "read_only_options",
    }
    source_path = source.get("kernel_path_at_preview") if isinstance(source, dict) else None
    source_type = str(source.get("filesystem_type")).casefold() if isinstance(source, dict) else ""
    schema_version = plan.get("schema_version")
    expected_fields = {
        "schema_version",
        "operation",
        "candidate_id",
        "hardware_snapshot_id",
        "hardware_snapshot_sha256",
        "source_inventory_operation_id",
        "source_inventory_sha256",
        "device",
        "device_binding_sha256",
        "source",
        "destination",
        "inventory",
        "verification",
        "collision_policy",
        "source_access",
        "source_retained",
        "parity_reuse_supported",
        "plan_sha256",
    }
    if schema_version == 2:
        expected_fields.add("selection")
    raw_selection = plan.get("selection")
    selection_input = (
        {
            key: value
            for key, value in raw_selection.items()
            if key not in {"capacity_upper_bound_bytes", "exact_selected_bytes_at_review"}
        }
        if isinstance(raw_selection, dict)
        else raw_selection
    )
    try:
        selection = (
            normalize_archive_selection(selection_input)
            if schema_version == 2
            else normalize_archive_selection(None)
        )
    except ForeignStorageError:
        selection = None
    selection_valid = schema_version == 1 or (
        isinstance(raw_selection, dict)
        and selection is not None
        and raw_selection
        == {
            **selection,
            "capacity_upper_bound_bytes": inventory.get("total_bytes")
            if isinstance(inventory, dict)
            else None,
            "exact_selected_bytes_at_review": (
                inventory.get("total_bytes")
                if isinstance(inventory, dict) and selection["mode"] == "full"
                else None
            ),
        }
    )
    if (
        set(value) != expected_fields
        or schema_version not in {1, 2}
        or not selection_valid
        or plan.get("operation") != "foreign.migrate_files"
        or plan.get("source_access") != "read_only"
        or plan.get("source_retained") is not True
        or plan.get("parity_reuse_supported") is not False
        or not isinstance(plan.get("candidate_id"), str)
        or not plan["candidate_id"].startswith("foreign:")
        or len(plan["candidate_id"]) != 32
        or any(character not in "0123456789abcdef" for character in plan["candidate_id"][8:])
        or not isinstance(plan.get("hardware_snapshot_id"), str)
        or not isinstance(plan.get("hardware_snapshot_sha256"), str)
        or len(plan["hardware_snapshot_sha256"]) != 64
        or not isinstance(plan.get("source_inventory_operation_id"), str)
        or not isinstance(plan.get("source_inventory_sha256"), str)
        or len(plan["source_inventory_sha256"]) != 64
        or not isinstance(plan.get("device"), dict)
        or set(plan["device"]) != set(IDENTITY_FIELDS)
        or plan["device"].get("stable_identity") is not True
        or document_hash(plan["device"]) != plan.get("device_binding_sha256")
        or not isinstance(source, dict)
        or set(source) != source_fields
        or not isinstance(source_path, str)
        or not source_path.startswith("/dev/")
        or "\0" in source_path
        or source.get("kind") not in {"whole_device", "partition"}
        or source_type not in SUPPORTED_FILESYSTEMS
        or source.get("read_only_options")
        != SUPPORTED_FILESYSTEMS[source_type]["read_only_options"]
        or not isinstance(destination, dict)
        or set(destination)
        != {
            "backend_id",
            "storage_group_id",
            "name",
            "path",
            "stable_identity",
            "device_number",
            "free_bytes_at_preview",
            "reserve_bytes",
        }
        or not isinstance(destination.get("path"), str)
        or not PurePosixPath(destination["path"]).is_absolute()
        or ".." in PurePosixPath(destination["path"]).parts
        or "\0" in destination["path"]
        or not isinstance(destination.get("backend_id"), str)
        or not isinstance(destination.get("storage_group_id"), str)
        or not isinstance(destination.get("stable_identity"), str)
        or not isinstance(destination.get("device_number"), int)
        or isinstance(destination.get("device_number"), bool)
        or not isinstance(destination.get("free_bytes_at_preview"), int)
        or isinstance(destination.get("free_bytes_at_preview"), bool)
        or destination["free_bytes_at_preview"] < 0
        or not isinstance(destination.get("reserve_bytes"), int)
        or isinstance(destination.get("reserve_bytes"), bool)
        or not 0 <= destination["reserve_bytes"] <= 10**15
        or not isinstance(inventory, dict)
        or set(inventory) != {"file_count", "total_bytes"}
        or not all(
            isinstance(inventory.get(key), int)
            and not isinstance(inventory.get(key), bool)
            and inventory[key] >= 0
            for key in inventory
        )
        or verification
        not in (
            {"mode": "fast", "algorithm": "size_mtime"},
            {"mode": "accurate", "algorithm": "blake3"},
        )
        or plan.get("collision_policy") not in {"stop", "reuse_identical"}
        or expected != document_hash(plan)
    ):
        raise ForeignStorageError(
            "foreign_migration_plan_invalid", "The migration plan is invalid."
        )
    return value


def _apply_candidate_nas_origin(
    candidate: dict[str, Any], candidate_members: list[dict[str, Any]]
) -> None:
    matched = [item["nas_origin"] for item in candidate_members if item.get("nas_origin")]
    identified_unraid = isinstance(candidate.get("unraid"), dict) and candidate["unraid"].get(
        "classification"
    ) == "identified"
    if not matched:
        candidate["nas_origin"] = None
        return
    if len(matched) != len(candidate_members) or len({item["platform"] for item in matched}) != 1:
        candidate["nas_origin"] = None
        candidate["warnings"].append(
            "The source NAS export matches only part of this storage candidate. Vendor origin "
            "remains Not reported until every member has one unambiguous stable-identity match."
        )
        return
    platform_name = matched[0]["platform_name"]
    candidate["nas_origin"] = {
        "platform": matched[0]["platform"],
        "platform_name": platform_name,
        "classification": "identified",
        "members": [item["member"] for item in matched],
        "evidence_sha256": matched[0]["evidence_sha256"],
        "reason": (
            f"Every member matches the {platform_name} runtime export by stable identity."
        ),
    }
    if identified_unraid:
        candidate["origin"] = {
            "name": "Not reported",
            "confidence": "unknown",
            "reason": "Loaded source manifests make conflicting origin claims for this disk.",
        }
        candidate["warnings"].append(
            "Unraid and NAS runtime evidence both match this disk. Hoardarr will not choose an "
            "origin until the incorrect source manifest is removed."
        )
        return
    candidate["origin"] = {
        "name": platform_name,
        "confidence": "high",
        "reason": candidate["nas_origin"]["reason"],
    }
    if candidate["profile"] == "standalone_filesystem":
        candidate["profile_name"] = f"Identified {platform_name} data filesystem"
    elif candidate["profile"] in {"linux_md", "lvm", "zfs"}:
        candidate["profile_name"] = f"Identified {platform_name} {candidate['profile_name']} stack"


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
    unraid_classifications, unraid_evidence = _unraid_classification(session, members)
    nas_classifications, nas_evidence = _nas_classification(session, members)
    for member in members:
        member["unraid"] = unraid_classifications.get(member["device_id"])
        member["nas_origin"] = nas_classifications.get(member["device_id"])

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
        elif not signatures and not member["system_device"]:
            grouped[("unraid_unknown", str(member["device_id"]))].append(member)

    candidates: list[dict[str, Any]] = []
    for (profile, _identity), candidate_members in sorted(grouped.items()):
        if profile in {"standalone_filesystem", "unraid_unknown"}:
            candidate = _candidate_document(
                profile=profile,
                name=(
                    "Standalone filesystem"
                    if profile == "standalone_filesystem"
                    else "Unrecognized foreign disk"
                ),
                members=candidate_members,
                managed_identities=managed_identities,
                tool=None,
            )
            classification = candidate_members[0].get("unraid")
            candidate["unraid"] = classification
            if (
                isinstance(classification, dict)
                and classification["classification"] == "identified"
            ):
                candidate["origin"] = {
                    "name": "Unraid",
                    "confidence": "high",
                    "reason": classification["reason"],
                }
                if classification["role"] == "parity":
                    candidate["profile_name"] = "Identified Unraid parity disk"
                    candidate["warnings"].append(
                        "The original parity assignment is identified, but parity reuse is not "
                        "supported or claimed by this read-only import workflow."
                    )
                else:
                    candidate["profile_name"] = "Identified Unraid data disk"
            elif (
                isinstance(classification, dict) and classification["classification"] == "suspected"
            ):
                candidate["profile_name"] = (
                    "Suspected Unraid parity disk"
                    if classification["role"] == "parity"
                    else "Possible Unraid data disk"
                )
                candidate["warnings"].append(classification["reason"])
            _apply_candidate_nas_origin(candidate, candidate_members)
            candidates.append(candidate)
            continue
        signature_type = next(
            item for item, details in STACK_SIGNATURES.items() if details[0] == profile
        )
        _stack, name, tool = STACK_SIGNATURES[signature_type]
        candidate = _candidate_document(
            profile=profile,
            name=name,
            members=candidate_members,
            managed_identities=managed_identities,
            tool=tool,
        )
        _apply_candidate_nas_origin(candidate, candidate_members)
        candidates.append(candidate)

    inspection_reports = _latest_inspection_reports(
        session, [str(candidate["id"]) for candidate in candidates]
    )
    for candidate in candidates:
        report = inspection_reports.get(str(candidate["id"]))
        if report is not None:
            candidate["latest_inventory"] = {
                **report,
                "current_snapshot_match": (report["hardware_snapshot_sha256"] == snapshot.sha256),
            }
        else:
            candidate["latest_inventory"] = None

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
        "unraid_evidence": unraid_evidence,
        "nas_evidence": nas_evidence,
        "migration_destinations": migration_destinations(session),
        "candidates": candidates,
        "unrecognized_device_count": sum(
            1 for member in members if not member["signatures"] and not member["system_device"]
        ),
    }
