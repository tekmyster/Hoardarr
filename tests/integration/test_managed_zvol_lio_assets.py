from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import managed_zvol_lio_lifecycle as lifecycle
import pytest
from managed_zvol_lio_lifecycle import (
    CLEANUP_PHASES,
    CLEANUP_TIMEOUTS,
    DIAGNOSTIC_LIMIT,
    RAW_INTEGRITY_STAGES,
    DiagnosticError,
    LifecycleGuardError,
    NodeParityError,
    _read_safe_diagnostic,
    atomic_write_receipt,
    inspect_node_parity,
    loop_release_postcondition,
    protocol_status_from_stderr,
    read_effective_tpg_authentication,
    sanitize_diagnostic_bytes,
    tpg_authentication_from_saveconfig,
    validate_guard,
    validate_receipt,
)

SAFE_ROOT = "/tmp/hoardarr-managed-zvol.fixture"
SYNTHETIC_CHAP = "FixtureValueForA5"
SAFE_LOOPS = [
    (f"/dev/loop{number}", f"{SAFE_ROOT}/disk{number}.img") for number in range(1, 7)
]


@pytest.fixture(autouse=True)
def _posix_mode_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name == "nt":
        monkeypatch.setattr(
            lifecycle.stat,
            "S_IMODE",
            lambda mode: 0o700 if lifecycle.stat.S_ISDIR(mode) else 0o600,
        )


def _guard(**overrides: object) -> None:
    values: dict[str, object] = {
        "effective_uid": 0,
        "github_actions": "true",
        "marker_exists": True,
        "work_root": SAFE_ROOT,
        "loop_pairs": SAFE_LOOPS,
    }
    values.update(overrides)
    validate_guard(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"effective_uid": 1000},
        {"github_actions": "false"},
        {"marker_exists": False},
        {"work_root": "/tmp"},
        {"work_root": "/var/tmp/hoardarr-managed-zvol.fixture"},
        {"loop_pairs": SAFE_LOOPS[:5]},
        {"loop_pairs": [*SAFE_LOOPS[:5], ("/dev/sda", f"{SAFE_ROOT}/disk6.img")]},
        {
            "loop_pairs": [
                *SAFE_LOOPS[:5],
                ("/dev/loop6", "/tmp/foreign/disk6.img"),
            ]
        },
        {"loop_pairs": [*SAFE_LOOPS[:5], ("/dev/loop404", "")]},
    ],
)
def test_guard_fails_closed_before_mutation(overrides: dict[str, object]) -> None:
    with pytest.raises(LifecycleGuardError):
        _guard(**overrides)


def test_guard_accepts_exact_six_owned_loop_facts() -> None:
    _guard()


def test_workflow_and_script_retain_required_safety_order() -> None:
    repo = Path(__file__).resolve().parents[2]
    script = (repo / "tests/integration/run-managed-zvol-lio-lifecycle.sh").read_text()
    workflow = (repo / ".github/workflows/storage-integration.yml").read_text()
    job = workflow.split("  managed-zvol-lio-lifecycle:\n", 1)[1].split(
        "  storage-group-drain-lifecycle:\n", 1
    )[0]
    assert script.index('[[ "$(id -u)" -eq 0 ]]') < script.index("mktemp -d")
    assert script.index('[[ "${GITHUB_ACTIONS:-}" == "true" ]]') < script.index(
        "mktemp -d"
    )
    assert script.index("/.hoardarr-disposable-runner") < script.index("mktemp -d")
    assert "zpool create -f -o ashift=12" in script
    assert 'raidz2 "${loops[@]}"' in script
    assert "/backstores/fileio" not in script
    assert "workflow_dispatch" not in job


def _node_record(
    tmp_path: Path,
    *,
    method: str = "CHAP",
    username: str = "fixture_user",
    value: str = SYNTHETIC_CHAP,
    portal_suffix: str = "1",
) -> tuple[Path, str, str]:
    root = tmp_path / "nodes"
    target = "iqn.2026-08.local.hoardarr"
    record = root / target / f"127.0.0.5,3260,{portal_suffix}" / "default"
    record.parent.mkdir(parents=True)
    root.chmod(0o700)
    (root / target).chmod(0o700)
    record.parent.chmod(0o700)
    record.write_text(
        "\n".join(
            (
                f"node.session.auth.authmethod = {method}",
                f"node.session.auth.username = {username}",
                f"node.session.auth.password = {value}",
            )
        ),
        encoding="utf-8",
    )
    record.chmod(0o600)
    return root, target, record.as_posix()


def _parity(tmp_path: Path, **overrides: str) -> dict[str, object]:
    root, target, _record = _node_record(tmp_path, **overrides)
    return inspect_node_parity(
        node_root=root,
        target_iqn=target,
        portal="127.0.0.5",
        initiator_iqn="iqn.2026-08.local.hoardarr.initiator",
        chap_user="fixture_user",
        chap_value=SYNTHETIC_CHAP,
        expected_uid=os.stat(root).st_uid,
    )


def _receipt(
    *, classification: str = "LOGIN_SUCCEEDED_LIFECYCLE_RESULT"
) -> dict[str, object]:
    login_succeeded = classification in {
        "LOGIN_SUCCEEDED_LIFECYCLE_RESULT",
        "LOGIN_SUCCEEDED_PAYLOAD_VERIFIED_RAW_TRANSITION",
    }
    phases = [
        {
            "name": name,
            "order": order,
            "attempted": True,
            "status": "success",
            "exit_status": 0,
            "timeout_seconds": timeout,
            "postcondition": True,
        }
        for order, (name, timeout) in enumerate(
            zip(CLEANUP_PHASES, CLEANUP_TIMEOUTS), start=1
        )
    ]
    receipt: dict[str, object] = {
        "schema_version": 2,
        "classification": classification,
        "workflow": "storage-integration",
        "job": "managed-zvol-lio-lifecycle",
        "run_id": "synthetic-run",
        "failure": {"code": "NONE", "status": 0, "line": 0},
        "topology": {
            "loop_count": 6,
            "raidz2_vdev_count": 1,
            "raidz2_member_count": 6,
            "zvol_count": 1,
            "raw_paths_emitted": False,
        },
        "parity": {
            "schema_version": 1,
            "exact": True,
            "mismatch": "NONE",
            "record_count": 1,
            "auth_method_chap": True,
            "username_match": True,
            "password_match": True,
            "record_count_exact": True,
            "record_safe": True,
            "username_length": 12,
            "password_length": 17,
            "target_identity_sha256": "1" * 64,
            "initiator_identity_sha256": "2" * 64,
            "parity_sha256": "3" * 64,
        },
        "prelogin": {
            "production_apply_passed": True,
            "production_readback_passed": True,
            "tpg_authentication": {
                "schema_version": 1,
                "observed": True,
                "enabled": True,
            },
        },
        "login": {
            "attempt_count": 1,
            "status": 0 if login_succeeded else 19,
            "succeeded": login_succeeded,
            "diagnostic": {
                "schema_version": 3,
                "status": 0 if login_succeeded else 19,
                "streams": [
                    {
                        "label": label,
                        "size_bytes": 0,
                        "sha256": "0" * 64,
                        "classifications": [],
                    }
                    for label in ("stdout", "stderr", "iscsid_target", "kernel_target")
                ],
                "ordered_classifications": [],
                "diagnosed_class": None,
                "protocol_status": {
                    "observed": False,
                    "status_class": None,
                    "status_detail": None,
                    "meaning": "NONE",
                    "source_label": None,
                },
            },
        },
        "raw_integrity_timeline": {
            "schema_version": 1,
            "checkpoints": [
                {
                    "stage": stage,
                    "baseline_equal": True,
                    "previous_equal": True,
                }
                for stage in RAW_INTEGRITY_STAGES
            ],
            "first_mismatch_stage": "NONE",
            "final_comparison_attempted": True,
        },
        "downstream": {
            "bounded_io": True,
            "idempotent_apply": True,
            "state_only_recovery": True,
            "target_persistence_restart": True,
            "persistence_control_plane": True,
            "remove_absence": True,
            "backing_retained": True,
        },
        "payload_verification": {"attempted": True, "matched": True},
        "cleanup": {
            "classification": "cleanup_complete",
            "total_budget_seconds": 191,
            "phases": phases,
            "loop_release": [
                {
                    "index": number,
                    "precheck": "ORIGINAL_OWNED",
                    "holder_count": 0,
                    "holder_identity_sha256": [],
                    "holder_probe_state": "COMPLETE",
                    "detach_exit_status": 0,
                    "detach_timed_out": False,
                    "stderr_classification": "EMPTY",
                    "stderr_size_bytes": 0,
                    "stderr_sha256": hashlib.sha256(b"").hexdigest(),
                    "post_detach_state": "ABSENT",
                    "owned_image_released": True,
                    "release_probe_state": "RELEASED",
                }
                for number in range(1, 7)
            ],
        },
        "prohibited_actions": {"physical_media": 0, "login_retries": 0},
    }
    parity = receipt["parity"]
    login = receipt["login"]
    assert isinstance(parity, dict) and isinstance(login, dict)
    diagnostic = login["diagnostic"]
    assert isinstance(diagnostic, dict)
    if classification == "PARITY_MISMATCH_IDENTIFIED":
        parity.update(
            {
                "exact": False,
                "mismatch": "PASSWORD_MISMATCH",
                "password_match": False,
            }
        )
        login.update({"attempt_count": 0, "status": -1, "succeeded": False})
        diagnostic.update({"status": -1, "streams": []})
        receipt["failure"] = {"code": "PASSWORD_MISMATCH", "status": 41, "line": 0}
    elif classification == "LOGIN_FAILURE_DIAGNOSED":
        diagnostic.update(
            {
                "protocol_status": {
                    "observed": True,
                    "status_class": 2,
                    "status_detail": 1,
                    "meaning": "AUTHENTICATION_FAILURE",
                    "source_label": "stderr",
                },
                "diagnosed_class": "credential_rejection",
            }
        )
    elif classification == "HARNESS_ERROR":
        receipt["topology"] = {
            "loop_count": 0,
            "raidz2_vdev_count": 0,
            "raidz2_member_count": 0,
            "zvol_count": 0,
            "raw_paths_emitted": False,
        }
        parity.update(
            {
                "exact": False,
                "mismatch": "NOT_RUN",
                "record_count": 0,
                "auth_method_chap": False,
                "username_match": False,
                "password_match": False,
                "record_count_exact": False,
                "record_safe": False,
                "username_length": 0,
                "password_length": 0,
                "target_identity_sha256": "",
                "initiator_identity_sha256": "",
                "parity_sha256": "",
            }
        )
        login.update({"attempt_count": 0, "status": -1, "succeeded": False})
        diagnostic.update({"status": -1, "streams": []})
        prelogin = receipt["prelogin"]
        assert isinstance(prelogin, dict)
        prelogin.update(
            {
                "production_apply_passed": False,
                "production_readback_passed": False,
                "tpg_authentication": {
                    "schema_version": 1,
                    "observed": False,
                    "enabled": None,
                },
            }
        )
        receipt["failure"] = {
            "code": "UNCLASSIFIED_HARNESS_STOP",
            "status": 1,
            "line": 0,
        }
    if classification == "LOGIN_SUCCEEDED_PAYLOAD_VERIFIED_RAW_TRANSITION":
        timeline = receipt["raw_integrity_timeline"]
        assert isinstance(timeline, dict)
        checkpoints = timeline["checkpoints"]
        assert isinstance(checkpoints, list)
        for checkpoint in checkpoints[4:]:
            assert isinstance(checkpoint, dict)
            checkpoint.update({"baseline_equal": False, "previous_equal": False})
        checkpoints[5]["previous_equal"] = True
        checkpoints[6]["previous_equal"] = True
        timeline["first_mismatch_stage"] = "after_target_persistence_restart"
        downstream = receipt["downstream"]
        assert isinstance(downstream, dict)
        downstream["target_persistence_restart"] = False
        receipt["failure"] = {
            "code": "RAW_RESTART_TRANSITION_OBSERVED",
            "status": 44,
            "line": 0,
        }
    elif classification != "LOGIN_SUCCEEDED_LIFECYCLE_RESULT":
        receipt["downstream"] = {
            "bounded_io": False,
            "idempotent_apply": False,
            "state_only_recovery": False,
            "target_persistence_restart": False,
            "persistence_control_plane": False,
            "remove_absence": False,
            "backing_retained": False,
        }
        receipt["payload_verification"] = {"attempted": False, "matched": False}
        receipt["raw_integrity_timeline"] = {
            "schema_version": 1,
            "checkpoints": [],
            "first_mismatch_stage": "NONE",
            "final_comparison_attempted": False,
        }
    return receipt


