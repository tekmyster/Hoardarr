from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class AddonError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


_NAME = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
_PACKAGE = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,127}$")
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$")


def _version_key(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or (match := _VERSION.fullmatch(value)) is None:
        raise AddonError("manifest_invalid", "Version compatibility value is invalid")
    return tuple(int(item) for item in match.groups())  # type: ignore[return-value]


def normalize_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AddonError("manifest_invalid", "Add-on manifest must be an object")
    required = {
        "schema_version",
        "name",
        "version",
        "api",
        "packages",
        "privileges",
        "database",
        "ui",
        "updates",
        "entrypoint",
        "payload_sha256",
    }
    if set(value) != required or value.get("schema_version") != 1:
        raise AddonError("manifest_invalid", "Add-on manifest schema is invalid")
    name = value.get("name")
    version = value.get("version")
    entrypoint = value.get("entrypoint")
    if (
        not isinstance(name, str)
        or not _NAME.fullmatch(name)
        or not isinstance(version, str)
        or not version
    ):
        raise AddonError("manifest_invalid", "Add-on identity is invalid")
    path = PurePosixPath(str(entrypoint))
    if path.is_absolute() or ".." in path.parts or path.suffix != ".py":
        raise AddonError(
            "entrypoint_invalid", "Add-on entrypoint must be a relative Python module path"
        )
    api = value.get("api")
    database = value.get("database")
    updates = value.get("updates")
    if not all(isinstance(item, Mapping) for item in (api, database, updates)):
        raise AddonError("manifest_invalid", "Compatibility metadata is invalid")
    for key in ("minimum", "maximum"):
        _version_key(updates.get(key))
    if (
        not all(isinstance(api.get(key), int) for key in ("minimum", "maximum"))
        or api["minimum"] > api["maximum"]
    ):
        raise AddonError("manifest_invalid", "API compatibility range is invalid")
    packages = value.get("packages")
    if not isinstance(packages, list) or not all(
        isinstance(item, str) and _PACKAGE.fullmatch(item) for item in packages
    ):
        raise AddonError("manifest_invalid", "Package requirements are invalid")
    privileges = value.get("privileges")
    allowed_privileges = {
        "hardware.read",
        "storage.read",
        "storage.operate",
        "network.read",
        "network.operate",
        "ui.extend",
    }
    if not isinstance(privileges, list) or not set(privileges) <= allowed_privileges:
        raise AddonError("privilege_invalid", "Add-on privilege declaration is invalid")
    ui = value.get("ui")
    if not isinstance(ui, list) or not all(
        isinstance(item, Mapping)
        and set(item) == {"slot", "module"}
        and item["slot"] in {"settings", "storage", "health", "overview"}
        and isinstance(item["module"], str)
        and ".." not in PurePosixPath(item["module"]).parts
        for item in ui
    ):
        raise AddonError("ui_extension_invalid", "UI extension declaration is invalid")
    digest = value.get("payload_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise AddonError("manifest_invalid", "Payload digest is invalid")
    return json.loads(json.dumps(value, sort_keys=True))


def verify_manifest(
    manifest: Mapping[str, Any], signature_text: str, public_key_text: str
) -> dict[str, Any]:
    normalized = normalize_manifest(manifest)
    try:
        key_bytes = base64.b64decode(public_key_text, validate=True)
        signature = base64.b64decode(signature_text, validate=True)
        if len(key_bytes) != 32:
            raise ValueError
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(
            signature,
            json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode(),
        )
    except (ValueError, InvalidSignature) as exc:
        raise AddonError("signature_invalid", "Add-on signature verification failed") from exc
    return normalized


def validate_payload(path: Path, expected_sha256: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise AddonError("payload_invalid", "Add-on payload must be a regular file")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise AddonError("payload_digest_mismatch", "Add-on payload verification failed")


def lifecycle_state(current: str, action: str) -> str:
    transitions = {
        ("installed", "enable"): "enabled",
        ("enabled", "disable"): "installed",
        ("installed", "remove"): "removed",
        ("failed", "disable"): "installed",
    }
    try:
        return transitions[(current, action)]
    except KeyError as exc:
        raise AddonError("lifecycle_invalid", "Add-on lifecycle transition is invalid") from exc


def validate_upgrade(current_version: str, next_version: str, current_state: str) -> None:
    if current_state == "enabled":
        raise AddonError("addon_enabled", "Disable the add-on before updating it")
    if _version_key(next_version) <= _version_key(current_version):
        raise AddonError("version_not_newer", "Add-on update version must be newer")


def validate_compatibility(
    manifest: Mapping[str, Any],
    *,
    api_version: int,
    database_revision: str,
    hoardarr_version: str,
    package_available: Any,
) -> None:
    normalized = normalize_manifest(manifest)
    if not normalized["api"]["minimum"] <= api_version <= normalized["api"]["maximum"]:
        raise AddonError("api_incompatible", "Add-on API compatibility does not include this host")
    if (
        not normalized["database"]["minimum"]
        <= database_revision
        <= normalized["database"]["maximum"]
    ):
        raise AddonError(
            "database_incompatible", "Add-on database compatibility does not include this host"
        )
    if not (
        _version_key(normalized["updates"]["minimum"])
        <= _version_key(hoardarr_version)
        <= _version_key(normalized["updates"]["maximum"])
    ):
        raise AddonError(
            "version_incompatible", "Add-on version compatibility does not include this host"
        )
    missing = [name for name in normalized["packages"] if not package_available(name)]
    if missing:
        raise AddonError("package_missing", f"Required package is unavailable: {missing[0]}")


def debian_package_available(name: str) -> bool:
    command = shutil.which("dpkg-query")
    if command is None:
        return False
    result = subprocess.run(
        [command, "-W", "-f=${db:Status-Status}", name],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=30,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
    )
    return result.returncode == 0 and result.stdout.strip() == "installed"


def install_payload(payload: Path, manifest: Mapping[str, Any], addon_root: Path) -> Path:
    normalized = normalize_manifest(manifest)
    validate_payload(payload, str(normalized["payload_sha256"]))
    target = addon_root / str(normalized["name"]) / str(normalized["version"])
    if target.exists() or target.is_symlink():
        raise AddonError("addon_exists", "This add-on version is already installed")
    stage = target.with_name(f".{target.name}.stage")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, mode=0o750)
    total = 0
    try:
        with zipfile.ZipFile(payload) as archive:
            for member in archive.infolist():
                path = PurePosixPath(member.filename)
                mode = member.external_attr >> 16
                total += member.file_size
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or not path.parts
                    or total > 256 * 1024 * 1024
                    or stat.S_ISLNK(mode)
                ):
                    raise AddonError("payload_unsafe", "Add-on archive contains an unsafe entry")
                destination = stage.joinpath(*path.parts)
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output)
                destination.chmod(0o640)
        entrypoint = stage.joinpath(*PurePosixPath(str(normalized["entrypoint"])).parts)
        if not entrypoint.is_file() or entrypoint.is_symlink():
            raise AddonError("entrypoint_missing", "Add-on entrypoint is missing")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        stage.replace(target)
    except AddonError:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        if stage.exists():
            shutil.rmtree(stage)
        raise AddonError("payload_invalid", "Add-on archive could not be installed") from exc
    return target


