from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from hoardarr.db.models import Base, HardwareSnapshot, Operation
from hoardarr.operations.service import document_hash
from hoardarr.wizard.service import (
    DEFAULT_LAYOUT,
    WizardConsentError,
    WizardValidationError,
    approve_plan,
    create_plan,
    create_wizard,
    plan_approval_status,
    refresh_plan_for_latest_discovery,
    update_step,
)
from hoardarr.wizard.storage_policy import StoragePolicyError, select_devices


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


def _usb_payload(*, capacity_bytes: int = 240_057_409_536) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source": {"kind": "sysfs"},
        "platform": {"manufacturer": "Oracle", "product": "storage-host"},
        "controllers": [],
        "disks": [
            {
                "id": "serial:cisco:ssd-240g:stp26501raw",
                "kernel_name": "sdb",
                "kernel_path": "/dev/sdb",
                "volatile_locator": True,
                "stable_identity": True,
                "identity": {
                    "serial": "STP26501RAW",
                    "wwn": None,
                    "eui64": None,
                    "nguid": None,
                },
                "vendor": "CISCO",
                "model": "SSD-240G V01",
                "capacity_bytes": capacity_bytes,
                "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
                "read_only": False,
                "connection": {
                    "transport": "usb",
                    "protocol": "uas",
                    "controller_address": "usb-1:2",
                    "enclosure_id": None,
                    "slot": None,
                },
                "partitions": [],
                "signature_scan": {
                    "status": "partial",
                    "reason": "Only active udev signatures were checked.",
                    "source": "udev",
                },
                "signatures": [],
            }
        ],
    }


def _direct_payload(*, drive_count: int = 4) -> dict[str, object]:
    payload = deepcopy(_usb_payload(capacity_bytes=8_000_000_000_000))
    disks = payload["disks"]
    assert isinstance(disks, list)
    template = disks[0]
    assert isinstance(template, dict)
    result: list[dict[str, object]] = []
    for index in range(drive_count):
        disk = deepcopy(template)
        serial = f"MEDIA{index + 1:04d}"
        disk["id"] = f"serial:seagate:media:{serial.casefold()}"
        disk["kernel_name"] = f"sd{chr(ord('b') + index)}"
        disk["kernel_path"] = f"/dev/sd{chr(ord('b') + index)}"
        disk["volatile_locator"] = False
        disk["identity"] = {
            "serial": serial,
            "wwn": f"naa.5000{index:012d}",
            "eui64": None,
            "nguid": None,
        }
        disk["vendor"] = "SEAGATE"
        disk["model"] = "MEDIA-HDD"
        disk["connection"] = {
            "transport": "sas",
            "protocol": "sas",
            "controller_address": "0000:18:00.0",
            "enclosure_id": "shelf-1",
            "slot": index + 1,
        }
        disk["signature_scan"] = {"status": "complete", "reason": None, "source": "wipefs"}
        result.append(disk)
    payload["disks"] = result
    return payload


def _snapshot(
    session: Session,
    payload: dict[str, object],
    *,
    captured_at: datetime | None = None,
) -> HardwareSnapshot:
    operation = Operation(
        kind="hardware.scan",
        status="succeeded",
        actor_type="user",
        actor_id="00000000-0000-0000-0000-000000000001",
        request_sha256=document_hash({}),
        request_json={},
    )
    session.add(operation)
    session.flush()
    snapshot = HardwareSnapshot(
        operation_id=operation.id,
        detector_schema_version=1,
        source="sysfs",
        payload_json=payload,
        sha256=document_hash(payload),
        captured_at=captured_at or datetime.now(UTC),
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def _storage_answers(**overrides: object) -> dict[str, object]:
    return {
        "selected_device_ids": ["serial:cisco:ssd-240g:stp26501raw"],
        "purpose": "media",
        "preserve_data": False,
        "portable_systems": ["windows"],
        "snapshots": False,
        "encryption": "none",
        **overrides,
    }


def _storage_plan(
    session: Session,
    *,
    mode: str = "guided",
    storage_answers: dict[str, object] | None = None,
) -> tuple[object, object, HardwareSnapshot]:
    snapshot = _snapshot(session, _usb_payload())
    wizard = create_wizard(session, mode=mode, hardware_snapshot_id=snapshot.id)
    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=0,
        step="storage",
        answers=storage_answers or _storage_answers(),
    )
    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=wizard.revision,
        step="layout",
        answers=DEFAULT_LAYOUT,
    )
    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=wizard.revision,
        step="applications",
        answers={},
    )
    plan = create_plan(session, wizard_id=wizard.id, expected_revision=wizard.revision)
    return wizard, plan, snapshot