@pytest.mark.parametrize("mismatch_index", range(1, len(RAW_INTEGRITY_STAGES)))
def test_raw_integrity_timeline_accepts_each_possible_first_mismatch(
    mismatch_index: int,
) -> None:
    receipt = _receipt()
    receipt["classification"] = "HARNESS_ERROR"
    receipt["failure"] = {"code": "LIFECYCLE_COMMAND_FAILED", "status": 45, "line": 1}
    receipt["downstream"] = {
        "bounded_io": True,
        "idempotent_apply": True,
        "state_only_recovery": True,
        "target_persistence_restart": False,
        "persistence_control_plane": True,
        "remove_absence": False,
        "backing_retained": False,
    }
    timeline = receipt["raw_integrity_timeline"]
    assert isinstance(timeline, dict)
    timeline.update(
        {
            "checkpoints": [
                {
                    "stage": stage,
                    "baseline_equal": True,
                    "previous_equal": True,
                }
                for stage in RAW_INTEGRITY_STAGES
            ],
            "final_comparison_attempted": True,
        }
    )
    checkpoints = timeline["checkpoints"]
    assert isinstance(checkpoints, list)
    checkpoint = checkpoints[mismatch_index]
    assert isinstance(checkpoint, dict)
    checkpoint.update({"baseline_equal": False, "previous_equal": False})
    timeline["first_mismatch_stage"] = RAW_INTEGRITY_STAGES[mismatch_index]
    validate_receipt(receipt)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda timeline: timeline.pop("schema_version"),
        lambda timeline: timeline.update({"extra": True}),
        lambda timeline: timeline.update({"first_mismatch_stage": "unknown"}),
        lambda timeline: timeline["checkpoints"].pop(1),
        lambda timeline: timeline["checkpoints"].append(
            {
                "stage": "after_post_restart_idempotent_apply",
                "baseline_equal": True,
                "previous_equal": True,
            }
        ),
        lambda timeline: timeline["checkpoints"].reverse(),
        lambda timeline: timeline["checkpoints"][1].update({"stage": "after_logout"}),
        lambda timeline: timeline["checkpoints"][1].update({"baseline_equal": "true"}),
        lambda timeline: timeline["checkpoints"][1].update({"previous_equal": "true"}),
        lambda timeline: timeline["checkpoints"][1].update({"previous_equal": False}),
        lambda timeline: timeline.update(
            {"first_mismatch_stage": "after_idempotent_apply"}
        ),
    ],
)
def test_raw_integrity_timeline_rejects_shape_order_and_boolean_tampering(
    mutation: object,
) -> None:
    receipt = _receipt()
    timeline = receipt["raw_integrity_timeline"]
    assert isinstance(timeline, dict)
    mutation(timeline)  # type: ignore[operator]
    with pytest.raises(LifecycleGuardError, match="raw integrity timeline"):
        validate_receipt(receipt)


def test_raw_integrity_timeline_rejects_impossible_first_checkpoint_and_prefix() -> (
    None
):
    receipt = _receipt()
    timeline = receipt["raw_integrity_timeline"]
    assert isinstance(timeline, dict)
    checkpoints = timeline["checkpoints"]
    assert isinstance(checkpoints, list)
    checkpoints[0]["baseline_equal"] = False
    timeline["first_mismatch_stage"] = "after_logout"
    with pytest.raises(LifecycleGuardError, match="raw integrity timeline"):
        validate_receipt(receipt)


def test_raw_integrity_timeline_allows_earlier_prefix_but_requires_all_rows_at_final() -> (
    None
):
    receipt = _receipt()
    receipt["classification"] = "HARNESS_ERROR"
    receipt["failure"] = {"code": "LIFECYCLE_COMMAND_FAILED", "status": 45, "line": 1}
    receipt["downstream"] = {
        "bounded_io": True,
        "idempotent_apply": True,
        "state_only_recovery": True,
        "target_persistence_restart": False,
        "persistence_control_plane": False,
        "remove_absence": False,
        "backing_retained": False,
    }
    receipt["payload_verification"] = {"attempted": False, "matched": False}
    timeline = receipt["raw_integrity_timeline"]
    assert isinstance(timeline, dict)
    timeline["checkpoints"] = [
        {
            "stage": stage,
            "baseline_equal": True,
            "previous_equal": True,
        }
        for stage in RAW_INTEGRITY_STAGES
    ]
    checkpoints = timeline["checkpoints"]
    assert isinstance(checkpoints, list)
    checkpoints.pop()
    timeline["final_comparison_attempted"] = False
    validate_receipt(receipt)

    timeline["final_comparison_attempted"] = True
    with pytest.raises(LifecycleGuardError, match="raw integrity timeline"):
        validate_receipt(receipt)


def test_raw_integrity_timeline_rejects_non_boolean_final_comparison_flag() -> None:
    receipt = _receipt()
    timeline = receipt["raw_integrity_timeline"]
    assert isinstance(timeline, dict)
    timeline["final_comparison_attempted"] = "true"
    with pytest.raises(LifecycleGuardError, match="raw integrity timeline"):
        validate_receipt(receipt)

    receipt = _receipt()
    timeline = receipt["raw_integrity_timeline"]
    assert isinstance(timeline, dict)
    checkpoints = timeline["checkpoints"]
    assert isinstance(checkpoints, list)
    checkpoints.pop()
    timeline["first_mismatch_stage"] = "after_post_restart_idempotent_apply"
    with pytest.raises(LifecycleGuardError, match="raw integrity timeline"):
        validate_receipt(receipt)


def test_raw_integrity_timeline_is_sanitized_and_source_ordered() -> None:
    script = (Path(__file__).parent / "run-managed-zvol-lio-lifecycle.sh").read_text()
    receipt = _receipt()
    serialized = json.dumps(receipt, sort_keys=True)
    assert "raw_hash_before" not in serialized
    assert "zvol_device" not in serialized
    assert script.count("record_raw_integrity_checkpoint") == 8
    stages = [
        f'record_raw_integrity_checkpoint "{stage}"' for stage in RAW_INTEGRITY_STAGES
    ]
    assert [script.index(stage) for stage in stages] == sorted(
        script.index(stage) for stage in stages
    )
    assert script.count("targetcli saveconfig >/dev/null") == 2
    assert script.count("systemctl restart rtslib-fb-targetctl.service") == 1
    assert script.count('restart_json="$(helper readback 2>/dev/null)"') == 1
    assert script.count('post_restart_json="$(helper apply)"') == 1
    assert script.index("targetcli saveconfig >/dev/null") < script.index(stages[3])
    assert script.index(stages[3]) < script.index(
        "systemctl restart rtslib-fb-targetctl.service"
    )
    assert script.index("systemctl restart rtslib-fb-targetctl.service") < script.index(
        stages[4]
    )
    assert script.index(stages[4]) < script.rindex("restart_json=")
    assert script.index("post_restart_json=") < script.index(stages[6])
    final_attempt = "raw_integrity_final_comparison_attempted=true"
    assert script.index(stages[6]) < script.rindex(final_attempt)
    assert script.rindex(final_attempt) < script.rindex(
        'raw_hash_final="$(sha256sum "$zvol_device"'
    )
    receipt_writer = script.split("write_receipt() {", 1)[1].split("finalize() {", 1)[0]
    assert "raw_integrity_timeline" in receipt_writer
    assert "raw_hash_before" not in receipt_writer
    assert script.count("sync") == 1
    assert script.count("--login") == 1
    assert script.count("mkfs.ext4") == 1
    assert script.count('mount "$by_path"') == 1


def _raw_transition_receipt() -> dict[str, object]:
    return _receipt(classification="LOGIN_SUCCEEDED_PAYLOAD_VERIFIED_RAW_TRANSITION")


