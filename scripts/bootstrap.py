#!/usr/bin/env python3
"""Install and validate a Hoardarr build host or storage appliance.

The program is deliberately conservative: planning is read-only, runtime profiles
require an explicit acknowledgement, package installation cannot start services,
and third-party controller utilities must be pinned in the repository catalog.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import datetime as dt
import hashlib
import hmac
import json
import os
import pathlib
import platform
import re
import shlex
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from typing import Any, Iterable, Iterator, Mapping, Sequence


SCHEMA_VERSION = 1
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "packaging" / "packages"
HARDWARE_DETECTOR = REPO_ROOT / "scripts" / "detect-hardware.py"
VENDOR_CATALOG = REPO_ROOT / "packaging" / "hardware" / "vendor-tools.json"
TOOLCHAIN_ROOT = pathlib.Path("/opt/hoardarr/toolchains")
TOOLCHAIN_BIN = TOOLCHAIN_ROOT / "bin"
DOWNLOAD_CACHE = pathlib.Path("/var/cache/hoardarr/downloads")
PROFILE_FILE = pathlib.Path("/etc/profile.d/hoardarr-build-tools.sh")
LOCK_FILE = pathlib.Path("/var/lock/hoardarr-bootstrap.lock")
STATE_ROOT = pathlib.Path("/var/lib/hoardarr-bootstrap")
VENDOR_STATE_ROOT = STATE_ROOT / "vendor"
RUNTIME_BASELINE = STATE_ROOT / "runtime-unit-baseline.json"
SYSTEM_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
POLICY_PATH = pathlib.Path("/usr/sbin/policy-rc.d")
POLICY_STATE = STATE_ROOT / "policy-rc.d-state.json"
POLICY_BACKUP = STATE_ROOT / "policy-rc.d-original"
POLICY_GUARD = b"#!/bin/sh\n# Temporary Hoardarr bootstrap guard.\nexit 101\n"
SYSV_INIT_DIR = pathlib.Path("/etc/init.d")
SYSV_BOOT_DIRS = tuple(pathlib.Path(f"/etc/rc{level}.d") for level in ("S", "2", "3", "4", "5"))

PROFILE_MANIFESTS = {
    "build-host": "build-host.txt",
    "appliance-core": "appliance-core.txt",
    "storage-protocols": "storage-services.txt",
    "tiered-storage": "tiered-storage.txt",
    "advanced-cluster": "advanced-ha.txt",
    "advanced-fcoe": "advanced-fcoe.txt",
}
BUILD_BOOT_PACKAGES = {
    "amd64": (
        "grub-efi-amd64-bin",
        "grub-efi-amd64-signed",
        "grub-pc-bin",
        "isolinux",
        "shim-signed",
        "syslinux-common",
    ),
    "arm64": ("grub-efi-arm64-bin", "grub-efi-arm64-signed", "shim-signed"),
}
RUNTIME_PROFILES = frozenset(PROFILE_MANIFESTS) - {"build-host"}
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]*(?::[a-z0-9][a-z0-9-]*)?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")

# These are observed only. The installer never enables or starts one of them.
RUNTIME_UNITS = (
    "corosync.service",
    "ctdb.service",
    "fcoe.service",
    "fcoe-utils.service",
    "fcoemon.socket",
    "iscsid.service",
    "iscsid.socket",
    "iscsi.service",
    "ledmon.service",
    "lldpad.service",
    "lldpd.service",
    "lvm2-lvmpolld.socket",
    "lvm2-lvmpolld.service",
    "lvm2-monitor.service",
    "dm-event.socket",
    "blk-availability.service",
    "mdmonitor.service",
    "mdmonitor-oneshot.service",
    "mdmonitor-oneshot.timer",
    "mdadm-shutdown.service",
    "mdcheck_continue.service",
    "mdcheck_continue.timer",
    "mdcheck_start.service",
    "mdcheck_start.timer",
    "multipathd.service",
    "multipathd.socket",
    "nfs-server.service",
    "nfs-blkmap.service",
    "nfs-idmapd.service",
    "nfs-mountd.service",
    "nfsdcld.service",
    "nmbd.service",
    "open-iscsi.service",
    "openipmi.service",
    "pacemaker.service",
    "rpcbind.service",
    "rpcbind.socket",
    "rpc-gssd.service",
    "rpc-svcgssd.service",
    "rpc-statd.service",
    "rpc-statd-notify.service",
    "rtslib-fb-targetctl.service",
    "smartmontools.service",
    "smbd.service",
    "samba.service",
    "samba-ad-dc.service",
    "target.service",
    "quotaon.service",
    "quotarpc.service",
    "winbind.service",
    "watchdog.service",
    "rsync.service",
    "rauc.service",
    "fstrim.service",
    "fstrim.timer",
    "uuidd.service",
    "uuidd.socket",
    "e2scrub_all.service",
    "e2scrub_all.timer",
    "e2scrub_reap.service",
    "xfs_scrub_all.service",
    "xfs_scrub_all.timer",
    "zfs-import-cache.service",
    "zfs-import-scan.service",
    "zfs-mount.service",
    "zfs-share.service",
    "zfs-zed.service",
    "zfs-volume-wait.service",
    "zfs-import.target",
    "zfs-volumes.target",
    "zfs.target",
)

PROFILE_COMMANDS = {
    "build-host": (
        "ansible",
        "autopkgtest",
        "debuild",
        "dpkg-buildpackage",
        "lb",
        "lintian",
        "meson",
        "ninja",
        "qemu-img",
        "qemu-system-x86_64",
        "rauc",
        "reprepro",
        "sbsign",
        "shellcheck",
        "xorriso",
    ),
    "appliance-core": (
        "badblocks",
        "blkid",
        "blockdev",
        "btrfs",
        "cryptsetup",
        "dcbtool",
        "f3probe",
        "f3read",
        "f3write",
        "fio",
        "fcoeadm",
        "fcoemon",
        "fipvlan",
        "findmnt",
        "hdparm",
        "ip",
        "ledctl",
        "lldpcli",
        "lldptool",
        "lsblk",
        "lsscsi",
        "lsusb",
        "mdadm",
        "mergerfs",
        "multipath",
        "modinfo",
        "netplan",
        "nft",
        "nvme",
        "pdbedit",
        "rsyslogd",
        "sdparm",
        "sg_inq",
        "sg_logs",
        "sg_readcap",
        "sg_ses",
        "smartctl",
        "smartd",
        "snmpd",
        "smbpasswd",
        "snapraid",
        "timedatectl",
        "unshare",
        "wipefs",
        "xfs_repair",
        "zdb",
        "zfs",
        "zpool",
    ),
    "storage-protocols": ("exportfs", "iscsiadm", "smbd", "targetcli"),
    "tiered-storage": (
        "bcache-super-show",
        "dmsetup",
        "inotifywait",
        "lsof",
        "rclone",
        "rsync",
        "thin_check",
    ),
    "advanced-cluster": ("corosync", "crm", "ctdb", "stonith_admin"),
    "advanced-fcoe": ("fcoeadm", "lldptool"),
}

# Ubuntu 24.04 package ownership hints for commands the guided storage wizard
# depends on directly.  Keeping these beside the executable validation makes a
# missing-command report actionable without authorizing any storage operation.
COMMAND_PACKAGE_HINTS = {
    "badblocks": "e2fsprogs",
    "blkid": "util-linux",
    "cryptsetup": "cryptsetup",
    "f3probe": "f3",
    "f3read": "f3",
    "f3write": "f3",
    "fio": "fio",
    "findmnt": "util-linux",
    "hdparm": "hdparm",
    "lsblk": "util-linux",
    "lsusb": "usbutils",
    "mergerfs": "mergerfs",
    "pdbedit": "samba",
    "sdparm": "sdparm",
    "sg_logs": "sg3-utils",
    "sg_ses": "sg3-utils",
    "smartctl": "smartmontools",
    "smbpasswd": "samba",
    "wipefs": "util-linux",
    "zdb": "zfsutils-linux",
    "zfs": "zfsutils-linux",
    "zpool": "zfsutils-linux",
}


class BootstrapError(RuntimeError):
    """A diagnosed, user-actionable bootstrap failure."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def eprint(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def command_display(argv: Sequence[os.PathLike[str] | str]) -> str:
    return shlex.join([os.fspath(value) for value in argv])


def base_environment() -> dict[str, str]:
    environment = {
        "PATH": SYSTEM_PATH,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PAGER": "cat",
        "SYSTEMD_PAGER": "cat",
        "SYSTEMD_COLORS": "0",
    }
    if os.name == "posix":
        # Do not inherit a caller-controlled HOME or point unrelated users at a
        # shared temporary directory.  passwd is the authority for this uid.
        import pwd

        try:
            passwd_home = pwd.getpwuid(os.geteuid()).pw_dir
        except (KeyError, OSError):
            passwd_home = ""
        if passwd_home and pathlib.PurePosixPath(passwd_home).is_absolute():
            environment["HOME"] = passwd_home
    else:
        # Apply is Linux-only.  Keep diagnostics on other platforms detached
        # from a caller-supplied HOME as well.
        environment["HOME"] = pathlib.Path(sys.executable).anchor or os.path.sep
    for name in ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        value = os.environ.get(name)
        if value and "\r" not in value and "\n" not in value and len(value) <= 2000:
            environment[name] = value
    return environment


def run_command(
    argv: Sequence[os.PathLike[str] | str],
    *,
    check: bool = True,
    env: Mapping[str, str] | None = None,
    timeout: int = 900,
) -> subprocess.CompletedProcess[str]:
    command = [os.fspath(value) for value in argv]
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env) if env is not None else base_environment(),
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise BootstrapError(f"Command not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BootstrapError(
            f"Command timed out after {timeout}s: {command_display(command)}"
        ) from exc
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        if len(detail) > 3000:
            detail = detail[-3000:]
        raise BootstrapError(
            f"Command failed ({completed.returncode}): {command_display(command)}"
            + (f"\n{detail}" if detail else "")
        )
    return completed


def read_os_release() -> dict[str, str]:
    result: dict[str, str] = {}
    path = pathlib.Path("/etc/os-release")
    if not path.is_file():
        return result
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw or raw.lstrip().startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        result[key] = value.strip().strip('"')
    return result


def normalized_arch(machine: str | None = None) -> str:
    value = (machine or platform.machine()).lower()
    mapping = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    if value not in mapping:
        raise BootstrapError(f"Unsupported architecture: {value}")
    return mapping[value]


def parse_versions(path: pathlib.Path) -> dict[str, str]:
    if not path.is_file():
        raise BootstrapError(f"Missing pinned-version file: {path}")
    values: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise BootstrapError(f"Malformed {path}:{line_number}")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or not value:
            raise BootstrapError(f"Malformed {path}:{line_number}")
        if key in values:
            raise BootstrapError(f"Duplicate version key {key} in {path}")
        values[key] = value
    required = {
        "NODE_VERSION",
        "NODE_SHA256_AMD64",
        "NODE_SHA256_ARM64",
        "COREPACK_VERSION",
        "COREPACK_INTEGRITY",
        "PNPM_VERSION",
        "PNPM_INTEGRITY",
        "UV_VERSION",
        "UV_SHA256_AMD64",
        "UV_SHA256_ARM64",
    }
    missing = sorted(required - values.keys())
    if missing:
        raise BootstrapError(f"Missing pinned-version keys: {', '.join(missing)}")
    for key in ("NODE_VERSION", "COREPACK_VERSION", "PNPM_VERSION", "UV_VERSION"):
        if not VERSION_RE.fullmatch(values[key]):
            raise BootstrapError(f"{key} is not a safe semantic version")
    for key in ("NODE_SHA256_AMD64", "NODE_SHA256_ARM64", "UV_SHA256_AMD64", "UV_SHA256_ARM64"):
        if not SHA256_RE.fullmatch(values[key]):
            raise BootstrapError(f"{key} is not a lowercase SHA-256 digest")
    for key in ("COREPACK_INTEGRITY", "PNPM_INTEGRITY"):
        verify_integrity_syntax(values[key], key)
    return values


def verify_integrity_syntax(value: str, label: str) -> tuple[str, bytes]:
    try:
        algorithm, encoded = value.split("-", 1)
        expected = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise BootstrapError(f"{label} is not valid Subresource Integrity syntax") from exc
    if algorithm not in {"sha256", "sha384", "sha512"}:
        raise BootstrapError(f"Unsupported integrity algorithm for {label}: {algorithm}")
    expected_length = {"sha256": 32, "sha384": 48, "sha512": 64}[algorithm]
    if len(expected) != expected_length:
        raise BootstrapError(f"Incorrect digest length for {label}")
    return algorithm, expected


