from __future__ import annotations

import json
from pathlib import Path

import pytest
from managed_zvol_lio_lifecycle import LifecycleGuardError, validate_guard

SAFE_ROOT = "/tmp/hoardarr-managed-zvol.fixture"
SAFE_LOOPS = [
    (f"/dev/loop{number}", f"{SAFE_ROOT}/disk{number}.img") for number in range(1, 7)
]


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


def test_synthetic_evidence_fixture_matches_bounded_schema(tmp_path: Path) -> None:
    fixture = {
        "schema_version": 1,
        "classification": "VERIFIED IN ISOLATION",
        "loop_count": 6,
        "raidz2_vdev_count": 1,
        "raidz2_member_count": 6,
        "zvol_count": 1,
        "production_executor_used": True,
        "initial_apply_active": True,
        "idempotent_apply": True,
        "state_only_recovery": True,
        "restart_restored": True,
        "remove_absent": True,
        "backing_retained": True,
        "cleanup_complete": True,
        "prohibited_action_count": 0,
    }
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["loop_count"] == 6
    assert loaded["raidz2_vdev_count"] == 1
    assert loaded["raidz2_member_count"] == 6
    assert loaded["prohibited_action_count"] == 0
    assert all(
        loaded[field] is True
        for field in (
            "production_executor_used",
            "initial_apply_active",
            "idempotent_apply",
            "state_only_recovery",
            "restart_restored",
            "remove_absent",
            "backing_retained",
            "cleanup_complete",
        )
    )
