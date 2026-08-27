from __future__ import annotations

import json
import os
from pathlib import Path

import managed_zvol_lio_lifecycle as lifecycle
import pytest
from managed_zvol_lio_lifecycle import (
    CLEANUP_PHASES,
    CLEANUP_TIMEOUTS,
    DIAGNOSTIC_LIMIT,
    DiagnosticError,
    LifecycleGuardError,
    NodeParityError,
    atomic_write_receipt,
    inspect_node_parity,
    sanitize_diagnostic_bytes,
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
        "login": {
            "attempt_count": 1,
            "status": 0 if classification == "LOGIN_SUCCEEDED_LIFECYCLE_RESULT" else 19,
            "succeeded": classification == "LOGIN_SUCCEEDED_LIFECYCLE_RESULT",
            "diagnostic": {
                "schema_version": 1,
                "status": 0
                if classification == "LOGIN_SUCCEEDED_LIFECYCLE_RESULT"
                else 19,
                "streams": [
                    {
                        "label": label,
                        "size_bytes": 0,
                        "sha256": "0" * 64,
                        "classifications": [],
                    }
                    for label in ("stdout", "stderr", "iscsid_target")
                ],
                "ordered_classifications": [],
                "diagnosed_class": None,
            },
        },
        "cleanup": {
            "classification": "cleanup_complete",
            "total_budget_seconds": 191,
            "phases": phases,
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
    elif classification == "LOGIN_FAILURE_DIAGNOSED":
        streams = diagnostic["streams"]
        assert isinstance(streams, list) and isinstance(streams[1], dict)
        streams[1]["classifications"] = ["credential_rejection"]
        diagnostic.update(
            {
                "ordered_classifications": ["credential_rejection"],
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
    return receipt


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


def test_receipt_is_atomic_and_schema_bounded(tmp_path: Path) -> None:
    receipt = _receipt()
    output = tmp_path / "receipt.json"
    atomic_write_receipt(receipt, output)
    if os.name != "nt":
        assert output.stat().st_mode & 0o777 == 0o600
    assert validate_receipt(json.loads(output.read_text(encoding="utf-8"))) == receipt


@pytest.mark.parametrize(
    "classification",
    [
        "PARITY_MISMATCH_IDENTIFIED",
        "LOGIN_FAILURE_DIAGNOSED",
        "LOGIN_FAILURE_UNRESOLVED",
        "LOGIN_SUCCEEDED_LIFECYCLE_RESULT",
        "HARNESS_ERROR",
    ],
)
def test_all_five_receipt_classifications_are_bounded(classification: str) -> None:
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


def test_cleanup_commands_are_bounded_and_receipt_absence_fails_closed() -> None:
    repo = Path(__file__).resolve().parents[2]
    script = (repo / "tests/integration/run-managed-zvol-lio-lifecycle.sh").read_text()
    workflow = (repo / ".github/workflows/storage-integration.yml").read_text()
    assert "timeout --signal=TERM --kill-after=2s" in script
    assert script.count("ulimit -f 16") == 2
    assert "total_budget_seconds:191" in script
    fixed_phases = [
        name for name in CLEANUP_PHASES if not name.startswith("loop_detach_")
    ]
    assert all(f'"{name}"' in script for name in fixed_phases)
    assert 'record_phase "loop_detach_$number"' in script
    assert "if: always()" in workflow
    assert "test -f dist/validation/managed-zvol-lio-lifecycle.json" in workflow
    assert "if-no-files-found: error" in workflow