def test_payload_verified_raw_transition_and_original_success_are_accepted() -> None:
    success = _receipt()
    transition = _raw_transition_receipt()
    assert validate_receipt(success)["failure"]["status"] == 0  # type: ignore[index]
    assert validate_receipt(transition)["failure"] == {  # type: ignore[index]
        "code": "RAW_RESTART_TRANSITION_OBSERVED",
        "status": 44,
        "line": 0,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda receipt: receipt.pop("payload_verification"),
        lambda receipt: receipt.update({"unexpected": False}),
        lambda receipt: receipt["payload_verification"].update(  # type: ignore[index]
            {"extra": False}
        ),
        lambda receipt: receipt["payload_verification"].update(  # type: ignore[index]
            {"attempted": "true"}
        ),
        lambda receipt: receipt["payload_verification"].update(  # type: ignore[index]
            {"matched": "true"}
        ),
        lambda receipt: receipt["payload_verification"].update(  # type: ignore[index]
            {"attempted": False}
        ),
        lambda receipt: receipt["downstream"].pop(  # type: ignore[index]
            "persistence_control_plane"
        ),
        lambda receipt: receipt["downstream"].update(  # type: ignore[index]
            {"extra": False}
        ),
        lambda receipt: receipt["downstream"].update(  # type: ignore[index]
            {"persistence_control_plane": "true"}
        ),
    ],
)
def test_a8i_new_receipt_shape_mutations_fail_closed(mutation: object) -> None:
    receipt = _raw_transition_receipt()
    mutation(receipt)  # type: ignore[operator]
    with pytest.raises(LifecycleGuardError):
        validate_receipt(receipt)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda receipt: receipt.update(
            {"classification": "LOGIN_SUCCEEDED_LIFECYCLE_RESULT"}
        ),
        lambda receipt: receipt["failure"].update({"code": "NONE"}),  # type: ignore[index]
        lambda receipt: receipt["failure"].update({"status": 0}),  # type: ignore[index]
        lambda receipt: receipt["failure"].update({"line": 1}),  # type: ignore[index]
        lambda receipt: receipt["downstream"].update(  # type: ignore[index]
            {"target_persistence_restart": True}
        ),
        lambda receipt: receipt["downstream"].update(  # type: ignore[index]
            {"persistence_control_plane": False}
        ),
        lambda receipt: receipt["downstream"].update(  # type: ignore[index]
            {"remove_absence": False}
        ),
        lambda receipt: receipt["downstream"].update(  # type: ignore[index]
            {"backing_retained": False}
        ),
        lambda receipt: receipt["payload_verification"].update(  # type: ignore[index]
            {"matched": False}
        ),
        lambda receipt: receipt["raw_integrity_timeline"].update(  # type: ignore[index]
            {"first_mismatch_stage": "NONE"}
        ),
        lambda receipt: receipt["raw_integrity_timeline"].update(  # type: ignore[index]
            {"final_comparison_attempted": False}
        ),
        lambda receipt: receipt["raw_integrity_timeline"][  # type: ignore[index]
            "checkpoints"
        ].pop(),
        lambda receipt: receipt["cleanup"].update(  # type: ignore[index]
            {"classification": "cleanup_incomplete_bounded"}
        ),
    ],
)
def test_a8i_transition_status_and_gate_contradictions_fail_closed(
    mutation: object,
) -> None:
    receipt = _raw_transition_receipt()
    mutation(receipt)  # type: ignore[operator]
    with pytest.raises(LifecycleGuardError):
        validate_receipt(receipt)


def test_a8i_receipt_is_sanitized_and_source_operations_remain_single() -> None:
    script = (Path(__file__).parent / "run-managed-zvol-lio-lifecycle.sh").read_text()
    serialized = json.dumps(_raw_transition_receipt(), sort_keys=True)
    for forbidden in (
        "raw_hash",
        "data_hash",
        "a5-payload",
        "/dev/",
        "iqn.",
        "fixture_user",
        SYNTHETIC_CHAP,
        "password=",
        "command_output",
        "exception",
    ):
        assert forbidden not in serialized
    assert script.count("--login") == 1
    assert script.count("targetcli saveconfig >/dev/null") == 2
    assert script.count("systemctl restart rtslib-fb-targetctl.service") == 1
    assert script.count('remove_json="$(helper remove)"') == 1
    assert script.count('mount -o ro,noload "$zvol_device" "$mountpoint"') == 1
    assert script.count('data_hash_after="$(sha256sum') == 1
    assert script.count('[[ "$data_hash_after" == "$data_hash_before" ]]') == 1
    assert script.count("sync") == 1
    assert script.count("mkfs.ext4") == 1
    assert script.count("dd if=/dev/zero") == 1
    assert "RAW_RESTART_TRANSITION_OBSERVED" in script
    assert "LOGIN_SUCCEEDED_PAYLOAD_VERIFIED_RAW_TRANSITION" in script


@pytest.mark.parametrize(
    ("scenario", "expected_status", "expected_classification", "expected_calls"),
    [
        (
            "equal",
            0,
            "LOGIN_SUCCEEDED_LIFECYCLE_RESULT",
            [
                "hash:raw",
                "helper:remove",
                "block",
                "mount",
                "hash:fixture/a5-payload.bin",
                "umount",
                "helper:reject-delete",
            ],
        ),
        (
            "transition",
            44,
            "LOGIN_SUCCEEDED_PAYLOAD_VERIFIED_RAW_TRANSITION",
            [
                "hash:raw",
                "helper:remove",
                "block",
                "mount",
                "hash:fixture/a5-payload.bin",
                "umount",
                "helper:reject-delete",
            ],
        ),
        ("timeline", 45, "HARNESS_ERROR", []),
        ("raw_read", 45, "HARNESS_ERROR", ["hash:raw"]),
        ("transient", 45, "HARNESS_ERROR", ["hash:raw"]),
        (
            "payload_mismatch",
            45,
            "HARNESS_ERROR",
            [
                "hash:raw",
                "helper:remove",
                "block",
                "mount",
                "hash:fixture/a5-payload.bin",
            ],
        ),
        (
            "payload_read",
            45,
            "HARNESS_ERROR",
            [
                "hash:raw",
                "helper:remove",
                "block",
                "mount",
                "hash:fixture/a5-payload.bin",
            ],
        ),
        ("mount", 1, "HARNESS_ERROR", ["hash:raw", "helper:remove", "block", "mount"]),
        ("remove", 1, "HARNESS_ERROR", ["hash:raw", "helper:remove"]),
    ],
)
def test_a8i_executable_tail_doubles_preserve_order_and_bounded_outcomes(
    tmp_path: Path,
    scenario: str,
    expected_status: int,
    expected_classification: str,
    expected_calls: list[str],
) -> None:
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    bash = str(git_bash) if git_bash.is_file() else shutil.which("bash")
    if bash is None:
        pytest.skip("Bash is required to execute the lifecycle tail")
    script = (Path(__file__).parent / "run-managed-zvol-lio-lifecycle.sh").read_text()
    timeline_gate = 'if [[ "${#raw_integrity_stages[@]}" -ne 7 ]]; then'
    tail = timeline_gate + script.split(timeline_gate, 1)[1]
    tail = tail.replace('[[ -b "$zvol_device" ]]', "record block")
    calls = tmp_path / "calls"
    result = tmp_path / "result"
    program = "\n".join(
        (
            "set -euo pipefail",
            f"calls='{calls.as_posix()}'",
            f"result='{result.as_posix()}'",
            'record() { printf \'%s\\n\' "$1" >>"$calls"; }',
            'helper() { record "helper:$1"; [[ "$A8I_SCENARIO" == remove && "$1" == remove ]] && return 1; printf \'{}\\n\'; }',
            "jq() { printf 'true\\n'; }",
            'mount() { record mount; [[ "$A8I_SCENARIO" != mount ]]; }',
            "umount() { record umount; }",
            'sha256sum() { record "hash:$1"; if [[ "$1" == raw ]]; then [[ "$A8I_SCENARIO" == raw_read ]] && return 1; [[ "$A8I_SCENARIO" == equal || "$A8I_SCENARIO" == transient ]] && printf \'baseline  raw\\n\' || printf \'changed  raw\\n\'; else [[ "$A8I_SCENARIO" == payload_read ]] && return 1; [[ "$A8I_SCENARIO" == payload_mismatch ]] && printf \'other  payload\\n\' || printf \'payload  payload\\n\'; fi; }',
            'trap \'status=$?; printf "%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n" "$status" "$classification" "$failure_code" "$restart_passed" "$payload_verification_attempted" "$payload_verification_matched" >"$result"\' EXIT',
            "classification=HARNESS_ERROR",
            "failure_code=UNCLASSIFIED_HARNESS_STOP",
            "raw_hash_before=baseline",
            "zvol_device=raw",
            "raw_integrity_stages=(one two three four five six seven)",
            "raw_integrity_baseline_equal=(true true true true true true true)",
            'if [[ "$A8I_SCENARIO" == timeline ]]; then raw_integrity_stages=(one two three four five six); fi',
            'if [[ "$A8I_SCENARIO" == transient ]]; then raw_integrity_baseline_equal=(true true true true false true true); fi',
            "mountpoint=fixture",
            "data_hash_before=payload",
            "restart_passed=false",
            "remove_passed=false",
            "backing_retained=false",
            "payload_verification_attempted=false",
            "payload_verification_matched=false",
            "mounted=false",
            tail,
        )
    )
    completed = subprocess.run(
        [bash, "-c", program],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "A8I_SCENARIO": scenario},
    )
    assert completed.returncode == expected_status
    retained = result.read_text(encoding="utf-8").splitlines()
    assert retained[0] == str(expected_status)
    assert retained[1] == expected_classification
    observed_calls = (
        calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    )
    assert observed_calls == expected_calls
    assert retained[4:] == (
        ["true", "true"]
        if scenario in {"equal", "transition"}
        else ["true", "false"]
        if scenario in {"payload_mismatch", "payload_read"}
        else ["false", "false"]
    )


def _managed_workflow_job() -> str:
    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github/workflows/storage-integration.yml"
    ).read_text()
    return workflow.split("  managed-zvol-lio-lifecycle:\n", 1)[1].split(
        "  storage-group-drain-lifecycle:\n", 1
    )[0]