def load_manifest(path: pathlib.Path) -> list[str]:
    if not path.is_file():
        raise BootstrapError(f"Missing package manifest: {path}")
    packages: list[str] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = raw.split("#", 1)[0].strip()
        if not value:
            continue
        if not PACKAGE_RE.fullmatch(value):
            raise BootstrapError(f"Unsafe package name at {path}:{line_number}: {value!r}")
        if value in seen:
            raise BootstrapError(f"Duplicate package at {path}:{line_number}: {value}")
        seen.add(value)
        packages.append(value)
    if not packages:
        raise BootstrapError(f"Empty package manifest: {path}")
    return packages


def expand_profiles(requested: Iterable[str]) -> list[str]:
    values = list(requested) or ["build-host"]
    if "all" in values:
        values = list(PROFILE_MANIFESTS)
    unknown = sorted(set(values) - PROFILE_MANIFESTS.keys())
    if unknown:
        raise BootstrapError(f"Unknown profile(s): {', '.join(unknown)}")
    return [name for name in PROFILE_MANIFESTS if name in values]


def collect_profile_packages(profiles: Sequence[str]) -> tuple[dict[str, list[str]], list[str]]:
    by_profile: dict[str, list[str]] = {}
    combined: set[str] = set()
    for profile in profiles:
        values = load_manifest(PACKAGE_ROOT / PROFILE_MANIFESTS[profile])
        by_profile[profile] = values
        combined.update(values)
    return by_profile, sorted(combined)


def preflight(*, apply: bool, runtime: bool, network: bool, minimum_free_gib: int) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            raise BootstrapError(f"Preflight failed: {name}: {detail}")

    release = read_os_release()
    record(
        "operating-system",
        release.get("ID") == "ubuntu" and release.get("VERSION_ID") == "24.04",
        f"expected Ubuntu 24.04; found {release.get('PRETTY_NAME', 'unknown')}",
    )
    arch = normalized_arch()
    record("architecture", arch in {"amd64", "arm64"}, arch)
    record("systemd", pathlib.Path("/run/systemd/system").is_dir(), "/run/systemd/system")
    if runtime:
        osrelease = pathlib.Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8").strip()
        wsl = "microsoft" in osrelease.lower() or bool(os.environ.get("WSL_INTEROP"))
        record("not-wsl", not wsl, osrelease)
        detect_virt = shutil.which("systemd-detect-virt", path=SYSTEM_PATH)
        record("systemd-detect-virt", detect_virt is not None, detect_virt or "missing")
        container = run_command(["systemd-detect-virt", "--container"], check=False, timeout=30)
        container_name = container.stdout.strip()
        record(
            "not-container",
            container.returncode != 0,
            container_name or "full machine or virtual machine",
        )
    if apply:
        record("root", os.geteuid() == 0, f"effective uid {os.geteuid()}")
    required_commands = ("apt-cache", "apt-get", "dpkg", "dpkg-query", "systemctl")
    missing_commands = [name for name in required_commands if shutil.which(name, path=SYSTEM_PATH) is None]
    record("base-commands", not missing_commands, ", ".join(missing_commands) or "present")
    usage = shutil.disk_usage("/")
    free_gib = usage.free / (1024**3)
    record("free-space", free_gib >= minimum_free_gib, f"{free_gib:.1f} GiB free")
    current_year = dt.datetime.now(dt.timezone.utc).year
    record("clock", current_year >= 2025, f"UTC year {current_year}")

    audit = run_command(["dpkg", "--audit"], check=False, timeout=60)
    audit_detail = (audit.stdout + audit.stderr).strip()
    record("dpkg-audit", audit.returncode == 0 and not audit_detail, audit_detail or "clean")
    apt_check = run_command(["apt-get", "--simulate", "check"], check=False, timeout=180)
    record(
        "apt-dependencies",
        apt_check.returncode == 0,
        (apt_check.stderr or apt_check.stdout).strip()[-1000:] or "clean",
    )

    if network:
        try:
            addresses = socket.getaddrinfo("archive.ubuntu.com", 443, type=socket.SOCK_STREAM)
            record("dns", bool(addresses), f"{len(addresses)} address records")
        except OSError as exc:
            record("dns", False, str(exc))
        try:
            request = urllib.request.Request(
                "https://archive.ubuntu.com/ubuntu/dists/noble/InRelease",
                method="HEAD",
                headers={"User-Agent": "Hoardarr-bootstrap/1"},
            )
            with urllib.request.urlopen(request, timeout=15, context=ssl.create_default_context()) as response:
                record("https", 200 <= response.status < 400, f"HTTP {response.status}")
        except Exception as exc:  # urllib exposes several unrelated exception types
            record("https", False, str(exc))

    return {
        "release": release,
        "architecture": arch,
        "kernel": platform.release(),
        "free_gib": round(free_gib, 2),
        "checks": checks,
    }


def detect_hardware(fixture: pathlib.Path | None) -> dict[str, Any]:
    if not HARDWARE_DETECTOR.is_file():
        raise BootstrapError(f"Hardware detector is missing: {HARDWARE_DETECTOR}")
    command: list[os.PathLike[str] | str] = [sys.executable, HARDWARE_DETECTOR, "--format", "json"]
    if fixture is not None:
        command.extend(["--fixture", fixture])
    result = run_command(command, timeout=120)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BootstrapError(f"Hardware detector did not return valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise BootstrapError("Hardware detector JSON must be an object")
    recommendations = payload.get("recommendations", {})
    if not isinstance(recommendations, dict):
        raise BootstrapError("Hardware detector recommendations must be an object")
    packages = recommendations.get("packages", [])
    if not isinstance(packages, list) or any(not isinstance(item, str) for item in packages):
        raise BootstrapError("Hardware package recommendations must be a list of strings")
    unsafe = [item for item in packages if not PACKAGE_RE.fullmatch(item)]
    if unsafe:
        raise BootstrapError(f"Hardware detector returned unsafe package names: {unsafe!r}")
    recommendations["packages"] = sorted(set(packages))
    payload["recommendations"] = recommendations
    return payload


def installed_packages(packages: Sequence[str]) -> dict[str, str]:
    if not packages:
        return {}
    command = ["dpkg-query", "-W", "-f=${binary:Package}\t${db:Status-Abbrev}\t${Version}\\n", *packages]
    result = run_command(command, check=False, timeout=120)
    installed: dict[str, str] = {}
    for raw in result.stdout.splitlines():
        fields = raw.split("\t")
        if len(fields) != 3 or not fields[1].startswith("ii"):
            continue
        package = fields[0]
        installed[package] = fields[2]
        installed.setdefault(package.split(":", 1)[0], fields[2])
    return installed


def all_installed_packages() -> dict[str, str]:
    result = run_command(
        ["dpkg-query", "-W", "-f=${binary:Package}\t${db:Status-Abbrev}\t${Version}\\n"],
        check=False,
        timeout=180,
    )
    installed: dict[str, str] = {}
    for raw in result.stdout.splitlines():
        fields = raw.split("\t")
        if len(fields) == 3 and fields[1].startswith("ii"):
            installed[fields[0]] = fields[2]
    return installed


def held_packages() -> list[str]:
    result = run_command(["apt-mark", "showhold"], check=False, timeout=60)
    if result.returncode != 0:
        raise BootstrapError(f"Could not inspect APT holds: {(result.stderr or result.stdout).strip()}")
    return sorted({line.strip() for line in result.stdout.splitlines() if line.strip()})


def compare_debian_versions(left: str, operator: str, right: str) -> bool:
    if operator not in {"lt", "eq", "gt"}:
        raise BootstrapError(f"Unsupported Debian version comparison: {operator}")
    result = run_command(
        ["dpkg", "--compare-versions", left, operator, right], check=False, timeout=30
    )
    return result.returncode == 0


def reconcile_package_versions(
    packages: Sequence[str],
    installed: Mapping[str, str],
    candidates: Mapping[str, str | None],
    *,
    comparator: Any = compare_debian_versions,
) -> dict[str, Any]:
    result: dict[str, list[Any]] = {
        "missing": [],
        "outdated": [],
        "current": [],
        "ahead_of_candidate": [],
        "unresolved_missing": [],
        "installed_without_candidate": [],
    }
    for package in packages:
        installed_version = installed.get(package)
        candidate = candidates.get(package)
        if installed_version is None:
            result["missing"].append(package)
            if candidate is None:
                result["unresolved_missing"].append(package)
            continue
        if candidate is None:
            result["installed_without_candidate"].append(
                {"package": package, "installed": installed_version}
            )
        elif comparator(installed_version, "lt", candidate):
            result["outdated"].append(
                {"package": package, "installed": installed_version, "candidate": candidate}
            )
        elif comparator(installed_version, "gt", candidate):
            result["ahead_of_candidate"].append(
                {"package": package, "installed": installed_version, "candidate": candidate}
            )
        else:
            result["current"].append(package)
    result["transaction_packages"] = sorted(
        set(result["missing"])
        | {item["package"] for item in result["outdated"]}
    )
    return result


def blocking_held_packages(
    reconciliation: Mapping[str, Any], held: Sequence[str]
) -> list[str]:
    transaction = reconciliation.get("transaction_packages", [])
    if not isinstance(transaction, list):
        raise BootstrapError("Package reconciliation has an invalid transaction list")
    return sorted(set(held) & set(transaction))


def package_systemd_units(packages: Sequence[str]) -> list[str]:
    """List native and generated SysV units owned by installed packages."""
    suffixes = (".service", ".socket", ".timer", ".target", ".path", ".mount", ".automount")
    units: set[str] = set()
    for package in packages:
        result = run_command(["dpkg-query", "-L", package], check=False, timeout=60)
        if result.returncode != 0:
            continue
        for raw in result.stdout.splitlines():
            normalized = raw.strip()
            if normalized.startswith("/etc/init.d/"):
                init_name = pathlib.PurePosixPath(normalized).name
                if init_name and init_name not in {"README", "skeleton"}:
                    units.add(f"{init_name}.service")
                continue
            if not normalized.startswith(("/lib/systemd/system/", "/usr/lib/systemd/system/")):
                continue
            path = pathlib.PurePosixPath(normalized)
            value = path.name
            if value.endswith(suffixes):
                units.add(value)
    return sorted(units)


def capture_preexisting_package_units(
    before: dict[str, dict[str, str]], packages: Sequence[str]
) -> list[str]:
    discovered = set(package_systemd_units(packages))
    new_names = sorted(discovered - before.keys())
    before.update(unit_state(new_names))
    return new_names


def seed_post_transaction_units(
    before: dict[str, dict[str, str]], packages: Sequence[str]
) -> list[str]:
    discovered = set(package_systemd_units(packages))
    new_names = sorted(discovered - before.keys())
    for unit in new_names:
        before[unit] = {"active": "not-found", "enabled": "not-found"}
    return new_names


def installed_package_delta(
    before: Mapping[str, str], after: Mapping[str, str]
) -> dict[str, Any]:
    return {
        "added": [
            {"package": package, "version": after[package]}
            for package in sorted(after.keys() - before.keys())
        ],
        "changed": [
            {"package": package, "before": before[package], "after": after[package]}
            for package in sorted(before.keys() & after.keys())
            if before[package] != after[package]
        ],
        "removed": [
            {"package": package, "version": before[package]}
            for package in sorted(before.keys() - after.keys())
        ],
    }


def unexpected_dpkg_names(
    delta: Mapping[str, Any], simulated_install_names: Sequence[str]
) -> list[str]:
    native_suffix = f":{normalized_arch()}"

    def native_identity(package: str) -> str:
        return package[: -len(native_suffix)] if package.endswith(native_suffix) else package

    expected = {native_identity(name) for name in simulated_install_names}
    actual = {
        str(item["package"])
        for category in ("added", "changed")
        for item in delta.get(category, [])
        if isinstance(item, Mapping) and isinstance(item.get("package"), str)
    }
    return sorted(name for name in actual if native_identity(name) not in expected)


def apt_candidates(packages: Sequence[str]) -> dict[str, str | None]:
    if not packages:
        return {}
    result = run_command(["apt-cache", "policy", *packages], check=False, timeout=180)
    candidates: dict[str, str | None] = {name: None for name in packages}
    current: str | None = None
    for raw in result.stdout.splitlines():
        if raw and not raw[0].isspace() and raw.endswith(":"):
            current = raw[:-1]
            continue
        match = re.match(r"\s+Candidate:\s+(\S+)", raw)
        if current and match:
            value = match.group(1)
            candidates[current] = None if value == "(none)" else value
            base = current.split(":", 1)[0]
            if base in candidates:
                candidates[base] = candidates[current]
    return candidates


def apt_environment() -> dict[str, str]:
    result = base_environment()
    result.update(
        {
            "DEBIAN_FRONTEND": "noninteractive",
            "LC_ALL": "C.UTF-8",
            "LANG": "C.UTF-8",
            "NEEDRESTART_MODE": "l",
        }
    )
    return result


def apt_base() -> list[str]:
    return ["apt-get", "-o", "DPkg::Lock::Timeout=120", "-o", "Acquire::Retries=5"]


def simulate_packages(packages: Sequence[str]) -> dict[str, Any]:
    if not packages:
        return {"ok": True, "changes": [], "summary": "nothing to install"}
    result = run_command(
        [*apt_base(), "--simulate", "install", "--no-install-recommends", *packages],
        check=False,
        env=apt_environment(),
        timeout=900,
    )
    changes = []
    install_names = []
    for line in result.stdout.splitlines():
        if line.startswith(("Inst ", "Remv ", "Conf ", "Purg ")):
            changes.append(line)
            match = re.match(r"Inst\s+(\S+)", line)
            if match:
                install_names.append(match.group(1))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-3000:]
        raise BootstrapError(f"APT simulation failed:\n{detail}")
    removals = [line for line in changes if line.startswith(("Remv ", "Purg "))]
    if removals:
        raise BootstrapError("APT simulation would remove packages: " + "; ".join(removals))
    if "DOWNGRADED" in result.stdout.upper():
        raise BootstrapError("APT simulation contains a package downgrade")
    return {
        "ok": True,
        "changes": changes,
        "install_packages": sorted(set(install_names)),
        "removals": [],
        "downgrades": [],
        "summary": result.stdout.splitlines()[-10:],
    }


