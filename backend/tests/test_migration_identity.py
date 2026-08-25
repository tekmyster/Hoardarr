from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.db.models import (
    MetricAlert,
    MetricEntity,
    MetricRollup,
    MetricSample,
    Operation,
    PhysicalDisk,
    PhysicalDiskIdentityAlias,
    StorageBackend,
    StorageEntity,
    StorageGroup,
    StorageVolume,
)
from hoardarr.migration_identity import (
    IdentityManifest,
    IdentityMigrationError,
    database_sha256,
    load_identity_manifest,
    run_identity_migration,
)
from hoardarr.storage.groups import register_disk
from hoardarr.telemetry.samples import EntityReading, MetricReading
from hoardarr.telemetry.store import ingest

OLD = "wwn:vmware-source-0001"
NEW = "wwn:hyperv-target-0001"
CAPACITY = 8 * 1024**3
DIGEST = hashlib.sha256(b"accepted converted image").hexdigest()
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def evidence(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "capacity_bytes": CAPACITY,
        "logical_sector_bytes": 512,
        "physical_sector_bytes": 4096,
        "content_sha256": DIGEST,
        "filesystem_uuid": "a6c45fbf-fdd4-4b92-9e87-5c2fdbeb5ccb",
    }
    document.update(changes)
    return document


def manifest_document(**mapping_changes: Any) -> dict[str, Any]:
    mapping: dict[str, Any] = {
        "old_identity": OLD,
        "new_identity": NEW,
        "evidence_type": "ext4",
        "source": evidence(kernel_path="/dev/sda"),
        "target": evidence(kernel_path="/dev/sdb", serial="new-serial"),
    }
    mapping.update(mapping_changes)
    return {"schema_version": 1, "mappings": [mapping]}


def write_manifest(root: Path, document: dict[str, Any]) -> tuple[Path, IdentityManifest, str]:
    path = root / "identity-map.json"
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    loaded, digest = load_identity_manifest(path.resolve())
    return path, loaded, digest


def runtime(root: Path):  # type: ignore[no-untyped-def]
    database = root / "hoardarr.db"
    url = f"sqlite:///{database.as_posix()}"
    upgrade_database(url)
    engine = create_database_engine(url)
    return database, engine, create_session_factory(engine)