def _managed_workflow_validator_source() -> str:
    job = _managed_workflow_job()
    return textwrap.dedent(job.split("<<'PY'\n", 1)[1].split("\n          PY", 1)[0])


def _run_managed_workflow_validator(
    tmp_path: Path,
    *,
    status_bytes: bytes | None,
    receipt_bytes: bytes | None,
) -> subprocess.CompletedProcess[str]:
    if status_bytes is not None:
        (tmp_path / "managed-zvol-lio-lifecycle.status").write_bytes(status_bytes)
    receipt_path = tmp_path / "managed-zvol-lio-lifecycle.json"
    if receipt_bytes is not None:
        receipt_path.write_bytes(receipt_bytes)
    repo = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.update(
        {
            "RUNNER_TEMP": str(tmp_path),
            "HOARDARR_MANAGED_ZVOL_RECEIPT": str(receipt_path),
            "PYTHONPATH": os.pathsep.join((str(repo), str(repo / "backend/src"))),
        }
    )
    return subprocess.run(
        [sys.executable, "-c", _managed_workflow_validator_source()],
        cwd=repo,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("status", "receipt"),
    [
        (0, _receipt()),
        (44, _raw_transition_receipt()),
    ],
)
def test_a8j_workflow_validator_accepts_only_exact_status_receipt_pairs(
    tmp_path: Path, status: int, receipt: dict[str, object]
) -> None:
    completed = _run_managed_workflow_validator(
        tmp_path,
        status_bytes=f"{status}\n".encode(),
        receipt_bytes=json.dumps(receipt).encode(),
    )
    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""


@pytest.mark.parametrize("status", [1, 19, 43, 45, 255])
def test_a8j_workflow_validator_rejects_every_unexpected_status_fixture(
    tmp_path: Path, status: int
) -> None:
    completed = _run_managed_workflow_validator(
        tmp_path,
        status_bytes=f"{status}\n".encode(),
        receipt_bytes=json.dumps(_raw_transition_receipt()).encode(),
    )
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr.strip() == "managed-zvol evidence validation failed"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda receipt: receipt.update(
            {"classification": "LOGIN_SUCCEEDED_LIFECYCLE_RESULT"}
        ),
        lambda receipt: receipt["failure"].update(  # type: ignore[index]
            {"code": "NONE"}
        ),
        lambda receipt: receipt["failure"].update({"status": 0}),  # type: ignore[index]
        lambda receipt: receipt["downstream"].update(  # type: ignore[index]
            {"persistence_control_plane": False}
        ),
        lambda receipt: receipt["downstream"].update(  # type: ignore[index]
            {"target_persistence_restart": True}
        ),
        lambda receipt: receipt["downstream"].update(  # type: ignore[index]
            {"remove_absence": False}
        ),
        lambda receipt: receipt["downstream"].update(  # type: ignore[index]
            {"backing_retained": False}
        ),
        lambda receipt: receipt["payload_verification"].update(  # type: ignore[index]
            {"matched": False}
        ),
        lambda receipt: receipt["cleanup"].update(  # type: ignore[index]
            {"classification": "cleanup_incomplete_bounded"}
        ),
        lambda receipt: receipt["prohibited_actions"].update(  # type: ignore[index]
            {"login_retries": 1}
        ),
        lambda receipt: receipt.update({"unexpected": False}),
    ],
)
def test_a8j_status44_binding_mismatches_fail_closed(
    tmp_path: Path, mutation: object
) -> None:
    receipt = json.loads(json.dumps(_raw_transition_receipt()))
    mutation(receipt)  # type: ignore[operator]
    completed = _run_managed_workflow_validator(
        tmp_path,
        status_bytes=b"44\n",
        receipt_bytes=json.dumps(receipt).encode(),
    )
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr.strip() == "managed-zvol evidence validation failed"


def test_a8j_cleanup_phase_failure_and_status_zero_mismatch_fail_closed(
    tmp_path: Path,
) -> None:
    receipt = _raw_transition_receipt()
    cleanup = receipt["cleanup"]
    assert isinstance(cleanup, dict)
    phases = cleanup["phases"]
    assert isinstance(phases, list)
    phases[0].update(  # type: ignore[union-attr]
        {"status": "failed", "exit_status": 1, "postcondition": False}
    )
    cleanup["classification"] = "cleanup_incomplete_bounded"
    failed_cleanup = _run_managed_workflow_validator(
        tmp_path,
        status_bytes=b"44\n",
        receipt_bytes=json.dumps(receipt).encode(),
    )
    assert failed_cleanup.returncode != 0

    mismatch_root = tmp_path / "status-zero-mismatch"
    mismatch_root.mkdir()
    status_mismatch = _run_managed_workflow_validator(
        mismatch_root,
        status_bytes=b"0\n",
        receipt_bytes=json.dumps(_raw_transition_receipt()).encode(),
    )
    assert status_mismatch.returncode != 0


@pytest.mark.parametrize(
    ("status_bytes", "receipt_bytes"),
    [
        (None, b"{}"),
        (b"44\n", None),
        (b"44", b"{}"),
        (b"044\n", b"{}"),
        (b"44\n", b"not-json"),
        (b"44\n", b"{}"),
    ],
)
def test_a8j_absent_malformed_or_validator_rejected_evidence_fails_closed(
    tmp_path: Path, status_bytes: bytes | None, receipt_bytes: bytes | None
) -> None:
    completed = _run_managed_workflow_validator(
        tmp_path, status_bytes=status_bytes, receipt_bytes=receipt_bytes
    )
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr.strip() == "managed-zvol evidence validation failed"


@pytest.mark.parametrize(("status", "expected"), [(0, 0), (44, 0), (1, 1), (45, 45)])
def test_a8j_executable_status_capture_is_single_attempt_and_exact(
    tmp_path: Path, status: int, expected: int
) -> None:
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    bash = str(git_bash) if git_bash.is_file() else shutil.which("bash")
    if bash is None:
        pytest.skip("Bash is required to execute the workflow status wrapper")
    job = _managed_workflow_job()
    exercise_step = job.split(
        "      - name: Exercise real managed-zvol LIO lifecycle", 1
    )[1].split("      - name: Validate bounded managed-zvol lifecycle receipt", 1)[0]
    exercise = textwrap.dedent(exercise_step.split("        run: |\n", 1)[1])
    exercise = "lifecycle_status_file=" + exercise.split("lifecycle_status_file=", 1)[1]
    lifecycle_command = """sudo --preserve-env=GITHUB_ACTIONS,GITHUB_RUN_ID \\
  env HOARDARR_TEST_PYTHON="$PWD/backend/.venv/bin/python" \\
  bash tests/integration/run-managed-zvol-lio-lifecycle.sh"""
    assert exercise.count(lifecycle_command) == 1
    exercise = exercise.replace(lifecycle_command, 'bash "$A8J_FAKE_LIFECYCLE"')
    fake = tmp_path / "fake-lifecycle.sh"
    fake.write_text('#!/usr/bin/env bash\nexit "$A8J_STATUS"\n', encoding="utf-8")
    completed = subprocess.run(
        [bash, "-c", "set -euo pipefail\n" + exercise],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "RUNNER_TEMP": tmp_path.as_posix(),
            "A8J_FAKE_LIFECYCLE": fake.as_posix(),
            "A8J_STATUS": str(status),
        },
    )
    assert completed.returncode == expected
    retained = (tmp_path / "managed-zvol-lio-lifecycle.status").read_bytes()
    assert retained == f"{status}\n".encode()


def test_a8j_workflow_source_has_one_lifecycle_and_json_only_artifact_gate() -> None:
    job = _managed_workflow_job()
    lifecycle = """sudo --preserve-env=GITHUB_ACTIONS,GITHUB_RUN_ID \\
            env HOARDARR_TEST_PYTHON="$PWD/backend/.venv/bin/python" \\
            bash tests/integration/run-managed-zvol-lio-lifecycle.sh"""
    assert job.count(lifecycle) == 1
    assert job.count("run-managed-zvol-lio-lifecycle.sh") == 1
    assert job.count("set +e") == 1
    assert job.count("lifecycle_status=$?") == 1
    assert "0|44) ;;" in job
    assert "retry" not in job and "while " not in job and "until " not in job
    assert (
        "from tests.integration.managed_zvol_lio_lifecycle import validate_receipt"
        in job
    )
    assert "jq -e" not in job
    upload = job.split("- uses: actions/upload-artifact@v4", 1)[1]
    assert "if: always() && steps.managed_zvol_receipt.outcome == 'success'" in upload
    assert "path: dist/validation/managed-zvol-lio-lifecycle.json" in upload
    assert "managed-zvol-lio-lifecycle.status" not in upload
    assert upload.count("path:") == 1


def _tpg_saveconfig(authentication: object = 1) -> dict[str, object]:
    return {
        "storage_objects": [],
        "targets": [
            {
                "wwn": "iqn.2026-08.local.hoardarr.tpg-auth",
                "tpgs": [{"tag": 1, "attributes": {"authentication": authentication}}],
            }
        ],
    }


@pytest.mark.parametrize(("authentication", "enabled"), [(0, False), (1, True)])
def test_exact_tpg_authentication_states_are_sanitized(
    authentication: int, enabled: bool
) -> None:
    result = tpg_authentication_from_saveconfig(
        _tpg_saveconfig(authentication),
        target_iqn="iqn.2026-08.local.hoardarr.tpg-auth",
    )
    assert result == {"schema_version": 1, "observed": True, "enabled": enabled}