def test_test_only_plan_runs_intake_checks_without_building_or_sharing_storage(
    session: Session,
) -> None:
    _wizard, plan, _snapshot_record = _storage_plan(
        session,
        storage_answers=_storage_answers(
            topology="test",
            preserve_data=True,
            portable_systems=["linux"],
            libraries=[],
            downloads={"torrents": False, "usenet": False},
            intake_tests={
                "identity": True,
                "full_surface_read": True,
                "smart_short": False,
                "smart_extended": False,
                "destructive_write_read": False,
            },
        ),
    )

    storage = plan.document_json["storage"]
    assert storage["topology"] == "test"
    assert [action["type"] for action in storage["actions"]] == [
        "drive.identity.verify",
        "drive.surface.read",
    ]
    assert storage["folders"] == []
    assert plan.document_json["actions"]["directories"] == []
    assert storage["risk"]["destructive"] is False


def test_test_only_plan_rejects_formatting_intent(session: Session) -> None:
    with pytest.raises(WizardValidationError, match="preserve"):
        _storage_plan(session, storage_answers=_storage_answers(topology="test"))


def test_running_system_disk_cannot_enter_a_storage_plan() -> None:
    payload = _usb_payload()
    payload["disks"][0]["system_disk"] = True  # type: ignore[index]

    with pytest.raises(StoragePolicyError, match="running operating system"):
        select_devices(payload, ["serial:cisco:ssd-240g:stp26501raw"])


def test_guided_usb_plan_derives_safe_windows_media_defaults(session: Session) -> None:
    wizard, plan, snapshot = _storage_plan(session)

    assert wizard.hardware_snapshot_id == snapshot.id
    assert plan.document_json["storage"]["topology"] == "individual"
    assert plan.document_json["storage"]["format"] == {
        "format_mode": "quick",
        "partition_table": "gpt",
        "alignment_bytes": 1_048_576,
        "filesystem": "ntfs",
        "allocation_unit_bytes": 4096,
        "linux_driver": "ntfs3",
        "mount_options": ["windows_names", "noatime"],
        "trim": {
            "mode": "conditional",
            "condition": "enable only when the complete USB/storage path reports discard support",
            "enabled": False,
            "status": "not_supported_or_not_reported",
            "path_evidence": [
                {
                    "device_id": "serial:cisco:ssd-240g:stp26501raw",
                    "granularity_bytes": None,
                    "max_bytes": None,
                    "supported": False,
                }
            ],
        },
        "reason": "Windows portability was selected",
    }
    storage = plan.document_json["storage"]
    assert storage["selected_devices"][0]["serial"] == "STP26501RAW"
    assert storage["selected_devices"][0]["logical_sector_bytes"] == 512
    assert storage["selected_devices"][0]["physical_sector_bytes"] == 4096
    assert storage["selected_devices"][0]["stable_identity"] is True
    assert storage["selected_devices"][0]["system_disk"] is False
    assert storage["selected_devices"][0]["partitions"] == []
    assert storage["selected_devices"][0]["signature_scan"] == {
        "status": "partial",
        "reason": "Only active udev signatures were checked.",
        "source": "udev",
    }
    assert storage["selected_devices"][0]["existing_data"]["status"] == "unknown"
    assert storage["risk"] == {
        "destructive": True,
        "heading": "ARE YOU SURE?",
        "message": (
            "The listed drives will be repartitioned and formatted. Existing data will be lost."
        ),
        "required_phrase": "I AGREE",
        "approval_required": True,
    }
    assert storage["folders"] == [
        "/data/media/Movies",
        "/data/media/TV",
        "/data/media/Music",
        "/data/media/Photos",
        "/data/media/Books",
        "/data/media/Audiobooks",
        "/data/downloads/torrents/incomplete",
        "/data/downloads/torrents/complete",
        "/data/downloads/usenet/incomplete",
        "/data/downloads/usenet/complete",
    ]
    assert storage["service_account"] == {
        "username": "media",
        "credential_mode": "generate",
    }
    assert storage["intake_tests"] == {
        "identity": True,
        "full_surface_read": True,
        "smart_short": False,
        "smart_extended": False,
        "destructive_write_read": False,
    }
    assert storage["file_access"]["permissions"] == {
        "administrators": "full_control",
        "media_applications": "modify",
        "media_users": "read_execute",
        "anonymous": "none",
    }
    assert storage["file_access"]["acl_model"] == "posix_acl"
    assert storage["file_access"]["client_presentation"] == ("windows_style_smb_permissions")
    assert storage["downloads"]["hardlinks"] == "same_filesystem_only"


def test_guided_default_is_linux_native_when_direct_attachment_is_not_requested(
    session: Session,
) -> None:
    answers = _storage_answers()
    answers.pop("portable_systems")
    wizard, plan, _snapshot_record = _storage_plan(session, storage_answers=answers)

    assert wizard.answers_json["storage"]["portable_systems"] == ["linux"]
    assert plan.document_json["storage"]["format"]["filesystem"] == "ext4"


