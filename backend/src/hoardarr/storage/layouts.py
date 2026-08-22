from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


class LayoutError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(message)


@dataclass(frozen=True)
class CommandSpec:
    argv: tuple[str, ...]
    timeout_seconds: int
    phase: str
    cancellable_before: bool = True


_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_ABSOLUTE_DEVICE = re.compile(r"^/dev/(?:disk/by-id/[A-Za-z0-9._:+-]+|mapper/[A-Za-z0-9._+-]+)$")


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LayoutError(field, "must be an object")
    return value


def _name(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise LayoutError(
            field, "must start with a lower-case letter and contain only letters, numbers, _ or -"
        )
    return value


def _path(value: Any, field: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise LayoutError(field, "must be an absolute managed path")
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts or path == PurePosixPath("/"):
        raise LayoutError(field, "must be an absolute managed path")
    managed_roots = tuple(map(PurePosixPath, ("/mnt/hoardarr", "/srv/hoardarr", "/data")))
    if not any(path == root or root in path.parents for root in managed_roots):
        raise LayoutError(field, "must be below a Hoardarr-managed root")
    return str(path)


def _ids(value: Any, field: str, *, minimum: int = 1) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        raise LayoutError(field, f"requires at least {minimum} drive(s)")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise LayoutError(f"{field}[{index}]", "must be a stable drive identity")
        if item in result:
            raise LayoutError(field, "must not contain duplicate drives")
        result.append(item)
    return result


def normalize_layout(topology: str, value: Any, selected_ids: Sequence[str]) -> dict[str, Any]:
    options = _mapping(value, "storage.layout_options")
    selected = set(selected_ids)
    if topology == "mixed":
        allowed = {"name", "components", "mountpoint", "create_policy", "search_policy"}
        unknown = set(options) - allowed
        if unknown:
            raise LayoutError(
                "storage.layout_options",
                f"unknown mixed-layout fields: {', '.join(sorted(unknown))}",
            )
        raw_components = options.get("components")
        if not isinstance(raw_components, list) or len(raw_components) < 2:
            raise LayoutError(
                "storage.layout_options.components", "requires at least two component pools"
            )
        components: list[dict[str, Any]] = []
        used: set[str] = set()
        names: set[str] = set()
        mountpoints: set[str] = set()
        for index, raw_component in enumerate(raw_components):
            component = _mapping(raw_component, f"storage.layout_options.components[{index}]")
            if set(component) != {"topology", "device_ids", "options"}:
                raise LayoutError(
                    f"storage.layout_options.components[{index}]",
                    "must contain only topology, device_ids, and options",
                )
            component_topology = component.get("topology")
            if component_topology not in {"zfs", "raid"}:
                raise LayoutError(
                    f"storage.layout_options.components[{index}].topology",
                    "must be zfs or raid",
                )
            component_ids = _ids(
                component.get("device_ids"),
                f"storage.layout_options.components[{index}].device_ids",
            )
            if used.intersection(component_ids):
                raise LayoutError(
                    "storage.layout_options.components",
                    "a drive can appear in only one component pool",
                )
            normalized = normalize_layout(
                str(component_topology), component.get("options"), component_ids
            )
            if normalized["name"] in names:
                raise LayoutError(
                    "storage.layout_options.components", "component names must be unique"
                )
            if normalized["mountpoint"] in mountpoints:
                raise LayoutError(
                    "storage.layout_options.components", "component mountpoints must be unique"
                )
            used.update(component_ids)
            names.add(str(normalized["name"]))
            mountpoints.add(str(normalized["mountpoint"]))
            components.append(
                {
                    "topology": component_topology,
                    "device_ids": component_ids,
                    "options": normalized,
                }
            )
        if used != selected:
            raise LayoutError(
                "storage.layout_options.components",
                "component pools must use every selected drive exactly once",
            )
        mountpoint = _path(options.get("mountpoint"), "storage.layout_options.mountpoint")
        if mountpoint in mountpoints or any(
            PurePosixPath(mountpoint) in PurePosixPath(item).parents
            or PurePosixPath(item) in PurePosixPath(mountpoint).parents
            for item in mountpoints
        ):
            raise LayoutError(
                "storage.layout_options.mountpoint",
                "combined and component mountpoints must not overlap",
            )
        create_policy = options.get("create_policy", "mfs")
        search_policy = options.get("search_policy", "ff")
        if create_policy not in {"mfs", "epmfs"}:
            raise LayoutError("storage.layout_options.create_policy", "must be mfs or epmfs")
        if search_policy not in {"ff", "all"}:
            raise LayoutError("storage.layout_options.search_policy", "must be ff or all")
        return {
            "name": _name(options.get("name"), "storage.layout_options.name"),
            "components": components,
            "mountpoint": mountpoint,
            "create_policy": create_policy,
            "search_policy": search_policy,
        }
    if topology == "zfs":
        allowed = {
            "name",
            "vdevs",
            "ashift",
            "recordsize",
            "compression",
            "mountpoint",
            "scrub_schedule",
            "snapshots",
            "special",
            "cache",
            "log",
        }
        unknown = set(options) - allowed
        if unknown:
            raise LayoutError(
                "storage.layout_options", f"unknown ZFS fields: {', '.join(sorted(unknown))}"
            )
        vdevs_value = options.get("vdevs")
        if not isinstance(vdevs_value, list) or not vdevs_value:
            raise LayoutError("storage.layout_options.vdevs", "requires at least one vdev")
        vdevs: list[dict[str, Any]] = []
        used: set[str] = set()
        minima = {"mirror": 2, "raidz1": 3, "raidz2": 4, "raidz3": 5}
        for index, raw in enumerate(vdevs_value):
            item = _mapping(raw, f"storage.layout_options.vdevs[{index}]")
            kind = item.get("type")
            if kind not in minima:
                raise LayoutError(
                    f"storage.layout_options.vdevs[{index}].type",
                    "must be mirror, raidz1, raidz2, or raidz3",
                )
            members = _ids(
                item.get("device_ids"),
                f"storage.layout_options.vdevs[{index}].device_ids",
                minimum=minima[str(kind)],
            )
            if used.intersection(members):
                raise LayoutError(
                    "storage.layout_options.vdevs", "a drive can appear in only one vdev"
                )
            used.update(members)
            vdevs.append(
                {
                    "type": kind,
                    "device_ids": members,
                    "tolerated_failures": {"mirror": 1, "raidz1": 1, "raidz2": 2, "raidz3": 3}[
                        str(kind)
                    ],
                }
            )
        auxiliaries: dict[str, list[str]] = {}
        for role in ("special", "cache", "log"):
            raw_members = options.get(role, [])
            members = (
                [] if raw_members == [] else _ids(raw_members, f"storage.layout_options.{role}")
            )
            if used.intersection(members):
                raise LayoutError(
                    f"storage.layout_options.{role}", "a drive cannot have more than one pool role"
                )
            used.update(members)
            auxiliaries[role] = members
        if auxiliaries["special"] and len(auxiliaries["special"]) < 2:
            raise LayoutError(
                "storage.layout_options.special",
                "special metadata devices require at least two drives for a mirror",
            )
        if used != selected:
            missing = sorted(selected - used)
            extra = sorted(used - selected)
            raise LayoutError(
                "storage.layout_options",
                "roles must use every selected drive exactly once "
                f"(missing={missing}, extra={extra})",
            )
        ashift = options.get("ashift", 12)
        if ashift not in {9, 12, 13, 14}:
            raise LayoutError("storage.layout_options.ashift", "must be 9, 12, 13, or 14")
        recordsize = options.get("recordsize", "1M")
        if recordsize not in {
            "16K",
            "32K",
            "64K",
            "128K",
            "256K",
            "512K",
            "1M",
            "2M",
            "4M",
            "8M",
            "16M",
        }:
            raise LayoutError("storage.layout_options.recordsize", "is not supported")
        compression = options.get("compression", "lz4")
        if compression not in {"off", "lz4", "zstd", "zstd-fast"}:
            raise LayoutError("storage.layout_options.compression", "is not supported")
        snapshots = options.get("snapshots", {"enabled": False, "retention": 0})
        if (
            not isinstance(snapshots, Mapping)
            or set(snapshots) - {"enabled", "retention"}
            or not isinstance(snapshots.get("enabled", False), bool)
            or not isinstance(snapshots.get("retention", 0), int)
            or not 0 <= int(snapshots.get("retention", 0)) <= 4096
        ):
            raise LayoutError(
                "storage.layout_options.snapshots",
                "must contain enabled and a retention count from 0 to 4096",
            )
        scrub_schedule = options.get("scrub_schedule", "monthly")
        if scrub_schedule not in {"disabled", "weekly", "monthly"}:
            raise LayoutError(
                "storage.layout_options.scrub_schedule", "must be disabled, weekly, or monthly"
            )
        return {
            "name": _name(options.get("name"), "storage.layout_options.name"),
            "vdevs": vdevs,
            "ashift": ashift,
            "recordsize": recordsize,
            "compression": compression,
            "mountpoint": _path(options.get("mountpoint"), "storage.layout_options.mountpoint"),
            "scrub_schedule": scrub_schedule,
            "snapshots": dict(snapshots),
            **auxiliaries,
        }
    if topology == "raid":
        allowed = {
            "name",
            "level",
            "device_ids",
            "filesystem",
            "mountpoint",
            "chunk_kib",
            "metadata",
        }
        if set(options) - allowed:
            raise LayoutError("storage.layout_options", "contains unknown Linux MD fields")
        level = options.get("level")
        minima = {"raid1": 2, "raid5": 3, "raid6": 4, "raid10": 4}
        if level not in minima:
            raise LayoutError(
                "storage.layout_options.level", "must be raid1, raid5, raid6, or raid10"
            )
        members = _ids(
            options.get("device_ids"),
            "storage.layout_options.device_ids",
            minimum=minima[str(level)],
        )
        if set(members) != selected:
            raise LayoutError(
                "storage.layout_options.device_ids", "must use every selected drive exactly once"
            )
        if level == "raid10" and len(members) % 2:
            raise LayoutError(
                "storage.layout_options.device_ids", "RAID10 requires an even drive count"
            )
        filesystem = options.get("filesystem", "xfs")
        if filesystem not in {"ext4", "xfs", "btrfs"}:
            raise LayoutError("storage.layout_options.filesystem", "must be ext4, XFS, or Btrfs")
        chunk = options.get("chunk_kib", 512)
        if not isinstance(chunk, int) or chunk < 4 or chunk > 16384 or chunk & (chunk - 1):
            raise LayoutError(
                "storage.layout_options.chunk_kib", "must be a power of two from 4 through 16384"
            )
        return {
            "name": _name(options.get("name"), "storage.layout_options.name"),
            "level": level,
            "device_ids": members,
            "filesystem": filesystem,
            "mountpoint": _path(options.get("mountpoint"), "storage.layout_options.mountpoint"),
            "chunk_kib": chunk,
            "metadata": options.get("metadata", "1.2"),
        }
    if topology == "snapraid":
        allowed = {
            "name",
            "data",
            "parity",
            "content",
            "mountpoint",
            "sync_schedule",
            "scrub_schedule",
            "scrub_percent",
        }
        if set(options) - allowed:
            raise LayoutError("storage.layout_options", "contains unknown SnapRAID fields")
        data = _ids(options.get("data"), "storage.layout_options.data")
        parity = _ids(options.get("parity"), "storage.layout_options.parity")
        if set(data).intersection(parity) or set(data + parity) != selected:
            raise LayoutError(
                "storage.layout_options",
                "data and parity roles must use every selected drive exactly once",
            )
        if len(parity) > 6 or len(parity) >= len(data) + 1:
            raise LayoutError(
                "storage.layout_options.parity",
                "parity count must be between 1 and 6 and not exceed data count",
            )
        normalized_name = _name(options.get("name"), "storage.layout_options.name")
        content = options.get("content") or [
            f"/var/lib/hoardarr/snapraid/{normalized_name}.content"
        ]
        if (
            not isinstance(content, list)
            or not content
            or not all(
                isinstance(item, str)
                and item.startswith("/var/lib/hoardarr/snapraid/")
                and ".." not in PurePosixPath(item).parts
                for item in content
            )
        ):
            raise LayoutError(
                "storage.layout_options.content", "must contain managed content-file paths"
            )
        percent = options.get("scrub_percent", 12)
        if not isinstance(percent, int) or not 1 <= percent <= 100:
            raise LayoutError("storage.layout_options.scrub_percent", "must be from 1 through 100")
        sync_schedule = options.get("sync_schedule", "daily")
        scrub_schedule = options.get("scrub_schedule", "weekly")
        if sync_schedule not in {"disabled", "daily", "weekly"}:
            raise LayoutError(
                "storage.layout_options.sync_schedule", "must be disabled, daily, or weekly"
            )
        if scrub_schedule not in {"disabled", "weekly", "monthly"}:
            raise LayoutError(
                "storage.layout_options.scrub_schedule", "must be disabled, weekly, or monthly"
            )
        return {
            "name": _name(options.get("name"), "storage.layout_options.name"),
            "data": data,
            "parity": parity,
            "content": list(content),
            "mountpoint": _path(options.get("mountpoint"), "storage.layout_options.mountpoint"),
            "sync_schedule": sync_schedule,
            "scrub_schedule": scrub_schedule,
            "scrub_percent": percent,
            "parity_state": "not_synced",
        }
    raise LayoutError("storage.topology", "does not accept advanced layout options")


def _device_path(identity: str, device_paths: Mapping[str, str]) -> str:
    value = device_paths.get(identity)
    if not isinstance(value, str) or not _ABSOLUTE_DEVICE.fullmatch(value):
        raise LayoutError(
            "device_paths",
            "every drive must resolve to a stable /dev/disk/by-id or /dev/mapper path",
        )
    return value


def layout_commands(
    topology: str, options: Mapping[str, Any], device_paths: Mapping[str, str]
) -> list[CommandSpec]:
    """Return argv-only commands. The privileged executor revalidates identities before use."""
    if topology == "mixed":
        commands: list[CommandSpec] = []
        branches: list[str] = []
        for component in options["components"]:
            component_options = component["options"]
            mountpoint = str(component_options["mountpoint"])
            commands.append(
                CommandSpec(
                    ("install", "-d", "-m", "0750", mountpoint),
                    60,
                    "Preparing component mountpoint",
                )
            )
            commands.extend(
                layout_commands(str(component["topology"]), component_options, device_paths)
            )
            branches.append(mountpoint)
        target = str(options["mountpoint"])
        commands.append(
            CommandSpec(
                ("install", "-d", "-m", "0750", target),
                60,
                "Preparing combined mountpoint",
            )
        )
        merger_options = (
            f"category.create={options['create_policy']},"
            f"category.search={options['search_policy']},"
            "use_ino,cache.files=off,dropcacheonclose=true"
        )
        commands.extend(
            [
                CommandSpec(
                    ("mergerfs", "-o", merger_options, ":".join(branches), target),
                    300,
                    "Combining component pools",
                    False,
                ),
                CommandSpec(
                    ("findmnt", "--mountpoint", target),
                    60,
                    "Verifying combined storage",
                ),
            ]
        )
        return commands
    if topology == "zfs":
        argv = [
            "zpool",
            "create",
            "-f",
            "-o",
            f"ashift={options['ashift']}",
            "-O",
            f"mountpoint={options['mountpoint']}",
            "-O",
            f"recordsize={options['recordsize']}",
            "-O",
            f"compression={options['compression']}",
            str(options["name"]),
        ]
        for vdev in options["vdevs"]:
            argv.append(
                {"mirror": "mirror", "raidz1": "raidz1", "raidz2": "raidz2", "raidz3": "raidz3"}[
                    vdev["type"]
                ]
            )
            argv.extend(_device_path(item, device_paths) for item in vdev["device_ids"])
        for role in ("special", "cache", "log"):
            members = options.get(role, [])
            if members:
                argv.append(role)
                if role == "special" or (role == "log" and len(members) > 1):
                    argv.append("mirror")
                argv.extend(_device_path(item, device_paths) for item in members)
        commands = [CommandSpec(tuple(argv), 3600, "Creating ZFS pool", False)]
        if options.get("snapshots", {}).get("enabled"):
            commands.append(
                CommandSpec(
                    ("zfs", "snapshot", f"{options['name']}@hoardarr-initial"),
                    300,
                    "Creating initial snapshot",
                )
            )
        return commands
    if topology == "raid":
        md_path = f"/dev/md/{options['name']}"
        members = [_device_path(item, device_paths) for item in options["device_ids"]]
        create = (
            "mdadm",
            "--create",
            md_path,
            "--run",
            f"--level={options['level'].removeprefix('raid')}",
            f"--raid-devices={len(members)}",
            f"--metadata={options['metadata']}",
            f"--chunk={options['chunk_kib']}",
            *members,
        )
        mkfs = {
            "ext4": ("mkfs.ext4", "-F", "-E", "lazy_itable_init=1,lazy_journal_init=1", md_path),
            "xfs": ("mkfs.xfs", "-f", "-K", md_path),
            "btrfs": ("mkfs.btrfs", "-f", "-K", md_path),
        }[options["filesystem"]]
        return [
            CommandSpec(create, 3600, "Creating Linux RAID", False),
            CommandSpec(mkfs, 3600, "Creating array filesystem", False),
            CommandSpec(("mount", md_path, str(options["mountpoint"])), 120, "Mounting Linux RAID"),
        ]
    if topology == "snapraid":
        config = f"/etc/snapraid/{options['name']}.conf"
        return [
            CommandSpec(
                ("snapraid", "-c", config, "status"), 300, "Validating SnapRAID configuration"
            ),
            CommandSpec(("snapraid", "-c", config, "sync"), 86400, "Synchronizing SnapRAID", False),
        ]
    raise LayoutError("topology", "is not executable")


def mergerfs_expand_commands(mountpoint: str, new_branches: Sequence[str]) -> list[CommandSpec]:
    target = _path(mountpoint, "mergerfs.mountpoint")
    branches = [
        _path(item, f"mergerfs.new_branches[{index}]") for index, item in enumerate(new_branches)
    ]
    if not branches or len(branches) != len(set(branches)):
        raise LayoutError("mergerfs.new_branches", "requires unique new branch paths")
    runtime = f"{target}/.mergerfs"
    return [
        CommandSpec(
            (
                "setfattr",
                "-n",
                "user.mergerfs.branches",
                "-v",
                f"+>{':'.join(branches)}",
                runtime,
            ),
            120,
            "Adding mergerFS branches",
            False,
        ),
        CommandSpec(
            ("getfattr", "-n", "user.mergerfs.branches", runtime),
            60,
            "Verifying mergerFS branches",
        ),
    ]


def snapraid_config(options: Mapping[str, Any], disk_mounts: Mapping[str, str]) -> str:
    lines: list[str] = []
    for index, identifier in enumerate(options["parity"], start=1):
        mount = _path(disk_mounts.get(identifier), f"snapraid.parity[{index - 1}]")
        keyword = "parity" if index == 1 else f"{index}-parity"
        lines.append(f"{keyword} {mount}/snapraid.parity")
    for content in options["content"]:
        value = PurePosixPath(str(content))
        if not value.is_absolute() or ".." in value.parts:
            raise LayoutError("snapraid.content", "contains an unsafe path")
        lines.append(f"content {value}")
    for index, identifier in enumerate(options["data"], start=1):
        mount = _path(disk_mounts.get(identifier), f"snapraid.data[{index - 1}]")
        lines.append(f"content {mount}/snapraid.content")
        lines.append(f"data d{index} {mount}")
    return "\n".join(lines) + "\n"


def normalize_wipe(value: Any) -> dict[str, Any]:
    item = _mapping(value, "wipe")
    method = item.get("method")
    if method not in {"quick", "hdd_overwrite", "ata_secure_erase", "nvme_sanitize"}:
        raise LayoutError(
            "wipe.method", "must be quick, hdd_overwrite, ata_secure_erase, or nvme_sanitize"
        )
    passes = item.get("passes", 1)
    if method == "hdd_overwrite" and (not isinstance(passes, int) or not 1 <= passes <= 7):
        raise LayoutError("wipe.passes", "must be from 1 through 7")
    capability = item.get("capability")
    if method in {"ata_secure_erase", "nvme_sanitize"} and capability is not True:
        raise LayoutError(
            "wipe.capability",
            "the drive and complete controller path must explicitly report support",
        )
    return {
        "method": method,
        "passes": passes if method == "hdd_overwrite" else 1,
        "capability": capability is True,
    }


def wipe_commands(plan: Mapping[str, Any], stable_path: str) -> list[CommandSpec]:
    if not _ABSOLUTE_DEVICE.fullmatch(stable_path):
        raise LayoutError("device", "secure wipe requires a stable by-id or mapper path")
    method = plan["method"]
    if method == "quick":
        return [
            CommandSpec(
                ("wipefs", "--all", "--force", stable_path),
                300,
                "Removing storage signatures",
                False,
            )
        ]
    if method == "hdd_overwrite":
        return [
            CommandSpec(
                ("shred", "--force", f"--iterations={plan['passes']}", "--zero", stable_path),
                604800,
                "Overwriting HDD",
                False,
            )
        ]
    if method == "ata_secure_erase":
        return [
            CommandSpec(
                (
                    "hdparm",
                    "--user-master",
                    "u",
                    "--security-set-pass",
                    "NULL",
                    stable_path,
                ),
                300,
                "Enabling ATA secure erase",
                False,
            ),
            CommandSpec(
                (
                    "hdparm",
                    "--user-master",
                    "u",
                    "--security-erase",
                    "NULL",
                    stable_path,
                ),
                86400,
                "Running ATA secure erase",
                False,
            ),
            CommandSpec(("hdparm", "-I", stable_path), 300, "Verifying ATA erase completion"),
        ]
    if method == "nvme_sanitize":
        return [
            CommandSpec(
                (
                    "nvme",
                    "sanitize",
                    stable_path,
                    "--sanact=start-block-erase",
                    "--wait",
                ),
                86400,
                "Running NVMe block erase",
                False,
            ),
            CommandSpec(
                ("nvme", "sanitize-log", stable_path), 300, "Verifying NVMe sanitize completion"
            ),
        ]
    raise LayoutError("wipe.method", "is not executable")


def normalize_sector_conversion(value: Any) -> dict[str, Any]:
    item = _mapping(value, "sector_conversion")
    current = item.get("current_logical_bytes")
    target = item.get("target_logical_bytes")
    if current not in {520, 528} or target not in {512, 4096}:
        raise LayoutError(
            "sector_conversion",
            "only an explicitly detected 520/528-byte format can be converted to a "
            "reported 512/4096-byte format",
        )
    if item.get("drive_support") is not True or item.get("controller_passthrough") is not True:
        raise LayoutError(
            "sector_conversion", "drive support and controller passthrough must both be verified"
        )
    return {
        "current_logical_bytes": current,
        "target_logical_bytes": target,
        "drive_support": True,
        "controller_passthrough": True,
        "advanced_only": True,
    }


def sector_conversion_commands(plan: Mapping[str, Any], stable_path: str) -> list[CommandSpec]:
    if not _ABSOLUTE_DEVICE.fullmatch(stable_path):
        raise LayoutError("device", "sector conversion requires a stable by-id or mapper path")
    return [
        CommandSpec(
            ("sg_format", "--format", f"--size={plan['target_logical_bytes']}", stable_path),
            604800,
            "Converting logical block format",
            False,
        ),
        CommandSpec(("sg_readcap", "--long", stable_path), 300, "Verifying logical block format"),
    ]
