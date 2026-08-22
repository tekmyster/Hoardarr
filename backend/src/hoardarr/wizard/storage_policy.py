from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from hoardarr.operations.service import document_hash
from hoardarr.storage.layouts import LayoutError, normalize_layout

GUIDED_MODES = frozenset({"guided", "simple"})
GUIDED_TOPOLOGIES = frozenset({"individual", "mergerfs", "cache", "block", "import", "test"})
ARRAY_TOPOLOGIES = frozenset({"zfs", "raid", "snapraid", "mixed"})
DESTRUCTIVE_LAYOUT_TOPOLOGIES = ARRAY_TOPOLOGIES
ALL_TOPOLOGIES = GUIDED_TOPOLOGIES | ARRAY_TOPOLOGIES
STANDARD_LIBRARIES = ("Movies", "TV", "Music", "Photos", "Books", "Audiobooks")
REQUIRED_CONSENT_PHRASE = "I AGREE"
INTAKE_TESTS = (
    "identity",
    "full_surface_read",
    "smart_short",
    "smart_extended",
    "destructive_write_read",
)

_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_MERGERFS_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MERGERFS_INSTANCE_RE = re.compile(r"^mergerfs:[a-f0-9]{16}$")
_CUSTOM_APPS = frozenset({"radarr", "sonarr", "lidarr", "readarr", "immich", "none"})
_CONTENT_TYPES = frozenset({"movies", "series", "music", "photos", "books", "audiobooks", "both"})
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)


class StoragePolicyError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


def _error(field: str, message: str) -> None:
    raise StoragePolicyError(field, message)


def _as_mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _error(field, "must be an object")
    return value


