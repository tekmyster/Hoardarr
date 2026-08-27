from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.db.models import StorageVolume
from hoardarr.storage.acl import normalize_acl

IQN_RE = re.compile(r"^iqn\.\d{4}-\d{2}\.[a-z0-9.-]+:[A-Za-z0-9_.:-]{1,128}$")
WWPN_RE = re.compile(r"^(?:[0-9a-f]{2}:){7}[0-9a-f]{2}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
INTERFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}$")
MANAGED_PATH_RE = re.compile(r"^/(?:[A-Za-z0-9._@+-]+)(?:/[A-Za-z0-9._@+-]+)*$")
ZVOL_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")
MANAGED_ZVOL_DIGEST_FIELDS = (
    "storage_volume_id",
    "stable_identity",
    "provider",
    "resource_type",
    "provider_resource_id",
    "device_path",
    "size_bytes",
)
MAX_BLOCK_SIZE_BYTES = 8 * 1024**5


class ManagedZvolBindingError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def config_hash(config: dict[str, Any]) -> str:
    return sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _binding_hash(document: Mapping[str, Any]) -> str:
    return config_hash({field: document.get(field) for field in MANAGED_ZVOL_DIGEST_FIELDS})


def validate_managed_zvol_binding(document: object) -> dict[str, Any]:
    if not isinstance(document, Mapping) or set(document) != {
        "kind",
        *MANAGED_ZVOL_DIGEST_FIELDS,
        "binding_sha256",
    }:
        raise ManagedZvolBindingError(
            "connectivity_managed_zvol_binding_invalid",
            "The managed volume binding is invalid.",
        )
    binding = dict(document)
    provider_id = binding.get("provider_resource_id")
    device_path = binding.get("device_path")
    parts = PurePosixPath(str(provider_id)).parts if isinstance(provider_id, str) else ()
    if (
        binding.get("kind") != "managed_zvol"
        or not isinstance(binding.get("storage_volume_id"), str)
        or len(binding["storage_volume_id"]) != 36
        or binding.get("provider") != "zfs"
        or binding.get("resource_type") != "zvol"
        or len(parts) < 2
        or any(not ZVOL_COMPONENT_RE.fullmatch(part) for part in parts)
        or str(PurePosixPath(str(provider_id))) != provider_id
        or binding.get("stable_identity") != f"zfs:zvol:{provider_id}"
        or device_path != f"/dev/zvol/{provider_id}"
        or not isinstance(binding.get("size_bytes"), int)
        or isinstance(binding.get("size_bytes"), bool)
        or not 0 < binding["size_bytes"] <= MAX_BLOCK_SIZE_BYTES
        or not isinstance(binding.get("binding_sha256"), str)
        or binding["binding_sha256"] != _binding_hash(binding)
    ):
        raise ManagedZvolBindingError(
            "connectivity_managed_zvol_binding_invalid",
            "The managed volume binding is invalid.",
        )
    return binding


def resolve_managed_zvol_binding(
    session: Session,
    *,
    storage_volume_id: str,
    expected: object | None = None,
) -> dict[str, Any]:
    rows = list(
        session.scalars(select(StorageVolume).where(StorageVolume.id == storage_volume_id).limit(2))
    )
    if len(rows) != 1:
        raise ManagedZvolBindingError(
            "connectivity_managed_zvol_not_found",
            "The selected managed block volume is unavailable.",
        )
    volume = rows[0]
    capabilities = volume.capabilities_json
    block = capabilities.get("block_presentation") if isinstance(capabilities, Mapping) else None
    size = capabilities.get("size") if isinstance(capabilities, Mapping) else None
    if (
        volume.provider != "zfs"
        or volume.resource_type != "zvol"
        or volume.presentation != "block"
        or volume.lifecycle_state != "active"
        or not isinstance(block, Mapping)
        or block.get("support") != "supported"
        or block.get("availability") != "available"
        or not isinstance(size, Mapping)
        or size.get("support") != "supported"
        or size.get("availability") != "available"
    ):
        raise ManagedZvolBindingError(
            "connectivity_managed_zvol_ineligible",
            "The selected volume is not an active supported ZFS block volume.",
        )
    binding: dict[str, Any] = {
        "kind": "managed_zvol",
        "storage_volume_id": volume.id,
        "stable_identity": volume.stable_identity,
        "provider": volume.provider,
        "resource_type": volume.resource_type,
        "provider_resource_id": volume.provider_resource_id,
        "device_path": volume.device_path,
        "size_bytes": volume.size_bytes,
    }
    binding["binding_sha256"] = _binding_hash(binding)
    binding = validate_managed_zvol_binding(binding)
    if expected is not None:
        validated_expected = validate_managed_zvol_binding(expected)
        if validated_expected != binding:
            raise ManagedZvolBindingError(
                "connectivity_managed_zvol_changed",
                "The managed block volume changed and must be reviewed again.",
            )
    return binding