def sysv_boot_state(unit: str) -> str:
    if not unit.endswith(".service"):
        return "not-applicable"
    name = unit[: -len(".service")]
    if not re.fullmatch(r"[A-Za-z0-9_.@+-]+", name):
        return "not-applicable"
    if not (SYSV_INIT_DIR / name).is_file():
        return "not-applicable"
    matcher = re.compile(rf"^S[0-9]{{2}}{re.escape(name)}$")
    try:
        for directory in SYSV_BOOT_DIRS:
            if directory.is_dir() and any(
                entry.is_symlink() and matcher.fullmatch(entry.name)
                for entry in directory.iterdir()
            ):
                return "enabled"
    except OSError as exc:
        raise BootstrapError(f"Could not inspect SysV boot links for {unit}: {exc}") from exc
    return "disabled"


def unit_state(units: Sequence[str] = RUNTIME_UNITS) -> dict[str, dict[str, str]]:
    if shutil.which("systemctl") is None or not pathlib.Path("/run/systemd/system").is_dir():
        return {}
    states: dict[str, dict[str, str]] = {}
    for unit in units:
        enabled = run_command(["systemctl", "is-enabled", unit], check=False, timeout=20)
        active = run_command(["systemctl", "is-active", unit], check=False, timeout=20)
        states[unit] = {
            "enabled": enabled.stdout.strip() or "not-found",
            "active": active.stdout.strip() or "not-found",
            "sysv_enabled": sysv_boot_state(unit),
        }
    return states


def restore_runtime_unit_safety(before: Mapping[str, Mapping[str, str]]) -> list[dict[str, str]]:
    """Undo only service activation introduced by this package transaction."""
    after = unit_state(tuple(before))
    changes: list[dict[str, str]] = []
    violations = runtime_safety_violations(before, after)
    stop_units = sorted(
        {item["unit"] for item in violations if item["state"] == "newly active"}
    )
    disable_units = sorted(
        {
            item["unit"]
            for item in violations
            if item["state"]
            in {"newly enabled", "newly enabled through SysV rc links"}
        }
    )
    for unit in stop_units:
        run_command(["systemctl", "stop", unit], check=False, timeout=60)
        changes.append({"unit": unit, "action": "stopped unexpected activation"})
    for unit in disable_units:
        run_command(["systemctl", "disable", unit], check=False, timeout=60)
        changes.append({"unit": unit, "action": "disabled unexpected enablement"})
    return changes


def runtime_safety_violations(
    before: Mapping[str, Mapping[str, str]], after: Mapping[str, Mapping[str, str]]
) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    enabled_values = {"enabled", "enabled-runtime"}
    active_values = {"active", "activating", "reloading"}
    for unit, old in before.items():
        new = after.get(unit, {})
        if old.get("active") not in active_values and new.get("active") in active_values:
            violations.append({"unit": unit, "state": "newly active"})
        if old.get("enabled") not in enabled_values and new.get("enabled") in enabled_values:
            violations.append({"unit": unit, "state": "newly enabled"})
        if old.get("sysv_enabled") != "enabled" and new.get("sysv_enabled") == "enabled":
            violations.append({"unit": unit, "state": "newly enabled through SysV rc links"})
    return violations


def runtime_state_differences(
    expected: Mapping[str, Mapping[str, str]], observed: Mapping[str, Mapping[str, str]]
) -> list[dict[str, Any]]:
    """Compare active/enabled semantics in both directions."""
    differences: list[dict[str, Any]] = []
    enabled_values = {"enabled", "enabled-runtime"}
    active_values = {"active", "activating", "reloading"}
    structural_values = {
        "alias",
        "linked",
        "linked-runtime",
        "masked",
        "masked-runtime",
    }
    for unit, old in expected.items():
        new = observed.get(unit, {"active": "not-found", "enabled": "not-found"})
        old_active = old.get("active") in active_values
        new_active = new.get("active") in active_values
        old_enabled = old.get("enabled") in enabled_values
        new_enabled = new.get("enabled") in enabled_values
        aspects = []
        if old_active != new_active:
            aspects.append("active")
        if old_enabled != new_enabled:
            aspects.append("enabled")
        if (old.get("sysv_enabled") == "enabled") != (
            new.get("sysv_enabled") == "enabled"
        ):
            aspects.append("sysv-enabled")
        old_enabled_raw = old.get("enabled", "not-found")
        new_enabled_raw = new.get("enabled", "not-found")
        if (
            old_enabled_raw != "not-found"
            and old_enabled_raw != new_enabled_raw
            and (
                old_enabled_raw in structural_values
                or new_enabled_raw in structural_values
            )
        ):
            aspects.append("enabled-metadata")
        if aspects:
            differences.append(
                {
                    "unit": unit,
                    "aspects": aspects,
                    "expected": dict(old),
                    "observed": dict(new),
                }
            )
    return differences


def require_runtime_baseline_match(
    existing_baseline: Mapping[str, Any] | None,
    current: Mapping[str, Mapping[str, str]],
    *,
    refresh_requested: bool,
) -> list[dict[str, Any]]:
    differences = (
        runtime_state_differences(existing_baseline["units"], current)
        if existing_baseline is not None
        else []
    )
    if differences and not refresh_requested:
        raise BootstrapError(
            "Runtime service state differs from its saved baseline; inspect "
            "runtime_preflight_drift and use --refresh-runtime-baseline only after "
            "a separately confirmed configuration change"
        )
    return differences


