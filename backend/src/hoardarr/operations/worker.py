from __future__ import annotations

import hashlib
import logging
import math
import os
import socket
import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from functools import partial
from threading import Event
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from hoardarr.connectivity.executor import ExecutorFailure as ConnectivityExecutorFailure
from hoardarr.connectivity.executor import apply as apply_connectivity
from hoardarr.connectivity.executor import remove as remove_connectivity
from hoardarr.core.config import Settings
from hoardarr.core.secrets import SecretBox, SecretStoreError
from hoardarr.db.models import (
    ConnectivityService,
    HardwareSnapshot,
    IntegrationConnection,
    Operation,
    Plan,
    PlanApproval,
    StorageDrainJob,
    UpdateState,
    WizardSession,
    utc_now,
)
from hoardarr.hardware.maintenance import enrich_maintenance_capabilities
from hoardarr.hardware.service import HardwareScanError, run_hardware_detector
from hoardarr.integrations.servarr import (
    PRODUCTS,
    ServarrError,
    apply_servarr_plan,
    discover_servarr,
)
from hoardarr.integrations.url_policy import IntegrationTargetError
from hoardarr.operations.service import (
    append_event,
    claim_next_operation,
    complete_operation,
    document_hash,
    fail_operation,
    mark_cancelled_resource,
    recover_stale_operations,
)
from hoardarr.storage.client import (
    StorageExecutorError,
    apply_device_maintenance,
    apply_snapraid_replacement,
    apply_storage_plan,
    apply_storage_redundancy,
    storage_operation_status,
)
from hoardarr.storage.drain_worker import (
    DrainExecutionError,
    DrainPaused,
    execute_drain,
    mark_drain_paused,
)
from hoardarr.storage.groups import reconcile_snapshot_disks
from hoardarr.storage.redundancy import (
    apply_redundancy_result,
    matching_devices,
    register_completed_storage,
    stable_path_identity,
    validate_redundancy_plan,
)
from hoardarr.storage.tiering import (
    TieringError,
    cleanup_retained_transfer,
    execute_transfer,
    plan_transfer,
)
from hoardarr.telemetry.service import TelemetryService, collect_for_worker
from hoardarr.updates.service import (
    UpdateError,
    UpdatePaths,
    download_artifact,
    execute_update,
    recover_interrupted_update,
)
from hoardarr.wizard.service import plan_approval_status

LOGGER = logging.getLogger(__name__)

SUPPORTED_OPERATION_KINDS = frozenset(
    {
        "hardware.scan",
        "servarr.discover",
        "servarr.apply",
        "storage.apply",
        "storage.transfer",
        "storage.transfer.cleanup",
        "storage.maintenance",
        "storage.snapraid.replace",
        "storage.redundancy.apply",
        "storage.drain",
        "connectivity.apply",
        "connectivity.remove",
        "update.apply",
    }
)
INTEGRATION_AAD_RECORD_TYPE = "integration_connection"

DetectorRunner = Callable[..., tuple[dict[str, Any], str]]
ServarrDiscoverer = Callable[..., dict[str, Any]]
StorageApplier = Callable[..., dict[str, Any]]
MaintenanceApplier = Callable[..., dict[str, Any]]
SnapraidReplacementApplier = Callable[..., dict[str, Any]]
StorageStatus = Callable[..., dict[str, Any]]
ConnectivityApplier = Callable[..., dict[str, Any]]
ConnectivityRemover = Callable[..., dict[str, Any]]