def test_mergerfs_requires_an_explicit_existing_or_new_target(session: Session) -> None:
    snapshot = _snapshot(session, _usb_payload())
    wizard = create_wizard(session, mode="guided", hardware_snapshot_id=snapshot.id)

    with pytest.raises(WizardValidationError, match="choose an existing combined storage"):
        update_step(
            session,
            wizard_id=wizard.id,
            expected_revision=0,
            step="storage",
            answers=_storage_answers(topology="mergerfs"),
        )


def test_mergerfs_new_target_is_bound_into_the_storage_plan(session: Session) -> None:
    _wizard, plan, _snapshot_record = _storage_plan(
        session,
        storage_answers=_storage_answers(
            topology="mergerfs",
            mergerfs={
                "mode": "create",
                "name": "combined-storage",
                "mountpoint": "/mnt/combined-storage",
                "create_policy": "mfs",
                "search_policy": "ff",
            },
        ),
    )

    storage = plan.document_json["storage"]
    assert storage["mergerfs"] == {
        "mode": "create",
        "name": "combined-storage",
        "mountpoint": "/mnt/combined-storage",
        "create_policy": "mfs",
        "search_policy": "ff",
    }
    layout_action = next(
        action for action in storage["actions"] if action["type"] == "storage.layout.ensure"
    )
    assert layout_action["mergerfs"] == storage["mergerfs"]
    assert layout_action["requires_live_instance_revalidation"] is False


def test_existing_mergerfs_expansion_target_and_snapshot_are_bound_into_plan(
    session: Session,
) -> None:
    snapshot_sha256 = document_hash(_usb_payload())
    expansion = {
        "candidate_id": "a" * 24,
        "kind": "add_mergerfs_member",
        "storage_group_id": "11111111-1111-4111-8111-111111111111",
        "hardware_snapshot_sha256": snapshot_sha256,
        "disk_ids": ["serial:cisco:ssd-240g:stp26501raw"],
        "target": {
            "provider": "mergerfs",
            "instance_id": "mergerfs:0123456789abcdef",
            "mountpoint": "/mnt/combined-storage",
        },
    }
    _wizard, plan, _snapshot_record = _storage_plan(
        session,
        storage_answers=_storage_answers(
            topology="mergerfs",
            mergerfs={
                "mode": "existing",
                "instance_id": "mergerfs:0123456789abcdef",
                "name": "combined-storage",
                "mountpoint": "/mnt/combined-storage",
            },
            expansion=expansion,
        ),
    )
    storage = plan.document_json["storage"]
    assert storage["expansion"] == expansion
    assert storage["snapshot_binding"]["snapshot_sha256"] == snapshot_sha256
    assert storage["mergerfs"]["instance_id"] == expansion["target"]["instance_id"]


def test_existing_mergerfs_expansion_rejects_stale_assessment(session: Session) -> None:
    with pytest.raises(WizardValidationError, match="assessment is stale"):
        _storage_plan(
            session,
            storage_answers=_storage_answers(
                topology="mergerfs",
                mergerfs={
                    "mode": "existing",
                    "instance_id": "mergerfs:0123456789abcdef",
                    "name": "combined-storage",
                    "mountpoint": "/mnt/combined-storage",
                },
                expansion={
                    "candidate_id": "b" * 24,
                    "kind": "add_mergerfs_member",
                    "storage_group_id": None,
                    "hardware_snapshot_sha256": "b" * 64,
                    "disk_ids": ["serial:cisco:ssd-240g:stp26501raw"],
                    "target": {
                        "provider": "mergerfs",
                        "instance_id": "mergerfs:0123456789abcdef",
                        "mountpoint": "/mnt/combined-storage",
                    },
                },
            ),
        )


def test_mergerfs_mountpoint_rejects_fstab_control_characters(session: Session) -> None:
    snapshot = _snapshot(session, _usb_payload())
    wizard = create_wizard(session, mode="guided", hardware_snapshot_id=snapshot.id)

    with pytest.raises(WizardValidationError, match="absolute Linux path"):
        update_step(
            session,
            wizard_id=wizard.id,
            expected_revision=0,
            step="storage",
            answers=_storage_answers(
                topology="mergerfs",
                mergerfs={
                    "mode": "create",
                    "name": "combined-storage",
                    "mountpoint": "/mnt/media\n/dev/sdz /root none bind 0 0",
                    "create_policy": "mfs",
                    "search_policy": "ff",
                },
            ),
        )


