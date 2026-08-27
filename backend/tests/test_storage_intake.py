from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hoardarr.api.app import create_app
from hoardarr.auth.service import issue_setup_token
from hoardarr.core.config import Settings
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import migration_config, upgrade_database
from hoardarr.db.models import (
    Base,
    DriveIntakeDisposition,
    HardwareSnapshot,
    Operation,
    OperationEvent,
    PhysicalDisk,
    Plan,
    WizardSession,
)
from hoardarr.operations.service import document_hash
from hoardarr.operations.worker import (
    StorageExecution,
    WorkFailure,
    WorkItem,
    _execute_storage,
    _finalize_success,
    reconcile_completed_storage_state,
)
from hoardarr.storage.intake import (
    IntakeEvaluationError,
    current_assessment,
    disposition_history,
    evaluate_storage_admission,
    persist_completed_intake,
)
from hoardarr.wizard.service import DEFAULT_LAYOUT, create_plan, create_wizard, update_step
from hoardarr.wizard.storage_policy import device_review_document

DEVICE_ID = "serial:synthetic:drive-a"


@dataclass
class IntakeContext:
    operation: Operation
    plan: Plan
    snapshot: HardwareSnapshot
    disk: PhysicalDisk
    result: dict[str, Any]


def _engine() -> Any:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _raw_disk(
    *,
    device_id: str = DEVICE_ID,
    kernel_path: str = "/dev/synthetic-a",
    model: str = "SYNTHETIC-240",
    capacity: int = 240_000_000_000,
    existing_data: bool = False,
) -> dict[str, Any]:
    return {
        "id": device_id,
        "stable_identity": True,
        "system_disk": False,
        "kernel_path": kernel_path,
        "vendor": "SYNTHETIC",
        "model": model,
        "firmware_revision": "1.0-test",
        "identity": {
            "serial": "SYNTHETIC-SERIAL-A",
            "wwn": None,
            "eui64": None,
            "nguid": None,
        },
        "identity_evidence": {
            "scsi_vpd_page_83": {
                "quality": "synthetic-stable",
                "source": "fixture",
                "identity_conflict": False,
            }
        },
        "capacity_bytes": capacity,
        "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
        "connection": {
            "transport": "usb",
            "protocol": "uas",
            "controller_address": "synthetic-controller-a",
            "hub_id": None,
            "hub_port": None,
        },
        "read_only": False,
        "discard": {},
        "signatures": ["ext4"] if existing_data else [],
        "partitions": (
            [{"number": 1, "filesystem_type": "ext4", "size_bytes": capacity - 1_000_000}]
            if existing_data
            else []
        ),
        "signature_scan": {
            "status": "complete",
            "reason": None,
            "source": "synthetic-fixture",
        },
    }


def _action_result(action: Mapping[str, Any], selected: Mapping[str, Any]) -> dict[str, Any]:
    action_type = action["type"]
    common = {
        "schema_version": 1,
        "action_id": action["action_id"],
        "device_id": action["device_id"],
        "type": action_type,
        "outcome": "passed",
    }
    if action_type == "drive.identity.verify":
        return {
            **common,
            "code": "identity_verified",
            "evidence": {
                "kind": "immutable_device_revalidation",
                "stable_plan_identity": selected["id"],
                "current_revalidation": "matched_selected_device",
                "identity_facts": {"stable_identity": True},
            },
        }
    if action_type == "drive.surface.read":
        return {
            **common,
            "code": "full_surface_read_completed",
            "evidence": {
                "kind": "command_success",
                "mode": "read_only",
                "full_device_intended_coverage": True,
                "target_capacity_bytes": selected["capacity_bytes"],
                "command_profile": "badblocks_-sv_full_device",
                "command_success": True,
            },
        }
    if action_type in {"drive.smart.short", "drive.smart.extended"}:
        kind = "extended" if action_type.endswith("extended") else "short"
        return {
            **common,
            "code": "smart_self_test_passed",
            "message": "synthetic SMART success",
            "test_kind": kind,
            "started_at": 1.0,
            "finished_at": 2.0,
            "evidence": {
                "kind": "smart_self_test_result",
                "test_kind": kind,
                "command_success": True,
            },
        }
    raise AssertionError(action_type)


