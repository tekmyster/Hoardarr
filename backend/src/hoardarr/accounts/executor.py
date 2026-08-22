from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import signal
import socket
import stat
import struct
import subprocess
from pathlib import Path
from typing import Any

USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
MAXIMUM_REQUEST_BYTES = 16 * 1024
MANAGED_ACCOUNTS = Path("/var/lib/hoardarr/media-accounts")
MANAGED_GROUP = "hoardarr-media"
MANAGED_COMMENT = "Hoardarr media account"
NOLOGIN_SHELLS = frozenset({"/usr/sbin/nologin", "/sbin/nologin", "/bin/false"})


class ExecutorFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _command(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ExecutorFailure(
            "account_tool_missing", f"The required local account tool is unavailable: {name}."
        )
    return path


def _run(command: list[str], *, input_text: str | None = None) -> None:
    try:
        subprocess.run(
            command,
            check=True,
            input=input_text,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
        )
    except subprocess.TimeoutExpired as exc:
        raise ExecutorFailure(
            "account_tool_timeout", "A local account tool did not finish in time."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise ExecutorFailure(
            "account_tool_failed", "A local account tool could not complete the request."
        ) from exc


def _account_marker(username: str) -> Path:
    return MANAGED_ACCOUNTS / f"{username}.json"


def _ensure_group() -> int:
    import grp

    try:
        group_id = grp.getgrnam(MANAGED_GROUP).gr_gid
    except KeyError:
        _run([_command("groupadd"), "--system", MANAGED_GROUP])
        group_id = grp.getgrnam(MANAGED_GROUP).gr_gid
    if group_id == 0:
        raise ExecutorFailure(
            "managed_group_invalid", "The Hoardarr media group cannot use the root group."
        )
    return group_id


def _validate_existing_account(username: str, expected_gid: int, marker: Path) -> None:
    import pwd

    try:
        record = pwd.getpwnam(username)
    except KeyError:
        return
    if not marker.is_file():
        raise ExecutorFailure(
            "account_name_in_use",
            "That username already belongs to an account not managed by Hoardarr.",
        )
    if record.pw_uid == 0 or record.pw_gid != expected_gid or record.pw_shell not in NOLOGIN_SHELLS:
        raise ExecutorFailure(
            "managed_account_changed",
            "The existing media account no longer matches Hoardarr's safety policy.",
        )


def _write_marker(marker: Path, username: str) -> None:
    MANAGED_ACCOUNTS.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(MANAGED_ACCOUNTS, 0, 0)
    os.chmod(MANAGED_ACCOUNTS, 0o700)
    temporary = marker.with_suffix(f".tmp-{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"schema": 1, "username": username, "kind": "media"}, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, marker)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def provision_media_account(username: str, password: str) -> dict[str, Any]:
    if not USERNAME_RE.fullmatch(username):
        raise ExecutorFailure(
            "account_username_invalid",
            "Use a lower-case username beginning with a letter or underscore.",
        )
    if not password or len(password) > 4096 or any(character in password for character in "\r\n\0"):
        raise ExecutorFailure(
            "account_password_invalid",
            "Enter a non-empty password without line breaks.",
        )
    import pwd

    marker = _account_marker(username)
    group_id = _ensure_group()
    _validate_existing_account(username, group_id, marker)
    try:
        pwd.getpwnam(username)
        created = False
    except KeyError:
        _run(
            [
                _command("useradd"),
                "--system",
                "--gid",
                MANAGED_GROUP,
                "--home-dir",
                "/nonexistent",
                "--no-create-home",
                "--shell",
                "/usr/sbin/nologin",
                "--comment",
                MANAGED_COMMENT,
                username,
            ]
        )
        created = True
        _write_marker(marker, username)
    _run([_command("smbpasswd"), "-a", "-s", username], input_text=f"{password}\n{password}\n")
    _run([_command("smbpasswd"), "-e", username])
    _run([_command("pdbedit"), "-L", "-u", username])
    return {
        "username": username,
        "created": created,
        "password_updated": True,
        "smb_enabled": True,
        "shell_login": False,
    }


def _read_request(connection: socket.socket) -> dict[str, Any]:
    payload = bytearray()
    while True:
        chunk = connection.recv(min(4096, MAXIMUM_REQUEST_BYTES + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > MAXIMUM_REQUEST_BYTES:
            raise ExecutorFailure("request_too_large", "The account request is too large.")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExecutorFailure("request_invalid", "The account request is invalid.") from exc
    if not isinstance(document, dict) or set(document) != {"operation", "username", "password"}:
        raise ExecutorFailure("request_invalid", "The account request is invalid.")
    return document


def _peer_is_allowed(connection: socket.socket) -> bool:
    import pwd

    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    _pid, uid, _gid = struct.unpack("3i", credentials)
    return uid in {0, pwd.getpwnam("hoardarr").pw_uid}


def _handle(connection: socket.socket) -> None:
    try:
        if not _peer_is_allowed(connection):
            raise ExecutorFailure("peer_forbidden", "The account request was not authorized.")
        request = _read_request(connection)
        if request["operation"] != "provision_media_account":
            raise ExecutorFailure("operation_unknown", "The account operation is not supported.")
        username = request["username"]
        password = request["password"]
        if not isinstance(username, str) or not isinstance(password, str):
            raise ExecutorFailure("request_invalid", "The account request is invalid.")
        response = {"ok": True, "result": provision_media_account(username, password)}
    except ExecutorFailure as exc:
        response = {"ok": False, "code": exc.code, "message": str(exc)}
    except Exception:
        response = {
            "ok": False,
            "code": "account_executor_failed",
            "message": "The local account service could not complete the request.",
        }
    with contextlib.suppress(OSError):
        connection.sendall(json.dumps(response, separators=(",", ":")).encode("utf-8"))


def serve(socket_path: Path) -> None:
    import grp

    if os.geteuid() != 0:
        raise SystemExit("hoardarr-account-executor must run as root")
    if socket_path.exists() or socket_path.is_symlink():
        details = socket_path.lstat()
        if not stat.S_ISSOCK(details.st_mode) or details.st_uid != 0:
            raise SystemExit(f"refusing to replace unsafe socket path: {socket_path}")
        socket_path.unlink()
    socket_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    os.chown(socket_path, 0, grp.getgrnam("hoardarr").gr_gid)
    os.chmod(socket_path, 0o660)
    server.listen(16)
    server.settimeout(1.0)
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            try:
                connection, _address = server.accept()
            except TimeoutError:
                continue
            with connection:
                connection.settimeout(35)
                _handle(connection)
    finally:
        server.close()
        with contextlib.suppress(FileNotFoundError):
            socket_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Hoardarr privileged media-account executor")
    parser.add_argument("--socket", type=Path, default=Path("/run/hoardarr/account-executor.sock"))
    args = parser.parse_args()
    serve(args.socket)


if __name__ == "__main__":
    main()
