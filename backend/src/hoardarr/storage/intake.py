from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hoardarr.db.models import (
    DriveIntakeDisposition,
    HardwareSnapshot,
    Operation,
    PhysicalDisk,
    Plan,
    utc_now,
)
from hoardarr.operations.service import canonical_json, document_hash
from hoardarr.wizard.storage_policy import StoragePolicyError, device_review_document

DISPOSITIONS = frozenset(
    {"PASS", "FAIL", "QUARANTINED", "INCOMPLETE", "UNSUPPORTED", "SOURCE_ONLY"}
)
TEST_ACTION_TYPES = {
    "identity": "drive.identity.verify",
    "full_surface_read": "drive.surface.read",
    "smart_short": "drive.smart.short",
    "smart_extended": "drive.smart.extended",
    "destructive_write_read": "drive.write_read.destructive",
}
POLICY_NAME = "plan_bound_drive_intake"
POLICY_VERSION = 1
MAX_FINGERPRINT_BYTES = 128 * 1024


class IntakeEvaluationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class IntakePersistenceResult:
    records: tuple[DriveIntakeDisposition, ...]
    created: int


@dataclass(frozen=True)
class IntakeAdmissionResult:
    admitted: bool
    qualification_exempt: bool
    blockers: tuple[dict[str, str], ...]

    @property
    def allowed(self) -> bool:
        return self.admitted or self.qualification_exempt


def validate_disposition(value: str) -> str:
    if value not in DISPOSITIONS:
        raise ValueError("invalid drive intake disposition")
    return value