def test_advanced_mixed_layout_is_immutable_and_does_not_format_raw_array_members(
    session: Session,
) -> None:
    payload = _usb_payload()
    template = payload["disks"][0]
    disks = []
    for index in range(6):
        disk = deepcopy(template)
        disk["id"] = f"serial:test:disk-{index}"
        disk["kernel_name"] = f"sd{chr(ord('b') + index)}"
        disk["kernel_path"] = f"/dev/sd{chr(ord('b') + index)}"
        disk["identity"]["serial"] = f"DISK-{index}"
        disk["connection"]["transport"] = "sas"
        disk["connection"]["protocol"] = "sas"
        disks.append(disk)
    payload["disks"] = disks
    snapshot = _snapshot(session, payload)
    wizard = create_wizard(session, mode="advanced", hardware_snapshot_id=snapshot.id)
    ids = [disk["id"] for disk in disks]
    components = []
    for number, members in enumerate((ids[:3], ids[3:]), start=1):
        components.append(
            {
                "topology": "zfs",
                "device_ids": members,
                "options": {
                    "name": f"media_{number}",
                    "vdevs": [{"type": "raidz1", "device_ids": members}],
                    "mountpoint": f"/mnt/hoardarr/media-{number}",
                },
            }
        )
    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=0,
        step="storage",
        answers=_storage_answers(
            selected_device_ids=ids,
            portable_systems=["linux"],
            topology="mixed",
            layout_options={
                "name": "media_all",
                "mountpoint": "/data",
                "components": components,
            },
        ),
    )
    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=wizard.revision,
        step="layout",
        answers=DEFAULT_LAYOUT,
    )
    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=wizard.revision,
        step="applications",
        answers={},
    )
    plan = create_plan(session, wizard_id=wizard.id, expected_revision=wizard.revision)
    storage = plan.document_json["storage"]
    assert storage["topology"] == "mixed"
    assert storage["layout_options"]["components"][0]["options"]["name"] == "media_1"
    assert not any(action["type"] == "filesystem.create" for action in storage["actions"])
    layout_action = storage["actions"][-1]
    assert layout_action["topology"] == "mixed"
    assert layout_action["layout_options"] == storage["layout_options"]
    assert layout_action["destructive"] is True


def test_advanced_format_controls_are_validated_and_written_to_the_plan(
    session: Session,
) -> None:
    format_options = {
        "filesystem": "xfs",
        "partition_table": "gpt",
        "alignment_bytes": 4_194_304,
        "allocation_unit_bytes": 65_536,
        "noatime": False,
        "trim_mode": "periodic",
    }
    wizard, plan, _snapshot_record = _storage_plan(
        session,
        mode="advanced",
        storage_answers=_storage_answers(format_options=format_options),
    )

    assert wizard.answers_json["storage"]["format_decision"] == {
        "format_mode": "quick",
        "partition_table": "gpt",
        "alignment_bytes": 4_194_304,
        "filesystem": "xfs",
        "allocation_unit_bytes": 65_536,
        "linux_driver": "xfs",
        "mount_options": [],
        "trim": {
            "mode": "periodic",
            "condition": "run scheduled fstrim only when discard is supported",
            "enabled": False,
            "status": "not_supported_or_not_reported",
            "path_evidence": [
                {
                    "device_id": "serial:cisco:ssd-240g:stp26501raw",
                    "granularity_bytes": None,
                    "max_bytes": None,
                    "supported": False,
                }
            ],
        },
        "reason": "Advanced disk format settings were selected",
    }
    assert (
        plan.document_json["storage"]["format"] == wizard.answers_json["storage"]["format_decision"]
    )


def test_trim_is_enabled_only_when_every_selected_path_reports_discard(session: Session) -> None:
    payload = _usb_payload()
    disk = payload["disks"][0]
    disk["discard"] = {
        "granularity_bytes": 4096,
        "max_bytes": 2_147_483_648,
        "zeroes_data": False,
    }
    snapshot = _snapshot(session, payload)
    wizard = create_wizard(session, hardware_snapshot_id=snapshot.id)
    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=0,
        step="storage",
        answers=_storage_answers(),
    )
    trim = wizard.answers_json["storage"]["format_decision"]["trim"]
    assert trim["enabled"] is True
    assert trim["status"] == "supported"
    assert trim["path_evidence"][0]["supported"] is True


def test_guided_rejects_advanced_format_overrides(session: Session) -> None:
    snapshot = _snapshot(session, _usb_payload())
    wizard = create_wizard(session, mode="guided", hardware_snapshot_id=snapshot.id)
    with pytest.raises(WizardValidationError, match="require Advanced mode"):
        update_step(
            session,
            wizard_id=wizard.id,
            expected_revision=0,
            step="storage",
            answers=_storage_answers(
                format_options={
                    "filesystem": "xfs",
                    "partition_table": "gpt",
                    "alignment_bytes": 1_048_576,
                    "allocation_unit_bytes": 4096,
                    "noatime": True,
                    "trim_mode": "conditional",
                }
            ),
        )