def seed_ext4(factory, *, metadata: dict[str, Any] | None = None) -> dict[str, str]:  # type: ignore[no-untyped-def]
    values = {
        "filesystem_uuid": evidence()["filesystem_uuid"],
        "migration_content_sha256": DIGEST,
        **(metadata or {}),
    }
    ids = {
        "disk": "disk-id",
        "group": "group-id",
        "backend": "backend-id",
        "entity": "entity-id",
        "metric": "metric-id",
        "volume": "volume-id",
        "sample": "1",
    }
    with factory() as session, session.begin():
        session.add_all(
            [
                PhysicalDisk(
                id=ids["disk"],
                stable_identity=OLD,
                kernel_path="/dev/sda",
                serial="old-serial",
                capacity_bytes=CAPACITY,
                logical_sector_bytes=512,
                physical_sector_bytes=4096,
                lifecycle_state="active",
                metadata_json=values,
                ),
                StorageEntity(
                id=ids["entity"],
                name="Media",
                stable_identity="filesystem:a6c45fbf",
                filesystem_uuid=evidence()["filesystem_uuid"],
                mountpoint="/srv/media",
                presentation_device="/dev/sda",
                capacity_bytes=CAPACITY,
                logical_sector_bytes=512,
                physical_sector_bytes=4096,
                config_json={"member_stable_identities": [OLD]},
                ),
                StorageGroup(
                id=ids["group"],
                name="Media",
                namespace_path="/srv/media",
                policy_json={"member_stable_identities": [OLD]},
                ),
                MetricEntity(
                    id=ids["metric"],
                    entity_type="drive",
                    stable_id=OLD,
                    display_name="Test disk",
                    labels_json={"identity": OLD},
                    topology_json={"disk": f"drive:{OLD}"},
                ),
            ]
        )
        session.flush()
        session.add(
            StorageBackend(
                id=ids["backend"],
                storage_group_id=ids["group"],
                storage_entity_id=ids["entity"],
                physical_disk_id=ids["disk"],
                stable_identity=f"disk:{OLD}",
                namespace_path="/srv/media/member-1",
                role="landing",
                lifecycle_state="preferred_write",
                config_json={
                    "member_stable_identities": [OLD],
                    "managed": {"physical_identity": OLD},
                },
            )
        )
        session.add(
            StorageVolume(
                id=ids["volume"],
                stable_identity="ext4:media",
                name="Media volume",
                provider="filesystem",
                resource_type="filesystem",
                provider_resource_id=evidence()["filesystem_uuid"],
                presentation="filesystem",
                parent_storage_entity_id=ids["entity"],
                config_json={"source_disk": OLD},
                device_path="/dev/sda",
            )
        )
        session.add(
            MetricSample(
                id=1,
                entity_id=ids["metric"],
                metric_id="io.read.iops",
                value=17.0,
                quality="available",
                source="fixture",
                collection_interval_seconds=5,
                raw=True,
                labels_json={},
                observed_at=NOW,
            )
        )
        session.add(
            MetricRollup(
                id=1,
                entity_id=ids["metric"],
                metric_id="io.read.iops",
                resolution="hour",
                period_start=NOW,
                sample_count=1,
                minimum=17.0,
                maximum=17.0,
                mean=17.0,
                first=17.0,
                last=17.0,
                quality="available",
            )
        )
        session.add(
            MetricAlert(
                id="alert-id",
                rule_id="fixture-rule",
                entity_id=ids["metric"],
                metric_id="io.read.iops",
                severity="warning",
                state="cleared",
                threshold_json={},
                topology_json={"disk": OLD},
                details_json={},
                started_at=NOW,
                last_seen_at=NOW,
                resolved_at=NOW,
            )
        )
        session.add(
            Operation(
                id="historical-operation",
                kind="storage.completed",
                status="succeeded",
                actor_type="session",
                actor_id="owner",
                request_sha256="0" * 64,
                request_json={"physical_identity": OLD},
            )
        )
    return ids


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 2, "mappings": []},
        {**manifest_document(), "unknown": True},
        {
            "schema_version": 1,
            "mappings": [
                manifest_document()["mappings"][0],
                {**manifest_document()["mappings"][0], "new_identity": "another"},
            ],
        },
        {
            "schema_version": 1,
            "mappings": [
                manifest_document()["mappings"][0],
                {**manifest_document()["mappings"][0], "old_identity": "another"},
            ],
        },
        manifest_document(target=evidence(content_sha256="0" * 64)),
        manifest_document(target=evidence(capacity_bytes=CAPACITY + 512)),
        manifest_document(target=evidence(filesystem_uuid="different")),
    ],
)
def test_manifest_rejects_unsupported_ambiguous_or_mismatched_input(
    document: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        IdentityManifest.model_validate(document)


def test_manifest_file_is_bounded_absolute_json_and_rejects_duplicate_keys(tmp_path: Path) -> None:
    relative = Path("identity-map.json")
    with pytest.raises(IdentityMigrationError, match="absolute"):
        load_identity_manifest(relative)
    wrong = tmp_path / "map.txt"
    wrong.write_text("{}", encoding="utf-8")
    with pytest.raises(IdentityMigrationError, match="JSON"):
        load_identity_manifest(wrong.resolve())
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(IdentityMigrationError, match="schema"):
        load_identity_manifest(duplicate.resolve())


def test_ext4_dry_run_is_read_only_and_apply_preserves_logical_and_history_ids(
    tmp_path: Path,
) -> None:
    database, engine, factory = runtime(tmp_path)
    ids = seed_ext4(factory)
    engine.dispose()
    _, manifest, manifest_digest = write_manifest(tmp_path, manifest_document())
    before = database_sha256(database)
    engine = create_database_engine(f"sqlite:///{database.as_posix()}")
    factory = create_session_factory(engine)

    dry = run_identity_migration(
        factory,
        database_path=database,
        manifest=manifest,
        manifest_digest=manifest_digest,
        expected_database_sha256=before,
        apply=False,
    )
    engine.dispose()
    assert database_sha256(database) == before
    assert dry["status"] == "ready"
    assert dry["mapped_count"] == 1
    assert dry["preserved_logical_ids"]["storage_group_ids"] == [ids["group"]]

    engine = create_database_engine(f"sqlite:///{database.as_posix()}")
    factory = create_session_factory(engine)
    applied = run_identity_migration(
        factory,
        database_path=database,
        manifest=manifest,
        manifest_digest=manifest_digest,
        expected_database_sha256=before,
        apply=True,
    )
    assert applied["status"] == "applied"
    with factory() as session:
        disk = session.get(PhysicalDisk, ids["disk"])
        backend = session.get(StorageBackend, ids["backend"])
        storage = session.get(StorageEntity, ids["entity"])
        volume = session.get(StorageVolume, ids["volume"])
        metric = session.get(MetricEntity, ids["metric"])
        assert disk is not None and disk.stable_identity == NEW
        assert disk.kernel_path == "/dev/sdb" and disk.serial == "new-serial"
        assert backend is not None and backend.stable_identity == f"disk:{NEW}"
        assert backend.config_json["member_stable_identities"] == [NEW]
        group = session.get(StorageGroup, ids["group"])
        assert group is not None and group.policy_json["member_stable_identities"] == [NEW]
        assert storage is not None and storage.id == ids["entity"]
        assert storage.config_json["member_stable_identities"] == [NEW]
        assert storage.presentation_device == "/dev/sdb"
        assert volume is not None and volume.config_json["source_disk"] == NEW
        assert volume.device_path == "/dev/sdb"
        assert metric is not None and metric.stable_id == NEW
        assert metric.labels_json["identity"] == NEW
        assert metric.topology_json["disk"] == f"drive:{NEW}"
        assert session.scalar(select(func.count()).select_from(MetricSample)) == 1
        assert session.scalar(select(func.count()).select_from(MetricRollup)) == 1
        sample = session.scalar(select(MetricSample))
        assert sample is not None and sample.entity_id == ids["metric"] and sample.value == 17.0
        assert session.get(MetricAlert, "alert-id").entity_id == ids["metric"]  # type: ignore[union-attr]
        assert session.get(StorageGroup, ids["group"]) is not None
        assert session.get(Operation, "historical-operation") is not None
        aliases = list(session.scalars(select(PhysicalDiskIdentityAlias)))
        assert [(item.alias_identity, item.physical_disk_id) for item in aliases] == [
            (OLD, ids["disk"])
        ]
    with factory() as session, session.begin():
        rediscovered, created = register_disk(
            session,
            {
                "stable_identity": OLD,
                "kernel_path": "/dev/retired-observation",
                "capacity_bytes": CAPACITY,
            },
        )
        assert created is False and rediscovered.id == ids["disk"]
        assert rediscovered.stable_identity == NEW
        timestamp = NOW + timedelta(seconds=5)
        readings = [
            MetricReading(
                entity=EntityReading(
                    "drive",
                    stable_id,
                    "Test disk",
                    labels={"identity": stable_id},
                ),
                metric_id="io.read.iops",
                observed_at=timestamp,
                value=23,
                quality="available",
                source="alias continuity fixture",
                collection_interval_seconds=5,
            )
            for stable_id in (OLD, NEW)
        ]
        ingestion = ingest(session, readings)
        assert ingestion == {"inserted": 1, "duplicates": 1}
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(PhysicalDisk)) == 1
        assert session.scalar(select(func.count()).select_from(MetricEntity)) == 1
        metric = session.get(MetricEntity, ids["metric"])
        assert metric is not None and metric.labels_json["identity"] == NEW
        assert session.scalar(select(func.count()).select_from(MetricSample)) == 2
    engine.dispose()


