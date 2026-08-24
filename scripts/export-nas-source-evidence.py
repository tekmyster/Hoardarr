#!/usr/bin/env python3
"""Export bounded, read-only source-NAS identity evidence for Hoardarr."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

SYS_BLOCK = Path("/sys/class/block")
SYNOLOGY_VERSION = Path("/etc.defaults/VERSION")
QNAP_CONFIG = Path("/etc/config/uLinux.conf")
OS_RELEASE = Path("/etc/os-release")
MAXIMUM_TEXT_BYTES = 65_536
SKIPPED_PREFIXES = ("loop", "ram", "dm-", "md", "sr", "zram")


def read_text(path: Path, *, limit: int = 4096) -> str | None:
    try:
        value = path.read_bytes()[: min(limit, MAXIMUM_TEXT_BYTES)].decode(
            "utf-8", errors="replace"
        )
    except OSError:
        return None
    normalized = value.strip()
    return normalized if normalized else None


def config_value(path: Path, key: str) -> str | None:
    text = read_text(path, limit=MAXIMUM_TEXT_BYTES)
    if text is None:
        return None
    match = re.search(
        rf"(?mi)^\s*{re.escape(key)}\s*=\s*[\"']?([^\"'\r\n]{{1,64}})", text
    )
    return match.group(1).strip() if match else None


def platform_evidence(allow_generic: bool) -> tuple[str, str, str | None]:
    synology = SYNOLOGY_VERSION
    qnap = QNAP_CONFIG
    if synology.is_file() and not qnap.is_file():
        return "synology", "synology_runtime", config_value(synology, "productversion")
    if qnap.is_file() and not synology.is_file():
        return "qnap", "qnap_runtime", config_value(qnap, "Version")
    if synology.is_file() and qnap.is_file():
        raise SystemExit("conflicting Synology and QNAP runtime markers were found")
    if not allow_generic:
        raise SystemExit(
            "no Synology or QNAP runtime marker was found; use --allow-generic-linux only "
            "when this host is intentionally the source NAS"
        )
    return (
        "generic_linux_nas",
        "linux_runtime",
        config_value(OS_RELEASE, "VERSION_ID"),
    )


def positive_integer(path: Path) -> int | None:
    value = read_text(path, limit=64)
    return int(value) if value and value.isdigit() and int(value) > 0 else None


def member_document(path: Path) -> dict[str, object] | None:
    name = path.name
    if name.startswith(SKIPPED_PREFIXES) or (path / "partition").exists():
        return None
    serial = read_text(path / "device" / "serial", limit=256)
    if serial is None:
        return None
    sectors = positive_integer(path / "size")
    logical = positive_integer(path / "queue" / "logical_block_size")
    capacity = sectors * logical if sectors is not None and logical is not None else None
    wwn = read_text(path / "device" / "wwid", limit=256) or read_text(
        path / "wwid", limit=256
    )
    return {
        "member": name,
        "serial": serial,
        "wwn": wwn,
        "eui64": read_text(path / "eui", limit=256),
        "nguid": read_text(path / "nguid", limit=256),
        "capacity_bytes": capacity,
    }


def build_document(*, allow_generic: bool) -> dict[str, object]:
    platform, marker, version = platform_evidence(allow_generic)
    members = [
        document
        for path in sorted(SYS_BLOCK.iterdir(), key=lambda item: item.name)
        if (document := member_document(path)) is not None
    ]
    if not members:
        raise SystemExit("no whole storage device with a reported serial was found")
    identities = [
        str(item.get("wwn") or item.get("eui64") or item.get("nguid") or item["serial"])
        .strip()
        .casefold()
        for item in members
    ]
    if len(identities) != len(set(identities)):
        raise SystemExit("source storage identities are ambiguous")
    return {
        "schema_version": 1,
        "source": "nas_runtime_state",
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "platform": platform,
        "platform_marker": marker,
        "product_version": version,
        "members": members,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read source-NAS runtime markers and stable disk identities without mounting, "
            "assembling, or modifying storage."
        )
    )
    parser.add_argument(
        "--allow-generic-linux",
        action="store_true",
        help="identify this intentionally selected source host as a generic Linux NAS",
    )
    parser.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    args = parser.parse_args()
    document = json.dumps(
        build_document(allow_generic=args.allow_generic), indent=2, sort_keys=True
    ) + "\n"
    if args.output is None:
        sys.stdout.write(document)
    else:
        args.output.write_text(document, encoding="utf-8")


if __name__ == "__main__":
    main()