def test_selected_device_review_preserves_partitions_signatures_and_nullable_geometry(
    session: Session,
) -> None:
    payload = _usb_payload()
    disks = payload["disks"]
    assert isinstance(disks, list)
    disk = disks[0]
    assert isinstance(disk, dict)
    disk["sector_sizes"] = {"logical_bytes": None, "physical_bytes": None}
    disk["partitions"] = [
        {
            "kernel_name": "sdb1",
            "kernel_path": "/dev/sdb1",
            "number": 1,
            "size_bytes": 239_000_000_000,
            "start_bytes": 1_048_576,
            "filesystem": {"type": "ntfs", "uuid": "volume-guid"},
            "signatures": [{"type": "ntfs", "usage": "filesystem"}],
            "signature_scan": {
                "status": "partial",
                "reason": "Only mounted metadata was available.",
                "source": "udev",
            },
        }
    ]
    disk["signatures"] = [{"type": "gpt", "usage": "partition_table"}]
    snapshot = _snapshot(session, payload)
    wizard = create_wizard(session, hardware_snapshot_id=snapshot.id)
    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=0,
        step="storage",
        answers=_storage_answers(preserve_data=True, topology="individual"),
    )
    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=wizard.revision,
        step="layout",
        answers=DEFAULT_LAYOUT,
    )
    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=wizard.revision,
        step="applications",
        answers={},
    )
    plan = create_plan(session, wizard_id=wizard.id, expected_revision=wizard.revision)
    selected = plan.document_json["storage"]["selected_devices"][0]

    assert selected["stable_identity"] is True
    assert selected["logical_sector_bytes"] is None
    assert selected["physical_sector_bytes"] is None
    assert selected["partitions"] == disk["partitions"]
    assert selected["signatures"] == disk["signatures"]
    assert selected["signature_scan"] == disk["signature_scan"]
    assert selected["existing_data"]["status"] == "detected"


@pytest.mark.parametrize("scan_status", ["partial", "unavailable"])
def test_incomplete_signature_scan_never_claims_no_existing_data(
    session: Session,
    scan_status: str,
) -> None:
    payload = _usb_payload()
    disks = payload["disks"]
    assert isinstance(disks, list)
    disk = disks[0]
    assert isinstance(disk, dict)
    disk["sector_sizes"] = {"logical_bytes": None, "physical_bytes": None}
    disk["signature_scan"] = {
        "status": scan_status,
        "reason": "The media was not completely scanned.",
        "source": "test",
    }
    snapshot = _snapshot(session, payload)
    wizard = create_wizard(session, hardware_snapshot_id=snapshot.id)
    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=0,
        step="storage",
        answers=_storage_answers(preserve_data=True, topology="individual"),
    )
    assert any(
        warning["code"] == f"signature_scan_{scan_status}"
        for warning in wizard.answers_json["storage"]["warnings"]
    )

    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=wizard.revision,
        step="layout",
        answers=DEFAULT_LAYOUT,
    )
    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=wizard.revision,
        step="applications",
        answers={},
    )
    plan = create_plan(session, wizard_id=wizard.id, expected_revision=wizard.revision)
    selected = plan.document_json["storage"]["selected_devices"][0]
    assert selected["existing_data"]["status"] == "unknown"
    assert "cannot be ruled out" in selected["existing_data"]["reason"]


def test_unknown_sector_geometry_blocks_format_and_layout_creation(session: Session) -> None:
    payload = _usb_payload()
    disks = payload["disks"]
    assert isinstance(disks, list)
    disk = disks[0]
    assert isinstance(disk, dict)
    disk["sector_sizes"] = {"logical_bytes": None, "physical_bytes": None}
    snapshot = _snapshot(session, payload)

    formatting = create_wizard(session, hardware_snapshot_id=snapshot.id)
    with pytest.raises(
        WizardValidationError,
        match="cannot plan formatting: logical and physical sector geometry is unknown",
    ):
        update_step(
            session,
            wizard_id=formatting.id,
            expected_revision=0,
            step="storage",
            answers=_storage_answers(preserve_data=False),
        )

    array = create_wizard(session, mode="advanced", hardware_snapshot_id=snapshot.id)
    with pytest.raises(
        WizardValidationError,
        match="cannot plan zfs layout creation: logical and physical sector geometry is unknown",
    ):
        update_step(
            session,
            wizard_id=array.id,
            expected_revision=0,
            step="storage",
            answers=_storage_answers(
                preserve_data=True,
                topology="zfs",
                advanced_usb_acknowledgement="I AGREE",
            ),
        )


@pytest.mark.parametrize("sector_size", [520, 528])
def test_nonstandard_sector_media_requires_unimplemented_low_level_workflow(
    session: Session,
    sector_size: int,
) -> None:
    payload = _usb_payload()
    disks = payload["disks"]
    assert isinstance(disks, list)
    disk = disks[0]
    assert isinstance(disk, dict)
    disk["sector_sizes"] = {
        "logical_bytes": sector_size,
        "physical_bytes": sector_size,
    }
    snapshot = _snapshot(session, payload)
    wizard = create_wizard(session, hardware_snapshot_id=snapshot.id)

    with pytest.raises(
        WizardValidationError,
        match=(
            r"520/528-byte sector media.*explicit low-level sector reformat "
            r"workflow.*not implemented"
        ),
    ):
        update_step(
            session,
            wizard_id=wizard.id,
            expected_revision=0,
            step="storage",
            answers=_storage_answers(preserve_data=False),
        )
    assert "storage" not in wizard.answers_json


