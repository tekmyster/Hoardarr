from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from hoardarr.db.models import (
    MetricAlert,
    MetricEntity,
    Operation,
    PhysicalDisk,
    PhysicalDiskIdentityAlias,
    StorageBackend,
    StorageDrainJob,
    StorageEntity,
    StorageGroup,
    StoragePath,
    StorageVolume,
)

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_MAPPINGS = 256
SHA256_RE = re.compile(r"[0-9a-f]{64}")
ACTIVE_OPERATION_STATES = ("queued", "running", "paused")


class IdentityMigrationError(RuntimeError):
    def __init__(self, code: str, safe_message: str, *, exit_code: int = 3) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.exit_code = exit_code


def _bounded_text(value: str, *, maximum: int = 512) -> str:
    if not value or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise ValueError("value is empty, too long, or contains control characters")
    return value


class DeviceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    capacity_bytes: int = Field(gt=0, le=2**63 - 1)
    logical_sector_bytes: int = Field(gt=0, le=1024 * 1024)
    physical_sector_bytes: int = Field(gt=0, le=1024 * 1024)
    content_sha256: str
    filesystem_uuid: str | None = Field(default=None, max_length=128)
    zfs_pool_guid: str | None = Field(default=None, max_length=128)
    zfs_dataset_guid: str | None = Field(default=None, max_length=128)
    md_array_uuid: str | None = Field(default=None, max_length=128)
    md_filesystem_uuid: str | None = Field(default=None, max_length=128)
    md_member_count: int | None = Field(default=None, gt=0, le=1024)
    kernel_path: str | None = Field(default=None, max_length=4096)
    serial: str | None = Field(default=None, max_length=256)
    wwn: str | None = Field(default=None, max_length=256)

    @field_validator("content_sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if not SHA256_RE.fullmatch(value):
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
        return value

    @field_validator(
        "filesystem_uuid",
        "zfs_pool_guid",
        "zfs_dataset_guid",
        "md_array_uuid",
        "md_filesystem_uuid",
        "kernel_path",
        "serial",
        "wwn",
    )
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return None if value is None else _bounded_text(value, maximum=4096)

    @model_validator(mode="after")
    def validate_geometry(self) -> DeviceEvidence:
        if self.physical_sector_bytes < self.logical_sector_bytes:
            raise ValueError("physical sector size cannot be smaller than logical sector size")
        if self.physical_sector_bytes % self.logical_sector_bytes:
            raise ValueError("physical sector size must be a multiple of logical sector size")
        return self


class IdentityMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    old_identity: str
    new_identity: str
    evidence_type: Literal["ext4", "zfs", "linux_md"]
    source: DeviceEvidence
    target: DeviceEvidence

    @field_validator("old_identity", "new_identity")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _bounded_text(value)

    @model_validator(mode="after")
    def validate_pair(self) -> IdentityMapping:
        if self.old_identity == self.new_identity:
            raise ValueError("old and new identities must differ")
        for name in (
            "capacity_bytes",
            "logical_sector_bytes",
            "physical_sector_bytes",
            "content_sha256",
        ):
            if getattr(self.source, name) != getattr(self.target, name):
                raise ValueError(f"source and target {name} must match exactly")
        if (self.source.kernel_path is None) != (self.target.kernel_path is None):
            raise ValueError("kernel path observations must be supplied as a complete pair")
        if self.evidence_type == "ext4":
            if not self.source.filesystem_uuid or not self.target.filesystem_uuid:
                raise ValueError("ext4 mappings require both filesystem UUID observations")
            if self.source.filesystem_uuid != self.target.filesystem_uuid:
                raise ValueError("ext4 filesystem UUID observations do not match")
        elif self.evidence_type == "zfs":
            if not self.source.zfs_pool_guid or not self.target.zfs_pool_guid:
                raise ValueError("ZFS mappings require both pool GUID observations")
            if self.source.zfs_pool_guid != self.target.zfs_pool_guid:
                raise ValueError("ZFS pool GUID observations do not match")
            if self.source.zfs_dataset_guid != self.target.zfs_dataset_guid:
                raise ValueError("ZFS dataset GUID observations do not match")
        else:
            required = ("md_array_uuid", "md_filesystem_uuid", "md_member_count")
            if any(getattr(self.source, name) is None for name in required) or any(
                getattr(self.target, name) is None for name in required
            ):
                raise ValueError(
                    "Linux MD mappings require array/filesystem UUIDs and member count"
                )
            if any(getattr(self.source, name) != getattr(self.target, name) for name in required):
                raise ValueError("Linux MD array evidence does not match")
        return self


class IdentityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    mappings: list[IdentityMapping] = Field(min_length=1, max_length=MAX_MAPPINGS)

    @model_validator(mode="after")
    def validate_one_to_one(self) -> IdentityManifest:
        old = [item.old_identity for item in self.mappings]
        new = [item.new_identity for item in self.mappings]
        if len(old) != len(set(old)):
            raise ValueError("duplicate old identities are not allowed")
        if len(new) != len(set(new)):
            raise ValueError("duplicate new identities are not allowed")
        if set(old) & set(new):
            raise ValueError("mapping chains and cycles are not allowed")
        md_groups: dict[str, list[IdentityMapping]] = {}
        for item in self.mappings:
            if item.evidence_type == "linux_md":
                assert item.source.md_array_uuid is not None
                md_groups.setdefault(item.source.md_array_uuid, []).append(item)
        for mappings in md_groups.values():
            required = mappings[0].source.md_member_count
            if required != len(mappings):
                raise ValueError("every Linux MD member must be present in the manifest")
        return self


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate JSON object key")
        document[key] = value
    return document


def load_identity_manifest(path: Path) -> tuple[IdentityManifest, str]:
    supplied = path.expanduser()
    if not supplied.is_absolute() or ".." in supplied.parts:
        raise IdentityMigrationError(
            "manifest_path_unsafe", "Manifest path must be absolute and cannot contain traversal."
        )
    if supplied.suffix.casefold() != ".json":
        raise IdentityMigrationError(
            "manifest_type_unsupported", "Identity manifest must be a JSON file."
        )
    try:
        if supplied.is_symlink() or any(parent.is_symlink() for parent in supplied.parents):
            raise IdentityMigrationError(
                "manifest_path_unsafe", "Identity manifest path cannot contain symbolic links."
            )
        if not supplied.is_file():
            raise IdentityMigrationError(
                "manifest_path_unsafe", "Identity manifest must be a regular non-symlink file."
            )
        size = supplied.stat().st_size
        if size <= 0 or size > MAX_MANIFEST_BYTES:
            raise IdentityMigrationError(
                "manifest_size_invalid", "Identity manifest size is outside the supported bounds."
            )
        payload = supplied.read_bytes()
        if len(payload) != size:
            raise IdentityMigrationError(
                "manifest_state_drift", "Identity manifest changed while it was being read."
            )
    except IdentityMigrationError:
        raise
    except OSError as exc:
        raise IdentityMigrationError(
            "manifest_unavailable", "Identity manifest could not be read."
        ) from exc
    digest = hashlib.sha256(payload).hexdigest()
    try:
        raw = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys)
        manifest = IdentityManifest.model_validate(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, ValidationError) as exc:
        raise IdentityMigrationError(
            "manifest_invalid", "Identity manifest failed strict schema validation."
        ) from exc
    return manifest, digest


