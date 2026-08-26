#!/usr/bin/env bash
# Install or upgrade Hoardarr from a verified, versioned release bundle.

set -Eeuo pipefail
IFS=$'\n\t'

PROGRAM_NAME="$(basename -- "$0")"
BUNDLE_ROOT="$(unset CDPATH; cd -- "$(dirname -- "$0")/.." && pwd -P)"
readonly PROGRAM_NAME BUNDLE_ROOT
readonly LIB_ROOT="/usr/lib/hoardarr"
readonly RELEASES_ROOT="${LIB_ROOT}/releases"
readonly STATE_ROOT="/var/lib/hoardarr"
readonly CONFIG_ROOT="/etc/hoardarr"
readonly CONFIG_FILE="${CONFIG_ROOT}/hoardarr.env"
readonly DOC_ROOT="/usr/share/doc/hoardarr"
readonly UNIT_ROOT="/etc/systemd/system"
readonly LLDPD_DROPIN_ROOT="${UNIT_ROOT}/lldpd.service.d"
readonly CLI_ROOT="/usr/local/bin"
readonly CLI_LINK="${CLI_ROOT}/hoardarr"
readonly QUARANTINE_CLI_LINK="/usr/local/sbin/hoardarr-storage-quarantine"
readonly RUNTIME_ROOT="/run/hoardarr"
readonly INSTALL_LOCK_PATH="${RUNTIME_ROOT}/release.lock"

ACTION="plan"
CONFIRMED="false"
PRESERVE_EXISTING_LOGIN_ACCOUNT="false"
DEFER_SERVICE_START="false"
STAGE_DIR=""
INSTALL_LOCK_FD=""

log() {
    printf '%s\n' "$*"
}

die() {
    printf 'error: %s\n' "$*" >&2
    exit 2
}

usage() {
    cat <<EOF
Usage:
  ${PROGRAM_NAME} plan
  sudo ${PROGRAM_NAME} apply --yes [--preserve-existing-login-account] [--defer-service-start]

The default and "plan" modes are read-only.  "apply" requires root and the
explicit --yes confirmation. Apply installs the mandatory storage and connectivity
host packages when they are missing. It does not issue an owner setup token or change
the loopback-only default listener. The account-preservation option is intended only
for legacy development hosts where "hoardarr" is already the administrator login.
The service-deferral option is only for a first appliance installation performed
inside an offline installer target. Units are enabled and start normally on boot.
EOF
}

cleanup_stage() {
    if [[ -n "${STAGE_DIR}" && -d "${STAGE_DIR}" ]]; then
        case "${STAGE_DIR}" in
            "${RELEASES_ROOT}"/.stage-*) rm -rf --one-file-system -- "${STAGE_DIR}" ;;
            *) printf 'warning: refusing to clean unexpected staging path: %s\n' "${STAGE_DIR}" >&2 ;;
        esac
    fi
}
trap cleanup_stage EXIT

parse_args() {
    if (($# > 0)); then
        ACTION="$1"
        shift
    fi
    case "${ACTION}" in
        plan)
            (($# == 0)) || die "plan accepts no additional arguments"
            ;;
        apply)
            while (($# > 0)); do
                case "$1" in
                    --yes) CONFIRMED="true" ;;
                    --preserve-existing-login-account)
                        PRESERVE_EXISTING_LOGIN_ACCOUNT="true"
                        ;;
                    --defer-service-start)
                        DEFER_SERVICE_START="true"
                        ;;
                    *) die "unknown apply option: $1" ;;
                esac
                shift
            done
            [[ "${CONFIRMED}" == "true" ]] || die "apply requires --yes"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            die "unknown action: ${ACTION}"
            ;;
    esac
}

require_commands() {
    local command_name
    for command_name in \
        python3 systemctl apt-get dpkg-query getent id install useradd usermod awk chmod chown curl find flock grep ln mv mktemp ps readlink sleep stat tr
    do
        command -v "${command_name}" >/dev/null 2>&1 || die "required command is missing: ${command_name}"
    done
}

acquire_install_lock() {
    if [[ ( -e "${RUNTIME_ROOT}" || -L "${RUNTIME_ROOT}" ) && \
        ( ! -d "${RUNTIME_ROOT}" || -L "${RUNTIME_ROOT}" ) ]]; then
        die "installer runtime path is not a real directory: ${RUNTIME_ROOT}"
    fi
    install -d -o root -g root -m 0755 "${RUNTIME_ROOT}"
    [[ "$(stat -c '%u:%g:%a' "${RUNTIME_ROOT}")" == "0:0:755" ]] || \
        die "installer runtime directory has unsafe ownership or mode"
    if [[ ( -e "${INSTALL_LOCK_PATH}" || -L "${INSTALL_LOCK_PATH}" ) && \
        ( ! -f "${INSTALL_LOCK_PATH}" || -L "${INSTALL_LOCK_PATH}" ) ]]; then
        die "installer lock path is not a regular file: ${INSTALL_LOCK_PATH}"
    fi
    exec {INSTALL_LOCK_FD}>"${INSTALL_LOCK_PATH}"
    flock --exclusive --nonblock "${INSTALL_LOCK_FD}" || \
        die "another Hoardarr release installation is already running"
}

