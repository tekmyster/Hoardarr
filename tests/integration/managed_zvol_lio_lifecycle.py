from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from hoardarr.connectivity import executor, lio_readback
from hoardarr.connectivity.service import config_hash


class LifecycleGuardError(ValueError):
    pass


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
    args.chap_value = os.environ.get("HOARDARR_A4_CHAP_FIXTURE")
    if (
        not isinstance(args.chap_value, str)
        or re.fullmatch(r"[A-Za-z0-9._~-]{12,255}", args.chap_value) is None
    ):
        raise LifecycleGuardError("the test-only CHAP fixture is unavailable")
    print(json.dumps(_run_product_action(args), sort_keys=True))


if __name__ == "__main__":
    main()
