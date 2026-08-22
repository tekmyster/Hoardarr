from __future__ import annotations

import hmac
import ipaddress
import json
import math
import posixpath
import re
import uuid
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any, NoReturn

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from hoardarr.db.models import (
    HardwareSnapshot,
    IntegrationConnection,
    Plan,
    PlanApproval,
    WizardSession,
    new_id,
    utc_now,
)
from hoardarr.integrations.servarr import PRODUCTS
from hoardarr.operations.service import document_hash
from hoardarr.wizard.storage_policy import (
    GUIDED_MODES,
    REQUIRED_CONSENT_PHRASE,
    StoragePolicyError,
    build_storage_plan,
    normalize_storage_answers,
    select_devices,
)

WORKFLOW = "storage_setup"
WORKFLOW_VERSION = 2
MODES = frozenset({"guided", "simple", "advanced"})
STEPS = ("storage", "layout", "applications", "connectivity", "draft_ui")
MUTABLE_STATUSES = frozenset({"draft", "review"})

DEFAULT_LAYOUT: dict[str, str] = {
    "work_path": "/data/work",
    "downloads_path": "/data/downloads",
    "media_path": "/data/media",
}

_STANDARD_LIBRARY_NAMES = {
    "sonarr": "tv",
    "radarr": "movies",
    "lidarr": "music",
    "readarr": "books",
}
_STANDARD_LIBRARY_NAMES_IN_ORDER = ("movies", "tv", "music", "books")
_SECRET_KEYS = frozenset(
    {
        "apikey",
        "api_key",
        "authorization",
        "bearer",
        "credential",
        "credentials",
        "password",
        "passwd",
        "secret",
        "token",
    }
)
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_SHARE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,63}$")
_MAX_ANSWER_BYTES = 128 * 1024
_MAX_ANSWER_DEPTH = 20
_MAX_ANSWER_NODES = 5_000


class WizardError(RuntimeError):
    """Base error with a stable API-facing code."""

    code = "wizard_error"


class WizardNotFound(WizardError):
    code = "wizard_not_found"


class WizardConflict(WizardError):
    code = "wizard_revision_conflict"

    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            f"wizard revision is {current_revision}, not the expected {expected_revision}"
        )


class WizardStateError(WizardError):
    code = "wizard_state_conflict"

    def __init__(self, message: str, *, status: str | None = None) -> None:
        self.status = status
        super().__init__(message)


class WizardConsentError(WizardStateError):
    code = "destructive_consent_required"

    def __init__(self, message: str, *, reason: str) -> None:
        self.reason = reason
        super().__init__(message, status="review")


class WizardValidationError(WizardError):
    code = "wizard_validation_failed"

    def __init__(self, errors: Mapping[str, str] | str) -> None:
        self.errors = dict(errors) if isinstance(errors, Mapping) else {"body": errors}
        super().__init__("; ".join(f"{field}: {message}" for field, message in self.errors.items()))


def _validation(field: str, message: str) -> NoReturn:
    raise WizardValidationError({field: message})


def _is_secret_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    compact = normalized.replace("_", "")
    compact_secret_suffixes = ("apikey", "credential", "password", "passwd", "secret", "token")
    return (
        normalized in _SECRET_KEYS
        or compact in _SECRET_KEYS
        or normalized.endswith(("_api_key", "_credential", "_password", "_secret", "_token"))
        or compact.endswith(compact_secret_suffixes)
    )