def atomic_write(path: pathlib.Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = pathlib.Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def machine_identity() -> str:
    path = pathlib.Path("/etc/machine-id")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise BootstrapError(f"Cannot read host identity {path}: {exc}") from exc
    if not re.fullmatch(r"[0-9a-fA-F]{16,64}", value):
        raise BootstrapError(f"Host identity in {path} is malformed")
    return value.lower()


def load_runtime_baseline(*, required: bool) -> dict[str, Any] | None:
    try:
        exists = RUNTIME_BASELINE.is_file()
    except OSError as exc:
        raise BootstrapError(f"Cannot access runtime service baseline {RUNTIME_BASELINE}: {exc}") from exc
    if not exists:
        if required:
            raise BootstrapError(
                "Runtime service baseline is missing; run a confirmed runtime apply before validation"
            )
        return None
    try:
        document = json.loads(RUNTIME_BASELINE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"Invalid runtime service baseline {RUNTIME_BASELINE}: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise BootstrapError("Runtime service baseline has an unsupported schema")
    if document.get("machine_id") != machine_identity():
        raise BootstrapError("Runtime service baseline belongs to a different machine")
    units = document.get("units")
    if not isinstance(units, dict):
        raise BootstrapError("Runtime service baseline has no unit-state object")
    for unit, state_data in units.items():
        if (
            not isinstance(unit, str)
            or not isinstance(state_data, dict)
            or not isinstance(state_data.get("active"), str)
            or not isinstance(state_data.get("enabled"), str)
        ):
            raise BootstrapError("Runtime service baseline contains malformed unit state")
    return document


def ensure_runtime_baseline(current: Mapping[str, Mapping[str, str]]) -> tuple[dict[str, Any], bool]:
    STATE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o755)
    os.chmod(STATE_ROOT, 0o755)
    document = load_runtime_baseline(required=False)
    changed = False
    if document is None:
        document = {
            "schema_version": 1,
            "machine_id": machine_identity(),
            "captured_at": utc_now(),
            "units": {unit: dict(value) for unit, value in sorted(current.items())},
        }
        changed = True
    else:
        units = document["units"]
        for unit, value in sorted(current.items()):
            if unit not in units:
                units[unit] = dict(value)
                changed = True
        if changed:
            document["updated_at"] = utc_now()
    if changed:
        atomic_write(
            RUNTIME_BASELINE,
            (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(),
            0o644,
        )
    return document, changed


def refresh_runtime_baseline(
    current: Mapping[str, Mapping[str, str]],
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    """Explicitly accept current unit state before a package transaction."""
    STATE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o755)
    os.chmod(STATE_ROOT, 0o755)
    previous = load_runtime_baseline(required=False)
    previous_units = previous["units"] if previous else {}
    differences: list[dict[str, Any]] = []
    for unit in sorted(set(previous_units) | set(current)):
        old = previous_units.get(unit, {"active": "untracked", "enabled": "untracked"})
        new = current.get(unit, {"active": "not-found", "enabled": "not-found"})
        if old != new:
            differences.append({"unit": unit, "before": old, "accepted": new})
    if previous is not None and not differences:
        return previous, differences, False
    document = {
        "schema_version": 1,
        "machine_id": machine_identity(),
        "captured_at": utc_now(),
        "refresh": "explicit-command-line-acceptance",
        "previous_captured_at": previous.get("captured_at") if previous else None,
        "units": {unit: dict(value) for unit, value in sorted(current.items())},
    }
    atomic_write(
        RUNTIME_BASELINE,
        (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(),
        0o644,
    )
    return document, differences, True


def _policy_current_state() -> dict[str, Any]:
    try:
        current = os.lstat(POLICY_PATH)
    except FileNotFoundError:
        return {"kind": "absent"}
    if stat.S_ISLNK(current.st_mode):
        return {
            "kind": "symlink",
            "target": os.readlink(POLICY_PATH),
            "uid": current.st_uid,
            "gid": current.st_gid,
        }
    if stat.S_ISREG(current.st_mode):
        content = POLICY_PATH.read_bytes()
        return {
            "kind": "regular",
            "sha256": hashlib.sha256(content).hexdigest(),
            "mode": stat.S_IMODE(current.st_mode),
            "uid": current.st_uid,
            "gid": current.st_gid,
        }
    return {"kind": "other", "mode": stat.S_IFMT(current.st_mode)}


def _policy_state_matches_original(
    original: Mapping[str, Any], current: Mapping[str, Any]
) -> bool:
    kind = original.get("kind")
    if current.get("kind") != kind:
        return False
    if kind == "absent":
        return True
    if kind == "symlink":
        return all(
            current.get(field) == original.get(field)
            for field in ("target", "uid", "gid")
        )
    if kind == "regular":
        return all(
            current.get(field) == original.get(field)
            for field in ("sha256", "mode", "uid", "gid")
        )
    return False


def _policy_state_is_guard(current: Mapping[str, Any]) -> bool:
    mode_matches = current.get("mode") == 0o755 if os.name == "posix" else True
    return (
        current.get("kind") == "regular"
        and mode_matches
        and hmac.compare_digest(
            str(current.get("sha256", "")), hashlib.sha256(POLICY_GUARD).hexdigest()
        )
    )


def _restore_policy_from_state() -> bool:
    if not POLICY_STATE.exists():
        return False
    try:
        state_data = json.loads(POLICY_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"Cannot read crash-recovery state {POLICY_STATE}: {exc}") from exc
    kind = state_data.get("kind")
    if kind not in {"absent", "symlink", "regular"}:
        raise BootstrapError(f"Unknown policy-rc.d recovery kind: {kind!r}")
    current = _policy_current_state()
    already_original = _policy_state_matches_original(state_data, current)
    guard_present = _policy_state_is_guard(current)
    if not already_original and not guard_present:
        raise BootstrapError(
            f"Refusing to recover {POLICY_PATH}: it changed after Hoardarr recorded "
            f"crash state; the current file and {POLICY_STATE} were preserved"
        )
    if already_original:
        # A crash may have occurred after writing recovery state but before the
        # guard was installed, or after a successful restoration but before
        # cleanup.  In either case there is nothing to overwrite.
        pass
    elif kind == "absent":
        POLICY_PATH.unlink(missing_ok=True)
    elif kind == "symlink":
        target = state_data.get("target")
        if not isinstance(target, str):
            raise BootstrapError("Invalid policy-rc.d symlink recovery state")
        POLICY_PATH.unlink(missing_ok=True)
        os.symlink(target, POLICY_PATH)
        if hasattr(os, "lchown"):
            os.lchown(POLICY_PATH, int(state_data["uid"]), int(state_data["gid"]))
        if "atime_ns" in state_data and os.utime in os.supports_follow_symlinks:
            os.utime(
                POLICY_PATH,
                ns=(int(state_data["atime_ns"]), int(state_data["mtime_ns"])),
                follow_symlinks=False,
            )
    elif kind == "regular":
        if not POLICY_BACKUP.is_file():
            raise BootstrapError(f"Missing policy-rc.d recovery backup: {POLICY_BACKUP}")
        content = POLICY_BACKUP.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if not hmac.compare_digest(digest, str(state_data.get("sha256", ""))):
            raise BootstrapError("policy-rc.d recovery backup failed its checksum")
        atomic_write(POLICY_PATH, content, int(state_data["mode"]))
        os.chown(POLICY_PATH, int(state_data["uid"]), int(state_data["gid"]))
        os.utime(POLICY_PATH, ns=(int(state_data["atime_ns"]), int(state_data["mtime_ns"])))
    else:
        raise BootstrapError(f"Unknown policy-rc.d recovery kind: {kind!r}")
    POLICY_STATE.unlink(missing_ok=True)
    POLICY_BACKUP.unlink(missing_ok=True)
    return True


@contextlib.contextmanager
def inhibit_service_starts() -> Iterator[dict[str, Any]]:
    STATE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o755)
    recovered = _restore_policy_from_state()
    try:
        current = os.lstat(POLICY_PATH)
    except FileNotFoundError:
        state_data: dict[str, Any] = {"kind": "absent", "created_at": utc_now()}
    else:
        if stat.S_ISLNK(current.st_mode):
            state_data = {
                "kind": "symlink",
                "target": os.readlink(POLICY_PATH),
                "uid": current.st_uid,
                "gid": current.st_gid,
                "atime_ns": current.st_atime_ns,
                "mtime_ns": current.st_mtime_ns,
                "created_at": utc_now(),
            }
        elif stat.S_ISREG(current.st_mode):
            content = POLICY_PATH.read_bytes()
            atomic_write(POLICY_BACKUP, content, stat.S_IMODE(current.st_mode))
            state_data = {
                "kind": "regular",
                "sha256": hashlib.sha256(content).hexdigest(),
                "mode": stat.S_IMODE(current.st_mode),
                "uid": current.st_uid,
                "gid": current.st_gid,
                "atime_ns": current.st_atime_ns,
                "mtime_ns": current.st_mtime_ns,
                "created_at": utc_now(),
            }
        else:
            raise BootstrapError(f"Refusing to replace non-file {POLICY_PATH}")
    atomic_write(POLICY_STATE, (json.dumps(state_data, sort_keys=True) + "\n").encode(), 0o600)
    atomic_write(POLICY_PATH, POLICY_GUARD, 0o755)
    try:
        yield {"recovered_stale_state": recovered, "original": state_data["kind"]}
    finally:
        _restore_policy_from_state()


@contextlib.contextmanager
def installer_lock() -> Iterator[None]:
    if os.name != "posix":
        raise BootstrapError("Apply is supported only on Linux")
    import fcntl  # Linux-only and intentionally imported after the platform check

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BootstrapError(f"Another Hoardarr bootstrap owns {LOCK_FILE}") from exc
        stream.seek(0)
        stream.truncate()
        stream.write(f"pid={os.getpid()} started={utc_now()}\n")
        stream.flush()
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def hash_file(path: pathlib.Path, algorithm: str = "sha256") -> bytes:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def verify_file_sha256(path: pathlib.Path, expected: str) -> None:
    actual = hash_file(path, "sha256").hex()
    if not hmac.compare_digest(actual, expected):
        raise BootstrapError(f"Checksum mismatch for {path.name}: expected {expected}, got {actual}")


def verify_file_integrity(path: pathlib.Path, integrity: str) -> None:
    algorithm, expected = verify_integrity_syntax(integrity, path.name)
    actual = hash_file(path, algorithm)
    if not hmac.compare_digest(actual, expected):
        raise BootstrapError(f"Integrity mismatch for {path.name}")


def download_pinned(
    url: str,
    destination: pathlib.Path,
    *,
    sha256: str | None = None,
    integrity: str | None = None,
    http_headers: Mapping[str, str] | None = None,
) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise BootstrapError(f"Refusing non-HTTPS or malformed download URL: {url}")
    if bool(sha256) == bool(integrity):
        raise BootstrapError("A download must have exactly one pinned checksum")
    verify = (lambda path: verify_file_sha256(path, sha256 or "")) if sha256 else (
        lambda path: verify_file_integrity(path, integrity or "")
    )
    if destination.is_file():
        try:
            verify(destination)
            return False
        except BootstrapError:
            destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.part")
    temporary.unlink(missing_ok=True)
    headers = {"User-Agent": "Hoardarr-bootstrap/1"}
    allowed_headers = {"user-agent": "User-Agent", "referer": "Referer", "accept": "Accept"}
    for raw_name, raw_value in (http_headers or {}).items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise BootstrapError("Curated download headers must be string pairs")
        name = allowed_headers.get(raw_name.lower())
        if name is None:
            raise BootstrapError(f"Vendor catalog contains a forbidden HTTP header: {raw_name}")
        if "\r" in raw_value or "\n" in raw_value or len(raw_value) > 1000:
            raise BootstrapError(f"Vendor catalog contains an unsafe HTTP header value: {raw_name}")
        headers[name] = raw_value
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60, context=ssl.create_default_context()) as response:
            final = urllib.parse.urlparse(response.geturl())
            if final.scheme != "https":
                raise BootstrapError(f"Download redirected away from HTTPS: {response.geturl()}")
            with temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
                output.flush()
                os.fsync(output.fileno())
        verify(temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def safe_extract(archive: pathlib.Path, destination: pathlib.Path) -> None:
    with tarfile.open(archive, "r:*") as tar:
        try:
            tar.extractall(destination, filter="data")
        except TypeError as exc:  # pragma: no cover - Noble uses a filter-capable Python
            raise BootstrapError("Python tar extraction safety filters are unavailable") from exc


def tool_version(
    command: Sequence[os.PathLike[str] | str],
    expected: str,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    path = pathlib.Path(os.fspath(command[0]))
    if not path.exists():
        return {"installed": False, "expected": expected, "actual": None, "ok": False}
    result = run_command(command, check=False, timeout=60, env=env)
    output = (result.stdout or result.stderr).strip().splitlines()
    actual = output[0].strip() if output else ""
    exact_version = re.compile(rf"(?:^|\s)v?{re.escape(expected)}(?:$|\s)")
    return {
        "installed": True,
        "expected": expected,
        "actual": actual,
        "ok": result.returncode == 0 and exact_version.search(actual) is not None,
    }


def toolchain_environment(node_dir: pathlib.Path | None = None) -> dict[str, str]:
    env = base_environment()
    components = []
    if node_dir is not None:
        components.append(os.fspath(node_dir / "bin"))
    components.append(os.fspath(TOOLCHAIN_BIN))
    components.append(SYSTEM_PATH)
    env["PATH"] = os.pathsep.join(components)
    env["LC_ALL"] = "C.UTF-8"
    env["npm_config_cache"] = os.fspath(DOWNLOAD_CACHE / "npm")
    return env


def ensure_toolchain_roots() -> None:
    parent = TOOLCHAIN_ROOT.parent
    parent_created = not parent.exists()
    parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    if parent_created:
        os.chmod(parent, 0o755)
    for directory in (TOOLCHAIN_ROOT, TOOLCHAIN_BIN):
        directory.mkdir(parents=True, exist_ok=True, mode=0o755)
        os.chmod(directory, 0o755)


def normalize_public_tree(root: pathlib.Path) -> bool:
    try:
        root.resolve(strict=True).relative_to(TOOLCHAIN_ROOT.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise BootstrapError(f"Refusing to normalize a path outside the toolchain root: {root}") from exc
    changed = False
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = pathlib.Path(directory)
        if stat.S_IMODE(os.lstat(directory_path).st_mode) != 0o755:
            os.chmod(directory_path, 0o755)
            changed = True
        for name in directory_names:
            path = directory_path / name
            if path.is_symlink():
                continue
            if stat.S_IMODE(os.lstat(path).st_mode) != 0o755:
                os.chmod(path, 0o755)
                changed = True
        for name in file_names:
            path = directory_path / name
            if path.is_symlink():
                continue
            current = stat.S_IMODE(os.lstat(path).st_mode)
            desired = 0o755 if current & 0o111 else 0o644
            if current != desired:
                os.chmod(path, desired)
                changed = True
    return changed


def inspect_toolchains(versions: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    node_dir = TOOLCHAIN_ROOT / f"node-{versions['NODE_VERSION']}"
    node = node_dir / "bin" / "node"
    corepack = TOOLCHAIN_ROOT / f"corepack-{versions['COREPACK_VERSION']}" / "bin" / "corepack"
    pnpm = TOOLCHAIN_ROOT / f"pnpm-{versions['PNPM_VERSION']}" / "bin" / "pnpm"
    uv = TOOLCHAIN_ROOT / f"uv-{versions['UV_VERSION']}" / "bin" / "uv"
    return {
        "node": tool_version([node, "--version"], versions["NODE_VERSION"]),
        "corepack": tool_version(
            [corepack, "--version"], versions["COREPACK_VERSION"], env=toolchain_environment(node_dir)
        ),
        "pnpm": tool_version(
            [pnpm, "--version"], versions["PNPM_VERSION"], env=toolchain_environment(node_dir)
        ),
        "uv": tool_version([uv, "--version"], versions["UV_VERSION"]),
    }


def _install_node(versions: Mapping[str, str], arch: str, changes: list[str]) -> pathlib.Path:
    version = versions["NODE_VERSION"]
    node_arch = {"amd64": "x64", "arm64": "arm64"}[arch]
    target = TOOLCHAIN_ROOT / f"node-{version}"
    executable = target / "bin" / "node"
    ensure_toolchain_roots()
    permissions_changed = normalize_public_tree(target) if target.exists() else False
    status = tool_version([executable, "--version"], version)
    if target.exists():
        if not status["ok"]:
            raise BootstrapError(f"Existing Node toolchain is not version {version}: {target}")
        if permissions_changed:
            changes.append(f"normalized permissions for Node {version}")
        return target
    filename = f"node-v{version}-linux-{node_arch}.tar.xz"
    archive = DOWNLOAD_CACHE / filename
    changed = download_pinned(
        f"https://nodejs.org/dist/v{version}/{filename}",
        archive,
        sha256=versions[f"NODE_SHA256_{arch.upper()}"],
    )
    if changed:
        changes.append(f"downloaded {filename}")
    TOOLCHAIN_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".node-stage-", dir=TOOLCHAIN_ROOT) as temporary:
        temporary_path = pathlib.Path(temporary)
        safe_extract(archive, temporary_path)
        roots = [value for value in temporary_path.iterdir() if value.is_dir()]
        if len(roots) != 1 or not (roots[0] / "bin" / "node").is_file():
            raise BootstrapError(f"Unexpected Node archive layout: {filename}")
        os.replace(roots[0], target)
    normalize_public_tree(target)
    if not tool_version([executable, "--version"], version)["ok"]:
        raise BootstrapError("Node failed post-install version validation")
    changes.append(f"installed Node {version}")
    return target


def _npm_package_install(
    package: str,
    version: str,
    integrity: str,
    node_dir: pathlib.Path,
    changes: list[str],
) -> pathlib.Path:
    target = TOOLCHAIN_ROOT / f"{package}-{version}"
    executable = target / "bin" / package
    ensure_toolchain_roots()
    permissions_changed = normalize_public_tree(target) if target.exists() else False
    status = tool_version([executable, "--version"], version, env=toolchain_environment(node_dir))
    if target.exists():
        if not status["ok"]:
            raise BootstrapError(f"Existing {package} toolchain is not version {version}: {target}")
        if permissions_changed:
            changes.append(f"normalized permissions for {package} {version}")
        return target
    filename = f"{package}-{version}.tgz"
    archive = DOWNLOAD_CACHE / filename
    changed = download_pinned(
        f"https://registry.npmjs.org/{package}/-/{filename}", archive, integrity=integrity
    )
    if changed:
        changes.append(f"downloaded {filename}")
    npm = node_dir / "bin" / "npm"
    TOOLCHAIN_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{package}-stage-", dir=TOOLCHAIN_ROOT) as temporary:
        staging = pathlib.Path(temporary) / "install"
        run_command(
            [
                npm,
                "install",
                "--global",
                "--prefix",
                staging,
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                archive,
            ],
            env=toolchain_environment(node_dir),
            timeout=600,
        )
        staged_executable = staging / "bin" / package
        if not tool_version(
            [staged_executable, "--version"], version, env=toolchain_environment(node_dir)
        )["ok"]:
            raise BootstrapError(f"{package} failed staged version validation")
        os.replace(staging, target)
    normalize_public_tree(target)
    if not tool_version(
        [executable, "--version"], version, env=toolchain_environment(node_dir)
    )["ok"]:
        raise BootstrapError(f"{package} failed post-install version validation")
    changes.append(f"installed {package} {version}")
    return target


def _install_uv(versions: Mapping[str, str], arch: str, changes: list[str]) -> pathlib.Path:
    version = versions["UV_VERSION"]
    triple = {"amd64": "x86_64-unknown-linux-gnu", "arm64": "aarch64-unknown-linux-gnu"}[arch]
    target = TOOLCHAIN_ROOT / f"uv-{version}"
    executable = target / "bin" / "uv"
    ensure_toolchain_roots()
    permissions_changed = normalize_public_tree(target) if target.exists() else False
    status = tool_version([executable, "--version"], version)
    if target.exists():
        if not status["ok"]:
            raise BootstrapError(f"Existing uv toolchain is not version {version}: {target}")
        if permissions_changed:
            changes.append(f"normalized permissions for uv {version}")
        return target
    filename = f"uv-{triple}.tar.gz"
    archive = DOWNLOAD_CACHE / f"uv-{version}-{triple}.tar.gz"
    changed = download_pinned(
        f"https://github.com/astral-sh/uv/releases/download/{version}/{filename}",
        archive,
        sha256=versions[f"UV_SHA256_{arch.upper()}"],
    )
    if changed:
        changes.append(f"downloaded {archive.name}")
    TOOLCHAIN_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".uv-stage-", dir=TOOLCHAIN_ROOT) as temporary:
        temporary_path = pathlib.Path(temporary)
        safe_extract(archive, temporary_path)
        binaries: dict[str, pathlib.Path] = {}
        for name in ("uv", "uvx"):
            matches = [value for value in temporary_path.rglob(name) if value.is_file()]
            if len(matches) != 1:
                raise BootstrapError(f"Unexpected uv archive layout for {name}")
            binaries[name] = matches[0]
        staging = temporary_path / "install"
        (staging / "bin").mkdir(parents=True)
        for name, source in binaries.items():
            shutil.copy2(source, staging / "bin" / name)
            os.chmod(staging / "bin" / name, 0o755)
        os.replace(staging, target)
    normalize_public_tree(target)
    if not tool_version([executable, "--version"], version)["ok"]:
        raise BootstrapError("uv failed post-install version validation")
    changes.append(f"installed uv {version}")
    return target


def _managed_symlink(source: pathlib.Path, destination: pathlib.Path, changes: list[str]) -> None:
    if not source.exists():
        return
    if destination.is_symlink():
        current_raw = pathlib.Path(os.readlink(destination))
        current = current_raw if current_raw.is_absolute() else destination.parent / current_raw
        if current == source:
            return
        def normalized_real_path(path: pathlib.Path) -> str:
            value = os.path.normcase(os.path.realpath(os.fspath(path)))
            # pathlib/os.path can disagree about the Windows extended-length
            # prefix after resolving a symlink.  It is not part of identity.
            if os.name == "nt" and value.startswith("\\\\?\\"):
                value = value[4:]
            return value

        current_real = normalized_real_path(current)
        root_real = normalized_real_path(TOOLCHAIN_ROOT)
        try:
            managed = os.path.commonpath([current_real, root_real]) == root_real
        except ValueError:
            managed = False
        if not managed:
            raise BootstrapError(f"Refusing to replace an unmanaged symlink: {destination}")
    elif destination.exists():
        raise BootstrapError(f"Refusing to overwrite non-symlink tool entry: {destination}")
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}")
    temporary.unlink(missing_ok=True)
    os.symlink(source, temporary)
    os.replace(temporary, destination)
    changes.append(f"linked {destination.name}")


def install_toolchains(versions: Mapping[str, str], arch: str) -> list[str]:
    changes: list[str] = []
    ensure_toolchain_roots()
    node_dir = _install_node(versions, arch, changes)
    corepack_dir = _npm_package_install(
        "corepack", versions["COREPACK_VERSION"], versions["COREPACK_INTEGRITY"], node_dir, changes
    )
    pnpm_dir = _npm_package_install(
        "pnpm", versions["PNPM_VERSION"], versions["PNPM_INTEGRITY"], node_dir, changes
    )
    uv_dir = _install_uv(versions, arch, changes)
    ensure_toolchain_roots()
    sources = {
        "node": node_dir / "bin" / "node",
        "npm": node_dir / "bin" / "npm",
        "npx": node_dir / "bin" / "npx",
        "corepack": corepack_dir / "bin" / "corepack",
        "pnpm": pnpm_dir / "bin" / "pnpm",
        "pnpx": pnpm_dir / "bin" / "pnpx",
        "uv": uv_dir / "bin" / "uv",
        "uvx": uv_dir / "bin" / "uvx",
    }
    for name, source in sources.items():
        _managed_symlink(source, TOOLCHAIN_BIN / name, changes)
    profile_content = (
        "# Managed by Hoardarr bootstrap.\n"
        "# Pinned tools live outside distro-managed paths.\n"
        f"export PATH={TOOLCHAIN_BIN}:$PATH\n"
    ).encode()
    if PROFILE_FILE.exists():
        old = PROFILE_FILE.read_bytes()
        if old != profile_content and not old.startswith(b"# Managed by Hoardarr bootstrap."):
            raise BootstrapError(f"Refusing to overwrite unmanaged profile: {PROFILE_FILE}")
    else:
        old = b""
    if old != profile_content:
        atomic_write(PROFILE_FILE, profile_content, 0o644)
        changes.append(f"wrote {PROFILE_FILE}")
    return changes


def load_vendor_catalog() -> dict[str, Any]:
    if not VENDOR_CATALOG.is_file():
        return {"schema_version": 1, "tools": []}
    try:
        data = json.loads(VENDOR_CATALOG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BootstrapError(f"Invalid vendor tool catalog {VENDOR_CATALOG}: {exc}") from exc
    if not isinstance(data, dict):
        raise BootstrapError("Vendor tool catalog must be a JSON object")
    tools = data.get("tools", data.get("vendor_tools", []))
    if not isinstance(tools, list) or any(not isinstance(item, dict) for item in tools):
        raise BootstrapError("Vendor tool catalog 'tools' must be a list of objects")
    data["tools"] = tools
    return data


def recommended_vendor_ids(hardware: Mapping[str, Any]) -> set[str]:
    values = hardware.get("recommendations", {}).get("vendor_tools", [])
    result: set[str] = set()
    if not isinstance(values, list):
        raise BootstrapError("Hardware vendor_tools recommendation must be a list")
    for item in values:
        if isinstance(item, str):
            result.add(item)
        elif isinstance(item, dict):
            value = item.get("id", item.get("tool_id", item.get("tool", item.get("name"))))
            if isinstance(value, str):
                result.add(value)
    return result


def resolve_vendor_tools(hardware: Mapping[str, Any], arch: str) -> list[dict[str, Any]]:
    wanted = recommended_vendor_ids(hardware)
    catalog = load_vendor_catalog()
    indexed: dict[str, dict[str, Any]] = {}
    for tool in catalog["tools"]:
        identifier = tool.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identifier):
            raise BootstrapError("Every vendor tool catalog entry needs a non-empty id")
        if identifier in indexed:
            raise BootstrapError(f"Duplicate vendor tool id: {identifier}")
        indexed[identifier] = tool
    plan: list[dict[str, Any]] = []
    for identifier in sorted(wanted):
        tool = indexed.get(identifier)
        if tool is None:
            plan.append({"id": identifier, "available": False, "reason": "not in pinned catalog"})
            continue
        method = tool.get("install_method", "manual")
        artifacts = tool.get("artifacts")
        # The catalog normally has one top-level pinned artifact. A list is also
        # accepted so a future entry can carry separate architecture payloads.
        if artifacts is None and tool.get("url"):
            artifacts = [
                {
                    "url": tool.get("url"),
                    "sha256": tool.get("sha256"),
                    "architecture": tool.get("architectures"),
                    "distro_versions": tool.get("distro_versions"),
                    "archive_type": tool.get("archive_type", "deb"),
                    "deb_member": tool.get("deb_member"),
                    "http_headers": tool.get("http_headers", {}),
                    "landing_url": tool.get("landing_url"),
                }
            ]
        if artifacts is None:
            artifacts = []
        if not isinstance(artifacts, list):
            raise BootstrapError(f"Vendor tool {identifier} artifacts must be a list")
        applicable = []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            artifact_arch = artifact.get("arch", artifact.get("architecture"))
            distro = artifact.get("distro", artifact.get("os"))
            releases = artifact.get(
                "distro_versions", artifact.get("version_id", artifact.get("release", "24.04"))
            )
            arch_ok = artifact_arch in (None, arch, "all") or (
                isinstance(artifact_arch, list) and arch in artifact_arch
            )
            distro_ok = distro in (None, "ubuntu", "ubuntu-24.04", "Ubuntu 24.04")
            release_ok = releases in (None, "24.04", "noble", "") or (
                isinstance(releases, list) and any(value in {"24.04", "noble"} for value in releases)
            )
            if arch_ok and distro_ok and release_ok:
                applicable.append(artifact)
        plan.append(
            {
                "id": identifier,
                "name": tool.get("name", identifier),
                "available": method == "official-public-fetch" and len(applicable) == 1,
                "install_method": method,
                "catalog_version": tool.get("version"),
                "deb_package": tool.get("deb_package"),
                "deb_version": tool.get("deb_version"),
                "conflict_group": tool.get("conflict_group"),
                "license_required": bool(
                    tool.get("requires_license_acceptance", tool.get("license_required", True))
                ),
                "license_url": tool.get("license_url"),
                "landing_url": tool.get("landing_url"),
                "artifact": applicable[0] if len(applicable) == 1 else None,
                "reason": (
                    None
                    if method == "official-public-fetch" and len(applicable) == 1
                    else (
                        f"catalog install method is {method}"
                        if method != "official-public-fetch"
                        else "no unique Ubuntu 24.04 artifact for this architecture"
                    )
                ),
                "validation": tool.get("validation", {}),
            }
        )
    return plan


def validate_vendor_plan_conflicts(plan: Sequence[Mapping[str, Any]]) -> None:
    groups: dict[str, list[str]] = {}
    packages: dict[str, list[tuple[str, str | None]]] = {}
    for tool in plan:
        if not tool.get("available"):
            continue
        identifier = str(tool["id"])
        package = tool.get("deb_package")
        version = tool.get("deb_version")
        if not isinstance(package, str) or not PACKAGE_RE.fullmatch(package):
            raise BootstrapError(f"Vendor tool {identifier} lacks a valid pinned deb_package")
        if not isinstance(version, str) or not version:
            raise BootstrapError(f"Vendor tool {identifier} lacks a pinned deb_version")
        packages.setdefault(package, []).append((identifier, version))
        conflict_group = tool.get("conflict_group")
        if conflict_group is not None:
            if not isinstance(conflict_group, str) or not re.fullmatch(
                r"[a-z0-9][a-z0-9-]*", conflict_group
            ):
                raise BootstrapError(f"Vendor tool {identifier} has an invalid conflict_group")
            groups.setdefault(conflict_group, []).append(identifier)
    conflicts = [
        f"{group}: {', '.join(sorted(identifiers))}"
        for group, identifiers in sorted(groups.items())
        if len(identifiers) > 1
    ]
    # A shared package name is also inherently mutually exclusive when exact
    # catalog versions differ, even if a future catalog entry omits a group.
    for package, entries in sorted(packages.items()):
        if len(entries) > 1 and len({version for _, version in entries}) > 1:
            description = f"Debian package {package}: " + ", ".join(
                f"{identifier}={version}" for identifier, version in sorted(entries)
            )
            if not any(description in item for item in conflicts):
                conflicts.append(description)
    if conflicts:
        raise BootstrapError(
            "Vendor tool conflict: choose the controller-specific tool "
            "after verifying the actual hardware, then exclude the other provider: "
            + "; ".join(conflicts)
        )


def _vendor_receipt_path(identifier: str) -> pathlib.Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identifier):
        raise BootstrapError(f"Unsafe vendor tool id: {identifier!r}")
    return VENDOR_STATE_ROOT / f"{identifier}.json"


def _read_vendor_receipt(identifier: str) -> dict[str, Any] | None:
    path = _vendor_receipt_path(identifier)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BootstrapError(f"Invalid vendor receipt {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BootstrapError(f"Invalid vendor receipt {path}")
    return value


def _installed_deb_version(package: str) -> str | None:
    if not PACKAGE_RE.fullmatch(package):
        raise BootstrapError(f"Unsafe Debian package name in vendor metadata: {package!r}")
    result = run_command(
        ["dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${Version}", package],
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        return None
    fields = result.stdout.strip().split("\t", 1)
    return fields[1] if len(fields) == 2 and fields[0].startswith("ii") else None


@contextlib.contextmanager
def _vendor_deb(archive: pathlib.Path, archive_type: str, member_name: Any) -> Iterator[pathlib.Path]:
    if archive_type == "deb":
        yield archive
        return
    if archive_type not in {"tar-deb", "zip-deb"}:
        raise BootstrapError(f"Unsupported vendor archive type: {archive_type}")
    if not isinstance(member_name, str) or not member_name.endswith(".deb"):
        raise BootstrapError(f"{archive_type} requires an exact deb_member")
    member_path = pathlib.PurePosixPath(member_name)
    if member_path.is_absolute() or ".." in member_path.parts or "\\" in member_name:
        raise BootstrapError("Unsafe deb_member path in vendor catalog")
    with tempfile.TemporaryDirectory(prefix="hoardarr-vendor-") as temporary:
        output = pathlib.Path(temporary) / member_path.name
        if archive_type == "tar-deb":
            with tarfile.open(archive, "r:*") as tar:
                try:
                    member = tar.getmember(member_name)
                except KeyError as exc:
                    raise BootstrapError(f"Vendor archive is missing {member_name}") from exc
                if not member.isfile():
                    raise BootstrapError(f"Vendor deb_member is not a regular file: {member_name}")
                source = tar.extractfile(member)
                if source is None:
                    raise BootstrapError(f"Could not read vendor deb_member: {member_name}")
                with source, output.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
        else:
            with zipfile.ZipFile(archive) as zipped:
                try:
                    info = zipped.getinfo(member_name)
                except KeyError as exc:
                    raise BootstrapError(f"Vendor archive is missing {member_name}") from exc
                if info.is_dir():
                    raise BootstrapError(f"Vendor deb_member is not a regular file: {member_name}")
                with zipped.open(info) as source, output.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
        yield output


def _deb_metadata(path: pathlib.Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for field in ("Package", "Version", "Architecture"):
        result = run_command(["dpkg-deb", "-f", path, field], timeout=60)
        value = result.stdout.strip()
        if not value or "\n" in value:
            raise BootstrapError(f"Vendor Debian package has invalid {field} metadata")
        metadata[field.lower()] = value
    if not PACKAGE_RE.fullmatch(metadata["package"]):
        raise BootstrapError(f"Vendor Debian package has an unsafe package name: {metadata['package']!r}")
    return metadata


def simulate_vendor_deb(path: pathlib.Path, metadata: Mapping[str, str]) -> dict[str, Any]:
    package = metadata["package"]
    new_version = metadata["version"]
    current_version = _installed_deb_version(package)
    if current_version is not None:
        downgrade = run_command(
            ["dpkg", "--compare-versions", current_version, "gt", new_version],
            check=False,
            timeout=30,
        )
        if downgrade.returncode == 0:
            raise BootstrapError(
                f"Refusing vendor package downgrade for {package}: {current_version} -> {new_version}"
            )
    simulation = run_command(
        [*apt_base(), "--simulate", "install", "--no-install-recommends", path],
        check=False,
        env=apt_environment(),
        timeout=900,
    )
    if simulation.returncode != 0:
        detail = (simulation.stderr or simulation.stdout).strip()[-3000:]
        raise BootstrapError(f"Vendor Debian package simulation failed for {package}:\n{detail}")
    transaction = [
        line
        for line in simulation.stdout.splitlines()
        if line.startswith(("Inst ", "Conf ", "Remv ", "Purg "))
    ]
    removals = [line for line in transaction if line.startswith(("Remv ", "Purg "))]
    if removals:
        raise BootstrapError(
            f"Vendor package {package} would remove packages: {'; '.join(removals)}"
        )
    if "DOWNGRADED" in simulation.stdout.upper():
        raise BootstrapError(f"Vendor package {package} simulation contains a downgrade")
    install_names = []
    for line in transaction:
        match = re.match(r"Inst\s+(\S+)", line)
        if match:
            install_names.append(match.group(1))
    target_seen = any(name.split(":", 1)[0] == package for name in install_names)
    if current_version != new_version and not target_seen:
        raise BootstrapError(f"Vendor package simulation did not select {package} {new_version}")
    already_installed = installed_packages(install_names)
    unexpected_changes = sorted(
        name
        for name in install_names
        if name.split(":", 1)[0] != package and name in already_installed
    )
    if unexpected_changes:
        raise BootstrapError(
            f"Vendor package {package} would change existing dependency packages: "
            + ", ".join(unexpected_changes)
        )
    return {
        "package": package,
        "current_version": current_version,
        "new_version": new_version,
        "transaction": transaction,
        "new_dependencies": sorted(
            name for name in install_names if name.split(":", 1)[0] != package
        ),
        "install_packages": sorted(set(install_names)),
        "removals": [],
        "downgrades": [],
    }


def _vendor_receipt_matches(tool: Mapping[str, Any], receipt: Mapping[str, Any] | None) -> bool:
    artifact = tool.get("artifact")
    if not isinstance(artifact, Mapping) or receipt is None:
        return False
    expected_sha = artifact.get("sha256")
    package = receipt.get("package")
    version = receipt.get("deb_version")
    if not all(isinstance(value, str) for value in (expected_sha, package, version)):
        return False
    return (
        receipt.get("id") == tool.get("id")
        and receipt.get("url") == artifact.get("url")
        and receipt.get("artifact_sha256") == expected_sha
        and _installed_deb_version(package) == version
    )


def install_vendor_tools(
    plan: Sequence[Mapping[str, Any]],
    accepted: set[str],
    changes: list[str],
    *,
    before_package_mutation: Any = None,
    after_package_mutation: Any = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for tool in plan:
        identifier = str(tool["id"])
        if not tool.get("available"):
            results.append(
                {
                    "id": identifier,
                    "ok": True,
                    "changed": False,
                    "skipped": True,
                    "reason": tool.get("reason") or f"install method is {tool.get('install_method')}",
                }
            )
            continue
        if tool.get("license_required") and identifier not in accepted:
            raise BootstrapError(
                f"Vendor tool {identifier} requires --accept-vendor-license {identifier}"
            )
        if tool.get("install_method") != "official-public-fetch":
            raise BootstrapError(f"Refusing unsupported vendor install method for {identifier}")
        artifact = tool.get("artifact")
        if not isinstance(artifact, Mapping):
            raise BootstrapError(f"Missing artifact for vendor tool {identifier}")
        url = artifact.get("url")
        digest = artifact.get("sha256")
        archive_type = artifact.get(
            "archive_type", artifact.get("package_type", artifact.get("type", "deb"))
        )
        if not isinstance(url, str) or not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise BootstrapError(f"Vendor artifact {identifier} needs a pinned HTTPS URL and SHA-256")
        if archive_type not in {"deb", "tar-deb", "zip-deb"}:
            raise BootstrapError(f"Vendor artifact {identifier} has unsupported archive type {archive_type}")
        receipt = _read_vendor_receipt(identifier)
        if _vendor_receipt_matches(tool, receipt):
            results.append(
                {
                    "id": identifier,
                    "ok": True,
                    "changed": False,
                    "package": receipt["package"],
                    "version": receipt["deb_version"],
                }
            )
            continue
        filename = pathlib.Path(urllib.parse.urlparse(url).path).name or f"{identifier}.artifact"
        path = DOWNLOAD_CACHE / "vendor" / identifier / filename
        headers = artifact.get("http_headers", {})
        if not isinstance(headers, Mapping):
            raise BootstrapError(f"Vendor artifact {identifier} http_headers must be an object")
        if download_pinned(url, path, sha256=digest, http_headers=headers):
            changes.append(f"downloaded vendor tool {identifier}")
        with _vendor_deb(path, str(archive_type), artifact.get("deb_member")) as deb:
            metadata = _deb_metadata(deb)
            expected_package = tool.get("deb_package")
            expected_version = tool.get("deb_version")
            if metadata["package"] != expected_package or metadata["version"] != expected_version:
                raise BootstrapError(
                    f"Vendor package metadata mismatch for {identifier}: expected "
                    f"{expected_package}={expected_version}, got "
                    f"{metadata['package']}={metadata['version']}"
                )
            if metadata["architecture"] not in {"all", normalized_arch()}:
                raise BootstrapError(
                    f"Vendor package {identifier} architecture is {metadata['architecture']}"
                )
            current = _installed_deb_version(metadata["package"])
            changed = current != metadata["version"]
            simulation = None
            if changed:
                simulation = simulate_vendor_deb(deb, metadata)
                transaction_packages = simulation["install_packages"]
                if before_package_mutation is not None:
                    before_package_mutation(transaction_packages)
                try:
                    run_command(
                        [
                            *apt_base(),
                            "install",
                            "-y",
                            "--no-remove",
                            "--no-install-recommends",
                            deb,
                        ],
                        env=apt_environment(),
                        timeout=900,
                    )
                finally:
                    if after_package_mutation is not None:
                        after_package_mutation(transaction_packages)
                changes.append(f"installed vendor tool {identifier}")
            installed = _installed_deb_version(metadata["package"])
            if installed != metadata["version"]:
                raise BootstrapError(
                    f"Vendor tool {identifier} validation failed: expected {metadata['version']}, got {installed}"
                )
            receipt_data = {
                "schema_version": 1,
                "id": identifier,
                "catalog_version": tool.get("catalog_version"),
                "artifact_sha256": digest,
                "url": url,
                "package": metadata["package"],
                "deb_version": metadata["version"],
                "installed_at": utc_now(),
            }
            receipt_path = _vendor_receipt_path(identifier)
            encoded = (json.dumps(receipt_data, indent=2, sort_keys=True) + "\n").encode()
            if not receipt_path.is_file() or receipt_path.read_bytes() != encoded:
                atomic_write(receipt_path, encoded, 0o644)
            os.chmod(receipt_path, 0o644)
            results.append(
                {
                    "id": identifier,
                    "ok": True,
                    "changed": changed,
                    "package": metadata["package"],
                    "version": metadata["version"],
                    "simulation": simulation,
                }
            )
    return results


def validate_vendor_tools(plan: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    allowed_validation_arguments = {"--version", "-v", "-V", "version", "show", "help", "-h", "--help"}
    for tool in plan:
        identifier = str(tool["id"])
        receipt = _read_vendor_receipt(identifier)
        installed_ok = _vendor_receipt_matches(tool, receipt)
        result: dict[str, Any] = {
            "id": identifier,
            "installed": installed_ok,
            "ok": installed_ok,
            "package": receipt.get("package") if receipt else None,
            "version": receipt.get("deb_version") if receipt else None,
        }
        validation = tool.get("validation")
        if installed_ok and isinstance(validation, Mapping) and validation.get("command"):
            raw_command = validation["command"]
            if isinstance(raw_command, str):
                command = shlex.split(raw_command)
            elif isinstance(raw_command, list) and all(isinstance(value, str) for value in raw_command):
                command = list(raw_command)
            else:
                raise BootstrapError(f"Invalid validation command for vendor tool {identifier}")
            if not command or any(value not in allowed_validation_arguments for value in command[1:]):
                raise BootstrapError(f"Vendor tool {identifier} has a non-read-only validation command")
            executable = command[0]
            if not (pathlib.Path(executable).is_absolute() or re.fullmatch(r"[A-Za-z0-9_.+-]+", executable)):
                raise BootstrapError(f"Vendor tool {identifier} has an unsafe validation executable")
            completed = run_command(command, check=False, timeout=60)
            output = (completed.stdout or completed.stderr).strip()
            expected = validation.get("version_contains", tool.get("catalog_version"))
            result["command"] = command
            result["command_output"] = output[:1000]
            result["command_ok"] = completed.returncode == 0 and (
                expected is None or str(expected) in output
            )
            result["ok"] = result["ok"] and result["command_ok"]
        results.append(result)
    return results


def validate_commands(profiles: Sequence[str]) -> list[dict[str, Any]]:
    results = []
    search_path = os.pathsep.join([os.fspath(TOOLCHAIN_BIN), SYSTEM_PATH])
    for profile in profiles:
        for command in PROFILE_COMMANDS.get(profile, ()):
            location = shutil.which(command, path=search_path)
            results.append(
                {
                    "profile": profile,
                    "command": command,
                    "package_hint": COMMAND_PACKAGE_HINTS.get(command),
                    "path": location,
                    "ok": location is not None,
                }
            )
    return results


def validate_pkg_config() -> list[dict[str, Any]]:
    results = []
    for library in ("libnvme", "libsystemd", "libudev"):
        completed = run_command(["pkg-config", "--exists", library], check=False, timeout=30)
        results.append({"library": library, "ok": completed.returncode == 0})
    return results


def reboot_status() -> dict[str, Any]:
    marker = pathlib.Path("/var/run/reboot-required")
    package_marker = pathlib.Path("/var/run/reboot-required.pkgs")
    packages: list[str] = []
    if package_marker.is_file():
        try:
            packages = sorted(
                {line.strip() for line in package_marker.read_text(encoding="utf-8").splitlines() if line.strip()}
            )
        except OSError as exc:
            return {
                "required": marker.is_file(),
                "packages": [],
                "packages_error": str(exc),
                "automatic_reboot": False,
            }
    return {
        "required": marker.is_file(),
        "packages": packages,
        "automatic_reboot": False,
    }


def write_report(report: Mapping[str, Any], destination: str | None, *, default_apply: bool) -> None:
    if destination == "-":
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    path_value = destination
    if path_value is None and default_apply:
        path_value = "/var/log/hoardarr/bootstrap-report.json"
    if path_value is None:
        return
    path = pathlib.Path(path_value)
    content = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    atomic_write(path, content, 0o640)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "plan", "apply", "validate"))
    parser.add_argument(
        "--profile",
        action="append",
        choices=(*PROFILE_MANIFESTS.keys(), "all"),
        help="repeatable; defaults to build-host",
    )
    parser.add_argument("--confirm-runtime-host", action="store_true", help="acknowledge runtime package installation")
    parser.add_argument("--yes", action="store_true", help="required for non-interactive apply")
    parser.add_argument("--no-apt-update", action="store_true", help="use current APT package indexes")
    parser.add_argument("--skip-network-check", action="store_true", help="allow an intentionally offline host")
    parser.add_argument("--hardware", choices=("auto", "none"), default="auto")
    parser.add_argument("--hardware-fixture", type=pathlib.Path, help="test detector against a recorded JSON fixture")
    parser.add_argument("--include-vendor-tools", action="store_true", help="install recommended, pinned public vendor tools")
    parser.add_argument(
        "--allow-missing-vendor-tools",
        action="store_true",
        help="explicitly continue when a recommended proprietary tool is manual or unsupported",
    )
    parser.add_argument(
        "--accept-vendor-license",
        action="append",
        default=[],
        metavar="TOOL_ID",
        help="repeat once for each selected proprietary tool",
    )
    parser.add_argument("--minimum-free-gib", type=int, default=10)
    parser.add_argument(
        "--refresh-runtime-baseline",
        action="store_true",
        help="explicitly accept current runtime unit state before a confirmed apply",
    )
    parser.add_argument("--report", help="write structured report to PATH, or '-' for stdout")
    parser.add_argument("--json", action="store_true", help="also print the structured report to stdout")
    return parser


def execute(args: argparse.Namespace, report: dict[str, Any]) -> None:
    profiles = expand_profiles(args.profile or [])
    runtime = bool(set(profiles) & RUNTIME_PROFILES)
    report["profiles"] = profiles
    report["runtime_profile"] = runtime
    if args.action == "apply" and not args.yes:
        raise BootstrapError("Apply requires --yes")
    if args.action == "apply" and runtime:
        raise BootstrapError(
            "Runtime profile apply is intentionally unavailable in this milestone. Storage "
            "packages can install udev rules that auto-assemble or claim attached disks; build "
            "and review a plan only, and keep physical data disks detached until Hoardarr has a "
            "boot-device-aware, deny-by-default autoactivation guard."
        )
    if runtime and not args.confirm_runtime_host:
        raise BootstrapError("Runtime profiles require --confirm-runtime-host")
    if args.include_vendor_tools and not runtime:
        raise BootstrapError("Vendor controller tools require a runtime profile")
    if args.allow_missing_vendor_tools and not args.include_vendor_tools:
        raise BootstrapError("--allow-missing-vendor-tools requires --include-vendor-tools")
    if args.refresh_runtime_baseline and (args.action != "apply" or not runtime):
        raise BootstrapError("--refresh-runtime-baseline requires a runtime apply")
    if args.hardware_fixture is not None and args.action == "apply":
        raise BootstrapError("--hardware-fixture is forbidden during apply")
    if args.include_vendor_tools and args.action in {"check", "plan"}:
        report.setdefault("warnings", []).append(
            "vendor tools are selected for a future apply; this action remains read-only"
        )
    if args.minimum_free_gib < 0:
        raise BootstrapError("--minimum-free-gib cannot be negative")

    report["system"] = preflight(
        apply=args.action == "apply",
        runtime=runtime,
        network=(args.action in {"check", "plan", "apply"} and not args.skip_network_check),
        minimum_free_gib=args.minimum_free_gib,
    )
    by_profile, packages = collect_profile_packages(profiles)
    report["package_manifests"] = by_profile
    versions = parse_versions(PACKAGE_ROOT / "versions.env")
    report["pinned_versions"] = versions

    hardware: dict[str, Any] = {"status": "disabled", "recommendations": {"packages": [], "vendor_tools": []}}
    if args.hardware == "auto":
        try:
            hardware = detect_hardware(args.hardware_fixture)
            hardware["status"] = "detected"
        except BootstrapError as exc:
            if runtime:
                raise
            report.setdefault("warnings", []).append(str(exc))
            hardware = {"status": "unavailable", "error": str(exc), "recommendations": {"packages": [], "vendor_tools": []}}
    report["hardware"] = hardware
    hardware_packages = hardware.get("recommendations", {}).get("packages", []) if runtime else []
    architecture_packages = (
        list(BUILD_BOOT_PACKAGES[report["system"]["architecture"]])
        if "build-host" in profiles
        else []
    )
    packages = sorted(set(packages) | set(hardware_packages) | set(architecture_packages))
    runtime_packages = sorted(
        {
            package
            for profile, profile_packages in by_profile.items()
            if profile in RUNTIME_PROFILES
            for package in profile_packages
        }
        | set(hardware_packages)
    )
    report["packages"] = {
        "requested": packages,
        "runtime_subset": runtime_packages,
        "hardware_added": sorted(set(hardware_packages)),
        "architecture_added": architecture_packages,
    }

    vendor_plan = resolve_vendor_tools(hardware, report["system"]["architecture"])
    report["vendor_tools"] = vendor_plan
    if args.include_vendor_tools:
        validate_vendor_plan_conflicts(vendor_plan)
        accepted = set(args.accept_vendor_license)
        known_ids = {str(tool["id"]) for tool in vendor_plan}
        unknown_acceptance = sorted(accepted - known_ids)
        if unknown_acceptance:
            raise BootstrapError(
                f"License acceptance was supplied for an unrecommended tool: {', '.join(unknown_acceptance)}"
            )
        unavailable_vendor = [
            {"id": tool["id"], "reason": tool.get("reason")}
            for tool in vendor_plan
            if not tool.get("available")
        ]
        report["missing_vendor_tools"] = unavailable_vendor
        if unavailable_vendor and not args.allow_missing_vendor_tools:
            names = ", ".join(str(item["id"]) for item in unavailable_vendor)
            raise BootstrapError(
                f"Recommended vendor tools need manual/unsupported installation: {names}; "
                "review the plan or explicitly use --allow-missing-vendor-tools"
            )
        if unavailable_vendor:
            report["warnings"].append(
                "Explicitly continuing without recommended vendor tools: "
                + ", ".join(str(item["id"]) for item in unavailable_vendor)
            )
        license_missing = sorted(
            str(tool["id"])
            for tool in vendor_plan
            if tool.get("available")
            and tool.get("license_required")
            and str(tool["id"]) not in accepted
        )
        if license_missing:
            raise BootstrapError(
                "Vendor license acceptance is required before mutation: " + ", ".join(license_missing)
            )

    installed = installed_packages(packages)
    missing = sorted(package for package in packages if package not in installed)
    report["packages"]["installed"] = installed
    report["packages"]["missing"] = missing

    if args.action in {"plan", "apply"}:
        candidates = apt_candidates(packages)
        report["packages"]["candidates"] = candidates
        reconciliation = reconcile_package_versions(packages, installed, candidates)
        report["packages"]["reconciliation"] = reconciliation
        report["packages"]["outdated"] = reconciliation["outdated"]
        selected_holds = sorted(set(packages) & set(held_packages()))
        report["packages"]["held"] = selected_holds
        blocking_holds = blocking_held_packages(reconciliation, selected_holds)
        report["packages"]["blocking_held"] = blocking_holds
        if blocking_holds and args.action == "plan":
            raise BootstrapError(
                "Packages that require installation or upgrade are held; the bootstrap will not "
                "unhold them: "
                + ", ".join(blocking_holds)
            )
        unresolved = reconciliation["unresolved_missing"]
        if unresolved and args.action == "plan":
            raise BootstrapError(f"No APT candidate for: {', '.join(unresolved)}")
        if unresolved:
            report["packages"]["simulation"] = {
                "ok": None,
                "summary": "deferred until APT indexes are refreshed during apply",
                "unresolved": unresolved,
            }
        else:
            report["packages"]["simulation"] = simulate_packages(
                reconciliation["transaction_packages"]
            )

    if "build-host" in profiles:
        report["toolchains"] = inspect_toolchains(versions)

    if args.action == "check":
        return

    if args.action == "plan":
        return

    if args.action == "apply":
        changes: list[str] = []
        report["changes"] = changes
        with installer_lock():
            recovered = _restore_policy_from_state()
            if recovered:
                changes.append("recovered stale policy-rc.d guard")
            existing_baseline = load_runtime_baseline(required=False) if runtime else None
            before_unit_names = (
                sorted(
                    set(RUNTIME_UNITS)
                    | set(package_systemd_units(runtime_packages))
                    | set(existing_baseline["units"] if existing_baseline else {})
                )
                if runtime
                else []
            )
            before_units = unit_state(before_unit_names) if runtime else {}
            if runtime:
                report["runtime_units_before"] = before_units
                preexisting_drift = require_runtime_baseline_match(
                    existing_baseline,
                    before_units,
                    refresh_requested=args.refresh_runtime_baseline,
                )
                report["runtime_preflight_drift"] = preexisting_drift
                if args.refresh_runtime_baseline:
                    baseline, baseline_diff, baseline_changed = refresh_runtime_baseline(before_units)
                    report["runtime_baseline_refresh"] = {
                        "explicit": True,
                        "differences": baseline_diff,
                        "changed": baseline_changed,
                    }
                else:
                    baseline, baseline_changed = ensure_runtime_baseline(before_units)
                report["runtime_service_baseline"] = {
                    "path": os.fspath(RUNTIME_BASELINE),
                    "captured_at": baseline.get("captured_at"),
                    "updated_at": baseline.get("updated_at"),
                    "tracked_units": len(baseline["units"]),
                    "changed": baseline_changed,
                }
                if baseline_changed:
                    changes.append(
                        "explicitly refreshed runtime service baseline"
                        if args.refresh_runtime_baseline
                        else "recorded runtime service baseline"
                    )
            all_packages_before = all_installed_packages()
            simulated_transaction_packages: set[str] = set()

            def update_baseline_after_capture() -> None:
                if not runtime:
                    return
                current_baseline, baseline_extended = ensure_runtime_baseline(before_units)
                if baseline_extended and not any(
                    "runtime service baseline" in item for item in changes
                ):
                    changes.append("recorded runtime service baseline")
                report["runtime_service_baseline"].update(
                    {
                        "updated_at": current_baseline.get("updated_at"),
                        "tracked_units": len(current_baseline["units"]),
                        "changed": report["runtime_service_baseline"]["changed"]
                        or baseline_extended,
                    }
                )

            def before_package_mutation(transaction: Sequence[str]) -> None:
                simulated_transaction_packages.update(transaction)
                if runtime:
                    capture_preexisting_package_units(before_units, transaction)
                    update_baseline_after_capture()

            def after_package_mutation(transaction: Sequence[str]) -> None:
                simulated_transaction_packages.update(transaction)
                if runtime:
                    seed_post_transaction_units(before_units, transaction)

            try:
                if not args.no_apt_update:
                    run_command([*apt_base(), "update"], env=apt_environment(), timeout=900)
                    report["apt_indexes_refreshed"] = True
                else:
                    report["apt_indexes_refreshed"] = False

                refreshed_installed = installed_packages(packages)
                refreshed_candidates = apt_candidates(packages)
                refreshed_holds = sorted(set(packages) & set(held_packages()))
                refreshed_reconciliation = reconcile_package_versions(
                    packages, refreshed_installed, refreshed_candidates
                )
                report["packages"]["installed"] = refreshed_installed
                report["packages"]["candidates"] = refreshed_candidates
                report["packages"]["held"] = refreshed_holds
                report["packages"]["reconciliation"] = refreshed_reconciliation
                report["packages"]["missing"] = refreshed_reconciliation["missing"]
                report["packages"]["outdated"] = refreshed_reconciliation["outdated"]
                transaction_packages = refreshed_reconciliation["transaction_packages"]
                blocking_holds = blocking_held_packages(
                    refreshed_reconciliation, refreshed_holds
                )
                report["packages"]["blocking_held"] = blocking_holds
                if blocking_holds:
                    raise BootstrapError(
                        "Packages that require installation or upgrade are held; the bootstrap "
                        "will not unhold them: "
                        + ", ".join(blocking_holds)
                    )
                unresolved = refreshed_reconciliation["unresolved_missing"]
                if unresolved:
                    raise BootstrapError(f"No APT candidate for: {', '.join(unresolved)}")
                report["packages"]["expected_after_apply"] = {
                    package: refreshed_candidates[package]
                    for package in transaction_packages
                    if refreshed_candidates.get(package) is not None
                }
                package_simulation = simulate_packages(transaction_packages)
                report["packages"]["simulation"] = package_simulation
                simulated_delta = package_simulation.get("install_packages", [])
                before_package_mutation(simulated_delta)

                if transaction_packages or args.include_vendor_tools:
                    with inhibit_service_starts() as policy:
                        report["policy_rc_d"] = policy
                        if transaction_packages:
                            try:
                                run_command(
                                    [
                                        *apt_base(),
                                        "install",
                                        "-y",
                                        "--no-remove",
                                        "--no-install-recommends",
                                        *transaction_packages,
                                    ],
                                    env=apt_environment(),
                                    timeout=3600,
                                )
                            finally:
                                after_package_mutation(simulated_delta)
                            missing_set = set(refreshed_reconciliation["missing"])
                            outdated_map = {
                                item["package"]: item
                                for item in refreshed_reconciliation["outdated"]
                            }
                            for package in transaction_packages:
                                if package in missing_set:
                                    changes.append(f"installed package {package}")
                                elif package in outdated_map:
                                    item = outdated_map[package]
                                    changes.append(
                                        f"upgraded package {package} {item['installed']} -> {item['candidate']}"
                                    )
                        if args.include_vendor_tools:
                            report["vendor_install"] = install_vendor_tools(
                                vendor_plan,
                                set(args.accept_vendor_license),
                                changes,
                                before_package_mutation=before_package_mutation,
                                after_package_mutation=after_package_mutation,
                            )
                if "build-host" in profiles:
                    changes.extend(install_toolchains(versions, report["system"]["architecture"]))
            finally:
                mutation_failed = sys.exc_info()[0] is not None
                audit_errors: list[str] = []
                try:
                    all_packages_after = all_installed_packages()
                    package_delta = installed_package_delta(all_packages_before, all_packages_after)
                    report["packages"]["actual_dpkg_delta"] = package_delta
                    delta_names = [item["package"] for item in package_delta["added"]]
                    delta_names.extend(item["package"] for item in package_delta["changed"])
                    expected_delta_names = sorted(simulated_transaction_packages)
                    unexpected_delta_names = unexpected_dpkg_names(
                        package_delta, sorted(simulated_transaction_packages)
                    )
                    report["packages"]["simulated_dpkg_names"] = expected_delta_names
                    report["packages"]["unexpected_dpkg_names"] = unexpected_delta_names
                    if package_delta["removed"]:
                        audit_errors.append(
                            "Package transaction unexpectedly removed packages; inspect actual_dpkg_delta"
                        )
                    if unexpected_delta_names:
                        audit_errors.append(
                            "Package transaction changed packages outside every simulated Inst set: "
                            + ", ".join(unexpected_delta_names)
                        )
                    if runtime:
                        seed_post_transaction_units(
                            before_units,
                            sorted(simulated_transaction_packages | set(delta_names)),
                        )
                        update_baseline_after_capture()
                        safety_changes = restore_runtime_unit_safety(before_units)
                        report["runtime_service_safety_changes"] = safety_changes
                        changes.extend(
                            f"{item['action']} for {item['unit']}" for item in safety_changes
                        )
                    if audit_errors:
                        raise BootstrapError("; ".join(audit_errors))
                except Exception as exc:
                    if audit_errors:
                        report["package_transaction_audit_error"] = str(exc)
                    elif runtime:
                        report["runtime_service_safety_error"] = str(exc)
                    else:
                        report["package_transaction_audit_error"] = str(exc)
                    if not mutation_failed:
                        raise

    # Apply and validate share the same evidence-based final validation.
    installed_after = installed_packages(packages)
    missing_after = sorted(package for package in packages if package not in installed_after)
    command_checks = validate_commands(profiles)
    validation: dict[str, Any] = {
        "packages_missing": missing_after,
        "commands": command_checks,
        "commands_missing": [item["command"] for item in command_checks if not item["ok"]],
    }
    expected_versions = report.get("packages", {}).get("expected_after_apply", {})
    package_version_failures = []
    if isinstance(expected_versions, Mapping):
        for package, expected in sorted(expected_versions.items()):
            actual = installed_after.get(package)
            if not isinstance(expected, str):
                continue
            if actual is None or compare_debian_versions(actual, "lt", expected):
                package_version_failures.append(
                    {"package": package, "expected_at_least": expected, "installed": actual}
                )
    validation["package_version_failures"] = package_version_failures
    if "build-host" in profiles:
        validation["toolchains"] = inspect_toolchains(versions)
        validation["pkg_config"] = validate_pkg_config()
    audit = run_command(["dpkg", "--audit"], check=False, timeout=60)
    apt_check = run_command(["apt-get", "--simulate", "check"], check=False, timeout=180)
    validation["dpkg_audit_clean"] = audit.returncode == 0 and not (audit.stdout + audit.stderr).strip()
    validation["apt_dependencies_clean"] = apt_check.returncode == 0
    report["validation"] = validation
    if runtime:
        baseline = load_runtime_baseline(required=True)
        assert baseline is not None
        baseline_units = baseline["units"]
        required_unit_names = set(RUNTIME_UNITS) | set(package_systemd_units(runtime_packages))
        untracked_units = sorted(required_unit_names - baseline_units.keys())
        observed_units = unit_state(sorted(required_unit_names | baseline_units.keys()))
        validation["runtime_units"] = observed_units
        validation["runtime_service_baseline"] = {
            "path": os.fspath(RUNTIME_BASELINE),
            "captured_at": baseline.get("captured_at"),
            "updated_at": baseline.get("updated_at"),
            "tracked_units": len(baseline_units),
            "untracked_units": untracked_units,
        }
        validation["runtime_baseline_violations"] = runtime_state_differences(
            baseline_units, observed_units
        )
        transaction_before = report.get("runtime_units_before")
        validation["runtime_transaction_violations"] = (
            runtime_state_differences(transaction_before, observed_units)
            if isinstance(transaction_before, Mapping)
            else []
        )
        validation["vendor_tools"] = validate_vendor_tools(vendor_plan)
    report["reboot"] = reboot_status()
    report["validation"] = validation
    toolchains_ok = all(item["ok"] for item in validation.get("toolchains", {}).values())
    pkg_config_ok = all(item["ok"] for item in validation.get("pkg_config", []))
    runtime_safety_ok = (
        not validation.get("runtime_baseline_violations", [])
        and not validation.get("runtime_transaction_violations", [])
        and not validation.get("runtime_service_baseline", {}).get("untracked_units", [])
    )
    vendor_ok = True
    if args.include_vendor_tools and args.action in {"apply", "validate"}:
        vendor_ok = all(
            item["ok"]
            for item in validation.get("vendor_tools", [])
            if next((tool.get("available") for tool in vendor_plan if tool["id"] == item["id"]), False)
        )
    if missing_after or validation["package_version_failures"] or validation["commands_missing"] or not validation["dpkg_audit_clean"] or not validation["apt_dependencies_clean"] or not toolchains_ok or not pkg_config_ok or not runtime_safety_ok or not vendor_ok:
        raise BootstrapError(
            "Validation failed; inspect the structured report. Runtime services are never "
            "auto-started to repair drift; restore the intended state manually or explicitly "
            "refresh the confirmed baseline on a later apply."
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "action": args.action,
        "status": "running",
        "started_at": utc_now(),
        "warnings": [],
    }
    exit_code = 0
    try:
        execute(args, report)
        report["status"] = "success"
    except BootstrapError as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        eprint(f"ERROR: {exc}")
        exit_code = 2
    except KeyboardInterrupt:
        report["status"] = "cancelled"
        report["error"] = "interrupted"
        eprint("ERROR: interrupted")
        exit_code = 130
    except Exception as exc:  # preserve a structured result for unexpected faults
        report["status"] = "failed"
        report["error"] = f"unexpected {type(exc).__name__}: {exc}"
        eprint(f"ERROR: {report['error']}")
        exit_code = 1
    finally:
        report["finished_at"] = utc_now()
        try:
            write_report(report, args.report, default_apply=args.action == "apply")
        except Exception as exc:
            report["report_write_error"] = str(exc)
            report["status"] = "failed"
            report.setdefault("error", "could not write the structured report")
            eprint(f"ERROR: could not write report: {exc}")
            exit_code = exit_code or 1
        if args.json and args.report != "-":
            print(json.dumps(report, indent=2, sort_keys=True))
    if not args.json and args.report != "-":
        package_data = report.get("packages", {})
        initially_missing = len(package_data.get("missing", []))
        remaining_missing = len(
            report.get("validation", {}).get(
                "packages_missing", package_data.get("missing", [])
            )
        )
        changes = len(report.get("changes", []))
        print(
            f"Hoardarr bootstrap {report['action']}: {report['status']} "
            f"({remaining_missing} packages remaining, {initially_missing} before the transaction, "
            f"{changes} changes)"
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
