from __future__ import annotations

import sys
from collections.abc import Callable


def _commands() -> dict[str, Callable[[], None]]:
    # Imports stay lazy so a malformed command cannot initialize a privileged
    # executor or the API before the closed command name has been validated.
    from hoardarr.accounts.executor import main as account_executor_main
    from hoardarr.cli import api_main, migrate_main, worker_main
    from hoardarr.cli import main as cli_main
    from hoardarr.storage.executor import main as storage_executor_main
    from hoardarr.storage.quarantine import main as storage_quarantine_main
    from hoardarr.storage.zfs import main as zfs_snapshot_main

    return {
        "account-executor": account_executor_main,
        "api": api_main,
        "cli": cli_main,
        "migrate": migrate_main,
        "storage-executor": storage_executor_main,
        "storage-quarantine": storage_quarantine_main,
        "worker": worker_main,
        "zfs-snapshot": zfs_snapshot_main,
    }


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m hoardarr.runtime COMMAND [ARG ...]")
    command = sys.argv.pop(1)
    target = _commands().get(command)
    if target is None:
        raise SystemExit(f"unknown Hoardarr runtime command: {command}")
    target()


if __name__ == "__main__":
    main()