def _as_bool(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        _error(field, "must be true or false")
    return value


def _mergerfs_mountpoint(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or any(character.isspace() or ord(character) < 32 for character in value)
        or "\\" in value
    ):
        _error("storage.mergerfs.mountpoint", "must be an absolute Linux path")
    path = PurePosixPath(value)
    if ".." in path.parts or path == PurePosixPath("/"):
        _error("storage.mergerfs.mountpoint", "cannot be the root directory or contain ..")
    allowed_roots = (PurePosixPath("/mnt"), PurePosixPath("/srv"), PurePosixPath("/data"))
    if not any(root in path.parents for root in allowed_roots):
        _error("storage.mergerfs.mountpoint", "must be beneath /mnt, /srv, or /data")
    return str(path)


def _normalize_mergerfs(value: Any) -> dict[str, Any]:
    mergerfs = _as_mapping(value, field="storage.mergerfs")
    mode = mergerfs.get("mode")
    if mode not in {"existing", "create"}:
        _error("storage.mergerfs.mode", "must be existing or create")
    allowed = {"mode", "instance_id", "name", "mountpoint", "create_policy", "search_policy"}
    unknown = sorted(set(mergerfs) - allowed)
    if unknown:
        _error("storage.mergerfs", f"unknown fields: {', '.join(unknown)}")
    mountpoint = _mergerfs_mountpoint(mergerfs.get("mountpoint"))
    name = mergerfs.get("name")
    if not isinstance(name, str) or not _MERGERFS_NAME_RE.fullmatch(name):
        _error(
            "storage.mergerfs.name",
            "must contain 1-64 lowercase letters, numbers, dots, dashes, or underscores",
        )
    if mode == "existing":
        instance_id = mergerfs.get("instance_id")
        if not isinstance(instance_id, str) or not _MERGERFS_INSTANCE_RE.fullmatch(instance_id):
            _error("storage.mergerfs.instance_id", "must identify a discovered mergerFS instance")
        return {
            "mode": "existing",
            "instance_id": instance_id,
            "name": name,
            "mountpoint": mountpoint,
        }
    create_policy = mergerfs.get("create_policy", "mfs")
    search_policy = mergerfs.get("search_policy", "ff")
    if create_policy not in {"mfs", "epmfs"}:
        _error("storage.mergerfs.create_policy", "must be mfs or epmfs")
    if search_policy not in {"ff", "all"}:
        _error("storage.mergerfs.search_policy", "must be ff or all")
    return {
        "mode": "create",
        "name": name,
        "mountpoint": mountpoint,
        "create_policy": create_policy,
        "search_policy": search_policy,
    }


def snapshot_disks(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    disks = payload.get("disks", [])
    if not isinstance(disks, list):
        _error("hardware_snapshot.disks", "must be a list")
    result: list[Mapping[str, Any]] = []
    for index, disk in enumerate(disks):
        if not isinstance(disk, Mapping):
            _error(f"hardware_snapshot.disks[{index}]", "must be an object")
        result.append(disk)
    return result


def _device_id(disk: Mapping[str, Any], *, index: int) -> str:
    value = disk.get("id")
    if not isinstance(value, str) or not value.strip():
        _error(f"hardware_snapshot.disks[{index}].id", "is required")
    return value.strip()


def _connection(disk: Mapping[str, Any]) -> Mapping[str, Any]:
    value = disk.get("connection", {})
    return value if isinstance(value, Mapping) else {}


def _identity(disk: Mapping[str, Any]) -> Mapping[str, Any]:
    value = disk.get("identity", {})
    return value if isinstance(value, Mapping) else {}


def _sector_sizes(disk: Mapping[str, Any]) -> Mapping[str, Any]:
    value = disk.get("sector_sizes", {})
    return value if isinstance(value, Mapping) else {}


def _signature_scan_document(disk: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    value = disk.get("signature_scan")
    if value is None:
        return {
            "status": "unavailable",
            "reason": "Signature-scan evidence was not present in the hardware snapshot.",
            "source": None,
        }
    if not isinstance(value, Mapping):
        _error(f"hardware_snapshot.disks[{index}].signature_scan", "must be an object")
    status = value.get("status")
    if status not in {"complete", "partial", "unavailable"}:
        _error(
            f"hardware_snapshot.disks[{index}].signature_scan.status",
            "must be complete, partial, or unavailable",
        )
    reason = value.get("reason")
    source = value.get("source")
    if reason is not None and not isinstance(reason, str):
        _error(f"hardware_snapshot.disks[{index}].signature_scan.reason", "must be a string")
    if source is not None and not isinstance(source, str):
        _error(f"hardware_snapshot.disks[{index}].signature_scan.source", "must be a string")
    return {"status": status, "reason": reason, "source": source}


def _existing_data_assessment(
    *,
    partitions: list[Any],
    signatures: list[Any],
    signature_scan: Mapping[str, Any],
) -> dict[str, str]:
    if partitions or signatures:
        return {
            "status": "detected",
            "reason": "The discovery snapshot reports partitions or storage signatures.",
        }
    if signature_scan["status"] == "complete":
        return {
            "status": "not_detected",
            "reason": "A complete signature scan did not detect partitions or signatures.",
        }
    reason = signature_scan.get("reason")
    detail = (
        reason if isinstance(reason, str) and reason else "The signature scan was not complete."
    )
    return {
        "status": "unknown",
        "reason": f"Existing data cannot be ruled out. {detail}",
    }


def device_review_document(disk: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    identity = _identity(disk)
    connection = _connection(disk)
    sector_sizes = _sector_sizes(disk)
    partitions = disk.get("partitions", [])
    signatures = disk.get("signatures", [])
    if not isinstance(partitions, list):
        _error(f"hardware_snapshot.disks[{index}].partitions", "must be a list")
    if not isinstance(signatures, list):
        _error(f"hardware_snapshot.disks[{index}].signatures", "must be a list")
    signature_scan = _signature_scan_document(disk, index=index)
    discard = disk.get("discard") if isinstance(disk.get("discard"), Mapping) else {}
    return {
        "id": _device_id(disk, index=index),
        "stable_identity": disk.get("stable_identity"),
        "system_disk": disk.get("system_disk") is True,
        "kernel_path": disk.get("kernel_path"),
        "vendor": disk.get("vendor"),
        "model": disk.get("model"),
        "serial": identity.get("serial"),
        "wwn": identity.get("wwn"),
        "eui64": identity.get("eui64"),
        "nguid": identity.get("nguid"),
        "capacity_bytes": disk.get("capacity_bytes"),
        "logical_sector_bytes": sector_sizes.get("logical_bytes"),
        "physical_sector_bytes": sector_sizes.get("physical_bytes"),
        "transport": connection.get("transport"),
        "protocol": connection.get("protocol"),
        "controller_address": connection.get("controller_address"),
        "enclosure_id": connection.get("enclosure_id"),
        "slot": connection.get("slot"),
        "read_only": disk.get("read_only"),
        "discard": {
            "granularity_bytes": discard.get("granularity_bytes"),
            "max_bytes": discard.get("max_bytes"),
            "zeroes_data": discard.get("zeroes_data"),
        },
        "signatures": signatures,
        "partitions": partitions,
        "signature_scan": signature_scan,
        "existing_data": _existing_data_assessment(
            partitions=partitions,
            signatures=signatures,
            signature_scan=signature_scan,
        ),
    }


def select_devices(
    snapshot_payload: Mapping[str, Any], selected_ids: Sequence[str]
) -> list[dict[str, Any]]:
    disks = snapshot_disks(snapshot_payload)
    by_id: dict[str, tuple[int, Mapping[str, Any]]] = {}
    for index, disk in enumerate(disks):
        disk_id = _device_id(disk, index=index)
        if disk_id in by_id:
            _error("hardware_snapshot.disks", f"duplicate device identity: {disk_id}")
        by_id[disk_id] = (index, disk)

    selected: list[dict[str, Any]] = []
    for index, selected_id in enumerate(selected_ids):
        if selected_id not in by_id:
            _error(
                f"storage.selected_device_ids[{index}]",
                "device is not present in the bound hardware discovery snapshot",
            )
        disk_index, disk = by_id[selected_id]
        review = device_review_document(disk, index=disk_index)
        if review["system_disk"] is True:
            _error(
                f"storage.selected_device_ids[{index}]",
                "device contains the running operating system and cannot be used in a storage plan",
            )
        if review["stable_identity"] is not True:
            _error(
                f"storage.selected_device_ids[{index}]",
                "device has no stable identity and cannot be used in a storage plan",
            )
        selected.append(review)
    return selected


def _has_known_sector_size(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _require_sector_geometry(
    devices: Sequence[Mapping[str, Any]],
    *,
    operation: str,
) -> None:
    for index, device in enumerate(devices):
        missing: list[str] = []
        if not _has_known_sector_size(device.get("logical_sector_bytes")):
            missing.append("logical")
        if not _has_known_sector_size(device.get("physical_sector_bytes")):
            missing.append("physical")
        if missing:
            _error(
                f"storage.selected_device_ids[{index}]",
                f"cannot plan {operation}: {' and '.join(missing)} sector geometry is unknown; "
                "run hardware discovery that reports both sector sizes",
            )
        logical = int(device["logical_sector_bytes"])
        physical = int(device["physical_sector_bytes"])
        compatible = (
            logical in {512, 4096}
            and physical >= logical
            and physical % logical == 0
            and physical & (physical - 1) == 0
        )
        if not compatible:
            sector_description = f"logical={logical}, physical={physical}"
            media_description = (
                "520/528-byte sector media"
                if 520 in {logical, physical} or 528 in {logical, physical}
                else f"sector geometry {sector_description}"
            )
            _error(
                f"storage.selected_device_ids[{index}]",
                f"cannot plan {operation}: {media_description} is not compatible with ordinary "
                "OS filesystem or storage-layout creation; an explicit low-level sector reformat "
                "workflow is required and is not implemented",
            )


def _normalize_selected_ids(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        _error("storage.selected_device_ids", "select at least one discovered drive")
    selected: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            _error(f"storage.selected_device_ids[{index}]", "must be a device identity")
        selected.append(item.strip())
    if len(selected) != len(set(selected)):
        _error("storage.selected_device_ids", "must not contain duplicate drives")
    return selected


def _normalize_portability(value: Any) -> list[str]:
    if value is None:
        return ["linux"]
    if not isinstance(value, list) or not value:
        _error("storage.portable_systems", "select at least one operating system")
    allowed = frozenset({"windows", "linux", "macos"})
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or item.lower() not in allowed:
            _error(
                f"storage.portable_systems[{index}]",
                "must be windows, linux, or macos",
            )
        normalized = item.lower()
        if normalized not in result:
            result.append(normalized)
    return result


def _normalize_libraries(value: Any) -> list[str]:
    if value is None:
        return list(STANDARD_LIBRARIES)
    if not isinstance(value, list):
        _error("storage.libraries", "must be a list")
    canonical = {name.casefold(): name for name in STANDARD_LIBRARIES}
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or item.casefold() not in canonical:
            _error(
                f"storage.libraries[{index}]",
                "must be Movies, TV, Music, Photos, Books, or Audiobooks",
            )
        name = canonical[item.casefold()]
        if name not in result:
            result.append(name)
    return result


def _normalize_custom_libraries(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        _error("storage.custom_libraries", "must be a list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        field = f"storage.custom_libraries[{index}]"
        library = _as_mapping(item, field=field)
        if set(library) != {"name", "content_type", "applications"}:
            _error(field, "must contain name, content_type, and applications")
        name = library.get("name")
        if not isinstance(name, str) or not name.strip():
            _error(f"{field}.name", "is required")
        name = name.strip()
        if len(name) > 128:
            _error(f"{field}.name", "must be 128 characters or fewer")
        if any(character in '<>:"/\\|?*' or ord(character) < 32 for character in name):
            _error(f"{field}.name", "must be safe as a Windows and Linux folder name")
        if name.endswith((".", " ")) or name.casefold() in _WINDOWS_RESERVED_NAMES:
            _error(f"{field}.name", "must be safe as a Windows and Linux folder name")
        if name.casefold() == "other":
            _error(f"{field}.name", "Other is not a library; give the library a specific name")
        if name.casefold() in {standard.casefold() for standard in STANDARD_LIBRARIES}:
            _error(f"{field}.name", "duplicates a built-in library")
        if name.casefold() in seen:
            _error(f"{field}.name", "duplicates another custom library")
        seen.add(name.casefold())
        content_type = library.get("content_type")
        if not isinstance(content_type, str) or content_type not in _CONTENT_TYPES:
            _error(f"{field}.content_type", "is not a supported content type")
        applications = library.get("applications")
        if not isinstance(applications, list) or not applications:
            _error(f"{field}.applications", "select at least one application")
        normalized_apps: list[str] = []
        for app_index, app in enumerate(applications):
            if not isinstance(app, str) or app not in _CUSTOM_APPS:
                _error(f"{field}.applications[{app_index}]", "is not a supported application")
            if app not in normalized_apps:
                normalized_apps.append(app)
        if name.casefold() == "anime":
            required = {
                "movies": {"radarr"},
                "series": {"sonarr"},
                "both": {"radarr", "sonarr"},
            }
            if content_type not in required:
                _error(f"{field}.content_type", "Anime must be movies, series, or both")
            if set(normalized_apps) != required[content_type]:
                expected = " and ".join(sorted(required[content_type]))
                _error(f"{field}.applications", f"Anime {content_type} must use {expected}")
        result.append({"name": name, "content_type": content_type, "applications": normalized_apps})
    return result


def _normalize_service_account(value: Any) -> dict[str, str]:
    if value is None:
        return {"username": "media", "credential_mode": "generate"}
    account = _as_mapping(value, field="storage.service_account")
    if set(account) - {"username", "credential_mode"}:
        _error(
            "storage.service_account",
            "only username and credential_mode are accepted; secrets are submitted separately",
        )
    username = account.get("username", "media")
    if not isinstance(username, str) or not _USERNAME_RE.fullmatch(username):
        _error(
            "storage.service_account.username",
            "must be a lower-case Linux service-account name",
        )
    credential_mode = account.get("credential_mode", "generate")
    if credential_mode not in {"generate", "provide_separately"}:
        _error(
            "storage.service_account.credential_mode",
            "must be generate or provide_separately",
        )
    return {"username": username, "credential_mode": credential_mode}


def _normalize_intake_tests(value: Any, *, mode: str) -> dict[str, bool]:
    defaults = {
        "identity": True,
        "full_surface_read": True,
        "smart_short": False,
        "smart_extended": False,
        "destructive_write_read": False,
    }
    if value is None:
        return defaults
    tests = _as_mapping(value, field="storage.intake_tests")
    unknown = sorted(set(tests) - set(INTAKE_TESTS))
    if unknown:
        _error("storage.intake_tests", f"unknown tests: {', '.join(unknown)}")
    normalized = {
        name: _as_bool(tests.get(name, default), field=f"storage.intake_tests.{name}")
        for name, default in defaults.items()
    }
    if mode in GUIDED_MODES and normalized["destructive_write_read"]:
        _error(
            "storage.intake_tests.destructive_write_read",
            "destructive write/read testing is available only in Advanced mode",
        )
    return normalized


def _normalize_downloads(value: Any) -> dict[str, bool]:
    defaults = {"torrents": True, "usenet": True}
    if value is None:
        return defaults
    downloads = _as_mapping(value, field="storage.downloads")
    unknown = sorted(set(downloads) - set(defaults))
    if unknown:
        _error("storage.downloads", f"unknown download types: {', '.join(unknown)}")
    return {
        name: _as_bool(downloads.get(name, default), field=f"storage.downloads.{name}")
        for name, default in defaults.items()
    }


def _filesystem_decision(portable_systems: Sequence[str]) -> dict[str, Any]:
    if "windows" in portable_systems:
        return {
            "format_mode": "quick",
            "partition_table": "gpt",
            "alignment_bytes": 1_048_576,
            "filesystem": "ntfs",
            "allocation_unit_bytes": 4096,
            "linux_driver": "ntfs3",
            "mount_options": ["windows_names", "noatime"],
            "trim": {
                "mode": "conditional",
                "condition": (
                    "enable only when the complete USB/storage path reports discard support"
                ),
            },
            "reason": "Windows portability was selected",
        }
    if portable_systems == ["linux"]:
        return {
            "format_mode": "quick",
            "partition_table": "gpt",
            "alignment_bytes": 1_048_576,
            "filesystem": "ext4",
            "allocation_unit_bytes": 4096,
            "linux_driver": "ext4",
            "mount_options": ["noatime"],
            "trim": {"mode": "conditional", "condition": "enable when discard is supported"},
            "reason": "Only Linux portability was selected",
        }
    return {
        "format_mode": "quick",
        "partition_table": "gpt",
        "alignment_bytes": 1_048_576,
        "filesystem": "exfat",
        "allocation_unit_bytes": 131_072,
        "linux_driver": "exfat",
        "mount_options": ["noatime"],
        "trim": {"mode": "conditional", "condition": "enable when discard is supported"},
        "reason": "Cross-platform Windows/macOS portability was selected",
    }


def _advanced_filesystem_decision(
    value: Any,
    *,
    mode: str,
    portable_systems: Sequence[str],
) -> dict[str, Any]:
    defaults = _filesystem_decision(portable_systems)
    if value is None:
        return defaults
    if mode in GUIDED_MODES:
        _error("storage.format_options", "custom disk format settings require Advanced mode")
    options = _as_mapping(value, field="storage.format_options")
    allowed = {
        "filesystem",
        "partition_table",
        "alignment_bytes",
        "allocation_unit_bytes",
        "noatime",
        "trim_mode",
    }
    unknown = sorted(set(options) - allowed)
    if unknown:
        _error("storage.format_options", f"unknown fields: {', '.join(unknown)}")

    filesystem = options.get("filesystem", defaults["filesystem"])
    if filesystem not in {"ext4", "xfs", "btrfs", "ntfs", "exfat"}:
        _error("storage.format_options.filesystem", "is not a supported filesystem")
    partition_table = options.get("partition_table", defaults["partition_table"])
    if partition_table not in {"gpt", "mbr"}:
        _error("storage.format_options.partition_table", "must be gpt or mbr")
    alignment_bytes = options.get("alignment_bytes", defaults["alignment_bytes"])
    if isinstance(alignment_bytes, bool) or alignment_bytes not in {1_048_576, 4_194_304}:
        _error("storage.format_options.alignment_bytes", "must be 1 MiB or 4 MiB")
    allocation_unit_bytes = options.get("allocation_unit_bytes", defaults["allocation_unit_bytes"])
    if isinstance(allocation_unit_bytes, bool) or allocation_unit_bytes not in {
        4096,
        16_384,
        65_536,
        131_072,
    }:
        _error(
            "storage.format_options.allocation_unit_bytes",
            "must be 4 KiB, 16 KiB, 64 KiB, or 128 KiB",
        )
    noatime = _as_bool(options.get("noatime", True), field="storage.format_options.noatime")
    trim_mode = options.get("trim_mode", "conditional")
    trim_conditions = {
        "conditional": "enable when the complete storage path reports discard support",
        "periodic": "run scheduled fstrim only when discard is supported",
        "continuous": "mount with continuous discard when supported",
        "disabled": "do not issue discard commands",
    }
    if trim_mode not in trim_conditions:
        _error(
            "storage.format_options.trim_mode",
            "must be conditional, periodic, continuous, or disabled",
        )

    mount_options = ["windows_names"] if filesystem == "ntfs" else []
    if noatime:
        mount_options.append("noatime")
    return {
        "format_mode": "quick",
        "partition_table": partition_table,
        "alignment_bytes": alignment_bytes,
        "filesystem": filesystem,
        "allocation_unit_bytes": allocation_unit_bytes,
        "linux_driver": {
            "ext4": "ext4",
            "xfs": "xfs",
            "btrfs": "btrfs",
            "ntfs": "ntfs3",
            "exfat": "exfat",
        }[filesystem],
        "mount_options": mount_options,
        "trim": {"mode": trim_mode, "condition": trim_conditions[trim_mode]},
        "reason": "Advanced disk format settings were selected",
    }


def normalize_storage_answers(
    value: Any,
    *,
    mode: str,
    snapshot_payload: Mapping[str, Any],
) -> dict[str, Any]:
    storage = _as_mapping(value, field="storage")
    allowed = {
        "selected_device_ids",
        "topology",
        "purpose",
        "preserve_data",
        "portable_systems",
        "snapshots",
        "encryption",
        "libraries",
        "custom_libraries",
        "service_account",
        "intake_tests",
        "downloads",
        "format_options",
        "mergerfs",
        "advanced_usb_acknowledgement",
        "layout_options",
    }
    unknown = sorted(set(storage) - allowed)
    if unknown:
        _error("storage", f"unknown fields: {', '.join(unknown)}")
    selected_ids = _normalize_selected_ids(storage.get("selected_device_ids"))
    devices = select_devices(snapshot_payload, selected_ids)
    for index, device in enumerate(devices):
        if device["read_only"] is True:
            _error(
                f"storage.selected_device_ids[{index}]",
                "drive is read-only; this workflow cannot guarantee a no-write import/share, so "
                "the device cannot be selected",
            )
    topology = storage.get("topology", "individual")
    if not isinstance(topology, str) or topology not in ALL_TOPOLOGIES:
        _error("storage.topology", "is not a supported storage layout")
    if mode in GUIDED_MODES and topology not in GUIDED_TOPOLOGIES:
        _error(
            "storage.topology",
            "ZFS, SnapRAID, and RAID are available only in Advanced mode",
        )

    usb_devices = [device for device in devices if str(device["transport"]).lower() == "usb"]
    acknowledgement = storage.get("advanced_usb_acknowledgement")
    if usb_devices and topology in ARRAY_TOPOLOGIES:
        if mode in GUIDED_MODES:
            _error("storage.topology", "USB drives cannot join an array in Guided mode")
        if acknowledgement != REQUIRED_CONSENT_PHRASE:
            _error(
                "storage.advanced_usb_acknowledgement",
                "type I AGREE to accept USB disconnect, reorder, and recovery risks",
            )

    purpose = storage.get("purpose", "media")
    if purpose not in {"media", "downloads", "archive", "general", "block"}:
        _error("storage.purpose", "is not supported")
    preserve_data = _as_bool(storage.get("preserve_data", False), field="storage.preserve_data")
    if topology == "import" and not preserve_data:
        _error("storage.preserve_data", "Import requires preserving the existing filesystem")
    if topology == "test" and not preserve_data:
        _error("storage.preserve_data", "Test-only intake must preserve existing data")
    if topology in {"individual", "cache", "block", "import"} and len(devices) != 1:
        _error("storage.selected_device_ids", f"{topology} requires exactly one drive")
    snapshots = _as_bool(storage.get("snapshots", False), field="storage.snapshots")
    encryption = storage.get("encryption", "none")
    if encryption not in {"none", "luks2", "bitlocker"}:
        _error("storage.encryption", "must be none, luks2, or bitlocker")
    if mode in GUIDED_MODES and snapshots:
        _error("storage.snapshots", "snapshots require Advanced mode")
    if mode in GUIDED_MODES and encryption != "none":
        _error("storage.encryption", "encryption requires Advanced mode")
    if topology == "test" and (snapshots or encryption != "none"):
        _error("storage.topology", "Test-only intake cannot create snapshots or encryption")

    geometry_operations: list[str] = []
    if not preserve_data:
        geometry_operations.append("formatting")
    if topology in DESTRUCTIVE_LAYOUT_TOPOLOGIES or topology == "block":
        geometry_operations.append(f"{topology} layout creation")
    if encryption != "none":
        geometry_operations.append("encrypted storage creation")
    if geometry_operations:
        _require_sector_geometry(devices, operation=" and ".join(geometry_operations))

    portability = _normalize_portability(storage.get("portable_systems"))
    warnings: list[dict[str, str]] = []
    if usb_devices and topology == "cache":
        warnings.append(
            {
                "code": "usb_cache_disconnect_risk",
                "message": "A USB disconnect can interrupt downloads and corrupt active work.",
            }
        )
    if usb_devices and topology == "block":
        warnings.append(
            {
                "code": "usb_block_disconnect_risk",
                "message": "A USB disconnect can abruptly remove block storage from its consumer.",
            }
        )
    if usb_devices and topology in ARRAY_TOPOLOGIES:
        warnings.append(
            {
                "code": "advanced_usb_array_risk",
                "message": (
                    "USB identity, reset, disconnect, and bridge behavior can make an array "
                    "unavailable or unsafe to recover."
                ),
            }
        )
    for device in devices:
        signature_scan = device["signature_scan"]
        if signature_scan["status"] != "complete":
            reason = signature_scan.get("reason") or "No complete signature scan is available."
            warnings.append(
                {
                    "code": f"signature_scan_{signature_scan['status']}",
                    "device_id": str(device["id"]),
                    "message": f"Existing data on this drive remains unknown. {reason}",
                }
            )

    format_decision = _advanced_filesystem_decision(
        storage.get("format_options"), mode=mode, portable_systems=portability
    )
    discard_evidence = [
        {
            "device_id": device["id"],
            "granularity_bytes": device["discard"]["granularity_bytes"],
            "max_bytes": device["discard"]["max_bytes"],
            "supported": isinstance(device["discard"]["granularity_bytes"], int)
            and int(device["discard"]["granularity_bytes"]) > 0
            and isinstance(device["discard"]["max_bytes"], int)
            and int(device["discard"]["max_bytes"]) > 0,
        }
        for device in devices
    ]
    trim_mode = str(format_decision["trim"]["mode"])
    trim_supported = bool(discard_evidence) and all(
        item["supported"] is True for item in discard_evidence
    )
    format_decision["trim"] = {
        **format_decision["trim"],
        "enabled": trim_mode != "disabled" and trim_supported,
        "path_evidence": discard_evidence,
        "status": "supported" if trim_supported else "not_supported_or_not_reported",
    }
    if trim_mode != "disabled" and not trim_supported:
        warnings.append(
            {
                "code": "trim_path_not_supported",
                "message": (
                    "TRIM is disabled because every selected storage path did not report "
                    "discard support."
                ),
            }
        )
    if format_decision["partition_table"] == "mbr" and any(
        int(device["capacity_bytes"] or 0) > 2_199_023_255_552 for device in devices
    ):
        _error(
            "storage.format_options.partition_table",
            "MBR cannot safely address a selected drive larger than 2 TiB; choose GPT",
        )

    normalized = {
        "selected_device_ids": selected_ids,
        "topology": topology,
        "purpose": purpose,
        "preserve_data": preserve_data,
        "portable_systems": portability,
        "snapshots": snapshots,
        "encryption": encryption,
        "libraries": _normalize_libraries(storage.get("libraries")),
        "custom_libraries": _normalize_custom_libraries(storage.get("custom_libraries")),
        "service_account": _normalize_service_account(storage.get("service_account")),
        "intake_tests": _normalize_intake_tests(storage.get("intake_tests"), mode=mode),
        "downloads": _normalize_downloads(storage.get("downloads")),
        "format_decision": format_decision,
        "warnings": warnings,
    }
    if topology == "mergerfs":
        if storage.get("mergerfs") is None:
            _error(
                "storage.mergerfs",
                "choose an existing combined storage instance or create a new one",
            )
        normalized["mergerfs"] = _normalize_mergerfs(storage["mergerfs"])
    elif storage.get("mergerfs") is not None:
        _error("storage.mergerfs", "is only valid for combined storage")
    if topology in ARRAY_TOPOLOGIES:
        if storage.get("layout_options") is not None:
            try:
                normalized["layout_options"] = normalize_layout(
                    topology, storage["layout_options"], selected_ids
                )
            except LayoutError as exc:
                _error(exc.field, str(exc))
    elif storage.get("layout_options") is not None:
        _error("storage.layout_options", "is only valid for ZFS, Linux RAID, or SnapRAID")
    if storage.get("format_options") is not None:
        normalized["format_options"] = {
            "filesystem": format_decision["filesystem"],
            "partition_table": format_decision["partition_table"],
            "alignment_bytes": format_decision["alignment_bytes"],
            "allocation_unit_bytes": format_decision["allocation_unit_bytes"],
            "noatime": "noatime" in format_decision["mount_options"],
            "trim_mode": format_decision["trim"]["mode"],
        }
    if usb_devices and topology in ARRAY_TOPOLOGIES:
        normalized["advanced_usb_acknowledgement"] = REQUIRED_CONSENT_PHRASE
    return normalized


def _library_documents(storage: Mapping[str, Any]) -> list[dict[str, Any]]:
    standard_apps = {
        "Movies": ["radarr"],
        "TV": ["sonarr"],
        "Music": ["lidarr"],
        "Photos": ["immich"],
        "Books": ["readarr"],
        "Audiobooks": ["readarr"],
    }
    media_path = str(storage["layout"]["media_path"])
    libraries = [
        {
            "name": name,
            "content_type": name.casefold(),
            "applications": standard_apps[name],
            "path": f"{media_path}/{name}",
        }
        for name in storage["libraries"]
    ]
    for custom in storage["custom_libraries"]:
        libraries.append({**custom, "path": f"{media_path}/{custom['name']}"})
    return libraries


def build_storage_plan(
    storage: Mapping[str, Any],
    *,
    layout: Mapping[str, str],
    snapshot_id: str,
    snapshot_sha256: str,
    snapshot_payload: Mapping[str, Any],
) -> dict[str, Any]:
    selected = select_devices(snapshot_payload, storage["selected_device_ids"])
    device_binding_hash = document_hash(selected)
    intake_tests = dict(storage["intake_tests"])
    destructive_test = intake_tests["destructive_write_read"]
    format_decision = dict(storage["format_decision"])
    actions: list[dict[str, Any]] = []
    test_action_types = {
        "identity": "drive.identity.verify",
        "full_surface_read": "drive.surface.read",
        "smart_short": "drive.smart.short",
        "smart_extended": "drive.smart.extended",
        "destructive_write_read": "drive.write_read.destructive",
    }
    for device in selected:
        actions.extend(
            {
                "action_id": f"test:{test_name}:{device['id']}",
                "type": action_type,
                "device_id": device["id"],
                "destructive": test_name == "destructive_write_read",
            }
            for test_name, action_type in test_action_types.items()
            if intake_tests[test_name]
        )
    # ZFS, Linux MD, and mixed component pools create their filesystem above
    # the raw members. Formatting each member first would be wasteful and wrong.
    format_members = storage["topology"] not in {"zfs", "raid", "mixed", "test"}
    if not storage["preserve_data"] and format_members:
        for device in selected:
            actions.extend(
                [
                    {
                        "action_id": f"partition:{device['id']}",
                        "type": "disk.partition_table.create",
                        "device_id": device["id"],
                        "table": format_decision["partition_table"],
                        "alignment_bytes": format_decision["alignment_bytes"],
                        "destructive": True,
                    },
                    {
                        "action_id": f"filesystem:{device['id']}",
                        "type": "filesystem.create",
                        "device_id": device["id"],
                        "filesystem": format_decision["filesystem"],
                        "allocation_unit_bytes": format_decision["allocation_unit_bytes"],
                        "format_mode": "quick",
                        "destructive": True,
                    },
                ]
            )
    layout_is_destructive = (
        storage["topology"] in DESTRUCTIVE_LAYOUT_TOPOLOGIES or storage["encryption"] != "none"
    )
    if storage["topology"] != "test":
        actions.append(
            {
                "action_id": "storage-layout",
                "type": "storage.layout.ensure",
                "topology": storage["topology"],
                "device_ids": list(storage["selected_device_ids"]),
                "purpose": storage["purpose"],
                **(
                    {
                        "mergerfs": dict(storage["mergerfs"]),
                        "requires_live_instance_revalidation": storage["mergerfs"]["mode"]
                        == "existing",
                    }
                    if storage["topology"] == "mergerfs"
                    else {}
                ),
                **(
                    {"layout_options": dict(storage["layout_options"])}
                    if storage["topology"] in ARRAY_TOPOLOGIES and "layout_options" in storage
                    else {}
                ),
                # The current schema expresses creation, not a verified read-only import.
                # Array metadata and a new encryption layer can overwrite existing media.
                "destructive": layout_is_destructive,
            }
        )

    storage_with_layout = {**storage, "layout": dict(layout)}
    libraries = [] if storage["topology"] == "test" else _library_documents(storage_with_layout)
    folders = [library["path"] for library in libraries]
    downloads_path = layout["downloads_path"]
    if storage["topology"] != "test" and storage["downloads"]["torrents"]:
        folders.extend(
            [
                f"{downloads_path}/torrents/incomplete",
                f"{downloads_path}/torrents/complete",
            ]
        )
    if storage["topology"] != "test" and storage["downloads"]["usenet"]:
        folders.extend(
            [
                f"{downloads_path}/usenet/incomplete",
                f"{downloads_path}/usenet/complete",
            ]
        )
    destructive = any(action["destructive"] for action in actions)
    risk_messages: list[str] = []
    if not storage["preserve_data"]:
        risk_messages.append(
            "The listed drives will be repartitioned and formatted. Existing data will be lost."
        )
    if destructive_test:
        risk_messages.append(
            "The destructive write/read test will overwrite data on the listed drives."
        )
    if layout_is_destructive:
        risk_messages.append(
            f"Creating the {storage['topology']} layout can overwrite storage metadata or data "
            "on the listed drives."
        )
    return {
        "snapshot_binding": {
            "snapshot_id": snapshot_id,
            "snapshot_sha256": snapshot_sha256,
            "device_binding_sha256": device_binding_hash,
            "selected_device_ids": list(storage["selected_device_ids"]),
        },
        "selected_devices": selected,
        "topology": storage["topology"],
        **({"mergerfs": dict(storage["mergerfs"])} if storage["topology"] == "mergerfs" else {}),
        **(
            {"layout_options": dict(storage["layout_options"])}
            if storage["topology"] in ARRAY_TOPOLOGIES and "layout_options" in storage
            else {}
        ),
        "format": format_decision,
        "snapshots": storage["snapshots"],
        "encryption": storage["encryption"],
        "service_account": storage["service_account"],
        "intake_tests": intake_tests,
        "file_access": {
            "protocol": "smb",
            "acl_model": "posix_acl",
            "client_presentation": "windows_style_smb_permissions",
            "permissions": {
                "administrators": "full_control",
                "media_applications": "modify",
                "media_users": "read_execute",
                "anonymous": "none",
            },
            "advanced_protocols": ["nfs", "iscsi"],
        },
        "downloads": {
            "torrents": {
                "enabled": storage["downloads"]["torrents"],
                "incomplete": f"{downloads_path}/torrents/incomplete",
                "complete": f"{downloads_path}/torrents/complete",
                "cache_import": (
                    "copy_then_retain_until_seeding_completes"
                    if storage["topology"] == "cache"
                    else "move_or_hardlink_when_same_filesystem"
                ),
            },
            "usenet": {
                "enabled": storage["downloads"]["usenet"],
                "incomplete": f"{downloads_path}/usenet/incomplete",
                "complete": f"{downloads_path}/usenet/complete",
                "cache_import": (
                    "verify_then_move_to_media"
                    if storage["topology"] == "cache"
                    else "move_when_same_filesystem"
                ),
            },
            "hardlinks": "same_filesystem_only",
        },
        "libraries": libraries,
        "folders": folders,
        "warnings": list(storage["warnings"]),
        "actions": actions,
        "risk": {
            "destructive": destructive,
            "heading": "ARE YOU SURE?" if destructive else None,
            "message": " ".join(risk_messages)
            if risk_messages
            else "No destructive disk action is planned.",
            "required_phrase": REQUIRED_CONSENT_PHRASE if destructive else None,
            "approval_required": destructive,
        },
    }
