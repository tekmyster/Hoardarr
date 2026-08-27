from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from hoardarr.connectivity import executor, lio_readback
from hoardarr.connectivity.service import config_hash


class LifecycleGuardError(ValueError):
    pass


class NodeParityError(ValueError):
    def __init__(self, code: str, *, record_count: int = 0) -> None:
        self.code = code
        self.record_count = record_count
        super().__init__("initiator node parity could not be established")


class DiagnosticError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("bounded diagnostic validation failed")


CLASSIFICATIONS = {
    "PARITY_MISMATCH_IDENTIFIED",
    "LOGIN_FAILURE_DIAGNOSED",
    "LOGIN_FAILURE_UNRESOLVED",
    "LOGIN_SUCCEEDED_LIFECYCLE_RESULT",
    "LOGIN_SUCCEEDED_PAYLOAD_VERIFIED_RAW_TRANSITION",
    "HARNESS_ERROR",
}
CLEANUP_CLASSIFICATIONS = {
    "cleanup_complete",
    "cleanup_incomplete_bounded",
    "cleanup_not_started",
}
DIAGNOSTIC_LIMIT = 16 * 1024
NODE_RECORD_LIMIT = 16 * 1024
LOOP_HOLDER_LIMIT = 8
PROTOCOL_STATUS_PATTERN = re.compile(
    rb"(?m)^.*\blogin response status ([0-9]{2})([0-9]{2})\s*$"
)
PROTOCOL_STATUS_MARKER = re.compile(rb"login response status")
PROTOCOL_MEANINGS = {
    (1, 0): "REDIRECT_TEMPORARY",
    (1, 1): "REDIRECT_PERMANENT",
    (2, 0): "INITIATOR_ERROR",
    (2, 1): "AUTHENTICATION_FAILURE",
    (2, 2): "AUTHORIZATION_FAILURE",
    (2, 3): "TARGET_NOT_FOUND",
    (2, 4): "TARGET_REMOVED",
    (2, 5): "UNSUPPORTED_VERSION",
    (2, 6): "TOO_MANY_CONNECTIONS",
    (2, 7): "MISSING_PARAMETER",
    (2, 8): "CANNOT_INCLUDE_IN_SESSION",
    (2, 9): "SESSION_TYPE_NOT_SUPPORTED",
    (2, 10): "SESSION_DOES_NOT_EXIST",
    (2, 11): "INVALID_REQUEST",
    (3, 0): "TARGET_ERROR",
    (3, 1): "SERVICE_UNAVAILABLE",
    (3, 2): "OUT_OF_RESOURCES",
}
PROTOCOL_DIAGNOSES = {
    (1, 0): "protocol_redirection",
    (1, 1): "protocol_redirection",
    (2, 0): "initiator_error",
    (2, 1): "credential_rejection",
    (2, 2): "acl_rejection",
    (2, 3): "target_not_found",
    (2, 4): "target_removed",
    (2, 5): "unsupported_version",
    (2, 6): "too_many_connections",
    (2, 7): "missing_parameter",
    (2, 8): "session_inclusion_rejection",
    (2, 9): "unsupported_session_type",
    (2, 10): "session_state_rejection",
    (2, 11): "invalid_request",
    (3, 0): "target_error",
    (3, 1): "service_unavailable",
    (3, 2): "target_resource_exhausted",
}
DIAGNOSED_CLASSES = {None, *PROTOCOL_DIAGNOSES.values()}
LOOP_RELEASE_PRECHECKS = {
    "ORIGINAL_OWNED",
    "ABSENT",
    "DIFFERENT_BACKING",
    "IDENTITY_CHANGED",
    "UNSAFE",
}
LOOP_RELEASE_POST_STATES = {
    "ABSENT",
    "ORIGINAL_OWNED",
    "DIFFERENT_BACKING",
    "UNSAFE",
}
LOOP_RELEASE_STDERR_CLASSES = {
    "DEVICE_BUSY",
    "NO_SUCH_DEVICE",
    "INVALID_ARGUMENT_OR_OPTION",
    "PERMISSION_DENIED",
    "EMPTY",
    "UNCLASSIFIED_BOUNDED",
}
LOOP_HOLDER_PROBE_STATES = {
    "COMPLETE",
    "OVER_LIMIT",
    "INVALID_NAME",
    "PROBE_ERROR",
    "NOT_APPLICABLE",
}
LOOP_RELEASE_PROBE_STATES = {"RELEASED", "STILL_MAPPED", "PROBE_ERROR"}
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
CLEANUP_PHASES = [
    "unmount",
    "logout",
    "node_delete",
    "target_delete",
    "backstore_delete",
    "saveconfig",
    "pool_destroy",
    *(f"loop_detach_{number}" for number in range(1, 7)),
    "initiator_restore",
    "work_root_remove",
    "runner_marker_remove",
]
CLEANUP_TIMEOUTS = [8, 8, 8, 10, 10, 10, 25, 5, 5, 5, 5, 5, 5, 8, 10, 3]
PARITY_KEYS = {
    "schema_version",
    "exact",
    "mismatch",
    "record_count",
    "auth_method_chap",
    "username_match",
    "password_match",
    "record_count_exact",
    "record_safe",
    "username_length",
    "password_length",
    "target_identity_sha256",
    "initiator_identity_sha256",
    "parity_sha256",
}
DIAGNOSTIC_CLASSIFICATIONS = {
    "acl_rejection",
    "authentication_method_rejection",
    "credential_rejection",
    "transport_rejection",
    "generic_login_rejection",
    "unclassified_bounded",
}
RAW_INTEGRITY_STAGES = (
    "after_logout",
    "after_idempotent_apply",
    "after_state_only_reconciliation",
    "after_saveconfig",
    "after_target_persistence_restart",
    "after_persistence_readback",
    "after_post_restart_idempotent_apply",
)


def validate_guard(
    *,
    effective_uid: int,
    github_actions: str,
    marker_exists: bool,
    work_root: str,
    loop_pairs: list[tuple[str, str]],
) -> None:
    if effective_uid != 0:
        raise LifecycleGuardError("root is required")
    if github_actions != "true":
        raise LifecycleGuardError("GitHub Actions is required")
    if not marker_exists:
        raise LifecycleGuardError("the disposable-runner marker is required")
    root = PurePosixPath(work_root)
    if (
        not root.is_absolute()
        or root.parent != PurePosixPath("/tmp")
        or not root.name.startswith("hoardarr-managed-zvol.")
        or not root.name.removeprefix("hoardarr-managed-zvol.")
    ):
        raise LifecycleGuardError("the cleanup root is unsafe")
    if loop_pairs:
        devices = [device for device, _backing in loop_pairs]
        backings = [PurePosixPath(backing) for _device, backing in loop_pairs]
        if (
            len(loop_pairs) != 6
            or len(set(devices)) != 6
            or len(set(backings)) != 6
            or any(
                re.fullmatch(r"/dev/loop[0-9]+", device) is None for device in devices
            )
            or any(backing.parent != root for backing in backings)
            or {backing.name for backing in backings}
            != {f"disk{number}.img" for number in range(1, 7)}
        ):
            raise LifecycleGuardError("loop ownership could not be proven")