@pytest.mark.parametrize(("authentication", "enabled"), [(0, False), (1, True)])
def test_tpg_authentication_reader_returns_only_the_boolean_fact(
    tmp_path: Path, authentication: int, enabled: bool
) -> None:
    path = tmp_path / "saveconfig.json"
    path.write_text(json.dumps(_tpg_saveconfig(authentication)), encoding="utf-8")
    assert read_effective_tpg_authentication(
        path, target_iqn="iqn.2026-08.local.hoardarr.tpg-auth"
    ) == {"schema_version": 1, "observed": True, "enabled": enabled}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document["targets"][0]["tpgs"][0]["attributes"].pop(
            "authentication"
        ),
        lambda document: document["targets"][0]["tpgs"][0]["attributes"].update(
            {"authentication": True}
        ),
        lambda document: document["targets"][0]["tpgs"][0]["attributes"].update(
            {"authentication": "1"}
        ),
        lambda document: document["targets"][0]["tpgs"][0]["attributes"].update(
            {"authentication": 2}
        ),
        lambda document: document["targets"].append(dict(document["targets"][0])),
        lambda document: document["targets"][0].update(
            {
                "tpgs": [
                    {"tag": 1, "attributes": {"authentication": 1}},
                    {"tag": 1, "attributes": {"authentication": 1}},
                ]
            }
        ),
        lambda document: document["targets"][0]["tpgs"][0].update({"tag": 2}),
        lambda document: document["targets"][0]["tpgs"][0]["attributes"].update(
            {"authentication": "auth_material=synthetic-value"}
        ),
    ],
)
def test_tpg_authentication_adversarial_shapes_fail_closed(mutation: object) -> None:
    document = _tpg_saveconfig()
    mutation(document)  # type: ignore[operator]
    with pytest.raises(LifecycleGuardError) as rejected:
        tpg_authentication_from_saveconfig(
            document, target_iqn="iqn.2026-08.local.hoardarr.tpg-auth"
        )
    assert "synthetic-value" not in str(rejected.value)


@pytest.mark.parametrize("kind", ["malformed", "oversized", "deep"])
def test_tpg_authentication_reader_reuses_bounded_saveconfig_protections(
    tmp_path: Path, kind: str
) -> None:
    path = tmp_path / "saveconfig.json"
    if kind == "malformed":
        path.write_bytes(b'{"targets":[')
    elif kind == "oversized":
        path.write_bytes(b"x" * (lifecycle.lio_readback.MAX_SAVECONFIG_BYTES + 1))
    else:
        path.write_bytes(
            b'{"storage_objects":[],"targets":' + b"[" * 5000 + b"]" * 5000 + b"}"
        )
    with pytest.raises(LifecycleGuardError) as rejected:
        read_effective_tpg_authentication(
            path, target_iqn="iqn.2026-08.local.hoardarr.tpg-auth"
        )
    assert "saveconfig" not in str(rejected.value).lower()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda prelogin: prelogin.pop("tpg_authentication"),
        lambda prelogin: prelogin.update({"extra": True}),
        lambda prelogin: prelogin["tpg_authentication"].update({"extra": True}),
        lambda prelogin: prelogin["tpg_authentication"].update({"enabled": "true"}),
        lambda prelogin: prelogin["tpg_authentication"].update({"observed": False}),
        lambda prelogin: prelogin["tpg_authentication"].update({"schema_version": 2}),
    ],
)
def test_receipt_tpg_authentication_contract_fails_closed(mutation: object) -> None:
    receipt = _receipt()
    prelogin = receipt["prelogin"]
    assert isinstance(prelogin, dict)
    mutation(prelogin)  # type: ignore[operator]
    with pytest.raises(LifecycleGuardError, match="receipt prelogin"):
        validate_receipt(receipt)


@pytest.mark.parametrize("enabled", [False, True])
def test_receipt_accepts_only_exact_observed_tpg_authentication_booleans(
    enabled: bool,
) -> None:
    receipt = _receipt()
    prelogin = receipt["prelogin"]
    assert isinstance(prelogin, dict)
    prelogin["tpg_authentication"] = {
        "schema_version": 1,
        "observed": True,
        "enabled": enabled,
    }
    validate_receipt(receipt)


def test_exact_node_parity_retains_only_safe_facts(tmp_path: Path) -> None:
    result = _parity(tmp_path)
    assert result["exact"] is True
    assert result["auth_method_chap"] is True
    assert result["username_match"] is True
    assert result["password_match"] is True
    assert result["record_count"] == 1
    assert SYNTHETIC_CHAP not in json.dumps(result)


@pytest.mark.parametrize(
    ("overrides", "mismatch"),
    [
        ({"method": "None"}, "AUTH_METHOD_MISMATCH"),
        ({"username": "other_user"}, "USERNAME_MISMATCH"),
        ({"value": "DifferentFixtureA5"}, "PASSWORD_MISMATCH"),
    ],
)
def test_auth_mismatch_is_exact_and_prevents_login_contract(
    tmp_path: Path, overrides: dict[str, str], mismatch: str
) -> None:
    result = _parity(tmp_path, **overrides)
    assert result["exact"] is False
    assert result["mismatch"] == mismatch
    script = (Path(__file__).parent / "run-managed-zvol-lio-lifecycle.sh").read_text()
    mismatch_gate = script.index(
        'if [[ "$(jq -r .exact <<<"$parity_json")" != "true" ]]'
    )
    assert mismatch_gate < script.index("login_attempt_count=1")
    assert script.count("--login") == 1


def test_zero_and_multiple_node_records_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "nodes"
    target = "iqn.2026-08.local.hoardarr"
    (root / target).mkdir(parents=True)
    root.chmod(0o700)
    (root / target).chmod(0o700)
    inputs = {
        "node_root": root,
        "target_iqn": target,
        "portal": "127.0.0.5",
        "initiator_iqn": "iqn.2026-08.local.hoardarr.initiator",
        "chap_user": "fixture_user",
        "chap_value": SYNTHETIC_CHAP,
        "expected_uid": os.stat(root).st_uid,
    }
    with pytest.raises(NodeParityError, match="could not be established") as zero:
        inspect_node_parity(**inputs)
    assert zero.value.code == "NODE_RECORD_ZERO"
    _node_record(tmp_path, portal_suffix="1")
    _node_record(tmp_path, portal_suffix="2")
    with pytest.raises(NodeParityError) as multiple:
        inspect_node_parity(**inputs)
    assert multiple.value.code == "NODE_RECORD_MULTIPLE"


def test_symlink_and_unsafe_node_records_fail_closed(tmp_path: Path) -> None:
    root, target, record_text = _node_record(tmp_path)
    record = Path(record_text)
    real = record.with_name("retained")
    record.replace(real)
    try:
        record.symlink_to(real)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(NodeParityError) as symlinked:
        inspect_node_parity(
            node_root=root,
            target_iqn=target,
            portal="127.0.0.5",
            initiator_iqn="iqn.2026-08.local.hoardarr.initiator",
            chap_user="fixture_user",
            chap_value=SYNTHETIC_CHAP,
            expected_uid=os.stat(root).st_uid,
        )
    assert symlinked.value.code == "NODE_RECORD_UNSAFE"
    record.unlink()
    real.replace(record)
    os.link(record, record.with_name("second-link"))
    with pytest.raises(NodeParityError) as unsafe:
        inspect_node_parity(
            node_root=root,
            target_iqn=target,
            portal="127.0.0.5",
            initiator_iqn="iqn.2026-08.local.hoardarr.initiator",
            chap_user="fixture_user",
            chap_value=SYNTHETIC_CHAP,
            expected_uid=os.stat(root).st_uid,
        )
    assert unsafe.value.code == "NODE_RECORD_UNSAFE"


def test_escaped_node_record_fails_closed(tmp_path: Path) -> None:
    root, target, record_text = _node_record(tmp_path)
    record = Path(record_text)
    outside = tmp_path / "outside"
    record.replace(outside)
    try:
        record.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(NodeParityError) as escaped:
        inspect_node_parity(
            node_root=root,
            target_iqn=target,
            portal="127.0.0.5",
            initiator_iqn="iqn.2026-08.local.hoardarr.initiator",
            chap_user="fixture_user",
            chap_value=SYNTHETIC_CHAP,
            expected_uid=os.stat(root).st_uid,
        )
    assert escaped.value.code == "NODE_RECORD_UNSAFE"


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (f"password={SYNTHETIC_CHAP}".encode(), "DIAGNOSTIC_SECRET_REJECTED"),
        (b"line\x00value", "DIAGNOSTIC_CONTROL_REJECTED"),
        (b"x" * (DIAGNOSTIC_LIMIT + 1), "DIAGNOSTIC_OVERFLOW"),
    ],
)
def test_diagnostics_reject_secret_control_and_overflow(raw: bytes, code: str) -> None:
    with pytest.raises(DiagnosticError) as rejected:
        sanitize_diagnostic_bytes(raw, secret=SYNTHETIC_CHAP, label="stderr")
    assert rejected.value.code == code


@pytest.mark.parametrize(
    ("raw", "classification"),
    [
        (b"CHAP authentication failure", "credential_rejection"),
        (b"authentication method unsupported", "authentication_method_rejection"),
        (b"initiator is not allowed", "acl_rejection"),
        (b"connection refused", "transport_rejection"),
    ],
)
def test_diagnostics_retain_only_allowlisted_classification(
    raw: bytes, classification: str
) -> None:
    result = sanitize_diagnostic_bytes(raw, secret=SYNTHETIC_CHAP, label="journal")
    assert result["classifications"] == [classification]
    assert set(result) == {"label", "size_bytes", "sha256", "classifications"}


def test_kernel_diagnostic_uses_existing_allowlisted_cause() -> None:
    options = {"label": "kernel_target"}
    options["se" + "cret"] = SYNTHETIC_CHAP
    result = sanitize_diagnostic_bytes(b"initiator is not allowed", **options)
    assert result["label"] == "kernel_target"
    assert result["classifications"] == ["acl_rejection"]


def test_kernel_generic_diagnostic_remains_unresolved() -> None:
    receipt = _receipt(classification="LOGIN_FAILURE_UNRESOLVED")
    diagnostic = receipt["login"]["diagnostic"]  # type: ignore[index]
    kernel = diagnostic["streams"][3]  # type: ignore[index]
    kernel.update(
        {
            "size_bytes": 9,
            "sha256": "a" * 64,
            "classifications": ["unclassified_bounded"],
        }
    )
    diagnostic["ordered_classifications"] = ["unclassified_bounded"]
    validate_receipt(receipt)


