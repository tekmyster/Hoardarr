from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import stat
import subprocess
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


class QuarantineError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


SCHEMA_VERSION = 1
POLICY_PATHS = (
    Path("/etc/udev/rules.d/60-hoardarr-storage-quarantine.rules"),
    Path("/etc/mdadm/mdadm.conf"),
    Path("/etc/lvm/lvmlocal.conf"),
    Path("/etc/multipath/conf.d/99-hoardarr-quarantine.conf"),
)
MANAGED_UDEV_RULE = Path("/etc/udev/rules.d/98-hoardarr-managed-storage.rules")
MANAGED_STORAGE_STATE = Path("/var/lib/hoardarr/storage-executor/managed-storage.json")
FSTAB_PATH = Path("/etc/fstab")
MANAGED_MEMBER_ROOT = Path("/mnt/hoardarr/disks")
_FILESYSTEM_UUID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_BLOCK_NAME_RE = re.compile(r"^[A-Za-z0-9._!+:-]{1,128}$")
_MANAGED_BEGIN_RE = re.compile(
    r"^# BEGIN HOARDARR ([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _apply_created_mode(descriptor: int, temporary: Path, mode: int) -> None:
    """Apply the caller's explicit mode without allowing umask to narrow it."""
    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, mode)
    else:  # pragma: no cover - Windows compatibility path
        os.chmod(temporary, mode)


