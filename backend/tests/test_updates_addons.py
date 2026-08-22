from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from hoardarr.addons.service import (
    AddonError,
    lifecycle_state,
    normalize_manifest,
    run_lifecycle_action,
    runtime_unit,
    validate_compatibility,
    validate_payload,
    validate_upgrade,
    verify_manifest,
)
from hoardarr.updates.service import (
    UpdateError,
    UpdatePaths,
    canonical_metadata,
    download_artifact,
    execute_update,
    preflight_update,
    safe_extract_release,
    verify_release_metadata,
)


def _release(artifact: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "channel": "stable",
        "version": "0.4.0",
        "release_id": "0.4.0",
        "artifact_url": "https://updates.example/hoardarr.tar.gz",
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "artifact_size": artifact.stat().st_size,
        "minimum_version": "0.1.0",
        "database_revision": "0004_runtime",
        "addon_api_version": 1,
    }


def _trust(tmp_path: Path, metadata: dict[str, object]) -> tuple[Path, str]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    trust = tmp_path / "trust.json"
    trust.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "channels": {"stable": {"ed25519_public_key": base64.b64encode(public).decode()}},
            }
        )
    )
    signature = private.sign(canonical_metadata(metadata))
    return trust, base64.b64encode(signature).decode()


def test_signed_metadata_uses_independent_local_trust_root(tmp_path: Path) -> None:
    artifact = tmp_path / "release.bin"
    artifact.write_bytes(b"release")
    metadata = _release(artifact)
    trust, signature = _trust(tmp_path, metadata)
    assert (
        verify_release_metadata(metadata, signature, trust_path=trust, channel="stable")["version"]
        == "0.4.0"
    )
    metadata["version"] = "9.9.9"
    with pytest.raises(UpdateError) as exc:
        verify_release_metadata(metadata, signature, trust_path=trust, channel="stable")
    assert exc.value.code == "signature_invalid"


def test_signed_metadata_rejects_malformed_compatibility_fields_before_use(tmp_path: Path) -> None:
    artifact = tmp_path / "release.bin"
    artifact.write_bytes(b"release")
    metadata = _release(artifact)
    metadata["minimum_version"] = "oldest"
    trust, signature = _trust(tmp_path, metadata)
    with pytest.raises(UpdateError) as exc:
        verify_release_metadata(metadata, signature, trust_path=trust, channel="stable")
    assert exc.value.code == "metadata_invalid"


def test_artifact_download_streams_and_verifies_before_atomic_install(tmp_path: Path) -> None:
    content = b"release-content"
    metadata = {
        "artifact_url": "https://github.com/tekmyster/Hoardarr/releases/download/v1/release.tar.gz",
        "artifact_size": len(content),
        "artifact_sha256": hashlib.sha256(content).hexdigest(),
    }
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=content))
    destination = tmp_path / "release.tar.gz"
    assert download_artifact(metadata, destination, transport=transport) == destination
    assert destination.read_bytes() == content
    assert not destination.with_suffix(".part").exists()


def test_update_preflight_blocks_active_storage_space_and_addons(tmp_path: Path) -> None:
    artifact = tmp_path / "release.bin"
    artifact.write_bytes(b"release")
    result = preflight_update(
        _release(artifact),
        current_version="0.3.10",
        active_storage_operations=1,
        free_bytes=0,
        installed_addons=[{"name": "old", "enabled": True, "api_min": 2, "api_max": 3}],
    )
    assert result["compatible"] is False
    assert {item["code"] for item in result["blockers"]} == {
        "storage_active",
        "insufficient_space",
        "addon_incompatible",
    }