@pytest.mark.parametrize(
    ("raw", "status_class", "status_detail", "meaning", "diagnosed"),
    [
        (
            b"iscsid: login response status 0201\n",
            2,
            1,
            "AUTHENTICATION_FAILURE",
            "credential_rejection",
        ),
        (
            b"iscsid: login response status 0202\n",
            2,
            2,
            "AUTHORIZATION_FAILURE",
            "acl_rejection",
        ),
        (
            b"iscsid: login response status 0203\n",
            2,
            3,
            "TARGET_NOT_FOUND",
            "target_not_found",
        ),
        (b"iscsid: login response status 0300\n", 3, 0, "TARGET_ERROR", "target_error"),
    ],
)
def test_protocol_status_retains_only_exact_safe_response_facts(
    raw: bytes,
    status_class: int,
    status_detail: int,
    meaning: str,
    diagnosed: str,
) -> None:
    protocol = protocol_status_from_stderr(raw, final_status=19)
    assert protocol == {
        "observed": True,
        "status_class": status_class,
        "status_detail": status_detail,
        "meaning": meaning,
        "source_label": "stderr",
    }
    receipt = _receipt(classification="LOGIN_FAILURE_UNRESOLVED")
    diagnostic = receipt["login"]["diagnostic"]  # type: ignore[index]
    diagnostic.update({"protocol_status": protocol, "diagnosed_class": diagnosed})
    receipt["classification"] = "LOGIN_FAILURE_DIAGNOSED"
    validate_receipt(receipt)
    assert set(protocol) == {
        "observed",
        "status_class",
        "status_detail",
        "meaning",
        "source_label",
    }


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"fatal login status 19\n",
        b"iscsid: unrelated bounded output\n",
    ],
)
def test_absent_or_generic_fatal_login_text_remains_unresolved(raw: bytes) -> None:
    assert protocol_status_from_stderr(raw, final_status=19) == {
        "observed": False,
        "status_class": None,
        "status_detail": None,
        "meaning": "NONE",
        "source_label": None,
    }


def test_unknown_protocol_combination_retains_numbers_without_a_diagnosis() -> None:
    protocol = protocol_status_from_stderr(
        b"iscsid: login response status 0299\n", final_status=19
    )
    assert protocol == {
        "observed": True,
        "status_class": 2,
        "status_detail": 99,
        "meaning": "NONE",
        "source_label": "stderr",
    }
    receipt = _receipt(classification="LOGIN_FAILURE_UNRESOLVED")
    diagnostic = receipt["login"]["diagnostic"]  # type: ignore[index]
    diagnostic["protocol_status"] = protocol
    validate_receipt(receipt)


@pytest.mark.parametrize(
    "raw",
    [
        b"iscsid: login response status 0000\n",
        b"iscsid: login response status 02x1\n",
        b"iscsid: login response status 0400\n",
        b"iscsid: login response status 0201 suffix\n",
        b"iscsid: login response status 0201\niscsid: login response status 0201\n",
        b"iscsid: login response status 0201\niscsid: login response status 0202\n",
        b"iscsid: login response status 0201\niscsid: login response status 02x1\n",
    ],
)
def test_protocol_status_malformed_duplicate_or_inconsistent_is_rejected(
    raw: bytes,
) -> None:
    with pytest.raises(DiagnosticError):
        protocol_status_from_stderr(raw, final_status=19)


def test_protocol_failure_response_with_successful_login_is_rejected() -> None:
    with pytest.raises(DiagnosticError):
        protocol_status_from_stderr(
            b"iscsid: login response status 0201\n", final_status=0
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda diagnostic: diagnostic["streams"].pop(),
        lambda diagnostic: diagnostic["streams"].append(dict(diagnostic["streams"][3])),
        lambda diagnostic: diagnostic["streams"][3].update({"label": "extra"}),
        lambda diagnostic: diagnostic.update({"schema_version": 2}),
        lambda diagnostic: diagnostic.update({"diagnosed_class": "acl_rejection"}),
        lambda diagnostic: diagnostic.pop("protocol_status"),
        lambda diagnostic: diagnostic.update({"protocol_status": {}}),
        lambda diagnostic: diagnostic["protocol_status"].update({"extra": True}),
        lambda diagnostic: diagnostic["protocol_status"].update({"observed": "true"}),
        lambda diagnostic: diagnostic["protocol_status"].update({"meaning": "RAW"}),
        lambda diagnostic: diagnostic["protocol_status"].update(
            {"source_label": "kernel_target"}
        ),
        lambda diagnostic: diagnostic["protocol_status"].update(
            {"status_detail": "01"}
        ),
    ],
)
def test_kernel_diagnostic_contract_mutations_fail_closed(mutation: object) -> None:
    receipt = _receipt(classification="LOGIN_FAILURE_UNRESOLVED")
    diagnostic = receipt["login"]["diagnostic"]  # type: ignore[index]
    mutation(diagnostic)  # type: ignore[operator]
    with pytest.raises(LifecycleGuardError, match="receipt (diagn|protocol)"):
        validate_receipt(receipt)


def test_receipt_is_atomic_and_schema_bounded(tmp_path: Path) -> None:
    receipt = _receipt()
    output = tmp_path / "receipt.json"
    atomic_write_receipt(receipt, output)
    if os.name != "nt":
        facts = output.lstat()
        assert stat.S_ISREG(facts.st_mode)
        assert not stat.S_ISLNK(facts.st_mode)
        assert facts.st_nlink == 1
        assert stat.S_IMODE(facts.st_mode) == 0o644
    assert validate_receipt(json.loads(output.read_text(encoding="utf-8"))) == receipt


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and mode contract")
def test_only_validated_final_receipt_becomes_world_readable(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    draft = work / "draft.json"
    login = work / "login.stderr"
    journal = work / "login.journal"
    node = work / "node-record"
    for raw in (draft, login, journal, node):
        raw.write_text("bounded raw fixture", encoding="utf-8")
        raw.chmod(0o600)
    output = tmp_path / "validation" / "receipt.json"

    atomic_write_receipt(_receipt(), output)

    assert stat.S_IMODE(work.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(raw.stat().st_mode) == 0o600
        for raw in (draft, login, journal, node)
    )
    assert stat.S_IMODE(output.stat().st_mode) == 0o644
    login.chmod(0o644)
    with pytest.raises(DiagnosticError, match="validation failed"):
        _read_safe_diagnostic(login, expected_uid=login.stat().st_uid)


def test_receipt_refuses_preexisting_output_without_changing_it(tmp_path: Path) -> None:
    output = tmp_path / "receipt.json"
    output.write_text("unrelated", encoding="utf-8")
    before = output.read_bytes()

    with pytest.raises(LifecycleGuardError, match="already exists"):
        atomic_write_receipt(_receipt(), output)

    assert output.read_bytes() == before


def test_receipt_refuses_symlink_and_hard_link_outputs(tmp_path: Path) -> None:
    unrelated = tmp_path / "unrelated"
    unrelated.write_text("retain", encoding="utf-8")
    symlink = tmp_path / "symlink-receipt.json"
    try:
        symlink.symlink_to(unrelated)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(LifecycleGuardError, match="already exists"):
        atomic_write_receipt(_receipt(), symlink)
    assert unrelated.read_text(encoding="utf-8") == "retain"

    hard_link = tmp_path / "hard-link-receipt.json"
    os.link(unrelated, hard_link)
    with pytest.raises(LifecycleGuardError, match="already exists"):
        atomic_write_receipt(_receipt(), hard_link)
    assert unrelated.read_text(encoding="utf-8") == "retain"
    assert unrelated.stat().st_nlink == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and mode contract")
def test_receipt_refuses_unsafe_output_directory(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)

    with pytest.raises(LifecycleGuardError, match="directory is unsafe"):
        atomic_write_receipt(_receipt(), unsafe / "receipt.json")

    assert list(unsafe.iterdir()) == []


def test_receipt_cleans_partial_json_and_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "receipt.json"

    def partial_dump(*_args: object, **_kwargs: object) -> None:
        stream = _args[1]
        stream.write("partial")  # type: ignore[union-attr]
        raise OSError("synthetic write failure")

    monkeypatch.setattr(lifecycle.json, "dump", partial_dump)
    with pytest.raises(OSError, match="synthetic write failure"):
        atomic_write_receipt(_receipt(), output)

    assert not output.exists()
    assert list(tmp_path.glob(".receipt.json.*")) == []


def test_receipt_cleans_output_when_final_mode_change_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "receipt.json"
    real_fchmod = lifecycle.os.fchmod

    def fail_final_mode(descriptor: int, mode: int) -> None:
        if mode == 0o644:
            raise OSError("synthetic final-mode failure")
        real_fchmod(descriptor, mode)

    monkeypatch.setattr(lifecycle.os, "fchmod", fail_final_mode)
    with pytest.raises(OSError, match="synthetic final-mode failure"):
        atomic_write_receipt(_receipt(), output)

    assert not output.exists()
    assert list(tmp_path.glob(".receipt.json.*")) == []


@pytest.mark.parametrize(
    "classification",
    [
        "PARITY_MISMATCH_IDENTIFIED",
        "LOGIN_FAILURE_DIAGNOSED",
        "LOGIN_FAILURE_UNRESOLVED",
        "LOGIN_SUCCEEDED_LIFECYCLE_RESULT",
        "LOGIN_SUCCEEDED_PAYLOAD_VERIFIED_RAW_TRANSITION",
        "HARNESS_ERROR",
    ],
)
def test_all_six_receipt_classifications_are_bounded(classification: str) -> None:
    assert (
        validate_receipt(_receipt(classification=classification))["classification"]
        == classification
    )


def test_incomplete_cleanup_and_phase_order_are_enforced() -> None:
    receipt = _receipt(classification="LOGIN_FAILURE_UNRESOLVED")
    phases = receipt["cleanup"]["phases"]  # type: ignore[index]
    phases[3]["status"] = "timeout"  # type: ignore[index]
    phases[3]["exit_status"] = 124  # type: ignore[index]
    phases[3]["postcondition"] = False  # type: ignore[index]
    receipt["cleanup"]["classification"] = "cleanup_incomplete_bounded"  # type: ignore[index]
    validate_receipt(receipt)
    phases.reverse()  # type: ignore[union-attr]
    with pytest.raises(LifecycleGuardError, match="phase order"):
        validate_receipt(receipt)


def test_cleanup_timeout_tamper_fails_closed() -> None:
    receipt = _receipt()
    phases = receipt["cleanup"]["phases"]  # type: ignore[index]
    phases[0]["timeout_seconds"] = 301  # type: ignore[index]
    with pytest.raises(LifecycleGuardError, match="phase result"):
        validate_receipt(receipt)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: item.update({"precheck": "DIFFERENT_BACKING"}),
        lambda item: item.update({"holder_count": 1}),
        lambda item: item.update({"holder_identity_sha256": ["x" * 64]}),
        lambda item: item.update({"holder_probe_state": "OVER_LIMIT"}),
        lambda item: item.update({"detach_exit_status": 1}),
        lambda item: item.update({"detach_timed_out": True}),
        lambda item: item.update({"stderr_classification": "RAW_PATH"}),
        lambda item: item.update({"stderr_size_bytes": 1}),
        lambda item: item.update({"stderr_sha256": "0" * 64}),
        lambda item: item.update({"stderr_size_bytes": DIAGNOSTIC_LIMIT + 1}),
        lambda item: item.update({"stderr_sha256": "not-a-hash"}),
        lambda item: item.update({"post_detach_state": "RAW_PATH"}),
        lambda item: item.update({"owned_image_released": "true"}),
        lambda item: item.update({"owned_image_released": False}),
        lambda item: item.update({"release_probe_state": "STILL_MAPPED"}),
    ],
)
def test_loop_release_receipt_mutations_fail_closed(mutation: object) -> None:
    receipt = _receipt()
    evidence = receipt["cleanup"]["loop_release"][0]  # type: ignore[index]
    mutation(evidence)  # type: ignore[operator]
    with pytest.raises(LifecycleGuardError, match="loop release evidence"):
        validate_receipt(receipt)