def test_repeat_apply_is_deterministic_and_does_not_duplicate_aliases(tmp_path: Path) -> None:
    database, engine, factory = runtime(tmp_path)
    seed_ext4(factory)
    engine.dispose()
    _, manifest, manifest_digest = write_manifest(tmp_path, manifest_document())
    expected = database_sha256(database)
    engine = create_database_engine(f"sqlite:///{database.as_posix()}")
    factory = create_session_factory(engine)
    run_identity_migration(
        factory,
        database_path=database,
        manifest=manifest,
        manifest_digest=manifest_digest,
        expected_database_sha256=expected,
        apply=True,
    )
    engine.dispose()
    expected = database_sha256(database)
    engine = create_database_engine(f"sqlite:///{database.as_posix()}")
    factory = create_session_factory(engine)
    repeat = run_identity_migration(
        factory,
        database_path=database,
        manifest=manifest,
        manifest_digest=manifest_digest,
        expected_database_sha256=expected,
        apply=True,
    )
    assert repeat["mapped_count"] == 0
    assert repeat["already_applied_count"] == 1
    assert repeat["alias_changes"] == []
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(PhysicalDiskIdentityAlias)) == 1
        assert session.scalar(select(func.count()).select_from(MetricEntity)) == 1
    engine.dispose()


