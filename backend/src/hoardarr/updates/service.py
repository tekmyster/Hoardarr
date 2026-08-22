from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import uuid
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class UpdateError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


_VERSION_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:[-+][0-9A-Za-z.-]+)?$"
)
_RELEASE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_REVISION_RE = re.compile(r"^[0-9]{4}_[a-z0-9_]{1,60}$")


def _version_key(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or not (match := _VERSION_RE.fullmatch(value)):
        raise UpdateError("metadata_invalid", "Update version metadata is invalid")
    return tuple(int(match.group(index)) for index in (1, 2, 3))  # type: ignore[return-value]


def fetch_bytes(url: str, *, limit: int, transport: httpx.BaseTransport | None = None) -> bytes:
    current = url
    with httpx.Client(
        timeout=httpx.Timeout(30.0),
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    ) as client:
        for _attempt in range(6):
            parts = urlsplit(current)
            host = (parts.hostname or "").casefold()
            if (
                parts.scheme != "https"
                or parts.username
                or not (host == "github.com" or host.endswith(".githubusercontent.com"))
            ):
                raise UpdateError("update_origin_rejected", "Update download origin was rejected")
            try:
                response = client.get(current)
            except httpx.HTTPError as exc:
                raise UpdateError(
                    "update_network_failed", "Update service could not be reached"
                ) from exc
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise UpdateError("update_redirect_invalid", "Update redirect was invalid")
                current = str(response.url.join(location))
                continue
            if response.status_code >= 400:
                raise UpdateError("update_remote_error", "Update service returned an error")
            content = response.content
            if len(content) > limit:
                raise UpdateError("update_response_too_large", "Update response exceeded its limit")
            return content
    raise UpdateError("update_redirect_limit", "Update download redirected too many times")


def fetch_signed_metadata(
    metadata_url: str,
    signature_url: str,
    *,
    trust_path: Path,
    channel: str,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    try:
        metadata = json.loads(
            fetch_bytes(metadata_url, limit=256 * 1024, transport=transport),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
        signature = fetch_bytes(signature_url, limit=4096, transport=transport).decode().strip()
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise UpdateError("metadata_invalid", "Update metadata response was invalid") from exc
    if not isinstance(metadata, Mapping):
        raise UpdateError("metadata_invalid", "Update metadata response was invalid")
    return verify_release_metadata(metadata, signature, trust_path=trust_path, channel=channel)


def download_artifact(
    metadata: Mapping[str, Any], destination: Path, *, transport: httpx.BaseTransport | None = None
) -> Path:
    expected_size = int(metadata["artifact_size"])
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_suffix(".part")
    temporary.unlink(missing_ok=True)
    current = str(metadata["artifact_url"])
    digest = hashlib.sha256()
    received = 0
    try:
        with httpx.Client(
            timeout=httpx.Timeout(30.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            for _attempt in range(6):
                parts = urlsplit(current)
                host = (parts.hostname or "").casefold()
                if (
                    parts.scheme != "https"
                    or parts.username
                    or not (host == "github.com" or host.endswith(".githubusercontent.com"))
                ):
                    raise UpdateError(
                        "update_origin_rejected", "Update download origin was rejected"
                    )
                with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise UpdateError(
                                "update_redirect_invalid", "Update redirect was invalid"
                            )
                        current = str(response.url.join(location))
                        continue
                    if response.status_code >= 400:
                        raise UpdateError("update_remote_error", "Update service returned an error")
                    with temporary.open("xb") as handle:
                        for chunk in response.iter_bytes(1024 * 1024):
                            received += len(chunk)
                            if received > expected_size:
                                raise UpdateError(
                                    "artifact_size_mismatch", "Update artifact size did not match"
                                )
                            digest.update(chunk)
                            handle.write(chunk)
                        handle.flush()
                        os.fsync(handle.fileno())
                    break
            else:
                raise UpdateError(
                    "update_redirect_limit", "Update download redirected too many times"
                )
        if received != expected_size or digest.hexdigest() != metadata["artifact_sha256"]:
            raise UpdateError("artifact_digest_mismatch", "Update artifact verification failed")
        os.replace(temporary, destination)
    except httpx.HTTPError as exc:
        raise UpdateError("update_network_failed", "Update service could not be reached") from exc
    finally:
        temporary.unlink(missing_ok=True)
    verify_artifact(
        destination,
        expected_sha256=str(metadata["artifact_sha256"]),
        expected_size=int(metadata["artifact_size"]),
    )
    return destination


@dataclass(frozen=True)
class UpdatePaths:
    releases: Path = Path("/usr/lib/hoardarr/releases")
    current: Path = Path("/usr/lib/hoardarr/current")
    state: Path = Path("/var/lib/hoardarr")
    config: Path = Path("/etc/hoardarr")
    trust: Path = Path("/etc/hoardarr/update-trust.json")
    backup: Path = Path("/var/lib/hoardarr/update-backups")

    @property
    def journal(self) -> Path:
        return self.backup / "update-journal.json"


def canonical_metadata(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def load_trust_root(path: Path, channel: str) -> Ed25519PublicKey:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("schema_version") != 1:
            raise ValueError
        key_text = document["channels"][channel]["ed25519_public_key"]
        key = base64.b64decode(key_text, validate=True)
        if len(key) != 32:
            raise ValueError
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise UpdateError("trust_root_unavailable", "The update trust root is unavailable") from exc
    return Ed25519PublicKey.from_public_bytes(key)


def verify_release_metadata(
    metadata: Mapping[str, Any], signature_text: str, *, trust_path: Path, channel: str
) -> dict[str, Any]:
    required = {
        "schema_version",
        "channel",
        "version",
        "release_id",
        "artifact_url",
        "artifact_sha256",
        "artifact_size",
        "minimum_version",
        "database_revision",
        "addon_api_version",
    }
    if set(metadata) != required or metadata.get("schema_version") != 1:
        raise UpdateError("metadata_invalid", "Update metadata has an invalid schema")
    if metadata.get("channel") != channel:
        raise UpdateError("channel_mismatch", "Update metadata does not match the selected channel")
    digest = metadata.get("artifact_sha256")
    size = metadata.get("artifact_size")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(item not in "0123456789abcdef" for item in digest)
        or not isinstance(size, int)
        or not 1 <= size <= 8 * 1024**3
    ):
        raise UpdateError("metadata_invalid", "Update artifact metadata is invalid")
    _version_key(metadata.get("version"))
    _version_key(metadata.get("minimum_version"))
    if not isinstance(metadata.get("release_id"), str) or not _RELEASE_RE.fullmatch(
        str(metadata["release_id"])
    ):
        raise UpdateError("metadata_invalid", "Update release identity is invalid")
    if not isinstance(metadata.get("database_revision"), str) or not _REVISION_RE.fullmatch(
        str(metadata["database_revision"])
    ):
        raise UpdateError("metadata_invalid", "Update database revision is invalid")
    if (
        not isinstance(metadata.get("addon_api_version"), int)
        or not 1 <= int(metadata["addon_api_version"]) <= 65535
    ):
        raise UpdateError("metadata_invalid", "Update add-on API version is invalid")
    try:
        signature = base64.b64decode(signature_text, validate=True)
        load_trust_root(trust_path, channel).verify(signature, canonical_metadata(metadata))
    except (ValueError, InvalidSignature) as exc:
        raise UpdateError(
            "signature_invalid", "Update metadata signature verification failed"
        ) from exc
    return dict(metadata)


def verify_artifact(path: Path, *, expected_sha256: str, expected_size: int) -> None:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(block)
                if size > expected_size:
                    raise UpdateError(
                        "artifact_size_mismatch", "Update artifact size did not match"
                    )
                digest.update(block)
    except OSError as exc:
        raise UpdateError("artifact_unavailable", "Update artifact could not be read") from exc
    if size != expected_size or digest.hexdigest() != expected_sha256:
        raise UpdateError("artifact_digest_mismatch", "Update artifact verification failed")


def preflight_update(
    metadata: Mapping[str, Any],
    *,
    current_version: str,
    active_storage_operations: int,
    free_bytes: int,
    installed_addons: list[Mapping[str, Any]],
) -> dict[str, Any]:
    blockers: list[dict[str, str]] = []
    try:
        current_key = _version_key(current_version)
        latest_key = _version_key(metadata["version"])
        minimum_key = _version_key(metadata["minimum_version"])
    except UpdateError:
        blockers.append({"code": "version_invalid", "message": "Update version is invalid"})
    else:
        if current_key < minimum_key:
            blockers.append(
                {
                    "code": "version_unsupported",
                    "message": "This release cannot update the installed version",
                }
            )
        if latest_key <= current_key:
            blockers.append({"code": "not_newer", "message": "No newer release is available"})
    if active_storage_operations:
        blockers.append({"code": "storage_active", "message": "Storage work is currently active"})
    required = int(metadata["artifact_size"]) * 3
    if free_bytes < required:
        blockers.append(
            {"code": "insufficient_space", "message": "Update storage has insufficient free space"}
        )
    target_api = int(metadata["addon_api_version"])
    incompatible = sorted(
        str(addon.get("name"))
        for addon in installed_addons
        if addon.get("enabled") is True
        and target_api
        not in range(int(addon.get("api_min", -1)), int(addon.get("api_max", -1)) + 1)
    )
    if incompatible:
        blockers.append(
            {
                "code": "addon_incompatible",
                "message": f"Disable or update: {', '.join(incompatible)}",
            }
        )
    return {
        "current_version": current_version,
        "latest_version": metadata["version"],
        "channel": metadata["channel"],
        "compatible": not blockers,
        "blockers": blockers,
        "required_free_bytes": required,
    }


Runner = Callable[[list[str], int], None]


def _run(argv: list[str], timeout: int) -> None:
    try:
        subprocess.run(
            argv,
            check=True,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise UpdateError("update_command_failed", "An update command failed") from exc


def _replace_link(source: Path, target: Path) -> None:
    # POSIX os.replace is atomic for symlinks. Windows repository tests need the
    # destination link removed first because ReplaceFile rejects directory links.
    if os.name == "nt" and target.is_symlink():
        target.unlink()
    os.replace(source, target)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _member_path(stage: Path, name: str) -> Path:
    normalized = Path(name.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts or not normalized.parts:
        raise UpdateError("release_archive_unsafe", "Release archive contains an unsafe path")
    destination = stage.joinpath(*normalized.parts)
    if stage.resolve() not in destination.resolve(strict=False).parents:
        raise UpdateError("release_archive_unsafe", "Release archive contains an unsafe path")
    return destination


def safe_extract_release(artifact: Path, stage: Path, *, maximum_bytes: int = 8 * 1024**3) -> None:
    total = 0
    try:
        if zipfile.is_zipfile(artifact):
            with zipfile.ZipFile(artifact) as archive:
                for member in archive.infolist():
                    total += member.file_size
                    mode = member.external_attr >> 16
                    if total > maximum_bytes or stat.S_ISLNK(mode):
                        raise UpdateError("release_archive_unsafe", "Release archive is unsafe")
                    destination = _member_path(stage, member.filename)
                    if member.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as source, destination.open("xb") as output:
                            shutil.copyfileobj(source, output)
            return
        with tarfile.open(artifact, mode="r:*") as archive:
            for member in archive.getmembers():
                total += member.size
                if total > maximum_bytes or not (member.isfile() or member.isdir()):
                    raise UpdateError("release_archive_unsafe", "Release archive is unsafe")
                destination = _member_path(stage, member.name)
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    source = archive.extractfile(member)
                    if source is None:
                        raise UpdateError("release_archive_invalid", "Release archive is invalid")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with source, destination.open("xb") as output:
                        shutil.copyfileobj(source, output)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise UpdateError(
            "release_archive_invalid", "Release archive could not be extracted"
        ) from exc


def recover_interrupted_update(paths: UpdatePaths, runner: Runner = _run) -> bool:
    if not paths.journal.is_file() or paths.journal.is_symlink():
        return False
    try:
        journal = json.loads(paths.journal.read_text(encoding="utf-8"))
        if journal.get("state") != "running":
            return False
        backup = Path(journal["backup"])
        old_target = Path(journal["old_target"]) if journal.get("old_target") else None
        if old_target is not None and (
            old_target.parent != paths.releases or not old_target.is_dir()
        ):
            raise ValueError
        if backup.parent != paths.backup or not backup.is_dir():
            raise ValueError
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise UpdateError("update_recovery_unsafe", "Interrupted update state is invalid") from exc
    if old_target is not None:
        rollback_link = paths.current.with_name(f".{paths.current.name}.recovery")
        rollback_link.unlink(missing_ok=True)
        rollback_link.symlink_to(old_target, target_is_directory=True)
        _replace_link(rollback_link, paths.current)
    database = paths.state / "hoardarr.db"
    if (backup / "hoardarr.db").is_file():
        shutil.copy2(backup / "hoardarr.db", database)
    if (backup / "config").is_dir():
        restored = paths.config.with_name(f".{paths.config.name}.recovery")
        if restored.exists():
            shutil.rmtree(restored)
        shutil.copytree(backup / "config", restored, symlinks=False)
        if paths.config.exists():
            shutil.rmtree(paths.config)
        os.replace(restored, paths.config)
    runner(["systemctl", "restart", "hoardarr-api.service", "hoardarr-worker.service"], 300)
    _atomic_json(paths.journal, {**journal, "state": "recovered"})
    return True


def execute_update(
    metadata: Mapping[str, Any],
    artifact: Path,
    *,
    paths: UpdatePaths,
    runner: Runner = _run,
    extractor: Callable[[Path, Path], None] | None = None,
) -> dict[str, Any]:
    """Stage, migrate, atomically switch, health-check, and roll back on failure."""
    verify_artifact(
        artifact,
        expected_sha256=str(metadata["artifact_sha256"]),
        expected_size=int(metadata["artifact_size"]),
    )
    release_id = str(metadata["release_id"])
    if not release_id or any(
        item not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for item in release_id
    ):
        raise UpdateError("release_id_invalid", "Release identity is invalid")
    paths.releases.mkdir(parents=True, exist_ok=True, mode=0o755)
    paths.backup.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = paths.releases / release_id
    if target.exists() or target.is_symlink():
        raise UpdateError("release_exists", "The target release is already staged")
    old_target = paths.current.resolve(strict=True) if paths.current.exists() else None
    backup = paths.backup / f"{release_id}-{uuid.uuid4().hex}"
    backup.mkdir(mode=0o700)
    if paths.config.exists():
        shutil.copytree(paths.config, backup / "config", symlinks=False)
    database = paths.state / "hoardarr.db"
    if database.is_file() and not database.is_symlink():
        shutil.copy2(database, backup / "hoardarr.db")
    stage = Path(tempfile.mkdtemp(prefix=f".{release_id}-", dir=paths.releases))
    switched = False
    migration_started = False
    journal = {
        "schema_version": 1,
        "state": "running",
        "release_id": release_id,
        "backup": str(backup),
        "old_target": str(old_target) if old_target else None,
        "switched": False,
        "phase": "Preparing update",
        "percent": 10,
    }
    _atomic_json(paths.journal, journal)
    try:
        journal.update(phase="Extracting release", percent=20)
        _atomic_json(paths.journal, journal)
        if extractor is None:
            safe_extract_release(artifact, stage, maximum_bytes=int(metadata["artifact_size"]) * 20)
        else:
            extractor(artifact, stage)
        if not (stage / "manifest.json").is_file() or not (stage / "backend").is_dir():
            raise UpdateError("release_structure_invalid", "Staged release structure is invalid")
        os.replace(stage, target)
        journal.update(phase="Running database migrations", percent=50)
        _atomic_json(paths.journal, journal)
        migration_started = True
        runner([str(target / "venv/bin/python"), "-m", "hoardarr.runtime", "migrate"], 900)
        journal.update(phase="Activating release", percent=65)
        _atomic_json(paths.journal, journal)
        temporary_link = paths.current.with_name(f".{paths.current.name}.{release_id}")
        temporary_link.symlink_to(target, target_is_directory=True)
        _replace_link(temporary_link, paths.current)
        switched = True
        journal["switched"] = True
        _atomic_json(paths.journal, journal)
        journal.update(phase="Restarting services", percent=75)
        _atomic_json(paths.journal, journal)
        runner(["systemctl", "daemon-reload"], 120)
        runner(["systemctl", "restart", "hoardarr-api.service", "hoardarr-worker.service"], 300)
        journal.update(phase="Checking service health", percent=90)
        _atomic_json(paths.journal, journal)
        runner(
            ["curl", "--fail", "--silent", "--show-error", "http://127.0.0.1:7877/health/ready"], 60
        )
    except Exception as exc:
        rollback_failed = False
        if switched and old_target is not None:
            rollback_link = paths.current.with_name(f".{paths.current.name}.rollback")
            rollback_link.unlink(missing_ok=True)
            rollback_link.symlink_to(old_target, target_is_directory=True)
            _replace_link(rollback_link, paths.current)
        if migration_started and (backup / "hoardarr.db").is_file():
            shutil.copy2(backup / "hoardarr.db", database)
        if paths.config.exists() and (backup / "config").is_dir():
            restored = paths.config.with_name(f".{paths.config.name}.rollback")
            if restored.exists():
                shutil.rmtree(restored)
            shutil.copytree(backup / "config", restored, symlinks=False)
            shutil.rmtree(paths.config)
            os.replace(restored, paths.config)
        if switched:
            try:
                runner(
                    ["systemctl", "restart", "hoardarr-api.service", "hoardarr-worker.service"], 300
                )
            except Exception:
                rollback_failed = True
        if target.is_dir() and (not switched or old_target is not None):
            shutil.rmtree(target)
        _atomic_json(
            paths.journal,
            {**journal, "state": "rollback_failed" if rollback_failed else "rolled_back"},
        )
        if rollback_failed:
            raise UpdateError(
                "rollback_failed", "Update failed and services could not be restored"
            ) from exc
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("update_failed", "Update failed and rollback was attempted") from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    _atomic_json(
        paths.journal,
        {**journal, "state": "completed", "phase": "Update completed", "percent": 100},
    )
    return {
        "state": "completed",
        "version": metadata["version"],
        "release_id": release_id,
        "backup": str(backup),
    }