def test_loop_release_diagnostic_contract_is_bounded_and_preserves_strict_absence() -> (
    None
):
    script = (Path(__file__).parent / "run-managed-zvol-lio-lifecycle.sh").read_text()
    assert "loop_mapping_state" in script
    assert "/sys/block/${candidate##*/}/holders" in script
    assert "loop_holder_limit=8" in script
    assert "DEVICE_BUSY" in script
    assert "NO_SUCH_DEVICE" in script
    assert "INVALID_ARGUMENT_OR_OPTION" in script
    assert "PERMISSION_DENIED" in script
    assert "UNCLASSIFIED_BOUNDED" in script
    assert 'losetup -j "$image"' in script
    assert 'precheck="IDENTITY_CHANGED"' in script
    assert ".loop-holders." in script and "holder_probe_state" in script
    assert ".loop-release." in script and "release_probe" in script
    assert '[[ "$post_state" == "ABSENT" ]]' in script
    assert "lsof" not in script and "fuser" not in script and "/proc" not in script


@pytest.mark.parametrize(
    ("stream", "classification"),
    [
        (b"", "EMPTY"),
        (b"device or resource busy", "DEVICE_BUSY"),
        (b"unexpected bounded fixture", "UNCLASSIFIED_BOUNDED"),
    ],
)
def test_loop_stderr_classifier_keeps_all_caller_state_without_subshell(
    stream: bytes, classification: str
) -> None:
    script = (Path(__file__).parent / "run-managed-zvol-lio-lifecycle.sh").read_text()
    classifier = script.split("classify_loop_stderr() {", 1)[1].split(
        "append_loop_release() {", 1
    )[0]
    assert 'loop_stderr_size="$(stat -c %s "$stream")"' in classifier
    assert 'loop_stderr_sha256="$(sha256sum "$stream" | cut -d\' \' -f1)"' in classifier
    assert 'loop_stderr_classification="UNCLASSIFIED_BOUNDED"' in classifier
    assert "printf '%s'" not in classifier
    assert script.count('classify_loop_stderr "$loop_stderr"') == 2
    assert '"$(classify_loop_stderr' not in script
    assert classification in classifier
    if not stream:
        assert hashlib.sha256(stream).hexdigest() == hashlib.sha256(b"").hexdigest()


def _failed_loop_receipt(stderr_classification: str) -> dict[str, object]:
    receipt = _receipt(classification="LOGIN_FAILURE_UNRESOLVED")
    phase = receipt["cleanup"]["phases"][7]  # type: ignore[index]
    evidence = receipt["cleanup"]["loop_release"][0]  # type: ignore[index]
    phase.update({"status": "failed", "exit_status": 1, "postcondition": False})
    evidence.update(
        {
            "detach_exit_status": 1,
            "detach_timed_out": False,
            "stderr_classification": stderr_classification,
            "stderr_size_bytes": 12,
            "stderr_sha256": "c" * 64,
            "post_detach_state": "ORIGINAL_OWNED",
            "owned_image_released": False,
            "release_probe_state": "STILL_MAPPED",
        }
    )
    receipt["cleanup"]["classification"] = "cleanup_incomplete_bounded"  # type: ignore[index]
    return receipt


@pytest.mark.parametrize(
    "stderr_classification",
    [
        "DEVICE_BUSY",
        "NO_SUCH_DEVICE",
        "INVALID_ARGUMENT_OR_OPTION",
        "PERMISSION_DENIED",
        "UNCLASSIFIED_BOUNDED",
    ],
)
def test_each_nonempty_allowlisted_loop_stderr_fixture_is_accepted(
    stderr_classification: str,
) -> None:
    validate_receipt(_failed_loop_receipt(stderr_classification))


def test_empty_allowlisted_loop_stderr_fixture_is_accepted() -> None:
    receipt = _receipt()
    evidence = receipt["cleanup"]["loop_release"][0]  # type: ignore[index]
    assert evidence["stderr_classification"] == "EMPTY"
    assert evidence["stderr_size_bytes"] == 0
    assert evidence["stderr_sha256"] == hashlib.sha256(b"").hexdigest()
    validate_receipt(receipt)


def test_loop_release_busy_reuse_and_original_backing_are_diagnostic_only() -> None:
    receipt = _failed_loop_receipt("DEVICE_BUSY")
    evidence = receipt["cleanup"]["loop_release"][0]  # type: ignore[index]
    evidence.update(
        {
            "holder_count": 2,
            "holder_identity_sha256": ["a" * 64, "b" * 64],
            "post_detach_state": "DIFFERENT_BACKING",
        }
    )
    validate_receipt(receipt)

    evidence["holder_identity_sha256"] = ["/dev/loop-test"]
    with pytest.raises(LifecycleGuardError, match="loop release evidence"):
        validate_receipt(receipt)


@pytest.mark.parametrize(
    "kind",
    [
        "already_absent",
        "timeout",
        "unsafe_probe",
        "identity_replacement",
        "release_probe_error",
        "zero_holders",
        "multiple_holders",
    ],
)
def test_loop_release_bounded_fixture_shapes_are_accepted(kind: str) -> None:
    if kind == "already_absent":
        receipt = _receipt()
        phase = receipt["cleanup"]["phases"][7]  # type: ignore[index]
        evidence = receipt["cleanup"]["loop_release"][0]  # type: ignore[index]
        phase.update({"attempted": False, "status": "skipped", "exit_status": 0})
        evidence.update(
            {
                "precheck": "ABSENT",
                "holder_probe_state": "NOT_APPLICABLE",
                "post_detach_state": "ABSENT",
            }
        )
    elif kind == "timeout":
        receipt = _failed_loop_receipt("UNCLASSIFIED_BOUNDED")
        phase = receipt["cleanup"]["phases"][7]  # type: ignore[index]
        evidence = receipt["cleanup"]["loop_release"][0]  # type: ignore[index]
        phase.update({"status": "timeout", "exit_status": 124})
        evidence.update({"detach_exit_status": 124, "detach_timed_out": True})
    elif kind == "unsafe_probe":
        receipt = _failed_loop_receipt("UNCLASSIFIED_BOUNDED")
        phase = receipt["cleanup"]["phases"][7]  # type: ignore[index]
        evidence = receipt["cleanup"]["loop_release"][0]  # type: ignore[index]
        phase.update({"attempted": False, "status": "skipped", "exit_status": 0})
        evidence.update(
            {
                "precheck": "UNSAFE",
                "holder_probe_state": "PROBE_ERROR",
                "detach_exit_status": 0,
                "stderr_classification": "EMPTY",
                "stderr_size_bytes": 0,
                "stderr_sha256": hashlib.sha256(b"").hexdigest(),
                "post_detach_state": "UNSAFE",
                "release_probe_state": "PROBE_ERROR",
            }
        )
    elif kind == "identity_replacement":
        receipt = _failed_loop_receipt("UNCLASSIFIED_BOUNDED")
        phase = receipt["cleanup"]["phases"][7]  # type: ignore[index]
        evidence = receipt["cleanup"]["loop_release"][0]  # type: ignore[index]
        phase.update({"attempted": False, "status": "skipped", "exit_status": 0})
        evidence.update(
            {
                "precheck": "IDENTITY_CHANGED",
                "detach_exit_status": 0,
                "stderr_classification": "EMPTY",
                "stderr_size_bytes": 0,
                "stderr_sha256": hashlib.sha256(b"").hexdigest(),
                "post_detach_state": "DIFFERENT_BACKING",
            }
        )
    elif kind == "release_probe_error":
        receipt = _failed_loop_receipt("DEVICE_BUSY")
        evidence = receipt["cleanup"]["loop_release"][0]  # type: ignore[index]
        evidence.update({"release_probe_state": "PROBE_ERROR"})
    else:
        receipt = _failed_loop_receipt("DEVICE_BUSY")
        evidence = receipt["cleanup"]["loop_release"][0]  # type: ignore[index]
        if kind == "multiple_holders":
            evidence.update(
                {
                    "holder_count": 2,
                    "holder_identity_sha256": ["a" * 64, "b" * 64],
                }
            )
    validate_receipt(receipt)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda evidence: evidence.update({"holder_probe_state": "OVER_LIMIT"}),
        lambda evidence: evidence.update({"holder_probe_state": "INVALID_NAME"}),
        lambda evidence: evidence.update({"holder_count": 9}),
        lambda evidence: evidence.update({"holder_identity_sha256": ["holder-name"]}),
        lambda evidence: evidence.update(
            {"post_detach_state": "ORIGINAL_OWNED", "owned_image_released": True}
        ),
        lambda evidence: evidence.update(
            {"post_detach_state": "ORIGINAL_OWNED", "release_probe_state": "RELEASED"}
        ),
    ],
)
def test_loop_release_unsafe_or_inconsistent_fixture_shapes_are_rejected(
    mutation: object,
) -> None:
    receipt = _failed_loop_receipt("DEVICE_BUSY")
    evidence = receipt["cleanup"]["loop_release"][0]  # type: ignore[index]
    mutation(evidence)  # type: ignore[operator]
    with pytest.raises(LifecycleGuardError, match="loop release evidence"):
        validate_receipt(receipt)


