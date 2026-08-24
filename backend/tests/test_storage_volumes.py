from __future__ import annotations

from pathlib import Path

import pytest

from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.db.models import StorageEntity
from hoardarr.storage.volumes import (
    StorageVolumeError,
    canonical_volume_identity,
    register_volume,
    volume_capabilities,
    volume_documents,
)


@pytest.fixture
def volume_session(tmp_path: Path):  # type: ignore[no-untyped-def]
    database_url = f"sqlite:///{(tmp_path / 'volumes.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_volume_identity_is_provider_native_and_kernel_path_independent(volume_session) -> None:
    parent = StorageEntity(
        stable_identity="scsi:3600a098000000001",
        name="Media storage",
        storage_kind="block",
        provider="dm-multipath",
        presentation_device="/dev/mapper/media",
        capacity_bytes=8_000_000_000,
        logical_sector_bytes=512,
        physical_sector_bytes=4096,
        filesystem_uuid="11111111-2222-3333-4444-555555555555",
        mountpoint="/srv/media",
    )
    volume_session.add(parent)
    volume_session.flush()

    volume, created = register_volume(
        volume_session,
        {
            "provider": "zfs",
            "resource_type": "dataset",
            "provider_resource_id": "tank/media",
            "name": "Media",
            "presentation": "file",
            "parent_storage_entity_id": parent.id,
            "mountpoint": "/srv/media",
            "device_path": "/dev/zvol/old-kernel-path",
            "size_bytes": 4_000_000_000,
            "allocated_bytes": 1_250_000_000,
            "filesystem_type": "zfs",
            "config": {"recordsize": "1M", "compression": "zstd"},
        },
    )
    assert created is True
    assert volume.stable_identity == "zfs:dataset:tank/media"

    updated, created = register_volume(
        volume_session,
        {
            "provider": "zfs",
            "resource_type": "dataset",
            "provider_resource_id": "tank/media",
            "name": "Media library",
            "presentation": "file",
            "parent_storage_entity_id": parent.id,
            "mountpoint": "/srv/media",
            "device_path": "/dev/zvol/new-kernel-path",
            "size_bytes": 4_000_000_000,
            "allocated_bytes": 1_500_000_000,
            "filesystem_type": "zfs",
            "config": {"recordsize": "1M", "compression": "zstd"},
        },
    )
    assert created is False
    assert updated.id == volume.id
    assert updated.device_path == "/dev/zvol/new-kernel-path"
    assert updated.stable_identity == "zfs:dataset:tank/media"
    assert volume_documents(volume_session)[0]["allocated_bytes"] == 1_500_000_000
    assert volume_documents(volume_session)[0]["capabilities"]["snapshot"] == {
        "support": "supported",
        "availability": "not_reported",
        "source": "provider_baseline",
        "constraints": {},
    }
    assert volume_documents(volume_session)[0]["capabilities"]["size"]["availability"] == (
        "available"
    )
    assert volume_documents(volume_session)[0]["capabilities_detected_at"] is not None


@pytest.mark.parametrize(
    ("document", "error_code"),
    [
        (
            {
                "provider": "unknown",
                "resource_type": "filesystem",
                "provider_resource_id": "uuid-1",
                "name": "Bad provider",
                "presentation": "file",
            },
            "volume_provider_unsupported",
        ),
        (
            {
                "provider": "filesystem",
                "resource_type": "filesystem",
                "provider_resource_id": "uuid-1",
                "name": "Negative capacity",
                "presentation": "file",
                "size_bytes": -1,
            },
            "volume_capacity_invalid",
        ),
        (
            {
                "provider": "iscsi",
                "resource_type": "lun",
                "provider_resource_id": "iqn.2026-08.test:media/1",
                "stable_identity": "iscsi:lun:different",
                "name": "Identity mismatch",
                "presentation": "block",
            },
            "volume_identity_mismatch",
        ),
    ],
)
def test_volume_registration_rejects_unsupported_or_misleading_values(
    volume_session, document: dict[str, object], error_code: str
) -> None:
    with pytest.raises(StorageVolumeError) as raised:
        register_volume(volume_session, document)
    assert raised.value.code == error_code


def test_canonical_volume_identity_rejects_control_characters() -> None:
    with pytest.raises(StorageVolumeError, match="identity"):
        canonical_volume_identity("filesystem", "filesystem", "uuid\nunsafe")


def test_provider_capabilities_distinguish_support_from_runtime_availability() -> None:
    capabilities = volume_capabilities(
        "lvm",
        "logical_volume",
        {
            "thin_provisioning": {
                "support": "supported",
                "availability": "available",
                "constraints": {"pool": "vg0/thinpool", "maximum_bytes": 8_000_000_000},
            },
            "snapshot": {
                "support": "supported",
                "availability": "temporarily_unavailable",
                "constraints": {"reason": "thin pool metadata is read-only"},
            },
        },
    )
    assert capabilities["thin_provisioning"]["availability"] == "available"
    assert capabilities["snapshot"]["availability"] == "temporarily_unavailable"
    assert capabilities["replication"] == {
        "support": "unsupported",
        "availability": "unsupported",
        "source": "provider_baseline",
        "constraints": {},
    }


def test_provider_capabilities_cannot_override_incompatible_backend_semantics() -> None:
    with pytest.raises(StorageVolumeError) as raised:
        volume_capabilities(
            "filesystem",
            "filesystem",
            {"snapshot": {"support": "supported", "availability": "available"}},
        )
    assert raised.value.code == "volume_capability_conflict"


@pytest.mark.parametrize(
    ("provider", "resource_type"),
    [
        ("zfs", "dataset"),
        ("zfs", "zvol"),
        ("lvm", "logical_volume"),
        ("linux_md", "filesystem"),
    ],
)
def test_qos_is_honestly_unsupported_when_provider_has_no_volume_limit(
    provider: str, resource_type: str
) -> None:
    qos = volume_capabilities(provider, resource_type)["qos"]
    assert qos["support"] == "unsupported"
    assert qos["availability"] == "unsupported"
    assert qos["constraints"]["scope"] == "provider_volume"
    assert "does not expose" in str(qos["constraints"]["reason"])

    with pytest.raises(StorageVolumeError) as raised:
        volume_capabilities(
            provider,
            resource_type,
            {"qos": {"support": "supported", "availability": "available"}},
        )
    assert raised.value.code == "volume_capability_conflict"