def _context(
    session: Session,
    *,
    suffix: str = "one",
    existing_data: bool = False,
    intended_use: str | None = None,
    include_smart: bool = False,
    raw: dict[str, Any] | None = None,
) -> IntakeContext:
    raw = deepcopy(raw or _raw_disk(existing_data=existing_data))
    scan_operation = Operation(
        id=f"scan-{suffix}",
        kind="hardware.scan",
        status="succeeded",
        actor_type="api_token",
        actor_id="synthetic-user",
        request_sha256=document_hash({}),
        request_json={},
        result_json={},
    )
    session.add(scan_operation)
    session.flush()
    snapshot_payload = {"schema_version": 1, "source": {"kind": "synthetic"}, "disks": [raw]}
    snapshot = HardwareSnapshot(
        id=f"snapshot-{suffix}",
        operation_id=scan_operation.id,
        detector_schema_version=1,
        source="synthetic",
        payload_json=snapshot_payload,
        sha256=document_hash(snapshot_payload),
    )
    session.add(snapshot)
    selected = device_review_document(raw, index=0)
    action_types = ["drive.identity.verify", "drive.surface.read"]
    if include_smart:
        action_types.append("drive.smart.short")
    actions = [
        {
            "action_id": f"test:{index}:{selected['id']}",
            "type": action_type,
            "device_id": selected["id"],
            "destructive": False,
        }
        for index, action_type in enumerate(action_types, start=1)
    ]
    storage = {
        "topology": "test",
        "selected_devices": [selected],
        "snapshot_binding": {
            "snapshot_id": snapshot.id,
            "snapshot_sha256": snapshot.sha256,
            "device_binding_sha256": document_hash([selected]),
            "selected_device_ids": [selected["id"]],
        },
        "intake_tests": {
            "identity": True,
            "full_surface_read": True,
            "smart_short": include_smart,
            "smart_extended": False,
            "destructive_write_read": False,
        },
        "actions": actions,
    }
    if intended_use is not None:
        storage["intended_use"] = intended_use
    document = {"apply_available": True, "blockers": [], "storage": storage}
    wizard = WizardSession(
        id=f"wizard-{suffix}",
        workflow="storage.add",
        status="applied",
        current_step="complete",
        revision=1,
        hardware_snapshot_id=snapshot.id,
    )
    plan = Plan(
        id=f"plan-{suffix}",
        wizard_session_id=wizard.id,
        revision=1,
        kind="storage.add",
        document_json=document,
        sha256=document_hash(document),
    )
    wizard.plan_id = plan.id
    request = {
        "schema_version": 1,
        "wizard_id": wizard.id,
        "wizard_revision": plan.revision,
        "plan_id": plan.id,
        "plan_sha256": plan.sha256,
    }
    operation = Operation(
        id=f"storage-{suffix}",
        kind="storage.apply",
        status="succeeded",
        actor_type="api_token",
        actor_id="synthetic-user",
        resource_type="wizard_session",
        resource_id=wizard.id,
        request_sha256=document_hash(request),
        request_json=request,
    )
    disk = session.scalar(
        select(PhysicalDisk).where(PhysicalDisk.stable_identity == selected["id"])
    )
    if disk is None:
        disk = PhysicalDisk(
            id=f"disk-{suffix}",
            stable_identity=str(selected["id"]),
            kernel_path=str(selected["kernel_path"]),
            vendor=str(selected["vendor"]),
            model=str(selected["model"]),
            capacity_bytes=int(selected["capacity_bytes"]),
            logical_sector_bytes=int(selected["logical_sector_bytes"]),
            physical_sector_bytes=int(selected["physical_sector_bytes"]),
            metadata_json={"transport": selected["transport"]},
        )
    session.add_all([disk, wizard])
    session.flush()
    session.add_all([plan, operation])
    session.flush()
    result = {
        "topology": "test",
        "mountpoint": None,
        "completed_actions": [action["action_id"] for action in actions],
        "notices": [],
        "action_results": [_action_result(action, selected) for action in actions],
        "replayed": False,
    }
    operation.result_json = result
    session.flush()
    return IntakeContext(operation, plan, snapshot, disk, result)


def _destination_document(context: IntakeContext) -> dict[str, Any]:
    storage = deepcopy(context.plan.document_json["storage"])
    storage["topology"] = "individual"
    storage["intended_use"] = "destination"
    return {"apply_available": True, "blockers": [], "storage": storage}


