from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any


class StorageExecutorError(RuntimeError):
    """A stable, safe failure returned by the privileged storage service."""

    def __init__(self, code: str, message: str, *, needs_attention: bool = False) -> None:
        self.code = code
        self.needs_attention = needs_attention
        super().__init__(message)


def _receive(connection: socket.socket, maximum: int = 1024 * 1024) -> dict[str, Any]:
    payload = bytearray()
    while True:
        chunk = connection.recv(min(64 * 1024, maximum + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > maximum:
            raise StorageExecutorError(
                "executor_response_too_large", "The storage service returned an invalid response."
            )
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageExecutorError(
            "executor_response_invalid", "The storage service returned an invalid response."
        ) from exc
    if not isinstance(document, dict):
        raise StorageExecutorError(
            "executor_response_invalid", "The storage service returned an invalid response."
        )
    return document


def _request_executor(
    socket_path: Path, request: dict[str, Any], *, timeout_seconds: float
) -> dict[str, Any]:
    encoded = json.dumps(request, separators=(",", ":")).encode("utf-8")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout_seconds)
            connection.connect(str(socket_path))
            connection.sendall(encoded)
            connection.shutdown(socket.SHUT_WR)
            response = _receive(connection)
    except (FileNotFoundError, ConnectionRefusedError, TimeoutError) as exc:
        raise StorageExecutorError(
            "storage_service_unavailable",
            "The privileged storage service is unavailable. No storage action was started.",
        ) from exc
    except OSError as exc:
        raise StorageExecutorError(
            "storage_service_unavailable", "The privileged storage service could not be reached."
        ) from exc
    if response.get("ok") is not True:
        raise StorageExecutorError(
            response.get("code")
            if isinstance(response.get("code"), str)
            else "storage_apply_failed",
            response.get("message")
            if isinstance(response.get("message"), str)
            else "The storage request could not be completed.",
            needs_attention=response.get("needs_attention") is True,
        )
    result = response.get("result")
    if not isinstance(result, dict):
        raise StorageExecutorError(
            "executor_response_invalid", "The storage service returned an invalid response."
        )
    return result


def apply_storage_plan(
    socket_path: Path,
    *,
    operation_id: str,
    plan_sha256: str,
    document: dict[str, Any],
    approval: dict[str, Any] | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    result = _request_executor(
        socket_path,
        {
            "operation": "apply_storage_plan",
            "operation_id": operation_id,
            "plan_sha256": plan_sha256,
            "document": document,
            "approval": approval,
        },
        timeout_seconds=timeout_seconds,
    )
    if result.get("operation_id") != operation_id:
        raise StorageExecutorError(
            "executor_response_invalid", "The storage service returned an invalid response."
        )
    return result


def apply_device_maintenance(
    socket_path: Path,
    *,
    operation_id: str,
    plan_sha256: str,
    plan: dict[str, Any],
    confirmation_sha256: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    result = _request_executor(
        socket_path,
        {
            "operation": "apply_device_maintenance",
            "operation_id": operation_id,
            "plan_sha256": plan_sha256,
            "plan": plan,
            "confirmation_sha256": confirmation_sha256,
        },
        timeout_seconds=timeout_seconds,
    )
    if result.get("operation_id") != operation_id:
        raise StorageExecutorError(
            "executor_response_invalid", "The storage service returned an invalid response."
        )
    return result


def apply_foreign_inspection(
    socket_path: Path,
    *,
    operation_id: str,
    plan_sha256: str,
    plan: dict[str, Any],
    confirmation_sha256: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    result = _request_executor(
        socket_path,
        {
            "operation": "apply_foreign_inspection",
            "operation_id": operation_id,
            "plan_sha256": plan_sha256,
            "plan": plan,
            "confirmation_sha256": confirmation_sha256,
        },
        timeout_seconds=timeout_seconds,
    )
    if result.get("operation_id") != operation_id:
        raise StorageExecutorError(
            "executor_response_invalid", "The storage service returned an invalid response."
        )
    return result


def apply_snapraid_replacement(
    socket_path: Path,
    *,
    operation_id: str,
    plan_sha256: str,
    plan: dict[str, Any],
    confirmation_sha256: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    result = _request_executor(
        socket_path,
        {
            "operation": "apply_snapraid_replacement",
            "operation_id": operation_id,
            "plan_sha256": plan_sha256,
            "plan": plan,
            "confirmation_sha256": confirmation_sha256,
        },
        timeout_seconds=timeout_seconds,
    )
    if result.get("operation_id") != operation_id:
        raise StorageExecutorError(
            "executor_response_invalid", "The storage service returned an invalid response."
        )
    return result


def apply_array_replacement(
    socket_path: Path,
    *,
    operation_id: str,
    plan_sha256: str,
    plan: dict[str, Any],
    confirmation_sha256: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    result = _request_executor(
        socket_path,
        {
            "operation": "apply_array_replacement",
            "operation_id": operation_id,
            "plan_sha256": plan_sha256,
            "plan": plan,
            "confirmation_sha256": confirmation_sha256,
        },
        timeout_seconds=timeout_seconds,
    )
    if result.get("operation_id") != operation_id:
        raise StorageExecutorError(
            "executor_response_invalid", "The storage service returned an invalid response."
        )
    return result


def apply_storage_redundancy(
    socket_path: Path,
    *,
    operation_id: str,
    plan_sha256: str,
    plan: dict[str, Any],
    confirmation_sha256: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    result = _request_executor(
        socket_path,
        {
            "operation": "apply_storage_redundancy",
            "operation_id": operation_id,
            "plan_sha256": plan_sha256,
            "plan": plan,
            "confirmation_sha256": confirmation_sha256,
        },
        timeout_seconds=timeout_seconds,
    )
    if result.get("operation_id") != operation_id:
        raise StorageExecutorError(
            "executor_response_invalid", "The storage service returned an invalid response."
        )
    return result


def storage_operation_status(
    socket_path: Path, *, operation_id: str, timeout_seconds: float = 5.0
) -> dict[str, Any]:
    result = _request_executor(
        socket_path,
        {"operation": "storage_operation_status", "operation_id": operation_id},
        timeout_seconds=timeout_seconds,
    )
    if result.get("operation_id") != operation_id or not isinstance(result.get("state"), str):
        raise StorageExecutorError(
            "executor_response_invalid", "The storage service returned an invalid response."
        )
    return result