verify_bundle() {
    python3 - "${BUNDLE_ROOT}" <<'PY'
import hashlib
import os
import re
import sys
from pathlib import Path, PurePosixPath

root = Path(sys.argv[1]).resolve()
manifest = root / "SHA256SUMS"
if not manifest.is_file() or manifest.is_symlink():
    raise SystemExit("error: SHA256SUMS is missing or is not a regular file")

expected = {}
pattern = re.compile(r"^([0-9a-f]{64})  ([^\n]+)$")
for number, raw_line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
    match = pattern.fullmatch(raw_line)
    if not match:
        raise SystemExit(f"error: malformed SHA256SUMS line {number}")
    digest, name = match.groups()
    if not name or "\\" in name or any(ord(char) < 32 for char in name):
        raise SystemExit(f"error: unsafe manifest path on line {number}")
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise SystemExit(f"error: unsafe manifest path on line {number}")
    if str(relative) != name or name == "SHA256SUMS" or name in expected:
        raise SystemExit(f"error: duplicate, reserved, or non-canonical path on line {number}")
    candidate = root.joinpath(*relative.parts)
    if candidate.is_symlink() or not candidate.is_file():
        raise SystemExit(f"error: manifest entry is missing or not regular: {name}")
    try:
        candidate.resolve().relative_to(root)
    except ValueError:
        raise SystemExit(f"error: manifest entry escapes bundle: {name}") from None
    expected[name] = digest

if not expected:
    raise SystemExit("error: release manifest is empty")

actual = set()
for directory, directory_names, file_names in os.walk(root, followlinks=False):
    directory_path = Path(directory)
    for item in directory_names:
        if (directory_path / item).is_symlink():
            raise SystemExit(f"error: symbolic links are not allowed: {directory_path / item}")
    for item in file_names:
        path = directory_path / item
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"error: non-regular bundle entry: {path}")
        name = path.relative_to(root).as_posix()
        if name != "SHA256SUMS":
            actual.add(name)

missing = set(expected) - actual
extra = actual - set(expected)
if missing or extra:
    detail = []
    if missing:
        detail.append("missing=" + ",".join(sorted(missing)))
    if extra:
        detail.append("unmanifested=" + ",".join(sorted(extra)))
    raise SystemExit("error: bundle file set mismatch: " + " ".join(detail))

for name, wanted in sorted(expected.items()):
    digest = hashlib.sha256()
    with (root / name).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != wanted:
        raise SystemExit(f"error: SHA-256 mismatch: {name}")
PY
}