@pytest.mark.parametrize(
    ("metadata", "code"),
    [
        ({"system_device": True}, "protected_disk"),
        ({"system_disk": True}, "protected_disk"),
        ({"protected": True}, "protected_disk"),
        ({"mounted": True, "foreign": True}, "mounted_foreign_disk"),
        ({"active_use": True}, "disk_active"),
    ],
)
def test_apply_rejects_protected_foreign_and_active_sources(
    tmp_path: Path, metadata: dict[str, Any], code: str
) -> None:
    database, engine, factory = runtime(tmp_path)
    seed_ext4(factory, metadata=metadata)
    engine.dispose()
    _, manifest, digest = write_manifest(tmp_path, manifest_document())
    expected = database_sha256(database)
    engine = create_database_engine(f"sqlite:///{database.as_posix()}")
    factory = create_session_factory(engine)
    with pytest.raises(IdentityMigrationError) as caught:
        run_identity_migration(
            factory,
            database_path=database,
            manifest=manifest,
            manifest_digest=digest,
            expected_database_sha256=expected,
            apply=True,
        )
    assert caught.value.code == code
    with factory() as session:
        assert session.get(PhysicalDisk, "disk-id").stable_identity == OLD  # type: ignore[union-attr]
    engine.dispose()


def test_geometry_target_ownership_and_database_digest_fail_closed(tmp_path: Path) -> None:
    database, engine, factory = runtime(tmp_path)
    seed_ext4(factory)
    with factory() as session, session.begin():
        session.add(
            PhysicalDisk(
                id="other-disk",
                stable_identity=NEW,
                capacity_bytes=CAPACITY,
                logical_sector_bytes=512,
                physical_sector_bytes=4096,
            )
        )
    engine.dispose()
    _, manifest, digest = write_manifest(tmp_path, manifest_document())
    expected = database_sha256(database)
    engine = create_database_engine(f"sqlite:///{database.as_posix()}")
    factory = create_session_factory(engine)
    with pytest.raises(IdentityMigrationError) as owned:
        run_identity_migration(
            factory,
            database_path=database,
            manifest=manifest,
            manifest_digest=digest,
            expected_database_sha256=expected,
            apply=True,
        )
    assert owned.value.code == "target_identity_owned"
    with pytest.raises(IdentityMigrationError) as digest_error:
        run_identity_migration(
            factory,
            database_path=database,
            manifest=manifest,
            manifest_digest=digest,
            expected_database_sha256="0" * 64,
            apply=True,
        )
    assert digest_error.value.code == "database_precondition_failed"
    engine.dispose()