def _store_destination_operation(
    session: Session,
    context: IntakeContext,
    *,
    suffix: str,
    resume: bool = False,
) -> tuple[Plan, Operation]:
    document = _destination_document(context)
    wizard = context.plan.wizard_session_id
    stored_wizard = session.get(WizardSession, wizard)
    assert stored_wizard is not None
    stored_wizard.revision += 1
    stored_wizard.status = "review"
    plan = Plan(
        id=f"admission-plan-{suffix}",
        wizard_session_id=wizard,
        revision=stored_wizard.revision,
        kind="storage.add",
        document_json=document,
        sha256=document_hash(document),
    )
    stored_wizard.plan_id = plan.id
    request = {
        "schema_version": 1,
        "wizard_id": wizard,
        "wizard_revision": plan.revision,
        "plan_id": plan.id,
        "plan_sha256": plan.sha256,
    }
    operation = Operation(
        id=f"admission-operation-{suffix}",
        kind="storage.apply",
        status="queued",
        actor_type="api_token",
        actor_id="synthetic-user",
        resource_type="wizard_session",
        resource_id=wizard,
        request_sha256=document_hash(request),
        request_json=request,
        result_json={"resume_requested": True, "resume_attempt": 1} if resume else None,
    )
    session.add_all([plan, operation])
    session.flush()
    return plan, operation


def test_migration_0030_upgrades_downgrades_and_declares_constraints(tmp_path: Path) -> None:
    database = tmp_path / "intake-migration.db"
    url = f"sqlite:///{database.as_posix()}"
    upgrade_database(url)
    engine = create_database_engine(url)
    inspector = inspect(engine)
    assert "drive_intake_dispositions" in inspector.get_table_names()
    unique_names = {
        item["name"] for item in inspector.get_unique_constraints("drive_intake_dispositions")
    }
    assert unique_names == {"uq_drive_intake_operation_disk_policy"}
    check_names = {
        item["name"] for item in inspector.get_check_constraints("drive_intake_dispositions")
    }
    assert check_names == {"ck_drive_intake_disposition"}
    engine.dispose()
    command.downgrade(migration_config(url), "0029_user_active_state")
    engine = create_database_engine(url)
    assert "drive_intake_dispositions" not in inspect(engine).get_table_names()
    engine.dispose()


@pytest.mark.parametrize(
    ("existing_data", "intended_use", "expected"),
    [
        (False, "destination", "PASS"),
        (True, "existing_data_read_only_source", "SOURCE_ONLY"),
    ],
)
def test_exact_completed_policy_persists_scoped_history(
    existing_data: bool, intended_use: str, expected: str
) -> None:
    engine = _engine()
    with Session(engine) as session, session.begin():
        context = _context(
            session,
            existing_data=existing_data,
            intended_use=intended_use,
        )
        persisted = persist_completed_intake(
            session,
            operation=context.operation,
            plan=context.plan,
            execution_result=context.result,
        )
        assert persisted.created == 1
        record = persisted.records[0]
        assert record.disposition == expected
        assert record.hardware_snapshot_id == context.snapshot.id
        assert (
            record.device_binding_sha256
            == context.plan.document_json["storage"]["snapshot_binding"]["device_binding_sha256"]
        )
        assert record.stable_identity == DEVICE_ID
        assert record.device_fingerprint_sha256 == document_hash(record.device_fingerprint_json)
        public = disposition_history(session, physical_disk_id=context.disk.id)[0]
        assert public["disposition"] == expected
        assert "stable_identity" not in public
        assert "serial" not in str(public).casefold()


@pytest.mark.parametrize(
    ("case", "expected", "reason"),
    [
        ("smart-skipped", "UNSUPPORTED", "required_test_skipped"),
        ("smart-unsupported", "UNSUPPORTED", "required_test_unsupported"),
        ("missing-result", "INCOMPLETE", "required_test_result_missing"),
        ("partial", "INCOMPLETE", "required_test_not_completed"),
        ("failed", "FAIL", "required_test_failed"),
        ("failure-notice", "QUARANTINED", "executor_failure_notice"),
        ("identity-conflict", "QUARANTINED", "identity_conflict"),
        (
            "existing-destination",
            "QUARANTINED",
            "existing_data_detected_for_destination",
        ),
    ],
)
def test_non_pass_evaluations_are_deterministic(case: str, expected: str, reason: str) -> None:
    engine = _engine()
    raw = _raw_disk(existing_data=case == "existing-destination")
    if case == "identity-conflict":
        raw["identity_evidence"]["scsi_vpd_page_83"]["identity_conflict"] = True
    with Session(engine) as session, session.begin():
        context = _context(
            session,
            raw=raw,
            intended_use="destination",
            include_smart=case in {"smart-skipped", "smart-unsupported"},
        )
        target = context.result["action_results"][-1]
        if case in {"smart-skipped", "smart-unsupported"}:
            target["outcome"] = "skipped" if case == "smart-skipped" else "unsupported"
            target["code"] = "smart_self_test_unavailable"
            target["evidence"]["command_success"] = False
        elif case == "missing-result":
            context.result["action_results"].pop()
        elif case == "partial":
            context.result["completed_actions"].pop()
        elif case == "failed":
            target["outcome"] = "failed"
            target["code"] = "surface_read_failed"
        elif case == "failure-notice":
            context.result["notices"] = [
                {"action_id": target["action_id"], "code": "uncorrectable_io"}
            ]
        record = persist_completed_intake(
            session,
            operation=context.operation,
            plan=context.plan,
            execution_result=context.result,
        ).records[0]
        assert record.disposition == expected
        assert reason in record.reason_codes_json
        assert record.reason_codes_json == sorted(record.reason_codes_json)