def _safe_owned_directory(path: Path, *, expected_uid: int) -> None:
    facts = path.lstat()
    if (
        not stat.S_ISDIR(facts.st_mode)
        or stat.S_ISLNK(facts.st_mode)
        or facts.st_uid != expected_uid
        or stat.S_IMODE(facts.st_mode) & 0o022
    ):
        raise NodeParityError("NODE_RECORD_UNSAFE")


def inspect_node_parity(
    *,
    node_root: Path,
    target_iqn: str,
    portal: str,
    initiator_iqn: str,
    chap_user: str,
    chap_value: str,
    expected_uid: int = 0,
) -> dict[str, Any]:
    if (
        not node_root.is_absolute()
        or re.fullmatch(r"iqn\.[A-Za-z0-9._:\-]+", target_iqn) is None
        or re.fullmatch(r"127\.0\.0\.[0-9]{1,3}", portal) is None
    ):
        raise NodeParityError("NODE_RECORD_UNSAFE")
    try:
        _safe_owned_directory(node_root, expected_uid=expected_uid)
        target_root = node_root / target_iqn
        _safe_owned_directory(target_root, expected_uid=expected_uid)
    except FileNotFoundError as exc:
        raise NodeParityError("NODE_RECORD_ZERO") from exc
    candidates: list[Path] = []
    prefix = f"{portal},3260,"
    try:
        entries = list(os.scandir(target_root))
    except OSError as exc:
        raise NodeParityError("NODE_RECORD_UNSAFE") from exc
    for entry in entries:
        if not entry.name.startswith(prefix):
            continue
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            raise NodeParityError("NODE_RECORD_UNSAFE")
        record = Path(entry.path) / "default"
        if record.exists() or record.is_symlink():
            candidates.append(record)
    if len(candidates) != 1:
        raise NodeParityError(
            "NODE_RECORD_ZERO" if not candidates else "NODE_RECORD_MULTIPLE",
            record_count=len(candidates),
        )
    record = candidates[0]
    portal_root = record.parent
    try:
        _safe_owned_directory(portal_root, expected_uid=expected_uid)
        root_resolved = node_root.resolve(strict=True)
        record_resolved = record.resolve(strict=True)
        record_resolved.relative_to(root_resolved)
        named = record.lstat()
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise NodeParityError("NODE_RECORD_UNSAFE", record_count=1) from exc
    if (
        not stat.S_ISREG(named.st_mode)
        or stat.S_ISLNK(named.st_mode)
        or named.st_uid != expected_uid
        or stat.S_IMODE(named.st_mode) != 0o600
        or named.st_nlink != 1
        or not 0 < named.st_size <= NODE_RECORD_LIMIT
    ):
        raise NodeParityError("NODE_RECORD_UNSAFE", record_count=1)
    descriptor = -1
    try:
        descriptor = os.open(
            record,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (
            (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_uid != expected_uid
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
        ):
            raise NodeParityError("NODE_RECORD_UNSAFE", record_count=1)
        raw = os.read(descriptor, NODE_RECORD_LIMIT + 1)
    except OSError as exc:
        raise NodeParityError("NODE_RECORD_UNSAFE", record_count=1) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > NODE_RECORD_LIMIT:
        raise NodeParityError("NODE_RECORD_UNSAFE", record_count=1)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NodeParityError("NODE_RECORD_UNSAFE", record_count=1) from exc
    if any(ord(character) < 32 and character not in "\n\r\t" for character in text):
        raise NodeParityError("NODE_RECORD_UNSAFE", record_count=1)
    selected: dict[str, str] = {}
    relevant = {
        "node.session.auth.authmethod",
        "node.session.auth.username",
        "node.session.auth.password",
    }
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or key not in relevant:
            continue
        if key in selected:
            raise NodeParityError("NODE_RECORD_UNSAFE", record_count=1)
        selected[key] = value.strip()
    method = selected.get("node.session.auth.authmethod", "")
    username = selected.get("node.session.auth.username", "")
    password = selected.get("node.session.auth.password", "")
    boolean_parity = {
        "auth_method_chap": hmac.compare_digest(method, "CHAP"),
        "username_match": hmac.compare_digest(username, chap_user),
        "password_match": hmac.compare_digest(password, chap_value),
        "record_count_exact": True,
        "record_safe": True,
    }
    mismatch = "NONE"
    if not boolean_parity["auth_method_chap"]:
        mismatch = "AUTH_METHOD_MISMATCH"
    elif not boolean_parity["username_match"]:
        mismatch = "USERNAME_MISMATCH"
    elif not boolean_parity["password_match"]:
        mismatch = "PASSWORD_MISMATCH"
    return {
        "schema_version": 1,
        "exact": all(boolean_parity.values()),
        "mismatch": mismatch,
        "record_count": 1,
        **boolean_parity,
        "username_length": len(username),
        "password_length": len(password),
        "target_identity_sha256": _digest(target_iqn),
        "initiator_identity_sha256": _digest(initiator_iqn),
        "parity_sha256": hashlib.sha256(
            json.dumps(boolean_parity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def parity_failure(
    error: NodeParityError, *, target_iqn: str, initiator_iqn: str
) -> dict[str, Any]:
    boolean_parity = {
        "auth_method_chap": False,
        "username_match": False,
        "password_match": False,
        "record_count_exact": False,
        "record_safe": error.code not in {"NODE_RECORD_UNSAFE"},
    }
    return {
        "schema_version": 1,
        "exact": False,
        "mismatch": error.code,
        "record_count": error.record_count,
        **boolean_parity,
        "username_length": 0,
        "password_length": 0,
        "target_identity_sha256": _digest(target_iqn),
        "initiator_identity_sha256": _digest(initiator_iqn),
        "parity_sha256": hashlib.sha256(
            json.dumps(boolean_parity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def sanitize_diagnostic_bytes(
    raw: bytes, *, secret: str, label: str, maximum: int = DIAGNOSTIC_LIMIT
) -> dict[str, Any]:
    if len(raw) > maximum:
        raise DiagnosticError("DIAGNOSTIC_OVERFLOW")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DiagnosticError("DIAGNOSTIC_UTF8_INVALID") from exc
    if any(ord(character) < 32 and character not in "\n\r\t" for character in text):
        raise DiagnosticError("DIAGNOSTIC_CONTROL_REJECTED")
    if secret and secret in text:
        raise DiagnosticError("DIAGNOSTIC_SECRET_REJECTED")
    if re.search(r"(?i)(password|secret)\s*[:=]\s*\S+", text):
        raise DiagnosticError("DIAGNOSTIC_SECRET_REJECTED")
    patterns = [
        (
            "acl_rejection",
            r"(?i)(initiator.*(?:not found|not allowed)|\bacl\b.*reject)",
        ),
        (
            "authentication_method_rejection",
            r"(?i)(auth(?:entication)? method.*(?:reject|unsupported|mismatch|fail)|chap.*not supported)",
        ),
        (
            "credential_rejection",
            r"(?i)(credential.*(?:reject|invalid|fail)|authentication failure|authorization failure|chap authentication.*fail)",
        ),
        (
            "transport_rejection",
            r"(?i)(connection refused|no route|transport.*fail|timed out)",
        ),
        (
            "generic_login_rejection",
            r"(?i)(could not login|login failure|initiator reported error)",
        ),
    ]
    classifications = [name for name, pattern in patterns if re.search(pattern, text)]
    if not classifications and text.strip():
        classifications = ["unclassified_bounded"]
    return {
        "label": label,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "classifications": classifications,
    }


def protocol_status_from_stderr(raw: bytes, *, final_status: int) -> dict[str, Any]:
    marker_lines = [
        line for line in raw.splitlines() if PROTOCOL_STATUS_MARKER.search(line)
    ]
    matches = [PROTOCOL_STATUS_PATTERN.fullmatch(line) for line in marker_lines]
    if marker_lines and (len(marker_lines) != 1 or matches[0] is None):
        raise DiagnosticError("PROTOCOL_STATUS_MALFORMED")
    if not marker_lines:
        return {
            "observed": False,
            "status_class": None,
            "status_detail": None,
            "meaning": "NONE",
            "source_label": None,
        }
    match = matches[0]
    assert match is not None
    status_class = int(match.group(1))
    status_detail = int(match.group(2))
    if status_class not in {0, 1, 2, 3} or (status_class == 0 and status_detail != 0):
        raise DiagnosticError("PROTOCOL_STATUS_OUT_OF_RANGE")
    if (status_class == 0) != (final_status == 0):
        raise DiagnosticError("PROTOCOL_STATUS_INCONSISTENT")
    return {
        "observed": True,
        "status_class": status_class,
        "status_detail": status_detail,
        "meaning": PROTOCOL_MEANINGS.get((status_class, status_detail), "NONE"),
        "source_label": "stderr",
    }


def protocol_diagnosed_class(protocol_status: dict[str, Any]) -> str | None:
    if protocol_status.get("observed") is not True:
        return None
    return PROTOCOL_DIAGNOSES.get(
        (protocol_status.get("status_class"), protocol_status.get("status_detail"))
    )


def _read_safe_diagnostic(path: Path, *, expected_uid: int = 0) -> bytes:
    try:
        named = path.lstat()
    except OSError as exc:
        raise DiagnosticError("DIAGNOSTIC_FILE_UNSAFE") from exc
    if (
        not stat.S_ISREG(named.st_mode)
        or stat.S_ISLNK(named.st_mode)
        or named.st_uid != expected_uid
        or stat.S_IMODE(named.st_mode) != 0o600
        or named.st_nlink != 1
        or named.st_size > DIAGNOSTIC_LIMIT
    ):
        raise DiagnosticError("DIAGNOSTIC_FILE_UNSAFE")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (
            (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_uid != expected_uid
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or opened.st_size > DIAGNOSTIC_LIMIT
        ):
            raise DiagnosticError("DIAGNOSTIC_FILE_UNSAFE")
        raw = os.read(descriptor, DIAGNOSTIC_LIMIT + 1)
    except OSError as exc:
        raise DiagnosticError("DIAGNOSTIC_FILE_UNSAFE") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return raw


def tpg_authentication_from_saveconfig(
    document: object, *, target_iqn: str
) -> dict[str, object]:
    if not isinstance(document, dict) or not isinstance(target_iqn, str):
        raise LifecycleGuardError("TPG authentication could not be safely read")
    targets = document.get("targets")
    if (
        not isinstance(targets, list)
        or len(targets) > lio_readback.MAX_COLLECTION_ENTRIES
    ):
        raise LifecycleGuardError("TPG authentication could not be safely read")
    matches: list[dict[str, Any]] = []
    for candidate in targets:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("wwn"), str):
            raise LifecycleGuardError("TPG authentication could not be safely read")
        if candidate["wwn"] == target_iqn:
            matches.append(candidate)
    if len(matches) != 1:
        raise LifecycleGuardError("TPG authentication could not be safely read")
    tpgs = matches[0].get("tpgs")
    if (
        not isinstance(tpgs, list)
        or len(tpgs) != 1
        or len(tpgs) > lio_readback.MAX_COLLECTION_ENTRIES
        or not isinstance(tpgs[0], dict)
        or tpgs[0].get("tag") != 1
        or isinstance(tpgs[0].get("tag"), bool)
    ):
        raise LifecycleGuardError("TPG authentication could not be safely read")
    attributes = tpgs[0].get("attributes")
    authentication = (
        attributes.get("authentication") if isinstance(attributes, dict) else None
    )
    if (
        not isinstance(authentication, int)
        or isinstance(authentication, bool)
        or authentication not in {0, 1}
    ):
        raise LifecycleGuardError("TPG authentication could not be safely read")
    return {"schema_version": 1, "observed": True, "enabled": authentication == 1}


def read_effective_tpg_authentication(
    path: Path, *, target_iqn: str
) -> dict[str, object]:
    try:
        document = lio_readback.read_saveconfig(path)
    except lio_readback.LioReadbackError as exc:
        raise LifecycleGuardError(
            "TPG authentication could not be safely read"
        ) from exc
    return tpg_authentication_from_saveconfig(document, target_iqn=target_iqn)


def loop_release_postcondition(evidence: dict[str, Any]) -> bool:
    if evidence.get("post_detach_state") == "ABSENT":
        return True
    return (
        evidence.get("post_detach_state") == "DIFFERENT_BACKING"
        and evidence.get("owned_image_released") is True
        and evidence.get("release_probe_state") == "RELEASED"
        and evidence.get("holder_count") == 0
        and evidence.get("holder_probe_state") == "COMPLETE"
    )


def validate_receipt(document: object) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("schema_version") != 2:
        raise LifecycleGuardError("receipt schema is invalid")
    if set(document) != {
        "schema_version",
        "classification",
        "workflow",
        "job",
        "run_id",
        "failure",
        "topology",
        "prelogin",
        "parity",
        "login",
        "downstream",
        "payload_verification",
        "raw_integrity_timeline",
        "cleanup",
        "prohibited_actions",
    }:
        raise LifecycleGuardError("receipt schema is invalid")
    if (
        document.get("workflow") != "storage-integration"
        or document.get("job") != "managed-zvol-lio-lifecycle"
        or not isinstance(document.get("run_id"), str)
        or not document["run_id"]
    ):
        raise LifecycleGuardError("receipt identity is invalid")
    if document.get("classification") not in CLASSIFICATIONS:
        raise LifecycleGuardError("receipt classification is invalid")
    failure = document.get("failure")
    downstream = document.get("downstream")
    payload_verification = document.get("payload_verification")
    if (
        not isinstance(failure, dict)
        or set(failure) != {"code", "status", "line"}
        or not isinstance(failure.get("code"), str)
        or not failure["code"]
        or not isinstance(failure.get("status"), int)
        or isinstance(failure.get("status"), bool)
        or not isinstance(failure.get("line"), int)
        or isinstance(failure.get("line"), bool)
        or failure["line"] < 0
        or not isinstance(downstream, dict)
        or set(downstream)
        != {
            "bounded_io",
            "idempotent_apply",
            "state_only_recovery",
            "target_persistence_restart",
            "persistence_control_plane",
            "remove_absence",
            "backing_retained",
        }
        or any(not isinstance(value, bool) for value in downstream.values())
        or not isinstance(payload_verification, dict)
        or set(payload_verification) != {"attempted", "matched"}
        or any(not isinstance(value, bool) for value in payload_verification.values())
        or (
            payload_verification["matched"] is True
            and payload_verification["attempted"] is not True
        )
    ):
        raise LifecycleGuardError("receipt lifecycle result is invalid")
    parity = document.get("parity")
    prelogin = document.get("prelogin")
    login = document.get("login")
    cleanup = document.get("cleanup")
    raw_integrity_timeline = document.get("raw_integrity_timeline")
    if (
        not isinstance(parity, dict)
        or not isinstance(prelogin, dict)
        or not isinstance(login, dict)
        or not isinstance(cleanup, dict)
        or not isinstance(raw_integrity_timeline, dict)
    ):
        raise LifecycleGuardError("receipt sections are invalid")
    tpg_authentication = prelogin.get("tpg_authentication")
    if (
        set(prelogin)
        != {
            "production_apply_passed",
            "production_readback_passed",
            "tpg_authentication",
        }
        or not isinstance(prelogin.get("production_apply_passed"), bool)
        or not isinstance(prelogin.get("production_readback_passed"), bool)
        or not isinstance(tpg_authentication, dict)
        or set(tpg_authentication) != {"schema_version", "observed", "enabled"}
        or tpg_authentication.get("schema_version") != 1
        or not isinstance(tpg_authentication.get("observed"), bool)
    ):
        raise LifecycleGuardError("receipt prelogin is invalid")
    if tpg_authentication["observed"]:
        if not isinstance(tpg_authentication.get("enabled"), bool):
            raise LifecycleGuardError("receipt prelogin is invalid")
    elif (
        document["classification"] != "HARNESS_ERROR"
        or tpg_authentication.get("enabled") is not None
    ):
        raise LifecycleGuardError("receipt prelogin is invalid")
    topology = document.get("topology")
    if not isinstance(topology, dict) or topology.get("raw_paths_emitted") is not False:
        raise LifecycleGuardError("receipt topology is invalid")
    counts = tuple(
        topology.get(key)
        for key in (
            "loop_count",
            "raidz2_vdev_count",
            "raidz2_member_count",
            "zvol_count",
        )
    )
    if document["classification"] == "HARNESS_ERROR":
        if (
            any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in counts
            )
            or not 0 <= counts[0] <= 6
            or counts[1] not in {0, 1}
            or counts[2] not in {0, 6}
            or counts[3] not in {0, 1}
        ):
            raise LifecycleGuardError("receipt topology is invalid")
    elif counts != (6, 1, 6, 1):
        raise LifecycleGuardError("receipt topology is invalid")
    login_count = login.get("attempt_count")
    if login_count not in {0, 1}:
        raise LifecycleGuardError("receipt login count is invalid")
    parity_placeholder = (
        document["classification"] == "HARNESS_ERROR"
        and parity.get("mismatch") == "NOT_RUN"
    )
    if (
        set(parity) != PARITY_KEYS
        or parity.get("schema_version") != 1
        or parity.get("mismatch")
        not in {
            "NONE",
            "NOT_RUN",
            "AUTH_METHOD_MISMATCH",
            "USERNAME_MISMATCH",
            "PASSWORD_MISMATCH",
            "NODE_RECORD_ZERO",
            "NODE_RECORD_MULTIPLE",
            "NODE_RECORD_UNSAFE",
        }
        or not isinstance(parity.get("exact"), bool)
        or any(
            not isinstance(parity.get(key), bool)
            for key in (
                "auth_method_chap",
                "username_match",
                "password_match",
                "record_count_exact",
                "record_safe",
            )
        )
        or any(
            not isinstance(parity.get(key), int)
            or isinstance(parity.get(key), bool)
            or not 0 <= parity[key] <= NODE_RECORD_LIMIT
            for key in ("record_count", "username_length", "password_length")
        )
        or (
            not parity_placeholder
            and any(
                re.fullmatch(r"[0-9a-f]{64}", str(parity.get(key))) is None
                for key in (
                    "target_identity_sha256",
                    "initiator_identity_sha256",
                    "parity_sha256",
                )
            )
        )
    ):
        raise LifecycleGuardError("receipt parity is invalid")
    login_status = login.get("status")
    if (
        not isinstance(login_status, int)
        or isinstance(login_status, bool)
        or login.get("succeeded") is not (login_status == 0)
    ):
        raise LifecycleGuardError("receipt login status is invalid")
    diagnostic = login.get("diagnostic")
    if (
        not isinstance(diagnostic, dict)
        or set(diagnostic)
        != {
            "schema_version",
            "status",
            "streams",
            "ordered_classifications",
            "diagnosed_class",
            "protocol_status",
        }
        or diagnostic.get("schema_version") != 3
        or diagnostic.get("status") != login_status
        or not isinstance(diagnostic.get("streams"), list)
        or len(diagnostic["streams"]) > 4
        or not isinstance(diagnostic.get("ordered_classifications"), list)
        or len(diagnostic["ordered_classifications"])
        != len(set(diagnostic["ordered_classifications"]))
        or not set(diagnostic["ordered_classifications"]).issubset(
            DIAGNOSTIC_CLASSIFICATIONS
        )
        or diagnostic.get("diagnosed_class") not in DIAGNOSED_CLASSES
    ):
        raise LifecycleGuardError("receipt diagnostic is invalid")
    protocol_status = diagnostic["protocol_status"]
    if (
        not isinstance(protocol_status, dict)
        or set(protocol_status)
        != {"observed", "status_class", "status_detail", "meaning", "source_label"}
        or not isinstance(protocol_status.get("observed"), bool)
        or protocol_status.get("meaning") not in {"NONE", *PROTOCOL_MEANINGS.values()}
    ):
        raise LifecycleGuardError("receipt protocol status is invalid")
    observed = protocol_status["observed"]
    status_class = protocol_status.get("status_class")
    status_detail = protocol_status.get("status_detail")
    if not observed:
        if (
            status_class is not None
            or status_detail is not None
            or protocol_status.get("meaning") != "NONE"
            or protocol_status.get("source_label") is not None
        ):
            raise LifecycleGuardError("receipt protocol status is invalid")
    elif (
        not isinstance(status_class, int)
        or isinstance(status_class, bool)
        or status_class not in {0, 1, 2, 3}
        or not isinstance(status_detail, int)
        or isinstance(status_detail, bool)
        or not 0 <= status_detail <= 99
        or protocol_status.get("source_label") != "stderr"
        or protocol_status.get("meaning")
        != PROTOCOL_MEANINGS.get((status_class, status_detail), "NONE")
        or (status_class == 0 and status_detail != 0)
        or ((status_class == 0) != (login_status == 0))
    ):
        raise LifecycleGuardError("receipt protocol status is invalid")
    if (
        document["classification"] != "HARNESS_ERROR"
        and len(diagnostic["streams"]) != login_count * 4
    ):
        raise LifecycleGuardError("receipt diagnostic stream count is invalid")
    labels: set[str] = set()
    for stream in diagnostic["streams"]:
        if (
            not isinstance(stream, dict)
            or set(stream) != {"label", "size_bytes", "sha256", "classifications"}
            or stream.get("label")
            not in {"stdout", "stderr", "iscsid_target", "kernel_target"}
            or stream["label"] in labels
            or not isinstance(stream.get("size_bytes"), int)
            or isinstance(stream.get("size_bytes"), bool)
            or not 0 <= stream["size_bytes"] <= DIAGNOSTIC_LIMIT
            or re.fullmatch(r"[0-9a-f]{64}", str(stream.get("sha256"))) is None
            or not isinstance(stream.get("classifications"), list)
            or not set(stream["classifications"]).issubset(DIAGNOSTIC_CLASSIFICATIONS)
        ):
            raise LifecycleGuardError("receipt diagnostic stream is invalid")
        labels.add(stream["label"])
    if login_count == 1 and labels != {
        "stdout",
        "stderr",
        "iscsid_target",
        "kernel_target",
    }:
        raise LifecycleGuardError("receipt diagnostic labels are invalid")
    if diagnostic["diagnosed_class"] != protocol_diagnosed_class(protocol_status):
        raise LifecycleGuardError("receipt diagnosis lacks protocol evidence")
    if document["classification"] == "PARITY_MISMATCH_IDENTIFIED" and login_count != 0:
        raise LifecycleGuardError("parity mismatch attempted login")
    if (
        document["classification"] == "PARITY_MISMATCH_IDENTIFIED"
        and parity.get("exact") is not False
    ):
        raise LifecycleGuardError("parity mismatch receipt is inconsistent")
    if (
        parity.get("exact") is True
        and document["classification"] != "HARNESS_ERROR"
        and login_count != 1
    ):
        raise LifecycleGuardError("exact parity did not make one login attempt")
    if document["classification"] != "HARNESS_ERROR" and (
        prelogin["production_apply_passed"] is not True
        or prelogin["production_readback_passed"] is not True
        or tpg_authentication["observed"] is not True
    ):
        raise LifecycleGuardError("receipt prelogin is incomplete")
    if document["classification"] in {
        "LOGIN_FAILURE_DIAGNOSED",
        "LOGIN_FAILURE_UNRESOLVED",
        "LOGIN_SUCCEEDED_LIFECYCLE_RESULT",
        "LOGIN_SUCCEEDED_PAYLOAD_VERIFIED_RAW_TRANSITION",
    } and (parity.get("exact") is not True or login_count != 1):
        raise LifecycleGuardError("login classification lacks exact parity")
    if (
        document["classification"]
        in {
            "LOGIN_FAILURE_DIAGNOSED",
            "LOGIN_FAILURE_UNRESOLVED",
        }
        and login_status == 0
    ):
        raise LifecycleGuardError("login failure classification is inconsistent")
    if (
        document["classification"] == "LOGIN_FAILURE_DIAGNOSED"
        and diagnostic["diagnosed_class"] is None
    ):
        raise LifecycleGuardError("diagnosed login failure lacks diagnosis")
    if (
        document["classification"] == "LOGIN_FAILURE_UNRESOLVED"
        and diagnostic["diagnosed_class"] is not None
    ):
        raise LifecycleGuardError("unresolved login failure contains diagnosis")
    if (
        document["classification"]
        in {
            "LOGIN_SUCCEEDED_LIFECYCLE_RESULT",
            "LOGIN_SUCCEEDED_PAYLOAD_VERIFIED_RAW_TRANSITION",
        }
        and login.get("succeeded") is not True
    ):
        raise LifecycleGuardError("successful login classification is inconsistent")
    if (
        set(raw_integrity_timeline)
        != {
            "schema_version",
            "checkpoints",
            "first_mismatch_stage",
            "final_comparison_attempted",
        }
        or raw_integrity_timeline.get("schema_version") != 1
    ):
        raise LifecycleGuardError("raw integrity timeline is invalid")
    checkpoints = raw_integrity_timeline.get("checkpoints")
    first_mismatch_stage = raw_integrity_timeline.get("first_mismatch_stage")
    if (
        not isinstance(checkpoints, list)
        or len(checkpoints) > len(RAW_INTEGRITY_STAGES)
        or first_mismatch_stage not in {"NONE", *RAW_INTEGRITY_STAGES}
        or not isinstance(
            raw_integrity_timeline.get("final_comparison_attempted"), bool
        )
    ):
        raise LifecycleGuardError("raw integrity timeline is invalid")
    if [checkpoint.get("stage") for checkpoint in checkpoints] != list(
        RAW_INTEGRITY_STAGES[: len(checkpoints)]
    ):
        raise LifecycleGuardError("raw integrity timeline is invalid")
    mismatches: list[str] = []
    for index, checkpoint in enumerate(checkpoints):
        if (
            not isinstance(checkpoint, dict)
            or set(checkpoint) != {"stage", "baseline_equal", "previous_equal"}
            or checkpoint.get("stage") != RAW_INTEGRITY_STAGES[index]
            or not isinstance(checkpoint.get("baseline_equal"), bool)
            or not isinstance(checkpoint.get("previous_equal"), bool)
        ):
            raise LifecycleGuardError("raw integrity timeline is invalid")
        if checkpoint["baseline_equal"] is False:
            mismatches.append(checkpoint["stage"])
    expected_first_mismatch = mismatches[0] if mismatches else "NONE"
    if first_mismatch_stage != expected_first_mismatch:
        raise LifecycleGuardError("raw integrity timeline is invalid")
    if not mismatches and any(
        checkpoint["previous_equal"] is not True for checkpoint in checkpoints
    ):
        raise LifecycleGuardError("raw integrity timeline is invalid")
    if checkpoints and (
        checkpoints[0]["baseline_equal"] is not True
        or checkpoints[0]["previous_equal"] is not True
    ):
        raise LifecycleGuardError("raw integrity timeline is invalid")
    if mismatches:
        mismatch_index = RAW_INTEGRITY_STAGES.index(first_mismatch_stage)
        if checkpoints[mismatch_index]["previous_equal"] is not False or any(
            checkpoint["baseline_equal"] is not True
            or checkpoint["previous_equal"] is not True
            for checkpoint in checkpoints[:mismatch_index]
        ):
            raise LifecycleGuardError("raw integrity timeline is invalid")
    if raw_integrity_timeline["final_comparison_attempted"] is True and len(
        checkpoints
    ) != len(RAW_INTEGRITY_STAGES):
        raise LifecycleGuardError("raw integrity timeline is invalid")
    raw_transition = bool(mismatches)
    lifecycle_success = document["classification"] == "LOGIN_SUCCEEDED_LIFECYCLE_RESULT"
    transition_result = (
        document["classification"] == "LOGIN_SUCCEEDED_PAYLOAD_VERIFIED_RAW_TRANSITION"
    )
    if downstream["persistence_control_plane"] and (
        raw_integrity_timeline["final_comparison_attempted"] is not True
        or len(checkpoints) != len(RAW_INTEGRITY_STAGES)
    ):
        raise LifecycleGuardError("receipt lifecycle result is invalid")
    if downstream["target_persistence_restart"] and (
        not downstream["persistence_control_plane"] or raw_transition
    ):
        raise LifecycleGuardError("receipt lifecycle result is invalid")
    if (any(downstream.values()) or payload_verification["attempted"]) and login.get(
        "succeeded"
    ) is not True:
        raise LifecycleGuardError("receipt lifecycle result is invalid")
    if (
        downstream["idempotent_apply"]
        and not downstream["bounded_io"]
        or downstream["state_only_recovery"]
        and not downstream["idempotent_apply"]
        or downstream["persistence_control_plane"]
        and not all(
            downstream[key]
            for key in ("bounded_io", "idempotent_apply", "state_only_recovery")
        )
        or payload_verification["attempted"]
        and not downstream["persistence_control_plane"]
        or downstream["remove_absence"]
        and payload_verification["matched"] is not True
        or downstream["backing_retained"] is not downstream["remove_absence"]
    ):
        raise LifecycleGuardError("receipt lifecycle result is invalid")
    if lifecycle_success and (
        failure != {"code": "NONE", "status": 0, "line": 0}
        or raw_transition
        or raw_integrity_timeline["final_comparison_attempted"] is not True
        or any(value is not True for value in downstream.values())
        or payload_verification != {"attempted": True, "matched": True}
    ):
        raise LifecycleGuardError("receipt lifecycle result is invalid")
    if transition_result and (
        failure
        != {
            "code": "RAW_RESTART_TRANSITION_OBSERVED",
            "status": 44,
            "line": 0,
        }
        or not raw_transition
        or raw_integrity_timeline["final_comparison_attempted"] is not True
        or downstream
        != {
            "bounded_io": True,
            "idempotent_apply": True,
            "state_only_recovery": True,
            "target_persistence_restart": False,
            "persistence_control_plane": True,
            "remove_absence": True,
            "backing_retained": True,
        }
        or payload_verification != {"attempted": True, "matched": True}
        or cleanup.get("classification") != "cleanup_complete"
    ):
        raise LifecycleGuardError("receipt lifecycle result is invalid")
    if not transition_result and (
        failure.get("code") == "RAW_RESTART_TRANSITION_OBSERVED"
        or document["classification"]
        == "LOGIN_SUCCEEDED_PAYLOAD_VERIFIED_RAW_TRANSITION"
    ):
        raise LifecycleGuardError("receipt lifecycle result is invalid")
    if cleanup.get("classification") not in CLEANUP_CLASSIFICATIONS:
        raise LifecycleGuardError("cleanup classification is invalid")
    budget = cleanup.get("total_budget_seconds")
    if not isinstance(budget, int) or isinstance(budget, bool) or not 0 < budget < 300:
        raise LifecycleGuardError("cleanup budget is invalid")
    phases = cleanup.get("phases")
    if (
        not isinstance(phases, list)
        or [phase.get("name") for phase in phases] != CLEANUP_PHASES
    ):
        raise LifecycleGuardError("cleanup phase order is invalid")
    for order, phase in enumerate(phases, start=1):
        if (
            not isinstance(phase, dict)
            or phase.get("order") != order
            or phase.get("status") not in {"success", "failed", "timeout", "skipped"}
            or not isinstance(phase.get("attempted"), bool)
            or not isinstance(phase.get("exit_status"), int)
            or isinstance(phase.get("exit_status"), bool)
            or phase.get("timeout_seconds") != CLEANUP_TIMEOUTS[order - 1]
            or not isinstance(phase.get("postcondition"), bool)
        ):
            raise LifecycleGuardError("cleanup phase result is invalid")
        if (
            (phase["status"] == "success" and phase["exit_status"] != 0)
            or (
                phase["status"] == "skipped"
                and (phase["attempted"] or phase["exit_status"] != 0)
            )
            or (phase["status"] == "timeout" and phase["exit_status"] not in {124, 137})
            or (phase["status"] == "failed" and phase["exit_status"] in {0, 124, 137})
        ):
            raise LifecycleGuardError("cleanup phase status is inconsistent")
    if cleanup["classification"] == "cleanup_complete" and not all(
        phase["status"] in {"success", "skipped"} and phase["postcondition"]
        for phase in phases
    ):
        raise LifecycleGuardError("cleanup completeness is invalid")
    if cleanup["classification"] == "cleanup_incomplete_bounded" and all(
        phase["status"] in {"success", "skipped"} and phase["postcondition"]
        for phase in phases
    ):
        raise LifecycleGuardError("cleanup incompleteness is invalid")
    loop_release = cleanup.get("loop_release")
    if not isinstance(loop_release, list) or len(loop_release) != 6:
        raise LifecycleGuardError("loop release evidence is invalid")
    for index, evidence in enumerate(loop_release, start=1):
        phase = phases[6 + index]
        if (
            not isinstance(evidence, dict)
            or set(evidence)
            != {
                "index",
                "precheck",
                "holder_count",
                "holder_identity_sha256",
                "holder_probe_state",
                "detach_exit_status",
                "detach_timed_out",
                "stderr_classification",
                "stderr_size_bytes",
                "stderr_sha256",
                "post_detach_state",
                "owned_image_released",
                "release_probe_state",
            }
            or evidence.get("index") != index
            or evidence.get("precheck") not in LOOP_RELEASE_PRECHECKS
            or not isinstance(evidence.get("holder_count"), int)
            or isinstance(evidence.get("holder_count"), bool)
            or not 0 <= evidence["holder_count"] <= LOOP_HOLDER_LIMIT
            or not isinstance(evidence.get("holder_identity_sha256"), list)
            or len(evidence["holder_identity_sha256"]) != evidence["holder_count"]
            or any(
                re.fullmatch(r"[0-9a-f]{64}", str(holder)) is None
                for holder in evidence["holder_identity_sha256"]
            )
            or evidence.get("holder_probe_state") not in LOOP_HOLDER_PROBE_STATES
            or not isinstance(evidence.get("detach_exit_status"), int)
            or isinstance(evidence.get("detach_exit_status"), bool)
            or evidence["detach_exit_status"] != phase["exit_status"]
            or not isinstance(evidence.get("detach_timed_out"), bool)
            or evidence["detach_timed_out"]
            is not (evidence["detach_exit_status"] in {124, 137})
            or evidence.get("stderr_classification") not in LOOP_RELEASE_STDERR_CLASSES
            or not isinstance(evidence.get("stderr_size_bytes"), int)
            or isinstance(evidence.get("stderr_size_bytes"), bool)
            or not 0 <= evidence["stderr_size_bytes"] <= DIAGNOSTIC_LIMIT
            or re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("stderr_sha256"))) is None
            or evidence.get("post_detach_state") not in LOOP_RELEASE_POST_STATES
            or not isinstance(evidence.get("owned_image_released"), bool)
            or evidence.get("release_probe_state") not in LOOP_RELEASE_PROBE_STATES
            or phase["attempted"] is not (evidence["precheck"] == "ORIGINAL_OWNED")
            or (
                evidence["holder_probe_state"] != "COMPLETE"
                and (
                    evidence["holder_count"] != 0
                    or evidence["holder_identity_sha256"] != []
                )
            )
            or (
                evidence["precheck"] in {"ORIGINAL_OWNED", "IDENTITY_CHANGED"}
                and evidence["holder_probe_state"] != "COMPLETE"
            )
            or (
                evidence["precheck"] in {"ABSENT", "DIFFERENT_BACKING"}
                and evidence["holder_probe_state"] != "NOT_APPLICABLE"
            )
            or (
                evidence["holder_probe_state"]
                in {"OVER_LIMIT", "INVALID_NAME", "PROBE_ERROR"}
                and evidence["precheck"] != "UNSAFE"
            )
            or (
                evidence["holder_probe_state"] == "COMPLETE"
                and evidence["precheck"] not in {"ORIGINAL_OWNED", "IDENTITY_CHANGED"}
            )
            or (
                evidence["holder_probe_state"] == "NOT_APPLICABLE"
                and evidence["precheck"] == "IDENTITY_CHANGED"
            )
            or (
                evidence["stderr_classification"] == "EMPTY"
                and (
                    evidence["stderr_size_bytes"] != 0
                    or evidence["stderr_sha256"] != EMPTY_SHA256
                )
            )
            or (
                evidence["stderr_classification"] != "EMPTY"
                and evidence["stderr_size_bytes"] == 0
            )
            or (
                evidence["release_probe_state"] == "RELEASED"
                and evidence["owned_image_released"] is not True
            )
            or (
                evidence["release_probe_state"] != "RELEASED"
                and evidence["owned_image_released"] is not False
            )
            or (
                evidence["post_detach_state"] == "ORIGINAL_OWNED"
                and (
                    evidence["release_probe_state"] == "RELEASED"
                    or evidence["owned_image_released"] is True
                )
            )
            or phase["postcondition"] is not loop_release_postcondition(evidence)
            or (
                phase["status"] == "timeout"
                and evidence["detach_timed_out"] is not True
            )
            or (
                phase["attempted"] is False
                and evidence["stderr_classification"] != "EMPTY"
            )
        ):
            raise LifecycleGuardError("loop release evidence is invalid")
    prohibited = document.get("prohibited_actions")
    if (
        not isinstance(prohibited, dict)
        or not prohibited
        or any(value != 0 for value in prohibited.values())
    ):
        raise LifecycleGuardError("prohibited action counters are invalid")
    return document


def atomic_write_receipt(document: object, output: Path) -> None:
    validated = validate_receipt(document)
    output.parent.mkdir(parents=True, exist_ok=True)
    parent = output.parent.lstat()
    expected_uid = getattr(os, "geteuid", lambda: parent.st_uid)()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != expected_uid
        or stat.S_IMODE(parent.st_mode) & 0o022
        or output.parent.resolve(strict=True) != output.parent.absolute()
    ):
        raise LifecycleGuardError("receipt output directory is unsafe")
    try:
        output.lstat()
    except FileNotFoundError:
        pass
    else:
        raise LifecycleGuardError("receipt output already exists")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=output.parent
    )
    placeholder = -1
    placeholder_identity: tuple[int, int] | None = None
    receipt_identity: tuple[int, int] | None = None
    try:
        os.fchmod(descriptor, 0o600)
        temporary_facts = os.fstat(descriptor)
        if temporary_facts.st_uid != expected_uid:
            raise LifecycleGuardError("receipt temporary owner is unsafe")
        receipt_identity = (temporary_facts.st_dev, temporary_facts.st_ino)
        placeholder = os.open(
            output,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchmod(placeholder, 0o600)
        placeholder_facts = os.fstat(placeholder)
        placeholder_identity = (placeholder_facts.st_dev, placeholder_facts.st_ino)
        os.close(placeholder)
        placeholder = -1
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as stream:
            json.dump(validated, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = -1
        named_placeholder = output.lstat()
        if (
            (named_placeholder.st_dev, named_placeholder.st_ino) != placeholder_identity
            or not stat.S_ISREG(named_placeholder.st_mode)
            or stat.S_ISLNK(named_placeholder.st_mode)
            or named_placeholder.st_uid != expected_uid
            or named_placeholder.st_nlink != 1
            or stat.S_IMODE(named_placeholder.st_mode) != 0o600
        ):
            raise LifecycleGuardError("receipt output placeholder is unsafe")
        os.replace(temporary, output)
        temporary = ""
        final_descriptor = -1
        try:
            final_descriptor = os.open(
                output,
                os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            final_facts = os.fstat(final_descriptor)
            if (
                (final_facts.st_dev, final_facts.st_ino) != receipt_identity
                or not stat.S_ISREG(final_facts.st_mode)
                or final_facts.st_uid != expected_uid
                or final_facts.st_nlink != 1
            ):
                raise LifecycleGuardError("final receipt identity is unsafe")
            os.fchmod(final_descriptor, 0o644)
            published_facts = os.fstat(final_descriptor)
            if (
                (published_facts.st_dev, published_facts.st_ino) != receipt_identity
                or not stat.S_ISREG(published_facts.st_mode)
                or published_facts.st_uid != expected_uid
                or published_facts.st_nlink != 1
                or (
                    os.name == "posix"
                    and stat.S_IMODE(published_facts.st_mode) != 0o644
                )
            ):
                raise LifecycleGuardError("final receipt mode is unsafe")
            os.fsync(final_descriptor)
        finally:
            if final_descriptor >= 0:
                os.close(final_descriptor)
    except Exception:
        for open_descriptor in (descriptor, placeholder):
            if open_descriptor >= 0:
                try:
                    os.close(open_descriptor)
                except OSError:
                    pass
        if temporary:
            Path(temporary).unlink(missing_ok=True)
        try:
            named = output.lstat()
        except FileNotFoundError:
            pass
        else:
            if (named.st_dev, named.st_ino) in {
                placeholder_identity,
                receipt_identity,
            }:
                output.unlink()
        raise


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _binding(args: argparse.Namespace) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "storage_volume_id": args.volume_id,
        "stable_identity": f"zfs:zvol:{args.pool}/{args.zvol}",
        "provider": "zfs",
        "resource_type": "zvol",
        "provider_resource_id": f"{args.pool}/{args.zvol}",
        "device_path": f"/dev/zvol/{args.pool}/{args.zvol}",
        "size_bytes": args.size_bytes,
    }
    return {"kind": "managed_zvol", **fields, "binding_sha256": config_hash(fields)}


def _config(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "protocol": "iscsi",
        "name": "managed-zvol-a4",
        "managed_zvol_binding": _binding(args),
        "target_iqn": args.target_iqn,
        "portal_ips": [args.portal],
        "initiator_iqns": [args.initiator_iqn],
        "chap_username": args.chap_user,
        "chap_enabled": True,
    }


def _sanitized_readback(
    evidence: dict[str, Any], args: argparse.Namespace
) -> dict[str, Any]:
    return {
        "schema_version": evidence.get("schema_version"),
        "state": evidence.get("state"),
        "evidence_sha256": evidence.get("evidence_sha256"),
        "block_plugin": evidence.get("backstore_plugin") == "block",
        "lun_zero": evidence.get("lun_index") == 0,
        "portal_exact": evidence.get("portals")
        == [{"ip_address": args.portal, "port": 3260}],
        "acl_exact": evidence.get("initiator_iqns") == [args.initiator_iqn],
        "chap_configured": evidence.get("chap_configured") is True,
        "chap_user_matches": evidence.get("chap_user_matches") is True,
        "chap_secret_matches": evidence.get("chap_secret_matches") is True,
        "device_matches_binding": evidence.get("device_matches_binding") is True,
    }


def _run_product_action(args: argparse.Namespace) -> dict[str, Any]:
    config = _config(args)
    executor.STATE_FILE = Path(args.state_file)
    counters = {"targetcli": 0, "state_reads": 0, "state_writes": 0, "readbacks": 0}
    real_targetcli = executor._targetcli
    real_load_state = executor._load_state
    real_save_state = executor._save_state
    real_readback = executor._read_lio_saveconfig

    def counted_targetcli(commands: list[str]) -> None:
        counters["targetcli"] += 1
        real_targetcli(commands)

    def counted_load_state() -> dict[str, dict[str, Any]]:
        counters["state_reads"] += 1
        return real_load_state()

    def counted_save_state(services: dict[str, dict[str, Any]]) -> None:
        counters["state_writes"] += 1
        real_save_state(services)

    def counted_readback() -> dict[str, Any]:
        counters["readbacks"] += 1
        return real_readback()

    executor._targetcli = counted_targetcli
    executor._load_state = counted_load_state
    executor._save_state = counted_save_state
    executor._read_lio_saveconfig = counted_readback
    common = {
        "schema_version": 1,
        "action": args.action,
        "service_identity_sha256": _digest(args.service_id),
        "target_identity_sha256": _digest(args.target_iqn),
        "counters": counters,
    }
    if args.action == "apply":
        result = executor.apply(
            args.service_id, config_hash(config), config, args.chap_value
        )
        return {
            **common,
            "state": result.get("state"),
            "already_active": result.get("already_active") is True,
            "reconciled_existing": result.get("reconciled_existing") is True,
            "readback": _sanitized_readback(result["readback"], args),
        }
    if args.action == "readback":
        document = lio_readback.read_saveconfig(lio_readback.RTSLIB_SAVECONFIG_PATH)
        counters["readbacks"] = 1
        evidence = lio_readback.verify_managed_apply(
            document,
            service_id=args.service_id,
            config=config,
            secret=args.chap_value,
        )
        return {
            **common,
            "state": "active",
            "readback": _sanitized_readback(evidence, args),
        }
    if args.action == "remove":
        result = executor.remove(args.service_id, config_hash(config), config, False)
        return {
            **common,
            "state": result.get("state"),
            "backing_data_deleted": result.get("backing_data_deleted") is True,
            "readback": {
                "schema_version": result["readback"].get("schema_version"),
                "state": result["readback"].get("state"),
                "evidence_sha256": result["readback"].get("evidence_sha256"),
                "target_absent": result["readback"].get("target_absent") is True,
                "backstore_absent": result["readback"].get("backstore_absent") is True,
            },
        }
    try:
        executor.remove(args.service_id, config_hash(config), config, True)
    except executor.ExecutorFailure as exc:
        if exc.code != "connectivity_managed_zvol_delete_forbidden":
            raise
        return {**common, "rejected_before_mutation": True, "failure_code": exc.code}
    raise RuntimeError("destructive managed removal was not rejected")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    guard = subparsers.add_parser("guard")
    guard.add_argument("--effective-uid", type=int, required=True)
    guard.add_argument("--github-actions", required=True)
    guard.add_argument("--marker-exists", choices=("true", "false"), required=True)
    guard.add_argument("--work-root", required=True)
    guard.add_argument("--loop-pair", action="append", default=[])
    lifecycle = subparsers.add_parser("lifecycle")
    lifecycle.add_argument(
        "--action",
        choices=("apply", "readback", "remove", "reject-delete"),
        required=True,
    )
    lifecycle.add_argument("--state-file", required=True)
    lifecycle.add_argument("--service-id", required=True)
    lifecycle.add_argument("--volume-id", required=True)
    lifecycle.add_argument("--pool", required=True)
    lifecycle.add_argument("--zvol", required=True)
    lifecycle.add_argument("--size-bytes", type=int, required=True)
    lifecycle.add_argument("--target-iqn", required=True)
    lifecycle.add_argument("--portal", required=True)
    lifecycle.add_argument("--initiator-iqn", required=True)
    lifecycle.add_argument("--chap-user", required=True)
    parity = subparsers.add_parser("parity")
    parity.add_argument("--node-root", type=Path, required=True)
    parity.add_argument("--target-iqn", required=True)
    parity.add_argument("--portal", required=True)
    parity.add_argument("--initiator-iqn", required=True)
    parity.add_argument("--chap-user", required=True)
    diagnostic = subparsers.add_parser("diagnostic")
    diagnostic.add_argument("--stdout", type=Path, required=True)
    diagnostic.add_argument("--stderr", type=Path, required=True)
    diagnostic.add_argument("--journal", type=Path, required=True)
    diagnostic.add_argument("--kernel", type=Path, required=True)
    diagnostic.add_argument("--status", type=int, required=True)
    tpg_authentication = subparsers.add_parser("tpg-authentication")
    tpg_authentication.add_argument("--target-iqn", required=True)
    tpg_authentication.add_argument(
        "--saveconfig", type=Path, default=lio_readback.RTSLIB_SAVECONFIG_PATH
    )
    receipt = subparsers.add_parser("receipt")
    receipt.add_argument("--draft", type=Path, required=True)
    receipt.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "guard":
        pairs: list[tuple[str, str]] = []
        for pair in args.loop_pair:
            device, separator, backing = pair.partition("=")
            if not separator:
                raise LifecycleGuardError("loop ownership could not be proven")
            pairs.append((device, backing))
        validate_guard(
            effective_uid=args.effective_uid,
            github_actions=args.github_actions,
            marker_exists=args.marker_exists == "true",
            work_root=args.work_root,
            loop_pairs=pairs,
        )
        print(json.dumps({"safe": True, "loop_count": len(pairs)}, sort_keys=True))
        return
    if args.command == "receipt":
        atomic_write_receipt(
            json.loads(args.draft.read_text(encoding="utf-8")), args.output
        )
        return
    if args.command == "tpg-authentication":
        print(
            json.dumps(
                read_effective_tpg_authentication(
                    args.saveconfig, target_iqn=args.target_iqn
                ),
                sort_keys=True,
            )
        )
        return
    args.chap_value = os.environ.get("HOARDARR_A4_CHAP_FIXTURE")
    if (
        not isinstance(args.chap_value, str)
        or re.fullmatch(r"[A-Za-z0-9._~-]{12,255}", args.chap_value) is None
    ):
        raise LifecycleGuardError("the test-only CHAP fixture is unavailable")
    if args.command == "lifecycle":
        print(json.dumps(_run_product_action(args), sort_keys=True))
        return
    if args.command == "parity":
        try:
            result = inspect_node_parity(
                node_root=args.node_root,
                target_iqn=args.target_iqn,
                portal=args.portal,
                initiator_iqn=args.initiator_iqn,
                chap_user=args.chap_user,
                chap_value=args.chap_value,
            )
        except NodeParityError as exc:
            result = parity_failure(
                exc,
                target_iqn=args.target_iqn,
                initiator_iqn=args.initiator_iqn,
            )
        print(json.dumps(result, sort_keys=True))
        return
    streams = []
    stderr_raw: bytes | None = None
    for label, path in (
        ("stdout", args.stdout),
        ("stderr", args.stderr),
        ("iscsid_target", args.journal),
        ("kernel_target", args.kernel),
    ):
        raw = _read_safe_diagnostic(path)
        if label == "stderr":
            stderr_raw = raw
        streams.append(
            sanitize_diagnostic_bytes(raw, secret=args.chap_value, label=label)
        )
    if stderr_raw is None:
        raise DiagnosticError("PROTOCOL_STATUS_MALFORMED")
    protocol_status = protocol_status_from_stderr(stderr_raw, final_status=args.status)
    ordered = []
    for stream in streams:
        for classification in stream["classifications"]:
            if classification not in ordered:
                ordered.append(classification)
    diagnosed = protocol_diagnosed_class(protocol_status)
    print(
        json.dumps(
            {
                "schema_version": 3,
                "status": args.status,
                "streams": streams,
                "ordered_classifications": ordered,
                "diagnosed_class": diagnosed,
                "protocol_status": protocol_status,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