def test_read_only_device_is_rejected_even_for_preserved_individual_layout(
    session: Session,
) -> None:
    payload = _usb_payload()
    disks = payload["disks"]
    assert isinstance(disks, list)
    disk = disks[0]
    assert isinstance(disk, dict)
    disk["read_only"] = True
    snapshot = _snapshot(session, payload)
    wizard = create_wizard(session, hardware_snapshot_id=snapshot.id)

    with pytest.raises(
        WizardValidationError,
        match=r"drive is read-only.*cannot guarantee a no-write import/share",
    ):
        update_step(
            session,
            wizard_id=wizard.id,
            expected_revision=0,
            step="storage",
            answers=_storage_answers(preserve_data=True, topology="individual"),
        )


def test_download_folders_follow_visible_guided_selections(session: Session) -> None:
    _wizard, plan, _snapshot_record = _storage_plan(
        session,
        storage_answers=_storage_answers(downloads={"torrents": False, "usenet": True}),
    )
    storage = plan.document_json["storage"]
    assert storage["downloads"]["torrents"]["enabled"] is False
    assert storage["downloads"]["usenet"]["enabled"] is True
    assert all("/torrents/" not in path for path in storage["folders"])
    assert "/data/downloads/usenet/complete" in storage["folders"]


def test_top_level_directories_exactly_follow_selected_storage_folders(
    session: Session,
) -> None:
    _wizard, plan, _snapshot_record = _storage_plan(
        session,
        storage_answers=_storage_answers(
            libraries=["Movies", "TV"],
            custom_libraries=[
                {
                    "name": "Anime",
                    "content_type": "both",
                    "applications": ["radarr", "sonarr"],
                }
            ],
            downloads={"torrents": False, "usenet": True},
        ),
    )
    document = plan.document_json
    directory_actions = document["actions"]["directories"]

    assert [action["path"] for action in directory_actions] == document["storage"]["folders"]
    assert [action["path"] for action in directory_actions] == [
        "/data/media/Movies",
        "/data/media/TV",
        "/data/media/Anime",
        "/data/downloads/usenet/incomplete",
        "/data/downloads/usenet/complete",
    ]
    assert all(action["destructive"] is False for action in directory_actions)


@pytest.mark.parametrize("topology", ["zfs", "raid", "snapraid"])
def test_layout_creation_is_explicitly_destructive_even_when_data_is_preserved(
    session: Session,
    topology: str,
) -> None:
    answers = _storage_answers(topology=topology, preserve_data=True)
    if topology in {"zfs", "raid", "snapraid"}:
        answers["advanced_usb_acknowledgement"] = "I AGREE"
    _wizard, plan, _snapshot_record = _storage_plan(
        session,
        mode="advanced",
        storage_answers=answers,
    )
    storage = plan.document_json["storage"]
    layout_action = next(
        action for action in storage["actions"] if action["type"] == "storage.layout.ensure"
    )

    assert all(isinstance(action.get("destructive"), bool) for action in storage["actions"])
    assert layout_action["destructive"] is True
    assert storage["risk"]["destructive"] is True
    assert storage["risk"]["approval_required"] is True
    assert f"Creating the {topology} layout" in storage["risk"]["message"]


def test_preserved_cache_does_not_invent_a_destructive_layout_action(session: Session) -> None:
    _wizard, plan, _snapshot_record = _storage_plan(
        session,
        mode="advanced",
        storage_answers=_storage_answers(topology="cache", preserve_data=True),
    )
    storage = plan.document_json["storage"]
    layout_action = next(
        action for action in storage["actions"] if action["type"] == "storage.layout.ensure"
    )
    assert layout_action["destructive"] is False
    assert storage["risk"]["destructive"] is False
    assert storage["risk"]["approval_required"] is False


def test_non_destructive_plan_marks_every_storage_action_explicitly(session: Session) -> None:
    _wizard, plan, _snapshot_record = _storage_plan(
        session,
        storage_answers=_storage_answers(
            preserve_data=True,
            topology="individual",
            intake_tests={
                "identity": True,
                "full_surface_read": True,
                "smart_short": True,
            },
        ),
    )
    storage = plan.document_json["storage"]

    assert storage["risk"]["destructive"] is False
    assert storage["risk"]["approval_required"] is False
    assert storage["actions"]
    assert all(action["destructive"] is False for action in storage["actions"])


def test_import_layout_requires_preservation_and_never_formats(session: Session) -> None:
    _wizard, plan, _snapshot_record = _storage_plan(
        session,
        storage_answers=_storage_answers(topology="import", preserve_data=True),
    )
    storage = plan.document_json["storage"]
    assert storage["topology"] == "import"
    assert not any(
        action["type"] in {"disk.partition_table.create", "filesystem.create"}
        for action in storage["actions"]
    )
    snapshot = _snapshot(session, _usb_payload())
    wizard = create_wizard(session, hardware_snapshot_id=snapshot.id)
    with pytest.raises(WizardValidationError, match="Import requires preserving"):
        update_step(
            session,
            wizard_id=wizard.id,
            expected_revision=0,
            step="storage",
            answers=_storage_answers(topology="import", preserve_data=False),
        )


