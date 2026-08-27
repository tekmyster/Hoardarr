from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

RTSLIB_SAVECONFIG_PATH = Path("/etc/rtslib-fb-target/saveconfig.json")
MAX_SAVECONFIG_BYTES = 4 * 1024 * 1024
MAX_COLLECTION_ENTRIES = 256


class LioReadbackError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _failure(code: str = "connectivity_lio_readback_invalid") -> LioReadbackError:
    return LioReadbackError(code, "The current iSCSI target state could not be verified.")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise _failure("connectivity_lio_readback_ambiguous")
        document[key] = value
    return document


def read_saveconfig(path: Path = RTSLIB_SAVECONFIG_PATH) -> dict[str, Any]:
    descriptor = -1
    try:
        named = path.lstat()
        if stat.S_ISLNK(named.st_mode) or not stat.S_ISREG(named.st_mode):
            raise _failure()
        if named.st_size > MAX_SAVECONFIG_BYTES:
            raise _failure("connectivity_lio_readback_too_large")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size > MAX_SAVECONFIG_BYTES
            or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise _failure()
        chunks: list[bytes] = []
        remaining = MAX_SAVECONFIG_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_SAVECONFIG_BYTES:
            raise _failure("connectivity_lio_readback_too_large")
    except LioReadbackError:
        raise
    except OSError as exc:
        raise _failure("connectivity_lio_readback_unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except LioReadbackError:
        raise
    except (UnicodeDecodeError, RecursionError, ValueError) as exc:
        raise _failure() from exc
    if not isinstance(document, dict):
        raise _failure()
    _bounded_list(document, "storage_objects")
    _bounded_list(document, "targets")
    return document


def _bounded_list(document: Mapping[str, Any], key: str) -> list[Any]:
    value = document.get(key)
    if not isinstance(value, list) or len(value) > MAX_COLLECTION_ENTRIES:
        raise _failure()
    return value


def _object(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _failure()
    return value


def _unique_match(
    values: list[Any], *, key: str, expected: str, missing_code: str
) -> Mapping[str, Any]:
    candidates: list[Mapping[str, Any]] = []
    for value in values:
        item = _object(value)
        identity = item.get(key)
        if not isinstance(identity, str):
            raise _failure()
        if identity == expected:
            candidates.append(item)
    if not candidates:
        raise _failure(missing_code)
    if len(candidates) != 1:
        raise _failure("connectivity_lio_readback_ambiguous")
    return candidates[0]


def _digest(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def managed_backstore_name(service_id: str) -> str:
    return f"hoardarr-zvol-{hashlib.sha256(service_id.encode()).hexdigest()[:24]}"


def verify_managed_apply(
    document: Mapping[str, Any],
    *,
    service_id: str,
    config: Mapping[str, Any],
    secret: str | None,
) -> dict[str, Any]:
    binding = _object(config.get("managed_zvol_binding"))
    backstore_name = managed_backstore_name(service_id)
    storage = _unique_match(
        _bounded_list(document, "storage_objects"),
        key="name",
        expected=backstore_name,
        missing_code="connectivity_lio_readback_mismatch",
    )
    if storage.get("plugin") != "block" or storage.get("dev") != binding.get("device_path"):
        raise _failure("connectivity_lio_readback_mismatch")

    target_iqn = config.get("target_iqn")
    if not isinstance(target_iqn, str):
        raise _failure()
    target = _unique_match(
        _bounded_list(document, "targets"),
        key="wwn",
        expected=target_iqn,
        missing_code="connectivity_lio_readback_mismatch",
    )
    if target.get("fabric") != "iscsi":
        raise _failure("connectivity_lio_readback_mismatch")
    tpgs = _bounded_list(target, "tpgs")
    if len(tpgs) != 1:
        raise _failure("connectivity_lio_readback_ambiguous")
    tpg = _object(tpgs[0])
    tag = tpg.get("tag")
    if not isinstance(tag, int) or isinstance(tag, bool) or tag != 1:
        raise _failure("connectivity_lio_readback_mismatch")

    luns = _bounded_list(tpg, "luns")
    if len(luns) != 1:
        raise _failure("connectivity_lio_readback_mismatch")
    lun = _object(luns[0])
    if lun.get("index") != 0 or lun.get("storage_object") != (
        f"/backstores/block/{backstore_name}"
    ):
        raise _failure("connectivity_lio_readback_mismatch")

    expected_portals = {(address, 3260) for address in config.get("portal_ips", [])}
    portal_pairs: list[tuple[str, int]] = []
    for raw_portal in _bounded_list(tpg, "portals"):
        portal = _object(raw_portal)
        address, port = portal.get("ip_address"), portal.get("port")
        if not isinstance(address, str) or not isinstance(port, int) or isinstance(port, bool):
            raise _failure()
        portal_pairs.append((address, port))
    if len(portal_pairs) != len(set(portal_pairs)) or set(portal_pairs) != expected_portals:
        raise _failure("connectivity_lio_readback_mismatch")

    expected_initiators = set(config.get("initiator_iqns", []))
    acl_identities: list[str] = []
    chap_enabled = config.get("chap_enabled") is True
    chap_user_matches = True
    chap_secret_matches = True
    for raw_acl in _bounded_list(tpg, "node_acls"):
        acl = _object(raw_acl)
        identity = acl.get("node_wwn")
        if not isinstance(identity, str):
            raise _failure()
        acl_identities.append(identity)
        user = acl.get("chap_userid")
        password = acl.get("chap_password")
        mutual_user = acl.get("chap_mutual_userid")
        mutual_password = acl.get("chap_mutual_password")
        if chap_enabled:
            chap_user_matches = chap_user_matches and user == config.get("chap_username")
            chap_secret_matches = chap_secret_matches and password == secret
            if mutual_user not in (None, "") or mutual_password not in (None, ""):
                raise _failure("connectivity_lio_readback_mismatch")
        elif any(
            value not in (None, "") for value in (user, password, mutual_user, mutual_password)
        ):
            raise _failure("connectivity_lio_readback_mismatch")
    if (
        len(acl_identities) != len(set(acl_identities))
        or set(acl_identities) != expected_initiators
    ):
        raise _failure("connectivity_lio_readback_mismatch")
    if chap_enabled and (not chap_user_matches or not chap_secret_matches):
        raise _failure("connectivity_lio_readback_auth_mismatch")

    attributes = _object(tpg.get("attributes"))
    generate_node_acls = attributes.get("generate_node_acls")
    demo_write_protect = attributes.get("demo_mode_write_protect")
    if (
        not isinstance(generate_node_acls, int)
        or isinstance(generate_node_acls, bool)
        or generate_node_acls != 0
        or not isinstance(demo_write_protect, int)
        or isinstance(demo_write_protect, bool)
        or demo_write_protect != 1
    ):
        raise _failure("connectivity_lio_readback_mismatch")

    volume_id = binding.get("storage_volume_id")
    if not isinstance(volume_id, str) or len(volume_id) < 8:
        raise _failure()
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "state": "active",
        "service_id": service_id,
        "target_iqn": target_iqn,
        "backstore_name": backstore_name,
        "backstore_plugin": "block",
        "device_matches_binding": True,
        "managed_volume_id": f"redacted-{volume_id[-8:]}",
        "tpg_tag": tag,
        "lun_index": 0,
        "portals": [
            {"ip_address": address, "port": port} for address, port in sorted(portal_pairs)
        ],
        "initiator_iqns": sorted(acl_identities),
        "chap_configured": chap_enabled,
        "chap_user_matches": chap_user_matches,
        "chap_secret_matches": chap_secret_matches,
        "safety_attributes": {
            "generate_node_acls": 0,
            "demo_mode_write_protect": 1,
        },
    }
    evidence["evidence_sha256"] = _digest(evidence)
    return evidence


def verify_managed_absent(
    document: Mapping[str, Any],
    *,
    service_id: str,
    target_iqn: str,
) -> dict[str, Any]:
    backstore_name = managed_backstore_name(service_id)
    for raw_storage in _bounded_list(document, "storage_objects"):
        storage = _object(raw_storage)
        if not isinstance(storage.get("name"), str):
            raise _failure()
        if storage["name"] == backstore_name:
            raise _failure("connectivity_lio_readback_removal_incomplete")
    for raw_target in _bounded_list(document, "targets"):
        target = _object(raw_target)
        if not isinstance(target.get("wwn"), str):
            raise _failure()
        if target["wwn"] == target_iqn:
            raise _failure("connectivity_lio_readback_removal_incomplete")
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "state": "absent",
        "service_id": service_id,
        "target_iqn": target_iqn,
        "backstore_name": backstore_name,
        "target_absent": True,
        "backstore_absent": True,
    }
    evidence["evidence_sha256"] = _digest(evidence)
    return evidence