@pytest.mark.parametrize(
    "case",
    [
        "wrong-snapshot",
        "newest-unbound",
        "plan-hash",
        "device-fingerprint",
        "duplicate-result",
        "unplanned-result",
        "result-topology",
        "duplicate-identity",
    ],
)
def test_binding_tamper_and_ambiguity_create_no_history(case: str) -> None:
    engine = _engine()
    with Session(engine) as session, session.begin():
        context = _context(session, intended_use="destination")
        if case == "wrong-snapshot":
            context.plan.document_json["storage"]["snapshot_binding"]["snapshot_id"] = "missing"
            context.plan.sha256 = document_hash(context.plan.document_json)
            context.operation.request_json["plan_sha256"] = context.plan.sha256
            context.operation.request_sha256 = document_hash(context.operation.request_json)
        elif case == "newest-unbound":
            newer = deepcopy(context.snapshot.payload_json)
            newer["disks"][0]["connection"]["hub_port"] = "newer-unbound-port"
            scan = Operation(
                id="newer-scan",
                kind="hardware.scan",
                status="succeeded",
                actor_type="api_token",
                actor_id="synthetic-user",
                request_sha256=document_hash({}),
                request_json={},
            )
            session.add(scan)
            session.add(
                HardwareSnapshot(
                    id="newer-unbound",
                    operation_id=scan.id,
                    detector_schema_version=1,
                    source="synthetic",
                    payload_json=newer,
                    sha256=document_hash(newer),
                )
            )
            session.flush()
            record = persist_completed_intake(
                session,
                operation=context.operation,
                plan=context.plan,
                execution_result=context.result,
            ).records[0]
            assert record.hardware_snapshot_id == context.snapshot.id
            assert record.disposition == "PASS"
            assessment = current_assessment(session, physical_disk_id=context.disk.id)
            assert assessment["current"] is False
            assert assessment["stale_reason_codes"] == ["device_fingerprint_changed"]
            return
        elif case == "plan-hash":
            context.plan.document_json["tampered"] = True
        elif case == "device-fingerprint":
            context.plan.document_json["storage"]["selected_devices"][0]["model"] = "ALTERED"
            context.plan.document_json["storage"]["snapshot_binding"]["device_binding_sha256"] = (
                document_hash(context.plan.document_json["storage"]["selected_devices"])
            )
            context.plan.sha256 = document_hash(context.plan.document_json)
            context.operation.request_json["plan_sha256"] = context.plan.sha256
            context.operation.request_sha256 = document_hash(context.operation.request_json)
        elif case == "duplicate-result":
            context.result["action_results"].append(deepcopy(context.result["action_results"][0]))
        elif case == "unplanned-result":
            context.result["action_results"][0]["action_id"] = "not-planned"
        elif case == "result-topology":
            context.result["topology"] = "individual"
        elif case == "duplicate-identity":
            context.snapshot.payload_json["disks"].append(
                deepcopy(context.snapshot.payload_json["disks"][0])
            )
            context.snapshot.sha256 = document_hash(context.snapshot.payload_json)
            context.plan.document_json["storage"]["snapshot_binding"]["snapshot_sha256"] = (
                context.snapshot.sha256
            )
            context.plan.sha256 = document_hash(context.plan.document_json)
            context.operation.request_json["plan_sha256"] = context.plan.sha256
            context.operation.request_sha256 = document_hash(context.operation.request_json)
        with pytest.raises(IntakeEvaluationError):
            persist_completed_intake(
                session,
                operation=context.operation,
                plan=context.plan,
                execution_result=context.result,
            )
        assert session.scalar(select(func.count()).select_from(DriveIntakeDisposition)) == 0


def test_replay_reconciliation_new_operation_and_hotplug_path_are_identity_bound() -> None:
    engine = _engine()
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        first = _context(session, suffix="first", intended_use="destination")
        disk_id = first.disk.id
        assert session.scalar(select(func.count()).select_from(DriveIntakeDisposition)) == 0
    assert reconcile_completed_storage_state(factory) == 1
    assert reconcile_completed_storage_state(factory) == 0
    with factory() as session, session.begin():
        second_raw = _raw_disk(kernel_path="/dev/reordered-hotplug")
        _context(
            session,
            suffix="second",
            raw=second_raw,
            intended_use="destination",
        )
    assert reconcile_completed_storage_state(factory) == 1
    with factory() as session:
        history = disposition_history(session, physical_disk_id=disk_id)
        assert len(history) == 2
        assert history[0]["current"] is True
        assert current_assessment(session, physical_disk_id=disk_id)["assessment"] == "PASS"