def _apply_connectivity_direct(
    _socket_path: object,
    *,
    operation_id: str,
    service_id: str,
    config_sha256: str,
    config: dict[str, Any],
    secret: str | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    del timeout_seconds
    result = apply_connectivity(service_id, config_sha256, config, secret)
    result["operation_id"] = operation_id
    return result


def _remove_connectivity_direct(
    _socket_path: object,
    *,
    operation_id: str,
    service_id: str,
    config_sha256: str,
    config: dict[str, Any],
    delete_backing_data: bool,
    timeout_seconds: float,
) -> dict[str, Any]:
    del timeout_seconds
    result = remove_connectivity(service_id, config_sha256, config, delete_backing_data)
    result["operation_id"] = operation_id
    return result


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


@dataclass(frozen=True)
class WorkItem:
    operation_id: str
    kind: str
    resource_type: str | None
    resource_id: str | None
    request: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class HardwareExecution:
    payload: dict[str, Any]
    sha256: str


@dataclass(frozen=True)
class ServarrConnectionData:
    id: str
    expected_product: str
    base_url: str
    approved_ips: list[str]
    allow_localhost: bool
    api_key_ciphertext: bytes = field(repr=False)
    verify_tls: bool
    fingerprint: str


@dataclass(frozen=True)
class ServarrExecution:
    connection: ServarrConnectionData
    discovery: dict[str, Any]


@dataclass(frozen=True)
class ServarrApplyExecution:
    connection: ServarrConnectionData
    result: dict[str, Any]


@dataclass(frozen=True)
class UpdateExecution:
    result: dict[str, Any]


@dataclass(frozen=True)
class StorageExecution:
    wizard_id: str
    plan_id: str
    plan_sha256: str
    result: dict[str, Any]


@dataclass(frozen=True)
class TierTransferExecution:
    result: dict[str, Any]


@dataclass(frozen=True)
class DrainExecution:
    result: dict[str, Any]


@dataclass(frozen=True)
class MaintenanceExecution:
    result: dict[str, Any]


@dataclass(frozen=True)
class RedundancyExecution:
    plan: dict[str, Any]
    result: dict[str, Any]


@dataclass(frozen=True)
class ConnectivityExecution:
    service_id: str
    config_sha256: str
    removing: bool
    result: dict[str, Any]


ExecutionResult = (
    HardwareExecution
    | ServarrExecution
    | ServarrApplyExecution
    | StorageExecution
    | TierTransferExecution
    | DrainExecution
    | MaintenanceExecution
    | RedundancyExecution
    | ConnectivityExecution
    | UpdateExecution
)


class WorkFailure(RuntimeError):
    """A safe, stable operation failure suitable for persistence and API output."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        connection: ServarrConnectionData | None = None,
        needs_attention: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.connection = connection
        self.needs_attention = needs_attention


_SERVARR_FAILURE_MESSAGES = {
    "authentication_failed": "Servarr rejected the configured API credential",
    "capability_missing": "Servarr does not expose a required API capability",
    "connection_failed": "Servarr could not be reached safely",
    "invalid_response": "Servarr returned an invalid response",
    "product_mismatch": "The endpoint is not the configured Servarr product",
    "redirect_refused": "Servarr returned an untrusted redirect",
    "remote_error": "Servarr returned an error response",
    "response_too_large": "Servarr returned more data than Hoardarr accepts",
    "unsupported_product": "The configured Servarr product is unsupported",
}


def make_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _claim_work(session_factory: SessionFactory, worker_id: str) -> WorkItem | None:
    # Claim and the associated event are committed before any detector or network call.
    with session_factory() as session, session.begin():
        operation = claim_next_operation(session, worker_id)
        if operation is None:
            return None
        return WorkItem(
            operation_id=operation.id,
            kind=operation.kind,
            resource_type=operation.resource_type,
            resource_id=operation.resource_id,
            request=deepcopy(operation.request_json),
        )


def _connection_fingerprint(connection: IntegrationConnection) -> str:
    digest = hashlib.sha256()
    values: tuple[object, ...] = (
        connection.id,
        connection.expected_product,
        connection.base_url,
        tuple(connection.approved_ips_json),
        connection.allow_localhost,
        connection.verify_tls,
    )
    for value in values:
        encoded = repr(value).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    digest.update(hashlib.sha256(bytes(connection.api_key_ciphertext)).digest())
    return digest.hexdigest()


def _load_connection(
    session_factory: SessionFactory,
    item: WorkItem,
) -> ServarrConnectionData:
    if item.resource_type not in {None, "integration_connection"} or not item.resource_id:
        raise WorkFailure(
            "invalid_operation_request",
            "Servarr discovery requires an integration connection resource",
        )
    with session_factory() as session, session.begin():
        connection = session.get(IntegrationConnection, item.resource_id)
        if connection is None or connection.adapter != "servarr":
            raise WorkFailure(
                "integration_not_found",
                "The Servarr integration connection no longer exists",
            )
        return ServarrConnectionData(
            id=connection.id,
            expected_product=connection.expected_product,
            base_url=connection.base_url,
            approved_ips=list(connection.approved_ips_json),
            allow_localhost=connection.allow_localhost,
            api_key_ciphertext=bytes(connection.api_key_ciphertext),
            verify_tls=connection.verify_tls,
            fingerprint=_connection_fingerprint(connection),
        )


def _bounded_text(value: object, *, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WorkFailure("invalid_discovery_result", "Servarr discovery returned invalid data")
    return value[:maximum]


def _bounded_identifier(value: object) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise WorkFailure("invalid_discovery_result", "Servarr discovery returned invalid data")
    if isinstance(value, str):
        return value[:128]
    return value


def _sanitize_status(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkFailure("invalid_discovery_result", "Servarr discovery returned invalid data")
    result: dict[str, Any] = {}
    text_fields = {
        "app_name": 128,
        "instance_name": 256,
        "version": 64,
        "url_base": 1024,
    }
    for field_name, maximum in text_fields.items():
        if field_name in value:
            result[field_name] = _bounded_text(value[field_name], maximum=maximum)
    for field_name in ("is_docker", "is_linux", "is_windows"):
        if field_name in value:
            field_value = value[field_name]
            if field_value is not None and not isinstance(field_value, bool):
                raise WorkFailure(
                    "invalid_discovery_result", "Servarr discovery returned invalid data"
                )
            result[field_name] = field_value
    return result


def _sanitize_roots(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise WorkFailure("invalid_discovery_result", "Servarr discovery returned invalid data")
    result: list[dict[str, Any]] = []
    for row in value[:4096]:
        if not isinstance(row, dict):
            continue
        free_space = row.get("free_space")
        if free_space is not None and (
            isinstance(free_space, bool) or not isinstance(free_space, (int, float))
        ):
            free_space = None
        if isinstance(free_space, float) and not math.isfinite(free_space):
            raise WorkFailure("invalid_discovery_result", "Servarr discovery returned invalid data")
        result.append(
            {
                "id": _bounded_identifier(row.get("id")),
                "path": _bounded_text(row.get("path"), maximum=4096),
                "free_space": free_space,
            }
        )
    return result


def _sanitize_mappings(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise WorkFailure("invalid_discovery_result", "Servarr discovery returned invalid data")
    result: list[dict[str, Any]] = []
    for row in value[:4096]:
        if not isinstance(row, dict):
            continue
        result.append(
            {
                "id": _bounded_identifier(row.get("id")),
                "host": _bounded_text(row.get("host"), maximum=1024),
                "remote_path": _bounded_text(row.get("remote_path"), maximum=4096),
                "local_path": _bounded_text(row.get("local_path"), maximum=4096),
            }
        )
    return result


def _sanitize_clients(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise WorkFailure("invalid_discovery_result", "Servarr discovery returned invalid data")
    result: list[dict[str, Any]] = []
    for row in value[:4096]:
        if not isinstance(row, dict):
            continue
        enabled = row.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            enabled = None
        result.append(
            {
                "id": _bounded_identifier(row.get("id")),
                "name": _bounded_text(row.get("name"), maximum=512),
                "implementation": _bounded_text(row.get("implementation"), maximum=256),
                "config_contract": _bounded_text(row.get("config_contract"), maximum=256),
                "enabled": enabled,
            }
        )
    return result


def _sanitize_client_schemas(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise WorkFailure("invalid_discovery_result", "Servarr discovery returned invalid data")
    result: list[dict[str, Any]] = []
    for row in value[:4096]:
        if not isinstance(row, dict):
            continue
        names = row.get("field_names")
        if not isinstance(names, list):
            names = []
        result.append(
            {
                "implementation": _bounded_text(row.get("implementation"), maximum=256),
                "config_contract": _bounded_text(row.get("config_contract"), maximum=256),
                "protocol": _bounded_text(row.get("protocol"), maximum=64),
                "field_names": [name[:256] for name in names[:1024] if isinstance(name, str)],
            }
        )
    return result


def _sanitize_discovery(
    discovery: dict[str, Any],
    *,
    expected_product: str,
) -> dict[str, Any]:
    if not isinstance(discovery, dict) or discovery.get("product") != expected_product:
        raise WorkFailure("invalid_discovery_result", "Servarr discovery returned invalid data")
    definition = PRODUCTS.get(expected_product)
    if definition is None:
        raise WorkFailure("unsupported_product", "The configured Servarr product is unsupported")
    capabilities_value = discovery.get("capabilities")
    if not isinstance(capabilities_value, list):
        raise WorkFailure("invalid_discovery_result", "Servarr discovery returned invalid data")
    capabilities = sorted(
        {
            capability
            for capability in capabilities_value
            if isinstance(capability, str) and capability in definition.declared_capabilities
        }
    )
    state_value = discovery.get("state")
    if not isinstance(state_value, dict) or "status" not in state_value:
        raise WorkFailure("invalid_discovery_result", "Servarr discovery returned invalid data")
    state: dict[str, Any] = {"status": _sanitize_status(state_value["status"])}
    sanitizers: tuple[tuple[str, Callable[[object], Any]], ...] = (
        ("root_folders", _sanitize_roots),
        ("remote_path_mappings", _sanitize_mappings),
        ("download_clients", _sanitize_clients),
        ("download_client_schemas", _sanitize_client_schemas),
    )
    for name, sanitizer in sanitizers:
        if name in state_value:
            state[name] = sanitizer(state_value[name])
    return {
        "product": expected_product,
        "version": _bounded_text(discovery.get("version"), maximum=64),
        "api_prefix": definition.api_prefix,
        "support_level": definition.support_level,
        "capabilities": capabilities,
        "state": state,
    }


def _contains_plaintext(value: object, plaintext: str) -> bool:
    if not plaintext:
        return False
    if isinstance(value, str):
        return plaintext in value
    if isinstance(value, dict):
        return any(
            _contains_plaintext(key, plaintext) or _contains_plaintext(child, plaintext)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_plaintext(child, plaintext) for child in value)
    return False


def _execute_hardware(
    settings: Settings,
    detector_runner: DetectorRunner,
) -> HardwareExecution:
    try:
        payload, _detector_sha256 = detector_runner(
            settings.hardware_detector,
            timeout_seconds=settings.hardware_scan_timeout_seconds,
            output_limit_bytes=settings.hardware_scan_output_limit_bytes,
            production=settings.environment == "production",
        )
    except HardwareScanError as exc:
        # HardwareScanError messages are produced by our bounded detector runner
        # (never copied from detector stderr), so they are safe and essential for
        # diagnosing packaging, timeout, and schema failures in service logs.
        LOGGER.error("Hardware detector failed: %s", exc)
        raise WorkFailure("hardware_scan_failed", "Hardware scan could not be completed") from exc
    payload = enrich_maintenance_capabilities(payload)
    return HardwareExecution(payload=payload, sha256=document_hash(payload))


def _execute_servarr(
    session_factory: SessionFactory,
    item: WorkItem,
    settings: Settings,
    secret_box: SecretBox,
    servarr_discoverer: ServarrDiscoverer,
    servarr_transport: httpx.BaseTransport | None,
) -> ServarrExecution:
    connection = _load_connection(session_factory, item)
    try:
        api_key = secret_box.decrypt(
            INTEGRATION_AAD_RECORD_TYPE,
            connection.id,
            connection.api_key_ciphertext,
        )
    except SecretStoreError as exc:
        raise WorkFailure(
            "credential_unavailable",
            "The Servarr API credential could not be loaded",
            connection=connection,
        ) from exc
    try:
        try:
            discovery = servarr_discoverer(
                settings=settings,
                expected_product=connection.expected_product,
                base_url=connection.base_url,
                approved_ips=connection.approved_ips,
                allow_localhost=connection.allow_localhost,
                api_key=api_key,
                verify_tls=connection.verify_tls,
                transport=servarr_transport,
            )
        except ServarrError as exc:
            code = exc.code if exc.code in _SERVARR_FAILURE_MESSAGES else "servarr_discovery_failed"
            message = _SERVARR_FAILURE_MESSAGES.get(
                code, "Servarr discovery could not be completed"
            )
            raise WorkFailure(code, message, connection=connection) from exc
        except IntegrationTargetError as exc:
            raise WorkFailure(
                "target_rejected",
                "The Servarr target no longer resolves to an approved address",
                connection=connection,
            ) from exc
        except Exception as exc:
            raise WorkFailure(
                "servarr_discovery_failed",
                "Servarr discovery could not be completed",
                connection=connection,
            ) from exc
        try:
            safe_discovery = _sanitize_discovery(
                discovery,
                expected_product=connection.expected_product,
            )
        except WorkFailure as exc:
            raise WorkFailure(
                exc.code,
                exc.safe_message,
                connection=connection,
            ) from exc
        if _contains_plaintext(safe_discovery, api_key):
            raise WorkFailure(
                "credential_reflected",
                "Servarr reflected its API credential in discovery data",
                connection=connection,
            )
    finally:
        # Python strings cannot be zeroed, but keep the plaintext lifetime and scope minimal.
        api_key = ""
    return ServarrExecution(connection=connection, discovery=safe_discovery)


def _execute_servarr_apply(
    session_factory: SessionFactory,
    item: WorkItem,
    settings: Settings,
    secret_box: SecretBox,
    servarr_transport: httpx.BaseTransport | None,
) -> ServarrApplyExecution:
    connection = _load_connection(session_factory, item)
    plan = item.request.get("plan")
    plan_sha256 = item.request.get("plan_sha256")
    if (
        item.request.get("schema_version") != 1
        or not isinstance(plan, dict)
        or document_hash(plan) != plan_sha256
    ):
        raise WorkFailure("invalid_operation_request", "The Servarr change request is invalid")
    try:
        api_key = secret_box.decrypt(
            INTEGRATION_AAD_RECORD_TYPE,
            connection.id,
            connection.api_key_ciphertext,
        )
    except SecretStoreError as exc:
        raise WorkFailure(
            "credential_unavailable",
            "The Servarr API credential could not be loaded",
            connection=connection,
        ) from exc
    try:
        result = apply_servarr_plan(
            settings=settings,
            expected_product=connection.expected_product,
            base_url=connection.base_url,
            approved_ips=connection.approved_ips,
            allow_localhost=connection.allow_localhost,
            api_key=api_key,
            verify_tls=connection.verify_tls,
            plan=plan,
            transport=servarr_transport,
        )
    except ServarrError as exc:
        safe_codes = {
            "authentication_failed",
            "capability_missing",
            "profile_required",
            "schema_changed",
            "partial_failure_needs_attention",
            "target_rejected",
        }
        code = exc.code if exc.code in safe_codes else "servarr_apply_failed"
        raise WorkFailure(
            code,
            "Servarr changes could not be completed",
            connection=connection,
            needs_attention=exc.code == "partial_failure_needs_attention",
        ) from exc
    reflected = _contains_plaintext(result, api_key)
    api_key = ""
    if reflected:
        raise WorkFailure(
            "credential_reflected",
            "Servarr reflected its API credential in change results",
            connection=connection,
        )
    return ServarrApplyExecution(connection=connection, result=result)


def _execute_update(item: WorkItem, settings: Settings) -> UpdateExecution:
    request = item.request
    metadata = request.get("metadata")
    if (
        set(request) != {"schema_version", "metadata_sha256", "metadata"}
        or request.get("schema_version") != 1
        or not isinstance(metadata, dict)
        or document_hash(metadata) != request.get("metadata_sha256")
    ):
        raise WorkFailure("invalid_operation_request", "The update request is invalid")
    release_id = metadata.get("release_id")
    if not isinstance(release_id, str):
        raise WorkFailure("invalid_operation_request", "The update request is invalid")
    artifact = settings.update_artifact_root / f"{release_id}.tar.gz"
    current = settings.frontend_dir.parent
    paths = UpdatePaths(
        releases=current.parent / "releases",
        current=current,
        state=settings.secret_key_file.parent,
        config=settings.update_trust_file.parent,
        trust=settings.update_trust_file,
        backup=settings.update_artifact_root.parent / "update-backups",
    )
    try:
        recover_interrupted_update(paths)
        download_artifact(metadata, artifact)
        result = execute_update(metadata, artifact, paths=paths)
    except UpdateError as exc:
        raise WorkFailure(
            exc.code,
            str(exc),
            needs_attention=exc.code
            in {"partial_failure_needs_attention", "update_failed", "rollback_failed"},
        ) from exc
    return UpdateExecution(result=result)


def _execute_storage(
    session_factory: SessionFactory,
    item: WorkItem,
    settings: Settings,
    storage_applier: StorageApplier,
) -> StorageExecution:
    request = item.request
    expected_fields = {
        "schema_version",
        "wizard_id",
        "wizard_revision",
        "plan_id",
        "plan_sha256",
    }
    if set(request) != expected_fields or request.get("schema_version") != 1:
        raise WorkFailure("invalid_operation_request", "The storage operation request is invalid")
    wizard_id = request.get("wizard_id")
    plan_id = request.get("plan_id")
    plan_sha256 = request.get("plan_sha256")
    revision = request.get("wizard_revision")
    if (
        not isinstance(wizard_id, str)
        or not isinstance(plan_id, str)
        or not isinstance(plan_sha256, str)
        or not isinstance(revision, int)
        or item.resource_type != "wizard_session"
        or item.resource_id != wizard_id
    ):
        raise WorkFailure("invalid_operation_request", "The storage operation request is invalid")
    with session_factory() as session, session.begin():
        wizard = session.get(WizardSession, wizard_id)
        plan = session.get(Plan, plan_id)
        if (
            wizard is None
            or plan is None
            or wizard.plan_id != plan.id
            or wizard.revision != revision
            or plan.revision != revision
            or plan.wizard_session_id != wizard.id
            or plan.sha256 != plan_sha256
            or document_hash(plan.document_json) != plan_sha256
        ):
            raise WorkFailure(
                "storage_plan_changed", "The storage plan changed before execution; review it again"
            )
        if (
            plan.document_json.get("apply_available") is not True
            or plan.document_json.get("blockers") != []
        ):
            raise WorkFailure("storage_plan_blocked", "The storage plan is not executable")
        approval_status = plan_approval_status(session, wizard_id=wizard.id)
        if approval_status["required"] and not approval_status["valid"]:
            raise WorkFailure(
                "destructive_approval_changed",
                "Destructive approval is no longer valid; review the storage plan again",
            )
        approval_document: dict[str, Any] | None = None
        if approval_status["required"]:
            approval = session.scalar(select(PlanApproval).where(PlanApproval.plan_id == plan.id))
            if approval is None:
                raise WorkFailure(
                    "destructive_approval_changed",
                    "Destructive approval is no longer valid; review the storage plan again",
                )
            approval_document = {
                "approval_id": approval.id,
                "plan_sha256": approval.plan_sha256,
                "wizard_revision": approval.wizard_revision,
                "hardware_snapshot_sha256": approval.hardware_snapshot_sha256,
                "device_binding_sha256": approval.device_binding_sha256,
                "selected_device_ids": list(approval.selected_device_ids_json),
                # The root service requires the exact fixed phrase too. It never receives
                # or reconstructs arbitrary user input from the approval endpoint.
                "confirmation_phrase": "I AGREE",
                "confirmation_sha256": approval.confirmation_sha256,
            }
        plan_document = deepcopy(plan.document_json)
    try:
        result = storage_applier(
            settings.storage_executor_socket,
            operation_id=item.operation_id,
            plan_sha256=plan_sha256,
            document=plan_document,
            approval=approval_document,
            timeout_seconds=settings.storage_executor_timeout_seconds,
        )
    except StorageExecutorError as exc:
        raise WorkFailure(exc.code, str(exc), needs_attention=exc.needs_attention) from exc
    return StorageExecution(
        wizard_id=wizard_id,
        plan_id=plan_id,
        plan_sha256=plan_sha256,
        result=result,
    )


def _execute_connectivity(
    session_factory: SessionFactory,
    item: WorkItem,
    settings: Settings,
    secret_box: SecretBox,
    connectivity_applier: ConnectivityApplier,
    connectivity_remover: ConnectivityRemover,
) -> ConnectivityExecution:
    if item.resource_type != "connectivity_service" or not item.resource_id:
        raise WorkFailure("invalid_operation_request", "Connectivity service is missing")
    with session_factory() as session, session.begin():
        service = session.get(ConnectivityService, item.resource_id)
        if service is None:
            raise WorkFailure("connectivity_not_found", "Connectivity service no longer exists")
        request_hash = item.request.get("config_sha256")
        if request_hash != service.config_sha256:
            raise WorkFailure(
                "connectivity_config_changed", "Connectivity settings changed; try again"
            )
        config = deepcopy(service.config_json)
        ciphertext = bytes(service.secret_ciphertext) if service.secret_ciphertext else None
        config_sha256 = service.config_sha256
    secret: str | None = None
    if ciphertext is not None:
        try:
            secret = secret_box.decrypt("connectivity_service", item.resource_id, ciphertext)
        except SecretStoreError as exc:
            raise WorkFailure(
                "connectivity_secret_unavailable",
                "Connectivity password could not be read",
                needs_attention=True,
            ) from exc
    try:
        if item.kind == "connectivity.apply":
            result = connectivity_applier(
                None,
                operation_id=item.operation_id,
                service_id=item.resource_id,
                config_sha256=config_sha256,
                config=config,
                secret=secret,
                timeout_seconds=settings.connectivity_executor_timeout_seconds,
            )
            removing = False
        else:
            delete_backing_data = item.request.get("delete_backing_data")
            if not isinstance(delete_backing_data, bool):
                raise WorkFailure(
                    "invalid_operation_request", "Connectivity removal request is invalid"
                )
            result = connectivity_remover(
                None,
                operation_id=item.operation_id,
                service_id=item.resource_id,
                config_sha256=config_sha256,
                config=config,
                delete_backing_data=delete_backing_data,
                timeout_seconds=settings.connectivity_executor_timeout_seconds,
            )
            removing = True
    except ConnectivityExecutorFailure as exc:
        raise WorkFailure(exc.code, str(exc), needs_attention=exc.needs_attention) from exc
    if (
        result.get("operation_id") != item.operation_id
        or result.get("service_id") != item.resource_id
    ):
        raise WorkFailure("executor_response_invalid", "Connectivity returned an invalid response")
    return ConnectivityExecution(
        service_id=item.resource_id,
        config_sha256=config_sha256,
        removing=removing,
        result=result,
    )


def _execute_maintenance(
    item: WorkItem,
    settings: Settings,
    maintenance_applier: MaintenanceApplier,
) -> MaintenanceExecution:
    plan = item.request.get("plan")
    plan_sha256 = item.request.get("plan_sha256")
    confirmation_sha256 = item.request.get("confirmation_sha256")
    if (
        not isinstance(plan, dict)
        or not isinstance(plan_sha256, str)
        or document_hash(plan) != plan_sha256
        or confirmation_sha256 != document_hash({"confirmation": "I AGREE"})
        or item.resource_type != "drive"
        or item.resource_id != plan.get("device", {}).get("id")
    ):
        raise WorkFailure("invalid_operation_request", "The drive maintenance request is invalid")
    try:
        result = maintenance_applier(
            settings.storage_executor_socket,
            operation_id=item.operation_id,
            plan_sha256=plan_sha256,
            plan=plan,
            confirmation_sha256=confirmation_sha256,
            timeout_seconds=settings.storage_executor_timeout_seconds,
        )
    except StorageExecutorError as exc:
        raise WorkFailure(exc.code, str(exc), needs_attention=exc.needs_attention) from exc
    return MaintenanceExecution(result=result)


def _execute_snapraid_replacement(
    item: WorkItem,
    settings: Settings,
    applier: SnapraidReplacementApplier,
) -> MaintenanceExecution:
    plan = item.request.get("plan")
    plan_sha256 = item.request.get("plan_sha256")
    confirmation_sha256 = item.request.get("confirmation_sha256")
    if (
        not isinstance(plan, dict)
        or not isinstance(plan_sha256, str)
        or document_hash(plan) != plan_sha256
        or confirmation_sha256 != document_hash({"confirmation": "I AGREE"})
        or item.resource_type != "drive"
        or item.resource_id != plan.get("device", {}).get("id")
    ):
        raise WorkFailure(
            "invalid_operation_request", "The SnapRAID replacement request is invalid"
        )
    try:
        result = applier(
            settings.storage_executor_socket,
            operation_id=item.operation_id,
            plan_sha256=plan_sha256,
            plan=plan,
            confirmation_sha256=confirmation_sha256,
            timeout_seconds=settings.storage_executor_timeout_seconds,
        )
    except StorageExecutorError as exc:
        raise WorkFailure(exc.code, str(exc), needs_attention=exc.needs_attention) from exc
    return MaintenanceExecution(result=result)


def _execute_work(
    session_factory: SessionFactory,
    item: WorkItem,
    settings: Settings,
    secret_box: SecretBox,
    detector_runner: DetectorRunner,
    servarr_discoverer: ServarrDiscoverer,
    servarr_transport: httpx.BaseTransport | None,
    storage_applier: StorageApplier,
    maintenance_applier: MaintenanceApplier,
    snapraid_replacement_applier: SnapraidReplacementApplier,
    connectivity_applier: ConnectivityApplier,
    connectivity_remover: ConnectivityRemover,
) -> ExecutionResult:
    if item.kind == "hardware.scan":
        return _execute_hardware(settings, detector_runner)
    if item.kind == "servarr.discover":
        return _execute_servarr(
            session_factory,
            item,
            settings,
            secret_box,
            servarr_discoverer,
            servarr_transport,
        )
    if item.kind == "servarr.apply":
        return _execute_servarr_apply(
            session_factory,
            item,
            settings,
            secret_box,
            servarr_transport,
        )
    if item.kind == "storage.apply":
        return _execute_storage(session_factory, item, settings, storage_applier)
    if item.kind == "storage.maintenance":
        return _execute_maintenance(item, settings, maintenance_applier)
    if item.kind == "storage.snapraid.replace":
        return _execute_snapraid_replacement(item, settings, snapraid_replacement_applier)
    if item.kind == "storage.redundancy.apply":
        raw_plan = item.request.get("plan")
        if not isinstance(raw_plan, dict):
            raise WorkFailure("invalid_operation_request", "The redundancy request is invalid")
        try:
            plan = validate_redundancy_plan(raw_plan)
            result = apply_storage_redundancy(
                settings.storage_executor_socket,
                operation_id=item.operation_id,
                plan_sha256=str(item.request.get("plan_sha256") or ""),
                plan=plan,
                confirmation_sha256=str(item.request.get("confirmation_sha256") or ""),
                timeout_seconds=settings.storage_executor_timeout_seconds,
            )
        except (StorageExecutorError, ValueError) as exc:
            code = exc.code if isinstance(exc, StorageExecutorError) else "redundancy_plan_invalid"
            raise WorkFailure(
                code,
                str(exc),
                needs_attention=getattr(exc, "needs_attention", False),
            ) from exc
        return RedundancyExecution(plan=plan, result=result)
    if item.kind == "storage.transfer":
        value = item.request.get("plan")
        if not isinstance(value, dict) or document_hash(value) != item.request.get("plan_sha256"):
            raise WorkFailure("transfer_plan_changed", "The transfer plan is invalid")
        try:
            plan = plan_transfer(value)
            if plan.document() != value:
                raise TieringError("transfer_plan_changed", "transfer plan changed")
            result = execute_transfer(
                plan,
                identity_provider=lambda path: f"dev:{path.stat().st_dev}",
            )
        except (TieringError, OSError, TypeError) as exc:
            code = exc.code if isinstance(exc, TieringError) else "transfer_failed"
            raise WorkFailure(code, "The storage transfer could not be completed") from exc
        return TierTransferExecution(result)
    if item.kind == "storage.drain":
        value = item.request.get("plan")
        confirmation_sha256 = item.request.get("confirmation_sha256")
        if (
            not isinstance(value, dict)
            or value.get("plan_sha256") != item.request.get("plan_sha256")
            or confirmation_sha256 != document_hash({"confirmation": "I AGREE"})
            or item.resource_type != "storage_group"
            or item.resource_id != value.get("storage_group_id")
        ):
            raise WorkFailure("drain_plan_changed", "The storage drain request is invalid")
        try:
            return DrainExecution(execute_drain(session_factory, item.operation_id, value))
        except DrainExecutionError as exc:
            raise WorkFailure(
                exc.code,
                exc.safe_message,
                needs_attention=exc.needs_attention,
            ) from exc
    if item.kind == "storage.transfer.cleanup":
        value = item.request.get("plan")
        if (
            not isinstance(value, dict)
            or document_hash(value) != item.request.get("plan_sha256")
            or item.resource_type != "storage_transfer"
            or item.resource_id != item.request.get("transfer_id")
        ):
            raise WorkFailure("transfer_plan_changed", "The retained transfer plan is invalid")
        try:
            plan = plan_transfer(value)
            result = cleanup_retained_transfer(
                plan, identity_provider=lambda path: f"dev:{path.stat().st_dev}"
            )
        except (TieringError, OSError, TypeError) as exc:
            code = exc.code if isinstance(exc, TieringError) else "transfer_cleanup_failed"
            raise WorkFailure(code, "The retained transfer could not be cleaned up") from exc
        return TierTransferExecution(result)
    if item.kind in {"connectivity.apply", "connectivity.remove"}:
        return _execute_connectivity(
            session_factory,
            item,
            settings,
            secret_box,
            connectivity_applier,
            connectivity_remover,
        )
    if item.kind == "update.apply":
        return _execute_update(item, settings)
    raise WorkFailure(
        "unsupported_operation",
        "This worker does not support the requested operation kind",
    )


def _leased_operation(session: Session, item: WorkItem, worker_id: str) -> Operation | None:
    operation = session.get(Operation, item.operation_id)
    if operation is None or operation.status != "running" or operation.lease_owner != worker_id:
        return None
    return operation


def _cancel_claimed_operation(session: Session, operation: Operation) -> None:
    operation.status = "cancelled"
    operation.result_json = None
    operation.error_json = None
    operation.heartbeat_at = utc_now()
    operation.updated_at = utc_now()
    mark_cancelled_resource(session, operation)
    append_event(session, operation, "cancelled", "Operation cancelled")


def _connection_is_current(
    session: Session,
    connection_data: ServarrConnectionData,
) -> IntegrationConnection | None:
    connection = session.get(IntegrationConnection, connection_data.id)
    if connection is None or _connection_fingerprint(connection) != connection_data.fingerprint:
        return None
    return connection


def _finalize_success(
    session_factory: SessionFactory,
    item: WorkItem,
    worker_id: str,
    execution: ExecutionResult,
) -> None:
    with session_factory() as session, session.begin():
        operation = _leased_operation(session, item, worker_id)
        if operation is None:
            LOGGER.warning(
                "Discarded result for operation %s after its lease was lost",
                item.operation_id,
            )
            return
        if operation.cancel_requested and isinstance(
            execution,
            (
                StorageExecution,
                MaintenanceExecution,
                RedundancyExecution,
                TierTransferExecution,
                ConnectivityExecution,
            ),
        ):
            operation.cancel_requested = False
            append_event(
                session,
                operation,
                "cancellation_too_late",
                "Cancellation was not applied because host changes had already started",
            )
        elif operation.cancel_requested:
            _cancel_claimed_operation(session, operation)
            return
        if isinstance(execution, HardwareExecution):
            source_value = execution.payload.get("source")
            source = source_value.get("kind") if isinstance(source_value, dict) else None
            schema_version = execution.payload.get("schema_version")
            if (
                not isinstance(source, str)
                or not isinstance(schema_version, int)
                or len(execution.sha256) != 64
            ):
                fail_operation(
                    session,
                    operation,
                    code="invalid_hardware_result",
                    message="Hardware scan returned data that could not be stored",
                )
                return
            snapshot = HardwareSnapshot(
                operation_id=operation.id,
                detector_schema_version=schema_version,
                source=source[:64],
                payload_json=execution.payload,
                sha256=execution.sha256,
            )
            session.add(snapshot)
            session.flush()
            disk_registry = reconcile_snapshot_disks(session, execution.payload)
            operation.resource_id = snapshot.id
            complete_operation(
                session,
                operation,
                {
                    "snapshot_id": snapshot.id,
                    "sha256": snapshot.sha256,
                    "schema_version": snapshot.detector_schema_version,
                    "source": snapshot.source,
                    "disk_registry": disk_registry,
                },
            )
            return

        if isinstance(execution, StorageExecution):
            wizard = session.get(WizardSession, execution.wizard_id)
            plan = session.get(Plan, execution.plan_id)
            if (
                wizard is None
                or plan is None
                or wizard.plan_id != plan.id
                or plan.sha256 != execution.plan_sha256
                or document_hash(plan.document_json) != execution.plan_sha256
            ):
                fail_operation(
                    session,
                    operation,
                    code="storage_plan_changed",
                    message="The storage plan changed while it was being applied",
                    needs_attention=True,
                )
                return
            wizard.status = "applied"
            wizard.updated_at = utc_now()
            snapshot = session.scalar(
                select(HardwareSnapshot).order_by(HardwareSnapshot.captured_at.desc()).limit(1)
            )
            register_completed_storage(
                session,
                plan.document_json,
                execution.result,
                hardware_snapshot=snapshot.payload_json if snapshot is not None else None,
            )
            complete_operation(session, operation, execution.result)
            return

        if isinstance(execution, TierTransferExecution):
            complete_operation(session, operation, execution.result)
            return

        if isinstance(execution, DrainExecution):
            complete_operation(session, operation, execution.result)
            return

        if isinstance(execution, RedundancyExecution):
            snapshot = session.scalar(
                select(HardwareSnapshot).order_by(HardwareSnapshot.captured_at.desc()).limit(1)
            )
            selected = execution.plan["selected_path"]
            observed = None
            if snapshot is not None and execution.plan["operation"] in {
                "redundancy.add",
                "redundancy.replace",
            }:
                observed = next(
                    (
                        item
                        for item in matching_devices(
                            snapshot.payload_json, str(execution.plan["logical_storage_identity"])
                        )
                        if stable_path_identity(item) == selected["stable_path_identity"]
                    ),
                    None,
                )
            entity = apply_redundancy_result(
                session,
                plan=execution.plan,
                observed_device=observed,
                operation_id=operation.id,
            )
            complete_operation(
                session,
                operation,
                {**execution.result, "storage_entity_id": entity.id},
            )
            return

        if isinstance(execution, MaintenanceExecution):
            complete_operation(session, operation, execution.result)
            return

        if isinstance(execution, ConnectivityExecution):
            service = session.get(ConnectivityService, execution.service_id)
            if service is None or service.config_sha256 != execution.config_sha256:
                fail_operation(
                    session,
                    operation,
                    code="connectivity_config_changed",
                    message="Connectivity settings changed while being applied",
                    needs_attention=True,
                )
                return
            if execution.removing:
                session.delete(service)
            else:
                service.status = "active"
                service.state_json = deepcopy(execution.result)
                service.last_error_json = None
                service.updated_at = utc_now()
            complete_operation(session, operation, execution.result)
            return

        if isinstance(execution, UpdateExecution):
            state = session.get(UpdateState, "system")
            if state is not None:
                state.last_error_json = None
                state.updated_at = utc_now()
            complete_operation(session, operation, execution.result)
            return

        if isinstance(execution, ServarrApplyExecution):
            connection = _connection_is_current(session, execution.connection)
            if connection is None:
                fail_operation(
                    session,
                    operation,
                    code="integration_changed",
                    message="The integration changed while changes were being applied",
                )
                return
            state = deepcopy(connection.state_json)
            state["last_apply"] = deepcopy(execution.result)
            connection.state_json = state
            connection.updated_at = utc_now()
            complete_operation(session, operation, execution.result)
            return

        connection = _connection_is_current(session, execution.connection)
        if connection is None:
            fail_operation(
                session,
                operation,
                code="integration_changed",
                message="The integration changed while discovery was running; retry the operation",
            )
            return
        discovery = execution.discovery
        connection.status = "connected"
        connection.discovered_product = discovery["product"]
        connection.product_version = discovery["version"]
        connection.capabilities_json = discovery["capabilities"]
        connection.state_json = discovery["state"]
        connection.last_checked_at = utc_now()
        connection.updated_at = utc_now()
        complete_operation(
            session,
            operation,
            {
                "connection_id": connection.id,
                "product": connection.discovered_product,
                "version": connection.product_version,
                "capabilities": connection.capabilities_json,
                "support_level": discovery["support_level"],
            },
        )


def _finalize_failure(
    session_factory: SessionFactory,
    item: WorkItem,
    worker_id: str,
    failure: WorkFailure,
) -> None:
    with session_factory() as session, session.begin():
        operation = _leased_operation(session, item, worker_id)
        if operation is None:
            LOGGER.warning(
                "Discarded failure for operation %s after its lease was lost",
                item.operation_id,
            )
            return
        if operation.cancel_requested and item.kind in {
            "storage.apply",
            "storage.maintenance",
            "storage.snapraid.replace",
            "storage.redundancy.apply",
            "storage.transfer",
            "storage.transfer.cleanup",
            "connectivity.apply",
            "connectivity.remove",
        }:
            operation.cancel_requested = False
            append_event(
                session,
                operation,
                "cancellation_too_late",
                "Cancellation was not applied because host changes had already started",
            )
        elif operation.cancel_requested:
            _cancel_claimed_operation(session, operation)
            return
        if failure.connection is not None:
            connection = _connection_is_current(session, failure.connection)
            if connection is None:
                fail_operation(
                    session,
                    operation,
                    code="integration_changed",
                    message=(
                        "The integration changed while discovery was running; retry the operation"
                    ),
                )
                return
            state = deepcopy(connection.state_json)
            state["last_error"] = {"code": failure.code}
            connection.state_json = state
            connection.status = "error"
            connection.last_checked_at = utc_now()
            connection.updated_at = utc_now()
        elif item.kind in {"connectivity.apply", "connectivity.remove"} and item.resource_id:
            service = session.get(ConnectivityService, item.resource_id)
            if service is not None:
                service.status = "error"
                service.last_error_json = {"code": failure.code, "message": failure.safe_message}
                service.updated_at = utc_now()
        elif item.kind == "update.apply":
            state = session.get(UpdateState, "system")
            if state is not None:
                state.last_error_json = {
                    "code": failure.code,
                    "message": failure.safe_message,
                }
                state.updated_at = utc_now()
        elif item.kind == "storage.drain":
            job = session.get(StorageDrainJob, operation.id)
            if job is not None:
                job.status = "needs_attention" if failure.needs_attention else "failed"
                job.report_json = {
                    **job.report_json,
                    "error": {"code": failure.code, "message": failure.safe_message},
                }
                job.updated_at = utc_now()
        fail_operation(
            session,
            operation,
            code=failure.code,
            message=failure.safe_message,
            needs_attention=failure.needs_attention,
        )


def _finalize_with_retry(operation_id: str, callback: Callable[[], None]) -> None:
    for attempt in range(3):
        try:
            callback()
            return
        except SQLAlchemyError as exc:
            LOGGER.warning(
                "Could not finalize operation %s on attempt %s (%s)",
                operation_id,
                attempt + 1,
                type(exc).__name__,
            )
            if attempt < 2:
                time.sleep(0.05 * (2**attempt))
        except Exception as exc:
            LOGGER.error(
                "Could not finalize operation %s (%s)",
                operation_id,
                type(exc).__name__,
            )
            return
    # The durable lease remains running and periodic stale recovery will move it
    # to needs_attention instead of terminating the worker process.
    LOGGER.error("Operation %s finalization exhausted its retries", operation_id)


def _finalize_paused(
    session_factory: SessionFactory, item: WorkItem, worker_id: str
) -> None:
    with session_factory() as session, session.begin():
        operation = _leased_operation(session, item, worker_id)
        if operation is None:
            return
        mark_drain_paused(session, operation)


def run_once(
    *,
    session_factory: SessionFactory,
    settings: Settings,
    secret_box: SecretBox,
    worker_id: str | None = None,
    detector_runner: DetectorRunner = run_hardware_detector,
    servarr_discoverer: ServarrDiscoverer = discover_servarr,
    servarr_transport: httpx.BaseTransport | None = None,
    storage_applier: StorageApplier = apply_storage_plan,
    maintenance_applier: MaintenanceApplier = apply_device_maintenance,
    snapraid_replacement_applier: SnapraidReplacementApplier = apply_snapraid_replacement,
    connectivity_applier: ConnectivityApplier = _apply_connectivity_direct,
    connectivity_remover: ConnectivityRemover = _remove_connectivity_direct,
) -> bool:
    """Claim and execute one operation; return False when the queue is empty."""

    effective_worker_id = worker_id or make_worker_id()
    item = _claim_work(session_factory, effective_worker_id)
    if item is None:
        return False
    try:
        execution = _execute_work(
            session_factory,
            item,
            settings,
            secret_box,
            detector_runner,
            servarr_discoverer,
            servarr_transport,
            storage_applier,
            maintenance_applier,
            snapraid_replacement_applier,
            connectivity_applier,
            connectivity_remover,
        )
    except DrainPaused:
        _finalize_with_retry(
            item.operation_id,
            partial(_finalize_paused, session_factory, item, effective_worker_id),
        )
    except WorkFailure as failure:
        _finalize_with_retry(
            item.operation_id,
            partial(_finalize_failure, session_factory, item, effective_worker_id, failure),
        )
    except Exception as exc:  # Keep unknown exception text and arguments out of durable state/logs.
        LOGGER.error(
            "Operation %s failed unexpectedly (%s)",
            item.operation_id,
            type(exc).__name__,
        )
        internal_failure = WorkFailure(
            "worker_internal_error", "The worker could not complete the operation"
        )
        _finalize_with_retry(
            item.operation_id,
            partial(
                _finalize_failure,
                session_factory,
                item,
                effective_worker_id,
                internal_failure,
            ),
        )
    else:
        _finalize_with_retry(
            item.operation_id,
            partial(_finalize_success, session_factory, item, effective_worker_id, execution),
        )
    return True


def recover_abandoned_operations(
    *,
    session_factory: SessionFactory,
    settings: Settings,
    storage_status: StorageStatus = storage_operation_status,
) -> int:
    # The lease must outlive the longest bounded external operation because this first
    # worker deliberately holds no database transaction/heartbeat while doing I/O.
    storage_max_age = max(
        120,
        int(settings.storage_executor_timeout_seconds) + 300,
    )
    reconciled = 0
    with session_factory() as session:
        running_storage = [
            (
                operation.id,
                operation.kind,
                operation.lease_owner,
                operation.resource_type,
                operation.resource_id,
                deepcopy(operation.request_json),
            )
            for operation in session.scalars(
                select(Operation).where(
                    Operation.status == "running",
                    Operation.kind.in_(
                        (
                            "storage.apply",
                            "storage.maintenance",
                            "storage.snapraid.replace",
                            "storage.redundancy.apply",
                        )
                    ),
                    Operation.lease_owner.is_not(None),
                )
            )
        ]
    for operation_id, kind, lease_owner, resource_type, resource_id, request in running_storage:
        try:
            status = storage_status(
                settings.storage_status_socket,
                operation_id=operation_id,
                timeout_seconds=min(5.0, settings.storage_executor_timeout_seconds),
            )
        except StorageExecutorError:
            continue
        state = status.get("state")
        if state == "succeeded" and isinstance(status.get("result"), dict):
            item = WorkItem(
                operation_id=operation_id,
                kind=kind,
                resource_type=resource_type,
                resource_id=resource_id,
                request=request,
            )
            if item.kind == "storage.apply":
                wizard_id = request.get("wizard_id")
                plan_id = request.get("plan_id")
                plan_sha256 = request.get("plan_sha256")
                if not all(isinstance(value, str) for value in (wizard_id, plan_id, plan_sha256)):
                    continue
                execution: ExecutionResult = StorageExecution(
                    wizard_id=str(wizard_id),
                    plan_id=str(plan_id),
                    plan_sha256=str(plan_sha256),
                    result=deepcopy(status["result"]),
                )
            elif item.kind == "storage.redundancy.apply":
                plan = request.get("plan")
                if not isinstance(plan, dict):
                    continue
                try:
                    validated_plan = validate_redundancy_plan(plan)
                except ValueError:
                    continue
                execution = RedundancyExecution(
                    plan=validated_plan,
                    result=deepcopy(status["result"]),
                )
            else:
                execution = MaintenanceExecution(result=deepcopy(status["result"]))
            _finalize_success(session_factory, item, str(lease_owner), execution)
            reconciled += 1
        elif state == "needs_attention":
            with session_factory() as session, session.begin():
                operation = session.get(Operation, operation_id)
                if (
                    operation is not None
                    and operation.status == "running"
                    and operation.lease_owner == lease_owner
                ):
                    fail_operation(
                        session,
                        operation,
                        code="storage_executor_interrupted",
                        message="The storage path change needs attention",
                        needs_attention=True,
                    )
                    reconciled += 1
        elif state == "running":
            updated_at = status.get("updated_at")
            if isinstance(updated_at, (int, float)) and time.time() - updated_at <= storage_max_age:
                with session_factory() as session, session.begin():
                    operation = session.get(Operation, operation_id)
                    if (
                        operation is not None
                        and operation.status == "running"
                        and operation.lease_owner == lease_owner
                    ):
                        operation.heartbeat_at = utc_now()
                        operation.updated_at = utc_now()
    with session_factory() as session, session.begin():
        return reconciled + recover_stale_operations(
            session,
            max_age_seconds=max(
                120,
                settings.hardware_scan_timeout_seconds + 60,
                int(settings.integration_timeout_seconds * 6) + 60,
            ),
            max_age_by_kind={
                "storage.apply": storage_max_age,
                "storage.maintenance": storage_max_age,
                "storage.snapraid.replace": storage_max_age,
                "storage.redundancy.apply": storage_max_age,
                "connectivity.apply": int(settings.connectivity_executor_timeout_seconds) + 120,
                "connectivity.remove": int(settings.connectivity_executor_timeout_seconds) + 120,
            },
        )


def run_forever(
    *,
    session_factory: SessionFactory,
    settings: Settings,
    secret_box: SecretBox,
    worker_id: str | None = None,
    stop_event: Event | None = None,
    detector_runner: DetectorRunner = run_hardware_detector,
    servarr_discoverer: ServarrDiscoverer = discover_servarr,
    servarr_transport: httpx.BaseTransport | None = None,
) -> None:
    """Recover abandoned leases periodically, then process work until stopped."""

    effective_worker_id = worker_id or make_worker_id()
    telemetry_service = TelemetryService(settings)
    try:
        recover_abandoned_operations(session_factory=session_factory, settings=settings)
        recovery_interval = 30.0
        next_recovery = time.monotonic() + recovery_interval
        while stop_event is None or not stop_event.is_set():
            try:
                collect_for_worker(session_factory, settings, telemetry_service)
            except Exception as exc:
                # Telemetry failure is isolated from destructive-operation durability.
                LOGGER.warning("Telemetry collection failed (%s)", type(exc).__name__)
            if time.monotonic() >= next_recovery:
                recover_abandoned_operations(session_factory=session_factory, settings=settings)
                next_recovery = time.monotonic() + recovery_interval
            worked = run_once(
                session_factory=session_factory,
                settings=settings,
                secret_box=secret_box,
                worker_id=effective_worker_id,
                detector_runner=detector_runner,
                servarr_discoverer=servarr_discoverer,
                servarr_transport=servarr_transport,
            )
            if worked:
                continue
            if stop_event is not None:
                stop_event.wait(settings.worker_poll_seconds)
            else:
                time.sleep(settings.worker_poll_seconds)
    finally:
        telemetry_service.close()