def test_intake_test_selection_is_planned_and_destructive_test_is_advanced_only(
    session: Session,
) -> None:
    snapshot = _snapshot(session, _usb_payload())
    guided = create_wizard(session, mode="guided", hardware_snapshot_id=snapshot.id)
    with pytest.raises(WizardValidationError, match="Advanced mode"):
        update_step(
            session,
            wizard_id=guided.id,
            expected_revision=0,
            step="storage",
            answers=_storage_answers(
                preserve_data=True,
                intake_tests={"destructive_write_read": True},
            ),
        )

    wizard, plan, _snapshot_record = _storage_plan(
        session,
        mode="advanced",
        storage_answers=_storage_answers(
            preserve_data=True,
            intake_tests={
                "identity": True,
                "full_surface_read": True,
                "destructive_write_read": True,
            },
        ),
    )
    storage = plan.document_json["storage"]
    assert wizard.mode == "advanced"
    assert storage["risk"]["destructive"] is True
    assert "write/read test" in storage["risk"]["message"]
    assert any(action["type"] == "drive.write_read.destructive" for action in storage["actions"])


def test_guided_forbids_arrays_and_advanced_usb_requires_exact_acknowledgement(
    session: Session,
) -> None:
    snapshot = _snapshot(session, _usb_payload())
    guided = create_wizard(session, mode="guided", hardware_snapshot_id=snapshot.id)
    with pytest.raises(
        WizardValidationError, match="USB drives cannot join an array in Guided mode"
    ):
        update_step(
            session,
            wizard_id=guided.id,
            expected_revision=0,
            step="storage",
            answers=_storage_answers(topology="zfs"),
        )

    advanced = create_wizard(session, mode="advanced", hardware_snapshot_id=snapshot.id)
    with pytest.raises(WizardValidationError, match="type I AGREE"):
        update_step(
            session,
            wizard_id=advanced.id,
            expected_revision=0,
            step="storage",
            answers=_storage_answers(
                topology="zfs",
                advanced_usb_acknowledgement="I Agree",
            ),
        )
    accepted = update_step(
        session,
        wizard_id=advanced.id,
        expected_revision=0,
        step="storage",
        answers=_storage_answers(
            topology="zfs",
            advanced_usb_acknowledgement="I AGREE",
        ),
    )
    assert accepted.answers_json["storage"]["warnings"][0]["code"] == ("advanced_usb_array_risk")
    accepted = update_step(
        session,
        wizard_id=advanced.id,
        expected_revision=accepted.revision,
        step="layout",
        answers=DEFAULT_LAYOUT,
    )
    accepted = update_step(
        session,
        wizard_id=advanced.id,
        expected_revision=accepted.revision,
        step="applications",
        answers={},
    )
    plan = create_plan(session, wizard_id=advanced.id, expected_revision=accepted.revision)
    assert plan.document_json["storage"]["warnings"][0]["code"] == ("advanced_usb_array_risk")


@pytest.mark.parametrize("topology", ["zfs", "snapraid"])
def test_guided_accepts_recommended_protected_layouts_on_direct_media_drives(
    session: Session,
    topology: str,
) -> None:
    payload = _direct_payload()
    disks = payload["disks"]
    assert isinstance(disks, list)
    selected_ids = [str(disk["id"]) for disk in disks if isinstance(disk, dict)]
    snapshot = _snapshot(session, payload)
    wizard = create_wizard(session, mode="guided", hardware_snapshot_id=snapshot.id)
    updated = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=0,
        step="storage",
        answers=_storage_answers(
            selected_device_ids=selected_ids,
            topology=topology,
            portable_systems=["linux"],
        ),
    )
    assert updated.answers_json["storage"]["topology"] == topology


@pytest.mark.parametrize("topology", ["raid", "mixed"])
def test_guided_keeps_expert_only_layouts_out_of_the_simple_path(
    session: Session,
    topology: str,
) -> None:
    payload = _direct_payload()
    disks = payload["disks"]
    assert isinstance(disks, list)
    snapshot = _snapshot(session, payload)
    wizard = create_wizard(session, mode="guided", hardware_snapshot_id=snapshot.id)
    with pytest.raises(WizardValidationError, match="Advanced mode"):
        update_step(
            session,
            wizard_id=wizard.id,
            expected_revision=0,
            step="storage",
            answers=_storage_answers(
                selected_device_ids=[str(disk["id"]) for disk in disks if isinstance(disk, dict)],
                topology=topology,
                portable_systems=["linux"],
            ),
        )