def test_non_test_topology_and_empty_history_never_imply_pass() -> None:
    engine = _engine()
    with Session(engine) as session, session.begin():
        context = _context(session, intended_use="destination")
        context.plan.document_json["storage"]["topology"] = "individual"
        context.plan.sha256 = document_hash(context.plan.document_json)
        context.operation.request_json["plan_sha256"] = context.plan.sha256
        context.operation.request_sha256 = document_hash(context.operation.request_json)
        assert (
            persist_completed_intake(
                session,
                operation=context.operation,
                plan=context.plan,
                execution_result={**context.result, "topology": "individual"},
            ).created
            == 0
        )
        assessment = current_assessment(session, physical_disk_id=context.disk.id)
        assert assessment == {
            "physical_disk_id": context.disk.id,
            "assessment": "NOT_TESTED",
            "current": True,
            "stale": False,
            "reason_codes": ["not_tested"],
            "record": None,
        }


def test_current_destination_pass_admits_while_test_topology_is_only_exempt() -> None:
    engine = _engine()
    with Session(engine) as session, session.begin():
        context = _context(session, intended_use="destination")
        exemption = evaluate_storage_admission(session, context.plan.document_json)
        assert exemption.allowed is True
        assert exemption.qualification_exempt is True
        assert exemption.admitted is False
        assert exemption.blockers == ()

        missing = evaluate_storage_admission(session, _destination_document(context))
        assert missing.allowed is False
        assert [item["code"] for item in missing.blockers] == ["drive_intake_admission_not_tested"]

        persist_completed_intake(
            session,
            operation=context.operation,
            plan=context.plan,
            execution_result=context.result,
        )
        admitted = evaluate_storage_admission(session, _destination_document(context))
        assert admitted.allowed is True
        assert admitted.admitted is True
        assert admitted.qualification_exempt is False
        assert admitted.blockers == ()