load_release_metadata() {
    local -a fields
    mapfile -t fields < <(python3 - "${BUNDLE_ROOT}/RELEASE.json" <<'PY'
import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file() or path.is_symlink():
    raise SystemExit("error: RELEASE.json is missing or not regular")
try:
    value = json.loads(path.read_text(encoding="utf-8"))
    target = value["target"]
    fields = (
        value["version"],
        value["release_id"],
        target["os_id"],
        target["os_version"],
        target["architecture"],
        target["python"],
    )
except (KeyError, TypeError, ValueError) as exc:
    raise SystemExit(f"error: invalid RELEASE.json: {exc}") from None
safe = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
if value.get("schema") != 1 or value.get("name") != "hoardarr":
    raise SystemExit("error: unsupported release metadata")
if any(not isinstance(item, str) or not safe.fullmatch(item) for item in fields):
    raise SystemExit("error: unsafe release metadata value")
print(*fields, sep="\n")
PY
    )
    ((${#fields[@]} == 6)) || die "could not read release metadata"
    RELEASE_VERSION="${fields[0]}"
    RELEASE_ID="${fields[1]}"
    TARGET_OS_ID="${fields[2]}"
    TARGET_OS_VERSION="${fields[3]}"
    TARGET_ARCH="${fields[4]}"
    TARGET_PYTHON="${fields[5]}"
    RELEASE_PATH="${RELEASES_ROOT}/${RELEASE_ID}"
}

validate_release_layout() {
    local required
    local -a required_files=(
        "requirements/runtime.lock"
        "requirements/hoardarr.lock"
        "config/hoardarr.env"
        "scripts/bootstrap.py"
        "scripts/detect-hardware.py"
        "packages/appliance-core.txt"
        "packages/storage-services.txt"
        "packages/tiered-storage.txt"
        "packages/versions.env"
        "systemd/hoardarr-api.service"
        "systemd/hoardarr-worker.service"
        "systemd/hoardarr-migrate.service"
        "systemd/hoardarr-account-executor.service"
        "systemd/hoardarr-storage-executor.service"
        "systemd/hoardarr-storage-status.service"
        "systemd/hoardarr-lldpd.conf"
        "docs/backend.md"
        "frontend/index.html"
    )
    for required in "${required_files[@]}"; do
        [[ -f "${BUNDLE_ROOT}/${required}" && ! -L "${BUNDLE_ROOT}/${required}" ]] || \
            die "required bundle file is missing: ${required}"
    done
    grep -Fxq "mergerfs" "${BUNDLE_ROOT}/packages/appliance-core.txt" || \
        die "mandatory package is missing from appliance-core.txt: mergerfs"
    grep -Fxq "attr" "${BUNDLE_ROOT}/packages/appliance-core.txt" || \
        die "mandatory package is missing from appliance-core.txt: attr"
    grep -Fxq "samba" "${BUNDLE_ROOT}/packages/appliance-core.txt" || \
        die "mandatory package is missing from appliance-core.txt: samba"
    grep -Fxq "lldpd" "${BUNDLE_ROOT}/packages/appliance-core.txt" || \
        die "mandatory package is missing from appliance-core.txt: lldpd"
    grep -Fxq "snapraid" "${BUNDLE_ROOT}/packages/appliance-core.txt" || \
        die "mandatory package is missing from appliance-core.txt: snapraid"
    compgen -G "${BUNDLE_ROOT}/wheels/*.whl" >/dev/null || die "bundle wheelhouse is empty"
    compgen -G "${BUNDLE_ROOT}/hardware/*.json" >/dev/null || die "bundle hardware manifests are empty"
}

host_os_field() {
    local wanted="$1"
    python3 - "${wanted}" <<'PY'
import sys
from pathlib import Path

wanted = sys.argv[1]
values = {}
for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
print(values.get(wanted, ""))
PY
}

validate_host() {
    [[ -r /etc/os-release ]] || die "/etc/os-release is unavailable"
    local host_id host_version host_arch host_python
    host_id="$(host_os_field ID)"
    host_version="$(host_os_field VERSION_ID)"
    if command -v dpkg >/dev/null 2>&1; then
        host_arch="$(dpkg --print-architecture)"
    else
        host_arch="$(uname -m)"
        [[ "${host_arch}" == "x86_64" ]] && host_arch="amd64"
    fi
    host_python="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    [[ "${host_id}" == "${TARGET_OS_ID}" ]] || \
        die "bundle requires ${TARGET_OS_ID}; host reports ${host_id:-unknown}"
    [[ "${host_version}" == "${TARGET_OS_VERSION}" ]] || \
        die "bundle requires Ubuntu ${TARGET_OS_VERSION}; host reports ${host_version:-unknown}"
    [[ "${host_arch}" == "${TARGET_ARCH}" ]] || \
        die "bundle requires ${TARGET_ARCH}; host reports ${host_arch:-unknown}"
    [[ "${host_python}" == "${TARGET_PYTHON}" ]] || \
        die "bundle requires Python ${TARGET_PYTHON}; host reports ${host_python}"
    python3 -c 'import venv' >/dev/null 2>&1 || die "Python venv support is unavailable"
    if [[ "${DEFER_SERVICE_START}" == "true" ]]; then
        log "Validating an offline appliance target; runtime PID 1 checks are deferred to first boot."
    else
        [[ "$(ps -p 1 -o comm= | tr -d '[:space:]')" == "systemd" ]] || \
            die "systemd must be PID 1"
    fi
}

validate_destination_paths() {
    local path
    for path in "${LIB_ROOT}" "${RELEASES_ROOT}" "${STATE_ROOT}" "${CONFIG_ROOT}" "${DOC_ROOT}"; do
        if [[ ( -e "${path}" || -L "${path}" ) && ( ! -d "${path}" || -L "${path}" ) ]]; then
            die "existing managed directory is not a real directory: ${path}"
        fi
    done
    for path in \
        "${LIB_ROOT}/current" \
        "${LIB_ROOT}/venv" \
        "${LIB_ROOT}/scripts" \
        "${LIB_ROOT}/packaging" \
        "${LIB_ROOT}/hardware"
    do
        if [[ -e "${path}" && ! -L "${path}" ]]; then
            die "refusing to replace non-symbolic-link path: ${path}"
        fi
    done
    if [[ -e "${CLI_LINK}" && ! -L "${CLI_LINK}" ]] && \
        ! runtime_wrapper_is_managed "${CLI_LINK}" cli; then
        die "refusing to replace non-symbolic-link path: ${CLI_LINK}"
    fi
    if [[ -e "${QUARANTINE_CLI_LINK}" && ! -L "${QUARANTINE_CLI_LINK}" ]] && \
        ! runtime_wrapper_is_managed "${QUARANTINE_CLI_LINK}" storage-quarantine; then
        die "refusing to replace non-symbolic-link path: ${QUARANTINE_CLI_LINK}"
    fi
    if [[ ( -e "${CONFIG_FILE}" || -L "${CONFIG_FILE}" ) && \
        ( ! -f "${CONFIG_FILE}" || -L "${CONFIG_FILE}" ) ]]; then
        die "existing configuration is not a regular file: ${CONFIG_FILE}"
    fi
    for path in hoardarr-api.service hoardarr-worker.service hoardarr-migrate.service hoardarr-account-executor.service hoardarr-storage-executor.service; do
        if [[ ( -e "${UNIT_ROOT}/${path}" || -L "${UNIT_ROOT}/${path}" ) && \
            ( ! -f "${UNIT_ROOT}/${path}" || -L "${UNIT_ROOT}/${path}" ) ]]; then
            die "existing unit destination is not a regular file: ${UNIT_ROOT}/${path}"
        fi
    done
    if [[ ( -e "${LLDPD_DROPIN_ROOT}/hoardarr.conf" || -L "${LLDPD_DROPIN_ROOT}/hoardarr.conf" ) && \
        ( ! -f "${LLDPD_DROPIN_ROOT}/hoardarr.conf" || -L "${LLDPD_DROPIN_ROOT}/hoardarr.conf" ) ]]; then
        die "existing LLDP/CDP service configuration is not a regular file"
    fi
}

runtime_wrapper_is_managed() {
    local path="$1"
    local command_name="$2"
    local expected
    [[ -f "${path}" && ! -L "${path}" ]] || return 1
    [[ "$(stat -c '%u:%g:%a' "${path}")" == "0:0:755" ]] || return 1
    expected="$(printf '%s\n' \
        '#!/bin/sh' \
        "exec /usr/lib/hoardarr/venv/bin/python -m hoardarr.runtime ${command_name} \"\$@\"")"
    [[ "$(<"${path}")" == "${expected}" ]]
}

manifest_digest() {
    python3 - "${BUNDLE_ROOT}/SHA256SUMS" <<'PY'
import hashlib
import sys
from pathlib import Path
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
}

show_plan() {
    local config_action="create from loopback-only default"
    [[ -e "${CONFIG_FILE}" ]] && config_action="preserve existing file byte-for-byte"
    cat <<EOF
Verified release: ${RELEASE_ID} (Hoardarr ${RELEASE_VERSION})
Target: Ubuntu ${TARGET_OS_VERSION} ${TARGET_ARCH}, Python ${TARGET_PYTHON}

Plan (no changes made):
  - install and verify the mandatory Ubuntu package "mergerfs" when missing
  - install and verify filesystem attribute tools used for live mergerFS expansion
  - install and verify Samba account tools for Windows file-access credentials
  - install and enable LLDP/CDP neighbor discovery for live network topology
  - create or validate locked system account "hoardarr"
  - stage an offline virtual environment under ${RELEASE_PATH}
  - install Python packages only from the verified bundled wheelhouse
  - install the verified, prebuilt web interface without contacting npm
  - switch ${LIB_ROOT}/current only after staging succeeds
  - install the root-owned detector, dependency reconciler, package/hardware
    manifests, units, and documentation
  - configuration: ${config_action}
  - stop runtime services, reload units, enable services, and restart migration
  - start runtime services only if migration succeeds

No package is installed during plan mode. Apply may contact configured Ubuntu
repositories for mergerfs. The installer does not issue a setup token, attach
or modify storage, or configure a non-loopback listener.
EOF
}

ensure_mergerfs() {
    if ! dpkg-query -W -f='${db:Status-Abbrev}' mergerfs 2>/dev/null | grep -q '^ii'; then
        log "Installing mandatory host package: mergerfs"
        apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=5 update
        DEBIAN_FRONTEND=noninteractive apt-get \
            -o DPkg::Lock::Timeout=120 \
            -o Acquire::Retries=5 \
            install --yes --no-install-recommends mergerfs
    fi
    dpkg-query -W -f='${db:Status-Abbrev}' mergerfs 2>/dev/null | grep -q '^ii' || \
        die "mandatory package is not installed: mergerfs"
    command -v mergerfs >/dev/null 2>&1 || \
        die "mandatory command is unavailable after package installation: mergerfs"
    mergerfs --version >/dev/null 2>&1 || \
        die "mandatory command failed its version check: mergerfs"
}

ensure_filesystem_attribute_tools() {
    if ! dpkg-query -W -f='${db:Status-Abbrev}' attr 2>/dev/null | grep -q '^ii'; then
        log "Installing mandatory host package: attr"
        apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=5 update
        DEBIAN_FRONTEND=noninteractive apt-get \
            -o DPkg::Lock::Timeout=120 \
            -o Acquire::Retries=5 \
            install --yes --no-install-recommends attr
    fi
    dpkg-query -W -f='${db:Status-Abbrev}' attr 2>/dev/null | grep -q '^ii' || \
        die "mandatory package is not installed: attr"
    local command_name
    for command_name in getfattr setfattr; do
        command -v "${command_name}" >/dev/null 2>&1 || \
            die "mandatory command is unavailable after package installation: ${command_name}"
    done
}

ensure_account_tools() {
    if ! dpkg-query -W -f='${db:Status-Abbrev}' samba 2>/dev/null | grep -q '^ii'; then
        log "Installing mandatory host package: samba"
        apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=5 update
        DEBIAN_FRONTEND=noninteractive apt-get \
            -o DPkg::Lock::Timeout=120 \
            -o Acquire::Retries=5 \
            install --yes --no-install-recommends samba
    fi
    dpkg-query -W -f='${db:Status-Abbrev}' samba 2>/dev/null | grep -q '^ii' || \
        die "mandatory package is not installed: samba"
    local command_name
    for command_name in smbpasswd pdbedit; do
        command -v "${command_name}" >/dev/null 2>&1 || \
            die "mandatory command is unavailable after package installation: ${command_name}"
    done
}

ensure_neighbor_discovery() {
    if ! dpkg-query -W -f='${db:Status-Abbrev}' lldpd 2>/dev/null | grep -q '^ii'; then
        log "Installing mandatory host package: lldpd"
        apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=5 update
        DEBIAN_FRONTEND=noninteractive apt-get \
            -o DPkg::Lock::Timeout=120 \
            -o Acquire::Retries=5 \
            install --yes --no-install-recommends lldpd
    fi
    dpkg-query -W -f='${db:Status-Abbrev}' lldpd 2>/dev/null | grep -q '^ii' || \
        die "mandatory package is not installed: lldpd"
    command -v lldpcli >/dev/null 2>&1 || \
        die "mandatory command is unavailable after package installation: lldpcli"
}

ensure_connectivity_tools() {
    local package_name missing="false"
    local kernel_extras="linux-modules-extra-$(uname -r)"
    local -a packages=(fcoe-utils lldpad "${kernel_extras}" netplan.io nftables nfs-common nfs-kernel-server open-iscsi rsyslog samba-common-bin samba-vfs-modules snmp snmpd targetcli-fb winbind)
    for package_name in "${packages[@]}"; do
        if ! dpkg-query -W -f='${db:Status-Abbrev}' "${package_name}" 2>/dev/null | grep -q '^ii'; then
            missing="true"
        fi
    done
    if [[ "${missing}" == "true" ]]; then
        log "Installing connectivity packages"
        apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=5 update
        DEBIAN_FRONTEND=noninteractive apt-get \
            -o DPkg::Lock::Timeout=120 \
            -o Acquire::Retries=5 \
            install --yes --no-install-recommends "${packages[@]}"
    fi
    for package_name in "${packages[@]}"; do
        dpkg-query -W -f='${db:Status-Abbrev}' "${package_name}" 2>/dev/null | grep -q '^ii' || \
            die "mandatory package is not installed: ${package_name}"
    done
    local command_name
    for command_name in dcbtool exportfs fcoeadm fcoemon fipvlan lldptool modinfo netplan nft rsyslogd snmpd systemd-run targetcli testparm unshare; do
        command -v "${command_name}" >/dev/null 2>&1 || \
            die "mandatory command is unavailable after package installation: ${command_name}"
    done
}

ensure_service_account() {
    local passwd_entry uid gid home shell login_uid_min group_gid groups is_login_account
    local -a passwd_fields
    if passwd_entry="$(getent passwd hoardarr)"; then
        IFS=: read -r -a passwd_fields <<<"${passwd_entry}"
        ((${#passwd_fields[@]} == 7)) || die "existing hoardarr account entry is malformed"
        uid="${passwd_fields[2]}"
        gid="${passwd_fields[3]}"
        home="${passwd_fields[5]}"
        shell="${passwd_fields[6]}"
        login_uid_min="$(awk '$1 == "UID_MIN" { print $2; exit }' /etc/login.defs 2>/dev/null || true)"
        [[ "${uid}" =~ ^[0-9]+$ ]] || die "existing hoardarr account has an invalid UID"
        [[ "${gid}" =~ ^[0-9]+$ ]] || die "existing hoardarr account has an invalid primary GID"
        ((uid > 0)) || die "existing hoardarr account must not use UID 0"
        ((gid > 0)) || die "existing hoardarr account must not use GID 0"
        getent group hoardarr >/dev/null || die "existing hoardarr group is missing"
        group_gid="$(getent group hoardarr | awk -F: '{ print $3 }')"
        [[ "${group_gid}" =~ ^[0-9]+$ ]] || die "existing hoardarr group has an invalid GID"
        ((group_gid > 0)) || die "existing hoardarr group must not use GID 0"
        [[ "${gid}" == "${group_gid}" ]] || die "hoardarr is not the account's primary group"
        is_login_account="false"
        if [[ "${login_uid_min}" =~ ^[0-9]+$ ]] && ((uid >= login_uid_min)); then
            is_login_account="true"
        fi
        case "${shell}" in
            /usr/sbin/nologin|/sbin/nologin|/bin/false) ;;
            *) is_login_account="true" ;;
        esac
        [[ "${home}" == "${STATE_ROOT}" ]] || is_login_account="true"
        if [[ "${is_login_account}" == "true" ]]; then
            [[ "${PRESERVE_EXISTING_LOGIN_ACCOUNT}" == "true" ]] || \
                die "existing hoardarr account is a login account; rerun only on a legacy development host with --preserve-existing-login-account"
            log "Preserving existing hoardarr administrator login for this legacy development host."
            return
        fi
        groups="$(id -nG hoardarr)"
        [[ "${groups}" == "hoardarr" ]] || \
            die "hoardarr has unexpected supplementary groups: ${groups}"
    else
        getent group hoardarr >/dev/null && die "group hoardarr exists without the service account"
        useradd --system --user-group --home-dir "${STATE_ROOT}" --shell /usr/sbin/nologin hoardarr
    fi
    usermod --lock --shell /usr/sbin/nologin hoardarr
}

copy_release_assets() {
    local stage="$1"
    install -D -o root -g root -m 0555 \
        "${BUNDLE_ROOT}/scripts/detect-hardware.py" "${stage}/scripts/detect-hardware.py"
    install -D -o root -g root -m 0555 \
        "${BUNDLE_ROOT}/scripts/bootstrap.py" "${stage}/scripts/bootstrap.py"
    local source relative
    while IFS= read -r -d '' source; do
        relative="${source#"${BUNDLE_ROOT}/hardware/"}"
        install -D -o root -g root -m 0444 \
            "${source}" "${stage}/packaging/hardware/${relative}"
    done < <(find "${BUNDLE_ROOT}/hardware" -type f -name '*.json' -print0)
    while IFS= read -r -d '' source; do
        relative="${source#"${BUNDLE_ROOT}/packages/"}"
        install -D -o root -g root -m 0444 \
            "${source}" "${stage}/packaging/packages/${relative}"
    done < <(find "${BUNDLE_ROOT}/packages" -type f \( -name '*.txt' -o -name '*.env' \) -print0)
    while IFS= read -r -d '' source; do
        relative="${source#"${BUNDLE_ROOT}/frontend/"}"
        install -D -o root -g root -m 0444 \
            "${source}" "${stage}/frontend/${relative}"
    done < <(find "${BUNDLE_ROOT}/frontend" -type f -print0)
    install -D -o root -g root -m 0444 "${BUNDLE_ROOT}/RELEASE.json" "${stage}/metadata/RELEASE.json"
    install -o root -g root -m 0444 "${BUNDLE_ROOT}/SHA256SUMS" "${stage}/metadata/SHA256SUMS"
    install -D -o root -g root -m 0444 \
        "${BUNDLE_ROOT}/requirements/runtime.lock" "${stage}/metadata/requirements/runtime.lock"
    install -o root -g root -m 0444 \
        "${BUNDLE_ROOT}/requirements/hoardarr.lock" "${stage}/metadata/requirements/hoardarr.lock"
}

stage_release() {
    local expected_manifest="$1"
    [[ ! -L "${RELEASE_PATH}" ]] || die "existing release path is a symbolic link"
    if [[ -d "${RELEASE_PATH}" ]]; then
        [[ -f "${RELEASE_PATH}/.bundle-manifest-sha256" && ! -L "${RELEASE_PATH}/.bundle-manifest-sha256" ]] || \
            die "existing release has no integrity marker: ${RELEASE_PATH}"
        [[ "$(<"${RELEASE_PATH}/.bundle-manifest-sha256")" == "${expected_manifest}" ]] || \
            die "existing release differs from this bundle: ${RELEASE_PATH}"
        [[ -x "${RELEASE_PATH}/venv/bin/python" ]] || die "existing release virtual environment is incomplete"
        [[ -f "${RELEASE_PATH}/frontend/index.html" ]] || die "existing release web interface is incomplete"
        "${RELEASE_PATH}/venv/bin/python" -c 'import hoardarr'
        log "Reusing already-staged release ${RELEASE_PATH}"
        return
    fi
    [[ ! -e "${RELEASE_PATH}" ]] || die "release destination is not a directory: ${RELEASE_PATH}"
    STAGE_DIR="$(mktemp -d "${RELEASES_ROOT}/.stage-${RELEASE_ID}.XXXXXX")"
    python3 -m venv "${STAGE_DIR}/venv"
    "${STAGE_DIR}/venv/bin/python" -m pip install \
        --isolated \
        --no-index \
        --no-input \
        --disable-pip-version-check \
        --only-binary=:all: \
        --require-hashes \
        --find-links "${BUNDLE_ROOT}/wheels" \
        --requirement "${BUNDLE_ROOT}/requirements/runtime.lock" \
        --requirement "${BUNDLE_ROOT}/requirements/hoardarr.lock"
    "${STAGE_DIR}/venv/bin/python" -c 'import hoardarr'
    copy_release_assets "${STAGE_DIR}"
    printf '%s\n' "${expected_manifest}" >"${STAGE_DIR}/.bundle-manifest-sha256"
    chown -R root:root "${STAGE_DIR}"
    chmod -R go-w "${STAGE_DIR}"
    mv -- "${STAGE_DIR}" "${RELEASE_PATH}"
    STAGE_DIR=""
}

atomic_symlink() {
    local target="$1"
    local link="$2"
    local temporary="${link}.new.$$"
    [[ ! -e "${temporary}" && ! -L "${temporary}" ]] || die "temporary link path exists: ${temporary}"
    ln -s -- "${target}" "${temporary}"
    mv -Tf -- "${temporary}" "${link}"
}

install_runtime_wrapper() {
    local destination="$1"
    local command_name="$2"
    local temporary="${destination}.new.$$"
    [[ ! -e "${temporary}" && ! -L "${temporary}" ]] || \
        die "temporary runtime wrapper exists: ${temporary}"
    printf '%s\n' \
        '#!/bin/sh' \
        "exec /usr/lib/hoardarr/venv/bin/python -m hoardarr.runtime ${command_name} \"\$@\"" \
        >"${temporary}"
    chown root:root "${temporary}"
    chmod 0755 "${temporary}"
    mv -Tf -- "${temporary}" "${destination}"
}

wait_for_api_ready() {
    local attempt
    for ((attempt = 1; attempt <= 30; attempt++)); do
        if curl --fail --silent --show-error --max-time 2 \
            http://127.0.0.1:7877/health/ready >/dev/null; then
            return 0
        fi
        sleep 1
    done
    return 1
}

stop_runtime_services() {
    systemctl stop \
        hoardarr-api.service \
        hoardarr-worker.service \
        hoardarr-account-executor.service \
        hoardarr-storage-executor.service \
        hoardarr-storage-status.service || true
}

restore_previous_release() {
    local previous_release="$1"
    stop_runtime_services
    if [[ -n "${previous_release}" ]]; then
        atomic_symlink "${previous_release}" "${LIB_ROOT}/current"
        systemctl restart hoardarr-migrate.service || return 1
        systemctl start \
            hoardarr-account-executor.service \
            hoardarr-storage-executor.service \
            hoardarr-storage-status.service \
            hoardarr-worker.service \
            hoardarr-api.service || return 1
    else
        rm -f -- "${LIB_ROOT}/current"
    fi
}

install_config_units_docs() {
    install -d -o root -g hoardarr -m 0750 "${CONFIG_ROOT}"
    if [[ ! -e "${CONFIG_FILE}" ]]; then
        install -o root -g hoardarr -m 0640 "${BUNDLE_ROOT}/config/hoardarr.env" "${CONFIG_FILE}"
    else
        log "Preserving existing ${CONFIG_FILE}"
        chown root:hoardarr "${CONFIG_FILE}"
        chmod 0640 "${CONFIG_FILE}"
    fi
    local unit
    for unit in hoardarr-api.service hoardarr-worker.service hoardarr-migrate.service hoardarr-account-executor.service hoardarr-storage-executor.service hoardarr-storage-status.service; do
        install -o root -g root -m 0644 "${BUNDLE_ROOT}/systemd/${unit}" "${UNIT_ROOT}/${unit}"
    done
    install -d -o root -g root -m 0755 "${LLDPD_DROPIN_ROOT}"
    install -o root -g root -m 0644 \
        "${BUNDLE_ROOT}/systemd/hoardarr-lldpd.conf" "${LLDPD_DROPIN_ROOT}/hoardarr.conf"
    install -d -o root -g root -m 0755 "${DOC_ROOT}"
    local source
    while IFS= read -r -d '' source; do
        install -o root -g root -m 0444 "${source}" "${DOC_ROOT}/$(basename -- "${source}")"
    done < <(find "${BUNDLE_ROOT}/docs" -maxdepth 1 -type f -name '*.md' -print0)
}

apply_release() {
    [[ "${EUID}" -eq 0 ]] || die "apply must run as root"
    [[ "${CONFIRMED}" == "true" ]] || die "apply requires --yes"
    acquire_install_lock
    ensure_mergerfs
    ensure_filesystem_attribute_tools
    ensure_account_tools
    ensure_neighbor_discovery
    ensure_service_account
    ensure_connectivity_tools
    install -d -o root -g root -m 0755 "${LIB_ROOT}" "${RELEASES_ROOT}"
    install -d -o root -g root -m 0755 "${CLI_ROOT}"
    install -d -o root -g root -m 0700 "${STATE_ROOT}"
    chown -R root:root "${STATE_ROOT}"
    # Storage state and presentation roots must exist before storage services
    # reconcile persisted mounts. The executor intentionally shares the host
    # mount namespace so its approved mounts are visible to applications.
    install -d -o root -g root -m 0700 "${STATE_ROOT}/storage-executor"
    install -d -o root -g root -m 0700 "${STATE_ROOT}/storage-executor/transactions"
    install -d -o root -g root -m 0700 "${STATE_ROOT}/connectivity"
    # Create every supported presentation root before systemd starts (or
    # restarts) the executor. In particular, Ubuntu does not provide /data by
    # default.
    install -d -o root -g root -m 0755 /data /mnt /srv
    install -d -o root -g root -m 0755 \
        /etc/samba /etc/snapraid /etc/multipath/conf.d /etc/systemd/system

    local expected_manifest previous_release
    expected_manifest="$(manifest_digest)"
    stage_release "${expected_manifest}"
    previous_release=""
    if [[ -L "${LIB_ROOT}/current" ]]; then
        previous_release="$(readlink -- "${LIB_ROOT}/current")"
        [[ "${previous_release}" =~ ^releases/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || \
            die "current release link has an unexpected target: ${previous_release}"
        [[ -d "${LIB_ROOT}/${previous_release}" && ! -L "${LIB_ROOT}/${previous_release}" ]] || \
            die "current release link does not name an installed release"
    fi
    if [[ "${DEFER_SERVICE_START}" == "true" && -n "${previous_release}" ]]; then
        die "--defer-service-start is only allowed for a first appliance installation"
    fi

    # Staging happens while the old release remains active.  The service outage
    # begins only after the new environment has installed and imported cleanly.
    install_config_units_docs
    systemctl daemon-reload
    if [[ "${DEFER_SERVICE_START}" == "true" ]]; then
        # Offline appliance installation leaves discovery/monitoring daemons
        # inactive until the owner configures the corresponding feature.
        systemctl disable lldpd.service >/dev/null 2>&1 || true
    else
        systemctl enable --now lldpd.service
        stop_runtime_services
    fi
    atomic_symlink "releases/${RELEASE_ID}" "${LIB_ROOT}/current"
    atomic_symlink "current/venv" "${LIB_ROOT}/venv"
    atomic_symlink "current/scripts" "${LIB_ROOT}/scripts"
    atomic_symlink "current/packaging" "${LIB_ROOT}/packaging"
    # Retain the early preview path as a compatibility alias. The detector
    # itself resolves current/packaging/hardware relative to its script.
    atomic_symlink "current/packaging/hardware" "${LIB_ROOT}/hardware"
    install_runtime_wrapper "${CLI_LINK}" cli
    install_runtime_wrapper "${QUARANTINE_CLI_LINK}" storage-quarantine
    # A first appliance install cannot safely execute a storage plan until the
    # host-bound deny-by-default policies and their checksum attestation exist.
    # Upgrades preserve an existing attestation; a missing one is prepared
    # before any Hoardarr runtime service is allowed to start.  Preparation is
    # fail-closed and restores the previous release on an upgrade failure.
    if [[ ! -f /var/lib/hoardarr/storage-executor/quarantine.json ]]; then
        if ! "${QUARANTINE_CLI_LINK}" prepare --yes; then
            restore_previous_release "${previous_release}" || true
            die "drive quarantine could not be prepared; the previous runtime was restored"
        fi
    fi
    # Direct-to-latest upgrades may already contain Hoardarr-managed fstab
    # blocks created before managed drives were released from startup
    # quarantine. Reconcile only those exact blocks, add explicit mergerFS
    # member dependencies, and activate their generated mount units.
    if ! "${QUARANTINE_CLI_LINK}" reconcile-managed --yes --activate; then
        restore_previous_release "${previous_release}" || true
        die "managed storage could not be reconciled; the previous runtime was restored"
    fi
    systemctl enable hoardarr-migrate.service hoardarr-api.service hoardarr-worker.service hoardarr-account-executor.service hoardarr-storage-executor.service hoardarr-storage-status.service

    if [[ "${DEFER_SERVICE_START}" == "true" ]]; then
        log "Installed Hoardarr ${RELEASE_VERSION} (${RELEASE_ID}) for first-boot activation."
        log "Database migration and runtime readiness will be enforced by systemd on boot."
        return
    fi

    if ! systemctl restart hoardarr-migrate.service; then
        restore_previous_release "${previous_release}" || true
        die "database migration failed; the previous runtime was restored when available"
    fi
    if ! systemctl start hoardarr-account-executor.service hoardarr-storage-executor.service hoardarr-storage-status.service hoardarr-worker.service hoardarr-api.service; then
        restore_previous_release "${previous_release}" || true
        die "a runtime service failed to start; the previous runtime was restored when available"
    fi
    if ! wait_for_api_ready; then
        restore_previous_release "${previous_release}" || true
        die "the API did not become ready; the previous runtime was restored when available"
    fi
    log "Installed Hoardarr ${RELEASE_VERSION} (${RELEASE_ID})."
    log "No setup token was issued. The API retains the configured bind address."
}

main() {
    parse_args "$@"
    require_commands
    verify_bundle
    load_release_metadata
    validate_release_layout
    validate_host
    validate_destination_paths
    if [[ "${ACTION}" == "plan" ]]; then
        show_plan
        return
    fi
    apply_release
}

main "$@"