def test_custom_library_is_specific_and_anime_maps_to_arr_application(session: Session) -> None:
    snapshot = _snapshot(session, _usb_payload())
    wizard = create_wizard(session, hardware_snapshot_id=snapshot.id)
    with pytest.raises(WizardValidationError, match="Other is not a library"):
        update_step(
            session,
            wizard_id=wizard.id,
            expected_revision=0,
            step="storage",
            answers=_storage_answers(
                custom_libraries=[
                    {"name": "Other", "content_type": "series", "applications": ["sonarr"]}
                ]
            ),
        )
    updated = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=0,
        step="storage",
        answers=_storage_answers(
            custom_libraries=[
                {
                    "name": "Anime",
                    "content_type": "both",
                    "applications": ["radarr", "sonarr"],
                }
            ]
        ),
    )
    assert updated.answers_json["storage"]["custom_libraries"] == [
        {
            "name": "Anime",
            "content_type": "both",
            "applications": ["radarr", "sonarr"],
        }
    ]

    for unsafe_name in ("Movies", "../Anime", "Anime?", "CON"):
        with pytest.raises(WizardValidationError, match=r"folder name|built-in"):
            update_step(
                session,
                wizard_id=updated.id,
                expected_revision=updated.revision,
                step="storage",
                answers=_storage_answers(
                    custom_libraries=[
                        {
                            "name": unsafe_name,
                            "content_type": "series",
                            "applications": ["sonarr"],
                        }
                    ]
                ),
            )


def test_destructive_approval_is_hash_bound_and_wizard_edits_invalidate_it(
    session: Session,
) -> None:
    wizard, plan, snapshot = _storage_plan(session)
    storage = plan.document_json["storage"]
    with pytest.raises(WizardConsentError, match="Type I AGREE"):
        approve_plan(
            session,
            wizard_id=wizard.id,
            expected_revision=wizard.revision,
            plan_sha256=plan.sha256,
            hardware_snapshot_sha256=snapshot.sha256,
            selected_device_ids=storage["snapshot_binding"]["selected_device_ids"],
            confirmation="I Agree",
            actor_type="user",
            actor_id="00000000-0000-0000-0000-000000000001",
        )
    approval = approve_plan(
        session,
        wizard_id=wizard.id,
        expected_revision=wizard.revision,
        plan_sha256=plan.sha256,
        hardware_snapshot_sha256=snapshot.sha256,
        selected_device_ids=storage["snapshot_binding"]["selected_device_ids"],
        confirmation="I AGREE",
        actor_type="user",
        actor_id="00000000-0000-0000-0000-000000000001",
    )
    assert approval.plan_sha256 == plan.sha256
    assert plan_approval_status(session, wizard_id=wizard.id)["valid"] is True

    wizard = update_step(
        session,
        wizard_id=wizard.id,
        expected_revision=wizard.revision,
        step="applications",
        answers={},
    )
    assert wizard.plan_id is None
    assert plan_approval_status(session, wizard_id=wizard.id) == {
        "required": False,
        "valid": False,
        "reason": "plan_not_created",
    }


def test_new_hardware_snapshot_invalidates_destructive_approval(session: Session) -> None:
    wizard, plan, snapshot = _storage_plan(session)
    selected_ids = plan.document_json["storage"]["snapshot_binding"]["selected_device_ids"]
    approve_plan(
        session,
        wizard_id=wizard.id,
        expected_revision=wizard.revision,
        plan_sha256=plan.sha256,
        hardware_snapshot_sha256=snapshot.sha256,
        selected_device_ids=selected_ids,
        confirmation="I AGREE",
        actor_type="user",
        actor_id="00000000-0000-0000-0000-000000000001",
    )
    _snapshot(
        session,
        _usb_payload(capacity_bytes=239_000_000_000),
        captured_at=snapshot.captured_at + timedelta(seconds=1),
    )
    assert plan_approval_status(session, wizard_id=wizard.id) == {
        "required": True,
        "valid": False,
        "reason": "hardware_snapshot_changed",
    }


def test_refresh_plan_rebinds_stable_drives_to_latest_discovery(session: Session) -> None:
    wizard, original_plan, original_snapshot = _storage_plan(session)
    original_revision = wizard.revision
    latest = _snapshot(
        session,
        _usb_payload(capacity_bytes=239_000_000_000),
        captured_at=original_snapshot.captured_at + timedelta(seconds=1),
    )

    refreshed_wizard, refreshed_plan, bound_snapshot = refresh_plan_for_latest_discovery(
        session,
        wizard_id=wizard.id,
        expected_revision=wizard.revision,
    )

    assert bound_snapshot.id == latest.id
    assert refreshed_wizard.hardware_snapshot_id == latest.id
    assert refreshed_wizard.revision == original_revision + 1
    assert refreshed_wizard.status == "review"
    assert refreshed_plan.sha256 != original_plan.sha256
    selected_device = refreshed_plan.document_json["storage"]["selected_devices"][0]
    assert selected_device["capacity_bytes"] == 239_000_000_000
    assert plan_approval_status(session, wizard_id=wizard.id) == {
        "required": True,
        "valid": False,
        "reason": "not_approved",
        "required_phrase": "I AGREE",
    }