def _validate_json_tree(value: Any, *, field: str) -> Any:
    """Return a detached JSON value after rejecting secrets and abusive input."""

    node_count = 0

    def visit(item: Any, path: str, depth: int) -> Any:
        nonlocal node_count
        node_count += 1
        if node_count > _MAX_ANSWER_NODES:
            _validation(field, "answer contains too many values")
        if depth > _MAX_ANSWER_DEPTH:
            _validation(field, "answer is nested too deeply")
        if item is None or isinstance(item, (str, bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                _validation(path, "number must be finite")
            return item
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            for key, child in item.items():
                if not isinstance(key, str):
                    _validation(path, "object keys must be strings")
                if _is_secret_key(key):
                    _validation(f"{path}.{key}", "credentials and secrets are not accepted here")
                result[key] = visit(child, f"{path}.{key}", depth + 1)
            return result
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            return [visit(child, f"{path}[{index}]", depth + 1) for index, child in enumerate(item)]
        _validation(path, "value is not JSON-compatible")

    cloned = visit(value, field, 0)
    encoded = json.dumps(cloned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    if len(encoded) > _MAX_ANSWER_BYTES:
        _validation(field, "answer is too large")
    return cloned


def _validate_path(value: Any, *, field: str, protect_host_paths: bool = False) -> str:
    if not isinstance(value, str) or not value:
        _validation(field, "must be a non-empty absolute Linux path")
    if len(value) > 4096:
        _validation(field, "path is too long")
    if "\\" in value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        _validation(field, "must use a printable Linux path")
    path = PurePosixPath(value)
    normalized = str(path)
    if not path.is_absolute() or normalized != value or "//" in value or ".." in path.parts:
        _validation(field, "must be an absolute, normalized Linux path")
    if normalized == "/":
        _validation(field, "the filesystem root cannot be used")
    if protect_host_paths:
        protected = (
            "/boot",
            "/dev",
            "/etc",
            "/proc",
            "/run",
            "/sys",
            "/usr",
            "/var/lib/hoardarr",
        )
        if any(
            path == PurePosixPath(root) or PurePosixPath(root) in path.parents for root in protected
        ):
            _validation(field, "operating-system paths cannot be used for storage layout")
    return normalized


def _paths_overlap(first: str, second: str) -> bool:
    first_path = PurePosixPath(first)
    second_path = PurePosixPath(second)
    return (
        first_path == second_path
        or first_path in second_path.parents
        or second_path in first_path.parents
    )


def _validate_layout(value: Any, *, mode: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        _validation("layout", "must be an object")
    allowed = frozenset(DEFAULT_LAYOUT)
    unknown = sorted(set(value) - allowed)
    if unknown:
        _validation("layout", f"unknown fields: {', '.join(unknown)}")
    missing = sorted(allowed - set(value))
    if missing:
        _validation("layout", f"missing fields: {', '.join(missing)}")
    layout = {
        name: _validate_path(value[name], field=f"layout.{name}", protect_host_paths=True)
        for name in DEFAULT_LAYOUT
    }
    if mode in GUIDED_MODES and layout != DEFAULT_LAYOUT:
        _validation("layout", "custom paths require Advanced mode")
    names = tuple(layout)
    for index, first_name in enumerate(names):
        for second_name in names[index + 1 :]:
            if _paths_overlap(layout[first_name], layout[second_name]):
                _validation(
                    "layout",
                    f"{first_name} and {second_name} must be separate, non-overlapping paths",
                )
    return layout


def _validate_uuid(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        _validation(field, "must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        _validation(field, "must be a UUID string")
    canonical = str(parsed)
    if value.lower() != canonical:
        _validation(field, "must be a canonical hyphenated UUID string")
    return canonical


def _validate_host(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        _validation(field, "must be a hostname or IP address, without a URL or port")
    if ":" in value:
        try:
            ipaddress.IPv6Address(value)
        except ipaddress.AddressValueError:
            _validation(field, "must be a hostname or IP address, without a URL or port")
    elif not _HOST_RE.fullmatch(value):
        _validation(field, "must be a hostname or IP address, without a URL or port")
    return value.lower()


def _validate_applications(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _validation("applications", "must be an object")
    allowed = {"selected_integration_ids", "root_folder_paths", "remote_path_mappings"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        _validation("applications", f"unknown fields: {', '.join(unknown)}")

    raw_selected = value.get("selected_integration_ids", [])
    if not isinstance(raw_selected, list):
        _validation("applications.selected_integration_ids", "must be a list")
    selected = [
        _validate_uuid(item, field=f"applications.selected_integration_ids[{index}]")
        for index, item in enumerate(raw_selected)
    ]
    if len(selected) != len(set(selected)):
        _validation("applications.selected_integration_ids", "must not contain duplicates")
    selected_set = set(selected)

    raw_roots = value.get("root_folder_paths", {})
    if not isinstance(raw_roots, Mapping):
        _validation("applications.root_folder_paths", "must be an object keyed by integration ID")
    roots: dict[str, str] = {}
    for raw_id, raw_path in raw_roots.items():
        integration_id = _validate_uuid(raw_id, field="applications.root_folder_paths key")
        if integration_id not in selected_set:
            _validation(
                f"applications.root_folder_paths.{integration_id}",
                "integration must also be selected",
            )
        roots[integration_id] = _validate_path(
            raw_path,
            field=f"applications.root_folder_paths.{integration_id}",
        )

    raw_mappings = value.get("remote_path_mappings", [])
    if not isinstance(raw_mappings, list):
        _validation("applications.remote_path_mappings", "must be a list")
    mappings: list[dict[str, str]] = []
    identities: set[tuple[str, str, str]] = set()
    for index, raw_mapping in enumerate(raw_mappings):
        field = f"applications.remote_path_mappings[{index}]"
        if not isinstance(raw_mapping, Mapping):
            _validation(field, "must be an object")
        expected_fields = {"integration_id", "host", "remote_path", "local_path"}
        if set(raw_mapping) != expected_fields:
            _validation(field, "must contain integration_id, host, remote_path, and local_path")
        integration_id = _validate_uuid(
            raw_mapping["integration_id"],
            field=f"{field}.integration_id",
        )
        if integration_id not in selected_set:
            _validation(f"{field}.integration_id", "integration must also be selected")
        host = _validate_host(raw_mapping["host"], field=f"{field}.host")
        remote_path = _validate_path(raw_mapping["remote_path"], field=f"{field}.remote_path")
        local_path = _validate_path(raw_mapping["local_path"], field=f"{field}.local_path")
        identity = (integration_id, host, remote_path)
        if identity in identities:
            _validation(field, "duplicates another remote-path mapping")
        identities.add(identity)
        mappings.append(
            {
                "integration_id": integration_id,
                "host": host,
                "remote_path": remote_path,
                "local_path": local_path,
            }
        )
    return {
        "selected_integration_ids": selected,
        "root_folder_paths": roots,
        "remote_path_mappings": mappings,
    }


def _validate_connectivity(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _validation("connectivity", "must be an object")
    allowed = {"skip", "services"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        _validation("connectivity", f"unknown fields: {', '.join(unknown)}")
    skip = value.get("skip")
    if not isinstance(skip, bool):
        _validation("connectivity.skip", "must be true or false")
    raw_services = value.get("services", [])
    if not isinstance(raw_services, list):
        _validation("connectivity.services", "must be a list")
    services: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for index, raw_service in enumerate(raw_services):
        field = f"connectivity.services[{index}]"
        if not isinstance(raw_service, Mapping):
            _validation(field, "must be an object")
        expected = {"protocol", "name", "path", "read_only"}
        if set(raw_service) != expected:
            _validation(field, "must contain protocol, name, path, and read_only")
        protocol = raw_service.get("protocol")
        if protocol not in {"smb", "nfs", "iscsi", "fcoe"}:
            _validation(f"{field}.protocol", "must be smb, nfs, iscsi, or fcoe")
        name = raw_service.get("name")
        if not isinstance(name, str) or not _SHARE_NAME_RE.fullmatch(name):
            _validation(f"{field}.name", "must be a predictable share or target name")
        path = _validate_path(raw_service.get("path"), field=f"{field}.path")
        read_only = raw_service.get("read_only")
        if not isinstance(read_only, bool):
            _validation(f"{field}.read_only", "must be true or false")
        identity = (protocol, name.casefold())
        if identity in identities:
            _validation(field, "duplicates another protocol and name")
        identities.add(identity)
        services.append({"protocol": protocol, "name": name, "path": path, "read_only": read_only})
    if skip and services:
        _validation("connectivity", "cannot skip connectivity while services are selected")
    if not skip and not services:
        _validation("connectivity.services", "select at least one service or skip connectivity")
    return {"skip": skip, "services": services}


def _refresh(session: Session, wizard: WizardSession) -> WizardSession:
    session.expire(wizard)
    session.refresh(wizard)
    return wizard


def get_wizard(session: Session, wizard_id: str) -> WizardSession:
    wizard = session.get(WizardSession, wizard_id)
    if wizard is None:
        raise WizardNotFound("wizard session was not found")
    return wizard


def create_wizard(
    session: Session,
    *,
    mode: str = "guided",
    hardware_snapshot_id: str | None = None,
) -> WizardSession:
    if mode not in MODES:
        _validation("mode", "must be guided or advanced")
    if (
        hardware_snapshot_id is not None
        and session.get(HardwareSnapshot, hardware_snapshot_id) is None
    ):
        _validation("hardware_snapshot_id", "must identify an existing discovery snapshot")
    wizard = WizardSession(
        workflow=WORKFLOW,
        workflow_version=WORKFLOW_VERSION,
        mode=mode,
        status="draft",
        current_step="storage" if hardware_snapshot_id is not None else "layout",
        revision=0,
        hardware_snapshot_id=hardware_snapshot_id,
        answers_json={"layout": dict(DEFAULT_LAYOUT)},
    )
    session.add(wizard)
    session.flush()
    return wizard


def update_step(
    session: Session,
    *,
    wizard_id: str,
    expected_revision: int,
    step: str,
    answers: Mapping[str, Any],
) -> WizardSession:
    wizard = get_wizard(session, wizard_id)
    if wizard.workflow != WORKFLOW or wizard.workflow_version != WORKFLOW_VERSION:
        raise WizardStateError("this service cannot edit the wizard workflow")
    if wizard.revision != expected_revision:
        raise WizardConflict(
            expected_revision=expected_revision,
            current_revision=wizard.revision,
        )
    if wizard.status not in MUTABLE_STATUSES:
        raise WizardStateError("wizard can no longer be edited", status=wizard.status)
    if step not in STEPS:
        _validation("step", f"must be one of: {', '.join(STEPS)}")

    detached = _validate_json_tree(answers, field=step)
    if step == "draft_ui":
        active_step = detached.get("active_step")
        if (
            not isinstance(active_step, int)
            or isinstance(active_step, bool)
            or not 2 <= active_step <= 9
        ):
            _validation("draft_ui.active_step", "must identify a resumable storage wizard step")
        selected_device_ids = detached.get("selected_device_ids")
        if not isinstance(selected_device_ids, list) or not all(
            isinstance(device_id, str) and device_id for device_id in selected_device_ids
        ):
            _validation("draft_ui.selected_device_ids", "must be a list of device identities")
        if len(set(selected_device_ids)) != len(selected_device_ids):
            _validation("draft_ui.selected_device_ids", "must not contain duplicate drives")
        normalized = detached
        next_step = "draft_ui"
    elif step == "storage":
        if wizard.hardware_snapshot_id is None:
            _validation("hardware_snapshot_id", "run storage discovery before selecting drives")
        snapshot = session.get(HardwareSnapshot, wizard.hardware_snapshot_id)
        if snapshot is None:
            raise WizardStateError("the bound hardware discovery snapshot is unavailable")
        try:
            normalized = normalize_storage_answers(
                detached,
                mode=wizard.mode,
                snapshot_payload=snapshot.payload_json,
            )
        except StoragePolicyError as exc:
            _validation(exc.field, exc.message)
        next_step = "layout"
    elif step == "layout":
        normalized = _validate_layout(detached, mode=wizard.mode)
        next_step = "applications"
    elif step == "applications":
        normalized = _validate_applications(detached)
        next_step = "connectivity"
    else:
        normalized = _validate_connectivity(detached)
        next_step = "review"

    all_answers = dict(wizard.answers_json)
    all_answers[step] = normalized
    changed = session.execute(
        update(WizardSession)
        .where(
            WizardSession.id == wizard.id,
            WizardSession.revision == expected_revision,
            WizardSession.status.in_(MUTABLE_STATUSES),
        )
        .values(
            answers_json=all_answers,
            current_step=next_step,
            revision=expected_revision + 1,
            status="draft",
            plan_id=None,
            updated_at=utc_now(),
        )
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        session.expire_all()
        current = get_wizard(session, wizard_id)
        if current.status not in MUTABLE_STATUSES:
            raise WizardStateError("wizard can no longer be edited", status=current.status)
        raise WizardConflict(
            expected_revision=expected_revision,
            current_revision=current.revision,
        )
    return _refresh(session, wizard)


def _load_integrations(
    session: Session,
    selected_ids: list[str],
) -> dict[str, IntegrationConnection]:
    if not selected_ids:
        return {}
    connections = {
        connection.id: connection
        for connection in session.scalars(
            select(IntegrationConnection).where(IntegrationConnection.id.in_(selected_ids))
        )
    }
    missing = sorted(set(selected_ids) - set(connections))
    if missing:
        _validation(
            "applications.selected_integration_ids",
            f"unknown integrations: {', '.join(missing)}",
        )
    for connection in connections.values():
        if connection.adapter != "servarr":
            _validation(
                "applications.selected_integration_ids",
                f"integration {connection.id} is not a Servarr connection",
            )
        if (
            connection.discovered_product is not None
            and connection.discovered_product != connection.expected_product
        ):
            _validation(
                "applications.selected_integration_ids",
                f"integration {connection.id} has a product identity mismatch",
            )
        if connection.status != "connected":
            _validation(
                "applications.selected_integration_ids",
                f"integration {connection.id} must be connected before it can be planned",
            )
    return connections


def _directory_actions(
    layout: Mapping[str, str],
    storage_plan: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if storage_plan is not None:
        paths: list[tuple[str, str]] = [
            (library["path"], f"library_{index + 1}")
            for index, library in enumerate(storage_plan["libraries"])
        ]
        for transport in ("torrents", "usenet"):
            download = storage_plan["downloads"][transport]
            if download["enabled"]:
                paths.extend(
                    [
                        (download["incomplete"], f"{transport}_incomplete"),
                        (download["complete"], f"{transport}_complete"),
                    ]
                )
    else:
        # Legacy plans without a storage-selection step retain the documented
        # default layout. Once storage is selected, only selected folders are
        # planned above.
        paths = [
            (layout["work_path"], "work"),
            (f"{layout['work_path']}/torrents", "torrent_work"),
            (f"{layout['work_path']}/usenet", "usenet_work"),
            (layout["downloads_path"], "downloads"),
            (f"{layout['downloads_path']}/torrents", "torrent_downloads"),
            (f"{layout['downloads_path']}/usenet", "usenet_downloads"),
            (layout["media_path"], "media"),
        ]
        for transport in ("torrents", "usenet"):
            paths.extend(
                (f"{layout['downloads_path']}/{transport}/{library}", f"{transport}_{library}")
                for library in _STANDARD_LIBRARY_NAMES_IN_ORDER
            )
        paths.extend(
            (f"{layout['media_path']}/{library}", f"media_{library}")
            for library in _STANDARD_LIBRARY_NAMES_IN_ORDER
        )
    return [
        {
            "action_id": f"directory:{purpose}",
            "type": "directory.ensure",
            "path": path,
            "purpose": purpose,
            "destructive": False,
        }
        for path, purpose in paths
    ]


def _storage_apply_blockers(storage_plan: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """Return schema/capability blockers; runtime safety is rechecked by the executor."""

    if storage_plan is None:
        return [
            {
                "code": "storage_selection_required",
                "message": "Select and review storage before applying this plan.",
            }
        ]
    blockers: list[dict[str, str]] = []
    topology = storage_plan["topology"]
    selected = storage_plan["selected_devices"]
    if storage_plan["encryption"] != "none":
        blockers.append(
            {
                "code": "encryption_plan_incomplete",
                "message": "Encryption requires a separate key-delivery and recovery plan.",
            }
        )
    if storage_plan["snapshots"] and storage_plan["topology"] != "zfs":
        blockers.append(
            {
                "code": "snapshot_policy_incomplete",
                "message": (
                    "Snapshot retention and recovery settings must be completed in Advanced."
                ),
            }
        )
    if topology in {"zfs", "raid", "snapraid"} and not storage_plan.get("layout_options"):
        blockers.append(
            {
                "code": f"{topology}_geometry_required",
                "message": (
                    "Choose the exact disk groups, parity, and recovery geometry in Advanced."
                ),
            }
        )
    elif topology == "cache":
        blockers.append(
            {
                "code": "cache_policy_required",
                "message": "Choose the backing storage and completed-download movement policy.",
            }
        )
    elif topology == "block":
        blockers.append(
            {
                "code": "block_target_required",
                "message": "Choose the target protocol, LUN identity, size, and access controls.",
            }
        )
    elif topology == "individual" and len(selected) != 1:
        blockers.append(
            {
                "code": "individual_drive_count_invalid",
                "message": "Individual storage requires exactly one selected drive per plan.",
            }
        )
    return blockers


def _connectivity_actions(
    connectivity: Mapping[str, Any], presentation_root: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    actions: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    if connectivity["skip"]:
        return actions, blockers
    root = PurePosixPath(presentation_root) if presentation_root else None
    for index, service in enumerate(connectivity["services"]):
        path = PurePosixPath(service["path"])
        if root is None or (path != root and root not in path.parents):
            blockers.append(
                {
                    "code": "connectivity_path_outside_storage",
                    "message": (
                        f"{service['protocol'].upper()} path {path} is outside the "
                        "reviewed storage root."
                    ),
                }
            )
            continue
        if service["protocol"] != "smb":
            blockers.append(
                {
                    "code": f"{service['protocol']}_executor_required",
                    "message": (
                        f"{service['protocol'].upper()} setup requires its privileged "
                        "connectivity executor before this plan can be applied."
                    ),
                }
            )
            continue
        actions.append(
            {
                "action_id": f"smb-share:{index + 1}",
                "type": "smb.share.ensure",
                "name": service["name"],
                "path": service["path"],
                "read_only": service["read_only"],
                "guest": False,
                "destructive": False,
            }
        )
    return actions, blockers


def _servarr_actions(
    applications: Mapping[str, Any],
    integrations: Mapping[str, IntegrationConnection],
    layout: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    roots: list[dict[str, Any]] = []
    overrides = applications["root_folder_paths"]
    for integration_id in applications["selected_integration_ids"]:
        connection = integrations[integration_id]
        product = connection.discovered_product or connection.expected_product
        definition = PRODUCTS.get(product)
        supports_roots = (
            definition is not None
            and "root_folders" in definition.declared_capabilities
            and "root_folders" in connection.capabilities_json
        )
        if not supports_roots:
            if integration_id in overrides:
                _validation(
                    f"applications.root_folder_paths.{integration_id}",
                    f"{product} did not advertise readable root-folder support",
                )
            continue
        path = overrides.get(integration_id)
        if path is None:
            library = _STANDARD_LIBRARY_NAMES.get(product)
            if library is None:
                _validation(
                    f"applications.root_folder_paths.{integration_id}",
                    f"{product} requires an explicitly confirmed library path",
                )
            path = f"{layout['media_path']}/{library}"
        roots.append(
            {
                "action_id": f"servarr-root:{integration_id}",
                "type": "servarr.root_folder.ensure",
                "integration_id": integration_id,
                "product": product,
                "path": path,
            }
        )

    mappings: list[dict[str, Any]] = []
    for index, mapping in enumerate(applications["remote_path_mappings"]):
        if mapping["remote_path"] == mapping["local_path"]:
            continue
        connection = integrations[mapping["integration_id"]]
        product = connection.discovered_product or connection.expected_product
        definition = PRODUCTS.get(product)
        supports_mappings = (
            definition is not None
            and "remote_path_mappings" in definition.declared_capabilities
            and "remote_path_mappings" in connection.capabilities_json
        )
        if not supports_mappings:
            _validation(
                f"applications.remote_path_mappings[{index}]",
                f"{product} did not advertise readable remote-path mapping support",
            )
        mappings.append(
            {
                "action_id": (f"servarr-remote-path:{mapping['integration_id']}:{index + 1}"),
                "type": "servarr.remote_path_mapping.ensure",
                "integration_id": mapping["integration_id"],
                "product": product,
                "host": mapping["host"],
                "remote_path": mapping["remote_path"],
                "local_path": mapping["local_path"],
            }
        )
    return roots, mappings


def _build_plan_document(
    session: Session,
    wizard: WizardSession,
) -> dict[str, Any]:
    raw_layout = wizard.answers_json.get("layout")
    raw_applications = wizard.answers_json.get("applications")
    if raw_layout is None:
        _validation("layout", "complete the layout step before reviewing the plan")
    if raw_applications is None:
        _validation("applications", "complete the applications step before reviewing the plan")
    layout = _validate_layout(raw_layout, mode=wizard.mode)
    applications = _validate_applications(raw_applications)
    connectivity = _validate_connectivity(
        wizard.answers_json.get("connectivity", {"skip": True, "services": []})
    )
    integrations = _load_integrations(session, applications["selected_integration_ids"])
    root_actions, mapping_actions = _servarr_actions(
        applications,
        integrations,
        layout,
    )
    storage_plan: dict[str, Any] | None = None
    raw_storage = wizard.answers_json.get("storage")
    if raw_storage is not None:
        if wizard.hardware_snapshot_id is None:
            _validation("hardware_snapshot_id", "storage plans require a discovery snapshot")
        snapshot = session.get(HardwareSnapshot, wizard.hardware_snapshot_id)
        if snapshot is None:
            raise WizardStateError("the bound hardware discovery snapshot is unavailable")
        try:
            storage_source = {
                key: value
                for key, value in raw_storage.items()
                if key not in {"format_decision", "warnings"}
            }
            storage = normalize_storage_answers(
                storage_source,
                mode=wizard.mode,
                snapshot_payload=snapshot.payload_json,
            )
            if storage != raw_storage:
                raise WizardStateError("the stored storage answers failed validation")
            storage_plan = build_storage_plan(
                storage,
                layout=layout,
                snapshot_id=snapshot.id,
                snapshot_sha256=snapshot.sha256,
                snapshot_payload=snapshot.payload_json,
            )
        except StoragePolicyError as exc:
            _validation(exc.field, exc.message)
    directory_actions = _directory_actions(layout, storage_plan)

    document: dict[str, Any] = {
        "schema_version": 2,
        "kind": WORKFLOW,
        "wizard_session_id": wizard.id,
        "workflow": {
            "name": WORKFLOW,
            "version": WORKFLOW_VERSION,
            "mode": wizard.mode,
        },
        "revision": wizard.revision,
        "apply_available": False,
        "blockers": [],
        "layout": layout,
        "actions": {
            "directories": directory_actions,
            "servarr_root_folders": root_actions,
            "servarr_remote_path_mappings": mapping_actions,
            "connectivity": [],
        },
        "connectivity": connectivity,
        "summary": {
            "directory_actions": len(directory_actions),
            "servarr_root_folder_actions": len(root_actions),
            "servarr_remote_path_mapping_actions": len(mapping_actions),
        },
    }
    if storage_plan is not None:
        document["storage"] = storage_plan
        presentation_root = posixpath.commonpath(tuple(layout.values()))
        if presentation_root == "/":
            document["blockers"].append(
                {
                    "code": "storage_presentation_root_ambiguous",
                    "message": "Storage paths must share one reviewed presentation root.",
                }
            )
        else:
            document["presentation_root"] = presentation_root
        connectivity_actions, connectivity_blockers = _connectivity_actions(
            connectivity, document.get("presentation_root")
        )
        document["actions"]["connectivity"] = connectivity_actions
        document["blockers"].extend(connectivity_blockers)
        document["summary"]["connectivity_actions"] = len(connectivity_actions)
        document["summary"]["selected_drives"] = len(storage_plan["selected_devices"])
        document["summary"]["storage_actions"] = len(storage_plan["actions"])
    document["blockers"].extend(_storage_apply_blockers(storage_plan))
    document["apply_available"] = not document["blockers"]
    return document


def create_plan(
    session: Session,
    *,
    wizard_id: str,
    expected_revision: int,
) -> Plan:
    wizard = get_wizard(session, wizard_id)
    if wizard.workflow != WORKFLOW or wizard.workflow_version != WORKFLOW_VERSION:
        raise WizardStateError("this service cannot plan the wizard workflow")
    if wizard.revision != expected_revision:
        raise WizardConflict(
            expected_revision=expected_revision,
            current_revision=wizard.revision,
        )
    if wizard.status not in MUTABLE_STATUSES:
        raise WizardStateError(
            "wizard cannot be planned in its current state",
            status=wizard.status,
        )
    if wizard.plan_id is not None:
        existing = session.get(Plan, wizard.plan_id)
        if existing is not None and existing.revision == expected_revision:
            return existing
        raise WizardStateError("wizard references an unavailable plan", status=wizard.status)

    document = _build_plan_document(session, wizard)
    plan = Plan(
        id=new_id(),
        wizard_session_id=wizard.id,
        revision=wizard.revision,
        kind=WORKFLOW,
        document_json=document,
        sha256=document_hash(document),
    )
    changed = session.execute(
        update(WizardSession)
        .where(
            WizardSession.id == wizard.id,
            WizardSession.revision == expected_revision,
            WizardSession.status.in_(MUTABLE_STATUSES),
            WizardSession.plan_id.is_(None),
        )
        .values(plan_id=plan.id, status="review", current_step="review", updated_at=utc_now())
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        session.expire_all()
        current = get_wizard(session, wizard_id)
        if current.revision != expected_revision:
            raise WizardConflict(
                expected_revision=expected_revision,
                current_revision=current.revision,
            )
        if current.plan_id:
            existing = session.get(Plan, current.plan_id)
            if existing is not None:
                return existing
        raise WizardStateError(
            "wizard cannot be planned in its current state",
            status=current.status,
        )
    session.add(plan)
    session.flush()
    _refresh(session, wizard)
    return plan


def refresh_plan_for_latest_discovery(
    session: Session,
    *,
    wizard_id: str,
    expected_revision: int,
) -> tuple[WizardSession, Plan, HardwareSnapshot]:
    """Rebind a draft to the latest discovery and rebuild its immutable plan."""
    wizard = get_wizard(session, wizard_id)
    if wizard.workflow != WORKFLOW or wizard.workflow_version != WORKFLOW_VERSION:
        raise WizardStateError("this service cannot refresh the wizard workflow")
    if wizard.revision != expected_revision:
        raise WizardConflict(
            expected_revision=expected_revision,
            current_revision=wizard.revision,
        )
    if wizard.status not in MUTABLE_STATUSES:
        raise WizardStateError(
            "wizard cannot be refreshed in its current state",
            status=wizard.status,
        )

    latest = _latest_snapshot(session)
    if latest is None:
        _validation("hardware_snapshot_id", "run storage discovery before refreshing the plan")

    if wizard.hardware_snapshot_id == latest.id:
        plan = create_plan(
            session,
            wizard_id=wizard.id,
            expected_revision=wizard.revision,
        )
        return get_wizard(session, wizard.id), plan, latest

    raw_storage = wizard.answers_json.get("storage")
    if not isinstance(raw_storage, Mapping):
        _validation("storage", "complete the storage questions before refreshing the plan")
    try:
        storage_source = {
            key: value
            for key, value in raw_storage.items()
            if key not in {"format_decision", "warnings"}
        }
        normalized_storage = normalize_storage_answers(
            storage_source,
            mode=wizard.mode,
            snapshot_payload=latest.payload_json,
        )
    except StoragePolicyError as exc:
        _validation(exc.field, exc.message)

    answers = dict(wizard.answers_json)
    answers["storage"] = normalized_storage
    changed = session.execute(
        update(WizardSession)
        .where(
            WizardSession.id == wizard.id,
            WizardSession.revision == expected_revision,
            WizardSession.status.in_(MUTABLE_STATUSES),
        )
        .values(
            hardware_snapshot_id=latest.id,
            answers_json=answers,
            current_step="review",
            revision=expected_revision + 1,
            status="draft",
            plan_id=None,
            updated_at=utc_now(),
        )
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        session.expire_all()
        current = get_wizard(session, wizard_id)
        raise WizardConflict(
            expected_revision=expected_revision,
            current_revision=current.revision,
        )

    refreshed = _refresh(session, wizard)
    plan = create_plan(
        session,
        wizard_id=refreshed.id,
        expected_revision=refreshed.revision,
    )
    return _refresh(session, refreshed), plan, latest


def _current_plan(session: Session, wizard: WizardSession) -> Plan:
    if wizard.plan_id is None:
        raise WizardConsentError(
            "Review the current plan before approving destructive actions.",
            reason="plan_not_created",
        )
    plan = session.get(Plan, wizard.plan_id)
    if plan is None:
        raise WizardStateError("the wizard plan is unavailable", status=wizard.status)
    if plan.revision != wizard.revision:
        raise WizardConsentError(
            "The wizard changed after this plan was created. Review a new plan.",
            reason="wizard_changed",
        )
    if not hmac.compare_digest(plan.sha256, document_hash(plan.document_json)):
        raise WizardStateError(
            "the immutable plan failed its integrity check",
            status=wizard.status,
        )
    return plan


def _latest_snapshot(session: Session) -> HardwareSnapshot | None:
    return session.scalar(
        select(HardwareSnapshot).order_by(
            HardwareSnapshot.captured_at.desc(),
            HardwareSnapshot.id.desc(),
        )
    )


def plan_approval_status(
    session: Session,
    *,
    wizard_id: str,
) -> dict[str, Any]:
    wizard = get_wizard(session, wizard_id)
    if wizard.plan_id is None:
        return {"required": False, "valid": False, "reason": "plan_not_created"}
    plan = session.get(Plan, wizard.plan_id)
    if plan is None:
        return {"required": False, "valid": False, "reason": "plan_unavailable"}
    storage = plan.document_json.get("storage")
    if not isinstance(storage, Mapping):
        return {"required": False, "valid": True, "reason": "not_required"}
    risk = storage.get("risk")
    if not isinstance(risk, Mapping) or risk.get("approval_required") is not True:
        return {"required": False, "valid": True, "reason": "not_required"}
    approval = session.scalar(select(PlanApproval).where(PlanApproval.plan_id == plan.id))
    if approval is None:
        return {
            "required": True,
            "valid": False,
            "reason": "not_approved",
            "required_phrase": REQUIRED_CONSENT_PHRASE,
        }
    if wizard.revision != approval.wizard_revision or wizard.plan_id != approval.plan_id:
        return {"required": True, "valid": False, "reason": "wizard_changed"}
    if not hmac.compare_digest(plan.sha256, approval.plan_sha256):
        return {"required": True, "valid": False, "reason": "plan_changed"}
    if not hmac.compare_digest(
        approval.confirmation_sha256,
        document_hash({"confirmation": REQUIRED_CONSENT_PHRASE}),
    ):
        return {"required": True, "valid": False, "reason": "approval_integrity_failed"}

    binding = storage.get("snapshot_binding")
    if not isinstance(binding, Mapping):
        return {"required": True, "valid": False, "reason": "snapshot_binding_missing"}
    if (
        approval.hardware_snapshot_id != binding.get("snapshot_id")
        or approval.hardware_snapshot_sha256 != binding.get("snapshot_sha256")
        or approval.device_binding_sha256 != binding.get("device_binding_sha256")
        or approval.selected_device_ids_json != binding.get("selected_device_ids")
    ):
        return {"required": True, "valid": False, "reason": "approval_binding_changed"}
    latest = _latest_snapshot(session)
    if (
        latest is None
        or latest.id != approval.hardware_snapshot_id
        or not hmac.compare_digest(latest.sha256, approval.hardware_snapshot_sha256)
    ):
        return {"required": True, "valid": False, "reason": "hardware_snapshot_changed"}
    try:
        selected = select_devices(latest.payload_json, approval.selected_device_ids_json)
    except StoragePolicyError:
        return {"required": True, "valid": False, "reason": "selected_device_changed"}
    if not hmac.compare_digest(document_hash(selected), approval.device_binding_sha256):
        return {"required": True, "valid": False, "reason": "selected_device_changed"}
    return {
        "required": True,
        "valid": True,
        "reason": "approved",
        "approval_id": approval.id,
        "approved_at": approval.approved_at,
        "plan_sha256": approval.plan_sha256,
        "hardware_snapshot_sha256": approval.hardware_snapshot_sha256,
        "selected_device_ids": approval.selected_device_ids_json,
    }


def approve_plan(
    session: Session,
    *,
    wizard_id: str,
    expected_revision: int,
    plan_sha256: str,
    hardware_snapshot_sha256: str,
    selected_device_ids: Sequence[str],
    confirmation: str,
    actor_type: str,
    actor_id: str,
) -> PlanApproval:
    wizard = get_wizard(session, wizard_id)
    if wizard.revision != expected_revision:
        raise WizardConflict(
            expected_revision=expected_revision,
            current_revision=wizard.revision,
        )
    plan = _current_plan(session, wizard)
    storage = plan.document_json.get("storage")
    if not isinstance(storage, Mapping):
        raise WizardStateError("this plan has no destructive storage actions")
    risk = storage.get("risk")
    if not isinstance(risk, Mapping) or risk.get("approval_required") is not True:
        raise WizardStateError("this plan does not require destructive approval")
    if confirmation != REQUIRED_CONSENT_PHRASE:
        raise WizardConsentError(
            "Type I AGREE exactly to approve the listed destructive actions.",
            reason="confirmation_phrase_mismatch",
        )
    if not hmac.compare_digest(plan.sha256, plan_sha256):
        raise WizardConsentError(
            "The plan changed. Review the current plan before approving it.",
            reason="plan_hash_mismatch",
        )
    binding = storage.get("snapshot_binding")
    if not isinstance(binding, Mapping):
        raise WizardStateError("the plan has no hardware snapshot binding")
    expected_snapshot_sha256 = binding.get("snapshot_sha256")
    if not isinstance(expected_snapshot_sha256, str) or not hmac.compare_digest(
        expected_snapshot_sha256, hardware_snapshot_sha256
    ):
        raise WizardConsentError(
            "Storage discovery changed. Review a new plan before approving it.",
            reason="snapshot_hash_mismatch",
        )
    planned_device_ids = binding.get("selected_device_ids")
    if not isinstance(planned_device_ids, list) or list(selected_device_ids) != planned_device_ids:
        raise WizardConsentError(
            "The selected drive list does not match the reviewed plan.",
            reason="selected_device_mismatch",
        )
    latest = _latest_snapshot(session)
    if (
        latest is None
        or latest.id != binding.get("snapshot_id")
        or not hmac.compare_digest(latest.sha256, expected_snapshot_sha256)
    ):
        raise WizardConsentError(
            "Storage discovery changed. Run discovery and review the plan again.",
            reason="hardware_snapshot_changed",
        )
    try:
        selected = select_devices(latest.payload_json, planned_device_ids)
    except StoragePolicyError as exc:
        raise WizardConsentError(
            "A selected drive changed or disappeared. Review a new plan.",
            reason="selected_device_changed",
        ) from exc
    selected_hash = document_hash(selected)
    expected_device_hash = binding.get("device_binding_sha256")
    if not isinstance(expected_device_hash, str) or not hmac.compare_digest(
        selected_hash, expected_device_hash
    ):
        raise WizardConsentError(
            "A selected drive changed. Review a new plan before approving it.",
            reason="selected_device_changed",
        )
    existing = session.scalar(select(PlanApproval).where(PlanApproval.plan_id == plan.id))
    if existing is not None:
        status = plan_approval_status(session, wizard_id=wizard.id)
        if status["valid"]:
            return existing
        raise WizardConsentError(
            "The prior approval is no longer valid. Review a new plan.",
            reason=str(status["reason"]),
        )
    approval = PlanApproval(
        id=new_id(),
        plan_id=plan.id,
        wizard_session_id=wizard.id,
        wizard_revision=wizard.revision,
        plan_sha256=plan.sha256,
        hardware_snapshot_id=latest.id,
        hardware_snapshot_sha256=latest.sha256,
        device_binding_sha256=selected_hash,
        selected_device_ids_json=list(planned_device_ids),
        confirmation_sha256=document_hash({"confirmation": confirmation}),
        actor_type=actor_type,
        actor_id=actor_id,
    )
    session.add(approval)
    session.flush()
    return approval


def require_current_plan_approval(session: Session, *, wizard_id: str) -> None:
    status = plan_approval_status(session, wizard_id=wizard_id)
    if status["required"] and not status["valid"]:
        raise WizardConsentError(
            "Review the exact drives and actions, then type I AGREE before applying this plan.",
            reason=str(status["reason"]),
        )


def cancel_wizard(
    session: Session,
    *,
    wizard_id: str,
    expected_revision: int,
) -> WizardSession:
    wizard = get_wizard(session, wizard_id)
    if wizard.revision != expected_revision:
        raise WizardConflict(
            expected_revision=expected_revision,
            current_revision=wizard.revision,
        )
    if wizard.status == "cancelled":
        return wizard
    if wizard.status not in MUTABLE_STATUSES:
        raise WizardStateError(
            "wizard cannot be cancelled in its current state",
            status=wizard.status,
        )
    changed = session.execute(
        update(WizardSession)
        .where(
            WizardSession.id == wizard.id,
            WizardSession.revision == expected_revision,
            WizardSession.status.in_(MUTABLE_STATUSES),
        )
        .values(
            status="cancelled",
            current_step="cancelled",
            revision=expected_revision + 1,
            plan_id=None,
            updated_at=utc_now(),
        )
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        session.expire_all()
        current = get_wizard(session, wizard_id)
        raise WizardConflict(
            expected_revision=expected_revision,
            current_revision=current.revision,
        )
    return _refresh(session, wizard)