def atomic_json(path: Path, document: dict[str, Any], *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        _apply_created_mode(descriptor, temporary, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if hasattr(os, "O_DIRECTORY"):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def validate_quarantine(marker: Path) -> dict[str, Any]:
    try:
        details = marker.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise QuarantineError(
            "quarantine_not_ready",
            "Drive quarantine has not been prepared on this host. No storage action was started.",
        ) from exc
    if not stat.S_ISREG(details.st_mode) or details.st_uid != 0 or details.st_mode & 0o022:
        raise QuarantineError(
            "quarantine_marker_unsafe",
            "The drive-quarantine attestation has unsafe ownership or permissions.",
        )
    try:
        document = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuarantineError(
            "quarantine_marker_invalid", "The drive-quarantine attestation is invalid."
        ) from exc
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != SCHEMA_VERSION
        or document.get("ready") is not True
        or not isinstance(document.get("machine_id_sha256"), str)
        or not isinstance(document.get("policies"), list)
    ):
        raise QuarantineError(
            "quarantine_marker_invalid", "The drive-quarantine attestation is invalid."
        )
    machine_id = Path("/etc/machine-id").read_bytes().strip()
    if hashlib.sha256(machine_id).hexdigest() != document["machine_id_sha256"]:
        raise QuarantineError(
            "quarantine_host_changed", "Drive quarantine was prepared for a different host."
        )
    for index, policy in enumerate(document["policies"]):
        if not isinstance(policy, dict) or set(policy) != {"path", "sha256"}:
            raise QuarantineError(
                "quarantine_marker_invalid", "The drive-quarantine attestation is invalid."
            )
        path_value = policy["path"]
        expected = policy["sha256"]
        if not isinstance(path_value, str) or not isinstance(expected, str):
            raise QuarantineError(
                "quarantine_marker_invalid", "The drive-quarantine attestation is invalid."
            )
        path = Path(path_value)
        if not path.is_absolute() or not path.is_file() or file_sha256(path) != expected:
            raise QuarantineError(
                "quarantine_policy_changed",
                f"Drive-quarantine policy {index + 1} changed after it was prepared.",
            )
    nodes_root = Path("/etc/iscsi/nodes")
    if nodes_root.exists():
        for node_file in nodes_root.glob("*/*/default"):
            try:
                settings = node_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise QuarantineError(
                    "iscsi_quarantine_unknown", "An iSCSI node policy could not be verified."
                ) from exc
            startup = [
                line.split("=", 1)[1].strip()
                for line in settings.splitlines()
                if line.strip().startswith("node.startup") and "=" in line
            ]
            if startup != ["manual"]:
                raise QuarantineError(
                    "iscsi_autoactivation_enabled",
                    "An iSCSI node is allowed to reconnect automatically.",
                )
    return document


def _command(command: list[str], *, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            command,
            check=True,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise QuarantineError(
            "quarantine_inspection_failed",
            "The host storage activation state could not be inspected safely.",
        ) from exc
    return result.stdout


def _lsblk_tree() -> list[dict[str, Any]]:
    try:
        document = json.loads(
            _command(
                [
                    "lsblk",
                    "--json",
                    "--paths",
                    "--output",
                    "NAME,TYPE,PKNAME,TRAN,MOUNTPOINTS",
                ]
            )
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuarantineError(
            "quarantine_inspection_failed", "The block-device graph was invalid."
        ) from exc
    devices = document.get("blockdevices") if isinstance(document, dict) else None
    if not isinstance(devices, list) or not all(isinstance(item, dict) for item in devices):
        raise QuarantineError("quarantine_inspection_failed", "The block-device graph was invalid.")
    return devices


def _boot_chain() -> tuple[list[str], list[str]]:
    protected_mounts = {"/", "/boot", "/boot/efi"}
    result: set[str] = set()
    md_arrays: set[str] = set()
    allowed_layers = {"disk", "part", "lvm", "crypt", "mpath", "md"}

    def visit(node: dict[str, Any], root_disk: str | None) -> bool:
        name = node.get("name")
        device_type = node.get("type")
        if not isinstance(name, str) or not isinstance(device_type, str):
            raise QuarantineError(
                "quarantine_inspection_failed", "The block-device graph was incomplete."
            )
        disk = name if device_type == "disk" else root_disk
        mountpoints = node.get("mountpoints")
        protected = isinstance(mountpoints, list) and any(
            item in protected_mounts for item in mountpoints
        )
        children = node.get("children", [])
        if not isinstance(children, list):
            raise QuarantineError(
                "quarantine_inspection_failed", "The block-device graph was incomplete."
            )
        for child in children:
            if not isinstance(child, dict):
                raise QuarantineError(
                    "quarantine_inspection_failed", "The block-device graph was incomplete."
                )
            protected = visit(child, disk) or protected
        if protected and not (device_type in allowed_layers or device_type.startswith("raid")):
            raise QuarantineError(
                "complex_boot_chain_requires_review",
                "An unsupported boot storage layer requires an explicit quarantine allowlist.",
            )
        if protected and (device_type == "md" or device_type.startswith("raid")):
            md_arrays.add(name)
        if protected and node.get("tran") in {"iscsi", "fc", "fcoe"}:
            raise QuarantineError(
                "remote_boot_chain_requires_review",
                "Boot-from-SAN requires an explicit remote-storage allowlist.",
            )
        if protected and disk:
            result.add(disk)
        return protected

    for root in _lsblk_tree():
        visit(root, None)
    if not result:
        raise QuarantineError(
            "boot_chain_ambiguous", "The physical boot-device chain could not be identified."
        )
    return sorted(result), sorted(md_arrays)


def _udev_properties(device: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _command(["udevadm", "info", "--query=property", f"--name={device}"]).splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _udev_escape(value: str) -> str:
    if not value or any(character in value for character in {'"', "\n", "\r", "\0"}):
        raise QuarantineError(
            "boot_identity_unsafe", "A boot-drive identity could not be represented safely."
        )
    return value


def _udev_policy(boot_devices: list[str]) -> str:
    identities: list[tuple[str, str]] = []
    for device in boot_devices:
        properties = _udev_properties(device)
        if properties.get("ID_WWN"):
            identities.append(("ID_WWN", _udev_escape(properties["ID_WWN"])))
        elif properties.get("ID_SERIAL"):
            identities.append(("ID_SERIAL", _udev_escape(properties["ID_SERIAL"])))
        else:
            raise QuarantineError(
                "boot_identity_unstable", "A physical boot drive has no stable udev identity."
            )
    lines = [
        "# Managed by Hoardarr. Non-boot disks remain visible but are not systemd-ready.",
        'SUBSYSTEM!="block", GOTO="hoardarr_quarantine_end"',
        'KERNEL=="sd*", GOTO="hoardarr_quarantine_inspect"',
        'KERNEL=="vd*", GOTO="hoardarr_quarantine_inspect"',
        'KERNEL=="xvd*", GOTO="hoardarr_quarantine_inspect"',
        'KERNEL=="nvme*n*", GOTO="hoardarr_quarantine_inspect"',
        'KERNEL=="mmcblk*", GOTO="hoardarr_quarantine_inspect"',
        'GOTO="hoardarr_quarantine_end"',
        'LABEL="hoardarr_quarantine_inspect"',
    ]
    lines.extend(
        f'ENV{{{field}}}=="{value}", GOTO="hoardarr_quarantine_end"' for field, value in identities
    )
    lines.extend(
        [
            'ENV{SYSTEMD_READY}="0"',
            'ENV{UDISKS_IGNORE}="1"',
            'ENV{UDISKS_AUTO}="0"',
            'LABEL="hoardarr_quarantine_end"',
            "",
        ]
    )
    return "\n".join(lines)


def _mdadm_policy(boot_arrays: list[str]) -> str:
    arrays: list[str] = []
    for array in boot_arrays:
        arrays.extend(
            line
            for line in _command(["mdadm", "--detail", "--scan", array]).splitlines()
            if line.startswith("ARRAY ")
        )
    return "\n".join(
        [
            "# Managed by Hoardarr. Only arrays explicitly present at preparation are eligible.",
            "HOMEHOST <system>",
            *arrays,
            "AUTO -all",
            "",
        ]
    )


def _boot_volume_groups() -> list[str]:
    sources = set()
    for target in ("/", "/boot", "/boot/efi"):
        # BIOS installations legitimately have no /boot/efi path. Its absence
        # means there is no EFI mount to protect, not that host inspection failed.
        if not Path(target).exists():
            continue
        source = _command(
            ["findmnt", "--noheadings", "--output", "SOURCE", "--target", target]
        ).strip()
        if source.startswith("/dev/"):
            sources.add(os.path.realpath(source))
    sources.discard("")
    groups: set[str] = set()
    output = _command(["lvs", "--noheadings", "--separator", "|", "--options", "vg_name,lv_path"])
    for line in output.splitlines():
        fields = [field.strip() for field in line.split("|")]
        if len(fields) == 2 and os.path.realpath(fields[1]) in sources:
            groups.add(fields[0])
    return sorted(groups)


def _lvm_policy() -> str:
    groups = _boot_volume_groups()
    values = ", ".join(json.dumps(group) for group in groups)
    return "\n".join(
        [
            "# Managed by Hoardarr. Non-boot volume groups require a temporary executor allowlist.",
            "activation {",
            f"    auto_activation_volume_list = [ {values} ]",
            "}",
            "",
        ]
    )


def _multipath_policy() -> str:
    return "\n".join(
        [
            "# Managed by Hoardarr.",
            "defaults {",
            '    find_multipaths "strict"',
            "}",
            "",
        ]
    )


def atomic_text(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        _apply_created_mode(descriptor, temporary, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if hasattr(os, "O_DIRECTORY"):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _managed_fstab_entries(content: str) -> tuple[list[str], list[str]]:
    """Return filesystem UUIDs and mount targets from exact Hoardarr blocks."""
    uuids: list[str] = []
    targets: list[str] = []
    operation_id: str | None = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        begin = _MANAGED_BEGIN_RE.fullmatch(line)
        if begin is not None:
            if operation_id is not None:
                raise QuarantineError(
                    "managed_fstab_invalid", "The Hoardarr mount configuration is malformed."
                )
            operation_id = begin.group(1)
            continue
        if operation_id is None:
            continue
        if line == f"# END HOARDARR {operation_id}":
            operation_id = None
            continue
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 6 or not fields[1].startswith("/"):
            raise QuarantineError(
                "managed_fstab_invalid", "The Hoardarr mount configuration is malformed."
            )
        targets.append(fields[1].replace("\\040", " "))
        if fields[0].startswith("UUID="):
            filesystem_uuid = fields[0][5:]
            if _FILESYSTEM_UUID_RE.fullmatch(filesystem_uuid) is None:
                raise QuarantineError(
                    "managed_fstab_invalid", "A managed filesystem identity is invalid."
                )
            uuids.append(filesystem_uuid)
    if operation_id is not None:
        raise QuarantineError(
            "managed_fstab_invalid", "The Hoardarr mount configuration is incomplete."
        )
    return uuids, targets


def _with_mergerfs_dependencies(content: str) -> str:
    """Bind managed mergerFS units to their member mounts at boot."""
    managed_targets: dict[str, str] = {}
    target_operation: str | None = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        begin = _MANAGED_BEGIN_RE.fullmatch(line)
        if begin is not None:
            target_operation = begin.group(1)
            continue
        if target_operation is not None and line == f"# END HOARDARR {target_operation}":
            target_operation = None
            continue
        if target_operation is None or not line or line.startswith("#"):
            continue
        fields = raw_line.split()
        if len(fields) != 6 or not fields[0].startswith("UUID="):
            continue
        target = Path(fields[1].replace("\\040", " "))
        if target.parent != MANAGED_MEMBER_ROOT or target.name in managed_targets:
            continue
        managed_targets[target.name] = target.as_posix()

    output: list[str] = []
    operation_id: str | None = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        begin = _MANAGED_BEGIN_RE.fullmatch(line)
        if begin is not None:
            operation_id = begin.group(1)
            output.append(raw_line)
            continue
        if operation_id is not None and line == f"# END HOARDARR {operation_id}":
            operation_id = None
            output.append(raw_line)
            continue
        if operation_id is None or not line or line.startswith("#"):
            output.append(raw_line)
            continue
        fields = raw_line.split()
        if len(fields) == 6 and fields[2] == "fuse.mergerfs":
            branches = [item.replace("\\040", " ") for item in fields[0].split(":")]
            normalized_branches: list[str] = []
            for branch in branches:
                if branch.startswith("/"):
                    normalized_branches.append(branch)
                    continue
                if Path(branch).name != branch or branch not in managed_targets:
                    raise QuarantineError(
                        "managed_fstab_invalid", "A managed mergerFS branch is invalid."
                    )
                normalized_branches.append(managed_targets[branch])
            branches = normalized_branches
            if not branches or len(branches) != len(set(branches)):
                raise QuarantineError(
                    "managed_fstab_invalid", "A managed mergerFS branch is invalid."
                )
            fields[0] = ":".join(item.replace(" ", "\\040") for item in branches)
            options = [item for item in fields[3].split(",") if item]
            options = [item for item in options if not item.startswith("x-systemd.requires=")]
            options.extend(f"x-systemd.requires={branch}" for branch in branches)
            fields[3] = ",".join(options)
            output.append(" ".join(fields))
        else:
            output.append(raw_line)
    suffix = "\n" if content.endswith("\n") or output else ""
    return "\n".join(output) + suffix


def _identity_from_properties(properties: Mapping[str, str]) -> tuple[str, str]:
    for field in ("ID_WWN_WITH_EXTENSION", "ID_SERIAL_SHORT", "ID_WWN", "ID_SERIAL"):
        value = properties.get(field)
        if isinstance(value, str) and value:
            return field, _udev_escape(value)
    raise QuarantineError(
        "managed_identity_unstable",
        "A managed filesystem could not be bound to a stable hardware identity.",
    )


def _managed_identity_sources(
    device: Path,
    *,
    sys_class_block: Path = Path("/sys/class/block"),
    dev_root: Path = Path("/dev"),
) -> list[Path]:
    """Return exact hardware devices whose identities authorize a managed filesystem."""
    device_type = _command(
        ["lsblk", "--noheadings", "--output", "TYPE", os.fspath(device)]
    ).strip()
    if device_type != "md" and not device_type.startswith("raid"):
        return [device]

    slaves = sys_class_block / device.name / "slaves"
    try:
        member_names = sorted(item.name for item in slaves.iterdir())
    except OSError as exc:
        raise QuarantineError(
            "managed_md_members_unavailable",
            "A managed Linux MD filesystem has no readable member identity mapping.",
        ) from exc
    if not member_names:
        raise QuarantineError(
            "managed_md_members_unavailable",
            "A managed Linux MD filesystem has no readable member identity mapping.",
        )
    if any(_BLOCK_NAME_RE.fullmatch(name) is None for name in member_names):
        raise QuarantineError(
            "managed_identity_invalid", "A managed Linux MD member name is invalid."
        )
    return [dev_root / name for name in member_names]


def managed_identity_from_device(device: Mapping[str, Any]) -> tuple[str, str]:
    """Translate a reviewed detector identity to an exact udev match."""
    identity = device.get("identity") if isinstance(device.get("identity"), Mapping) else {}
    serial = identity.get("serial")
    if isinstance(serial, str) and serial:
        return "ID_SERIAL_SHORT", _udev_escape(serial)
    for field in ("wwn", "nguid", "eui64"):
        raw = identity.get(field)
        if not isinstance(raw, str) or not raw:
            continue
        normalized = raw.casefold()
        for prefix in ("wwn:", "naa.", "eui.", "nguid.", "0x"):
            normalized = normalized.removeprefix(prefix)
        if re.fullmatch(r"[0-9a-f]{16,128}", normalized) is not None:
            return "ID_WWN_WITH_EXTENSION", f"0x{normalized}"
    raise QuarantineError(
        "managed_identity_unstable",
        "A managed drive could not be bound to a stable udev identity.",
    )


def _load_managed_identities(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    details = path.stat(follow_symlinks=False)
    expected_uid = os.geteuid() if hasattr(os, "geteuid") else details.st_uid
    if not stat.S_ISREG(details.st_mode) or (
        os.name != "nt" and (details.st_uid != expected_uid or details.st_mode & 0o077)
    ):
        raise QuarantineError(
            "managed_state_unsafe", "The managed-drive identity state has unsafe permissions."
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuarantineError(
            "managed_state_invalid", "The managed-drive identity state is invalid."
        ) from exc
    entries = document.get("identities") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != 1
        or not isinstance(entries, list)
    ):
        raise QuarantineError(
            "managed_state_invalid", "The managed-drive identity state is invalid."
        )
    result: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"field", "value"}:
            raise QuarantineError(
                "managed_state_invalid", "The managed-drive identity state is invalid."
            )
        field = entry.get("field")
        value = entry.get("value")
        if field not in {
            "ID_WWN_WITH_EXTENSION",
            "ID_SERIAL_SHORT",
            "ID_WWN",
            "ID_SERIAL",
        } or not isinstance(value, str):
            raise QuarantineError(
                "managed_state_invalid", "The managed-drive identity state is invalid."
            )
        result.add((field, _udev_escape(value)))
    return result


def _managed_udev_policy(identities: Iterable[tuple[str, str]]) -> str:
    lines = [
        "# Managed by Hoardarr. Approved storage is systemd-ready but remains desktop-hidden.",
        'SUBSYSTEM!="block", GOTO="hoardarr_managed_end"',
        'ENV{DM_MULTIPATH_DEVICE_PATH}=="1", GOTO="hoardarr_managed_end"',
    ]
    for field, value in sorted(set(identities)):
        lines.append(
            f'ENV{{{field}}}=="{_udev_escape(value)}", ENV{{SYSTEMD_READY}}="1", '
            'ENV{UDISKS_IGNORE}="1", ENV{UDISKS_AUTO}="0", GOTO="hoardarr_managed_end"'
        )
    lines.extend(['LABEL="hoardarr_managed_end"', ""])
    return "\n".join(lines)


def persist_managed_identities(
    identities: Iterable[tuple[str, str]],
    *,
    state_path: Path = MANAGED_STORAGE_STATE,
    rule_path: Path = MANAGED_UDEV_RULE,
) -> list[tuple[str, str]]:
    combined = _load_managed_identities(state_path)
    combined.update((field, _udev_escape(value)) for field, value in identities)
    ordered = sorted(combined)
    atomic_json(
        state_path,
        {
            "schema_version": 1,
            "updated_at": time.time(),
            "identities": [{"field": field, "value": value} for field, value in ordered],
        },
    )
    atomic_text(rule_path, _managed_udev_policy(ordered), mode=0o644)
    return ordered


def reconcile_managed_storage(
    *,
    fstab_path: Path = FSTAB_PATH,
    state_path: Path = MANAGED_STORAGE_STATE,
    rule_path: Path = MANAGED_UDEV_RULE,
    dev_by_uuid: Path = Path("/dev/disk/by-uuid"),
    sys_class_block: Path = Path("/sys/class/block"),
    dev_root: Path = Path("/dev"),
    activate: bool = False,
) -> dict[str, Any]:
    """Release only Hoardarr-managed filesystems from deny-by-default quarantine."""
    if not fstab_path.exists():
        return {"managed_filesystems": 0, "activated_mounts": 0, "identities": []}
    try:
        original = fstab_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise QuarantineError(
            "managed_fstab_unavailable", "The managed mount configuration is unavailable."
        ) from exc
    filesystem_uuids, targets = _managed_fstab_entries(original)
    if not filesystem_uuids:
        return {"managed_filesystems": 0, "activated_mounts": 0, "identities": []}

    identities: set[tuple[str, str]] = set()
    trigger_names: set[str] = set()
    for filesystem_uuid in filesystem_uuids:
        alias = dev_by_uuid / filesystem_uuid
        try:
            device = alias.resolve(strict=True)
        except OSError as exc:
            raise QuarantineError(
                "managed_filesystem_missing",
                "A Hoardarr-managed filesystem is not currently available.",
            ) from exc
        parent_name = _command(
            ["lsblk", "--noheadings", "--output", "PKNAME", os.fspath(device)]
        ).strip()
        if parent_name:
            if _BLOCK_NAME_RE.fullmatch(parent_name) is None:
                raise QuarantineError(
                    "managed_identity_invalid", "A managed block-device parent is invalid."
                )
            parent = Path("/dev") / parent_name
        else:
            parent = device
        identity_sources = _managed_identity_sources(
            parent, sys_class_block=sys_class_block, dev_root=dev_root
        )
        for source in identity_sources:
            identities.add(_identity_from_properties(_udev_properties(os.fspath(source))))
            trigger_names.add(os.fspath(source))
        trigger_names.update({os.fspath(parent), os.fspath(device)})

    ordered = persist_managed_identities(
        identities,
        state_path=state_path,
        rule_path=rule_path,
    )
    normalized = _with_mergerfs_dependencies(original)
    atomic_text(fstab_path, normalized, mode=0o644)
    for target in targets:
        Path(target).mkdir(parents=True, exist_ok=True, mode=0o750)
    _command(["udevadm", "control", "--reload-rules"])
    for name in sorted(trigger_names):
        _command(["udevadm", "trigger", "--action=change", "--settle", f"--name-match={name}"])

    activated = 0
    if activate:
        _command(["systemctl", "daemon-reload"])
        for target in targets:
            unit = _command(["systemd-escape", "--path", "--suffix=mount", target]).strip()
            if not unit or "\n" in unit:
                raise QuarantineError(
                    "managed_mount_unit_invalid", "A managed mount unit name is invalid."
                )
            _command(["systemctl", "reset-failed", unit])
            _command(["systemctl", "start", unit], timeout=180)
            activated += 1
    return {
        "managed_filesystems": len(filesystem_uuids),
        "activated_mounts": activated,
        "identities": [{"field": field, "value": value} for field, value in ordered],
    }


def recover_incomplete_preparations(state_root: Path) -> list[str]:
    recovered: list[str] = []
    if not state_root.exists():
        return recovered
    allowed_paths = {str(path) for path in POLICY_PATHS}
    for recovery_path in sorted(state_root.glob("prepare-*/recovery.json")):
        try:
            recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QuarantineError(
                "quarantine_recovery_invalid",
                "An incomplete quarantine transaction cannot be recovered safely.",
            ) from exc
        if not isinstance(recovery, dict) or recovery.get("state") != "staging":
            continue
        files = recovery.get("files")
        if not isinstance(files, list):
            raise QuarantineError(
                "quarantine_recovery_invalid",
                "An incomplete quarantine transaction cannot be recovered safely.",
            )
        for item in reversed(files):
            if not isinstance(item, dict) or item.get("path") not in allowed_paths:
                raise QuarantineError(
                    "quarantine_recovery_invalid",
                    "An incomplete quarantine transaction cannot be recovered safely.",
                )
            target = Path(str(item["path"]))
            previous = item.get("previous")
            if previous is None:
                with contextlib.suppress(FileNotFoundError):
                    target.unlink()
                continue
            backup = Path(str(previous))
            if backup.parent != recovery_path.parent or not backup.is_file():
                raise QuarantineError(
                    "quarantine_recovery_invalid",
                    "An incomplete quarantine transaction cannot be recovered safely.",
                )
            try:
                content = backup.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise QuarantineError(
                    "quarantine_recovery_invalid",
                    "An incomplete quarantine transaction cannot be recovered safely.",
                ) from exc
            atomic_text(target, content)
        recovery["state"] = "recovered"
        recovery["recovered_at"] = time.time()
        atomic_json(recovery_path, recovery)
        recovered.append(str(recovery.get("transaction_id", recovery_path.parent.name)))
    return recovered


def prepare_quarantine(
    *,
    marker: Path = Path("/var/lib/hoardarr/storage-executor/quarantine.json"),
    state_root: Path = Path("/var/lib/hoardarr/storage-executor/quarantine-state"),
) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise QuarantineError("root_required", "Drive quarantine must be prepared as root.")
    recover_incomplete_preparations(state_root)
    boot_devices, boot_arrays = _boot_chain()
    policies = {
        POLICY_PATHS[0]: _udev_policy(boot_devices),
        POLICY_PATHS[1]: _mdadm_policy(boot_arrays),
        POLICY_PATHS[2]: _lvm_policy(),
        POLICY_PATHS[3]: _multipath_policy(),
    }
    transaction_id = f"prepare-{int(time.time())}-{os.getpid()}"
    transaction_root = state_root / transaction_id
    transaction_root.mkdir(parents=True, exist_ok=False, mode=0o700)
    recovery: dict[str, Any] = {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "state": "staging",
        "boot_devices": boot_devices,
        "boot_arrays": boot_arrays,
        "files": [],
    }
    atomic_json(transaction_root / "recovery.json", recovery)
    for index, (path, content) in enumerate(policies.items()):
        previous = path.read_bytes() if path.exists() else None
        backup = transaction_root / f"policy-{index}.previous"
        if previous is not None:
            backup.write_bytes(previous)
            os.chmod(backup, 0o600)
        recovery["files"].append(
            {"path": str(path), "previous": str(backup) if previous is not None else None}
        )
        atomic_json(transaction_root / "recovery.json", recovery)
        atomic_text(path, content)
    # Existing iSCSI nodes must never reconnect implicitly. Boot-from-SAN is
    # intentionally refused by the physical boot-chain inspection above.
    try:
        nodes = _command(["iscsiadm", "--mode", "node"]).splitlines()
    except QuarantineError:
        # iscsiadm returns a non-zero status when its node database is empty.
        nodes = []
    if nodes:
        _command(
            [
                "iscsiadm",
                "--mode",
                "node",
                "--op",
                "update",
                "--name",
                "node.startup",
                "--value",
                "manual",
            ]
        )
    _command(["udevadm", "control", "--reload-rules"])
    _command(["update-initramfs", "-u"], timeout=900)
    attestation = {
        "schema_version": SCHEMA_VERSION,
        "ready": True,
        "prepared_at": time.time(),
        "machine_id_sha256": hashlib.sha256(
            Path("/etc/machine-id").read_bytes().strip()
        ).hexdigest(),
        "boot_devices": boot_devices,
        "boot_arrays": boot_arrays,
        "policies": [{"path": str(path), "sha256": file_sha256(path)} for path in policies],
        "reboot_recommended": True,
        "transaction_id": transaction_id,
    }
    atomic_json(marker, attestation)
    recovery["state"] = "committed"
    recovery["marker"] = str(marker)
    atomic_json(transaction_root / "recovery.json", recovery)
    return attestation


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare deny-by-default drive quarantine")
    parser.add_argument("command", choices=("prepare", "reconcile-managed"))
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument(
        "--marker",
        type=Path,
        default=Path("/var/lib/hoardarr/storage-executor/quarantine.json"),
    )
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit(
            "use: hoardarr-storage-quarantine {prepare|reconcile-managed} --yes [--activate]"
        )
    if args.command == "prepare":
        if args.activate:
            raise SystemExit("--activate is only valid with reconcile-managed")
        result = prepare_quarantine(marker=args.marker)
    else:
        result = reconcile_managed_storage(activate=args.activate)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