def test_plan_review_appends_current_pass_blocker() -> None:
    engine = _engine()
    with Session(engine) as session, session.begin():
        context = _context(session, intended_use="destination")
        wizard = create_wizard(session, hardware_snapshot_id=context.snapshot.id)
        wizard = update_step(
            session,
            wizard_id=wizard.id,
            expected_revision=0,
            step="storage",
            answers={
                "selected_device_ids": [DEVICE_ID],
                "topology": "individual",
                "purpose": "media",
                "preserve_data": False,
                "portable_systems": ["linux"],
                "snapshots": False,
                "encryption": "none",
            },
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
        assert plan.document_json["apply_available"] is False
        assert "drive_intake_admission_not_tested" in {
            item["code"] for item in plan.document_json["blockers"]
        }
        assert plan.sha256 == document_hash(plan.document_json)


@pytest.mark.parametrize(
    "disposition",
    ["FAIL", "QUARANTINED", "INCOMPLETE", "UNSUPPORTED", "SOURCE_ONLY"],
)
def test_every_latest_non_pass_disposition_blocks_destination(disposition: str) -> None:
    engine = _engine()
    with Session(engine) as session, session.begin():
        context = _context(session, intended_use="destination")
        record = persist_completed_intake(
            session,
            operation=context.operation,
            plan=context.plan,
            execution_result=context.result,
        ).records[0]
        record.disposition = disposition
        session.flush()
        assessment = evaluate_storage_admission(session, _destination_document(context))
        assert assessment.allowed is False
        assert [item["code"] for item in assessment.blockers] == [
            "drive_intake_admission_source_only"
            if disposition == "SOURCE_ONLY"
            else "drive_intake_admission_latest_not_pass"
        ]
        assert DEVICE_ID not in str(assessment.blockers)


def test_later_non_pass_supersedes_older_pass_and_history_tamper_blocks() -> None:
    engine = _engine()
    with Session(engine) as session, session.begin():
        first = _context(session, suffix="older-pass", intended_use="destination")
        persist_completed_intake(
            session,
            operation=first.operation,
            plan=first.plan,
            execution_result=first.result,
        )
        second = _context(session, suffix="newer-fail", intended_use="destination")
        second.result["action_results"][-1]["outcome"] = "failed"
        second.result["action_results"][-1]["code"] = "surface_read_failed"
        second.operation.result_json = second.result
        newer = persist_completed_intake(
            session,
            operation=second.operation,
            plan=second.plan,
            execution_result=second.result,
        ).records[0]
        assert newer.disposition == "FAIL"
        assessment = evaluate_storage_admission(session, _destination_document(second))
        assert [item["code"] for item in assessment.blockers] == [
            "drive_intake_admission_latest_not_pass"
        ]

    engine = _engine()
    with Session(engine) as session, session.begin():
        context = _context(session, intended_use="destination")
        record = persist_completed_intake(
            session,
            operation=context.operation,
            plan=context.plan,
            execution_result=context.result,
        ).records[0]
        record.device_fingerprint_json = {
            **record.device_fingerprint_json,
            "firmware": "tampered",
        }
        assessment = evaluate_storage_admission(session, _destination_document(context))
        assert [item["code"] for item in assessment.blockers] == [
            "drive_intake_admission_binding_mismatch"
        ]


@pytest.mark.parametrize(
    "change",
    ["selected_device", "device_hash", "snapshot_hash", "physical_disk", "source_result"],
)
def test_plan_disk_and_history_binding_tamper_fail_closed(change: str) -> None:
    engine = _engine()
    with Session(engine) as session, session.begin():
        context = _context(session, intended_use="destination")
        persist_completed_intake(
            session,
            operation=context.operation,
            plan=context.plan,
            execution_result=context.result,
        )
        document = _destination_document(context)
        if change == "selected_device":
            document["storage"]["selected_devices"][0]["model"] = "ALTERED"
        elif change == "device_hash":
            document["storage"]["snapshot_binding"]["device_binding_sha256"] = "0" * 64
        elif change == "snapshot_hash":
            document["storage"]["snapshot_binding"]["snapshot_sha256"] = "0" * 64
        elif change == "physical_disk":
            context.disk.stable_identity = "serial:synthetic:different"
        else:
            context.operation.result_json = {**context.result, "replayed": True}
        assessment = evaluate_storage_admission(session, document)
        assert assessment.allowed is False
        assert assessment.blockers
        serialized = str(assessment.blockers)
        assert DEVICE_ID not in serialized
        assert "SYNTHETIC-SERIAL-A" not in serialized


@pytest.mark.parametrize(
    "change",
    [
        "policy",
        "firmware",
        "capacity",
        "geometry",
        "controller",
        "hub",
        "port",
        "signature",
        "kernel_path",
        "duplicate_current",
    ],
)
def test_policy_and_current_hardware_revalidation_are_exact(change: str) -> None:
    engine = _engine()
    with Session(engine) as session, session.begin():
        context = _context(session, intended_use="destination")
        persist_completed_intake(
            session,
            operation=context.operation,
            plan=context.plan,
            execution_result=context.result,
        )
        document = _destination_document(context)
        if change == "policy":
            document["storage"]["intake_tests"]["smart_short"] = True
        else:
            payload = deepcopy(context.snapshot.payload_json)
            if change == "firmware":
                payload["disks"][0]["firmware_revision"] = "2.0-test"
            elif change == "capacity":
                payload["disks"][0]["capacity_bytes"] += 4096
            elif change == "geometry":
                payload["disks"][0]["sector_sizes"]["physical_bytes"] = 8192
            elif change == "controller":
                payload["disks"][0]["connection"]["controller_address"] = "changed"
            elif change == "hub":
                payload["disks"][0]["connection"]["hub_id"] = "synthetic-hub"
            elif change == "port":
                payload["disks"][0]["connection"]["hub_port"] = "7"
            elif change == "signature":
                payload["disks"][0]["signatures"] = ["zfs_member"]
            elif change == "kernel_path":
                payload["disks"][0]["kernel_path"] = "/dev/reordered"
            else:
                payload["disks"].append(deepcopy(payload["disks"][0]))
            scan = Operation(
                id=f"current-scan-{change}",
                kind="hardware.scan",
                status="succeeded",
                actor_type="api_token",
                actor_id="synthetic-user",
                request_sha256=document_hash({}),
                request_json={},
            )
            session.add(scan)
            session.flush()
            session.add(
                HardwareSnapshot(
                    id=f"current-snapshot-{change}",
                    operation_id=scan.id,
                    detector_schema_version=1,
                    source="synthetic",
                    payload_json=payload,
                    sha256=document_hash(payload),
                )
            )
            session.flush()
        assessment = evaluate_storage_admission(session, document)
        if change == "kernel_path":
            assert assessment.allowed is True
        else:
            assert assessment.allowed is False


def test_worker_resume_recomputes_admission_and_calls_applier_zero_times(
    tmp_path: Path,
) -> None:
    engine = _engine()
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        context = _context(session, intended_use="destination")
        plan, operation = _store_destination_operation(
            session, context, suffix="resume-reject", resume=True
        )
        item = WorkItem(
            operation_id=operation.id,
            kind=operation.kind,
            resource_type=operation.resource_type,
            resource_id=operation.resource_id,
            request=deepcopy(operation.request_json),
        )
    calls: list[dict[str, Any]] = []

    def applier(_socket: object, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {}

    settings = Settings(
        environment="test",
        database_url="sqlite+pysqlite:///:memory:",
        secret_key_file=tmp_path / "secret.key",
        secure_cookies=False,
        hardware_detector=tmp_path / "synthetic-detector.py",
    )
    with pytest.raises(WorkFailure) as rejected:
        _execute_storage(factory, item, settings, applier)
    assert rejected.value.code == "storage_drive_admission_blocked"
    assert calls == []
    with factory() as session:
        stored_plan = session.get(Plan, plan.id)
        assert stored_plan is not None
        assert stored_plan.document_json["apply_available"] is True
        assert stored_plan.document_json["blockers"] == []


def test_worker_finalization_persists_disposition_in_completion_transaction() -> None:
    engine = _engine()
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        context = _context(session, intended_use="destination")
        context.operation.status = "running"
        context.operation.result_json = None
        context.operation.lease_owner = "synthetic-worker"
        operation_id = context.operation.id
        plan_id = context.plan.id
        plan_sha256 = context.plan.sha256
        wizard_id = context.plan.wizard_session_id
        request = deepcopy(context.operation.request_json)
        result = deepcopy(context.result)
    _finalize_success(
        factory,
        WorkItem(
            operation_id=operation_id,
            kind="storage.apply",
            resource_type="wizard_session",
            resource_id=wizard_id,
            request=request,
        ),
        "synthetic-worker",
        StorageExecution(
            wizard_id=wizard_id,
            plan_id=plan_id,
            plan_sha256=plan_sha256,
            result=result,
        ),
    )
    with factory() as session:
        operation = session.get(Operation, operation_id)
        record = session.scalar(select(DriveIntakeDisposition))
        assert operation is not None and operation.status == "succeeded"
        assert operation.result_json == result
        assert record is not None and record.disposition == "PASS"


def test_startup_reconciliation_records_one_durable_binding_error() -> None:
    engine = _engine()
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        context = _context(session, intended_use="destination")
        operation_id = context.operation.id
        tampered = deepcopy(context.plan.document_json)
        tampered["tampered_after_completion"] = True
        context.plan.document_json = tampered
    assert reconcile_completed_storage_state(factory) == 0
    assert reconcile_completed_storage_state(factory) == 0
    with factory() as session:
        events = list(
            session.scalars(
                select(OperationEvent).where(
                    OperationEvent.operation_id == operation_id,
                    OperationEvent.event_type == "drive_intake_reconciliation_deferred",
                )
            )
        )
        assert len(events) == 1
        assert events[0].data_json == {"code": "drive_intake_plan_binding_invalid"}
        assert session.scalar(select(func.count()).select_from(DriveIntakeDisposition)) == 0


def test_heterogeneous_models_and_capacities_remain_separate_histories() -> None:
    engine = _engine()
    with Session(engine) as session, session.begin():
        first = _context(session, suffix="small", intended_use="destination")
        second = _context(
            session,
            suffix="large",
            raw=_raw_disk(
                device_id="serial:synthetic:drive-b",
                model="SYNTHETIC-960",
                capacity=960_000_000_000,
            ),
            intended_use="destination",
        )
        for context in (first, second):
            persist_completed_intake(
                session,
                operation=context.operation,
                plan=context.plan,
                execution_result=context.result,
            )
        records = list(session.scalars(select(DriveIntakeDisposition)))
        assert {record.disposition for record in records} == {"PASS"}
        assert {record.device_fingerprint_json["capacity_bytes"] for record in records} == {
            240_000_000_000,
            960_000_000_000,
        }
        assert len({record.physical_disk_id for record in records}) == 2


def test_database_constraint_rejects_unknown_disposition() -> None:
    engine = _engine()
    with Session(engine) as session, session.begin():
        context = _context(session, intended_use="destination")
        record = persist_completed_intake(
            session,
            operation=context.operation,
            plan=context.plan,
            execution_result=context.result,
        ).records[0]
        record_id = record.id
    with Session(engine) as session, pytest.raises(IntegrityError):
        session.execute(
            update(DriveIntakeDisposition)
            .where(DriveIntakeDisposition.id == record_id)
            .values(disposition="HEALTHY")
        )
        session.commit()


def test_authenticated_read_api_is_redacted_and_empty_history_is_not_tested(
    tmp_path: Path,
) -> None:
    database = tmp_path / "intake-api.db"
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{database.as_posix()}",
        secret_key_file=tmp_path / "secret.key",
        secure_cookies=False,
        hardware_detector=tmp_path / "synthetic-detector.py",
    )
    upgrade_database(settings.database_url)
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        setup_token = issue_setup_token(session)
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        credential_field = "".join(("pass", "word"))
        claim = client.post(
            "/api/v1/setup/claim",
            headers={"Origin": "http://testserver"},
            json={
                "token": setup_token,
                "username": "owner",
                credential_field: "a-long-synthetic-credential-value",
            },
        )
        assert claim.status_code == 201
        csrf = claim.json()["csrf_token"]
        state_headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf}
        read_token = client.post(
            "/api/v1/auth/tokens",
            headers=state_headers,
            json={"name": "intake-read", "scopes": ["read"]},
        ).json()["secret"]
        operate_token = client.post(
            "/api/v1/auth/tokens",
            headers=state_headers,
            json={"name": "intake-operate", "scopes": ["operate"]},
        ).json()["secret"]
        with app.state.session_factory() as session, session.begin():
            context = _context(session, intended_use="destination")
            persist_completed_intake(
                session,
                operation=context.operation,
                plan=context.plan,
                execution_result=context.result,
            )
            untested = PhysicalDisk(
                id="untested-disk",
                stable_identity="serial:synthetic:untested",
                metadata_json={},
            )
            session.add(untested)

        history_path = f"/api/v1/storage/disks/{context.disk.id}/intake-history"
        client.cookies.clear()
        assert client.get(history_path).status_code == 401
        forbidden = client.get(history_path, headers={"Authorization": f"Bearer {operate_token}"})
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] == "insufficient_scope"
        allowed = client.get(history_path, headers={"Authorization": f"Bearer {read_token}"})
        assert allowed.status_code == 200
        assert allowed.json()["items"][0]["disposition"] == "PASS"
        assert DEVICE_ID not in allowed.text
        assert "SYNTHETIC-SERIAL-A" not in allowed.text
        assessment = client.get(
            "/api/v1/storage/disks/untested-disk/intake-assessment",
            headers={"Authorization": f"Bearer {read_token}"},
        )
        assert assessment.status_code == 200
        assert assessment.json()["assessment"] == "NOT_TESTED"
        assert assessment.json()["reason_codes"] == ["not_tested"]


