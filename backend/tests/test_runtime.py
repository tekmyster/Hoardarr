from __future__ import annotations

import sys

import pytest

from hoardarr import runtime


def test_runtime_dispatches_closed_command_and_preserves_remaining_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[list[str]] = []
    monkeypatch.setattr(
        runtime, "_commands", lambda: {"worker": lambda: observed.append(sys.argv[:])}
    )
    monkeypatch.setattr(sys, "argv", ["hoardarr-runtime", "worker", "--once"])

    runtime.main()

    assert observed == [["hoardarr-runtime", "--once"]]


def test_runtime_rejects_unknown_command_before_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_commands", lambda: {})
    monkeypatch.setattr(sys, "argv", ["hoardarr-runtime", "arbitrary-shell"])

    with pytest.raises(SystemExit, match="unknown Hoardarr runtime command"):
        runtime.main()
