from __future__ import annotations

import hashlib
import json
import os
import stat
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
    _read_safe_diagnostic,
    atomic_write_receipt,
    inspect_node_parity,
    loop_release_postcondition,
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