def database_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise IdentityMigrationError(
            "database_unavailable", "The SQLite database could not be read.", exit_code=4
        ) from exc
    return digest.hexdigest()


def _require_checkpointed_database(path: Path) -> None:
    wal = Path(f"{path}-wal")
    try:
        if wal.exists() and wal.stat().st_size > 0:
            raise IdentityMigrationError(
                "database_wal_not_checkpointed",
                "Checkpoint the offline SQLite WAL before using a database file digest.",
                exit_code=4,
            )
    except OSError as exc:
        raise IdentityMigrationError(
            "database_state_unavailable",
            "SQLite sidecar state could not be verified.",
            exit_code=4,
        ) from exc


def _replace_identity(value: Any, mapping: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        if value in mapping:
            return mapping[value]
        for prefix in ("disk:", "drive:"):
            raw = value.removeprefix(prefix)
            if raw != value and raw in mapping:
                return f"{prefix}{mapping[raw]}"
        return value
    if isinstance(value, list):
        return [_replace_identity(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: _replace_identity(item, mapping) for key, item in value.items()}
    return value


def _disk_filesystem_uuid(session: Session, disk: PhysicalDisk) -> str | None:
    value = disk.metadata_json.get("filesystem_uuid")
    if isinstance(value, str) and value:
        return value
    return session.scalar(
        select(StorageEntity.filesystem_uuid)
        .join(StorageBackend, StorageBackend.storage_entity_id == StorageEntity.id)
        .where(StorageBackend.physical_disk_id == disk.id)
        .limit(1)
    )


def _validate_source_evidence(
    session: Session,
    disk: PhysicalDisk,
    mapping: IdentityMapping,
    *,
    already_applied: bool,
) -> None:
    expected = mapping.target if already_applied else mapping.source
    fields = (
        ("capacity_bytes", disk.capacity_bytes),
        ("logical_sector_bytes", disk.logical_sector_bytes),
        ("physical_sector_bytes", disk.physical_sector_bytes),
    )
    if any(observed != getattr(expected, name) for name, observed in fields):
        raise IdentityMigrationError(
            "source_geometry_mismatch", "Stored disk capacity or sector geometry does not match."
        )
    if expected.kernel_path is not None and disk.kernel_path != expected.kernel_path:
        raise IdentityMigrationError(
            "source_path_mismatch", "Stored kernel-path observation does not match."
        )
    metadata = disk.metadata_json
    recorded_digest = metadata.get("migration_content_sha256")
    if recorded_digest is not None and recorded_digest != expected.content_sha256:
        raise IdentityMigrationError(
            "source_digest_mismatch", "Stored converted-image digest evidence does not match."
        )
    if mapping.evidence_type == "ext4":
        if _disk_filesystem_uuid(session, disk) != expected.filesystem_uuid:
            raise IdentityMigrationError(
                "source_filesystem_mismatch", "Stored ext4 filesystem identity does not match."
            )
    elif mapping.evidence_type == "zfs":
        if metadata.get("zfs_pool_guid") != expected.zfs_pool_guid or metadata.get(
            "zfs_dataset_guid"
        ) != expected.zfs_dataset_guid:
            raise IdentityMigrationError(
                "source_zfs_mismatch", "Stored ZFS identity evidence does not match."
            )
    elif (
        metadata.get("md_array_uuid") != expected.md_array_uuid
        or metadata.get("md_filesystem_uuid") != expected.md_filesystem_uuid
        or metadata.get("md_member_count") != expected.md_member_count
    ):
        raise IdentityMigrationError(
            "source_md_mismatch", "Stored Linux MD identity evidence does not match."
        )


def _active_use_exists(session: Session, disk: PhysicalDisk) -> bool:
    if disk.metadata_json.get("active_use") is True:
        return True
    backends = list(
        session.scalars(select(StorageBackend).where(StorageBackend.physical_disk_id == disk.id))
    )
    resources: set[str] = {disk.id, disk.stable_identity}
    for backend in backends:
        resources.update((backend.id, backend.stable_identity, backend.storage_group_id))
        if backend.storage_entity_id is not None:
            resources.add(backend.storage_entity_id)
        if backend.lifecycle_state == "draining" or session.scalar(
            select(func.count())
            .select_from(StorageDrainJob)
            .where(
                StorageDrainJob.source_backend_id == backend.id,
                StorageDrainJob.status.in_(ACTIVE_OPERATION_STATES),
            )
        ):
            return True
    return bool(
        session.scalar(
            select(func.count())
            .select_from(Operation)
            .where(
                Operation.status.in_(ACTIVE_OPERATION_STATES),
                Operation.resource_id.in_(sorted(resources)),
            )
        )
    )


def _resolve_mapping_disk(
    session: Session, item: IdentityMapping, manifest_digest: str
) -> tuple[PhysicalDisk, bool]:
    disk = session.scalar(
        select(PhysicalDisk).where(PhysicalDisk.stable_identity == item.old_identity)
    )
    if disk is not None:
        return disk, False
    alias = session.scalar(
        select(PhysicalDiskIdentityAlias).where(
            PhysicalDiskIdentityAlias.alias_identity == item.old_identity
        )
    )
    if alias is None:
        raise IdentityMigrationError(
            "source_identity_unmatched", "A manifest source identity is not managed by Hoardarr."
        )
    disk = session.get(PhysicalDisk, alias.physical_disk_id)
    if (
        disk is None
        or disk.stable_identity != item.new_identity
        or alias.manifest_sha256 != manifest_digest
    ):
        raise IdentityMigrationError(
            "identity_alias_conflict", "A retired identity alias does not match this manifest."
        )
    return disk, True


def _safety_inventory(
    session: Session, manifest: IdentityManifest, manifest_digest: str
) -> list[tuple[IdentityMapping, PhysicalDisk, bool]]:
    result: list[tuple[IdentityMapping, PhysicalDisk, bool]] = []
    for item in manifest.mappings:
        disk, already_applied = _resolve_mapping_disk(session, item, manifest_digest)
        metadata = disk.metadata_json
        if any(
            metadata.get(name) is True
            for name in (
                "system_device",
                "system_disk",
                "boot_disk",
                "protected",
                "protected_identity",
            )
        ):
            raise IdentityMigrationError(
                "protected_disk", "A mapped identity is a system, boot, or protected disk."
            )
        if metadata.get("mounted") is True and (
            metadata.get("foreign") is True or metadata.get("foreign_storage") is True
        ):
            raise IdentityMigrationError(
                "mounted_foreign_disk", "A mapped identity is mounted foreign storage."
            )
        if _active_use_exists(session, disk):
            raise IdentityMigrationError(
                "disk_active", "A mapped disk has an active storage operation or use marker."
            )
        _validate_source_evidence(
            session, disk, item, already_applied=already_applied
        )
        target_disk = session.scalar(
            select(PhysicalDisk).where(PhysicalDisk.stable_identity == item.new_identity)
        )
        if target_disk is not None and target_disk.id != disk.id:
            raise IdentityMigrationError(
                "target_identity_owned", "A target identity already belongs to another disk."
            )
        target_alias = session.scalar(
            select(PhysicalDiskIdentityAlias).where(
                PhysicalDiskIdentityAlias.alias_identity == item.new_identity
            )
        )
        if target_alias is not None and target_alias.physical_disk_id != disk.id:
            raise IdentityMigrationError(
                "target_identity_owned", "A target identity is already owned as an alias."
            )
        target_backend = session.scalar(
            select(StorageBackend).where(
                StorageBackend.stable_identity.in_(
                    (item.new_identity, f"disk:{item.new_identity}")
                )
            )
        )
        if target_backend is not None and target_backend.physical_disk_id != disk.id:
            raise IdentityMigrationError(
                "target_identity_owned", "A target identity already belongs to another backend."
            )
        old_metrics = list(
            session.scalars(
                select(MetricEntity).where(
                    MetricEntity.entity_type == "drive",
                    MetricEntity.stable_id.in_(
                        (item.old_identity, f"drive:{item.old_identity}")
                    ),
                )
            )
        )
        new_metrics = list(
            session.scalars(
                select(MetricEntity).where(
                    MetricEntity.entity_type == "drive",
                    MetricEntity.stable_id.in_(
                        (item.new_identity, f"drive:{item.new_identity}")
                    ),
                )
            )
        )
        if len(old_metrics) > 1 or len(new_metrics) > 1:
            raise IdentityMigrationError(
                "telemetry_identity_ambiguous",
                "Multiple telemetry entities resolve to one physical identity.",
            )
        old_metric = old_metrics[0] if old_metrics else None
        new_metric = new_metrics[0] if new_metrics else None
        if old_metric is not None and new_metric is not None and old_metric.id != new_metric.id:
            raise IdentityMigrationError(
                "target_telemetry_owned",
                "Target telemetry identity already belongs to another metric entity.",
            )
        result.append((item, disk, already_applied))
    if len({disk.id for _, disk, _ in result}) != len(result):
        raise IdentityMigrationError(
            "source_identity_ambiguous", "Multiple mappings resolve to the same managed disk."
        )
    return result


def _preserved_ids(
    session: Session, inventory: list[tuple[IdentityMapping, PhysicalDisk, bool]]
) -> dict[str, Any]:
    disk_ids = sorted({disk.id for _, disk, _ in inventory})
    backends = list(
        session.scalars(
            select(StorageBackend).where(StorageBackend.physical_disk_id.in_(disk_ids))
        )
    )
    group_ids = sorted({item.storage_group_id for item in backends})
    entity_ids = sorted(
        {item.storage_entity_id for item in backends if item.storage_entity_id is not None}
    )
    metric_ids = sorted(
        entity.id
        for item, _, _ in inventory
        for entity in session.scalars(
            select(MetricEntity).where(
                MetricEntity.entity_type == "drive",
                MetricEntity.stable_id.in_((item.old_identity, item.new_identity)),
            )
        )
    )
    alert_count = (
        session.scalar(
            select(func.count()).select_from(MetricAlert).where(MetricAlert.entity_id.in_(metric_ids))
        )
        if metric_ids
        else 0
    )
    return {
        "physical_disk_ids": disk_ids,
        "storage_group_ids": group_ids,
        "backend_ids": sorted(item.id for item in backends),
        "storage_entity_ids": entity_ids,
        "metric_entity_ids": metric_ids,
        "operation_count": session.scalar(select(func.count()).select_from(Operation)) or 0,
        "related_alert_count": alert_count or 0,
    }


def _report(
    session: Session,
    manifest: IdentityManifest,
    manifest_digest: str,
    database_digest: str,
    inventory: list[tuple[IdentityMapping, PhysicalDisk, bool]],
    *,
    status: str,
) -> dict[str, Any]:
    mapped = [
        {
            "old_identity": item.old_identity,
            "new_identity": item.new_identity,
            "physical_disk_id": disk.id,
            "evidence_type": item.evidence_type,
            "state": "already_applied" if already else "proposed",
        }
        for item, disk, already in inventory
    ]
    source_identities = {item.old_identity for item in manifest.mappings}
    unmatched = sorted(
        disk.stable_identity
        for disk in session.scalars(select(PhysicalDisk).order_by(PhysicalDisk.stable_identity))
        if disk.stable_identity not in source_identities
        and all(disk.id != candidate.id for _, candidate, _ in inventory)
    )[:MAX_MAPPINGS]
    return {
        "schema_version": 1,
        "status": status,
        "manifest": {"schema_version": manifest.schema_version, "sha256": manifest_digest},
        "database_precondition": {"expected_sha256": database_digest, "matched": True},
        "mapped_count": sum(not already for _, _, already in inventory),
        "already_applied_count": sum(already for _, _, already in inventory),
        "rejected_count": 0,
        "mappings": mapped,
        "preserved_logical_ids": _preserved_ids(session, inventory),
        "alias_changes": [
            {
                "retired_identity": item.old_identity,
                "current_identity": item.new_identity,
                "physical_disk_id": disk.id,
            }
            for item, disk, already in inventory
            if not already
        ],
        "remaining_unmatched_identities": unmatched,
    }


def _apply_rebind(
    session: Session,
    inventory: list[tuple[IdentityMapping, PhysicalDisk, bool]],
    manifest_digest: str,
) -> None:
    replacements = {
        item.old_identity: item.new_identity for item, _, already in inventory if not already
    }
    replacements.update(
        {
            item.source.kernel_path: item.target.kernel_path
            for item, _, already in inventory
            if not already
            and item.source.kernel_path is not None
            and item.target.kernel_path is not None
        }
    )
    if not replacements:
        return
    for item, disk, already in inventory:
        if already:
            continue
        session.add(
            PhysicalDiskIdentityAlias(
                physical_disk_id=disk.id,
                alias_identity=item.old_identity,
                manifest_sha256=manifest_digest,
            )
        )
        disk.stable_identity = item.new_identity
        if item.target.kernel_path is not None:
            disk.kernel_path = item.target.kernel_path
        if item.target.serial is not None:
            disk.serial = item.target.serial
        if item.target.wwn is not None:
            disk.wwn = item.target.wwn
        disk.metadata_json = _replace_identity(disk.metadata_json, replacements)
    for backend in session.scalars(select(StorageBackend)):
        backend.stable_identity = _replace_identity(backend.stable_identity, replacements)
        backend.config_json = _replace_identity(backend.config_json, replacements)
    for group in session.scalars(select(StorageGroup)):
        group.policy_json = _replace_identity(group.policy_json, replacements)
    for entity in session.scalars(select(StorageEntity)):
        entity.config_json = _replace_identity(entity.config_json, replacements)
        entity.presentation_device = _replace_identity(
            entity.presentation_device, replacements
        )
    for volume in session.scalars(select(StorageVolume)):
        volume.config_json = _replace_identity(volume.config_json, replacements)
        volume.capabilities_json = _replace_identity(volume.capabilities_json, replacements)
        if volume.device_path is not None:
            volume.device_path = _replace_identity(volume.device_path, replacements)
    for path in session.scalars(select(StoragePath)):
        path.kernel_path = _replace_identity(path.kernel_path, replacements)
        path.metadata_json = _replace_identity(path.metadata_json, replacements)
    for metric in session.scalars(select(MetricEntity)):
        metric.stable_id = _replace_identity(metric.stable_id, replacements)
        metric.labels_json = _replace_identity(metric.labels_json, replacements)
        metric.topology_json = _replace_identity(metric.topology_json, replacements)
    session.flush()


FailureHook = Callable[[str, Session], None]


def run_identity_migration(
    factory: sessionmaker[Session],
    *,
    database_path: Path,
    manifest: IdentityManifest,
    manifest_digest: str,
    expected_database_sha256: str,
    apply: bool,
    failure_hook: FailureHook | None = None,
) -> dict[str, Any]:
    if not SHA256_RE.fullmatch(expected_database_sha256):
        raise IdentityMigrationError(
            "database_digest_invalid", "Expected database SHA-256 is malformed.", exit_code=2
        )
    _require_checkpointed_database(database_path)
    observed = database_sha256(database_path)
    if observed != expected_database_sha256:
        raise IdentityMigrationError(
            "database_precondition_failed",
            "Database SHA-256 does not match the supplied precondition.",
            exit_code=4,
        )
    with factory() as session:
        if apply:
            session.execute(text("BEGIN IMMEDIATE"))
        try:
            if database_sha256(database_path) != expected_database_sha256:
                raise IdentityMigrationError(
                    "database_state_drift",
                    "Database changed before the protected transaction began.",
                    exit_code=4,
                )
            inventory = _safety_inventory(session, manifest, manifest_digest)
            before_signature = json.dumps(
                _report(
                    session,
                    manifest,
                    manifest_digest,
                    expected_database_sha256,
                    inventory,
                    status="ready",
                ),
                sort_keys=True,
                separators=(",", ":"),
            )
            if not apply:
                session.rollback()
                if database_sha256(database_path) != expected_database_sha256:
                    raise IdentityMigrationError(
                        "dry_run_mutated_database",
                        "Dry-run changed the database and was rejected.",
                        exit_code=5,
                    )
                return json.loads(before_signature)
            if failure_hook is not None:
                failure_hook("after_validation", session)
            rechecked = _safety_inventory(session, manifest, manifest_digest)
            rechecked_signature = json.dumps(
                _report(
                    session,
                    manifest,
                    manifest_digest,
                    expected_database_sha256,
                    rechecked,
                    status="ready",
                ),
                sort_keys=True,
                separators=(",", ":"),
            )
            if rechecked_signature != before_signature:
                raise IdentityMigrationError(
                    "transaction_state_drift",
                    "Identity safety state changed inside the protected transaction.",
                    exit_code=4,
                )
            _apply_rebind(session, rechecked, manifest_digest)
            if failure_hook is not None:
                failure_hook("after_rebind", session)
            result = _report(
                session,
                manifest,
                manifest_digest,
                expected_database_sha256,
                rechecked,
                status="applied",
            )
            for mapping in result["mappings"]:
                mapping["state"] = "applied" if mapping["state"] == "proposed" else mapping["state"]
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise


def failure_result(exc: IdentityMigrationError) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "rejected",
        "error": {"code": exc.code, "message": exc.safe_message},
        "mapped_count": 0,
        "rejected_count": 1,
    }
