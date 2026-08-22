from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hoardarr.storage.zfs import ZfsSnapshotError, create_and_prune_snapshots, managed_snapshots


def test_managed_snapshot_parser_ignores_user_and_other_pool_snapshots() -> None:
    output = "\n".join(
        [
            "media@manual",
            "other@hoardarr-20260101T000000Z",
            "media@hoardarr-invalid",
            "media@hoardarr-20260102T000000Z",
            "media@hoardarr-20260101T000000Z",
        ]
    )
    assert managed_snapshots("media", output) == [
        "media@hoardarr-20260101T000000Z",
        "media@hoardarr-20260102T000000Z",
    ]


def test_snapshot_job_creates_and_prunes_only_managed_snapshots() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(argv: object, _timeout: int) -> str:
        command = tuple(argv)  # type: ignore[arg-type]
        calls.append(command)
        if command[1] == "list":
            return "\n".join(
                [
                    "media@manual",
                    "media@hoardarr-20260101T000000Z",
                    "media@hoardarr-20260102T000000Z",
                    "media@hoardarr-20260103T000000Z",
                ]
            )
        return ""

    removed = create_and_prune_snapshots(
        pool="media",
        retention=2,
        now=datetime(2026, 1, 4, tzinfo=UTC),
        runner=runner,
    )
    assert calls[0] == ("zfs", "snapshot", "media@hoardarr-20260104T000000Z")
    assert removed == ["media@hoardarr-20260101T000000Z"]
    assert ("zfs", "destroy", "media@manual") not in calls


@pytest.mark.parametrize(("pool", "retention"), [("../bad", 1), ("media", 0), ("media", 4097)])
def test_snapshot_job_rejects_unsafe_input(pool: str, retention: int) -> None:
    with pytest.raises(ZfsSnapshotError):
        create_and_prune_snapshots(pool=pool, retention=retention, runner=lambda *_args: "")


def test_snapshot_job_stops_after_tool_failure() -> None:
    def failed(_argv: object, _timeout: int) -> str:
        raise ZfsSnapshotError("failed")

    with pytest.raises(ZfsSnapshotError, match="failed"):
        create_and_prune_snapshots(pool="media", retention=3, runner=failed)
