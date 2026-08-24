from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import stat
import subprocess
import time
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
    parser.add_argument("prepare", nargs="?")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument(
        "--marker",
        type=Path,
        default=Path("/var/lib/hoardarr/storage-executor/quarantine.json"),
    )
    args = parser.parse_args()
    if args.prepare != "prepare" or not args.yes:
        raise SystemExit("use: hoardarr-storage-quarantine prepare --yes")
    result = prepare_quarantine(marker=args.marker)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
