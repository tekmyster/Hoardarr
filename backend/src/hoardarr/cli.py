from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

import uvicorn

from hoardarr.api.app import create_app
from hoardarr.auth.service import (
    AuthenticationError,
    SetupUnavailableError,
    create_initial_owner,
    issue_setup_token,
)
from hoardarr.backups.service import (
    BackupError,
    apply_fresh_control_plane_restore,
    build_control_plane_artifact,
)
from hoardarr.core.config import Settings
from hoardarr.core.secrets import SecretBox
from hoardarr.db.engine import (
    create_database_engine,
    create_session_factory,
    sqlite_database_has_schema,
)
from hoardarr.db.migrate import database_is_current, upgrade_database
from hoardarr.operations.worker import run_forever, run_once


def _migrate(settings: Settings) -> None:
    database_initialized = sqlite_database_has_schema(settings.database_url)
    key_existed = settings.secret_key_file.exists()
    if database_initialized and not key_existed:
        raise SystemExit(
            "refusing to create a replacement encryption key for an existing database; "
            "restore the original secret key or explicitly recover the installation"
        )
    # This explicit administrative command is the only production path allowed
    # to create the encryption key. Create and fsync it before initializing a
    # new schema so a crash cannot strand an initialized database without its
    # encryption key. The API and worker only open an existing key.
    SecretBox.from_file(settings.secret_key_file, create=not key_existed)
    upgrade_database(settings.database_url)


def migrate_main() -> None:
    _migrate(Settings())


def _site_url(settings: Settings, supplied: str | None) -> str:
    if supplied:
        candidate = supplied.rstrip("/")
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AuthenticationError("site URL must be an absolute http or https URL")
        return candidate
    host = settings.bind_host
    if host in {"0.0.0.0", "127.0.0.1", "::", "::1"}:
        host = socket.getfqdn() or socket.gethostname()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{settings.bind_port}"


def _read_password(password_stdin: bool) -> str:
    if password_stdin:
        password = sys.stdin.readline().removesuffix("\n").removesuffix("\r")
        if not password:
            raise AuthenticationError("password cannot be empty")
        return password
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise AuthenticationError("passwords do not match")
    return password


def _is_root() -> bool:
    return not hasattr(os, "geteuid") or os.geteuid() == 0


def _setup_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    browser_setup = args.browser or not (args.console or args.username or args.password_stdin)
    if browser_setup and (args.username or args.password_stdin):
        parser.error("browser setup cannot be combined with --username or --password-stdin")
    settings = Settings()
    try:
        _migrate(settings)
        engine = create_database_engine(settings.database_url)
        factory = create_session_factory(engine)
        site_url = _site_url(settings, args.site_url)
        if browser_setup:
            with factory() as session, session.begin():
                token = issue_setup_token(session, ttl_seconds=args.ttl)
            print("Open this one-time setup link:")
            print(f"{site_url}/#pair={quote(token, safe='')}")
            print()
            print(f"Server code (manual recovery only): {token}")
            print("The link applies the server code without showing it in the Web UI.")
            print("This setup-only code expires, works once, and is not an API key.")
            return

        username = args.username or input("Username [admin]: ").strip() or "admin"
        password = _read_password(args.password_stdin)
        with factory() as session, session.begin():
            user = create_initial_owner(session, username=username, password=password)
    except (AuthenticationError, SetupUnavailableError) as exc:
        parser.error(str(exc))
    print(f"Hoardarr account '{user.username}' is ready.")
    print(f"Open {site_url} and sign in. No setup code is required.")


def _restore_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.yes:
        parser.error("fresh restore requires --yes")
    if not _is_root():
        parser.error("fresh restore must run as root")
    if os.name == "posix":
        active: list[str] = []
        for unit in (
            "hoardarr-api.service",
            "hoardarr-worker.service",
            "hoardarr-account-executor.service",
            "hoardarr-storage-executor.service",
            "hoardarr-storage-status.service",
        ):
            result = subprocess.run(
                ["systemctl", "is-active", "--quiet", unit],
                check=False,
                timeout=5,
            )
            if result.returncode == 0:
                active.append(unit)
        if active:
            parser.error(
                "stop Hoardarr services before restore; active: " + ", ".join(active)
            )
    try:
        passphrase = _read_secret_export_passphrase() if args.passphrase_stdin else None
        report = apply_fresh_control_plane_restore(
            Settings(),
            Path(args.archive),
            args.sha256,
            secret_export_passphrase=passphrase,
        )
    except BackupError as exc:
        parser.error(exc.safe_message)
    print(json.dumps(report, indent=2, sort_keys=True))


def _read_secret_export_passphrase() -> str:
    passphrase = sys.stdin.readline().removesuffix("\n").removesuffix("\r")
    if not passphrase:
        raise BackupError(
            "backup_secret_passphrase_required",
            "A secret-export passphrase is required on standard input.",
        )
    return passphrase


