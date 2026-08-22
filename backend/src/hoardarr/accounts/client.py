from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any


class AccountExecutorError(RuntimeError):
    """A safe, typed failure returned by the local privileged executor."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _receive_document(connection: socket.socket, *, maximum: int = 64 * 1024) -> dict[str, Any]:
    payload = bytearray()
    while True:
        chunk = connection.recv(min(4096, maximum + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > maximum:
            raise AccountExecutorError(
                "executor_response_too_large",
                "The account service returned an invalid response.",
            )
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AccountExecutorError(
            "executor_response_invalid", "The account service returned an invalid response."
        ) from exc
    if not isinstance(document, dict):
        raise AccountExecutorError(
            "executor_response_invalid", "The account service returned an invalid response."
        )
    return document


def provision_media_account(
    socket_path: Path,
    *,
    username: str,
    password: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    request = json.dumps(
        {"operation": "provision_media_account", "username": username, "password": password},
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout_seconds)
            connection.connect(str(socket_path))
            connection.sendall(request)
            connection.shutdown(socket.SHUT_WR)
            response = _receive_document(connection)
    except (FileNotFoundError, ConnectionRefusedError, TimeoutError) as exc:
        raise AccountExecutorError(
            "account_service_unavailable",
            "The local account service is unavailable. Check its service status and try again.",
        ) from exc
    except OSError as exc:
        raise AccountExecutorError(
            "account_service_unavailable",
            "The local account service could not be reached.",
        ) from exc

    if response.get("ok") is not True:
        code = response.get("code")
        message = response.get("message")
        raise AccountExecutorError(
            code if isinstance(code, str) else "account_provision_failed",
            message if isinstance(message, str) else "The media account could not be created.",
        )
    result = response.get("result")
    if not isinstance(result, dict) or result.get("username") != username:
        raise AccountExecutorError(
            "executor_response_invalid", "The account service returned an invalid response."
        )
    return result