def test_transaction_drift_and_injected_failure_roll_back_every_surface(tmp_path: Path) -> None:
    database, engine, factory = runtime(tmp_path)
    seed_ext4(factory)
    engine.dispose()
    _, manifest, digest = write_manifest(tmp_path, manifest_document())
    expected = database_sha256(database)
    engine = create_database_engine(f"sqlite:///{database.as_posix()}")
    factory = create_session_factory(engine)

    def drift(_phase: str, session) -> None:  # type: ignore[no-untyped-def]
        disk = session.get(PhysicalDisk, "disk-id")
        disk.metadata_json = {**disk.metadata_json, "active_use": True}

    with pytest.raises(IdentityMigrationError) as drifted:
        run_identity_migration(
            factory,
            database_path=database,
            manifest=manifest,
            manifest_digest=digest,
            expected_database_sha256=expected,
            apply=True,
            failure_hook=drift,
        )
    assert drifted.value.code == "disk_active"
    with factory() as session:
        disk = session.get(PhysicalDisk, "disk-id")
        assert disk is not None and disk.stable_identity == OLD
        assert disk.metadata_json.get("active_use") is None
    engine.dispose()

    expected = database_sha256(database)
    engine = create_database_engine(f"sqlite:///{database.as_posix()}")
    factory = create_session_factory(engine)

    def fail_after_rebind(phase: str, _session) -> None:  # type: ignore[no-untyped-def]
        if phase == "after_rebind":
            raise RuntimeError("injected transaction failure")

    with pytest.raises(RuntimeError, match="injected"):
        run_identity_migration(
            factory,
            database_path=database,
            manifest=manifest,
            manifest_digest=digest,
            expected_database_sha256=expected,
            apply=True,
            failure_hook=fail_after_rebind,
        )
    with factory() as session:
        assert session.get(PhysicalDisk, "disk-id").stable_identity == OLD  # type: ignore[union-attr]
        assert session.scalar(select(func.count()).select_from(PhysicalDiskIdentityAlias)) == 0
        assert session.get(MetricEntity, "metric-id").stable_id == OLD  # type: ignore[union-attr]
    engine.dispose()


def test_zfs_and_complete_linux_md_evidence_are_supported() -> None:
    zfs = manifest_document(
        evidence_type="zfs",
        source=evidence(filesystem_uuid=None, zfs_pool_guid="12345", zfs_dataset_guid="67890"),
        target=evidence(filesystem_uuid=None, zfs_pool_guid="12345", zfs_dataset_guid="67890"),
    )
    assert IdentityManifest.model_validate(zfs).mappings[0].evidence_type == "zfs"

    first = manifest_document()["mappings"][0]
    md_evidence = evidence(
        filesystem_uuid=None,
        md_array_uuid="md-array",
        md_filesystem_uuid="md-filesystem",
        md_member_count=2,
    )
    md = {
        "schema_version": 1,
        "mappings": [
            {
                **first,
                "evidence_type": "linux_md",
                "source": copy.deepcopy(md_evidence),
                "target": copy.deepcopy(md_evidence),
            },
            {
                **first,
                "old_identity": "wwn:old-member-2",
                "new_identity": "wwn:new-member-2",
                "evidence_type": "linux_md",
                "source": copy.deepcopy(md_evidence),
                "target": copy.deepcopy(md_evidence),
            },
        ],
    }
    assert len(IdentityManifest.model_validate(md).mappings) == 2
    md["mappings"].pop()
    with pytest.raises(ValidationError, match="every Linux MD member"):
        IdentityManifest.model_validate(md)


def _seed_evidence_disk(
    factory, *, disk_id: str, identity: str, metadata: dict[str, Any]  # type: ignore[no-untyped-def]
) -> None:
    with factory() as session, session.begin():
        session.add(
            PhysicalDisk(
                id=disk_id,
                stable_identity=identity,
                capacity_bytes=CAPACITY,
                logical_sector_bytes=512,
                physical_sector_bytes=4096,
                lifecycle_state="active",
                metadata_json={"migration_content_sha256": DIGEST, **metadata},
            )
        )