def _export_control_plane_command(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> None:
    if not _is_root():
        parser.error("control-plane export must run as root")
    output = Path(args.output).expanduser().resolve(strict=False)
    if output.exists():
        parser.error("refusing to overwrite an existing export")
    try:
        passphrase = _read_secret_export_passphrase() if args.encrypt_secrets else None
        artifact, report = build_control_plane_artifact(
            Settings(),
            f"console-export-{os.getpid()}",
            secret_export_passphrase=passphrase,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.parent.is_symlink():
            raise BackupError(
                "backup_export_path_unsafe",
                "The export destination directory cannot be a symbolic link.",
            )
        with artifact.open("rb") as source, output.open("xb") as destination:
            while chunk := source.read(1024 * 1024):
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(output, 0o600)
    except (BackupError, OSError) as exc:
        output.unlink(missing_ok=True)
        parser.error(exc.safe_message if isinstance(exc, BackupError) else "export failed")
    report = {**report, "artifact_path": str(output)}
    print(json.dumps(report, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(prog="hoardarr", description="Manage this Hoardarr server")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="set up the first administrator")
    setup.add_argument("--username", help="owner username; prompts when omitted")
    setup.add_argument(
        "--password-stdin",
        action="store_true",
        help="read one password line from standard input instead of prompting",
    )
    setup_mode = setup.add_mutually_exclusive_group()
    setup_mode.add_argument(
        "--browser",
        action="store_true",
        help=(
            "print a one-time browser pairing link (the default when no account options are given)"
        ),
    )
    setup_mode.add_argument(
        "--console",
        action="store_true",
        help="create the first administrator entirely at the console",
    )
    setup.add_argument("--ttl", type=int, default=900, help="browser link lifetime in seconds")
    setup.add_argument("--site-url", help="public Hoardarr URL shown after setup")
    restore = commands.add_parser(
        "restore-control-plane",
        help="apply a credential-redacted archive to a fresh offline appliance",
    )
    restore.add_argument("--archive", required=True, help="local control-plane tar.gz archive")
    restore.add_argument("--sha256", required=True, help="expected 64-character SHA-256 digest")
    restore.add_argument(
        "--passphrase-stdin",
        action="store_true",
        help="read the encrypted-secret export passphrase from standard input",
    )
    restore.add_argument(
        "--yes",
        action="store_true",
        help="confirm replacing the empty appliance database and restorable configuration",
    )
    export = commands.add_parser(
        "export-control-plane",
        help="write an offline control-plane archive to an explicit local path",
    )
    export.add_argument("--output", required=True, help="new local tar.gz archive path")
    export.add_argument(
        "--encrypt-secrets",
        action="store_true",
        help="include the installation key encrypted by a passphrase read from standard input",
    )
    args = parser.parse_args()
    if args.command == "setup":
        if not 60 <= args.ttl <= 3600:
            setup.error("--ttl must be between 60 and 3600 seconds")
        _setup_command(args, setup)
    elif args.command == "restore-control-plane":
        _restore_command(args, restore)
    elif args.command == "export-control-plane":
        _export_control_plane_command(args, export)


def setup_token_main() -> None:
    parser = argparse.ArgumentParser(description="Issue the one-time Hoardarr owner setup token")
    parser.add_argument("--ttl", type=int, default=900, help="token lifetime in seconds")
    args = parser.parse_args()
    if not 60 <= args.ttl <= 3600:
        parser.error("--ttl must be between 60 and 3600 seconds")
    settings = Settings()
    engine = create_database_engine(settings.database_url)
    if not database_is_current(engine, settings.database_url):
        parser.error("database migrations are not current; run hoardarr-migrate first")
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        token = issue_setup_token(session, ttl_seconds=args.ttl)
    # The secret is intentionally shown exactly once to the administrator.
    print(token)


def api_main() -> None:
    settings = Settings()
    logging.basicConfig(level=logging.INFO)
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.bind_host,
        port=settings.bind_port,
        proxy_headers=False,
        access_log=True,
    )


def worker_main() -> None:
    parser = argparse.ArgumentParser(description="Run the Hoardarr background worker")
    parser.add_argument("--once", action="store_true", help="process at most one queued operation")
    args = parser.parse_args()
    settings = Settings()
    logging.basicConfig(level=logging.INFO)
    engine = create_database_engine(settings.database_url)
    if not database_is_current(engine, settings.database_url):
        parser.error("database migrations are not current; run hoardarr-migrate first")
    session_factory = create_session_factory(engine)
    secret_box = SecretBox.from_file(settings.secret_key_file, create=False)
    if args.once:
        run_once(session_factory=session_factory, settings=settings, secret_box=secret_box)
    else:
        run_forever(session_factory=session_factory, settings=settings, secret_box=secret_box)
