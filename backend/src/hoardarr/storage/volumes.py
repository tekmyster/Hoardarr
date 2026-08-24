from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.db.models import StorageEntity, StorageVolume

_PROVIDERS = frozenset({"filesystem", "zfs", "lvm", "linux_md", "iscsi"})
_RESOURCE_TYPES = frozenset({"filesystem", "dataset", "zvol", "logical_volume", "lun"})
_PRESENTATIONS = frozenset({"file", "block"})
_LIFECYCLE_STATES = frozenset({"active", "read_only", "offline", "retired"})


class StorageVolumeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def canonical_volume_identity(provider: str, resource_type: str, provider_id: str) -> str:
    provider = provider.strip().lower()
    resource_type = resource_type.strip().lower()
    provider_id = provider_id.strip()
    if provider not in _PROVIDERS:
        raise StorageVolumeError(
            "volume_provider_unsupported", "The volume provider is unsupported."
        )
    if resource_type not in _RESOURCE_TYPES:
        raise StorageVolumeError(
            "volume_resource_type_unsupported", "The provider resource type is unsupported."
        )
    if not provider_id or len(provider_id) > 400 or any(ord(char) < 32 for char in provider_id):
        raise StorageVolumeError(
            "volume_provider_identity_invalid", "The provider resource identity is invalid."
        )
    return f"{provider}:{resource_type}:{provider_id}"


def register_volume(session: Session, document: Mapping[str, Any]) -> tuple[StorageVolume, bool]:
    provider = str(document.get("provider", "")).strip().lower()
    resource_type = str(document.get("resource_type", "")).strip().lower()
    provider_id = str(document.get("provider_resource_id", "")).strip()
    stable_identity = canonical_volume_identity(provider, resource_type, provider_id)
    supplied_identity = document.get("stable_identity")
    if supplied_identity is not None and str(supplied_identity) != stable_identity:
        raise StorageVolumeError(
            "volume_identity_mismatch", "The supplied identity does not match provider identity."
        )
    name = str(document.get("name", "")).strip()
    if not name or len(name) > 128:
        raise StorageVolumeError("volume_name_invalid", "The volume name is invalid.")
    presentation = str(document.get("presentation", "")).strip().lower()
    if presentation not in _PRESENTATIONS:
        raise StorageVolumeError(
            "volume_presentation_invalid", "The volume presentation must be file or block."
        )
    lifecycle = str(document.get("lifecycle_state", "active")).strip().lower()
    if lifecycle not in _LIFECYCLE_STATES:
        raise StorageVolumeError("volume_state_invalid", "The volume lifecycle state is invalid.")
    parent_id = document.get("parent_storage_entity_id")
    if parent_id is not None and session.get(StorageEntity, str(parent_id)) is None:
        raise StorageVolumeError(
            "volume_parent_not_found", "The parent storage object was not found."
        )
    for field in ("size_bytes", "allocated_bytes"):
        value = document.get(field)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise StorageVolumeError("volume_capacity_invalid", f"{field} must be non-negative.")

    volume = session.scalar(
        select(StorageVolume).where(StorageVolume.stable_identity == stable_identity)
    )
    created = volume is None
    if volume is None:
        volume = StorageVolume(
            stable_identity=stable_identity,
            provider=provider,
            resource_type=resource_type,
            provider_resource_id=provider_id,
            name=name,
            presentation=presentation,
        )
        session.add(volume)
    elif (
        volume.provider != provider
        or volume.resource_type != resource_type
        or volume.provider_resource_id != provider_id
    ):
        raise StorageVolumeError(
            "volume_identity_changed", "The existing provider identity cannot be changed."
        )

    volume.name = name
    volume.presentation = presentation
    volume.parent_storage_entity_id = str(parent_id) if parent_id is not None else None
    volume.mountpoint = _optional_text(document.get("mountpoint"), 4096)
    volume.device_path = _optional_text(document.get("device_path"), 4096)
    volume.filesystem_type = _optional_text(document.get("filesystem_type"), 64)
    volume.filesystem_uuid = _optional_text(document.get("filesystem_uuid"), 128)
    volume.size_bytes = document.get("size_bytes")
    volume.allocated_bytes = document.get("allocated_bytes")
    volume.lifecycle_state = lifecycle
    config = document.get("config")
    if config is not None and not isinstance(config, dict):
        raise StorageVolumeError("volume_config_invalid", "Volume configuration must be an object.")
    volume.config_json = dict(config or {})
    session.flush()
    return volume, created


def _optional_text(value: object, maximum: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > maximum or any(ord(char) < 32 for char in text):
        raise StorageVolumeError("volume_value_invalid", "A volume value is invalid.")
    return text


def volume_documents(session: Session) -> list[dict[str, object]]:
    items = session.scalars(select(StorageVolume).order_by(StorageVolume.name, StorageVolume.id))
    return [
        {
            "id": item.id,
            "stable_identity": item.stable_identity,
            "name": item.name,
            "provider": item.provider,
            "resource_type": item.resource_type,
            "provider_resource_id": item.provider_resource_id,
            "presentation": item.presentation,
            "parent_storage_entity_id": item.parent_storage_entity_id,
            "mountpoint": item.mountpoint,
            "device_path": item.device_path,
            "filesystem_type": item.filesystem_type,
            "filesystem_uuid": item.filesystem_uuid,
            "size_bytes": item.size_bytes,
            "allocated_bytes": item.allocated_bytes,
            "lifecycle_state": item.lifecycle_state,
            "config": dict(item.config_json),
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        }
        for item in items
    ]