def test_zfs_rebind_executes_with_guid_evidence(tmp_path: Path) -> None:
    database, engine, factory = runtime(tmp_path)
    _seed_evidence_disk(
        factory,
        disk_id="zfs-disk",
        identity=OLD,
        metadata={"zfs_pool_guid": "12345", "zfs_dataset_guid": "67890"},
    )
    engine.dispose()
    document = manifest_document(
        evidence_type="zfs",
        source=evidence(filesystem_uuid=None, zfs_pool_guid="12345", zfs_dataset_guid="67890"),
        target=evidence(filesystem_uuid=None, zfs_pool_guid="12345", zfs_dataset_guid="67890"),
    )
    _, manifest, digest = write_manifest(tmp_path, document)
    expected = database_sha256(database)
    engine = create_database_engine(f"sqlite:///{database.as_posix()}")
    factory = create_session_factory(engine)
    result = run_identity_migration(
        factory,
        database_path=database,
        manifest=manifest,
        manifest_digest=digest,
        expected_database_sha256=expected,
        apply=True,
    )
    assert result["mapped_count"] == 1
    with factory() as session:
        assert session.get(PhysicalDisk, "zfs-disk").stable_identity == NEW  # type: ignore[union-attr]
    engine.dispose()


def test_linux_md_rebind_executes_only_with_complete_member_set(tmp_path: Path) -> None:
    database, engine, factory = runtime(tmp_path)
    md_metadata = {
        "md_array_uuid": "md-array",
        "md_filesystem_uuid": "md-filesystem",
        "md_member_count": 2,
    }
    _seed_evidence_disk(factory, disk_id="md-1", identity=OLD, metadata=md_metadata)
    _seed_evidence_disk(
        factory,
        disk_id="md-2",
        identity="wwn:old-member-2",
        metadata=md_metadata,
    )
    engine.dispose()
    md_evidence = evidence(filesystem_uuid=None, **md_metadata)
    first = manifest_document()["mappings"][0]
    document = {
        "schema_version": 1,
        "mappings": [
            {
                **first,
                "evidence_type": "linux_md",
                "source": md_evidence,
                "target": md_evidence,
            },
            {
                **first,
                "old_identity": "wwn:old-member-2",
                "new_identity": "wwn:new-member-2",
                "evidence_type": "linux_md",
                "source": md_evidence,
                "target": md_evidence,
            },
        ],
    }
    _, manifest, digest = write_manifest(tmp_path, document)
    expected = database_sha256(database)
    engine = create_database_engine(f"sqlite:///{database.as_posix()}")
    factory = create_session_factory(engine)
    result = run_identity_migration(
        factory,
        database_path=database,
        manifest=manifest,
        manifest_digest=digest,
        expected_database_sha256=expected,
        apply=True,
    )
    assert result["mapped_count"] == 2
    with factory() as session:
        assert session.get(PhysicalDisk, "md-1").stable_identity == NEW  # type: ignore[union-attr]
        assert session.get(PhysicalDisk, "md-2").stable_identity == (  # type: ignore[union-attr]
            "wwn:new-member-2"
        )
        assert session.scalar(select(func.count()).select_from(PhysicalDiskIdentityAlias)) == 2
    engine.dispose()


def test_unmatched_source_and_stored_evidence_mismatch_are_rejected(tmp_path: Path) -> None:
    database, engine, factory = runtime(tmp_path)
    seed_ext4(factory)
    engine.dispose()
    missing = manifest_document(old_identity="wwn:not-managed")
    _, manifest, digest = write_manifest(tmp_path, missing)
    expected = database_sha256(database)
    engine = create_database_engine(f"sqlite:///{database.as_posix()}")
    factory = create_session_factory(engine)
    with pytest.raises(IdentityMigrationError) as unmatched:
        run_identity_migration(
            factory,
            database_path=database,
            manifest=manifest,
            manifest_digest=digest,
            expected_database_sha256=expected,
            apply=True,
        )
    assert unmatched.value.code == "source_identity_unmatched"
    with factory() as session, session.begin():
        disk = session.get(PhysicalDisk, "disk-id")
        disk.capacity_bytes = CAPACITY - 512
    engine.dispose()
    expected = database_sha256(database)
    _, manifest, digest = write_manifest(tmp_path, manifest_document())
    engine = create_database_engine(f"sqlite:///{database.as_posix()}")
    factory = create_session_factory(engine)
    with pytest.raises(IdentityMigrationError) as mismatch:
        run_identity_migration(
            factory,
            database_path=database,
            manifest=manifest,
            manifest_digest=digest,
            expected_database_sha256=expected,
            apply=True,
        )
    assert mismatch.value.code == "source_geometry_mismatch"
    engine.dispose()
