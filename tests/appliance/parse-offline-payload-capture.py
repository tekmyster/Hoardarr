#!/usr/bin/env python3
"""Validate and retain the CI-only offline-payload serial capture."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

BEGIN = b"HOARDARR_OFFLINE_PAYLOAD_BEGIN\n"
END = b"HOARDARR_OFFLINE_PAYLOAD_END\n"
EXIT_PREFIX = b"HOARDARR_OFFLINE_PAYLOAD_EXIT="
SIZE_PREFIX = b"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE="
SHA_PREFIX = b"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SHA256="
COMPLETE = b"HOARDARR_OFFLINE_PAYLOAD_CAPTURE_COMPLETE\n"

EXIT_COMPLETE_ZERO = 0
EXIT_COMPLETE_NONZERO = 10
EXIT_INCOMPLETE = 20
EXIT_MALFORMED = 21


class CaptureIncomplete(ValueError):
    """The serial stream does not yet contain a complete capture."""


class CaptureMalformed(ValueError):
    """The serial stream contains a complete but invalid capture."""


@dataclass(frozen=True)
class Capture:
    payload_status: int
    target_log: bytes
    console_log: bytes
    target_log_sha256: str
    serial_transform: str


def _one_index(lines: list[bytes], expected: bytes, label: str) -> int:
    matches = [index for index, line in enumerate(lines) if line == expected]
    if len(matches) != 1:
        raise CaptureMalformed(f"expected exactly one {label} marker")
    return matches[0]


def _one_value(
    lines: list[bytes], prefix: bytes, pattern: bytes, label: str
) -> tuple[int, bytes]:
    matches = [
        (index, line.removeprefix(prefix).rstrip(b"\r\n"))
        for index, line in enumerate(lines)
        if line.startswith(prefix)
    ]
    if len(matches) != 1 or re.fullmatch(pattern, matches[0][1]) is None:
        raise CaptureMalformed(f"expected exactly one valid {label} marker")
    return matches[0]


def _parse_lf_capture(serial: bytes, *, serial_transform: str) -> Capture:
    lines = serial.splitlines(keepends=True)
    if COMPLETE not in lines:
        if sum(line == BEGIN for line in lines) > 1:
            raise CaptureMalformed("duplicate begin marker before completion")
        raise CaptureIncomplete("capture-complete marker is absent")

    begin = _one_index(lines, BEGIN, "begin")
    end = _one_index(lines, END, "end")
    exit_index, exit_value = _one_value(
        lines, EXIT_PREFIX, rb"(?:0|[1-9][0-9]{0,2})", "exit"
    )
    size_index, size_value = _one_value(
        lines, SIZE_PREFIX, rb"(?:0|[1-9][0-9]*)", "target-log size"
    )
    sha_index, sha_value = _one_value(
        lines, SHA_PREFIX, rb"[0-9a-f]{64}", "target-log SHA-256"
    )
    complete = _one_index(lines, COMPLETE, "capture-complete")
    if not (begin < end < exit_index < size_index < sha_index < complete):
        raise CaptureMalformed("capture markers are out of order")

    status = int(exit_value)
    if status > 255:
        raise CaptureMalformed("payload exit status is outside 0..255")
    target_log = b"".join(lines[begin : exit_index + 1])
    actual_sha256 = hashlib.sha256(target_log).hexdigest()
    if len(target_log) != int(size_value):
        raise CaptureMalformed("target-log size does not match reconstructed bytes")
    if actual_sha256.encode("ascii") != sha_value:
        raise CaptureMalformed("target-log SHA-256 does not match reconstructed bytes")
    return Capture(
        payload_status=status,
        target_log=target_log,
        console_log=b"".join(lines[begin : complete + 1]),
        target_log_sha256=actual_sha256,
        serial_transform=serial_transform,
    )


def parse_capture(serial: bytes) -> Capture:
    """Parse an exact LF stream or reverse only the terminal ONLCR transform."""
    crlf_begin = BEGIN.removesuffix(b"\n") + b"\r\n"
    lf_begin_count = serial.count(BEGIN)
    crlf_begin_count = serial.count(crlf_begin)
    if lf_begin_count and crlf_begin_count:
        raise CaptureMalformed("capture mixes LF and CRLF marker framing")
    if crlf_begin_count:
        # ONLCR maps each LF to CRLF. Reversing only that byte pair also
        # reconstructs an original CRLF correctly: CRCRLF becomes CRLF.
        return _parse_lf_capture(
            serial.replace(b"\r\n", b"\n"), serial_transform="onlcr_crlf"
        )
    return _parse_lf_capture(serial, serial_transform="none")


def _remove_outputs(paths: tuple[Path, ...]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("serial", type=Path)
    parser.add_argument("console_log", type=Path)
    parser.add_argument("target_log", type=Path)
    parser.add_argument("metadata", type=Path)
    args = parser.parse_args(argv)
    outputs = (args.console_log, args.target_log, args.metadata)
    try:
        capture = parse_capture(args.serial.read_bytes())
    except CaptureIncomplete as exc:
        _remove_outputs(outputs)
        print(str(exc), file=sys.stderr)
        return EXIT_INCOMPLETE
    except (CaptureMalformed, OSError) as exc:
        _remove_outputs(outputs)
        print(str(exc), file=sys.stderr)
        return EXIT_MALFORMED

    args.console_log.write_bytes(capture.console_log)
    args.target_log.write_bytes(capture.target_log)
    args.metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "payload_status": capture.payload_status,
                "target_log_size": len(capture.target_log),
                "target_log_sha256": capture.target_log_sha256,
                "console_log_size": len(capture.console_log),
                "console_log_sha256": hashlib.sha256(capture.console_log).hexdigest(),
                "serial_transform": capture.serial_transform,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return EXIT_COMPLETE_ZERO if capture.payload_status == 0 else EXIT_COMPLETE_NONZERO


if __name__ == "__main__":
    raise SystemExit(main())