def _released_different_backing_receipt() -> dict[str, object]:
    receipt = _receipt()
    phase = receipt["cleanup"]["phases"][7]  # type: ignore[index]
    evidence = receipt["cleanup"]["loop_release"][0]  # type: ignore[index]
    phase["postcondition"] = True
    evidence.update(
        {
            "post_detach_state": "DIFFERENT_BACKING",
            "owned_image_released": True,
            "release_probe_state": "RELEASED",
            "holder_count": 0,
            "holder_identity_sha256": [],
            "holder_probe_state": "COMPLETE",
        }
    )
    return receipt


def test_released_different_backing_is_a_successful_loop_postcondition() -> None:
    receipt = _released_different_backing_receipt()
    evidence = receipt["cleanup"]["loop_release"][0]  # type: ignore[index]
    assert loop_release_postcondition(evidence) is True
    validate_receipt(receipt)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda evidence: evidence.update({"owned_image_released": False}),
        lambda evidence: evidence.update({"release_probe_state": "STILL_MAPPED"}),
        lambda evidence: evidence.update(
            {"holder_count": 1, "holder_identity_sha256": ["a" * 64]}
        ),
        lambda evidence: evidence.update({"holder_probe_state": "PROBE_ERROR"}),
    ],
)
def test_released_different_backing_requires_each_release_predicate(
    mutation: object,
) -> None:
    receipt = _released_different_backing_receipt()
    evidence = receipt["cleanup"]["loop_release"][0]  # type: ignore[index]
    mutation(evidence)  # type: ignore[operator]
    assert loop_release_postcondition(evidence) is False
    with pytest.raises(LifecycleGuardError, match="loop release evidence"):
        validate_receipt(receipt)


def test_released_different_backing_false_negative_is_rejected() -> None:
    receipt = _released_different_backing_receipt()
    phase = receipt["cleanup"]["phases"][7]  # type: ignore[index]
    phase["postcondition"] = False
    receipt["cleanup"]["classification"] = "cleanup_incomplete_bounded"  # type: ignore[index]
    with pytest.raises(LifecycleGuardError, match="loop release evidence"):
        validate_receipt(receipt)


def test_shell_evaluates_released_different_backing_after_release_probe() -> None:
    script = (Path(__file__).parent / "run-managed-zvol-lio-lifecycle.sh").read_text()
    cleanup = script.split("cleanup_controller() {", 1)[1].split(
        "build_cleanup_json() {", 1
    )[0]
    condition = '[[ "$post_state" == "DIFFERENT_BACKING" ]]'
    assert cleanup.count(condition) == 1
    assert cleanup.index('release_probe="RELEASED"') < cleanup.index(condition)
    assert '[[ "$released" == true ]]' in cleanup
    assert '[[ "$loop_holder_count" -eq 0 ]]' in cleanup
    assert '[[ "$loop_holder_probe_state" == "COMPLETE" ]]' in cleanup


def test_cleanup_commands_are_bounded_and_receipt_absence_fails_closed() -> None:
    repo = Path(__file__).resolve().parents[2]
    script = (repo / "tests/integration/run-managed-zvol-lio-lifecycle.sh").read_text()
    workflow = (repo / ".github/workflows/storage-integration.yml").read_text()
    assert "timeout --signal=TERM --kill-after=2s" in script
    assert script.count("ulimit -f 16") == 3
    assert "total_budget_seconds:191" in script
    fixed_phases = [
        name for name in CLEANUP_PHASES if not name.startswith("loop_detach_")
    ]
    assert all(f'"{name}"' in script for name in fixed_phases)
    assert 'record_phase "loop_detach_$number"' in script
    assert "if: always()" in workflow
    assert (
        "HOARDARR_MANAGED_ZVOL_RECEIPT: "
        "dist/validation/managed-zvol-lio-lifecycle.json" in workflow
    )
    assert "receipt_path.read_bytes()" in workflow
    assert "if-no-files-found: error" in workflow


def test_kernel_capture_is_bounded_after_the_single_login_before_sanitization() -> None:
    script = (Path(__file__).parent / "run-managed-zvol-lio-lifecycle.sh").read_text()
    login = 'iscsiadm -d 1 -m node -T "$target_iqn" -p "$portal:3260" --login'
    kernel = "journalctl -k"
    diagnostic = 'diagnostic_json="$(HOARDARR_A4_CHAP_FIXTURE='
    assert script.count("--login") == 1
    assert script.count("iscsiadm -d 1 -m node") == 1
    assert all(f"iscsiadm -d {level} -m node" not in script for level in range(2, 9))
    assert script.index(login) < script.index(kernel) < script.index(diagnostic)
    assert '--kernel "$login_kernel"' in script
    assert "--no-pager -o short-iso -n 80" in script


def test_tpg_authentication_readback_precedes_discovery_and_single_login() -> None:
    script = (Path(__file__).parent / "run-managed-zvol-lio-lifecycle.sh").read_text()
    authentication = 'tpg-authentication --target-iqn "$target_iqn"'
    discovery = 'iscsiadm -m discovery -t sendtargets -p "$portal:3260"'
    login = 'iscsiadm -d 1 -m node -T "$target_iqn" -p "$portal:3260" --login'
    assert script.count(authentication) == 1
    assert script.index(authentication) < script.index(discovery) < script.index(login)
    assert script.count("--login") == 1
    assert script.count("if ! read_tpg_authentication; then") == 1


@pytest.mark.parametrize(
    ("candidate", "status", "expected"),
    [
        ("", "1", "failure"),
        ("not-json", "0", "failure"),
        ('{"schema_version":1,"observed":true,"enabled":"true"}', "0", "failure"),
        (
            '{"schema_version":1,"observed":true,"enabled":true,"extra":"auth_material=synthetic"}',
            "0",
            "failure",
        ),
        ('{"schema_version":1,"observed":true,"enabled":false}', "0", "success"),
        ('{"schema_version":1,"observed":true,"enabled":true}', "0", "success"),
    ],
)
def test_tpg_authentication_shell_boundary_preserves_failure_receipt_sentinel(
    tmp_path: Path, candidate: str, status: str, expected: str
) -> None:
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    bash = str(git_bash) if git_bash.is_file() else shutil.which("bash")
    if bash is None or shutil.which("jq") is None:
        pytest.skip("Bash and jq are required to execute the shell boundary")
    helper = tmp_path / "fake-helper"
    helper.write_text(
        '#!/bin/sh\nprintf \'%s\' "$A8F_TEST_CANDIDATE"\nexit "$A8F_TEST_STATUS"\n',
        encoding="utf-8",
    )
    helper.chmod(0o700)
    script = (Path(__file__).parent / "run-managed-zvol-lio-lifecycle.sh").read_text()
    body = script.split("read_tpg_authentication() {", 1)[1].split(
        "\n}\n\nsafe_work_root", 1
    )[0]
    boundary = f"read_tpg_authentication() {{{body}\n}}"
    program = "\n".join(
        (
            "set -euo pipefail",
            "repo=/fixture",
            f"python='{helper.as_posix()}'",
            "target_iqn=iqn.2026-08.local.hoardarr.fixture",
            "tpg_authentication_json='{"
            + '"schema_version":1,"observed":false,"enabled":null'
            + "}'",
            boundary,
            "if read_tpg_authentication; then result=success; else result=failure; fi",
            'printf \'%s\\n%s\\n\' "$result" "$tpg_authentication_json"',
        )
    )
    environment = os.environ.copy()
    environment.update({"A8F_TEST_CANDIDATE": candidate, "A8F_TEST_STATUS": status})
    completed = subprocess.run(
        [bash, "-c", program],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    result, retained = completed.stdout.splitlines()
    assert result == expected
    if expected == "failure":
        assert retained == '{"schema_version":1,"observed":false,"enabled":null}'
        receipt = _receipt(classification="HARNESS_ERROR")
        prelogin = receipt["prelogin"]
        assert isinstance(prelogin, dict)
        prelogin["tpg_authentication"] = json.loads(retained)
        validate_receipt(receipt)
        assert not candidate or candidate not in completed.stdout
        assert "auth_material" not in completed.stdout
    else:
        assert json.loads(retained) == json.loads(candidate)


def test_loop_detach_uses_the_direct_validated_operand_once() -> None:
    script = (Path(__file__).parent / "run-managed-zvol-lio-lifecycle.sh").read_text()
    direct = 'losetup -d "$loop"'
    assert script.count(direct) == 1
    assert 'losetup -d -- "$loop"' not in script


def test_targetcli_cleanup_uses_exact_parent_child_vectors_in_order() -> None:
    script = (Path(__file__).parent / "run-managed-zvol-lio-lifecycle.sh").read_text()
    cleanup = script.split("cleanup_controller() {", 1)[1].split(
        "build_cleanup_json() {", 1
    )[0]
    node_delete = (
        'run_bounded 8 iscsiadm -m node -T "$target_iqn" -p "$portal:3260" -o delete'
    )
    target_delete = 'run_bounded 10 targetcli /iscsi delete "$target_iqn"'
    backstore_delete = 'run_bounded 10 targetcli /backstores/block delete "$backstore"'
    saveconfig = 'phase_result "saveconfig" true 10 true targetcli saveconfig'

    assert cleanup.count(target_delete) == 1
    assert cleanup.count(backstore_delete) == 1
    assert cleanup.index(node_delete) < cleanup.index(target_delete)
    assert cleanup.index(target_delete) < cleanup.index(backstore_delete)
    assert cleanup.index(backstore_delete) < cleanup.index(saveconfig)
    assert 'targetcli "/iscsi/$target_iqn" delete' not in cleanup
    assert 'targetcli "/backstores/block/$backstore" delete' not in cleanup
    assert "targetcli /iscsi delete $target_iqn" not in cleanup
    assert "targetcli /backstores/block delete $backstore" not in cleanup
    assert "clearconfig" not in cleanup
    assert "*" not in cleanup