def _mapping(value: Any, *, code: str, message: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IntakeEvaluationError(code, message)
    return value


def _exact_plan_and_operation(operation: Operation, plan: Plan) -> None:
    request = operation.request_json
    if (
        operation.kind != "storage.apply"
        or not isinstance(request, dict)
        or document_hash(request) != operation.request_sha256
        or request.get("plan_id") != plan.id
        or request.get("plan_sha256") != plan.sha256
        or request.get("wizard_revision") != plan.revision
        or document_hash(plan.document_json) != plan.sha256
    ):
        raise IntakeEvaluationError(
            "drive_intake_plan_binding_invalid",
            "The completed operation is not bound to the immutable intake plan.",
        )


def _bound_snapshot(
    session: Session, storage: Mapping[str, Any]
) -> tuple[HardwareSnapshot, list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    binding = _mapping(
        storage.get("snapshot_binding"),
        code="drive_intake_snapshot_binding_invalid",
        message="The intake plan has no immutable hardware snapshot binding.",
    )
    snapshot_id = binding.get("snapshot_id")
    snapshot_sha256 = binding.get("snapshot_sha256")
    selected_ids = binding.get("selected_device_ids")
    selected = storage.get("selected_devices")
    if (
        not isinstance(snapshot_id, str)
        or not isinstance(snapshot_sha256, str)
        or not isinstance(selected_ids, list)
        or not selected_ids
        or any(not isinstance(item, str) for item in selected_ids)
        or len(selected_ids) != len(set(selected_ids))
        or not isinstance(selected, list)
        or len(selected) != len(selected_ids)
        or any(not isinstance(item, Mapping) for item in selected)
        or [item.get("id") for item in selected] != selected_ids
        or binding.get("device_binding_sha256") != document_hash(selected)
    ):
        raise IntakeEvaluationError(
            "drive_intake_device_binding_invalid",
            "The intake plan's selected-device binding is invalid.",
        )
    snapshot = session.get(HardwareSnapshot, snapshot_id)
    if (
        snapshot is None
        or snapshot.sha256 != snapshot_sha256
        or document_hash(snapshot.payload_json) != snapshot_sha256
    ):
        raise IntakeEvaluationError(
            "drive_intake_snapshot_binding_invalid",
            "The exact hardware snapshot bound to the intake plan is unavailable or altered.",
        )
    raw_disks = snapshot.payload_json.get("disks")
    if not isinstance(raw_disks, list):
        raise IntakeEvaluationError(
            "drive_intake_snapshot_invalid",
            "The bound hardware snapshot has no valid disk observations.",
        )
    raw_by_id: dict[str, tuple[int, Mapping[str, Any]]] = {}
    duplicate_ids: set[str] = set()
    for index, raw in enumerate(raw_disks[:4096]):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("id"), str):
            continue
        identity = str(raw["id"])
        if identity in raw_by_id:
            duplicate_ids.add(identity)
        else:
            raw_by_id[identity] = (index, raw)
    if duplicate_ids.intersection(str(item) for item in selected_ids):
        raise IntakeEvaluationError(
            "drive_intake_identity_ambiguous",
            "A selected stable identity is duplicated in the bound snapshot.",
        )
    observed: list[Mapping[str, Any]] = []
    for selected_device in selected:
        identity = str(selected_device["id"])
        indexed = raw_by_id.get(identity)
        if indexed is None:
            raise IntakeEvaluationError(
                "drive_intake_device_missing",
                "A selected drive is absent from the bound snapshot.",
            )
        index, raw = indexed
        try:
            expected = device_review_document(raw, index=index)
        except StoragePolicyError as exc:
            raise IntakeEvaluationError(
                "drive_intake_snapshot_invalid",
                "The bound disk observation cannot be validated.",
            ) from exc
        if expected != dict(selected_device):
            raise IntakeEvaluationError(
                "drive_intake_fingerprint_drift",
                "The plan-bound device observation differs from its hardware snapshot.",
            )
        observed.append(raw)
    return snapshot, list(selected), observed


def _fingerprint(selected: Mapping[str, Any], raw: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    identity = raw.get("identity") if isinstance(raw.get("identity"), Mapping) else {}
    sectors = raw.get("sector_sizes") if isinstance(raw.get("sector_sizes"), Mapping) else {}
    connection = raw.get("connection") if isinstance(raw.get("connection"), Mapping) else {}
    connection_facts = {
        str(key): value
        for key, value in connection.items()
        if str(key) not in {"kernel_path", "device_node", "discovery_index", "disk_number"}
    }
    fingerprint = {
        "schema_version": 1,
        "stable_identity": raw.get("id"),
        "stable_identity_reported": raw.get("stable_identity") is True,
        "identity": {name: identity.get(name) for name in ("serial", "wwn", "eui64", "nguid")},
        "identity_evidence": raw.get("identity_evidence"),
        "vendor": raw.get("vendor"),
        "model": raw.get("model"),
        "firmware": raw.get("firmware_revision", raw.get("firmware", identity.get("firmware"))),
        "capacity_bytes": raw.get("capacity_bytes"),
        "sector_geometry": {
            "logical_bytes": sectors.get("logical_bytes"),
            "physical_bytes": sectors.get("physical_bytes"),
        },
        # The physical connection document may retain optional controller/HBA,
        # enclosure, hub and port facts. Kernel paths and discovery order are
        # deliberately excluded because they are not durable identity.
        "connection": connection_facts,
        "filesystem_evidence": {
            "partitions": selected.get("partitions"),
            "signatures": selected.get("signatures"),
            "signature_scan": selected.get("signature_scan"),
            "existing_data": selected.get("existing_data"),
        },
    }
    encoded = canonical_json(fingerprint)
    if len(encoded) > MAX_FINGERPRINT_BYTES:
        raise IntakeEvaluationError(
            "drive_intake_fingerprint_too_large",
            "The bound device fingerprint exceeds its safety limit.",
        )
    return fingerprint, document_hash(fingerprint)


def _identity_reason_codes(raw: Mapping[str, Any]) -> set[str]:
    reasons: set[str] = set()
    if raw.get("stable_identity") is not True:
        reasons.add("identity_unstable")

    def visit(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                name = str(key).casefold()
                next_path = (*path, name)
                if item not in (None, False, "", [], {}) and "conflict" in name:
                    reasons.add("identity_conflict")
                if item not in (None, False, "", [], {}) and "duplicate" in name:
                    reasons.add("identity_duplicate")
                if item not in (None, False, "", [], {}) and "ambiguous" in name:
                    reasons.add("identity_ambiguous")
                if len(next_path) <= 8:
                    visit(item, next_path)
        elif isinstance(value, list) and len(path) <= 8:
            for item in value[:64]:
                visit(item, path)

    visit(raw.get("identity_evidence"))
    return reasons


def _intended_use(storage: Mapping[str, Any], selected: Mapping[str, Any]) -> str:
    explicit = storage.get("intended_use")
    if explicit in {"destination", "existing_data_read_only_source"}:
        return str(explicit)
    existing = selected.get("existing_data")
    status = existing.get("status") if isinstance(existing, Mapping) else None
    if status == "detected":
        return "existing_data_read_only_source"
    if status == "not_detected":
        return "destination"
    return "undetermined"


def _policy(storage: Mapping[str, Any], intended_use: str) -> tuple[list[str], dict[str, Any], str]:
    configured = storage.get("intake_tests")
    actions = storage.get("actions")
    if (
        not isinstance(configured, Mapping)
        or set(configured) != set(TEST_ACTION_TYPES)
        or any(not isinstance(configured[name], bool) for name in TEST_ACTION_TYPES)
        or not isinstance(actions, list)
        or any(not isinstance(item, Mapping) for item in actions)
    ):
        raise IntakeEvaluationError(
            "drive_intake_policy_invalid", "The plan's intake policy is invalid."
        )
    required = [action_type for name, action_type in TEST_ACTION_TYPES.items() if configured[name]]
    if not required:
        raise IntakeEvaluationError(
            "drive_intake_policy_invalid", "The plan selects no drive intake tests."
        )
    policy = {
        "name": POLICY_NAME,
        "version": POLICY_VERSION,
        "intended_use": intended_use,
        "required_action_types": required,
    }
    return required, policy, document_hash(policy)


def _validate_pass_evidence(result: Mapping[str, Any], selected: Mapping[str, Any]) -> bool:
    action_type = result.get("type")
    evidence = result.get("evidence")
    if result.get("schema_version") != 1 or not isinstance(evidence, Mapping):
        return False
    if action_type == "drive.identity.verify":
        return (
            result.get("code") == "identity_verified"
            and evidence.get("kind") == "immutable_device_revalidation"
            and evidence.get("stable_plan_identity") == selected.get("id")
            and evidence.get("current_revalidation") == "matched_selected_device"
        )
    if action_type in {"drive.surface.read", "drive.write_read.destructive"}:
        destructive = action_type == "drive.write_read.destructive"
        return (
            result.get("code")
            == (
                "destructive_write_read_completed" if destructive else "full_surface_read_completed"
            )
            and evidence.get("kind") == "command_success"
            and evidence.get("mode") == ("destructive_write_read" if destructive else "read_only")
            and evidence.get("full_device_intended_coverage") is True
            and evidence.get("target_capacity_bytes") == selected.get("capacity_bytes")
            and evidence.get("command_profile")
            == ("badblocks_-wsv_full_device" if destructive else "badblocks_-sv_full_device")
            and evidence.get("command_success") is True
        )
    if action_type in {"drive.smart.short", "drive.smart.extended"}:
        kind = "extended" if action_type == "drive.smart.extended" else "short"
        return (
            result.get("code") == "smart_self_test_passed"
            and evidence.get("kind") == "smart_self_test_result"
            and evidence.get("test_kind") == kind
            and evidence.get("command_success") is True
        )
    return False


def _evaluate_device(
    *,
    storage: Mapping[str, Any],
    selected: Mapping[str, Any],
    raw: Mapping[str, Any],
    execution_result: Mapping[str, Any],
) -> tuple[str, list[str], list[str], list[dict[str, Any]], dict[str, Any], str, str]:
    intended_use = _intended_use(storage, selected)
    required_types, _policy_document, policy_sha256 = _policy(storage, intended_use)
    device_id = selected.get("id")
    actions = [
        item
        for item in storage["actions"]
        if item.get("device_id") == device_id and item.get("type") in TEST_ACTION_TYPES.values()
    ]
    action_types = Counter(str(item.get("type")) for item in actions)
    if any(action_types[action_type] != 1 for action_type in required_types) or any(
        count != 1 or action_type not in required_types
        for action_type, count in action_types.items()
    ):
        raise IntakeEvaluationError(
            "drive_intake_policy_binding_invalid",
            "The plan actions do not exactly implement its selected intake policy.",
        )
    action_by_id = {str(item.get("action_id")): item for item in actions}
    if len(action_by_id) != len(actions) or any(not key or key == "None" for key in action_by_id):
        raise IntakeEvaluationError(
            "drive_intake_action_binding_invalid", "The plan has ambiguous intake action IDs."
        )
    completed = execution_result.get("completed_actions")
    results = execution_result.get("action_results")
    notices = execution_result.get("notices", [])
    if (
        not isinstance(completed, list)
        or any(not isinstance(item, str) for item in completed)
        or not isinstance(results, list)
        or any(not isinstance(item, Mapping) for item in results)
        or not isinstance(notices, list)
        or any(not isinstance(item, Mapping) for item in notices)
    ):
        raise IntakeEvaluationError(
            "drive_intake_execution_result_invalid",
            "The completed executor result has an invalid evidence shape.",
        )
    completed_counts = Counter(completed)
    results_by_action: dict[str, list[Mapping[str, Any]]] = {}
    for item in results:
        action_id = item.get("action_id")
        if not isinstance(action_id, str) or action_id not in action_by_id:
            # Results for another selected device are valid, but an unplanned result is not.
            other_planned = any(
                action.get("action_id") == action_id for action in storage["actions"]
            )
            if not other_planned:
                raise IntakeEvaluationError(
                    "drive_intake_execution_result_tampered",
                    "The executor result contains an unplanned action result.",
                )
            continue
        results_by_action.setdefault(action_id, []).append(item)

    reasons = _identity_reason_codes(raw)
    existing = selected.get("existing_data")
    existing_status = existing.get("status") if isinstance(existing, Mapping) else None
    if intended_use == "undetermined":
        reasons.add("intended_use_undetermined")
    elif intended_use == "destination":
        if existing_status == "detected":
            reasons.add("existing_data_detected_for_destination")
        elif existing_status != "not_detected":
            reasons.add("existing_data_status_incomplete")
    else:
        if existing_status != "detected":
            reasons.add("source_data_not_detected")
        if any(item.get("destructive") is True for item in actions):
            reasons.add("source_policy_contains_destructive_test")

    result_documents: list[dict[str, Any]] = []
    for action_id, action in action_by_id.items():
        matches = results_by_action.get(action_id, [])
        if completed_counts[action_id] == 0:
            reasons.add("required_test_not_completed")
        elif completed_counts[action_id] != 1:
            reasons.add("required_test_completion_ambiguous")
        if not matches:
            reasons.add("required_test_result_missing")
            continue
        if len(matches) != 1:
            raise IntakeEvaluationError(
                "drive_intake_execution_result_tampered",
                "The executor result duplicates a required action result.",
            )
        item = matches[0]
        if (
            item.get("device_id") != device_id
            or item.get("type") != action.get("type")
            or item.get("schema_version") != 1
            or not isinstance(item.get("code"), str)
            or not isinstance(item.get("evidence"), Mapping)
        ):
            reasons.add("required_test_evidence_ambiguous")
        outcome = item.get("outcome")
        if outcome == "passed":
            if not _validate_pass_evidence(item, selected):
                reasons.add("required_test_evidence_incomplete")
        elif outcome == "failed":
            reasons.add("required_test_failed")
        elif outcome == "skipped":
            reasons.add("required_test_skipped")
        elif outcome == "unsupported":
            reasons.add("required_test_unsupported")
        else:
            reasons.add("required_test_outcome_ambiguous")
        result_documents.append(
            {
                **dict(item),
                "result_sha256": document_hash(item),
                "evidence_sha256": document_hash(item["evidence"])
                if isinstance(item.get("evidence"), Mapping)
                else None,
            }
        )

    skip_notice_codes = {"smart_self_test_unavailable"}
    if any(
        notice.get("code") not in skip_notice_codes
        and (
            notice.get("device_id") in {None, device_id} or notice.get("action_id") in action_by_id
        )
        for notice in notices
    ):
        reasons.add("executor_failure_notice")
    blockers = execution_result.get("blockers")
    if blockers not in (None, []):
        reasons.add("executor_blocker_reported")

    fail_reasons = {"required_test_failed"}
    quarantine_reasons = {
        "identity_unstable",
        "identity_conflict",
        "identity_duplicate",
        "identity_ambiguous",
        "existing_data_detected_for_destination",
        "source_policy_contains_destructive_test",
        "executor_failure_notice",
        "executor_blocker_reported",
    }
    unsupported_reasons = {"required_test_skipped", "required_test_unsupported"}
    if reasons & fail_reasons:
        disposition = "FAIL"
    elif reasons & quarantine_reasons:
        disposition = "QUARANTINED"
    elif reasons & unsupported_reasons:
        disposition = "UNSUPPORTED"
    elif reasons:
        disposition = "INCOMPLETE"
    elif intended_use == "existing_data_read_only_source":
        disposition = "SOURCE_ONLY"
        reasons.add("source_policy_requirements_satisfied")
    else:
        disposition = "PASS"
        reasons.add("policy_requirements_satisfied")
    validate_disposition(disposition)
    fingerprint, fingerprint_sha256 = _fingerprint(selected, raw)
    return (
        disposition,
        sorted(reasons),
        required_types,
        result_documents,
        fingerprint,
        fingerprint_sha256,
        policy_sha256,
    )


def persist_completed_intake(
    session: Session,
    *,
    operation: Operation,
    plan: Plan,
    execution_result: Mapping[str, Any],
) -> IntakePersistenceResult:
    """Evaluate and append exact test-only history without touching storage."""

    storage_value = plan.document_json.get("storage")
    if not isinstance(storage_value, Mapping) or storage_value.get("topology") != "test":
        return IntakePersistenceResult(records=(), created=0)
    _exact_plan_and_operation(operation, plan)
    storage = storage_value
    if execution_result.get("topology") != "test":
        raise IntakeEvaluationError(
            "drive_intake_result_topology_invalid",
            "A test-only plan returned a non-test execution result.",
        )
    snapshot, selected_devices, raw_devices = _bound_snapshot(session, storage)
    execution_sha256 = document_hash(execution_result)
    now = utc_now()
    completed_at = operation.updated_at if operation.status == "succeeded" else now
    records: list[DriveIntakeDisposition] = []
    created = 0
    for selected_device, raw in zip(selected_devices, raw_devices, strict=True):
        stable_identity = str(selected_device["id"])
        disk = session.scalar(
            select(PhysicalDisk).where(PhysicalDisk.stable_identity == stable_identity)
        )
        if disk is None:
            raise IntakeEvaluationError(
                "drive_intake_physical_disk_missing",
                "The plan-bound stable identity has no durable physical-disk row.",
            )
        (
            disposition,
            reasons,
            required_types,
            result_documents,
            fingerprint,
            fingerprint_sha256,
            policy_sha256,
        ) = _evaluate_device(
            storage=storage,
            selected=selected_device,
            raw=raw,
            execution_result=execution_result,
        )
        intended_use = _intended_use(storage, selected_device)
        existing = session.scalar(
            select(DriveIntakeDisposition).where(
                DriveIntakeDisposition.operation_id == operation.id,
                DriveIntakeDisposition.physical_disk_id == disk.id,
                DriveIntakeDisposition.policy_sha256 == policy_sha256,
            )
        )
        immutable = {
            "stable_identity": stable_identity,
            "plan_id": plan.id,
            "plan_sha256": plan.sha256,
            "wizard_revision": plan.revision,
            "hardware_snapshot_id": snapshot.id,
            "hardware_snapshot_sha256": snapshot.sha256,
            "device_binding_sha256": str(storage["snapshot_binding"]["device_binding_sha256"]),
            "device_fingerprint_sha256": fingerprint_sha256,
            "execution_result_sha256": execution_sha256,
            "policy_name": POLICY_NAME,
            "policy_version": POLICY_VERSION,
            "intended_use": intended_use,
            "disposition": disposition,
        }
        if existing is not None:
            replay_fields = {
                **immutable,
                "evaluating_operation_id": operation.id,
                "device_fingerprint_json": fingerprint,
                "required_tests_json": required_types,
                "test_results_json": result_documents,
                "reason_codes_json": reasons,
            }
            if any(getattr(existing, key) != value for key, value in replay_fields.items()):
                raise IntakeEvaluationError(
                    "drive_intake_replay_conflict",
                    "Existing intake history conflicts with the immutable replay.",
                )
            records.append(existing)
            continue
        record = DriveIntakeDisposition(
            physical_disk_id=disk.id,
            operation_id=operation.id,
            evaluating_operation_id=operation.id,
            device_fingerprint_json=fingerprint,
            policy_sha256=policy_sha256,
            required_tests_json=required_types,
            test_results_json=result_documents,
            reason_codes_json=reasons,
            completed_at=completed_at,
            evaluated_at=now,
            **immutable,
        )
        session.add(record)
        session.flush()
        records.append(record)
        created += 1
    return IntakePersistenceResult(records=tuple(records), created=created)


def _latest_observed_fingerprint(
    session: Session, stable_identity: str
) -> tuple[str | None, str | None]:
    snapshot = session.scalar(
        select(HardwareSnapshot)
        .order_by(HardwareSnapshot.captured_at.desc(), HardwareSnapshot.id.desc())
        .limit(1)
    )
    if snapshot is None or document_hash(snapshot.payload_json) != snapshot.sha256:
        return None, "current_snapshot_unavailable"
    disks = snapshot.payload_json.get("disks")
    if not isinstance(disks, list):
        return None, "current_snapshot_invalid"
    matches = [
        (index, item)
        for index, item in enumerate(disks[:4096])
        if isinstance(item, Mapping) and item.get("id") == stable_identity
    ]
    if len(matches) != 1:
        return None, (
            "current_identity_ambiguous" if len(matches) > 1 else "device_not_in_current_snapshot"
        )
    index, raw = matches[0]
    try:
        selected = device_review_document(raw, index=index)
        _fingerprint_document, fingerprint_sha256 = _fingerprint(selected, raw)
    except (StoragePolicyError, IntakeEvaluationError):
        return None, "current_fingerprint_invalid"
    return fingerprint_sha256, None


def _admission_blocker(
    code: str, message: str, *, physical_disk_id: str | None = None
) -> dict[str, str]:
    blocker = {"code": code, "message": message}
    if physical_disk_id is not None:
        blocker["physical_disk_id"] = physical_disk_id
    return blocker


def _validate_admission_record(
    session: Session,
    *,
    record: DriveIntakeDisposition,
    disk: PhysicalDisk,
    stable_identity: str,
    current_fingerprint_sha256: str,
    expected_required_tests: list[str],
    expected_policy_sha256: str,
) -> str | None:
    """Return a stable reason when immutable A1 history cannot be trusted."""

    if (
        record.physical_disk_id != disk.id
        or record.stable_identity != stable_identity
        or record.evaluating_operation_id != record.operation_id
        or record.policy_name != POLICY_NAME
        or record.policy_version != POLICY_VERSION
        or record.intended_use != "destination"
        or record.required_tests_json != expected_required_tests
        or record.policy_sha256 != expected_policy_sha256
        or record.device_fingerprint_sha256 != current_fingerprint_sha256
        or document_hash(record.device_fingerprint_json) != record.device_fingerprint_sha256
    ):
        return "drive_intake_admission_binding_mismatch"

    plan = session.get(Plan, record.plan_id)
    operation = session.get(Operation, record.operation_id)
    snapshot = session.get(HardwareSnapshot, record.hardware_snapshot_id)
    if plan is None or operation is None or snapshot is None:
        return "drive_intake_admission_history_incomplete"
    try:
        _exact_plan_and_operation(operation, plan)
    except IntakeEvaluationError:
        return "drive_intake_admission_history_tampered"
    storage_value = plan.document_json.get("storage")
    if (
        not isinstance(storage_value, Mapping)
        or storage_value.get("topology") != "test"
        or operation.status != "succeeded"
        or not isinstance(operation.result_json, Mapping)
        or document_hash(operation.result_json) != record.execution_result_sha256
        or record.plan_sha256 != plan.sha256
        or record.wizard_revision != plan.revision
        or record.hardware_snapshot_sha256 != snapshot.sha256
    ):
        return "drive_intake_admission_history_tampered"
    try:
        bound_snapshot, selected_devices, raw_devices = _bound_snapshot(session, storage_value)
    except IntakeEvaluationError:
        return "drive_intake_admission_history_tampered"
    if (
        bound_snapshot.id != record.hardware_snapshot_id
        or storage_value["snapshot_binding"].get("device_binding_sha256")
        != record.device_binding_sha256
    ):
        return "drive_intake_admission_history_tampered"
    matches = [
        (selected, raw)
        for selected, raw in zip(selected_devices, raw_devices, strict=True)
        if selected.get("id") == stable_identity
    ]
    if len(matches) != 1:
        return "drive_intake_admission_history_tampered"
    selected, raw = matches[0]
    try:
        (
            disposition,
            reasons,
            required_tests,
            test_results,
            fingerprint,
            fingerprint_sha256,
            policy_sha256,
        ) = _evaluate_device(
            storage=storage_value,
            selected=selected,
            raw=raw,
            execution_result=operation.result_json,
        )
    except IntakeEvaluationError:
        return "drive_intake_admission_history_tampered"
    if (
        disposition != record.disposition
        or reasons != record.reason_codes_json
        or required_tests != record.required_tests_json
        or test_results != record.test_results_json
        or fingerprint != record.device_fingerprint_json
        or fingerprint_sha256 != record.device_fingerprint_sha256
        or policy_sha256 != record.policy_sha256
    ):
        return "drive_intake_admission_history_tampered"
    return None


def evaluate_storage_admission(
    session: Session, plan_document: Mapping[str, Any]
) -> IntakeAdmissionResult:
    """Evaluate current destination PASS requirements without mutating durable state."""

    storage_value = plan_document.get("storage")
    if storage_value is None:
        return IntakeAdmissionResult(
            admitted=False,
            qualification_exempt=False,
            blockers=(
                _admission_blocker(
                    "storage_selection_required",
                    "Select and review storage before applying this plan.",
                ),
            ),
        )
    if not isinstance(storage_value, Mapping):
        return IntakeAdmissionResult(
            admitted=False,
            qualification_exempt=False,
            blockers=(
                _admission_blocker(
                    "drive_intake_admission_plan_invalid",
                    "The storage plan cannot be validated for drive admission.",
                ),
            ),
        )
    if storage_value.get("topology") == "test":
        return IntakeAdmissionResult(admitted=False, qualification_exempt=True, blockers=())

    try:
        _snapshot, selected_devices, raw_devices = _bound_snapshot(session, storage_value)
    except IntakeEvaluationError as exc:
        return IntakeAdmissionResult(
            admitted=False,
            qualification_exempt=False,
            blockers=(
                _admission_blocker(
                    exc.code,
                    "The storage plan's immutable drive binding is missing or invalid.",
                ),
            ),
        )

    blockers: list[dict[str, str]] = []
    for selected, raw in zip(selected_devices, raw_devices, strict=True):
        stable_identity = str(selected["id"])
        disks = list(
            session.scalars(
                select(PhysicalDisk).where(PhysicalDisk.stable_identity == stable_identity).limit(2)
            )
        )
        if len(disks) != 1:
            blockers.append(
                _admission_blocker(
                    "drive_intake_admission_identity_ambiguous"
                    if disks
                    else "drive_intake_admission_disk_missing",
                    "The selected drive does not resolve to one durable inventory record.",
                )
            )
            continue
        disk = disks[0]
        intended_use = _intended_use(storage_value, selected)
        if intended_use != "destination":
            blockers.append(
                _admission_blocker(
                    "drive_intake_admission_destination_required",
                    "Only destination-qualified drives can be admitted to this storage plan.",
                    physical_disk_id=disk.id,
                )
            )
            continue
        try:
            expected_required, _policy_document, expected_policy_sha256 = _policy(
                storage_value, intended_use
            )
            _fingerprint_document, current_fingerprint_sha256 = _fingerprint(selected, raw)
        except IntakeEvaluationError:
            blockers.append(
                _admission_blocker(
                    "drive_intake_admission_policy_invalid",
                    "The destination intake policy cannot be validated.",
                    physical_disk_id=disk.id,
                )
            )
            continue
        record = session.scalar(
            select(DriveIntakeDisposition)
            .where(DriveIntakeDisposition.physical_disk_id == disk.id)
            .order_by(
                DriveIntakeDisposition.evaluated_at.desc(),
                DriveIntakeDisposition.id.desc(),
            )
            .limit(1)
        )
        if record is None:
            blockers.append(
                _admission_blocker(
                    "drive_intake_admission_not_tested",
                    "The selected drive has no completed intake disposition.",
                    physical_disk_id=disk.id,
                )
            )
            continue
        if record.disposition != "PASS":
            code = (
                "drive_intake_admission_source_only"
                if record.disposition == "SOURCE_ONLY"
                else "drive_intake_admission_latest_not_pass"
            )
            blockers.append(
                _admission_blocker(
                    code,
                    "The newest drive intake disposition does not permit destination use.",
                    physical_disk_id=disk.id,
                )
            )
            continue
        invalid = _validate_admission_record(
            session,
            record=record,
            disk=disk,
            stable_identity=stable_identity,
            current_fingerprint_sha256=current_fingerprint_sha256,
            expected_required_tests=expected_required,
            expected_policy_sha256=expected_policy_sha256,
        )
        if invalid is not None:
            blockers.append(
                _admission_blocker(
                    invalid,
                    "The newest drive intake PASS no longer matches this exact plan and policy.",
                    physical_disk_id=disk.id,
                )
            )
            continue
        latest_fingerprint, stale_code = _latest_observed_fingerprint(session, stable_identity)
        if stale_code is not None or latest_fingerprint != current_fingerprint_sha256:
            blockers.append(
                _admission_blocker(
                    f"drive_intake_admission_{stale_code or 'current_fingerprint_changed'}",
                    "Current hardware evidence no longer matches the qualified drive.",
                    physical_disk_id=disk.id,
                )
            )

    return IntakeAdmissionResult(
        admitted=not blockers,
        qualification_exempt=False,
        blockers=tuple(blockers),
    )


def _public_result(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": result.get("type"),
        "outcome": result.get("outcome"),
        "code": result.get("code"),
        "result_sha256": result.get("result_sha256"),
        "evidence_sha256": result.get("evidence_sha256"),
    }


def disposition_document(
    record: DriveIntakeDisposition, *, current: bool, stale_reason_codes: list[str]
) -> dict[str, Any]:
    return {
        "id": record.id,
        "physical_disk_id": record.physical_disk_id,
        "operation_id": record.operation_id,
        "plan_id": record.plan_id,
        "plan_sha256": record.plan_sha256,
        "wizard_revision": record.wizard_revision,
        "hardware_snapshot_id": record.hardware_snapshot_id,
        "hardware_snapshot_sha256": record.hardware_snapshot_sha256,
        "device_binding_sha256": record.device_binding_sha256,
        "device_fingerprint_sha256": record.device_fingerprint_sha256,
        "execution_result_sha256": record.execution_result_sha256,
        "policy": {
            "name": record.policy_name,
            "version": record.policy_version,
            "sha256": record.policy_sha256,
            "intended_use": record.intended_use,
            "required_tests": list(record.required_tests_json),
        },
        "test_results": [_public_result(item) for item in record.test_results_json],
        "disposition": record.disposition,
        "reason_codes": list(record.reason_codes_json),
        "current": current,
        "stale": not current,
        "stale_reason_codes": stale_reason_codes,
        "completed_at": record.completed_at,
        "evaluated_at": record.evaluated_at,
    }


def disposition_history(session: Session, *, physical_disk_id: str) -> list[dict[str, Any]]:
    disk = session.get(PhysicalDisk, physical_disk_id)
    if disk is None:
        raise LookupError("physical disk not found")
    rows = list(
        session.scalars(
            select(DriveIntakeDisposition)
            .where(DriveIntakeDisposition.physical_disk_id == disk.id)
            .order_by(
                DriveIntakeDisposition.evaluated_at.desc(),
                DriveIntakeDisposition.id.desc(),
            )
        )
    )
    current_fingerprint, stale_code = _latest_observed_fingerprint(session, disk.stable_identity)
    documents: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        current = (
            index == 0
            and stale_code is None
            and current_fingerprint == row.device_fingerprint_sha256
        )
        stale_reasons: list[str] = []
        if not current:
            stale_reasons.append(
                stale_code
                if index == 0 and stale_code is not None
                else "newer_assessment_exists"
                if index > 0
                else "device_fingerprint_changed"
            )
        documents.append(
            disposition_document(row, current=current, stale_reason_codes=stale_reasons)
        )
    return documents


def current_assessment(session: Session, *, physical_disk_id: str) -> dict[str, Any]:
    history = disposition_history(session, physical_disk_id=physical_disk_id)
    if not history:
        return {
            "physical_disk_id": physical_disk_id,
            "assessment": "NOT_TESTED",
            "current": True,
            "stale": False,
            "reason_codes": ["not_tested"],
            "record": None,
        }
    record = history[0]
    return {
        "physical_disk_id": physical_disk_id,
        "assessment": record["disposition"],
        "current": record["current"],
        "stale": record["stale"],
        "reason_codes": record["reason_codes"],
        "stale_reason_codes": record["stale_reason_codes"],
        "record": record,
    }