def _path(value: str | None, field: str) -> str:
    if (
        not value
        or not value.startswith("/")
        or "\0" in value
        or ".." in PurePosixPath(value).parts
        or MANAGED_PATH_RE.fullmatch(value) is None
    ):
        raise ValueError(f"{field} must be a safe absolute storage path")
    return str(PurePosixPath(value))


def normalize_connectivity_request(
    payload: Any,
    *,
    require_secret: bool,
    managed_zvol_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    protocol = payload.protocol
    if protocol == "smb":
        if payload.write_users or payload.read_users:
            write_users = sorted(set(payload.write_users))
            read_users = sorted(set(payload.read_users) - set(write_users))
        else:
            write_users = [] if payload.read_only else sorted(set(payload.valid_users))
            read_users = sorted(set(payload.valid_users)) if payload.read_only else []
        acl = normalize_acl(
            {
                "path": payload.path,
                "entries": [item.model_dump() for item in payload.acl_entries]
                or [
                    *(
                        {"kind": "user", "name": user, "role": "media_application"}
                        for user in write_users
                    ),
                    *({"kind": "user", "name": user, "role": "media_user"} for user in read_users),
                ],
                "inherit": payload.inherit_acl,
            }
        )
        acl_tokens = {
            (entry["name"] if entry["kind"] == "user" else f"@{entry['name']}")
            for entry in acl["entries"]
        }
        valid_users = sorted(
            set(payload.valid_users) | set(write_users) | set(read_users) | acl_tokens
        )
        write_users = sorted(
            set(write_users)
            | {
                entry["name"] if entry["kind"] == "user" else f"@{entry['name']}"
                for entry in acl["entries"]
                if entry["role"] in {"administrator", "media_application"}
            }
        )
        read_users = sorted(
            (
                set(read_users)
                | {
                    entry["name"] if entry["kind"] == "user" else f"@{entry['name']}"
                    for entry in acl["entries"]
                    if entry["role"] == "media_user"
                }
            )
            - set(write_users)
        )
        if not valid_users or any(
            not USER_RE.fullmatch(user[1:] if user.startswith("@") else user)
            for user in valid_users
        ):
            raise ValueError("SMB permissions must contain at least one Linux account or group")
        if any(
            (
                payload.clients,
                payload.backing_path,
                payload.size_bytes,
                payload.storage_volume_id,
                payload.target_iqn,
                payload.portal_ips,
                payload.initiator_iqns,
                payload.interfaces,
                payload.initiator_wwpns,
            )
        ):
            raise ValueError("SMB request contains fields for another protocol")
        return {
            "protocol": "smb",
            "name": payload.name,
            "path": _path(payload.path, "path"),
            "read_only": not write_users,
            "valid_users": valid_users,
            "write_users": write_users,
            "read_users": read_users,
            "browseable": payload.browseable,
            "acl": acl,
        }
    if protocol == "nfs":
        if not payload.clients:
            raise ValueError("clients must contain at least one network")
        try:
            clients = sorted(
                {str(ipaddress.ip_network(client, strict=False)) for client in payload.clients}
            )
        except ValueError as exc:
            raise ValueError("clients contains an invalid network") from exc
        if any(
            (
                payload.valid_users,
                payload.write_users,
                payload.read_users,
                payload.backing_path,
                payload.size_bytes,
                payload.storage_volume_id,
                payload.target_iqn,
                payload.portal_ips,
                payload.initiator_iqns,
                payload.interfaces,
                payload.initiator_wwpns,
            )
        ):
            raise ValueError("NFS request contains fields for another protocol")
        return {
            "protocol": "nfs",
            "name": payload.name,
            "path": _path(payload.path, "path"),
            "read_only": payload.read_only,
            "clients": clients,
        }
    if protocol == "iscsi":
        managed_zvol = payload.storage_volume_id is not None
        if (
            not payload.target_iqn
            or not IQN_RE.fullmatch(payload.target_iqn.lower())
            or not payload.portal_ips
            or not payload.initiator_iqns
        ):
            raise ValueError("iSCSI target, portals, and initiators are required")
        if managed_zvol:
            if payload.backing_path is not None or payload.size_bytes is not None:
                raise ValueError("managed iSCSI volumes cannot accept a caller path or size")
            if managed_zvol_binding is not None:
                binding = validate_managed_zvol_binding(managed_zvol_binding)
                if binding["storage_volume_id"] != payload.storage_volume_id:
                    raise ValueError("managed iSCSI volume binding does not match the request")
            else:
                binding = None
        else:
            if not payload.size_bytes:
                raise ValueError("iSCSI backing path and size are required")
            binding = None
        try:
            portals = sorted({str(ipaddress.ip_address(item)) for item in payload.portal_ips})
        except ValueError as exc:
            raise ValueError("portal_ips contains an invalid address") from exc
        initiators = sorted({item.lower() for item in payload.initiator_iqns})
        if any(not IQN_RE.fullmatch(item) for item in initiators):
            raise ValueError("initiator_iqns contains an invalid IQN")
        if payload.chap_enabled:
            if not payload.chap_username:
                raise ValueError("chap_username is required")
            if (
                require_secret
                and payload.chap_password is None
                and not payload.generate_chap_password
            ):
                raise ValueError("set or generate a CHAP password")
        elif payload.chap_username or payload.chap_password or payload.generate_chap_password:
            raise ValueError("CHAP fields require chap_enabled")
        if any(
            (
                payload.path,
                payload.valid_users,
                payload.write_users,
                payload.read_users,
                payload.clients,
                payload.interfaces,
                payload.initiator_wwpns,
            )
        ):
            raise ValueError("iSCSI request contains fields for another protocol")
        if managed_zvol:
            return {
                "protocol": "iscsi",
                "name": payload.name,
                "managed_zvol_binding": binding
                if binding is not None
                else {"storage_volume_id": payload.storage_volume_id},
                "target_iqn": payload.target_iqn.lower(),
                "portal_ips": portals,
                "initiator_iqns": initiators,
                "chap_username": payload.chap_username if payload.chap_enabled else None,
                "chap_enabled": payload.chap_enabled,
            }
        return {
            "protocol": "iscsi",
            "name": payload.name,
            "backing_path": _path(payload.backing_path, "backing_path"),
            "size_bytes": payload.size_bytes,
            "target_iqn": payload.target_iqn.lower(),
            "portal_ips": portals,
            "initiator_iqns": initiators,
            "chap_username": payload.chap_username if payload.chap_enabled else None,
            "chap_enabled": payload.chap_enabled,
        }
    if (
        not payload.size_bytes
        or not payload.interfaces
        or len(set(payload.interfaces)) != len(payload.interfaces)
        or any(not INTERFACE_RE.fullmatch(item) for item in payload.interfaces)
        or not payload.initiator_wwpns
    ):
        raise ValueError("FCoE network ports, size, and initiators are required")
    initiators = sorted({item.lower() for item in payload.initiator_wwpns})
    if any(not WWPN_RE.fullmatch(item) for item in initiators):
        raise ValueError("initiator_wwpns contains an invalid WWPN")
    if payload.fcoe_mode == "fabric" and payload.fip_responder:
        raise ValueError("FIP responder is only available in VN2VN mode")
    if payload.fcoe_mode == "vn2vn" and payload.auto_vlan:
        raise ValueError("automatic VLAN discovery is only available in fabric mode")
    if any(
        (
            payload.path,
            payload.valid_users,
            payload.write_users,
            payload.read_users,
            payload.clients,
            payload.target_iqn,
            payload.portal_ips,
            payload.initiator_iqns,
            payload.chap_username,
            payload.chap_password,
            payload.generate_chap_password,
            payload.storage_volume_id,
        )
    ):
        raise ValueError("FCoE request contains fields for another protocol")
    return {
        "protocol": "fcoe",
        "name": payload.name,
        "backing_path": _path(payload.backing_path, "backing_path"),
        "size_bytes": payload.size_bytes,
        "interfaces": sorted(payload.interfaces),
        "fcoe_mode": payload.fcoe_mode,
        "dcb_mode": payload.dcb_mode,
        "auto_vlan": payload.auto_vlan,
        "fip_responder": payload.fip_responder,
        "initiator_wwpns": initiators,
    }