def runtime_unit(manifest: Mapping[str, Any], install_path: Path) -> str:
    normalized = normalize_manifest(manifest)
    entrypoint = install_path.joinpath(*PurePosixPath(str(normalized["entrypoint"])).parts)
    if (
        not entrypoint.is_file()
        or entrypoint.is_symlink()
        or install_path not in entrypoint.parents
    ):
        raise AddonError("entrypoint_missing", "Add-on entrypoint is missing")
    privileges = set(normalized["privileges"])
    properties = [
        "DynamicUser=yes",
        "NoNewPrivileges=yes",
        "PrivateTmp=yes",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "ProtectKernelTunables=yes",
        "ProtectKernelModules=yes",
        "ProtectControlGroups=yes",
        "RestrictSUIDSGID=yes",
        "LockPersonality=yes",
        "RestrictRealtime=yes",
        "MemoryDenyWriteExecute=yes",
        "PrivateDevices=yes" if "hardware.read" not in privileges else "PrivateDevices=no",
    ]
    if privileges & {"storage.read", "storage.operate"}:
        properties.append("SupplementaryGroups=hoardarr-media")
        directive = "ReadWritePaths" if "storage.operate" in privileges else "ReadOnlyPaths"
        properties.append(f"{directive}=/data /mnt/hoardarr /srv/hoardarr")
    if "hardware.read" in privileges:
        properties.extend(("DevicePolicy=closed", "DeviceAllow=block-* r", "ReadOnlyPaths=/sys"))
    if "network.operate" in privileges:
        properties.extend(
            ("AmbientCapabilities=CAP_NET_ADMIN", "CapabilityBoundingSet=CAP_NET_ADMIN")
        )
    else:
        properties.append("CapabilityBoundingSet=")
    return "\n".join(
        [
            "[Unit]",
            f"Description=Hoardarr add-on {normalized['name']}",
            "After=network-online.target hoardarr-api.service",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            *properties,
            f"WorkingDirectory={install_path}",
            f"ExecStart=/usr/lib/hoardarr/current/venv/bin/python -I {entrypoint}",
            "Restart=on-failure",
            "RestartSec=5s",
            "TimeoutStopSec=30s",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def runtime_command(manifest: Mapping[str, Any], install_path: Path) -> list[str]:
    normalized = normalize_manifest(manifest)
    return ["systemctl", "enable", "--now", f"hoardarr-addon-{normalized['name']}.service"]


def _atomic_unit(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_lifecycle_action(
    manifest: Mapping[str, Any],
    install_path: Path,
    action: str,
    *,
    unit_root: Path = Path("/etc/systemd/system"),
    runner: Any = subprocess.run,
) -> None:
    normalized = normalize_manifest(manifest)
    unit = f"hoardarr-addon-{normalized['name']}.service"
    unit_path = unit_root / unit
    if unit_path.parent != unit_root or unit_path.is_symlink():
        raise AddonError("runtime_path_invalid", "Add-on runtime path is unsafe")
    commands: list[list[str]] = []
    if action == "enable":
        _atomic_unit(unit_path, runtime_unit(normalized, install_path))
        commands = [["systemctl", "daemon-reload"], runtime_command(normalized, install_path)]
    elif action == "disable":
        commands = [["systemctl", "disable", "--now", unit]]
    elif action == "remove":
        commands = (
            [["systemctl", "disable", "--now", unit], ["systemctl", "daemon-reload"]]
            if unit_path.is_file()
            else []
        )
    else:
        raise AddonError("lifecycle_invalid", "Add-on lifecycle action is invalid")
    try:
        for command in commands:
            runner(
                command,
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=120,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
            )
    except (OSError, subprocess.SubprocessError) as exc:
        if action == "enable":
            unit_path.unlink(missing_ok=True)
        raise AddonError("runtime_failed", "Add-on runtime lifecycle failed") from exc
    if action == "remove":
        unit_path.unlink(missing_ok=True)