def test_authenticated_apply_recomputes_admission_before_queue(tmp_path: Path) -> None:
    database = tmp_path / "intake-admission-api.db"
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{database.as_posix()}",
        secret_key_file=tmp_path / "secret.key",
        secure_cookies=False,
        hardware_detector=tmp_path / "synthetic-detector.py",
    )
    upgrade_database(settings.database_url)
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        setup_token = issue_setup_token(session)
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        credential_field = "".join(("pass", "word"))
        claim = client.post(
            "/api/v1/setup/claim",
            headers={"Origin": "http://testserver"},
            json={
                "token": setup_token,
                "username": "owner",
                credential_field: "a-long-synthetic-credential-value",
            },
        )
        csrf = claim.json()["csrf_token"]
        with app.state.session_factory() as session, session.begin():
            context = _context(session, intended_use="destination")
            stored_wizard = session.get(WizardSession, context.plan.wizard_session_id)
            assert stored_wizard is not None
            document = _destination_document(context)
            current_plan = Plan(
                id="api-admission-plan",
                wizard_session_id=stored_wizard.id,
                revision=stored_wizard.revision + 1,
                kind="storage.add",
                document_json=document,
                sha256=document_hash(document),
            )
            stored_wizard.revision = current_plan.revision
            stored_wizard.status = "review"
            stored_wizard.plan_id = current_plan.id
            session.add(current_plan)
            operation_count = session.scalar(select(func.count()).select_from(Operation))

        response = client.post(
            f"/api/v1/wizards/{stored_wizard.id}/apply",
            headers={
                "Origin": "http://testserver",
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "synthetic-admission-rejection",
            },
        )
        assert response.status_code == 409
        assert response.json()["code"] == "storage_drive_admission_blocked"
        assert response.json()["errors"] == [
            {
                "code": "drive_intake_admission_not_tested",
                "message": "The selected drive has no completed intake disposition.",
                "physical_disk_id": context.disk.id,
            }
        ]
        assert DEVICE_ID not in response.text
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(Operation)) == operation_count