def test_update_switches_and_rolls_back_after_failed_health_check(tmp_path: Path) -> None:
    artifact = tmp_path / "release.bin"
    artifact.write_bytes(b"release")
    paths = UpdatePaths(
        releases=tmp_path / "releases",
        current=tmp_path / "current",
        state=tmp_path / "state",
        config=tmp_path / "config",
        trust=tmp_path / "trust.json",
        backup=tmp_path / "backups",
    )
    old = paths.releases / "0.3.10"
    (old / "backend").mkdir(parents=True)
    (old / "manifest.json").write_text("{}")
    paths.current.symlink_to(old, target_is_directory=True)
    paths.state.mkdir()
    (paths.state / "hoardarr.db").write_bytes(b"database")
    paths.config.mkdir()
    (paths.config / "hoardarr.env").write_text("CONFIG=preserved")

    calls: list[list[str]] = []

    def runner(argv: list[str], _timeout: int) -> None:
        calls.append(argv)
        if argv[0] == "curl":
            raise UpdateError("health_failed", "Health check failed")

    def extractor(_artifact: Path, stage: Path) -> None:
        (stage / "backend").mkdir()
        (stage / "manifest.json").write_text("{}")
        (stage / "venv" / "bin").mkdir(parents=True)
        (stage / "venv" / "bin" / "python").write_text("")

    with pytest.raises(UpdateError) as exc:
        execute_update(
            _release(artifact), artifact, paths=paths, runner=runner, extractor=extractor
        )
    assert exc.value.code == "health_failed"
    assert paths.current.resolve() == old.resolve()
    assert (paths.state / "hoardarr.db").read_bytes() == b"database"
    assert sum(1 for call in calls if call[0] == "systemctl" and "restart" in call) == 2


def test_update_restores_database_when_migration_fails_before_switch(tmp_path: Path) -> None:
    artifact = tmp_path / "release.bin"
    artifact.write_bytes(b"release")
    paths = UpdatePaths(
        releases=tmp_path / "releases",
        current=tmp_path / "current",
        state=tmp_path / "state",
        config=tmp_path / "config",
        trust=tmp_path / "trust.json",
        backup=tmp_path / "backups",
    )
    old = paths.releases / "0.3.10"
    (old / "backend").mkdir(parents=True)
    paths.current.symlink_to(old, target_is_directory=True)
    paths.state.mkdir()
    database = paths.state / "hoardarr.db"
    database.write_bytes(b"before")
    paths.config.mkdir()
    (paths.config / "hoardarr.env").write_text("before", encoding="utf-8")

    def runner(argv: list[str], _timeout: int) -> None:
        if argv[-1] == "migrate":
            database.write_bytes(b"partially migrated")
            (paths.config / "hoardarr.env").write_text("changed", encoding="utf-8")
            raise UpdateError("migration_failed", "migration failed")

    def extractor(_artifact: Path, stage: Path) -> None:
        (stage / "backend").mkdir()
        (stage / "manifest.json").write_text("{}", encoding="utf-8")
        (stage / "venv" / "bin").mkdir(parents=True)

    with pytest.raises(UpdateError) as exc:
        execute_update(
            _release(artifact), artifact, paths=paths, runner=runner, extractor=extractor
        )
    assert exc.value.code == "migration_failed"
    assert database.read_bytes() == b"before"
    assert (paths.config / "hoardarr.env").read_text(encoding="utf-8") == "before"
    assert paths.current.resolve() == old.resolve()
    assert not (paths.releases / "0.4.0").exists()


def test_release_extraction_rejects_traversal_and_links(tmp_path: Path) -> None:
    artifact = tmp_path / "release.tar"
    with tarfile.open(artifact, "w") as archive:
        entry = tarfile.TarInfo("../escape")
        entry.size = 1
        archive.addfile(entry, io.BytesIO(b"x"))
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(UpdateError) as exc:
        safe_extract_release(artifact, stage)
    assert exc.value.code == "release_archive_unsafe"
    assert not (tmp_path / "escape").exists()


def _manifest(payload: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "name": "netapp-shelf",
        "version": "1.0.0",
        "api": {"minimum": 1, "maximum": 1},
        "packages": ["sg3-utils"],
        "privileges": ["hardware.read", "storage.read", "ui.extend"],
        "database": {
            "minimum": "0004_runtime_features",
            "maximum": "0004_runtime_features",
        },
        "ui": [{"slot": "storage", "module": "ui/storage.js"}],
        "updates": {"minimum": "0.3.10", "maximum": "0.9.0"},
        "entrypoint": "provider.py",
        "payload_sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
    }


def test_local_addon_manifest_signature_payload_and_lifecycle(tmp_path: Path) -> None:
    payload = tmp_path / "addon.zip"
    payload.write_bytes(b"addon payload")
    manifest = normalize_manifest(_manifest(payload))
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    signature = private.sign(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())
    verified = verify_manifest(
        manifest,
        base64.b64encode(signature).decode(),
        base64.b64encode(public).decode(),
    )
    validate_payload(payload, str(verified["payload_sha256"]))
    assert lifecycle_state("installed", "enable") == "enabled"
    assert lifecycle_state("enabled", "disable") == "installed"
    assert lifecycle_state("installed", "remove") == "removed"


def test_addon_rejects_undeclared_privilege_and_tampered_payload(tmp_path: Path) -> None:
    payload = tmp_path / "addon.zip"
    payload.write_bytes(b"payload")
    manifest = _manifest(payload)
    manifest["privileges"] = ["root.everything"]
    with pytest.raises(AddonError) as exc:
        normalize_manifest(manifest)
    assert exc.value.code == "privilege_invalid"
    original_digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    payload.write_bytes(b"tampered")
    with pytest.raises(AddonError) as exc:
        validate_payload(payload, original_digest)
    assert exc.value.code == "payload_digest_mismatch"


def test_addon_upgrade_is_forward_only_and_requires_disable() -> None:
    validate_upgrade("1.0.0", "1.1.0", "installed")
    with pytest.raises(AddonError) as exc:
        validate_upgrade("1.0.0", "1.1.0", "enabled")
    assert exc.value.code == "addon_enabled"
    with pytest.raises(AddonError) as exc:
        validate_upgrade("1.1.0", "1.0.0", "installed")
    assert exc.value.code == "version_not_newer"


def test_addon_compatibility_and_runtime_are_enforced(tmp_path: Path) -> None:
    payload = tmp_path / "addon.zip"
    payload.write_bytes(b"payload")
    manifest = normalize_manifest(_manifest(payload))
    validate_compatibility(
        manifest,
        api_version=1,
        database_revision="0004_runtime_features",
        hoardarr_version="0.3.10",
        package_available=lambda name: name == "sg3-utils",
    )
    with pytest.raises(AddonError) as exc:
        validate_compatibility(
            manifest,
            api_version=2,
            database_revision="0004_runtime_features",
            hoardarr_version="0.3.10",
            package_available=lambda _name: True,
        )
    assert exc.value.code == "api_incompatible"
    install_path = tmp_path / "installed"
    install_path.mkdir()
    (install_path / "provider.py").write_text("print('provider')", encoding="utf-8")
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> None:
        calls.append(command)

    unit_root = tmp_path / "systemd"
    run_lifecycle_action(manifest, install_path, "enable", unit_root=unit_root, runner=runner)
    unit_path = unit_root / "hoardarr-addon-netapp-shelf.service"
    assert unit_path.is_file()
    content = unit_path.read_text(encoding="utf-8")
    assert "DynamicUser=yes" in content
    assert "PrivateDevices=no" in content
    assert "ReadOnlyPaths=/data /mnt/hoardarr /srv/hoardarr" in content
    assert "CapabilityBoundingSet=" in content
    assert calls[:2] == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "--now", "hoardarr-addon-netapp-shelf.service"],
    ]
    run_lifecycle_action(manifest, install_path, "disable", unit_root=unit_root, runner=runner)
    assert calls[-1] == [
        "systemctl",
        "disable",
        "--now",
        "hoardarr-addon-netapp-shelf.service",
    ]
    assert runtime_unit(manifest, install_path) == content
