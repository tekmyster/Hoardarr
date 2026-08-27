from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build-offline-apt-repository.py"
SPEC = importlib.util.spec_from_file_location("hoardarr_offline_repo", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
offline_repo = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = offline_repo
SPEC.loader.exec_module(offline_repo)


PCP_TRACE_PHASES = (
    ("01-fixture-creation", "fixture-creation"),
    ("02-package-download", "package-download"),
    ("03-package-hash", "package-hash"),
    ("04-package-extract", "package-extract"),
    ("05-mount-namespace", "mount-namespace"),
    ("06-old-failure", "old-failure"),
    ("07-guard-preparation", "guard-preparation"),
    ("08-pcp-configure", "pcp-configure"),
    ("09-all-denied-presets", "all-denied-presets"),
    ("10-host-manager-isolation", "host-manager-isolation"),
    ("11-interrupted-retention", "interrupted-retention"),
    ("12-final-disable-readback", "final-disable-readback"),
    ("13-retained-manifest", "retained-manifest"),
    ("14-peer-isolation", "peer-isolation"),
    ("15-fixture-cleanup", "fixture-cleanup"),
)
PCP_TRACE_MAX_BYTES = 8192
PCP_TRACE_MAX_LINE_BYTES = 240
PCP_TRACE_RECORD = re.compile(
    r"^HPCP\|1\|(BEGIN|PASS|EXIT)\|([0-9]{2}-[a-z0-9-]+)\|"
    r"status=(-|[0-9]{1,3})\|line=(-|[0-9]{1,6})\|"
    r"function=(-|[A-Za-z_][A-Za-z0-9_]*)\|label=([a-z0-9-]+)$"
)
PCP_MANAGER_ROOT_RECEIPT_MAX_BYTES = 32 * 1024
PCP_MANAGER_ROOT_RECEIPT_MAX_ENTRIES = 128
PCP_MANAGER_ROOT_PATH_MAX_BYTES = 192
PCP_MANAGER_ROOT_TYPES = {
    "directory",
    "regular",
    "symlink",
    "socket",
    "fifo",
    "block",
    "char",
    "other",
}
PCP_MANAGER_ROOT_HEADER = re.compile(
    r"^HMROOT\|1\|(before|after)\|status=(-|[0-9]{1,3})$"
)
PCP_MANAGER_ROOT_COMPONENT = re.compile(r"^[A-Za-z0-9_.@:+,-]+$")
PCP_SYSTEMD_SOURCE_RECEIPT_MAX_BYTES = 8192
PCP_SYSTEMD_CAUSAL_RECEIPT_MAX_BYTES = 4096
PCP_SYSTEMD_UPSTREAM_REPOSITORY = "https://github.com/systemd/systemd-stable"
PCP_SYSTEMD_UPSTREAM_TAG = "v255.4"
PCP_SYSTEMD_UPSTREAM_REVISION = "387a14a7b67b8b76adaed4175e14bb7e39b2f738"
PCP_SYSTEMD_ANALYZE_SOURCE_PATH = "src/analyze/analyze-condition.c"
PCP_SYSTEMD_ANALYZE_SOURCE_FUNCTION = "verify_conditions:68-114"
PCP_SYSTEMD_ANALYZE_SOURCE_SHA256 = (
    "3f89216b21faa202099f290615cdd8ed4ee5f98a2f0094242d447670248a9b89"
)
PCP_SYSTEMD_MANAGER_SOURCE_PATH = "src/core/manager.c"
PCP_SYSTEMD_MANAGER_SOURCE_FUNCTION = "manager_ready:1891-1910"
PCP_SYSTEMD_MANAGER_SOURCE_SHA256 = (
    "58af3c261e43b6de343be931a46c049152eb57c856f24f81dd53bdd9abafa72e"
)
PCP_SYSTEMD_MARKER_PATH = "/run/systemd/systemd-units-load"
PCP_SYSTEMD_FALSE_CONDITION = (
    "ConditionPathExists=/dev/null/hoardarr-offline-service-guard/pmcd.service"
)

PCP_MANAGER_ROOT_SNAPSHOT_FUNCTION = r"""
manager_root_snapshot_abort() {
    local status="$1"
    shift
    /usr/bin/rm -f -- "$@" || return 126
    return "$status"
}
manager_root_snapshot() {
    local stage="$1"
    local condition_status="$2"
    local output="$3"
    local root="$work/run-systemd"
    local expected_output
    local raw="${output}.raw"
    local sorted="${output}.sorted"
    local entries="${output}.entries"
    local partial="${output}.partial"
    local deep relative previous= object type metadata mode uid gid
    local count=0
    local LC_ALL=C
    case "$stage:$condition_status" in
        before:-) expected_output="$work/manager-root-before.tsv" ;;
        after:[1-9]|after:[1-9][0-9]|after:[12][0-9][0-9])
            (( condition_status <= 255 )) || return 120
            expected_output="$work/manager-root-after.tsv"
            ;;
        *) return 120 ;;
    esac
    [[ "$output" == "$expected_output" && "${output%/*}" == "$work" ]] || return 120
    [[ "$root" == "$work/run-systemd" && -d "$root" && ! -L "$root" ]] || return 120
    for path in "$output" "$raw" "$sorted" "$entries" "$partial"; do
        [[ ! -e "$path" && ! -L "$path" ]] || return 120
    done
    : >"$raw" || return 121
    : >"$sorted" || manager_root_snapshot_abort 121 "$raw"
    : >"$entries" || manager_root_snapshot_abort 121 "$raw" "$sorted"
    : >"$partial" || manager_root_snapshot_abort 121 "$raw" "$sorted" "$entries"
    deep="$(/usr/bin/find "$root" -xdev -mindepth 6 -printf x -quit)" || \
        manager_root_snapshot_abort 122 "$raw" "$sorted" "$entries" "$partial"
    [[ -z "$deep" ]] || \
        manager_root_snapshot_abort 122 "$raw" "$sorted" "$entries" "$partial"
    /usr/bin/find "$root" -xdev -mindepth 1 -maxdepth 5 -printf '%P\0' >"$raw" || \
        manager_root_snapshot_abort 122 "$raw" "$sorted" "$entries" "$partial"
    /usr/bin/sort -z -- "$raw" >"$sorted" || \
        manager_root_snapshot_abort 122 "$raw" "$sorted" "$entries" "$partial"
    while IFS= read -r -d '' relative; do
        count=$((count + 1))
        (( count <= 128 )) || \
            manager_root_snapshot_abort 123 "$raw" "$sorted" "$entries" "$partial"
        (( ${#relative} > 0 && ${#relative} <= 192 )) || \
            manager_root_snapshot_abort 123 "$raw" "$sorted" "$entries" "$partial"
        [[ "$relative" =~ ^[A-Za-z0-9_.@:+,-]+(/[A-Za-z0-9_.@:+,-]+){0,4}$ ]] || \
            manager_root_snapshot_abort 123 "$raw" "$sorted" "$entries" "$partial"
        IFS=/ read -r -a components <<<"$relative"
        for component in "${components[@]}"; do
            [[ "$component" != . && "$component" != .. ]] || \
                manager_root_snapshot_abort 123 "$raw" "$sorted" "$entries" "$partial"
        done
        [[ -z "$previous" || "$previous" != "$relative" ]] || \
            manager_root_snapshot_abort 123 "$raw" "$sorted" "$entries" "$partial"
        previous="$relative"
        object="$root/$relative"
        if [[ -L "$object" ]]; then type=symlink
        elif [[ -d "$object" ]]; then type=directory
        elif [[ -f "$object" ]]; then type=regular
        elif [[ -S "$object" ]]; then type=socket
        elif [[ -p "$object" ]]; then type=fifo
        elif [[ -b "$object" ]]; then type=block
        elif [[ -c "$object" ]]; then type=char
        else type=other
        fi
        metadata="$(/usr/bin/stat -c '%a %u %g' -- "$object")" || \
            manager_root_snapshot_abort 124 "$raw" "$sorted" "$entries" "$partial"
        read -r mode uid gid extra <<<"$metadata"
        [[ -z "${extra:-}" && "$mode" =~ ^[0-7]{3,4}$ && \
            "$uid" =~ ^[0-9]+$ && "$gid" =~ ^[0-9]+$ ]] || \
            manager_root_snapshot_abort 124 "$raw" "$sorted" "$entries" "$partial"
        printf 'ENTRY\t%s\t%s\t%s\t%s\t%s\n' \
            "$relative" "$type" "$mode" "$uid" "$gid" >>"$entries" || \
            manager_root_snapshot_abort 124 "$raw" "$sorted" "$entries" "$partial"
    done <"$sorted"
    printf 'HMROOT|1|%s|status=%s\n' "$stage" "$condition_status" >"$partial" || \
        manager_root_snapshot_abort 125 "$raw" "$sorted" "$entries" "$partial"
    /usr/bin/cat -- "$entries" >>"$partial" || \
        manager_root_snapshot_abort 125 "$raw" "$sorted" "$entries" "$partial"
    (( $(/usr/bin/stat -c %s -- "$partial") <= 32768 )) || \
        manager_root_snapshot_abort 125 "$raw" "$sorted" "$entries" "$partial"
    /usr/bin/mv -- "$partial" "$output" || \
        manager_root_snapshot_abort 125 "$raw" "$sorted" "$entries" "$partial"
    /usr/bin/rm -f -- "$raw" "$sorted" "$entries" || return 126
}
""".strip()

PCP_SYSTEMD_SOURCE_RECEIPT = rf"""
systemd_source_receipt="$work/systemd-source.tsv"
systemd_source_partial="${{systemd_source_receipt}}.partial"
[[ ! -e "$systemd_source_receipt" && ! -L "$systemd_source_receipt" ]]
[[ ! -e "$systemd_source_partial" && ! -L "$systemd_source_partial" ]]
systemd_analyze=/usr/bin/systemd-analyze
[[ -f "$systemd_analyze" && ! -L "$systemd_analyze" ]]
[[ "$(/usr/bin/readlink -e -- "$systemd_analyze")" == "$systemd_analyze" ]]
systemd_package_record="$(/usr/bin/dpkg-query -W \
    -f='${{binary:Package}}\t${{Version}}\t${{Architecture}}\n' systemd)"
IFS=$'\t' read -r systemd_package systemd_package_version systemd_package_arch extra \
    <<<"$systemd_package_record"
[[ -z "${{extra:-}}" && "$systemd_package" == systemd && \
    "$systemd_package_arch" == amd64 ]]
[[ "$systemd_package_version" =~ ^255\.4-[0-9A-Za-z.+:~]+$ ]]
systemd_owner="$(/usr/bin/dpkg-query -S -- "$systemd_analyze")"
[[ "$systemd_owner" == "systemd: /usr/bin/systemd-analyze" ]]
systemd_version_output="$work/systemd-analyze-version.txt"
[[ ! -e "$systemd_version_output" && ! -L "$systemd_version_output" ]]
"$systemd_analyze" --version >"$systemd_version_output"
[[ -f "$systemd_version_output" && ! -L "$systemd_version_output" ]]
(( $(/usr/bin/stat -c %s -- "$systemd_version_output") > 0 ))
(( $(/usr/bin/stat -c %s -- "$systemd_version_output") <= 4096 ))
IFS= read -r systemd_version_first <"$systemd_version_output"
[[ "$systemd_version_first" == "systemd 255 ($systemd_package_version)" ]]
[[ "$systemd_version_first" =~ ^systemd\ 255\ \(255\.4-[0-9A-Za-z.+:~]+\)$ ]]
systemd_version_sha256="$(/usr/bin/sha256sum -- "$systemd_version_output")"
systemd_version_sha256="${{systemd_version_sha256%% *}}"
systemd_executable_sha256="$(/usr/bin/sha256sum -- "$systemd_analyze")"
systemd_executable_sha256="${{systemd_executable_sha256%% *}}"
systemd_executable_metadata="$(/usr/bin/stat -c '%a %u %g %s %h' -- "$systemd_analyze")"
read -r systemd_executable_mode systemd_executable_uid systemd_executable_gid \
    systemd_executable_size systemd_executable_links extra \
    <<<"$systemd_executable_metadata"
[[ -z "${{extra:-}}" && "$systemd_executable_mode" =~ ^[0-7]{{3,4}}$ && \
    "$systemd_executable_uid" == 0 && "$systemd_executable_gid" == 0 && \
    "$systemd_executable_size" =~ ^[1-9][0-9]*$ && \
    "$systemd_executable_links" =~ ^[1-9][0-9]*$ && \
    "$systemd_version_sha256" =~ ^[0-9a-f]{{64}}$ && \
    "$systemd_executable_sha256" =~ ^[0-9a-f]{{64}}$ ]]
{{
    printf '%s\n' 'HSOURCE|1'
    printf 'PACKAGE\t%s\t%s\t%s\n' \
        systemd "$systemd_package_version" "$systemd_package_arch"
    printf 'VERSION\t%s\t%s\n' \
        "$systemd_version_first" "$systemd_version_sha256"
    printf 'EXECUTABLE\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        /usr/bin/systemd-analyze "$systemd_executable_sha256" \
        "$systemd_executable_mode" 0 0 "$systemd_executable_size" \
        "$systemd_executable_links" systemd
    printf 'UPSTREAM\t%s\t%s\t%s\n' \
        '{PCP_SYSTEMD_UPSTREAM_REPOSITORY}' '{PCP_SYSTEMD_UPSTREAM_TAG}' \
        '{PCP_SYSTEMD_UPSTREAM_REVISION}'
    printf 'SOURCE\t%s\t%s\t%s\n' \
        '{PCP_SYSTEMD_ANALYZE_SOURCE_PATH}' \
        '{PCP_SYSTEMD_ANALYZE_SOURCE_FUNCTION}' \
        '{PCP_SYSTEMD_ANALYZE_SOURCE_SHA256}'
    printf 'SOURCE\t%s\t%s\t%s\n' \
        '{PCP_SYSTEMD_MANAGER_SOURCE_PATH}' \
        '{PCP_SYSTEMD_MANAGER_SOURCE_FUNCTION}' \
        '{PCP_SYSTEMD_MANAGER_SOURCE_SHA256}'
    printf 'CHAIN\t%s\n' \
        'verb_condition>verify_conditions>manager_startup>manager_ready>touch_file'
    printf 'MARKER\t%s\t%s\t%s\t%s\t%s\n' \
        '{PCP_SYSTEMD_MARKER_PATH}' regular 0444 zero-length manager-ready
}} >"$systemd_source_partial"
(( $(/usr/bin/stat -c %s -- "$systemd_source_partial") <= {PCP_SYSTEMD_SOURCE_RECEIPT_MAX_BYTES} ))
/usr/bin/mv -- "$systemd_source_partial" "$systemd_source_receipt"
/usr/bin/sync -f "$systemd_source_receipt"
""".strip()

PCP_SYSTEMD_CAUSAL_PROOF = rf"""
systemd_causal_parent="$work/systemd-causal-controls"
systemd_positive_root="$systemd_causal_parent/positive"
systemd_negative_root="$systemd_causal_parent/negative"
systemd_causal_receipt="$work/systemd-causal.tsv"
systemd_causal_partial="${{systemd_causal_receipt}}.partial"
[[ ! -e "$systemd_causal_parent" && ! -L "$systemd_causal_parent" ]]
[[ ! -e "$systemd_causal_receipt" && ! -L "$systemd_causal_receipt" ]]
[[ ! -e "$systemd_causal_partial" && ! -L "$systemd_causal_partial" ]]
mkdir -- "$systemd_causal_parent"
mkdir -- "$systemd_positive_root" "$systemd_negative_root"
[[ -d "$systemd_causal_parent" && ! -L "$systemd_causal_parent" ]]
[[ -d "$systemd_positive_root" && ! -L "$systemd_positive_root" ]]
[[ -d "$systemd_negative_root" && ! -L "$systemd_negative_root" ]]
systemd_mount_id() {{
    /usr/bin/awk '$5 == "/run/systemd" {{ id=$1 }} END {{ print id }}' /proc/self/mountinfo
}}
systemd_underlay_mount_id="$(systemd_mount_id)"
[[ "$systemd_underlay_mount_id" =~ ^[1-9][0-9]*$ ]]
systemd_causal_mounted=false
systemd_causal_cleanup_root() {{
    local control_root="$1"
    local expected_entry="$2"
    if [[ "$systemd_causal_mounted" == true ]]; then
        umount -- /run/systemd || return 131
        systemd_causal_mounted=false
        [[ "$(systemd_mount_id)" == "$systemd_underlay_mount_id" ]] || return 132
    fi
    if [[ -n "$expected_entry" ]]; then
        [[ "$expected_entry" == "$control_root/systemd-units-load" ]] || return 133
        [[ -f "$expected_entry" && ! -L "$expected_entry" ]] || return 133
        rm -f -- "$expected_entry" || return 133
    fi
    [[ -z "$(find "$control_root" -mindepth 1 -print -quit)" ]] || return 134
    rmdir -- "$control_root" || return 134
}}
systemd_causal_abort() {{
    local status="$1"
    local control_root="${{2:-}}"
    local expected_entry="${{3:-}}"
    if [[ -n "$control_root" && -d "$control_root" && ! -L "$control_root" ]]; then
        systemd_causal_cleanup_root "$control_root" "$expected_entry" || status=135
    fi
    if [[ -d "$systemd_positive_root" && ! -L "$systemd_positive_root" ]]; then
        [[ -z "$(find "$systemd_positive_root" -mindepth 1 -print -quit)" ]] && \
            rmdir -- "$systemd_positive_root" || status=135
    fi
    if [[ -d "$systemd_negative_root" && ! -L "$systemd_negative_root" ]]; then
        [[ -z "$(find "$systemd_negative_root" -mindepth 1 -print -quit)" ]] && \
            rmdir -- "$systemd_negative_root" || status=135
    fi
    if [[ -d "$systemd_causal_parent" && ! -L "$systemd_causal_parent" ]]; then
        [[ -z "$(find "$systemd_causal_parent" -mindepth 1 -print -quit)" ]] && \
            rmdir -- "$systemd_causal_parent" || status=135
    fi
    rm -f -- "$systemd_causal_partial" || status=135
    return "$status"
}}

# Negative control: the same fresh private root remains empty when no
# systemd-analyze command is run.
[[ -z "$(find "$systemd_negative_root" -mindepth 1 -print -quit)" ]] || \
    systemd_causal_abort 136
mount --bind "$systemd_negative_root" /run/systemd || systemd_causal_abort 137
systemd_causal_mounted=true
mount --make-private /run/systemd || \
    systemd_causal_abort 138 "$systemd_negative_root"
[[ "$(systemd_mount_id)" =~ ^[1-9][0-9]*$ && \
    "$(systemd_mount_id)" != "$systemd_underlay_mount_id" && \
    "$(stat -c %d:%i -- /run/systemd)" == "$(stat -c %d:%i -- "$systemd_negative_root")" ]] || \
    systemd_causal_abort 139 "$systemd_negative_root"
[[ -z "$(find /run/systemd -mindepth 1 -print -quit)" ]] || \
    systemd_causal_abort 140 "$systemd_negative_root"
[[ -z "$(find /run/systemd -xdev -type s -print -quit)" ]] || \
    systemd_causal_abort 141 "$systemd_negative_root"
[[ -z "$(find /run/systemd -mindepth 1 -print -quit)" ]] || \
    systemd_causal_abort 142 "$systemd_negative_root"
systemd_causal_cleanup_root "$systemd_negative_root" "" || \
    systemd_causal_abort 143

# Positive control: exactly one false condition invocation in an otherwise
# identical fresh private root creates only the documented local marker.
[[ -z "$(find "$systemd_positive_root" -mindepth 1 -print -quit)" ]] || \
    systemd_causal_abort 144
mount --bind "$systemd_positive_root" /run/systemd || systemd_causal_abort 145
systemd_causal_mounted=true
mount --make-private /run/systemd || \
    systemd_causal_abort 146 "$systemd_positive_root"
[[ "$(systemd_mount_id)" =~ ^[1-9][0-9]*$ && \
    "$(systemd_mount_id)" != "$systemd_underlay_mount_id" && \
    "$(stat -c %d:%i -- /run/systemd)" == "$(stat -c %d:%i -- "$systemd_positive_root")" ]] || \
    systemd_causal_abort 147 "$systemd_positive_root"
[[ -z "$(find /run/systemd -mindepth 1 -print -quit)" ]] || \
    systemd_causal_abort 148 "$systemd_positive_root"
[[ -z "$(find /run/systemd -xdev -type s -print -quit)" ]] || \
    systemd_causal_abort 149 "$systemd_positive_root"
systemd_positive_status=0
/usr/bin/systemd-analyze condition \
    "{PCP_SYSTEMD_FALSE_CONDITION}" >/dev/null 2>&1 || systemd_positive_status=$?
[[ "$systemd_positive_status" -eq 1 ]] || \
    systemd_causal_abort 150 "$systemd_positive_root"
mapfile -d '' systemd_positive_entries \
    < <(find /run/systemd -xdev -mindepth 1 -maxdepth 1 -printf '%f\0' | sort -z)
[[ "${{#systemd_positive_entries[@]}}" -eq 1 && \
    "${{systemd_positive_entries[0]}}" == systemd-units-load ]] || \
    systemd_causal_abort 151 "$systemd_positive_root"
systemd_marker=/run/systemd/systemd-units-load
[[ -f "$systemd_marker" && ! -L "$systemd_marker" ]] || \
    systemd_causal_abort 152 "$systemd_positive_root"
systemd_marker_metadata="$(stat -c '%a %u %g %s %h %d' -- "$systemd_marker")"
read -r systemd_marker_mode systemd_marker_uid systemd_marker_gid \
    systemd_marker_size systemd_marker_links systemd_marker_device extra \
    <<<"$systemd_marker_metadata"
systemd_root_device="$(stat -c %d -- /run/systemd)"
[[ -z "${{extra:-}}" && "$systemd_marker_mode" == 444 && \
    "$systemd_marker_uid" == 0 && "$systemd_marker_gid" == 0 && \
    "$systemd_marker_size" == 0 && "$systemd_marker_links" == 1 && \
    "$systemd_marker_device" == "$systemd_root_device" ]] || \
    systemd_causal_abort 153 "$systemd_positive_root" "$systemd_positive_root/systemd-units-load"
cmp -s -- "$systemd_marker" /dev/null || \
    systemd_causal_abort 154 "$systemd_positive_root" "$systemd_positive_root/systemd-units-load"
[[ -z "$(find /run/systemd -xdev -type s -print -quit)" ]] || \
    systemd_causal_abort 155 "$systemd_positive_root" "$systemd_positive_root/systemd-units-load"
systemd_causal_cleanup_root \
    "$systemd_positive_root" "$systemd_positive_root/systemd-units-load" || \
    systemd_causal_abort 156
[[ -z "$(find "$systemd_causal_parent" -mindepth 1 -print -quit)" ]]
rmdir -- "$systemd_causal_parent"
[[ ! -e "$systemd_causal_parent" && ! -L "$systemd_causal_parent" ]]
{{
    printf '%s\n' 'HCAUSE|1'
    printf 'CONTROL\t%s\tcommand=%s\tstatus=%s\tbefore=%s\tafter=%s\tmanager_endpoints_before=%s\tmanager_endpoints_after=%s\tcleanup=%s\n' \
        negative none - 0 0 0 0 removed
    printf 'CONTROL\t%s\tcommand=%s\tstatus=%s\tbefore=%s\tafter=%s\tmanager_endpoints_before=%s\tmanager_endpoints_after=%s\tcleanup=%s\n' \
        positive systemd-analyze-condition 1 0 1 0 0 removed
    printf 'MARKER\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        systemd-units-load regular "$systemd_marker_mode" "$systemd_marker_uid" \
        "$systemd_marker_gid" "$systemd_marker_size" "$systemd_marker_links" \
        same-filesystem
}} >"$systemd_causal_partial"
(( $(stat -c %s -- "$systemd_causal_partial") <= {PCP_SYSTEMD_CAUSAL_RECEIPT_MAX_BYTES} ))
mv -- "$systemd_causal_partial" "$systemd_causal_receipt"
sync -f "$systemd_causal_receipt"
[[ "$(systemd_mount_id)" == "$systemd_underlay_mount_id" ]]
""".strip()

PCP_LOCAL_SYSTEMD_MARKER_ORACLE = r"""
local_systemd_marker_cleanup_count=0
validate_and_remove_local_systemd_marker() {
    local receipt marker private_marker mounted_root private_root
    local expected_receipt expected_receipt_size receipt_hash_before receipt_hash_after
    local mount_id mounted_identity private_identity marker_metadata marker_metadata_now
    local marker_mode marker_uid marker_gid marker_size marker_links marker_device
    local marker_inode root_device extra entry_file
    local -a entries=()
    [[ "$#" -eq 1 ]] || return 160
    receipt="$1"
    mounted_root=/run/systemd
    private_root="$work/run-systemd"
    marker="$mounted_root/systemd-units-load"
    private_marker="$private_root/systemd-units-load"
    entry_file="$work/local-systemd-marker.entries"
    [[ "$receipt" == "$work/manager-root-after.tsv" && \
        "${receipt%/*}" == "$work" && -f "$receipt" && ! -L "$receipt" ]] || return 161
    [[ "$condition_status" -eq 1 ]] || return 162
    expected_receipt=$'HMROOT|1|after|status=1\nENTRY\tsystemd-units-load\tregular\t444\t0\t0\n'
    expected_receipt_size="${#expected_receipt}"
    [[ "$(/usr/bin/stat -c %s -- "$receipt")" == "$expected_receipt_size" && \
        "$(/usr/bin/cat -- "$receipt")"$'\n' == "$expected_receipt" ]] || return 163
    receipt_hash_before="$(/usr/bin/sha256sum -- "$receipt")" || return 164
    receipt_hash_before="${receipt_hash_before%% *}"
    [[ "$receipt_hash_before" =~ ^[0-9a-f]{64}$ ]] || return 164
    /usr/bin/sync -f "$receipt" || return 164
    [[ "$private_root" == "$work/run-systemd" && -d "$private_root" && \
        ! -L "$private_root" && -d "$mounted_root" && ! -L "$mounted_root" ]] || return 165
    [[ "$(/usr/bin/readlink -e -- "$private_root")" == "$private_root" && \
        "$(/usr/bin/readlink -e -- "$mounted_root")" == "$mounted_root" ]] || return 165
    mount_id="$(systemd_mount_id)" || return 166
    [[ "$mount_id" =~ ^[1-9][0-9]*$ && "$mount_id" == "$systemd_underlay_mount_id" ]] || return 166
    mounted_identity="$(/usr/bin/stat -c '%d:%i' -- "$mounted_root")" || return 167
    private_identity="$(/usr/bin/stat -c '%d:%i' -- "$private_root")" || return 167
    [[ "$mounted_identity" == "$private_identity" ]] || return 167
    [[ ! -e "$entry_file" && ! -L "$entry_file" ]] || return 168
    /usr/bin/find "$mounted_root" -xdev -mindepth 1 -maxdepth 1 -print0 \
        >"$entry_file" || return 168
    mapfile -d '' -t entries <"$entry_file" || return 168
    /usr/bin/rm -- "$entry_file" || return 168
    [[ "${#entries[@]}" -eq 1 && "${entries[0]}" == "$marker" ]] || return 169
    [[ -z "$(/usr/bin/find "$mounted_root" -xdev -mindepth 2 -print -quit)" ]] || return 170
    [[ -z "$(/usr/bin/find "$mounted_root" -xdev -type s -print -quit)" ]] || return 171
    [[ "$marker" == /run/systemd/systemd-units-load && \
        "$private_marker" == "$work/run-systemd/systemd-units-load" && \
        -f "$marker" && ! -L "$marker" && -f "$private_marker" && \
        ! -L "$private_marker" ]] || return 172
    [[ "$(/usr/bin/stat -c '%d:%i' -- "$marker")" == \
        "$(/usr/bin/stat -c '%d:%i' -- "$private_marker")" ]] || return 172
    marker_metadata="$(/usr/bin/stat -c '%a %u %g %s %h %d %i' -- "$marker")" || return 173
    read -r marker_mode marker_uid marker_gid marker_size marker_links marker_device \
        marker_inode extra <<<"$marker_metadata"
    root_device="$(/usr/bin/stat -c %d -- "$mounted_root")" || return 173
    [[ -z "${extra:-}" && "$marker_mode" == 444 && "$marker_uid" == 0 && \
        "$marker_gid" == 0 && "$marker_size" == 0 && "$marker_links" == 1 && \
        "$marker_device" == "$root_device" && "$marker_inode" =~ ^[1-9][0-9]*$ ]] || return 173
    /usr/bin/cmp -s -- "$marker" /dev/null || return 174
    [[ "$local_systemd_marker_cleanup_count" -eq 0 ]] || return 175
    [[ "$(systemd_mount_id)" == "$systemd_underlay_mount_id" && \
        "$(/usr/bin/stat -c '%d:%i' -- "$mounted_root")" == "$private_identity" ]] || return 176
    marker_metadata_now="$(/usr/bin/stat -c '%a %u %g %s %h %d %i' -- "$marker")" || return 176
    [[ "$marker_metadata_now" == "$marker_metadata" ]] || return 176
    [[ "$(/usr/bin/sha256sum -- "$receipt")" == "$receipt_hash_before  $receipt" ]] || return 177
    /usr/bin/rm -- "$marker" || return 178
    local_systemd_marker_cleanup_count=$((local_systemd_marker_cleanup_count + 1))
    /usr/bin/sync -f "$private_root" || return 179
    [[ "$local_systemd_marker_cleanup_count" -eq 1 && \
        ! -e "$marker" && ! -L "$marker" && \
        ! -e "$private_marker" && ! -L "$private_marker" && \
        -z "$(/usr/bin/find "$mounted_root" -xdev -mindepth 1 -print -quit)" ]] || return 180
    receipt_hash_after="$(/usr/bin/sha256sum -- "$receipt")" || return 181
    receipt_hash_after="${receipt_hash_after%% *}"
    [[ "$receipt_hash_after" == "$receipt_hash_before" && \
        "$(/usr/bin/stat -c %s -- "$receipt")" == "$expected_receipt_size" && \
        "$(/usr/bin/cat -- "$receipt")"$'\n' == "$expected_receipt" ]] || return 181
}
""".strip()

PCP_PHASE11_WATCHDOG_GUARD_LOOKUP = r"""
watchdog_guard="${recovery_guard_paths_by_unit[watchdog.service]-}"
[[ -n "$watchdog_guard" ]]
[[ "${recovery_guard_paths_by_unit[watchdog.service]-}" == "$watchdog_guard" ]]
[[ "${recovery_guard_path_owners[$watchdog_guard]-}" == watchdog.service ]]
watchdog_guard_inode="${recovery_guard_file_inodes[$watchdog_guard]-}"
[[ "$watchdog_guard_inode" =~ ^[1-9][0-9]*$ && \
    -f "$watchdog_guard" && ! -L "$watchdog_guard" ]]
watchdog_guard_inode_now="$(stat -c %i -- "$watchdog_guard")"
[[ "$watchdog_guard_inode_now" == "$watchdog_guard_inode" ]]
[[ -n "${recovery_guard_condition_paths[$watchdog_guard]+present}" ]]
watchdog_condition="${recovery_guard_condition_paths[$watchdog_guard]-}"
[[ -n "$watchdog_condition" ]]
""".strip()

PCP_PHASE14_PEER_GUARD_LOOKUP = r"""
[[ -n "$peer_guard" && \
    "${recovery_guard_paths_by_unit[zfs.target]-}" == "$peer_guard" ]]
[[ "${recovery_guard_path_owners[$peer_guard]-}" == zfs.target ]]
peer_guard_inode="${recovery_guard_file_inodes[$peer_guard]-}"
[[ "$peer_guard_inode" =~ ^[1-9][0-9]*$ && \
    -f "$peer_guard" && ! -L "$peer_guard" ]]
peer_guard_inode_now="$(stat -c %i -- "$peer_guard")"
[[ "$peer_guard_inode_now" == "$peer_guard_inode" ]]
[[ -n "${recovery_guard_condition_paths[$peer_guard]+present}" ]]
peer_condition="${recovery_guard_condition_paths[$peer_guard]-}"
[[ -n "$peer_condition" ]]
""".strip()


def _assert_recovery_guard_path_key_contract(harness: str) -> None:
    for direct_key in (
        "recovery_guard_condition_paths[watchdog.service]",
        "recovery_guard_condition_paths[zfs.target]",
    ):
        if direct_key in harness:
            raise AssertionError(f"condition map uses unit-name key: {direct_key}")
    required = (
        PCP_PHASE11_WATCHDOG_GUARD_LOOKUP,
        '"ConditionPathExists=$watchdog_condition"',
        PCP_PHASE14_PEER_GUARD_LOOKUP,
        '"ConditionPathExists=$peer_condition"',
    )
    for fragment in required:
        if harness.count(fragment) != 1:
            raise AssertionError(
                f"recovery guard path-key contract is ambiguous: {fragment}"
            )
    if harness.index(PCP_PHASE11_WATCHDOG_GUARD_LOOKUP) > harness.index(
        '"ConditionPathExists=$watchdog_condition"'
    ):
        raise AssertionError("watchdog condition runs before path-key validation")
    if harness.index(PCP_PHASE14_PEER_GUARD_LOOKUP) > harness.index(
        '"ConditionPathExists=$peer_condition"'
    ):
        raise AssertionError("peer condition runs before path-key validation")


def _pcp_phase_ten_with_causal_proof() -> str:
    prefix = "trace_begin 10-host-manager-isolation host-manager-isolation\n"
    if PCP_OFFLINE_NONACTIVATION_PROOF.count(prefix) != 1:
        raise AssertionError("real PCP phase-10 entry is missing or ambiguous")
    return PCP_OFFLINE_NONACTIVATION_PROOF.replace(
        prefix,
        prefix
        + PCP_SYSTEMD_SOURCE_RECEIPT
        + "\n"
        + PCP_SYSTEMD_CAUSAL_PROOF
        + "\n"
        + PCP_LOCAL_SYSTEMD_MARKER_ORACLE
        + "\n",
        1,
    )


PCP_OFFLINE_NONACTIVATION_PROOF = r"""
trace_begin 10-host-manager-isolation host-manager-isolation
[[ -z "$(find "$work/run-systemd" -mindepth 1 -print -quit)" ]]
preset_enabled_state="$(SYSTEMD_OFFLINE=1 systemctl --root=/ is-enabled pmcd.service)"
[[ "$preset_enabled_state" == enabled ]]
post_configure_start_status=0
"$policy" pmcd.service start || post_configure_start_status=$?
[[ "$post_configure_start_status" -eq 101 ]]
validate_recovery_unit_guards
pmcd_guard="${recovery_guard_paths_by_unit[pmcd.service]}"
expected_pmcd_guard="$mask_root/pmcd.service.d/90-hoardarr-offline-recovery.conf"
[[ "$pmcd_guard" == "$expected_pmcd_guard" ]]
[[ "${recovery_guard_path_owners[$pmcd_guard]-}" == pmcd.service ]]
[[ "${recovery_guard_file_inodes[$pmcd_guard]-}" =~ ^[1-9][0-9]*$ ]]
[[ -f "$pmcd_guard" && ! -L "$pmcd_guard" ]]
entry_is_root_owned "$pmcd_guard"
[[ "$(stat -c %a -- "$pmcd_guard")" == 644 ]]
[[ "$(stat -c %i -- "$pmcd_guard")" == "${recovery_guard_file_inodes[$pmcd_guard]}" ]]
pmcd_guard_count=0
for guard in "${recovery_guard_files[@]}"; do
    [[ "$guard" != "$pmcd_guard" ]] || pmcd_guard_count=$((pmcd_guard_count + 1))
done
[[ "$pmcd_guard_count" -eq 1 ]]
expected_pmcd_condition=/dev/null/hoardarr-offline-service-guard/pmcd.service
[[ "${recovery_guard_condition_paths[$pmcd_guard]-}" == "$expected_pmcd_condition" ]]
expected_pmcd_content="$(printf '[Unit]\nConditionPathExists=%s\n' "$expected_pmcd_condition")"$'\n'
[[ "${recovery_guard_contents[$pmcd_guard]-}" == "$expected_pmcd_content" ]]
[[ "$(cat -- "$pmcd_guard")"$'\n' == "$expected_pmcd_content" ]]
[[ ! -e "$expected_pmcd_condition" && ! -L "$expected_pmcd_condition" ]]
[[ ! -e "$recovery_guard_authorization_root" && ! -L "$recovery_guard_authorization_root" ]]
[[ "$expected_pmcd_condition" != "$recovery_guard_authorization_root" && \
    "$expected_pmcd_condition" != "$recovery_guard_authorization_root/"* ]]
manager_root_snapshot before - "$work/manager-root-before.tsv"
systemd-analyze condition "ConditionPathExists=$expected_pmcd_condition" \
    >/dev/null 2>&1 && exit 100
condition_status=$?
manager_root_snapshot after "$condition_status" "$work/manager-root-after.tsv"
validate_and_remove_local_systemd_marker "$work/manager-root-after.tsv"
[[ "$local_systemd_marker_cleanup_count" -eq 1 ]]
[[ -z "$(find "$work/run-systemd" -mindepth 1 -print -quit)" ]]
trace_pass
""".strip()


def _assert_pcp_offline_nonactivation_contract(harness: str) -> None:
    required = (
        '[[ -z "$(find "$work/run-systemd" -mindepth 1 -print -quit)" ]]',
        "SYSTEMD_OFFLINE=1 systemctl --root=/ is-enabled pmcd.service",
        '"$policy" pmcd.service start || post_configure_start_status=$?',
        '[[ "$post_configure_start_status" -eq 101 ]]',
        "validate_recovery_unit_guards",
        'expected_pmcd_guard="$mask_root/pmcd.service.d/90-hoardarr-offline-recovery.conf"',
        '[[ "${recovery_guard_path_owners[$pmcd_guard]-}" == pmcd.service ]]',
        'entry_is_root_owned "$pmcd_guard"',
        '[[ "$(stat -c %a -- "$pmcd_guard")" == 644 ]]',
        "expected_pmcd_condition=/dev/null/hoardarr-offline-service-guard/pmcd.service",
        '[[ ! -e "$expected_pmcd_condition" && ! -L "$expected_pmcd_condition" ]]',
        '[[ ! -e "$recovery_guard_authorization_root" && ! -L "$recovery_guard_authorization_root" ]]',
        'manager_root_snapshot before - "$work/manager-root-before.tsv"',
        'systemd-analyze condition "ConditionPathExists=$expected_pmcd_condition"',
        "condition_status=$?",
        'manager_root_snapshot after "$condition_status" "$work/manager-root-after.tsv"',
        'validate_and_remove_local_systemd_marker "$work/manager-root-after.tsv"',
        '[[ "$local_systemd_marker_cleanup_count" -eq 1 ]]',
    )
    for fragment in required:
        if fragment not in harness:
            raise AssertionError(
                f"generated PCP harness lacks offline proof: {fragment}"
            )
    manager_root_check = (
        '[[ -z "$(find "$work/run-systemd" -mindepth 1 -print -quit)" ]]'
    )
    if harness.count(manager_root_check) != 2:
        raise AssertionError(
            "generated PCP harness must bracket offline proof with manager-root checks"
        )
    snapshot_sequence = (
        'manager_root_snapshot before - "$work/manager-root-before.tsv"\n'
        'systemd-analyze condition "ConditionPathExists=$expected_pmcd_condition" \\\n'
        "    >/dev/null 2>&1 && exit 100\n"
        "condition_status=$?\n"
        'manager_root_snapshot after "$condition_status" '
        '"$work/manager-root-after.tsv"\n'
        "validate_and_remove_local_systemd_marker "
        '"$work/manager-root-after.tsv"\n'
        '[[ "$local_systemd_marker_cleanup_count" -eq 1 ]]\n' + manager_root_check
    )
    if snapshot_sequence not in harness:
        raise AssertionError(
            "generated PCP harness changes condition or snapshot ordering semantics"
        )
    if re.search(r"systemctl\s+is-active\s+pmcd\.service", harness):
        raise AssertionError("generated PCP harness queries manager-dependent activity")
    for obsolete in ("pcp_active_state", "pcp_active_status"):
        if obsolete in harness:
            raise AssertionError(f"generated PCP harness retains obsolete {obsolete}")


def _pcp_trace_shell_prelude() -> str:
    return r"""
trace_file="$5"
current_phase=01-fixture-creation
current_label=fixture-creation
trace_terminal=false
trace_write() {
    local record="$1"
    (( ${#record} <= 240 ))
    printf '%s\n' "$record" >>"$trace_file"
}
trace_begin() {
    current_phase="$1"
    current_label="$2"
    trace_write "HPCP|1|BEGIN|$current_phase|status=-|line=-|function=-|label=$current_label"
}
trace_pass() {
    trace_write "HPCP|1|PASS|$current_phase|status=-|line=-|function=-|label=$current_label"
}
trace_failure() {
    local status="$1"
    local line="$2"
    local function="${FUNCNAME[1]:-main}"
    trap - ERR EXIT
    if [[ "$trace_terminal" != true ]]; then
        trace_terminal=true
        trace_write "HPCP|1|EXIT|$current_phase|status=$status|line=$line|function=$function|label=$current_label" || :
    fi
    exit "$status"
}
trace_exit() {
    local status="$1"
    local line="$2"
    trap - ERR EXIT
    if [[ "$trace_terminal" != true ]]; then
        trace_terminal=true
        trace_write "HPCP|1|EXIT|$current_phase|status=$status|line=$line|function=main|label=$current_label" || :
    fi
    exit "$status"
}
trap 'trace_failure "$?" "$LINENO"' ERR
trap 'trace_exit "$?" "$LINENO"' EXIT
""".strip()


def _append_pcp_trace_phase(
    trace_path: pathlib.Path, phase_index: int, kind: str
) -> None:
    phase, label = PCP_TRACE_PHASES[phase_index]
    if kind not in {"BEGIN", "PASS"}:
        raise AssertionError("invalid PCP trace phase marker")
    with trace_path.open("a", encoding="ascii", newline="\n") as trace:
        trace.write(f"HPCP|1|{kind}|{phase}|status=-|line=-|function=-|label={label}\n")


def _validate_pcp_trace(
    trace_path: pathlib.Path,
    fixture_root: pathlib.Path,
    namespace_path: pathlib.Path,
) -> tuple[str, int]:
    root = fixture_root.resolve(strict=True)
    namespace = namespace_path.resolve(strict=False)
    trace = trace_path.resolve(strict=False)
    if trace.parent != root or trace == namespace or namespace in trace.parents:
        raise AssertionError("PCP trace is outside the exact fixture root")
    if trace_path.is_symlink() or not trace_path.is_file():
        raise AssertionError("PCP trace is missing or is not a regular file")
    raw = trace_path.read_bytes()
    if not raw or len(raw) > PCP_TRACE_MAX_BYTES:
        raise AssertionError("PCP trace size is missing or unbounded")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise AssertionError("PCP trace is not bounded ASCII") from exc
    if not text.endswith("\n"):
        raise AssertionError("PCP trace is not newline terminated")
    lines = text.splitlines()
    if any(len(line.encode("ascii")) > PCP_TRACE_MAX_LINE_BYTES for line in lines):
        raise AssertionError("PCP trace contains an unbounded line")

    expected = list(PCP_TRACE_PHASES)
    expected_index = 0
    open_phase: tuple[str, str] | None = None
    terminal_status: int | None = None
    for index, line in enumerate(lines):
        match = PCP_TRACE_RECORD.fullmatch(line)
        if match is None:
            raise AssertionError(f"PCP trace record {index + 1} is malformed")
        kind, phase, status_text, line_text, function, label = match.groups()
        if kind == "BEGIN":
            if terminal_status is not None or open_phase is not None:
                raise AssertionError("PCP trace phase is duplicate or out of order")
            if (
                expected_index >= len(expected)
                or (phase, label) != expected[expected_index]
            ):
                raise AssertionError(
                    "PCP trace phase is unknown, missing, or out of order"
                )
            if (status_text, line_text, function) != ("-", "-", "-"):
                raise AssertionError("PCP BEGIN record contains unexpected fields")
            open_phase = (phase, label)
        elif kind == "PASS":
            if terminal_status is not None or open_phase != (phase, label):
                raise AssertionError("PCP trace PASS is duplicate or out of order")
            if (status_text, line_text, function) != ("-", "-", "-"):
                raise AssertionError("PCP PASS record contains unexpected fields")
            open_phase = None
            expected_index += 1
        else:
            if terminal_status is not None:
                raise AssertionError(
                    "PCP trace terminal receipt is duplicate or misplaced"
                )
            status = int(status_text) if status_text != "-" else -1
            source_line = int(line_text) if line_text != "-" else -1
            if not 0 <= status <= 255 or source_line <= 0 or function == "-":
                raise AssertionError(
                    "PCP trace terminal status or source identity is invalid"
                )
            if status == 0:
                if (
                    open_phase is not None
                    or expected_index != len(expected)
                    or (phase, label) != expected[-1]
                ):
                    raise AssertionError(
                        "PCP trace reports success before every phase passed"
                    )
            elif open_phase != (phase, label):
                raise AssertionError("PCP trace failure is not tied to the open phase")
            terminal_status = status
    if terminal_status is None:
        raise AssertionError(f"PCP trace has no terminal receipt:\n{text}")
    return text, terminal_status


def _validate_manager_root_receipt(
    receipt_path: pathlib.Path,
    fixture_root: pathlib.Path,
    manager_root: pathlib.Path,
    expected_stage: str,
) -> tuple[str, int | None, tuple[tuple[str, str, str, int, int], ...]]:
    if expected_stage not in {"before", "after"}:
        raise AssertionError("manager-root receipt stage is unsupported")
    root = fixture_root.resolve(strict=True)
    manager = manager_root.resolve(strict=True)
    expected = root / f"manager-root-{expected_stage}.tsv"
    if manager != root / "run-systemd" or manager.is_symlink():
        raise AssertionError("manager-root receipt uses an unexpected private root")
    if receipt_path != expected or receipt_path.parent != root:
        raise AssertionError("manager-root receipt is outside its exact fixture path")
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise AssertionError("manager-root receipt is missing or is not regular")
    raw = receipt_path.read_bytes()
    if not raw or len(raw) > PCP_MANAGER_ROOT_RECEIPT_MAX_BYTES:
        raise AssertionError("manager-root receipt size is missing or unbounded")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssertionError("manager-root receipt is not UTF-8") from exc
    if not text.isascii() or not text.endswith("\n") or "\r" in text:
        raise AssertionError("manager-root receipt encoding is not deterministic")
    if any(ord(character) < 32 and character not in {"\t", "\n"} for character in text):
        raise AssertionError("manager-root receipt contains a control character")
    if "\x7f" in text:
        raise AssertionError("manager-root receipt contains a control character")
    lines = text[:-1].split("\n")
    if not lines or not lines[0]:
        raise AssertionError("manager-root receipt header is missing")
    header = PCP_MANAGER_ROOT_HEADER.fullmatch(lines[0])
    if header is None:
        raise AssertionError("manager-root receipt header is malformed")
    stage, status_text = header.groups()
    if stage != expected_stage:
        raise AssertionError("manager-root receipt stage does not match its file")
    if stage == "before":
        if status_text != "-":
            raise AssertionError("before manager-root receipt has a status")
        condition_status: int | None = None
    else:
        if status_text == "-":
            raise AssertionError("after manager-root receipt lacks condition status")
        condition_status = int(status_text)
        if condition_status <= 0 or condition_status > 255:
            raise AssertionError("manager-root condition status is out of range")
    entry_lines = lines[1:]
    if len(entry_lines) > PCP_MANAGER_ROOT_RECEIPT_MAX_ENTRIES:
        raise AssertionError("manager-root receipt has too many entries")
    entries: list[tuple[str, str, str, int, int]] = []
    previous_path: bytes | None = None
    for index, line in enumerate(entry_lines, start=1):
        fields = line.split("\t")
        if len(fields) != 6 or fields[0] != "ENTRY":
            raise AssertionError(f"manager-root receipt entry {index} is malformed")
        relative_path, object_type, mode, uid_text, gid_text = fields[1:]
        path_bytes = relative_path.encode("ascii")
        if not path_bytes or len(path_bytes) > PCP_MANAGER_ROOT_PATH_MAX_BYTES:
            raise AssertionError("manager-root receipt path is missing or unbounded")
        if relative_path.startswith("/") or "\\" in relative_path:
            raise AssertionError(
                "manager-root receipt path is absolute or noncanonical"
            )
        components = relative_path.split("/")
        if len(components) > 5 or any(
            component in {"", ".", ".."}
            or PCP_MANAGER_ROOT_COMPONENT.fullmatch(component) is None
            for component in components
        ):
            raise AssertionError("manager-root receipt path is unsafe")
        if previous_path is not None and path_bytes <= previous_path:
            raise AssertionError("manager-root receipt paths are duplicate or unsorted")
        previous_path = path_bytes
        if object_type not in PCP_MANAGER_ROOT_TYPES:
            raise AssertionError("manager-root receipt object type is unknown")
        if re.fullmatch(r"[0-7]{3,4}", mode) is None:
            raise AssertionError("manager-root receipt mode is malformed")
        if not uid_text.isascii() or not uid_text.isdecimal():
            raise AssertionError("manager-root receipt UID is malformed")
        if not gid_text.isascii() or not gid_text.isdecimal():
            raise AssertionError("manager-root receipt GID is malformed")
        uid = int(uid_text)
        gid = int(gid_text)
        if uid > 2**32 - 1 or gid > 2**32 - 1:
            raise AssertionError("manager-root receipt ownership is out of range")
        entries.append((relative_path, object_type, mode, uid, gid))
    return text, condition_status, tuple(entries)


def _read_bounded_ascii_receipt(
    receipt_path: pathlib.Path,
    fixture_root: pathlib.Path,
    expected_name: str,
    maximum_bytes: int,
) -> str:
    root = fixture_root.resolve(strict=True)
    expected = root / expected_name
    if receipt_path != expected or receipt_path.parent != root:
        raise AssertionError("receipt is outside its exact fixture path")
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise AssertionError("receipt is missing or is not regular")
    raw = receipt_path.read_bytes()
    if not raw or len(raw) > maximum_bytes:
        raise AssertionError("receipt size is missing or unbounded")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise AssertionError("receipt is not bounded ASCII") from exc
    if not text.endswith("\n") or "\r" in text:
        raise AssertionError("receipt encoding is not deterministic")
    if any(ord(character) < 32 and character not in {"\t", "\n"} for character in text):
        raise AssertionError("receipt contains a control character")
    if "\x7f" in text:
        raise AssertionError("receipt contains a control character")
    return text


def _validate_systemd_source_receipt(
    receipt_path: pathlib.Path,
    fixture_root: pathlib.Path,
) -> tuple[str, str, str]:
    text = _read_bounded_ascii_receipt(
        receipt_path,
        fixture_root,
        "systemd-source.tsv",
        PCP_SYSTEMD_SOURCE_RECEIPT_MAX_BYTES,
    )
    lines = text[:-1].split("\n")
    if len(lines) != 9 or lines[0] != "HSOURCE|1":
        raise AssertionError("systemd source receipt schema is incomplete")
    package = lines[1].split("\t")
    if len(package) != 4 or package[0:2] != ["PACKAGE", "systemd"]:
        raise AssertionError("systemd package identity is malformed")
    package_version, architecture = package[2:]
    if re.fullmatch(r"255\.4-[0-9A-Za-z.+:~]+", package_version) is None:
        raise AssertionError("systemd package version is not tied to upstream v255.4")
    if architecture != "amd64":
        raise AssertionError("systemd package architecture is unexpected")
    version = lines[2].split("\t")
    if len(version) != 3 or version[0] != "VERSION":
        raise AssertionError("systemd version receipt is malformed")
    if version[1] != f"systemd 255 ({package_version})":
        raise AssertionError("systemd executable and package versions diverge")
    if re.fullmatch(r"[0-9a-f]{64}", version[2]) is None:
        raise AssertionError("systemd version-output hash is malformed")
    executable = lines[3].split("\t")
    if len(executable) != 9 or executable[0:2] != [
        "EXECUTABLE",
        "/usr/bin/systemd-analyze",
    ]:
        raise AssertionError("systemd executable identity is malformed")
    executable_hash, mode, uid, gid, size, links, owner = executable[2:]
    if (
        re.fullmatch(r"[0-9a-f]{64}", executable_hash) is None
        or re.fullmatch(r"[0-7]{3,4}", mode) is None
        or uid != "0"
        or gid != "0"
        or not size.isdecimal()
        or int(size) <= 0
        or not links.isdecimal()
        or int(links) <= 0
        or owner != "systemd"
    ):
        raise AssertionError("systemd executable metadata is invalid")
    expected_lines = (
        (
            f"UPSTREAM\t{PCP_SYSTEMD_UPSTREAM_REPOSITORY}\t"
            f"{PCP_SYSTEMD_UPSTREAM_TAG}\t{PCP_SYSTEMD_UPSTREAM_REVISION}"
        ),
        (
            f"SOURCE\t{PCP_SYSTEMD_ANALYZE_SOURCE_PATH}\t"
            f"{PCP_SYSTEMD_ANALYZE_SOURCE_FUNCTION}\t"
            f"{PCP_SYSTEMD_ANALYZE_SOURCE_SHA256}"
        ),
        (
            f"SOURCE\t{PCP_SYSTEMD_MANAGER_SOURCE_PATH}\t"
            f"{PCP_SYSTEMD_MANAGER_SOURCE_FUNCTION}\t"
            f"{PCP_SYSTEMD_MANAGER_SOURCE_SHA256}"
        ),
        "CHAIN\tverb_condition>verify_conditions>manager_startup>manager_ready>touch_file",
        f"MARKER\t{PCP_SYSTEMD_MARKER_PATH}\tregular\t0444\tzero-length\tmanager-ready",
    )
    if tuple(lines[4:]) != expected_lines:
        raise AssertionError("systemd immutable upstream source identity diverges")
    return text, package_version, executable_hash


def _validate_systemd_causal_receipt(
    receipt_path: pathlib.Path,
    fixture_root: pathlib.Path,
) -> str:
    text = _read_bounded_ascii_receipt(
        receipt_path,
        fixture_root,
        "systemd-causal.tsv",
        PCP_SYSTEMD_CAUSAL_RECEIPT_MAX_BYTES,
    )
    expected = (
        "HCAUSE|1\n"
        "CONTROL\tnegative\tcommand=none\tstatus=-\tbefore=0\tafter=0\t"
        "manager_endpoints_before=0\tmanager_endpoints_after=0\tcleanup=removed\n"
        "CONTROL\tpositive\tcommand=systemd-analyze-condition\tstatus=1\t"
        "before=0\tafter=1\tmanager_endpoints_before=0\t"
        "manager_endpoints_after=0\tcleanup=removed\n"
        "MARKER\tsystemd-units-load\tregular\t444\t0\t0\t0\t1\t"
        "same-filesystem\n"
    )
    if text != expected:
        raise AssertionError("systemd causal receipt is malformed or incomplete")
    return text


def _extract_systemd_receipt_writer(shell: str, partial_variable: str) -> str:
    end_marker = f'}} >"${partial_variable}"'
    end = shell.find(end_marker)
    if end < 0 or shell.find(end_marker, end + 1) >= 0:
        raise AssertionError(
            f"receipt writer for {partial_variable} is missing or ambiguous"
        )
    start = shell.rfind("{\n", 0, end)
    if start < 0:
        raise AssertionError(
            f"receipt writer for {partial_variable} has no group start"
        )
    writer = shell[start : end + len(end_marker)]
    if writer.count("printf ") < 2 or "%b" in writer or "echo -e" in writer:
        raise AssertionError(f"receipt writer for {partial_variable} is not field-safe")
    return writer


class OfflineApplianceTests(unittest.TestCase):
    @staticmethod
    def _actual_install_fragment(payload: str) -> str:
        match = re.search(
            r'^chroot "\$target" apt-get "\$\{apt_options\[@\]\}" \\\n'
            r'    --yes [^\n]+ install "\$\{exact_roots\[@\]\}"$',
            payload,
            flags=re.MULTILINE,
        )
        if match is None:
            raise AssertionError("missing unique production actual-install command")
        if payload.count(match.group(0)) != 1:
            raise AssertionError("production actual-install command is ambiguous")
        return match.group(0)

    @classmethod
    def _assert_actual_install_contract(cls, payload: str) -> None:
        fragment = cls._actual_install_fragment(payload)
        expected_fragment = (
            'chroot "$target" apt-get "${apt_options[@]}" \\\n'
            '    --yes --no-install-recommends install "${exact_roots[@]}"'
        )
        if fragment != expected_fragment:
            raise AssertionError("production actual-install argv changed")
        required = (
            "sha256sum --check --strict SHA256SUMS",
            'cp -a -- "$source_repo" "$retained_repo"',
            "deb [signed-by=/usr/share/keyrings/hoardarr-offline-archive-keyring.gpg] file:/opt/hoardarr/offline-repository noble main",
            "*.hoardarr-online-disabled",
            '-o "Dir::Etc::sourcelist=/etc/apt/sources.list.d/hoardarr-offline.list"',
            '-o "Dir::Etc::sourceparts=-"',
            '-o "Acquire::Retries=0"',
            '-o "Acquire::http::Proxy=false"',
            '-o "Acquire::https::Proxy=false"',
            "policy-rc.d",
            "export SYSTEMD_OFFLINE=1",
            "package_transaction_started=true",
            "service_readback_complete=true",
            "Failed to preset unit",
            "AUTO -all",
            'global_filter = [ "r|.*|" ]',
            'devnode ".*"',
            'mapfile -t exact_roots <"$retained_repo/evidence/root-package-versions.txt"',
            'simulation="$(chroot "$target" apt-get "${apt_options[@]}" --simulate --no-install-recommends install "${exact_roots[@]}")"',
            'chroot "$target" apt-get "${apt_options[@]}" --simulate check',
            "package-readback.json",
            "service-policy-readback.json",
            "service-retained-guards.json",
        )
        for value in required:
            if value not in payload:
                raise AssertionError(f"missing offline install safeguard: {value}")
        lowered = payload.lower()
        for forbidden in (
            "trusted=yes",
            "allow-unauthenticated",
            "allowinsecurerepositories=true",
        ):
            if forbidden in lowered:
                raise AssertionError(f"signature safeguard weakened: {forbidden}")
        if "http://" in payload or "https://" in payload:
            raise AssertionError("network source introduced into offline payload")
        if "--no-download" in fragment:
            raise AssertionError(
                "actual install cannot acquire from the file repository"
            )

    @staticmethod
    def _assert_runtime_mount_contract(payload: str) -> None:
        required = (
            "exec {parent_namespace_fd}< /proc/self/ns/mnt",
            "unshare --mount --propagation private",
            "--hoardarr-private-mount-namespace",
            "unset HOARDARR_OFFLINE_PRIVATE_MOUNT_NAMESPACE HOARDARR_OFFLINE_PARENT_MOUNT_NAMESPACE",
            'current_mount_namespace="$(readlink -- /proc/self/ns/mnt)"',
            'open("/proc/self/mountinfo", encoding="utf-8")',
            "runtime_mount_paths=(proc sys dev dev/pts run)",
            "runtime_mount_sources=(/proc /sys /dev /dev/pts /run)",
            'mount --bind -- "$source" "$destination"',
            'if mount --bind -- "$source" "$destination"; then',
            'runtime_mount_ids["$destination"]="$mount_id"',
            'mount --make-private -- "$destination"',
            'if mount --make-private -- "$destination"; then',
            'prepare_runtime_mounts_failure "$bind_status"',
            'prepare_runtime_mounts_failure "$propagation_status"',
            'rollback_just_attempted_runtime_mount "$destination"',
            "if ! printf 'mount_id\\tparent_id\\tmajor_minor",
            'if ! sync -f "$runtime_mount_record"',
            'umount -- "$destination"',
            "cleanup_runtime_mounts || cleanup_status=1",
            "trap 'exit_cleanup $?' EXIT",
            "trap 'signal_exit 143' TERM",
            "prepare_runtime_mounts",
            "cleanup_service_guards",
            "disable_unmasked_units",
            "export SYSTEMD_OFFLINE=1",
            "cleanup_runtime_mounts",
            "cleanup_guard 0",
            "trap - EXIT HUP INT TERM",
        )
        for value in required:
            if value not in payload:
                raise AssertionError(f"missing runtime mount safeguard: {value}")
        exact_counts = {
            'if mount --bind -- "$source" "$destination"; then': 1,
            'if mount --make-private -- "$destination"; then': 1,
            'prepare_runtime_mounts_failure "$bind_status"': 2,
            'prepare_runtime_mounts_failure "$propagation_status"': 1,
            'rollback_just_attempted_runtime_mount "$destination"': 4,
        }
        for value, expected in exact_counts.items():
            if payload.count(value) != expected:
                raise AssertionError(
                    f"runtime mutation handling count changed: {value}"
                )
        for forbidden in ("mount --rbind", "umount -l", "umount --lazy"):
            if forbidden in payload:
                raise AssertionError(f"unsafe runtime mount operation: {forbidden}")
        prepare = payload.rindex("\nprepare_runtime_mounts\n")
        first_chroot = payload.index('chroot "$target" apt-get', prepare)
        service_cleanup = payload.rindex("\ncleanup_service_guards\n")
        disable = payload.rindex("\ndisable_unmasked_units\n")
        runtime_cleanup = payload.rindex("\ncleanup_runtime_mounts\n")
        success = payload.rindex(
            'echo "Hoardarr offline package payload installed and verified."'
        )
        if not (
            prepare
            < first_chroot
            < disable
            < service_cleanup
            < runtime_cleanup
            < success
        ):
            raise AssertionError("runtime mount lifecycle ordering changed")

    def test_target_runtime_mount_contract_rejects_mutations(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        self._assert_runtime_mount_contract(payload)
        mutations = {
            "caller sentinel trusted": payload.replace(
                "unset HOARDARR_OFFLINE_PRIVATE_MOUNT_NAMESPACE HOARDARR_OFFLINE_PARENT_MOUNT_NAMESPACE",
                ":",
            ),
            "no private namespace": payload.replace(
                "unshare --mount --propagation private",
                "unshare --mount --propagation unchanged",
            ),
            "mountinfo removed": payload.replace(
                'open("/proc/self/mountinfo", encoding="utf-8")',
                'open("/etc/mtab", encoding="utf-8")',
            ),
            "runtime path missing": payload.replace(
                "runtime_mount_paths=(proc sys dev dev/pts run)",
                "runtime_mount_paths=(proc sys dev run)",
            ),
            "ID not recorded": payload.replace(
                'runtime_mount_ids["$destination"]="$mount_id"',
                ":",
                1,
            ),
            "propagation not isolated": payload.replace(
                'if mount --make-private -- "$destination"; then',
                ":",
            ),
            "bind status not checked": payload.replace(
                'if mount --bind -- "$source" "$destination"; then',
                'mount --bind -- "$source" "$destination"\n        if true; then',
            ),
            "bind failure cleanup missing": payload.replace(
                'prepare_runtime_mounts_failure "$bind_status"',
                "return 1",
                1,
            ),
            "ambiguous bind rollback missing": payload.replace(
                'rollback_just_attempted_runtime_mount "$destination"',
                "false",
            ),
            "lazy cleanup": payload.replace(
                'umount -- "$destination"',
                'umount --lazy -- "$destination"',
                1,
            ),
            "EXIT cleanup missing": payload.replace(
                "trap 'exit_cleanup $?' EXIT",
                ":",
            ),
            "TERM cleanup missing": payload.replace(
                "trap 'signal_exit 143' TERM",
                ":",
            ),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name), self.assertRaises(AssertionError):
                self._assert_runtime_mount_contract(mutation)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux mounts")
    def test_target_runtime_mount_lifecycle_and_package_postinst(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        self._assert_runtime_mount_contract(payload)

        def shell_function(name: str) -> str:
            match = re.search(
                rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n",
                payload,
                flags=re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"missing production function {name}")
            assert match is not None
            return match.group(0)

        required = (
            "bash",
            "dpkg",
            "dpkg-deb",
            "mount",
            "sudo",
            "umount",
            "unshare",
        )
        missing = [command for command in required if shutil.which(command) is None]
        self.assertEqual(missing, [], f"missing runtime integration tools: {missing}")
        sudo = subprocess.run(
            ["sudo", "-n", "true"], text=True, capture_output=True, check=False
        )
        self.assertEqual(sudo.returncode, 0, sudo.stderr)
        names = (
            "mountinfo_exact_record",
            "runtime_path_is_safe",
            "runtime_record_field",
            "prepare_runtime_mounts_failure",
            "runtime_mount_matches_source",
            "runtime_mount_path_is_absent",
            "rollback_just_attempted_runtime_mount",
            "prepare_runtime_mounts",
            "cleanup_runtime_mounts",
            "cleanup_guard",
            "exit_cleanup",
            "signal_exit",
        )
        fragment = "\n".join(
            (
                "runtime_mount_paths=(proc sys dev dev/pts run)",
                "runtime_mount_sources=(/proc /sys /dev /dev/pts /run)",
                "created_runtime_mounts=()",
                "declare -A runtime_mount_ids=()",
                "declare -A runtime_mount_records=()",
                *(shell_function(name) for name in names),
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = pathlib.Path(temporary)
            wrapper_root = temporary_root / "bin"
            wrapper_root.mkdir()
            launcher_marker = temporary_root / "unshare-invoked"
            real_unshare = pathlib.Path(shutil.which("unshare") or "")
            unshare_wrapper = wrapper_root / "unshare"
            unshare_wrapper.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' invoked >'{launcher_marker}'\n"
                f"exec '{real_unshare}' \"$@\"\n",
                encoding="utf-8",
                newline="\n",
            )
            unshare_wrapper.chmod(0o755)
            launcher_target = temporary_root / "launcher-target"
            launcher_repository = temporary_root / "launcher-repository"
            launcher_target.mkdir()
            launcher_repository.mkdir()
            launcher_payload = temporary_root / "install-offline-payload.sh"
            shutil.copyfile(
                ROOT / "packaging" / "appliance" / "install-offline-payload.sh",
                launcher_payload,
            )
            launcher_payload.chmod(0o755)
            launcher = subprocess.run(
                [
                    "sudo",
                    "-n",
                    "env",
                    f"PATH={wrapper_root}:{os.environ.get('PATH', '')}",
                    "HOARDARR_OFFLINE_PRIVATE_MOUNT_NAMESPACE=1",
                    "HOARDARR_OFFLINE_PARENT_MOUNT_NAMESPACE=mnt:[1]",
                    str(launcher_payload),
                    str(launcher_target),
                    str(launcher_repository),
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertNotEqual(launcher.returncode, 0)
            self.assertTrue(
                launcher_marker.is_file(),
                "preseeded variables bypassed the production unshare launcher",
            )
            self.assertIn(
                "offline payload target must be the real /target directory",
                launcher.stderr,
            )
            fragment_path = pathlib.Path(temporary) / "production-runtime-functions.sh"
            fragment_path.write_text(fragment, encoding="utf-8", newline="\n")
            result = subprocess.run(
                [
                    "sudo",
                    "-n",
                    "bash",
                    str(
                        ROOT
                        / "tests"
                        / "appliance"
                        / "test-target-chroot-runtime-mounts.sh"
                    ),
                    str(fragment_path),
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=240,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("private_namespace_containment=true", result.stdout)
        self.assertIn("postinst_runtime_probe=passed", result.stdout)
        self.assertIn("partial_and_signal_cleanup=passed", result.stdout)

    def test_offline_service_masks_are_classified_and_cleaned_executably(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")

        def shell_function(name: str) -> str:
            match = re.search(
                rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n",
                payload,
                flags=re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"missing production function {name}")
            assert match is not None
            return match.group(0)

        if sys.platform == "win32":
            candidates = (
                pathlib.Path(r"C:\Program Files\Git\bin\bash.exe"),
                pathlib.Path(r"C:\msys64\usr\bin\bash.exe"),
            )
            bash = next((str(path) for path in candidates if path.is_file()), None)
        else:
            bash = shutil.which("bash")
        self.assertIsNotNone(bash, "Bash is required for executable mask regression")
        assert bash is not None

        script = "\n".join(
            (
                "set -euo pipefail",
                "temporary_masks=()",
                "declare -A temporary_mask_inodes=()",
                "temporary_masks_cleanup_complete=false",
                "policy_cleanup_complete=false",
                "service_guard_cleanup_complete=false",
                "package_transaction_started=false",
                "denied_units_finalized=false",
                "service_readback_complete=false",
                "created_runtime_mounts=()",
                "declare -A runtime_mount_ids=()",
                "declare -A runtime_mount_records=()",
                "declare -A preserved_unit_masks=()",
                "declare -A preserved_unit_mask_inodes=()",
                "declare -A preserved_package_aliases=()",
                "declare -A preserved_package_alias_inodes=()",
                "declare -A preserved_package_alias_targets=()",
                "declare -A preserved_package_alias_canonical_units=()",
                "declare -A policy_guarded_canonical_units=()",
                "declare -A policy_guarded_absent_units=()",
                "recovery_guard_files=()",
                "recovery_guard_created_directories=()",
                "declare -A recovery_guard_file_inodes=()",
                "declare -A recovery_guard_contents=()",
                "declare -A recovery_guard_condition_paths=()",
                "declare -A recovery_guard_directory_inodes=()",
                "declare -A recovery_guard_paths_by_unit=()",
                "declare -A recovery_guard_path_owners=()",
                "declare -A recovery_guard_paths_retained=()",
                "declare -A recovery_guard_retained_states=()",
                "recovery_guards_cleanup_complete=false",
                "recovery_guard_authorization_root=",
                shell_function("entry_is_root_owned"),
                shell_function("exact_iscsi_alias_parents_are_safe"),
                shell_function("unit_declares_exact_alias"),
                shell_function("is_exact_package_backed_iscsi_alias"),
                shell_function("record_package_backed_iscsi_alias"),
                shell_function("validate_preserved_unit_objects"),
                shell_function("prepare_recovery_unit_guard"),
                shell_function("validate_recovery_unit_guards"),
                shell_function("retain_recovery_unit_guards"),
                shell_function("remove_recovery_unit_guards"),
                shell_function("prepare_temporary_unit_mask"),
                shell_function("cleanup_temporary_masks"),
                shell_function("disable_unmasked_units"),
                "reset_tracking() {",
                "  temporary_masks=()",
                "  temporary_mask_inodes=()",
                "  preserved_unit_masks=()",
                "  preserved_unit_mask_inodes=()",
                "  preserved_package_aliases=()",
                "  preserved_package_alias_inodes=()",
                "  preserved_package_alias_targets=()",
                "  preserved_package_alias_canonical_units=()",
                "  policy_guarded_canonical_units=()",
                "  policy_guarded_absent_units=()",
                "  recovery_guard_files=()",
                "  recovery_guard_created_directories=()",
                "  recovery_guard_file_inodes=()",
                "  recovery_guard_contents=()",
                "  recovery_guard_condition_paths=()",
                "  recovery_guard_directory_inodes=()",
                "  recovery_guard_paths_by_unit=()",
                "  recovery_guard_path_owners=()",
                "  recovery_guard_paths_retained=()",
                "  recovery_guard_retained_states=()",
                "  recovery_guards_cleanup_complete=false",
                "  denied_units_finalized=false",
                "}",
                'root="$1"',
                'if command -v cygpath >/dev/null 2>&1; then root="$(cygpath -u -- "$root")"; fi',
                'mkdir -p -- "$root"',
                'target="$root/no-package-root"',
                'mask_root="$target/etc/systemd/system"',
                "",
                "# Newly absent units remain absent so package preset bookkeeping works.",
                'absent="$root/absent/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$absent")"',
                "reset_tracking",
                'prepare_temporary_unit_mask "$absent" iscsi.service',
                '[[ ! -e "$absent" && ! -L "$absent" ]]',
                '[[ "${policy_guarded_absent_units[iscsi.service]}" == "$absent" ]]',
                "cleanup_temporary_masks",
                '[[ ! -e "$absent" && ! -L "$absent" ]]',
                'later="$root/later-failure/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$later")"',
                "later_status=0",
                "(",
                "  reset_tracking",
                "  trap cleanup_temporary_masks EXIT",
                '  prepare_temporary_unit_mask "$later" iscsi.service',
                "  exit 79",
                ") || later_status=$?",
                '[[ "$later_status" -eq 79 && ! -e "$later" && ! -L "$later" ]]',
                "",
                "# A pre-existing exact absolute mask is never tracked or recreated.",
                'safe="$root/safe/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$safe")"',
                'ln -s -- /dev/null "$safe"',
                'safe_inode="$(stat -c %i -- "$safe")"',
                "reset_tracking",
                'prepare_temporary_unit_mask "$safe" iscsi.service',
                '[[ "${#temporary_masks[@]}" -eq 0 ]]',
                '[[ "${preserved_unit_masks[iscsi.service]}" == "$safe" ]]',
                "cleanup_temporary_masks",
                '[[ -L "$safe" && "$(readlink -- "$safe")" == /dev/null ]]',
                '[[ "$(stat -c %i -- "$safe")" == "$safe_inode" ]]',
                'safe_failure="$root/safe-failure/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$safe_failure")"',
                'ln -s -- /dev/null "$safe_failure"',
                'safe_failure_inode="$(stat -c %i -- "$safe_failure")"',
                "safe_status=0",
                "(",
                "  reset_tracking",
                "  trap cleanup_temporary_masks EXIT",
                '  prepare_temporary_unit_mask "$safe_failure" iscsi.service',
                "  exit 81",
                ") || safe_status=$?",
                '[[ "$safe_status" -eq 81 ]]',
                '[[ -L "$safe_failure" && "$(readlink -- "$safe_failure")" == /dev/null ]]',
                '[[ "$(stat -c %i -- "$safe_failure")" == "$safe_failure_inode" ]]',
                "",
                "# Every other pre-existing object is rejected without modification.",
                'regular="$root/reject-regular/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$regular")"',
                'printf preserved >"$regular"',
                'regular_sha="$(sha256sum "$regular")"',
                'if prepare_temporary_unit_mask "$regular" iscsi.service; then exit 91; fi',
                '[[ "$(sha256sum "$regular")" == "$regular_sha" ]]',
                'directory="$root/reject-directory/iscsi.service"',
                'mkdir -p -- "$directory"',
                'printf marker >"$directory/preserved"',
                'if prepare_temporary_unit_mask "$directory" iscsi.service; then exit 92; fi',
                '[[ "$(cat "$directory/preserved")" == marker ]]',
                'target="$root/ordinary-target"',
                'printf target >"$target"',
                'ordinary="$root/reject-symlink/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$ordinary")"',
                'ln -s -- "$target" "$ordinary"',
                'ordinary_inode="$(stat -c %i -- "$ordinary")"',
                'if prepare_temporary_unit_mask "$ordinary" iscsi.service; then exit 93; fi',
                '[[ "$(readlink -- "$ordinary")" == "$target" ]]',
                '[[ "$(stat -c %i -- "$ordinary")" == "$ordinary_inode" ]]',
                'dangling="$root/reject-dangling/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$dangling")"',
                'ln -s -- /does/not/exist "$dangling"',
                'if prepare_temporary_unit_mask "$dangling" iscsi.service; then exit 94; fi',
                '[[ "$(readlink -- "$dangling")" == /does/not/exist ]]',
                'relative="$root/reject-relative/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$relative")"',
                'ln -s -- ../../dev/null "$relative"',
                'if prepare_temporary_unit_mask "$relative" iscsi.service; then exit 95; fi',
                '[[ "$(readlink -- "$relative")" == ../../dev/null ]]',
                "",
                "# Mixed ownership cleanup preserves existing and leaves absent units absent.",
                'mixed_safe="$root/mixed/iscsi.service"',
                'mixed_new="$root/mixed/iscsid.service"',
                'mkdir -p -- "$(dirname -- "$mixed_safe")"',
                'ln -s -- /dev/null "$mixed_safe"',
                'mixed_inode="$(stat -c %i -- "$mixed_safe")"',
                "reset_tracking",
                'prepare_temporary_unit_mask "$mixed_safe" iscsi.service',
                'prepare_temporary_unit_mask "$mixed_new" iscsid.service',
                '[[ "${#temporary_masks[@]}" -eq 0 ]]',
                '[[ "${policy_guarded_absent_units[iscsid.service]}" == "$mixed_new" ]]',
                "cleanup_temporary_masks",
                '[[ -L "$mixed_safe" && "$(readlink -- "$mixed_safe")" == /dev/null ]]',
                '[[ "$(stat -c %i -- "$mixed_safe")" == "$mixed_inode" ]]',
                '[[ ! -e "$mixed_new" && ! -L "$mixed_new" ]]',
                "",
                "# Final disable preserves the accepted mask and validates the absent unit.",
                'lifecycle_safe="$root/lifecycle/iscsi.service"',
                'lifecycle_new="$root/lifecycle/iscsid.service"',
                'mkdir -p -- "$(dirname -- "$lifecycle_safe")"',
                'ln -s -- /dev/null "$lifecycle_safe"',
                'lifecycle_inode="$(stat -c %i -- "$lifecycle_safe")"',
                'disable_log="$root/disable.log"',
                "reset_tracking",
                'prepare_temporary_unit_mask "$lifecycle_safe" iscsi.service',
                'prepare_temporary_unit_mask "$lifecycle_new" iscsid.service',
                "denied_units=(iscsi.service iscsid.service)",
                'target="$root/target"',
                'state_root="$root/state"',
                'mkdir -p -- "$state_root"',
                "active_mode=inactive",
                "systemctl() {",
                '  printf \'%s\\n\' "$*" >>"$disable_log"',
                '  if [[ "$*" == *" is-enabled "* ]]; then',
                "    if [[ \"${*: -1}\" == iscsi.service ]]; then printf '%s\\n' masked; else printf '%s\\n' not-found; fi",
                "    return 1",
                "  fi",
                "  return 0",
                "}",
                "chroot() {",
                "  if [[ \"$active_mode\" == active ]]; then printf '%s\\n' active; return 0; fi",
                "  if [[ \"$active_mode\" == ambiguous ]]; then printf '%s\\n' unknown; return 4; fi",
                "  printf '%s\\n' inactive; return 3",
                "}",
                "disable_unmasked_units",
                '[[ -L "$lifecycle_safe" && "$(readlink -- "$lifecycle_safe")" == /dev/null ]]',
                '[[ "$(stat -c %i -- "$lifecycle_safe")" == "$lifecycle_inode" ]]',
                '[[ ! -e "$lifecycle_new" && ! -L "$lifecycle_new" ]]',
                'grep -Fq -- "--root=$target disable iscsid.service" "$disable_log"',
                '[[ "$denied_units_finalized" == true ]]',
                "",
                "# Active and ambiguous target observations both fail closed.",
                'active_mask="$root/active/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$active_mask")"',
                'ln -s -- /dev/null "$active_mask"',
                "reset_tracking",
                'prepare_temporary_unit_mask "$active_mask" iscsi.service',
                "denied_units=(iscsi.service)",
                "active_mode=active",
                "if disable_unmasked_units >/dev/null 2>&1; then exit 96; fi",
                '[[ -L "$active_mask" && "$(readlink -- "$active_mask")" == /dev/null ]]',
                "active_mode=ambiguous",
                "if disable_unmasked_units >/dev/null 2>&1; then exit 97; fi",
                '[[ -L "$active_mask" && "$(readlink -- "$active_mask")" == /dev/null ]]',
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            script_path = pathlib.Path(temporary) / "mask-regression.sh"
            script_path.write_text(script, encoding="utf-8", newline="\n")
            result = subprocess.run(
                [bash, str(script_path), temporary],
                capture_output=True,
                check=False,
                env={**os.environ, "MSYS": "winsymlinks:sys"},
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("command not found", result.stderr)

    def test_package_backed_iscsi_alias_lifecycle_is_fail_closed(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")

        def shell_function(name: str) -> str:
            match = re.search(
                rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n",
                payload,
                flags=re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"missing production function {name}")
            assert match is not None
            return match.group(0)

        if sys.platform == "win32":
            candidates = (
                pathlib.Path(r"C:\Program Files\Git\bin\bash.exe"),
                pathlib.Path(r"C:\msys64\usr\bin\bash.exe"),
            )
            bash = next((str(path) for path in candidates if path.is_file()), None)
        else:
            bash = shutil.which("bash")
        self.assertIsNotNone(bash, "Bash is required for executable alias regression")
        assert bash is not None

        script = "\n".join(
            (
                "set -euo pipefail",
                "temporary_masks=()",
                "declare -A temporary_mask_inodes=()",
                "temporary_masks_cleanup_complete=false",
                "policy_cleanup_complete=false",
                "service_guard_cleanup_complete=false",
                "package_transaction_started=false",
                "denied_units_finalized=false",
                "service_readback_complete=false",
                "created_runtime_mounts=()",
                "declare -A runtime_mount_ids=()",
                "declare -A runtime_mount_records=()",
                "declare -A preserved_unit_masks=()",
                "declare -A preserved_unit_mask_inodes=()",
                "declare -A preserved_package_aliases=()",
                "declare -A preserved_package_alias_inodes=()",
                "declare -A preserved_package_alias_targets=()",
                "declare -A preserved_package_alias_canonical_units=()",
                "declare -A policy_guarded_canonical_units=()",
                "declare -A policy_guarded_absent_units=()",
                "recovery_guard_files=()",
                "recovery_guard_created_directories=()",
                "declare -A recovery_guard_file_inodes=()",
                "declare -A recovery_guard_contents=()",
                "declare -A recovery_guard_condition_paths=()",
                "declare -A recovery_guard_directory_inodes=()",
                "declare -A recovery_guard_paths_by_unit=()",
                "declare -A recovery_guard_path_owners=()",
                "declare -A recovery_guard_paths_retained=()",
                "declare -A recovery_guard_retained_states=()",
                "recovery_guards_cleanup_complete=false",
                "recovery_guard_authorization_root=",
                shell_function("install_service_start_guard"),
                shell_function("entry_is_root_owned"),
                shell_function("exact_iscsi_alias_parents_are_safe"),
                shell_function("unit_declares_exact_alias"),
                shell_function("is_exact_package_backed_iscsi_alias"),
                shell_function("record_package_backed_iscsi_alias"),
                shell_function("validate_preserved_unit_objects"),
                shell_function("prepare_recovery_unit_guard"),
                shell_function("validate_recovery_unit_guards"),
                shell_function("retain_recovery_unit_guards"),
                shell_function("remove_recovery_unit_guards"),
                shell_function("prepare_temporary_unit_mask"),
                shell_function("cleanup_temporary_masks"),
                shell_function("cleanup_runtime_mounts"),
                shell_function("cleanup_service_guards"),
                shell_function("cleanup_guard"),
                shell_function("exit_cleanup"),
                shell_function("signal_exit"),
                shell_function("disable_unmasked_units"),
                r"""
root="$1"
if command -v cygpath >/dev/null 2>&1; then root="$(cygpath -u -- "$root")"; fi
mkdir -p -- "$root"
real_stat="$(command -v stat)"
non_root_path=
fail_inode_path=
package_metadata_mode=ok
stat() {
    local format="${2:-}"
    local path="${*: -1}"
    if [[ "${1:-}" == -c && "$format" == %u:%g ]]; then
        if [[ -n "$non_root_path" && "$path" == "$non_root_path" ]]; then
            printf '%s\n' 1000:1000
        else
            printf '%s\n' 0:0
        fi
        return 0
    fi
    if [[ "${1:-}" == -c && "$format" == %i && -n "$fail_inode_path" && \
        "$path" == "$fail_inode_path" ]]; then
        return 1
    fi
    "$real_stat" "$@"
}
dpkg-query() {
    if [[ " $* " == *" -W "* ]]; then
        case "$package_metadata_mode" in
            ok|wrong-owner) printf 'installed\topen-iscsi\n' ;;
            wrong-package) printf 'installed\tother-package\n' ;;
            malformed) printf 'not-a-valid-status\n' ;;
            missing) return 1 ;;
        esac
        return 0
    fi
    if [[ " $* " == *" -S "* ]]; then
        case "$package_metadata_mode" in
            ok|wrong-package) printf 'open-iscsi: /usr/lib/systemd/system/open-iscsi.service\n' ;;
            wrong-owner) printf 'other-package: /usr/lib/systemd/system/open-iscsi.service\n' ;;
            malformed) printf 'ambiguous\nopen-iscsi: /usr/lib/systemd/system/open-iscsi.service\n' ;;
            missing) return 1 ;;
        esac
        return 0
    fi
    return 1
}
reset_tracking() {
    temporary_masks=()
    temporary_mask_inodes=()
    temporary_masks_cleanup_complete=false
    policy_cleanup_complete=false
    service_guard_cleanup_complete=false
    package_transaction_started=false
    denied_units_finalized=false
    service_readback_complete=false
    created_runtime_mounts=()
    runtime_mount_ids=()
    runtime_mount_records=()
    preserved_unit_masks=()
    preserved_unit_mask_inodes=()
    preserved_package_aliases=()
    preserved_package_alias_inodes=()
    preserved_package_alias_targets=()
    preserved_package_alias_canonical_units=()
    policy_guarded_canonical_units=()
    policy_guarded_absent_units=()
    recovery_guard_files=()
    recovery_guard_created_directories=()
    recovery_guard_file_inodes=()
    recovery_guard_contents=()
    recovery_guard_condition_paths=()
    recovery_guard_directory_inodes=()
    recovery_guard_paths_by_unit=()
    recovery_guard_path_owners=()
    recovery_guard_paths_retained=()
    recovery_guard_retained_states=()
    recovery_guards_cleanup_complete=false
}
refresh_md5() {
    local canonical="$target/usr/lib/systemd/system/open-iscsi.service"
    local digest
    digest="$(md5sum -- "$canonical" | awk '{print $1}')"
    printf '%s  %s\n' "$digest" usr/lib/systemd/system/open-iscsi.service \
        >"$target/var/lib/dpkg/info/open-iscsi.md5sums"
}
make_fixture() {
    target="$root/$1"
    mask_root="$target/etc/systemd/system"
    recovery_guard_authorization_root="$mask_root/.hoardarr-service-start-authorized"
    mkdir -p -- \
        "$mask_root" \
        "$target/usr/lib/systemd/system" \
        "$target/var/lib/dpkg/info" \
        "$target/usr/sbin" \
        "$target/opt/hoardarr-install"
    printf '%s\n' \
        '[Unit]' \
        'Description=Open-iSCSI' \
        '[Install]' \
        'WantedBy=sysinit.target' \
        'Alias=iscsi.service' \
        >"$target/usr/lib/systemd/system/open-iscsi.service"
    printf '%s\n' 'Package: open-iscsi' 'Status: install ok installed' \
        >"$target/var/lib/dpkg/status"
    printf '%s\n' /usr/lib/systemd/system/open-iscsi.service \
        >"$target/var/lib/dpkg/info/open-iscsi.list"
    refresh_md5
    ln -s -- /usr/lib/systemd/system/open-iscsi.service "$mask_root/iscsi.service"
    policy="$target/usr/sbin/policy-rc.d"
    policy_backup="$target/opt/hoardarr-install/policy-rc.d.original"
    state_root="$target/opt/hoardarr-install/state"
    mkdir -p -- "$state_root"
    policy_state=absent
    package_metadata_mode=ok
    non_root_path=
    fail_inode_path=
    reset_tracking
}
expect_alias_rejected_unchanged() {
    local unit="${1:-iscsi.service}"
    local alias="$mask_root/iscsi.service"
    local inode target_before status=0
    inode="$(stat -c %i -- "$alias")"
    target_before="$(readlink -- "$alias")"
    prepare_temporary_unit_mask "$alias" "$unit" >/dev/null 2>&1 || status=$?
    [[ "$status" -ne 0 ]]
    [[ -L "$alias" && "$(readlink -- "$alias")" == "$target_before" ]]
    [[ "$(stat -c %i -- "$alias")" == "$inode" ]]
}

# Exact retained tuple: no replacement, exact inode survives the entire
# pre-finalization lifecycle, and policy-rc.d denies the retained postinst start.
make_fixture exact
alias="$mask_root/iscsi.service"
canonical_override="$mask_root/open-iscsi.service"
wants="$mask_root/sysinit.target.wants/open-iscsi.service"
alias_inode="$(stat -c %i -- "$alias")"
install_service_start_guard
prepare_temporary_unit_mask "$alias" iscsi.service
prepare_temporary_unit_mask "$canonical_override" open-iscsi.service
[[ "${preserved_package_aliases[iscsi.service]}" == "$alias" ]]
[[ "${policy_guarded_canonical_units[open-iscsi.service]}" == "$canonical_override" ]]
[[ ! -e "$canonical_override" && ! -L "$canonical_override" ]]

# Retained open-iscsi.postinst semantics: unmask, enable, then invoke start.
rm -f -- "$canonical_override"
mkdir -p -- "$(dirname -- "$wants")"
ln -s -- /usr/lib/systemd/system/open-iscsi.service "$wants"
postinst_start_status=0
invoke_start() {
    local status=0
    "$policy" open-iscsi.service start || status=$?
    if (( status == 0 )); then
        mkdir -p -- "$target/run"
        : >"$target/run/open-iscsi.started"
    fi
    return "$status"
}
invoke_start || postinst_start_status=$?
[[ "$postinst_start_status" -eq 101 ]]
[[ ! -e "$target/run/open-iscsi.started" ]]
[[ -L "$alias" && "$(readlink -- "$alias")" == /usr/lib/systemd/system/open-iscsi.service ]]
[[ "$(stat -c %i -- "$alias")" == "$alias_inode" ]]
cleanup_temporary_masks
[[ "$(stat -c %i -- "$alias")" == "$alias_inode" ]]

# A later payload failure preserves the original status and alias identity.
failure_status=0
cleanup_guard 79 || failure_status=$?
[[ "$failure_status" -eq 79 ]]
[[ -L "$alias" && "$(stat -c %i -- "$alias")" == "$alias_inode" ]]

# A clean success finalization acts only on the canonical unit, removes the
# vendor alias and wants link, and requires a disabled canonical readback.
make_fixture final
alias="$mask_root/iscsi.service"
canonical_override="$mask_root/open-iscsi.service"
wants="$mask_root/sysinit.target.wants/open-iscsi.service"
mkdir -p -- "$(dirname -- "$wants")"
ln -s -- /usr/lib/systemd/system/open-iscsi.service "$wants"
install_service_start_guard
prepare_temporary_unit_mask "$alias" iscsi.service
prepare_temporary_unit_mask "$canonical_override" open-iscsi.service
cleanup_temporary_masks
disable_log="$target/disable.log"
systemctl() {
    [[ "$1" == "--root=$target" ]]
    if [[ "$2" == disable && "$3" == open-iscsi.service ]]; then
        printf '%s\n' disable-open-iscsi >>"$disable_log"
        rm -f -- "$alias" "$wants"
        return 0
    fi
    if [[ "$2" == is-enabled && "$3" == open-iscsi.service ]]; then
        printf '%s\n' disabled
        return 1
    fi
    printf 'unexpected systemctl argv: %s\n' "$*" >&2
    return 97
}
chroot() { printf '%s\n' inactive; return 3; }
denied_units=(iscsi.service open-iscsi.service)
disable_unmasked_units
[[ ! -e "$alias" && ! -L "$alias" ]]
[[ ! -e "$wants" && ! -L "$wants" ]]
[[ "$(cat "$disable_log")" == disable-open-iscsi ]]

# Canonical disable failure is not ignored and does not remove either link.
make_fixture disable-failure
alias="$mask_root/iscsi.service"
wants="$mask_root/sysinit.target.wants/open-iscsi.service"
mkdir -p -- "$(dirname -- "$wants")"
ln -s -- /usr/lib/systemd/system/open-iscsi.service "$wants"
prepare_temporary_unit_mask "$alias" iscsi.service
systemctl() {
    [[ "$2" == disable && "$3" == open-iscsi.service ]]
    return 1
}
chroot() { printf '%s\n' inactive; return 3; }
denied_units=(iscsi.service open-iscsi.service)
disable_failure_status=0
disable_unmasked_units >/dev/null 2>&1 || disable_failure_status=$?
[[ "$disable_failure_status" -ne 0 ]]
[[ -L "$alias" && -L "$wants" ]]

# A nominal disable that leaves either generated link is rejected.
make_fixture disable-incomplete
alias="$mask_root/iscsi.service"
wants="$mask_root/sysinit.target.wants/open-iscsi.service"
mkdir -p -- "$(dirname -- "$wants")"
ln -s -- /usr/lib/systemd/system/open-iscsi.service "$wants"
prepare_temporary_unit_mask "$alias" iscsi.service
systemctl() {
    if [[ "$2" == disable ]]; then return 0; fi
    printf '%s\n' disabled
    return 1
}
chroot() { printf '%s\n' inactive; return 3; }
denied_units=(iscsi.service open-iscsi.service)
disable_incomplete_status=0
disable_unmasked_units >/dev/null 2>&1 || disable_incomplete_status=$?
[[ "$disable_incomplete_status" -ne 0 ]]
[[ -L "$alias" && -L "$wants" ]]

# Exact tuple negative cases all reject without changing the alias object.
make_fixture wrong-unit
expect_alias_rejected_unchanged other.service

make_fixture relative-target
rm -f -- "$mask_root/iscsi.service"
ln -s -- ../../usr/lib/systemd/system/open-iscsi.service "$mask_root/iscsi.service"
expect_alias_rejected_unchanged

make_fixture alternate-target
rm -f -- "$mask_root/iscsi.service"
ln -s -- /usr/lib/systemd/system/alternate.service "$mask_root/iscsi.service"
expect_alias_rejected_unchanged

make_fixture missing-canonical
rm -f -- "$target/usr/lib/systemd/system/open-iscsi.service"
expect_alias_rejected_unchanged

make_fixture canonical-symlink
rm -f -- "$target/usr/lib/systemd/system/open-iscsi.service"
ln -s -- /dev/null "$target/usr/lib/systemd/system/open-iscsi.service"
expect_alias_rejected_unchanged

make_fixture canonical-directory
rm -f -- "$target/usr/lib/systemd/system/open-iscsi.service"
mkdir -- "$target/usr/lib/systemd/system/open-iscsi.service"
expect_alias_rejected_unchanged

make_fixture non-root-alias
non_root_path="$mask_root/iscsi.service"
expect_alias_rejected_unchanged

make_fixture non-root-canonical
non_root_path="$target/usr/lib/systemd/system/open-iscsi.service"
expect_alias_rejected_unchanged

make_fixture wrong-owner
package_metadata_mode=wrong-owner
expect_alias_rejected_unchanged

make_fixture wrong-package
package_metadata_mode=wrong-package
expect_alias_rejected_unchanged

make_fixture missing-package
package_metadata_mode=missing
expect_alias_rejected_unchanged

make_fixture malformed-package
package_metadata_mode=malformed
expect_alias_rejected_unchanged

make_fixture missing-alias-metadata
sed -i '/^Alias=/d' "$target/usr/lib/systemd/system/open-iscsi.service"
refresh_md5
expect_alias_rejected_unchanged

make_fixture wrong-alias-metadata
sed -i 's/^Alias=.*/Alias=other.service/' "$target/usr/lib/systemd/system/open-iscsi.service"
refresh_md5
expect_alias_rejected_unchanged

make_fixture extra-alias-metadata
sed -i 's/^Alias=.*/Alias=iscsi.service other.service/' \
    "$target/usr/lib/systemd/system/open-iscsi.service"
refresh_md5
expect_alias_rejected_unchanged

make_fixture status-symlink
mv -- "$target/var/lib/dpkg/status" "$target/status-retained"
ln -s -- "$target/status-retained" "$target/var/lib/dpkg/status"
expect_alias_rejected_unchanged

make_fixture package-list-symlink
mv -- "$target/var/lib/dpkg/info/open-iscsi.list" "$target/list-retained"
ln -s -- "$target/list-retained" "$target/var/lib/dpkg/info/open-iscsi.list"
expect_alias_rejected_unchanged

make_fixture missing-md5
rm -f -- "$target/var/lib/dpkg/info/open-iscsi.md5sums"
expect_alias_rejected_unchanged

make_fixture malformed-md5
printf '%s\n' malformed >"$target/var/lib/dpkg/info/open-iscsi.md5sums"
expect_alias_rejected_unchanged

make_fixture parent-symlink
external="$root/parent-symlink-systemd"
mv -- "$target/etc/systemd" "$external"
ln -s -- "$external" "$target/etc/systemd"
expect_alias_rejected_unchanged

# Recorded alias drift is detected before finalization and never overwritten.
make_fixture alias-drift
alias="$mask_root/iscsi.service"
prepare_temporary_unit_mask "$alias" iscsi.service
original_inode="$(stat -c %i -- "$alias")"
mv -- "$alias" "$target/original-alias-retained"
ln -s -- /usr/lib/systemd/system/drifted.service "$alias"
drift_inode="$(stat -c %i -- "$alias")"
drift_status=0
cleanup_temporary_masks >/dev/null 2>&1 || drift_status=$?
[[ "$drift_status" -ne 0 ]]
[[ "$(readlink -- "$alias")" == /usr/lib/systemd/system/drifted.service ]]
[[ "$(stat -c %i -- "$alias")" == "$drift_inode" ]]
[[ "$(stat -c %i -- "$target/original-alias-retained")" == "$original_inode" ]]

# Drift between alias classification and canonical policy-guard registration
# is rejected before the canonical path is accepted.
make_fixture canonical-registration-drift
alias="$mask_root/iscsi.service"
canonical_override="$mask_root/open-iscsi.service"
prepare_temporary_unit_mask "$alias" iscsi.service
mv -- "$alias" "$target/original-alias-retained"
ln -s -- /usr/lib/systemd/system/drifted.service "$alias"
canonical_registration_status=0
prepare_temporary_unit_mask "$canonical_override" open-iscsi.service \
    >/dev/null 2>&1 || canonical_registration_status=$?
[[ "$canonical_registration_status" -ne 0 ]]
[[ ! -e "$canonical_override" && ! -L "$canonical_override" ]]
[[ "$(readlink -- "$alias")" == /usr/lib/systemd/system/drifted.service ]]

# A pre-existing exact mask whose inode cannot be recorded is rejected intact.
make_fixture safe-stat-failure
safe_stat_failure="$mask_root/safe-stat-failure.service"
ln -s -- /dev/null "$safe_stat_failure"
safe_stat_failure_inode="$(stat -c %i -- "$safe_stat_failure")"
fail_inode_path="$safe_stat_failure"
safe_stat_failure_status=0
prepare_temporary_unit_mask "$safe_stat_failure" safe-stat-failure.service \
    >/dev/null 2>&1 || safe_stat_failure_status=$?
fail_inode_path=
[[ "$safe_stat_failure_status" -ne 0 ]]
[[ -L "$safe_stat_failure" && "$(readlink -- "$safe_stat_failure")" == /dev/null ]]
[[ "$(stat -c %i -- "$safe_stat_failure")" == "$safe_stat_failure_inode" ]]
[[ "${#preserved_unit_masks[@]}" -eq 0 ]]

# Cleanup failure is aggregated; an existing payload failure remains exact,
# while an otherwise successful invocation becomes failure.
make_fixture incomplete-transaction
install_service_start_guard
prepare_temporary_unit_mask "$mask_root/iscsi.service" iscsi.service
prepare_recovery_unit_guard iscsi.service
mkdir -p -- "$mask_root/sysinit.target.wants"
ln -s -- /usr/lib/systemd/system/open-iscsi.service \
    "$mask_root/sysinit.target.wants/open-iscsi.service"
package_transaction_started=true
incomplete_status=0
cleanup_guard 73 >/dev/null 2>&1 || incomplete_status=$?
[[ "$incomplete_status" -eq 73 ]]
guard_status=0
"$policy" pmcd.service start || guard_status=$?
[[ -x "$policy" && "$guard_status" -eq 101 ]]
grep -Fq 'finalization=false readback=false' "$state_root/service-guard-recovery.txt"
recovery_path="${recovery_guard_paths_by_unit[iscsi.service]}"
[[ -f "$recovery_path" && ! -L "$recovery_path" ]]
grep -Fxq 'ConditionPathExists=/dev/null/hoardarr-offline-service-guard/open-iscsi.service' "$recovery_path"
[[ -L "$mask_root/sysinit.target.wants/open-iscsi.service" ]]

set +e
cleanup_guard 0 >/dev/null 2>&1
set_plus_e_status=$?
set -e
[[ "$set_plus_e_status" -ne 0 && -x "$policy" ]]

denied_units_finalized=true
cleanup_guard 0 >/dev/null 2>&1 || readback_incomplete_status=$?
[[ "${readback_incomplete_status:-0}" -ne 0 && -x "$policy" ]]
denied_units_finalized=false
systemctl() {
    if [[ "$2" == disable && "$3" == open-iscsi.service ]]; then
        rm -f -- "$mask_root/iscsi.service" \
            "$mask_root/sysinit.target.wants/open-iscsi.service"
        return 0
    fi
    if [[ "$2" == is-enabled && "$3" == open-iscsi.service ]]; then
        printf '%s\n' disabled
        return 1
    fi
    return 97
}
chroot() { printf '%s\n' inactive; return 3; }
denied_units=(iscsi.service open-iscsi.service)
disable_unmasked_units
cleanup_guard 0
[[ ! -e "$policy" && ! -L "$policy" ]]

for signal_case in 'HUP 129' 'INT 130' 'TERM 143'; do
    read -r signal_name signal_status <<<"$signal_case"
    make_fixture "signal-$signal_name"
    install_service_start_guard
    prepare_temporary_unit_mask "$mask_root/iscsi.service" iscsi.service
    prepare_recovery_unit_guard iscsi.service
    package_transaction_started=true
    observed_status=0
    (
        trap 'exit_cleanup $?' EXIT
        trap 'signal_exit 129' HUP
        trap 'signal_exit 130' INT
        trap 'signal_exit 143' TERM
        kill -s "$signal_name" "$BASHPID"
    ) || observed_status=$?
    [[ "$observed_status" -eq "$signal_status" && -x "$policy" ]]
    grep -Fq 'finalization=false readback=false' "$state_root/service-guard-recovery.txt"
done

make_fixture cleanup-original-status
reset_tracking
rm -f -- "$policy"
mkdir -- "$policy"
original_status=0
cleanup_guard 73 >/dev/null 2>&1 || original_status=$?
[[ "$original_status" -eq 73 ]]

make_fixture cleanup-success-status
reset_tracking
rm -f -- "$policy"
mkdir -- "$policy"
success_status=0
cleanup_guard 0 >/dev/null 2>&1 || success_status=$?
[[ "$success_status" -ne 0 ]]
""",
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            script_path = pathlib.Path(temporary) / "alias-regression.sh"
            script_path.write_text(script, encoding="utf-8", newline="\n")
            result = subprocess.run(
                [bash, str(script_path), temporary],
                capture_output=True,
                check=False,
                env={**os.environ, "MSYS": "winsymlinks:sys"},
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("command not found", result.stderr)

    def test_pcp_trace_contract_rejects_untrusted_or_incomplete_evidence(
        self,
    ) -> None:
        def phase_record(kind: str, index: int) -> str:
            phase, label = PCP_TRACE_PHASES[index]
            return f"HPCP|1|{kind}|{phase}|status=-|line=-|function=-|label={label}"

        valid_lines = [
            record
            for index in range(len(PCP_TRACE_PHASES))
            for record in (phase_record("BEGIN", index), phase_record("PASS", index))
        ]
        final_phase, final_label = PCP_TRACE_PHASES[-1]
        valid_lines.append(
            f"HPCP|1|EXIT|{final_phase}|status=0|line=321|function=main|"
            f"label={final_label}"
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            namespace = root / "namespace"
            namespace.mkdir()

            def rejected(name: str, lines: list[str], *, outside: bool = False) -> None:
                if outside:
                    with tempfile.TemporaryDirectory() as other:
                        trace = pathlib.Path(other) / f"{name}.trace"
                        trace.write_text("\n".join(lines) + "\n", encoding="ascii")
                        with self.assertRaises(AssertionError):
                            _validate_pcp_trace(trace, root, namespace)
                    return
                trace = root / f"{name}.trace"
                trace.write_text("\n".join(lines) + "\n", encoding="ascii")
                with self.assertRaises(AssertionError):
                    _validate_pcp_trace(trace, root, namespace)

            success_trace = root / "success.trace"
            success_trace.write_text("\n".join(valid_lines) + "\n", encoding="ascii")
            _, status = _validate_pcp_trace(success_trace, root, namespace)
            self.assertEqual(status, 0)

            missing = valid_lines[:4] + valid_lines[6:]
            duplicate = valid_lines[:1] + [valid_lines[0]] + valid_lines[1:]
            out_of_order = valid_lines.copy()
            out_of_order[2:6] = valid_lines[4:6] + valid_lines[2:4]
            unknown = valid_lines.copy()
            unknown[0] = unknown[0].replace("01-fixture-creation", "01-unknown-phase")
            multiple_terminal = valid_lines + [valid_lines[-1]]
            malformed_status = valid_lines.copy()
            malformed_status[-1] = malformed_status[-1].replace(
                "status=0", "status=999"
            )
            malformed_line = valid_lines.copy()
            malformed_line[-1] = malformed_line[-1].replace("line=321", "line=0")
            unbounded = valid_lines.copy()
            unbounded[0] += "x" * PCP_TRACE_MAX_LINE_BYTES
            environment_like = valid_lines.copy()
            environment_like[0] += "|PASSWORD=do-not-record"

            cases = {
                "missing": missing,
                "duplicate": duplicate,
                "out-of-order": out_of_order,
                "unknown": unknown,
                "multiple-terminal": multiple_terminal,
                "malformed-status": malformed_status,
                "malformed-line": malformed_line,
                "unbounded": unbounded,
                "environment-like": environment_like,
            }
            for name, lines in cases.items():
                with self.subTest(name=name):
                    rejected(name, lines)
            rejected("outside-root", valid_lines, outside=True)

    def test_manager_root_receipt_parser_is_bounded_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            namespace = pathlib.Path(temporary).resolve() / "namespace"
            manager = namespace / "run-systemd"
            manager.mkdir(parents=True)
            before = namespace / "manager-root-before.tsv"
            after = namespace / "manager-root-after.tsv"
            before.write_text(
                "HMROOT|1|before|status=-\n", encoding="utf-8", newline="\n"
            )
            before_text, before_status, before_entries = _validate_manager_root_receipt(
                before, namespace, manager, "before"
            )
            self.assertEqual(before_text, "HMROOT|1|before|status=-\n")
            self.assertIsNone(before_status)
            self.assertEqual(before_entries, ())
            after.write_text(
                "HMROOT|1|after|status=1\n"
                "ENTRY\tprivate\tdirectory\t755\t0\t0\n"
                "ENTRY\tprivate/socket\tsocket\t660\t100\t101\n",
                encoding="utf-8",
                newline="\n",
            )
            _, after_status, after_entries = _validate_manager_root_receipt(
                after, namespace, manager, "after"
            )
            self.assertEqual(after_status, 1)
            self.assertEqual(len(after_entries), 2)

            before.unlink()
            after.unlink()

            def rejected(
                name: str,
                text: str | bytes | None,
                *,
                stage: str = "after",
                path: pathlib.Path | None = None,
            ) -> None:
                receipt = path or namespace / f"manager-root-{stage}.tsv"
                if receipt.exists() or receipt.is_symlink():
                    receipt.unlink()
                if isinstance(text, bytes):
                    receipt.write_bytes(text)
                elif text is not None:
                    receipt.write_text(text, encoding="utf-8", newline="\n")
                with self.assertRaises(AssertionError, msg=name):
                    _validate_manager_root_receipt(receipt, namespace, manager, stage)
                if receipt.exists() or receipt.is_symlink():
                    receipt.unlink()

            header = "HMROOT|1|after|status=1\n"
            valid = "ENTRY\tentry\tregular\t600\t0\t0\n"
            cases: dict[str, str | bytes | None] = {
                "missing-file": None,
                "missing-header": valid,
                "wrong-version": "HMROOT|2|after|status=1\n",
                "wrong-stage": "HMROOT|1|before|status=-\n",
                "wrong-status": "HMROOT|1|after|status=-\n",
                "overlong-path": header + f"ENTRY\t{'a' * 193}\tregular\t600\t0\t0\n",
                "absolute-path": header + "ENTRY\t/absolute\tregular\t600\t0\t0\n",
                "traversal-path": header
                + "ENTRY\tsafe/../escape\tregular\t600\t0\t0\n",
                "control-path": (header + "ENTRY\tbad\x01path\tregular\t600\t0\t0\n"),
                "excess-depth": header + "ENTRY\ta/b/c/d/e/f\tregular\t600\t0\t0\n",
                "unknown-type": header + "ENTRY\tentry\tunknown\t600\t0\t0\n",
                "invalid-mode": header + "ENTRY\tentry\tregular\t888\t0\t0\n",
                "invalid-uid": header + "ENTRY\tentry\tregular\t600\troot\t0\n",
                "invalid-gid": header + "ENTRY\tentry\tregular\t600\t0\t-1\n",
                "duplicate": header + valid + valid,
                "out-of-order": header
                + "ENTRY\tz\tregular\t600\t0\t0\n"
                + "ENTRY\ta\tregular\t600\t0\t0\n",
                "excess-entries": header
                + "".join(
                    f"ENTRY\tp{index:03d}\tregular\t600\t0\t0\n" for index in range(129)
                ),
                "oversized": b"x" * (PCP_MANAGER_ROOT_RECEIPT_MAX_BYTES + 1),
                "appended-text": header + valid + "TRAILING\n",
            }
            for name, text in cases.items():
                with self.subTest(name=name):
                    rejected(name, text)
            rejected(
                "outside-exact-path",
                header,
                path=namespace / "unexpected-receipt.tsv",
            )
            rejected(
                "before-has-status",
                "HMROOT|1|before|status=1\n",
                stage="before",
            )

    def test_pcp_generated_nonactivation_proof_is_structural_and_managerless(
        self,
    ) -> None:
        _assert_pcp_offline_nonactivation_contract(PCP_OFFLINE_NONACTIVATION_PROOF)
        with self.assertRaises(AssertionError):
            _assert_pcp_offline_nonactivation_contract(
                PCP_OFFLINE_NONACTIVATION_PROOF + "\nsystemctl is-active pmcd.service\n"
            )
        with self.assertRaises(AssertionError):
            _assert_pcp_offline_nonactivation_contract(
                PCP_OFFLINE_NONACTIVATION_PROOF.replace(
                    '[[ "$post_configure_start_status" -eq 101 ]]', "", 1
                )
            )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and shutil.which("bash"),
        "requires Linux Bash",
    )
    def test_systemd_receipt_production_writers_emit_real_tab_bytes(self) -> None:
        source_writer = _extract_systemd_receipt_writer(
            PCP_SYSTEMD_SOURCE_RECEIPT, "systemd_source_partial"
        )
        causal_writer = _extract_systemd_receipt_writer(
            PCP_SYSTEMD_CAUSAL_PROOF, "systemd_causal_partial"
        )
        self.assertNotIn("printf '%s\\n' \\", source_writer)
        self.assertNotIn("printf '%s\\n' \\", causal_writer)

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            script = root / "emit-systemd-receipts.sh"
            script.write_text(
                "set -Eeuo pipefail\n"
                'work="$1"\n'
                'systemd_source_receipt="$work/systemd-source.tsv"\n'
                'systemd_source_partial="${systemd_source_receipt}.partial"\n'
                'systemd_package_version="255.4-1ubuntu8.17"\n'
                'systemd_package_arch="amd64"\n'
                'systemd_version_first="systemd 255 (255.4-1ubuntu8.17)"\n'
                f'systemd_version_sha256="{"b" * 64}"\n'
                f'systemd_executable_sha256="{"a" * 64}"\n'
                'systemd_executable_mode="755"\n'
                'systemd_executable_size="123456"\n'
                'systemd_executable_links="1"\n'
                + source_writer
                + "\n"
                + '/usr/bin/mv -- "$systemd_source_partial" '
                '"$systemd_source_receipt"\n'
                + 'systemd_causal_receipt="$work/systemd-causal.tsv"\n'
                'systemd_causal_partial="${systemd_causal_receipt}.partial"\n'
                'systemd_marker_mode="444"\n'
                'systemd_marker_uid="0"\n'
                'systemd_marker_gid="0"\n'
                'systemd_marker_size="0"\n'
                'systemd_marker_links="1"\n'
                + causal_writer
                + "\n"
                + '/usr/bin/mv -- "$systemd_causal_partial" '
                '"$systemd_causal_receipt"\n',
                encoding="ascii",
                newline="\n",
            )
            result = subprocess.run(
                [shutil.which("bash") or "bash", str(script), str(root)],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            source = root / "systemd-source.tsv"
            causal = root / "systemd-causal.tsv"
            for receipt, expected_lines in ((source, 9), (causal, 4)):
                raw = receipt.read_bytes()
                with self.subTest(receipt=receipt.name):
                    self.assertTrue(raw.isascii())
                    self.assertTrue(raw.endswith(b"\n"))
                    self.assertNotIn(b"\r", raw)
                    self.assertEqual(len(raw.splitlines()), expected_lines)
                    self.assertIn(b"\t", raw)
                    self.assertNotIn(b"\\t", b"\n".join(raw.splitlines()[1:]))
                    self.assertTrue(all(b"\t" in row for row in raw.splitlines()[1:]))

            source_text, version, executable_hash = _validate_systemd_source_receipt(
                source, root
            )
            self.assertEqual(version, "255.4-1ubuntu8.17")
            self.assertEqual(executable_hash, "a" * 64)
            self.assertEqual(source_text.encode("ascii"), source.read_bytes())
            causal_text = _validate_systemd_causal_receipt(causal, root)
            self.assertEqual(causal_text.encode("ascii"), causal.read_bytes())

            source.write_bytes(source.read_bytes().replace(b"\t", b"\\t"))
            causal.write_bytes(causal.read_bytes().replace(b"\t", b"\\t"))
            with self.assertRaises(AssertionError):
                _validate_systemd_source_receipt(source, root)
            with self.assertRaises(AssertionError):
                _validate_systemd_causal_receipt(causal, root)

    def test_systemd_source_and_causal_receipts_are_bounded_and_fail_closed(
        self,
    ) -> None:
        package_version = "255.4-1ubuntu8.17"
        executable_hash = "a" * 64
        version_hash = "b" * 64
        source_receipt = (
            "HSOURCE|1\n"
            f"PACKAGE\tsystemd\t{package_version}\tamd64\n"
            f"VERSION\tsystemd 255 ({package_version})\t{version_hash}\n"
            "EXECUTABLE\t/usr/bin/systemd-analyze\t"
            f"{executable_hash}\t755\t0\t0\t123456\t1\tsystemd\n"
            f"UPSTREAM\t{PCP_SYSTEMD_UPSTREAM_REPOSITORY}\t"
            f"{PCP_SYSTEMD_UPSTREAM_TAG}\t{PCP_SYSTEMD_UPSTREAM_REVISION}\n"
            f"SOURCE\t{PCP_SYSTEMD_ANALYZE_SOURCE_PATH}\t"
            f"{PCP_SYSTEMD_ANALYZE_SOURCE_FUNCTION}\t"
            f"{PCP_SYSTEMD_ANALYZE_SOURCE_SHA256}\n"
            f"SOURCE\t{PCP_SYSTEMD_MANAGER_SOURCE_PATH}\t"
            f"{PCP_SYSTEMD_MANAGER_SOURCE_FUNCTION}\t"
            f"{PCP_SYSTEMD_MANAGER_SOURCE_SHA256}\n"
            "CHAIN\tverb_condition>verify_conditions>manager_startup>"
            "manager_ready>touch_file\n"
            f"MARKER\t{PCP_SYSTEMD_MARKER_PATH}\tregular\t0444\t"
            "zero-length\tmanager-ready\n"
        )
        causal_receipt = (
            "HCAUSE|1\n"
            "CONTROL\tnegative\tcommand=none\tstatus=-\tbefore=0\tafter=0\t"
            "manager_endpoints_before=0\tmanager_endpoints_after=0\t"
            "cleanup=removed\n"
            "CONTROL\tpositive\tcommand=systemd-analyze-condition\tstatus=1\t"
            "before=0\tafter=1\tmanager_endpoints_before=0\t"
            "manager_endpoints_after=0\tcleanup=removed\n"
            "MARKER\tsystemd-units-load\tregular\t444\t0\t0\t0\t1\t"
            "same-filesystem\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            source = root / "systemd-source.tsv"
            causal = root / "systemd-causal.tsv"
            source.write_text(source_receipt, encoding="ascii", newline="\n")
            causal.write_text(causal_receipt, encoding="ascii", newline="\n")
            source_text, actual_version, actual_hash = _validate_systemd_source_receipt(
                source, root
            )
            self.assertEqual(source_text, source_receipt)
            self.assertEqual(actual_version, package_version)
            self.assertEqual(actual_hash, executable_hash)
            self.assertEqual(
                _validate_systemd_causal_receipt(causal, root), causal_receipt
            )

            def source_rejected(name: str, mutated: str | bytes | None) -> None:
                if source.exists() or source.is_symlink():
                    source.unlink()
                if isinstance(mutated, bytes):
                    source.write_bytes(mutated)
                elif mutated is not None:
                    source.write_text(mutated, encoding="ascii", newline="\n")
                with self.assertRaises(AssertionError, msg=name):
                    _validate_systemd_source_receipt(source, root)

            source_cases: dict[str, str | bytes | None] = {
                "missing": None,
                "wrong-package": source_receipt.replace(
                    "PACKAGE\tsystemd", "PACKAGE\tlibsystemd0", 1
                ),
                "wrong-package-version": source_receipt.replace(
                    package_version, "256.1-1", 1
                ),
                "version-divergence": source_receipt.replace(
                    f"systemd 255 ({package_version})", "systemd 255 (255.4-other)", 1
                ),
                "wrong-executable": source_receipt.replace(
                    "/usr/bin/systemd-analyze", "/tmp/systemd-analyze", 1
                ),
                "wrong-executable-hash": source_receipt.replace(
                    executable_hash, "z" * 64, 1
                ),
                "wrong-revision": source_receipt.replace(
                    PCP_SYSTEMD_UPSTREAM_REVISION, "0" * 40, 1
                ),
                "wrong-source": source_receipt.replace(
                    PCP_SYSTEMD_MANAGER_SOURCE_PATH, "src/core/not-manager.c", 1
                ),
                "wrong-source-function": source_receipt.replace(
                    PCP_SYSTEMD_MANAGER_SOURCE_FUNCTION, "manager_ready:1-2", 1
                ),
                "wrong-source-hash": source_receipt.replace(
                    PCP_SYSTEMD_MANAGER_SOURCE_SHA256, "0" * 64, 1
                ),
                "unknown-field": source_receipt + "ENV\tSECRET=value\n",
                "control": source_receipt.replace("systemd\t", "systemd\x01", 1),
                "oversized": b"x" * (PCP_SYSTEMD_SOURCE_RECEIPT_MAX_BYTES + 1),
            }
            for name, mutation in source_cases.items():
                with self.subTest(source=name):
                    source_rejected(name, mutation)

            source.write_text(source_receipt, encoding="ascii", newline="\n")

            def causal_rejected(name: str, mutated: str | bytes | None) -> None:
                if causal.exists() or causal.is_symlink():
                    causal.unlink()
                if isinstance(mutated, bytes):
                    causal.write_bytes(mutated)
                elif mutated is not None:
                    causal.write_text(mutated, encoding="ascii", newline="\n")
                with self.assertRaises(AssertionError, msg=name):
                    _validate_systemd_causal_receipt(causal, root)

            causal_cases: dict[str, str | bytes | None] = {
                "missing": None,
                "preexisting-marker": causal_receipt.replace("before=0", "before=1", 1),
                "symlink": causal_receipt.replace("\tregular\t", "\tsymlink\t", 1),
                "directory": causal_receipt.replace("\tregular\t", "\tdirectory\t", 1),
                "nonzero-size": causal_receipt.replace("\t0\t1\t", "\t1\t1\t", 1),
                "wrong-mode": causal_receipt.replace("\t444\t", "\t644\t", 1),
                "wrong-owner": causal_receipt.replace(
                    "\t444\t0\t0\t", "\t444\t1\t0\t", 1
                ),
                "wrong-link-count": causal_receipt.replace("\t0\t1\t", "\t0\t2\t", 1),
                "wrong-filesystem": causal_receipt.replace(
                    "same-filesystem", "different-filesystem", 1
                ),
                "extra-entry": causal_receipt + "ENTRY\tprivate\n",
                "manager-before": causal_receipt.replace(
                    "manager_endpoints_before=0", "manager_endpoints_before=1", 1
                ),
                "manager-after": causal_receipt.replace(
                    "manager_endpoints_after=0", "manager_endpoints_after=1", 1
                ),
                "wrong-command": causal_receipt.replace(
                    "command=systemd-analyze-condition", "command=systemctl", 1
                ),
                "wrong-status": causal_receipt.replace("status=1", "status=0", 1),
                "negative-nonempty": causal_receipt.replace(
                    "command=none\tstatus=-\tbefore=0\tafter=0",
                    "command=none\tstatus=-\tbefore=0\tafter=1",
                    1,
                ),
                "cleanup-drift": causal_receipt.replace(
                    "cleanup=removed", "cleanup=present", 1
                ),
                "unknown-field": causal_receipt.replace(
                    "HCAUSE|1", "HCAUSE|1\nENV\tTOKEN=value", 1
                ),
                "control": causal_receipt.replace("positive", "pos\x01itive", 1),
                "oversized": b"x" * (PCP_SYSTEMD_CAUSAL_RECEIPT_MAX_BYTES + 1),
            }
            for name, mutation in causal_cases.items():
                with self.subTest(causal=name):
                    causal_rejected(name, mutation)
            outside = root.parent / "systemd-causal.tsv"
            outside.write_text(causal_receipt, encoding="ascii", newline="\n")
            try:
                with self.assertRaises(AssertionError):
                    _validate_systemd_causal_receipt(outside, root)
            finally:
                outside.unlink()

    def test_systemd_causal_control_preserves_real_phase_ten_sequence(self) -> None:
        phase = _pcp_phase_ten_with_causal_proof()
        real_sequence = (
            'manager_root_snapshot before - "$work/manager-root-before.tsv"\n'
            'systemd-analyze condition "ConditionPathExists=$expected_pmcd_condition" \\\n'
            "    >/dev/null 2>&1 && exit 100\n"
            "condition_status=$?\n"
            'manager_root_snapshot after "$condition_status" '
            '"$work/manager-root-after.tsv"\n'
            "validate_and_remove_local_systemd_marker "
            '"$work/manager-root-after.tsv"\n'
            '[[ "$local_systemd_marker_cleanup_count" -eq 1 ]]\n'
            '[[ -z "$(find "$work/run-systemd" -mindepth 1 -print -quit)" ]]'
        )
        self.assertEqual(phase.count(real_sequence), 1)
        self.assertEqual(
            phase.count(
                "/usr/bin/systemd-analyze condition \\\n"
                f'    "{PCP_SYSTEMD_FALSE_CONDITION}" >/dev/null 2>&1'
            ),
            1,
        )
        self.assertEqual(phase.count("systemd_causal_cleanup_root"), 4)
        self.assertNotIn("apt-get", PCP_SYSTEMD_CAUSAL_PROOF)
        self.assertNotIn("systemctl", PCP_SYSTEMD_CAUSAL_PROOF)
        self.assertNotIn("curl", PCP_SYSTEMD_CAUSAL_PROOF)
        self.assertNotIn("wget", PCP_SYSTEMD_CAUSAL_PROOF)
        self.assertNotIn("strace", PCP_SYSTEMD_CAUSAL_PROOF)
        self.assertEqual(
            PCP_LOCAL_SYSTEMD_MARKER_ORACLE.count('/usr/bin/rm -- "$marker"'), 1
        )
        self.assertNotIn("rm -f", PCP_LOCAL_SYSTEMD_MARKER_ORACLE)
        self.assertNotIn("rm -r", PCP_LOCAL_SYSTEMD_MARKER_ORACLE)
        self.assertIsNone(
            re.search(
                r"(?m)^\s*/usr/bin/rm[^\n]*[\*\?\[]",
                PCP_LOCAL_SYSTEMD_MARKER_ORACLE,
            )
        )
        self.assertNotIn("systemctl", PCP_LOCAL_SYSTEMD_MARKER_ORACLE)
        self.assertNotIn("systemd-analyze", PCP_LOCAL_SYSTEMD_MARKER_ORACLE)
        self.assertIn(
            'rm -f -- "$expected_entry"',
            PCP_SYSTEMD_CAUSAL_PROOF,
        )
        _assert_pcp_offline_nonactivation_contract(phase)
        with self.assertRaises(AssertionError):
            _assert_pcp_offline_nonactivation_contract(
                phase.replace(
                    'manager_root_snapshot after "$condition_status" '
                    '"$work/manager-root-after.tsv"\n'
                    "validate_and_remove_local_systemd_marker",
                    "validate_and_remove_local_systemd_marker "
                    '"$work/manager-root-after.tsv"\n'
                    'manager_root_snapshot after "$condition_status"',
                    1,
                )
            )
        with self.assertRaises(AssertionError):
            _assert_pcp_offline_nonactivation_contract(
                phase.replace(
                    "validate_and_remove_local_systemd_marker "
                    '"$work/manager-root-after.tsv"\n'
                    '[[ "$local_systemd_marker_cleanup_count" -eq 1 ]]\n'
                    '[[ -z "$(find "$work/run-systemd" -mindepth 1 -print -quit)" ]]',
                    '[[ -z "$(find "$work/run-systemd" -mindepth 1 -print -quit)" ]]\n'
                    "validate_and_remove_local_systemd_marker "
                    '"$work/manager-root-after.tsv"\n'
                    '[[ "$local_systemd_marker_cleanup_count" -eq 1 ]]',
                    1,
                )
            )

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux mounts")
    def test_local_systemd_marker_oracle_is_exact_and_fail_closed(self) -> None:
        required = ("bash", "mount", "sudo", "umount", "unshare")
        missing = [command for command in required if shutil.which(command) is None]
        self.assertEqual(missing, [], f"missing marker-oracle tools: {missing}")
        sudo = subprocess.run(
            ["sudo", "-n", "true"], text=True, capture_output=True, check=False
        )
        self.assertEqual(sudo.returncode, 0, sudo.stderr)

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            script = root / "local-systemd-marker-oracle.sh"
            script.write_text(
                "set -Eeuo pipefail\n"
                'fixture_root="$1"\n'
                "mounted=false\n"
                "nested_marker_mount=false\n"
                "private_marker_mount=false\n"
                "sync_wrapper_mount=false\n"
                'wrong_device_source="/dev/shm/hoardarr-f16-marker-$$"\n'
                "cleanup_case() {\n"
                '    if [[ "$sync_wrapper_mount" == true ]]; then\n'
                "        /usr/bin/umount -- /usr/bin/sync || return 201\n"
                "        sync_wrapper_mount=false\n"
                "    fi\n"
                '    if [[ "$nested_marker_mount" == true ]]; then\n'
                "        /usr/bin/umount -- /run/systemd/systemd-units-load || return 202\n"
                "        nested_marker_mount=false\n"
                "    fi\n"
                '    if [[ "$private_marker_mount" == true ]]; then\n'
                '        /usr/bin/umount -- "$work/run-systemd/systemd-units-load" || return 202\n'
                "        private_marker_mount=false\n"
                "    fi\n"
                '    if [[ "$mounted" == true ]]; then\n'
                "        /usr/bin/umount -- /run/systemd || return 203\n"
                "        mounted=false\n"
                "    fi\n"
                "}\n"
                "cleanup_all() {\n"
                '    local status="$?"\n'
                "    trap - EXIT\n"
                "    cleanup_case || status=$?\n"
                '    /usr/bin/rm -f -- "$wrong_device_source" || status=204\n'
                '    exit "$status"\n'
                "}\n"
                "trap cleanup_all EXIT\n"
                "systemd_mount_id() {\n"
                "    /usr/bin/awk '$5 == \"/run/systemd\" { id=$1 } END { print id }' "
                "/proc/self/mountinfo\n"
                "}\n"
                "start_case() {\n"
                "    cleanup_case\n"
                '    work="$fixture_root/$1"\n'
                '    /usr/bin/mkdir -p -- "$work/run-systemd"\n'
                '    /usr/bin/mount --bind "$work/run-systemd" /run/systemd\n'
                "    mounted=true\n"
                "    /usr/bin/mount --make-private /run/systemd\n"
                '    systemd_underlay_mount_id="$(systemd_mount_id)"\n'
                '    [[ "$systemd_underlay_mount_id" =~ ^[1-9][0-9]*$ ]]\n'
                "    condition_status=1\n"
                "    local_systemd_marker_cleanup_count=0\n"
                "}\n"
                "write_receipt() {\n"
                "    printf 'HMROOT|1|after|status=1\\nENTRY\\tsystemd-units-load\\tregular\\t444\\t0\\t0\\n' >\"$work/manager-root-after.tsv\"\n"
                "}\n"
                "write_marker() {\n"
                '    : >"$work/run-systemd/systemd-units-load"\n'
                '    /usr/bin/chown 0:0 -- "$work/run-systemd/systemd-units-load"\n'
                '    /usr/bin/chmod 0444 -- "$work/run-systemd/systemd-units-load"\n'
                "}\n"
                "expect_rejected() {\n"
                '    local label="$1" expected_status="$2" '
                'before="$local_systemd_marker_cleanup_count" actual_status=0\n'
                "    if validate_and_remove_local_systemd_marker "
                '"$work/manager-root-after.tsv"; then\n'
                "        printf 'unexpected oracle acceptance: %s\\n' \"$label\" >&2\n"
                "        exit 205\n"
                "    else\n"
                "        actual_status=$?\n"
                "    fi\n"
                '    [[ "$actual_status" -eq "$expected_status" ]] || {\n'
                "        printf 'unexpected oracle rejection: %s expected=%s actual=%s\\n' "
                '"$label" "$expected_status" "$actual_status" >&2\n'
                "        exit 206\n"
                "    }\n"
                '    [[ "$local_systemd_marker_cleanup_count" -eq "$before" ]] || '
                '[[ "$label" == residual && "$local_systemd_marker_cleanup_count" -eq 1 ]]\n'
                "}\n"
                + PCP_LOCAL_SYSTEMD_MARKER_ORACLE
                + "\n"
                + r"""
negative_count=0
start_case valid
write_receipt
write_marker
receipt_hash="$(/usr/bin/sha256sum -- "$work/manager-root-after.tsv")"
validate_and_remove_local_systemd_marker "$work/manager-root-after.tsv"
[[ "$local_systemd_marker_cleanup_count" -eq 1 ]]
[[ -z "$(/usr/bin/find "$work/run-systemd" -mindepth 1 -print -quit)" ]]
[[ "$(/usr/bin/sha256sum -- "$work/manager-root-after.tsv")" == "$receipt_hash" ]]

start_case missing
write_receipt
expect_rejected missing 169
negative_count=$((negative_count + 1))

start_case wrong-name
write_receipt
: >"$work/run-systemd/not-systemd-units-load"
expect_rejected wrong-name 169
negative_count=$((negative_count + 1))

start_case extra
write_receipt
write_marker
: >"$work/run-systemd/extra"
expect_rejected extra 169
negative_count=$((negative_count + 1))

start_case deeper
write_receipt
/usr/bin/mkdir -- "$work/run-systemd/systemd-units-load"
: >"$work/run-systemd/systemd-units-load/deeper"
expect_rejected deeper 170
negative_count=$((negative_count + 1))

start_case directory
write_receipt
/usr/bin/mkdir -- "$work/run-systemd/systemd-units-load"
expect_rejected directory 172
negative_count=$((negative_count + 1))

start_case symlink
write_receipt
/usr/bin/ln -s -- /dev/null "$work/run-systemd/systemd-units-load"
expect_rejected symlink 172
negative_count=$((negative_count + 1))

start_case socket
write_receipt
/usr/bin/python3 - "$work/run-systemd/systemd-units-load" <<'PY'
import socket, sys
s = socket.socket(socket.AF_UNIX)
s.bind(sys.argv[1])
s.close()
PY
expect_rejected socket 171
negative_count=$((negative_count + 1))

start_case fifo
write_receipt
/usr/bin/mkfifo -- "$work/run-systemd/systemd-units-load"
expect_rejected fifo 172
negative_count=$((negative_count + 1))

start_case nonzero
write_receipt
printf x >"$work/run-systemd/systemd-units-load"
/usr/bin/chmod 0444 -- "$work/run-systemd/systemd-units-load"
expect_rejected nonzero 173
negative_count=$((negative_count + 1))

start_case wrong-mode
write_receipt
write_marker
/usr/bin/chmod 0644 -- "$work/run-systemd/systemd-units-load"
expect_rejected wrong-mode 173
negative_count=$((negative_count + 1))

start_case wrong-owner
write_receipt
write_marker
/usr/bin/chown 1:0 -- "$work/run-systemd/systemd-units-load"
expect_rejected wrong-owner 173
negative_count=$((negative_count + 1))

start_case wrong-group
write_receipt
write_marker
/usr/bin/chown 0:1 -- "$work/run-systemd/systemd-units-load"
expect_rejected wrong-group 173
negative_count=$((negative_count + 1))

start_case wrong-links
write_receipt
write_marker
/usr/bin/ln -- "$work/run-systemd/systemd-units-load" "$work/marker-peer"
expect_rejected wrong-links 173
negative_count=$((negative_count + 1))

start_case wrong-device
write_receipt
write_marker
: >"$wrong_device_source"
/usr/bin/chown 0:0 -- "$wrong_device_source"
/usr/bin/chmod 0444 -- "$wrong_device_source"
[[ "$(/usr/bin/stat -c %d -- "$wrong_device_source")" != \
    "$(/usr/bin/stat -c %d -- "$work/run-systemd")" ]]
/usr/bin/mount --bind "$wrong_device_source" /run/systemd/systemd-units-load
nested_marker_mount=true
/usr/bin/mount --bind "$wrong_device_source" "$work/run-systemd/systemd-units-load"
private_marker_mount=true
expect_rejected wrong-device 173
negative_count=$((negative_count + 1))

start_case manager-endpoint
write_receipt
write_marker
/usr/bin/python3 - "$work/run-systemd/private" <<'PY'
import socket, sys
s = socket.socket(socket.AF_UNIX)
s.bind(sys.argv[1])
s.close()
PY
expect_rejected manager-endpoint 169
negative_count=$((negative_count + 1))

start_case binding-mismatch
write_receipt
write_marker
/usr/bin/umount -- /run/systemd
mounted=false
/usr/bin/mkdir -p -- "$fixture_root/binding-mismatch-mounted"
/usr/bin/mount --bind "$fixture_root/binding-mismatch-mounted" /run/systemd
mounted=true
/usr/bin/mount --make-private /run/systemd
systemd_underlay_mount_id="$(systemd_mount_id)"
: >/run/systemd/systemd-units-load
/usr/bin/chown 0:0 -- /run/systemd/systemd-units-load
/usr/bin/chmod 0444 -- /run/systemd/systemd-units-load
expect_rejected binding-mismatch 167
negative_count=$((negative_count + 1))

start_case cleanup-failure
write_receipt
write_marker
cleanup_source="$fixture_root/cleanup-source"
: >"$cleanup_source"
/usr/bin/chown 0:0 -- "$cleanup_source"
/usr/bin/chmod 0444 -- "$cleanup_source"
[[ "$(/usr/bin/stat -c %d -- "$cleanup_source")" == \
    "$(/usr/bin/stat -c %d -- "$work/run-systemd")" ]]
/usr/bin/mount --bind "$cleanup_source" /run/systemd/systemd-units-load
nested_marker_mount=true
/usr/bin/mount --bind "$cleanup_source" "$work/run-systemd/systemd-units-load"
private_marker_mount=true
expect_rejected cleanup-failure 178
[[ "$local_systemd_marker_cleanup_count" -eq 0 ]]
negative_count=$((negative_count + 1))

start_case receipt-drift
write_receipt
write_marker
/usr/bin/sed -i 's/regular\t444/regular\t644/' "$work/manager-root-after.tsv"
expect_rejected receipt-drift 163
negative_count=$((negative_count + 1))

start_case residual
write_receipt
write_marker
/usr/bin/cp -- /usr/bin/sync "$fixture_root/real-sync"
cat >"$fixture_root/sync-wrapper" <<EOF
#!/bin/sh
count_file='$fixture_root/sync-count'
count=0
if [ -f "\$count_file" ]; then count=\$(cat -- "\$count_file"); fi
count=\$((count + 1))
printf '%s\n' "\$count" >"\$count_file"
if [ "\$count" -eq 2 ]; then : >/run/systemd/residual; fi
exec '$fixture_root/real-sync' "\$@"
EOF
/usr/bin/chmod 0755 -- "$fixture_root/sync-wrapper"
/usr/bin/mount --bind "$fixture_root/sync-wrapper" /usr/bin/sync
sync_wrapper_mount=true
expect_rejected residual 180
[[ "$local_systemd_marker_cleanup_count" -eq 1 && -f /run/systemd/residual ]]
negative_count=$((negative_count + 1))

start_case one-removal
write_receipt
write_marker
validate_and_remove_local_systemd_marker "$work/manager-root-after.tsv"
write_marker
expect_rejected second-removal 175
[[ "$local_systemd_marker_cleanup_count" -eq 1 && \
    -f "$work/run-systemd/systemd-units-load" ]]
negative_count=$((negative_count + 1))

[[ "$negative_count" -eq 20 ]]
printf 'local_systemd_marker_oracle_valid=1 negatives=%s cleanup_count=1\n' \
    "$negative_count"
""",
                encoding="utf-8",
                newline="\n",
            )
            syntax = subprocess.run(
                [shutil.which("bash") or "bash", "-n", str(script)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(syntax.returncode, 0, syntax.stdout + syntax.stderr)
            result: subprocess.CompletedProcess[str] | None = None
            ownership: subprocess.CompletedProcess[str] | None = None
            try:
                result = subprocess.run(
                    [
                        "sudo",
                        "-n",
                        "unshare",
                        "--mount",
                        "--fork",
                        shutil.which("bash") or "bash",
                        str(script),
                        str(root),
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=120,
                )
            finally:
                ownership = subprocess.run(
                    [
                        "sudo",
                        "-n",
                        "chown",
                        "-R",
                        f"{os.getuid()}:{os.getgid()}",
                        "--",
                        str(root),
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=30,
                )
            assert ownership is not None
            self.assertEqual(ownership.returncode, 0, ownership.stderr)
            assert result is not None
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                result.stdout,
                "local_systemd_marker_oracle_valid=1 negatives=20 cleanup_count=1\n",
            )

    def test_recovery_guard_condition_lookups_require_path_keys(self) -> None:
        harness = f"""{PCP_PHASE11_WATCHDOG_GUARD_LOOKUP}
systemd-analyze condition "ConditionPathExists=$watchdog_condition"
{PCP_PHASE14_PEER_GUARD_LOOKUP}
systemd-analyze condition "ConditionPathExists=$peer_condition"
"""
        _assert_recovery_guard_path_key_contract(harness)
        for resolved, direct in (
            (
                '"ConditionPathExists=$watchdog_condition"',
                '"ConditionPathExists=${recovery_guard_condition_paths[watchdog.service]}"',
            ),
            (
                '"ConditionPathExists=$peer_condition"',
                '"ConditionPathExists=${recovery_guard_condition_paths[zfs.target]}"',
            ),
        ):
            with self.subTest(direct=direct), self.assertRaises(AssertionError):
                _assert_recovery_guard_path_key_contract(
                    harness.replace(resolved, direct, 1)
                )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and shutil.which("bash"),
        "requires Linux Bash",
    )
    def test_recovery_guard_wrong_domain_fails_before_condition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            guard = root / "90-hoardarr-offline-recovery.conf"
            guard.write_text("guard\n", encoding="ascii")
            script = root / "guard-key-domain.sh"
            script.write_text(
                "set -Eeuo pipefail\n"
                'guard="$1"\n'
                'unit="$2"\n'
                'mutation="$3"\n'
                "declare -A recovery_guard_paths_by_unit=()\n"
                "declare -A recovery_guard_path_owners=()\n"
                "declare -A recovery_guard_file_inodes=()\n"
                "declare -A recovery_guard_condition_paths=()\n"
                'if [[ "$mutation" != missing-unit ]]; then\n'
                '    recovery_guard_paths_by_unit["$unit"]="$guard"\n'
                "fi\n"
                'recovery_guard_path_owners["$guard"]="$unit"\n'
                'recovery_guard_file_inodes["$guard"]="$(stat -c %i -- "$guard")"\n'
                'case "$mutation" in\n'
                '    unit-domain) recovery_guard_condition_paths["$unit"]=/dev/null/wrong-domain ;;\n'
                "    missing-condition) ;;\n"
                '    empty-condition) recovery_guard_condition_paths["$guard"]="" ;;\n'
                '    wrong-owner) recovery_guard_path_owners["$guard"]=other.service ;;\n'
                '    wrong-path) recovery_guard_paths_by_unit["$unit"]="$guard.other" ;;\n'
                '    wrong-inode) recovery_guard_file_inodes["$guard"]=1 ;;\n'
                "    valid|missing-unit) "
                'recovery_guard_condition_paths["$guard"]=/dev/null/exact ;;\n'
                "    *) exit 210 ;;\n"
                "esac\n"
                'if [[ "$unit" == watchdog.service ]]; then\n'
                + PCP_PHASE11_WATCHDOG_GUARD_LOOKUP
                + "\n"
                '    [[ "$watchdog_condition" == /dev/null/exact ]]\n'
                "else\n"
                '    peer_guard="${recovery_guard_paths_by_unit[zfs.target]-}"\n'
                + PCP_PHASE14_PEER_GUARD_LOOKUP
                + "\n"
                '    [[ "$peer_condition" == /dev/null/exact ]]\n'
                "fi\n"
                "printf 'condition-command-reached:%s\\n' \"$unit\"\n",
                encoding="utf-8",
                newline="\n",
            )
            syntax = subprocess.run(
                [shutil.which("bash") or "bash", "-n", str(script)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(syntax.returncode, 0, syntax.stdout + syntax.stderr)
            for unit in ("watchdog.service", "zfs.target"):
                valid = subprocess.run(
                    [
                        shutil.which("bash") or "bash",
                        str(script),
                        str(guard),
                        unit,
                        "valid",
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
                self.assertEqual(valid.stdout, f"condition-command-reached:{unit}\n")
                for mutation in (
                    "unit-domain",
                    "missing-unit",
                    "missing-condition",
                    "empty-condition",
                    "wrong-owner",
                    "wrong-path",
                    "wrong-inode",
                ):
                    with self.subTest(unit=unit, mutation=mutation):
                        rejected = subprocess.run(
                            [
                                shutil.which("bash") or "bash",
                                str(script),
                                str(guard),
                                unit,
                                mutation,
                            ],
                            text=True,
                            capture_output=True,
                            check=False,
                        )
                        self.assertNotEqual(rejected.returncode, 0)
                        self.assertEqual(rejected.stdout, "")

    @unittest.skipUnless(
        sys.platform.startswith("linux") and shutil.which("bash"),
        "requires Linux Bash",
    )
    def test_pcp_trace_trap_preserves_original_exit_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            namespace = root / "namespace"
            namespace.mkdir()
            trace = root / "exit-preservation.trace"
            for index in range(4):
                _append_pcp_trace_phase(trace, index, "BEGIN")
                _append_pcp_trace_phase(trace, index, "PASS")
            script = root / "trace-exit.sh"
            script.write_text(
                "set -Eeuo pipefail\n"
                + _pcp_trace_shell_prelude()
                + "\ntrace_begin 05-mount-namespace mount-namespace\nexit 73\n",
                encoding="utf-8",
                newline="\n",
            )
            result = subprocess.run(
                [
                    shutil.which("bash") or "bash",
                    str(script),
                    "unused-1",
                    "unused-2",
                    "unused-3",
                    "unused-4",
                    str(trace),
                ],
                capture_output=True,
                check=False,
                env={**os.environ, "MSYS": "winsymlinks:sys"},
                text=True,
            )
            try:
                trace_text, trace_status = _validate_pcp_trace(trace, root, namespace)
            except AssertionError as exc:
                self.fail(result.stdout + result.stderr + str(exc))
            self.assertEqual(result.returncode, 73, trace_text)
            self.assertEqual(trace_status, result.returncode, trace_text)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux mounts")
    def test_real_noble_pcp_postinst_presets_with_production_service_guard(
        self,
    ) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")

        def shell_function(name: str) -> str:
            if name == "write_retained_recovery_guard_manifest":
                start = payload.index(f"{name}() {{\n")
                end = payload.index("\nprepare_temporary_unit_mask() {", start)
                return payload[start : end + 1]
            match = re.search(
                rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n",
                payload,
                flags=re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"missing production function {name}")
            assert match is not None
            return match.group(0)

        required = (
            "apt-get",
            "bash",
            "deb-systemd-helper",
            "dpkg-deb",
            "dpkg-query",
            "sudo",
            "systemd-analyze",
            "systemctl",
            "unshare",
        )
        missing = [command for command in required if shutil.which(command) is None]
        self.assertEqual(missing, [], f"missing Noble service-guard tools: {missing}")
        sudo = subprocess.run(
            ["sudo", "-n", "true"], text=True, capture_output=True, check=False
        )
        self.assertEqual(sudo.returncode, 0, sudo.stderr)

        expected_version = "6.2.0-1.1build4"
        expected_deb_sha256 = (
            "5941a5aabb5e873883b1f4ac8e5e577a3617a8c9b7cb1918a3baea6e1d1b89a9"
        )
        expected_postinst_sha256 = (
            "a964a5c5a17ad154eec1068fe984c37fa9cc1642d85fe5dc393f6022afe6440c"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            trace_path = root / "pcp-harness.trace"
            namespace_path = (root / "namespace").resolve()
            self.assertEqual(namespace_path.parent, root.resolve())
            self.assertEqual(trace_path.resolve(strict=False).parent, root.resolve())
            self.assertNotEqual(trace_path.resolve(strict=False), namespace_path)
            _append_pcp_trace_phase(trace_path, 0, "BEGIN")
            _append_pcp_trace_phase(trace_path, 0, "PASS")
            _append_pcp_trace_phase(trace_path, 1, "BEGIN")
            download = subprocess.run(
                ["apt-get", "download", f"pcp={expected_version}"],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
                timeout=240,
            )
            self.assertEqual(download.returncode, 0, download.stdout + download.stderr)
            _append_pcp_trace_phase(trace_path, 1, "PASS")
            debs = list(root.glob("pcp_*.deb"))
            self.assertEqual(len(debs), 1, [path.name for path in debs])
            _append_pcp_trace_phase(trace_path, 2, "BEGIN")
            self.assertEqual(
                hashlib.sha256(debs[0].read_bytes()).hexdigest(), expected_deb_sha256
            )
            _append_pcp_trace_phase(trace_path, 2, "PASS")
            _append_pcp_trace_phase(trace_path, 3, "BEGIN")
            control = root / "control"
            data = root / "data"
            subprocess.run(["dpkg-deb", "-e", str(debs[0]), str(control)], check=True)
            subprocess.run(["dpkg-deb", "-x", str(debs[0]), str(data)], check=True)
            postinst = control / "postinst"
            self.assertEqual(
                hashlib.sha256(postinst.read_bytes()).hexdigest(),
                expected_postinst_sha256,
            )
            _append_pcp_trace_phase(trace_path, 3, "PASS")
            policy = json.loads(
                (ROOT / "packaging" / "offline" / "package-policy.json").read_text(
                    encoding="utf-8"
                )
            )
            denied_units = policy["denied_units"]
            denied_path = root / "denied-units.txt"
            denied_path.write_text(
                "".join(f"{unit}\n" for unit in denied_units), encoding="utf-8"
            )

            harness = root / "pcp-service-guard.sh"
            harness.write_text(
                "\n".join(
                    (
                        "set -Eeuo pipefail",
                        _pcp_trace_shell_prelude(),
                        PCP_MANAGER_ROOT_SNAPSHOT_FUNCTION,
                        shell_function("install_service_start_guard"),
                        shell_function("entry_is_root_owned"),
                        shell_function("validate_preserved_unit_objects"),
                        shell_function("prepare_recovery_unit_guard"),
                        shell_function("validate_recovery_unit_guards"),
                        shell_function("retain_recovery_unit_guards"),
                        shell_function("remove_recovery_unit_guards"),
                        shell_function("write_retained_recovery_guard_manifest"),
                        shell_function("prepare_temporary_unit_mask"),
                        shell_function("cleanup_temporary_masks"),
                        shell_function("cleanup_service_guards"),
                        shell_function("disable_unmasked_units"),
                        r"""
postinst="$1"
data="$2"
work="$3"
denied_file="$4"
trace_begin 05-mount-namespace mount-namespace
mount --make-rprivate /
mkdir -p "$work"/{etc-systemd,systemd-state,run-systemd,usr-sbin,wrappers,state,install}
cp -a "$(command -v chroot)" "$work/usr-sbin/chroot"
cp -a "$data/usr/lib/systemd/system/." "$work/vendor-units/" 2>/dev/null || {
    mkdir -p "$work/vendor-units"
    cp -a "$data/usr/lib/systemd/system/." "$work/vendor-units/"
}
while IFS= read -r unit; do
    [[ "$unit" =~ ^[A-Za-z0-9@_.:-]+\.(service|socket|timer|target)$ ]]
    unit_path="$work/vendor-units/$unit"
    [[ -e "$unit_path" ]] && continue
    case "$unit" in
        *.service) body=$'[Service]\nType=oneshot\nExecStart=/bin/true' ;;
        *.socket) body=$'[Socket]\nListenStream=/run/hoardarr-test-'"${unit//[^A-Za-z0-9]/-}" ;;
        *.timer) body=$'[Timer]\nOnBootSec=1h' ;;
        *.target) body= ;;
    esac
    printf '[Unit]\nDescription=Hoardarr denied-unit preset regression\n%s\n[Install]\nWantedBy=multi-user.target\n' \
        "$body" >"$unit_path"
done <"$denied_file"
# Guarantee one supported static-style unit so intentional retained-guard
# behavior is exercised independently of the host package set.
printf '%s\n' \
    '[Unit]' \
    'Description=Hoardarr static denied-unit regression' \
    '[Service]' \
    'Type=oneshot' \
    'ExecStart=/bin/true' \
    >"$work/vendor-units/watchdog.service"
printf '%s\n' \
    '[Unit]' \
    'Description=Hoardarr static peer denied-unit regression' \
    >"$work/vendor-units/zfs.target"
mount --bind "$work/vendor-units" /usr/lib/systemd/system
mount --bind "$work/etc-systemd" /etc/systemd/system
mount --bind "$work/systemd-state" /var/lib/systemd
mount --bind "$work/run-systemd" /run/systemd
mount --bind "$work/usr-sbin" /usr/sbin
for command in dpkg-maintscript-helper touch chown groupadd useradd; do
    cat >"$work/wrappers/$command" <<'EOF'
#!/bin/sh
case "$(basename "$0"):$1" in
    dpkg-maintscript-helper:supports) exit 0 ;;
esac
exit 0
EOF
    chmod 0755 "$work/wrappers/$command"
done
cat >"$work/wrappers/chmod" <<'EOF'
#!/bin/sh
# Package-maintainer chmod calls remain isolated.  Delegate only the exact
# recovery-guard temporary-file operation performed by the extracted
# production helper, inside this fixture's private systemd bind mount.
if [ "$#" -ne 3 ] || [ "$1" != 0644 ] || [ "$2" != -- ]; then
    exit 0
fi
candidate=$3
case "$candidate" in
    "$HOARDARR_TEST_RECOVERY_ROOT"/*) ;;
    *) exit 0 ;;
esac
case "$candidate" in
    *//*|*/../*|*/./*) exit 0 ;;
esac
parent=${candidate%/*}
name=${candidate##*/}
case "$name" in
    .hoardarr-recovery.??????) ;;
    *) exit 0 ;;
esac
suffix=${name#.hoardarr-recovery.}
case "$suffix" in
    *[!A-Za-z0-9]*) exit 0 ;;
esac
parent_name=${parent##*/}
case "$parent_name" in
    *.d) unit=${parent_name%.d} ;;
    *) exit 0 ;;
esac
case "$unit" in
    ''|*[!A-Za-z0-9@_.:-]*) exit 0 ;;
esac
unit_count=0
while IFS= read -r denied_unit; do
    if [ "$denied_unit" = "$unit" ]; then
        unit_count=$((unit_count + 1))
    fi
done <"$HOARDARR_TEST_DENIED_UNITS"
[ "$unit_count" -eq 1 ] || exit 0
[ -f "$candidate" ] && [ ! -L "$candidate" ] || exit 0
[ "$(/usr/bin/stat -c %h -- "$candidate" 2>/dev/null)" = 1 ] || exit 0
canonical_root=$(/usr/bin/readlink -e -- "$HOARDARR_TEST_RECOVERY_ROOT") || exit 0
canonical_parent=$(/usr/bin/readlink -e -- "$parent") || exit 0
canonical_target=$(/usr/bin/readlink -e -- "$candidate") || exit 0
[ "$canonical_parent" = "$canonical_root/$unit.d" ] || exit 0
[ "$canonical_target" = "$canonical_parent/$name" ] || exit 0
/usr/bin/chmod 0644 -- "$candidate" || exit $?
[ "$(/usr/bin/stat -c %a -- "$candidate" 2>/dev/null)" = 644 ] || exit 1
printf '%s\t%s\t0644\n' "$unit" "$name" >>"$HOARDARR_TEST_CHMOD_RECEIPT" || exit 1
exit 0
EOF
chmod 0755 "$work/wrappers/chmod"
cat >"$work/wrappers/getent" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod 0755 "$work/wrappers/getent"
export HOARDARR_TEST_RECOVERY_ROOT=/etc/systemd/system
export HOARDARR_TEST_DENIED_UNITS="$denied_file"
export HOARDARR_TEST_CHMOD_RECEIPT="$work/chmod-delegated.tsv"
: >"$HOARDARR_TEST_CHMOD_RECEIPT"
export PATH="$work/wrappers:/usr/sbin:/usr/bin:/bin"
export DPKG_MAINTSCRIPT_PACKAGE=pcp
export DPKG_MAINTSCRIPT_NAME=postinst
export SYSTEMD_OFFLINE=1
pcp_units=(pcp-reboot-init.service pmcd.service pmlogger.service pmie.service pmproxy.service)
mapfile -t all_denied_units <"$denied_file"

# Prove malformed and unrelated requests remain isolated no-ops.  These
# fixtures are wholly inside the disposable private mount namespace.
negative_unit=${all_denied_units[0]}
negative_dir="$HOARDARR_TEST_RECOVERY_ROOT/$negative_unit.d"
wrong_dir="$HOARDARR_TEST_RECOVERY_ROOT/not-denied.service.d"
mkdir -- "$negative_dir" "$wrong_dir"
negative_target="$negative_dir/.hoardarr-recovery.NEG001"
wrong_name="$negative_dir/not-a-recovery-temporary"
wrong_directory="$wrong_dir/.hoardarr-recovery.DIR001"
outside_target="$work/.hoardarr-recovery.OUT001"
unrelated_target="$work/package-mode-target"
for path in "$negative_target" "$wrong_name" "$wrong_directory" \
    "$outside_target" "$unrelated_target"; do
    : >"$path"
    /usr/bin/chmod 0600 -- "$path"
done
symlink_target="$negative_dir/.hoardarr-recovery.SYM001"
hardlink_source="$HOARDARR_TEST_RECOVERY_ROOT/.hoardarr-hardlink-negative-source"
hardlink_target="$negative_dir/.hoardarr-recovery.LNK001"
ln -s -- "$outside_target" "$symlink_target"
: >"$hardlink_source"
/usr/bin/chmod 0600 -- "$hardlink_source"
ln -- "$hardlink_source" "$hardlink_target"
[[ "$hardlink_source" == "$HOARDARR_TEST_RECOVERY_ROOT/"* ]]
[[ "${hardlink_source%/*}" == "$HOARDARR_TEST_RECOVERY_ROOT" ]]
[[ "$hardlink_source" != *.d/* ]]
[[ "$hardlink_source" != */../* ]]
[[ -f "$hardlink_source" && ! -L "$hardlink_source" ]]
[[ "$(/usr/bin/stat -c %d -- "$hardlink_source")" == \
    "$(/usr/bin/stat -c %d -- "$hardlink_target")" ]]
[[ "$(/usr/bin/stat -c %i -- "$hardlink_source")" == \
    "$(/usr/bin/stat -c %i -- "$hardlink_target")" ]]
[[ "$(/usr/bin/stat -c %h -- "$hardlink_source")" == 2 ]]
[[ "$(/usr/bin/stat -c %h -- "$hardlink_target")" == 2 ]]
"$work/wrappers/chmod" 0600 -- "$negative_target"
"$work/wrappers/chmod" 0644 "$negative_target"
"$work/wrappers/chmod" 0644 -- "$negative_target" extra
"$work/wrappers/chmod" 0644 -- "$negative_dir/../$negative_unit.d/.hoardarr-recovery.NEG001"
"$work/wrappers/chmod" 0644 -- "$outside_target"
"$work/wrappers/chmod" 0644 -- "$wrong_name"
"$work/wrappers/chmod" 0644 -- "$wrong_directory"
"$work/wrappers/chmod" 0644 -- "$symlink_target"
"$work/wrappers/chmod" 0644 -- "$hardlink_target"
"$work/wrappers/chmod" 0644 -- "$unrelated_target"
for path in "$negative_target" "$wrong_name" "$wrong_directory" \
    "$outside_target" "$unrelated_target" "$hardlink_source" "$hardlink_target"; do
    [[ "$(/usr/bin/stat -c %a -- "$path")" == 600 ]]
done
[[ ! -s "$HOARDARR_TEST_CHMOD_RECEIPT" ]]
rm -f -- "$symlink_target" "$hardlink_target" "$hardlink_source" \
    "$negative_target" "$wrong_name" "$wrong_directory" "$outside_target" \
    "$unrelated_target"
[[ ! -e "$hardlink_target" && ! -L "$hardlink_target" ]]
[[ ! -e "$hardlink_source" && ! -L "$hardlink_source" ]]
rmdir -- "$negative_dir" "$wrong_dir"
trace_pass

# Reproduce the accepted F7A defect using the exact package script.
trace_begin 06-old-failure old-failure
for unit in "${pcp_units[@]}"; do ln -s /dev/null "/etc/systemd/system/$unit"; done
old_status=0
"$postinst" configure >"$work/old.log" 2>&1 || old_status=$?
(( old_status != 0 ))
grep -Fq 'Failed to preset unit' "$work/old.log"
find "$work/etc-systemd" -mindepth 1 -maxdepth 1 -delete
find "$work/systemd-state" -mindepth 1 -delete
trace_pass

# Exercise the production classification and exact start guard.
trace_begin 07-guard-preparation guard-preparation
target="/"
mask_root=/etc/systemd/system
install_root="$work/install"
state_root="$work/state"
policy=/usr/sbin/policy-rc.d
policy_backup="$install_root/policy-rc.d.original"
policy_state=absent
temporary_masks=()
declare -A temporary_mask_inodes=()
temporary_masks_cleanup_complete=false
policy_cleanup_complete=false
service_guard_cleanup_complete=false
declare -A preserved_unit_masks=()
declare -A preserved_unit_mask_inodes=()
declare -A preserved_package_aliases=()
declare -A preserved_package_alias_inodes=()
declare -A preserved_package_alias_targets=()
declare -A preserved_package_alias_canonical_units=()
declare -A policy_guarded_canonical_units=()
declare -A policy_guarded_absent_units=()
recovery_guard_files=()
recovery_guard_created_directories=()
declare -A recovery_guard_file_inodes=()
declare -A recovery_guard_contents=()
declare -A recovery_guard_condition_paths=()
declare -A recovery_guard_directory_inodes=()
declare -A recovery_guard_paths_by_unit=()
declare -A recovery_guard_path_owners=()
declare -A recovery_guard_paths_retained=()
declare -A recovery_guard_retained_states=()
recovery_guards_cleanup_complete=false
recovery_guard_authorization_root=/etc/systemd/system/.hoardarr-service-start-authorized
denied_units=("${all_denied_units[@]}")
denied_units_finalized=false
service_readback_complete=false
package_transaction_started=false
install_service_start_guard
# A pre-existing authorization namespace is never trusted during guard setup.
mkdir -- "$recovery_guard_authorization_root"
if prepare_recovery_unit_guard corosync.service >/dev/null 2>&1; then exit 95; fi
rmdir -- "$recovery_guard_authorization_root"
for unit in "${denied_units[@]}"; do
    prepare_temporary_unit_mask "$mask_root/$unit" "$unit"
    [[ ! -e "$mask_root/$unit" && ! -L "$mask_root/$unit" ]]
    prepare_recovery_unit_guard "$unit"
done
[[ "$(wc -l <"$HOARDARR_TEST_CHMOD_RECEIPT")" -eq "${#denied_units[@]}" ]]
declare -A delegated_chmod_units=()
while IFS=$'\t' read -r unit temporary_name delegated_mode extra; do
    [[ -z "$extra" && "$delegated_mode" == 0644 ]]
    [[ "$temporary_name" == .hoardarr-recovery.?????? ]]
    [[ "$temporary_name" != *[!A-Za-z0-9.\-]* ]]
    [[ -z "${delegated_chmod_units[$unit]+present}" ]]
    delegated_chmod_units[$unit]=$temporary_name
done <"$HOARDARR_TEST_CHMOD_RECEIPT"
for unit in "${denied_units[@]}"; do
    [[ -n "${delegated_chmod_units[$unit]+present}" ]]
done
chmod_receipt_hash="$(sha256sum -- "$HOARDARR_TEST_CHMOD_RECEIPT" | awk '{print $1}')"
start_status=0
"$policy" pmcd.service start || start_status=$?
[[ "$start_status" -eq 101 ]]
trace_pass

trace_begin 08-pcp-configure pcp-configure
"$postinst" configure >"$work/corrected.log" 2>&1
! grep -Fq 'Failed to preset unit' "$work/corrected.log"
[[ "$(sha256sum -- "$HOARDARR_TEST_CHMOD_RECEIPT" | awk '{print $1}')" == \
    "$chmod_receipt_hash" ]]
trace_pass
trace_begin 09-all-denied-presets all-denied-presets
for unit in "${denied_units[@]}"; do
    SYSTEMD_OFFLINE=1 systemctl preset "$unit"
done >"$work/all-denied-presets.log" 2>&1
! grep -Fq 'Failed to preset unit' "$work/all-denied-presets.log"
[[ "$(sha256sum -- "$HOARDARR_TEST_CHMOD_RECEIPT" | awk '{print $1}')" == \
    "$chmod_receipt_hash" ]]
trace_pass
""",
                        _pcp_phase_ten_with_causal_proof(),
                        r"""
trace_begin 11-interrupted-retention interrupted-retention
package_transaction_started=true
interrupted_status=0
# The old marker namespace cannot authorize the structurally false condition,
# and its appearance makes interrupted recovery evidence fail closed.
mkdir -- "$recovery_guard_authorization_root"
: >"$recovery_guard_authorization_root/watchdog.service"
rm -f -- "$state_root/service-guard-recovery.txt"
cleanup_service_guards >/dev/null 2>&1 || interrupted_status=$?
[[ "$interrupted_status" -ne 0 ]]
[[ ! -e "$state_root/service-guard-recovery.txt" ]]
""",
                        PCP_PHASE11_WATCHDOG_GUARD_LOOKUP,
                        r"""
systemd-analyze condition \
    "ConditionPathExists=$watchdog_condition" \
    >/dev/null 2>&1 && exit 98
rm -f -- "$recovery_guard_authorization_root/watchdog.service"
rmdir -- "$recovery_guard_authorization_root"
interrupted_status=0
cleanup_service_guards >/dev/null 2>&1 || interrupted_status=$?
[[ "$interrupted_status" -ne 0 ]]
validate_recovery_unit_guards
grep -Fq 'finalization=false readback=false' "$state_root/service-guard-recovery.txt"
for path in "${recovery_guard_files[@]}"; do
    systemd-analyze condition "ConditionPathExists=${recovery_guard_condition_paths[$path]}" \
        >/dev/null 2>&1 && exit 96
done
trace_pass
trace_begin 12-final-disable-readback final-disable-readback
disable_unmasked_units
[[ "$denied_units_finalized" == true ]]
[[ "$(wc -l <"$state_root/service-policy-readback.tsv")" -eq "${#denied_units[@]}" ]]
python3 - "$state_root/service-policy-readback.tsv" \
    "$state_root/service-policy-readback.json" <<'PY'
import json, pathlib, sys
rows=[]
for raw in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    unit,enabled,enabled_status,active,active_status,boundary=raw.split("\t")
    rows.append({
        "unit":unit,
        "enabled_state":enabled,
        "enabled_status":int(enabled_status),
        "active_state":active,
        "active_status":int(active_status),
        "start_boundary":boundary,
    })
pathlib.Path(sys.argv[2]).write_text(
    json.dumps({"schema_version":1,"units":rows})+"\n", encoding="utf-8"
)
PY
cleanup_service_guards
[[ "$service_guard_cleanup_complete" == true ]]
trace_pass
trace_begin 13-retained-manifest retained-manifest
write_retained_recovery_guard_manifest
retained_count=0
while IFS=$'\t' read -r unit enabled enabled_status active active_status boundary; do
    path="${recovery_guard_paths_by_unit[$unit]}"
    if [[ "$boundary" == condition-drop-in ]]; then
        [[ -n "${recovery_guard_paths_retained[$path]+present}" && -f "$path" ]]
        systemd-analyze condition "ConditionPathExists=${recovery_guard_condition_paths[$path]}" \
            >/dev/null 2>&1 && exit 97
        retained_count=$((retained_count + 1))
    else
        [[ ! -e "$path" && ! -L "$path" ]]
    fi
done <"$state_root/service-policy-readback.tsv"
(( retained_count > 0 ))
python3 - "$state_root/service-retained-guards.json" <<'PY'
import json, pathlib, sys
document=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert document["schema_version"] == 1
assert document["supported_activation_action"] == "remove-exact-verified-guard-only"
assert document["removal_requirement"] == "later-authorized-selection-must-verify-unit-path-inode-and-sha256-before-removal"
assert document["guards"]
for guard in document["guards"]:
    assert guard["reason"] == "unit-file-state-requires-persistent-start-boundary"
    assert guard["enabled_state"] in {"static","indirect","generated","transient"}
    assert guard["canonical_path"].startswith("/etc/systemd/system/")
    assert guard["condition_path"] == f"/dev/null/hoardarr-offline-service-guard/{guard['unit']}"
    assert guard["inode"] > 0 and len(guard["sha256"]) == 64
PY
sha256sum "$state_root/service-policy-readback.json" \
    "$state_root/service-retained-guards.json" >"$state_root/SHA256SUMS"
(cd "$state_root" && sha256sum --check --strict SHA256SUMS)
trace_pass
# Removing one exact verified guard in this disposable fixture cannot release
# its retained static peer.  Product activation remains out of scope.
trace_begin 14-peer-isolation peer-isolation
watchdog_guard="${recovery_guard_paths_by_unit[watchdog.service]}"
peer_guard="${recovery_guard_paths_by_unit[zfs.target]}"
[[ -f "$watchdog_guard" && -f "$peer_guard" ]]
""",
                        PCP_PHASE14_PEER_GUARD_LOOKUP,
                        r"""
watchdog_inode="$(stat -c %i -- "$watchdog_guard")"
watchdog_hash="$(sha256sum -- "$watchdog_guard" | awk '{print $1}')"
python3 - "$state_root/service-retained-guards.json" "$watchdog_inode" \
    "$watchdog_hash" <<'PY'
import json, pathlib, sys
document=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
matches=[guard for guard in document["guards"] if guard["unit"] == "watchdog.service"]
assert len(matches) == 1
assert matches[0]["inode"] == int(sys.argv[2])
assert matches[0]["sha256"] == sys.argv[3]
PY
rm -f -- "$watchdog_guard"
[[ ! -e "$watchdog_guard" && -f "$peer_guard" ]]
systemd-analyze condition \
    "ConditionPathExists=$peer_condition" \
    >/dev/null 2>&1 && exit 99
for unit in "${denied_units[@]}"; do
    state_status=0
    state="$(SYSTEMD_OFFLINE=1 systemctl --root=/ is-enabled "$unit" 2>&1)" || state_status=$?
    [[ "$state" != enabled ]]
done
trace_pass
trace_begin 15-fixture-cleanup fixture-cleanup
printf '%s\n' \
    real_pcp_old_preset_failure=reproduced \
    real_pcp_corrected_preset_errors=0 \
    policy_rc_d_start_status=101 \
    host_manager_contacts=0 \
    final_denied_units="${#denied_units[@]}"
trace_pass
trace_terminal=true
trace_write "HPCP|1|EXIT|$current_phase|status=0|line=$LINENO|function=main|label=$current_label"
trap - ERR EXIT
exit 0
""",
                    )
                ),
                encoding="utf-8",
                newline="\n",
            )
            _assert_pcp_offline_nonactivation_contract(
                harness.read_text(encoding="utf-8")
            )
            _assert_recovery_guard_path_key_contract(
                harness.read_text(encoding="utf-8")
            )
            result: subprocess.CompletedProcess[str] | None = None
            run_error: OSError | subprocess.TimeoutExpired | None = None
            ownership: subprocess.CompletedProcess[str] | None = None
            ownership_error: OSError | subprocess.TimeoutExpired | None = None
            manager_receipt_diagnostic = ""
            systemd_receipt_diagnostic = ""
            try:
                try:
                    result = subprocess.run(
                        [
                            "sudo",
                            "-n",
                            "unshare",
                            "--mount",
                            "--fork",
                            "bash",
                            str(harness),
                            str(postinst),
                            str(data),
                            str(namespace_path),
                            str(denied_path),
                            str(trace_path),
                        ],
                        text=True,
                        capture_output=True,
                        check=False,
                        timeout=240,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    run_error = exc
            finally:
                if namespace_path.exists():
                    try:
                        ownership = subprocess.run(
                            [
                                "sudo",
                                "-n",
                                "chown",
                                "-R",
                                f"{os.getuid()}:{os.getgid()}",
                                "--",
                                str(namespace_path),
                            ],
                            text=True,
                            capture_output=True,
                            check=False,
                            timeout=30,
                        )
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        ownership_error = exc
            trace_text, trace_status = _validate_pcp_trace(
                trace_path, root, namespace_path
            )
            if ownership_error is not None:
                self.fail(
                    f"namespace ownership cleanup failed: {ownership_error}\n{trace_text}"
                )
            if ownership is not None:
                self.assertEqual(
                    ownership.returncode,
                    0,
                    ownership.stdout + ownership.stderr + trace_text,
                )
            if run_error is not None:
                self.fail(f"PCP harness execution failed: {run_error}\n{trace_text}")
            assert result is not None
            self.assertEqual(trace_status, result.returncode, trace_text)
            try:
                before_text, before_status, _ = _validate_manager_root_receipt(
                    namespace_path / "manager-root-before.tsv",
                    namespace_path,
                    namespace_path / "run-systemd",
                    "before",
                )
                after_text, after_status, _ = _validate_manager_root_receipt(
                    namespace_path / "manager-root-after.tsv",
                    namespace_path,
                    namespace_path / "run-systemd",
                    "after",
                )
                source_text, package_version, executable_hash = (
                    _validate_systemd_source_receipt(
                        namespace_path / "systemd-source.tsv",
                        namespace_path,
                    )
                )
                causal_text = _validate_systemd_causal_receipt(
                    namespace_path / "systemd-causal.tsv",
                    namespace_path,
                )
            except AssertionError as exc:
                self.fail(
                    f"systemd/manager receipt validation failed: {exc}\n{trace_text}"
                )
            self.assertIsNone(before_status)
            self.assertIsNotNone(after_status)
            manager_receipt_diagnostic = (
                "\nVALIDATED MANAGER-ROOT BEFORE RECEIPT\n"
                + before_text
                + "VALIDATED MANAGER-ROOT AFTER RECEIPT\n"
                + after_text
            )
            systemd_receipt_diagnostic = (
                "VALIDATED SYSTEMD SOURCE RECEIPT\n"
                + source_text
                + "VALIDATED SYSTEMD CAUSAL RECEIPT\n"
                + causal_text
            )
            self.assertTrue(package_version.startswith("255.4-"))
            self.assertRegex(executable_hash, r"^[0-9a-f]{64}$")
        self.assertEqual(
            result.returncode,
            0,
            result.stdout
            + result.stderr
            + trace_text
            + manager_receipt_diagnostic
            + systemd_receipt_diagnostic,
        )
        self.assertIn("real_pcp_old_preset_failure=reproduced", result.stdout)
        self.assertIn("real_pcp_corrected_preset_errors=0", result.stdout)
        self.assertIn("policy_rc_d_start_status=101", result.stdout)
        self.assertIn("host_manager_contacts=0", result.stdout)
        self.assertIn(f"final_denied_units={len(denied_units)}", result.stdout)

    def test_debian_control_metadata_is_parsed_by_field_name(self) -> None:
        control = """Package: snapraid
Version: 12.3-1
Architecture: amd64
Source: snapraid
Depends: libc6 (>= 2.34), libgcc-s1 (>= 3.0)
Homepage: https://www.snapraid.it/
Description: Backup program for disk arrays
"""
        completed = mock.Mock(stdout=control)
        with mock.patch.object(offline_repo, "_run", return_value=completed) as run:
            fields = offline_repo._deb_fields(pathlib.Path("snapraid.deb"))

        run.assert_called_once_with(["dpkg-deb", "-f", "snapraid.deb"])
        self.assertEqual(fields["Package"], "snapraid")
        self.assertEqual(fields["Version"], "12.3-1")
        self.assertEqual(fields["Architecture"], "amd64")
        self.assertEqual(fields["Source"], "snapraid")
        self.assertEqual(fields["Depends"], "libc6 (>= 2.34), libgcc-s1 (>= 3.0)")

    def test_reconciled_plan_covers_profiles_providers_and_all_dispositions(
        self,
    ) -> None:
        plan = offline_repo.build_plan()
        candidates = plan.matrix["candidates"]
        self.assertEqual(len(plan.roots), 109)
        self.assertEqual(len(candidates), 129)
        self.assertEqual(
            {item["disposition"] for item in candidates},
            {
                "included-and-installed",
                "included-but-feature-disabled",
                "sidecar-manual-offline-import",
                "not-supported",
            },
        )
        selected = {item["package"] for item in candidates if item["package"]}
        self.assertEqual(selected, set(plan.roots))
        self.assertIn("snapraid", selected)
        self.assertIn("b3sum", selected)
        self.assertIn("xxhash", selected)
        self.assertIn("lm-sensors", selected)
        self.assertIn("sysstat", selected)
        self.assertIn("pcp", selected)
        self.assertNotIn("dstat", selected)

    def test_compatibility_families_are_explicit_without_changing_product_roots(
        self,
    ) -> None:
        plan = offline_repo.build_plan()
        systemd_members = {
            "systemd",
            "systemd-sysv",
            "systemd-timesyncd",
            "systemd-resolved",
            "udev",
            "libudev1",
            "libsystemd0",
            "libsystemd-shared",
            "libpam-systemd",
            "libnss-systemd",
            "systemd-dev",
        }
        linux_members = {
            "linux-generic",
            "linux-image-generic",
            "linux-headers-generic",
        }
        self.assertEqual(len(plan.roots), 109)
        self.assertEqual(len(plan.compatibility_families), 2)
        families = {family["id"]: family for family in plan.compatibility_families}
        self.assertEqual(set(families), {"systemd-noble", "linux-meta-noble"})
        self.assertEqual(set(families["systemd-noble"]["members"]), systemd_members)
        self.assertEqual(families["systemd-noble"]["exact_dependencies"], {})
        linux_family = families["linux-meta-noble"]
        self.assertEqual(set(linux_family["members"]), linux_members)
        self.assertEqual(
            linux_family["exact_dependencies"],
            {
                "linux-generic": (
                    "linux-image-generic",
                    "linux-headers-generic",
                )
            },
        )
        self.assertTrue(
            all(
                family["version_policy"] == "single-candidate-version"
                for family in families.values()
            )
        )
        self.assertEqual(
            set(plan.roots),
            {item["package"] for item in plan.matrix["candidates"] if item["package"]},
        )
        self.assertEqual(set(plan.roots) & linux_members, {"linux-image-generic"})
        self.assertTrue(systemd_members - set(plan.roots))

    def test_compatibility_family_schema_rejects_unsafe_and_duplicate_values(
        self,
    ) -> None:
        valid = [
            {
                "id": "systemd-noble",
                "members": ["systemd", "systemd-sysv"],
                "version_policy": "single-candidate-version",
            }
        ]
        self.assertEqual(
            offline_repo._compatibility_families(valid)[0]["id"], "systemd-noble"
        )
        invalid_values = (
            [{**valid[0], "members": ["systemd", "../unsafe"]}],
            [valid[0], {**valid[0], "members": ["udev"]}],
            [valid[0], {**valid[0], "id": "udev-noble"}],
            [{**valid[0], "version_policy": "runner-installed"}],
            [{**valid[0], "extra": True}],
            [{**valid[0], "members": []}],
            [
                {
                    **valid[0],
                    "exact_dependencies": {"not-a-member": ["systemd"]},
                }
            ],
            [
                {
                    **valid[0],
                    "exact_dependencies": {"systemd": ["systemd"]},
                }
            ],
            [
                {
                    **valid[0],
                    "exact_dependencies": {"systemd": ["missing-member"]},
                }
            ],
        )
        for value in invalid_values:
            with (
                self.subTest(value=value),
                self.assertRaises(offline_repo.OfflineRepositoryError),
            ):
                offline_repo._compatibility_families(value)

    def test_exact_dependency_parser_is_whitespace_and_alternative_safe(self) -> None:
        version = "6.8.0-138.138"
        self.assertEqual(
            offline_repo._exact_dependency_versions(
                " linux-image-generic   (= 6.8.0-138.138) , "
                "linux-headers-generic (= 6.8.0-138.138), "
                "unrelated:any (>= 1) "
            ),
            {
                "linux-image-generic": version,
                "linux-headers-generic": version,
            },
        )
        self.assertEqual(
            offline_repo._exact_dependency_versions(
                "linux-image-generic (= 6.8.0-138.138) | linux-image-virtual "
                "(= 6.8.0-138.138), linux-headers-generic (>= 6.8.0-138.138)"
            ),
            {},
        )
        with self.assertRaisesRegex(
            offline_repo.OfflineRepositoryError, "unsupported clause"
        ):
            offline_repo._exact_dependency_versions("linux-image-generic (6.8.0)")

    def test_linux_meta_dependency_validation_requires_exact_sibling_versions(
        self,
    ) -> None:
        version = "6.8.0-138.138"
        family = {
            "id": "linux-meta-noble",
            "members": (
                "linux-generic",
                "linux-image-generic",
                "linux-headers-generic",
            ),
            "version_policy": "single-candidate-version",
            "exact_dependencies": {
                "linux-generic": (
                    "linux-image-generic",
                    "linux-headers-generic",
                )
            },
        }
        valid = [
            {
                "name": "linux-generic",
                "version": version,
                "depends": (
                    f"linux-image-generic (= {version}), "
                    f"linux-headers-generic (= {version})"
                ),
            }
        ]
        offline_repo._validate_family_dependencies((family,), valid)
        invalid_depends = (
            f"linux-image-generic (= {version})",
            (
                f"linux-image-generic (= {version}), "
                "linux-headers-generic (= 6.8.0-137.137)"
            ),
            (
                f"linux-image-generic (= {version}), "
                f"linux-headers-generic (>= {version})"
            ),
            (
                f"linux-image-generic (= {version}), "
                f"linux-headers-generic (= {version}) | linux-headers-virtual"
            ),
        )
        for depends in invalid_depends:
            with (
                self.subTest(depends=depends),
                self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "depend exactly"
                ),
            ):
                offline_repo._validate_family_dependencies(
                    (family,), [{**valid[0], "depends": depends}]
                )

    def test_download_closure_pins_roots_and_complete_family_at_one_version(
        self,
    ) -> None:
        plan = offline_repo.PackagePlan(
            roots=("root-package",),
            compatibility_families=(
                {
                    "id": "systemd-noble",
                    "members": ("systemd", "systemd-sysv"),
                    "version_policy": "single-candidate-version",
                },
            ),
            matrix={},
            policy={},
        )
        candidates = {
            "root-package": "1.0",
            "systemd": "255.4-1ubuntu8.17",
            "systemd-sysv": "255.4-1ubuntu8.17",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)

            def run(command: list[str], **_: object) -> mock.Mock:
                archives_arg = next(
                    item for item in command if item.startswith("Dir::Cache::archives=")
                )
                archives = pathlib.Path(archives_arg.split("=", 1)[1])
                for package in candidates:
                    (archives / f"{package}.deb").write_bytes(package.encode())
                return mock.Mock(stdout="", stderr="", returncode=0)

            def fields(path: pathlib.Path) -> dict[str, str]:
                package = path.stem
                return {
                    "Package": package,
                    "Version": candidates[package],
                    "Architecture": "amd64",
                }

            with (
                mock.patch.object(
                    offline_repo,
                    "_candidate",
                    side_effect=lambda name: candidates[name],
                ),
                mock.patch.object(offline_repo, "_run", side_effect=run) as apt_run,
                mock.patch.object(offline_repo, "_deb_fields", side_effect=fields),
            ):
                roots, families, debs = offline_repo._download_closure(plan, root)

        argv = apt_run.call_args.args[0]
        self.assertEqual(roots, {"root-package": "1.0"})
        self.assertEqual(
            families["systemd-noble"],
            {
                "systemd": "255.4-1ubuntu8.17",
                "systemd-sysv": "255.4-1ubuntu8.17",
            },
        )
        self.assertEqual(len(debs), 3)
        self.assertEqual(
            argv[-4:],
            [
                "install",
                "root-package=1.0",
                "systemd=255.4-1ubuntu8.17",
                "systemd-sysv=255.4-1ubuntu8.17",
            ],
        )

    def test_download_closure_rejects_family_version_mismatch_and_omission(
        self,
    ) -> None:
        plan = offline_repo.PackagePlan(
            roots=("root-package",),
            compatibility_families=(
                {
                    "id": "systemd-noble",
                    "members": ("systemd", "systemd-sysv"),
                    "version_policy": "single-candidate-version",
                },
            ),
            matrix={},
            policy={},
        )
        with (
            mock.patch.object(
                offline_repo,
                "_candidate",
                side_effect=lambda name: {
                    "root-package": "1.0",
                    "systemd": "8.17",
                    "systemd-sysv": "8.12",
                }[name],
            ),
            tempfile.TemporaryDirectory() as temporary,
            self.assertRaisesRegex(
                offline_repo.OfflineRepositoryError, "candidate versions differ"
            ),
        ):
            offline_repo._download_closure(plan, pathlib.Path(temporary))

        candidates = {
            "root-package": "1.0",
            "systemd": "8.17",
            "systemd-sysv": "8.17",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)

            def run(command: list[str], **_: object) -> mock.Mock:
                archives = pathlib.Path(
                    next(
                        item
                        for item in command
                        if item.startswith("Dir::Cache::archives=")
                    ).split("=", 1)[1]
                )
                for package in ("root-package", "systemd"):
                    (archives / f"{package}.deb").write_bytes(package.encode())
                return mock.Mock(stdout="", stderr="", returncode=0)

            with (
                mock.patch.object(
                    offline_repo,
                    "_candidate",
                    side_effect=lambda name: candidates[name],
                ),
                mock.patch.object(offline_repo, "_run", side_effect=run),
                mock.patch.object(
                    offline_repo,
                    "_deb_fields",
                    side_effect=lambda path: {
                        "Package": path.stem,
                        "Version": candidates[path.stem],
                        "Architecture": "amd64",
                    },
                ),
                self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError,
                    "omitted required exact inputs: systemd-sysv",
                ),
            ):
                offline_repo._download_closure(plan, root)

    def test_download_closure_rejects_duplicate_binary_identity(self) -> None:
        plan = offline_repo.PackagePlan(
            roots=("systemd",),
            compatibility_families=(),
            matrix={},
            policy={},
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)

            def run(command: list[str], **_: object) -> mock.Mock:
                archives = pathlib.Path(
                    next(
                        item
                        for item in command
                        if item.startswith("Dir::Cache::archives=")
                    ).split("=", 1)[1]
                )
                (archives / "one.deb").write_bytes(b"one")
                (archives / "two.deb").write_bytes(b"two")
                return mock.Mock(stdout="", stderr="", returncode=0)

            with (
                mock.patch.object(offline_repo, "_candidate", return_value="8.17"),
                mock.patch.object(offline_repo, "_run", side_effect=run),
                mock.patch.object(
                    offline_repo,
                    "_deb_fields",
                    return_value={
                        "Package": "systemd",
                        "Version": "8.17",
                        "Architecture": "amd64",
                    },
                ),
                self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "duplicate binary identity"
                ),
            ):
                offline_repo._download_closure(plan, root)

    def test_owner_workbook_aliases_and_superseded_container_rows_are_explicit(
        self,
    ) -> None:
        matrix = offline_repo.build_plan().matrix
        self.assertEqual(
            matrix["owner_workbook"]["sha256"],
            "438991f1a7def5de709beea6337780baf50e2fd5e50f3a9229ef858d8186ed4c",
        )
        self.assertEqual(matrix["command_aliases"]["dd"], "coreutils")
        self.assertEqual(matrix["command_aliases"]["shred"], "coreutils")
        self.assertEqual(matrix["command_aliases"]["wipefs"], "util-linux")
        self.assertEqual(matrix["command_aliases"]["dstat"], "pcp")
        intake = json.loads(
            (ROOT / "packaging" / "offline" / "owner-workbook-intake.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual([item["row"] for item in intake["rows"]], list(range(4, 51)))
        dispositions = {item["candidate"]: item for item in matrix["candidates"]}
        for candidate in (
            "docker-ce",
            "docker-ce-cli",
            "containerd.io",
            "docker-compose-plugin",
        ):
            self.assertEqual(dispositions[candidate]["disposition"], "not-supported")
            self.assertTrue(dispositions[candidate]["reason"])
        self.assertEqual(dispositions["nwipe"]["disposition"], "not-supported")

    def test_every_vendor_tool_is_manual_sidecar_not_silently_downloaded(self) -> None:
        matrix = offline_repo.build_plan().matrix
        sidecars = {
            item["candidate"]
            for item in matrix["candidates"]
            if item["disposition"] == "sidecar-manual-offline-import"
        }
        catalog = json.loads(
            (ROOT / "packaging" / "hardware" / "vendor-tools.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(sidecars, {item["id"] for item in catalog["tools"]})

    def test_autoinstall_is_explicitly_offline_and_installs_payload_before_release(
        self,
    ) -> None:
        user_data = (ROOT / "packaging" / "appliance" / "user-data").read_text(
            encoding="utf-8"
        )
        self.assertIn("fallback: offline-install", user_data)
        self.assertIn("geoip: false", user_data)
        self.assertNotRegex(user_data, r"(?m)^\s+packages:\s*$")
        payload = user_data.index("install-offline-payload.sh /target")
        release = user_data.index("hoardarr-release.tar.gz")
        self.assertLess(payload, release)
        self.assertNotIn("http://", user_data)
        self.assertNotIn("https://", user_data)

    def test_offline_installer_has_independent_service_and_storage_guards(self) -> None:
        installer = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("policy-rc.d", installer)
        self.assertIn(
            'condition_path="/dev/null/hoardarr-offline-service-guard/$guarded_unit"',
            installer,
        )
        self.assertNotIn('ln -s -- /dev/null "$destination"', installer)
        self.assertIn("AUTO -all", installer)
        self.assertIn('global_filter = [ "r|.*|" ]', installer)
        self.assertIn('devnode ".*"', installer)
        self.assertIn("--simulate --no-install-recommends", installer)
        self._assert_actual_install_contract(installer)
        self.assertIn("package-readback.json", installer)
        self.assertIn("service-policy-readback.json", installer)
        self.assertIn("service-retained-guards.json", installer)
        self.assertIn(
            "later-authorized-selection-must-verify-unit-path-inode-and-sha256",
            installer,
        )
        self.assertIn("sha256sum --check --strict SHA256SUMS", installer)
        self.assertNotIn("curl ", installer)
        self.assertNotIn("wget ", installer)
        verifier = (
            ROOT / "packaging" / "appliance" / "verify-offline-appliance.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("HOARDARR_OFFLINE_EVIDENCE_BEGIN", verifier)
        self.assertIn("list-unit-files", verifier)
        self.assertIn("127.0.0.1:7877/health/ready", verifier)

    def test_actual_install_argv_executes_exact_production_fragment(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        fragment = self._actual_install_fragment(payload)
        if sys.platform == "win32":
            candidates = (
                pathlib.Path(r"C:\Program Files\Git\bin\bash.exe"),
                pathlib.Path(r"C:\msys64\usr\bin\bash.exe"),
            )
            bash = next((str(path) for path in candidates if path.is_file()), None)
        else:
            bash = shutil.which("bash")
        self.assertIsNotNone(
            bash, "Bash is required for actual-install argv regression"
        )
        assert bash is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            fragment_path = root / "production-fragment.sh"
            log_path = root / "argv.bin"
            fragment_path.write_text(fragment + "\n", encoding="utf-8")
            script = "\n".join(
                (
                    "set -euo pipefail",
                    'target="/target"',
                    "apt_options=(",
                    '  -o "Dir::Etc::sourcelist=/etc/apt/sources.list.d/hoardarr-offline.list"',
                    '  -o "Dir::Etc::sourceparts=-"',
                    '  -o "Acquire::Languages=none"',
                    '  -o "Acquire::Retries=0"',
                    '  -o "Acquire::http::Proxy=false"',
                    '  -o "Acquire::https::Proxy=false"',
                    ")",
                    'exact_roots=("alpha=1" "beta=2")',
                    'log="$1"',
                    'chroot() { printf \'%s\\0\' "$@" >"$log"; }',
                    f'source "{fragment_path.as_posix()}"',
                )
            )
            result = subprocess.run(
                [bash, "-c", script, "bash", log_path.as_posix()],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            argv = log_path.read_bytes().split(b"\0")[:-1]
            self.assertEqual(
                [value.decode() for value in argv],
                [
                    "/target",
                    "apt-get",
                    "-o",
                    "Dir::Etc::sourcelist=/etc/apt/sources.list.d/hoardarr-offline.list",
                    "-o",
                    "Dir::Etc::sourceparts=-",
                    "-o",
                    "Acquire::Languages=none",
                    "-o",
                    "Acquire::Retries=0",
                    "-o",
                    "Acquire::http::Proxy=false",
                    "-o",
                    "Acquire::https::Proxy=false",
                    "--yes",
                    "--no-install-recommends",
                    "install",
                    "alpha=1",
                    "beta=2",
                ],
            )

    def test_actual_install_contract_rejects_safeguard_regressions(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        self._assert_actual_install_contract(payload)
        mutations = {
            "network source": payload.replace(
                "file:/opt/hoardarr/offline-repository",
                "https://example.invalid/repository",
            ),
            "missing signed-by": payload.replace("signed-by=", "keyring="),
            "signature weakening": payload.replace(
                "noble main", "noble main trusted=yes", 1
            ),
            "missing retry guard": payload.replace(
                "Acquire::Retries=0", "Acquire::Retries=1"
            ),
            "missing HTTP proxy guard": payload.replace(
                "Acquire::http::Proxy=false", "Acquire::http::Proxy=direct"
            ),
            "missing HTTPS proxy guard": payload.replace(
                "Acquire::https::Proxy=false", "Acquire::https::Proxy=direct"
            ),
            "root loss": payload.replace(
                '"${exact_roots[@]}"', '"${exact_roots[0]}"', 1
            ),
            "service guard loss": payload.replace("policy-rc.d", "policy-start.d"),
            "direct systemctl offline guard loss": payload.replace(
                "export SYSTEMD_OFFLINE=1", "export SYSTEMD_OFFLINE=0"
            ),
            "md storage guard loss": payload.replace("AUTO -all", "AUTO +all"),
            "LVM storage guard loss": payload.replace(
                'global_filter = [ "r|.*|" ]', 'global_filter = [ "a|.*|" ]'
            ),
            "multipath storage guard loss": payload.replace(
                'devnode ".*"', 'devnode "^$"'
            ),
            "no-download reintroduced": payload.replace(
                "--yes --no-install-recommends install",
                "--yes --no-download --no-install-recommends install",
            ),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name), self.assertRaises(AssertionError):
                self._assert_actual_install_contract(mutation)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux APT")
    def test_signed_local_file_repository_actual_install(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        fragment = self._actual_install_fragment(payload)
        required = ("apt-get", "dpkg-deb", "dpkg-query", "gpg", "gzip", "sudo")
        missing = [command for command in required if shutil.which(command) is None]
        self.assertEqual(missing, [], f"missing Linux integration tools: {missing}")
        sudo = subprocess.run(
            ["sudo", "-n", "true"], text=True, capture_output=True, check=False
        )
        self.assertEqual(sudo.returncode, 0, sudo.stderr)
        with tempfile.TemporaryDirectory() as temporary:
            fragment_path = pathlib.Path(temporary) / "production-fragment.sh"
            fragment_path.write_text(fragment + "\n", encoding="utf-8")
            result = subprocess.run(
                [
                    "sudo",
                    "-n",
                    "bash",
                    str(
                        ROOT / "tests" / "appliance" / "test-local-file-apt-install.sh"
                    ),
                    str(fragment_path),
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=180,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"(?m)^old_no_download_status=[1-9][0-9]*$")
        self.assertIn("archive_cache_was_empty=true", result.stdout)
        self.assertIn("actual_install_file_acquisition=true", result.stdout)
        self.assertIn("network_sources=0", result.stdout)
        self.assertIn("package_readback=installed\t1.0\tall", result.stdout)

    def test_appliance_builder_embeds_verified_repository_and_emits_complete_tree_manifest(
        self,
    ) -> None:
        builder = (ROOT / "scripts" / "build-appliance.sh").read_text(encoding="utf-8")
        self.assertIn("build-offline-apt-repository.py verify", builder)
        self.assertIn("/hoardarr/offline-repository", builder)
        self.assertIn("hoardarr/install-offline-payload.sh", builder)
        self.assertIn("offline repository contains a symbolic link", builder)
        self.assertIn('>"${output}.tree-sha256"', builder)

    def test_two_clean_no_nic_install_passes_are_manual_release_gates(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "appliance.yml").read_text(
            encoding="utf-8"
        )
        harness = (ROOT / "tests" / "appliance" / "run-offline-iso-pass.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("offline_validation_mode:", workflow)
        self.assertIn("default: two-pass", workflow)
        self.assertIn("- diagnostic-pass-1", workflow)
        self.assertIn(
            "inputs.offline_validation_mode == 'diagnostic-pass-1'"
            ' && \'["pass-1"]\' || \'["pass-1","pass-2"]\'',
            workflow,
        )
        self.assertIn("HOARDARR_OFFLINE_DIAGNOSTIC_MODE", workflow)
        self.assertIn(
            "'hoardarr-offline-diagnostic-pass-1'"
            " || format('hoardarr-offline-{0}', matrix.pass)",
            workflow,
        )
        retention = workflow.split(
            "- name: Retain offline install inputs for no-network validation", 1
        )[1].split("\n\n  offline-install:", 1)[0]
        self.assertNotIn("if:", retention)
        self.assertIn("name: hoardarr-offline-install-inputs", retention)
        self.assertIn("compression-level: 0", retention)
        self.assertIn("retention-days: 3", retention)
        self.assertEqual(
            [
                line.strip()
                for line in retention.splitlines()
                if line.strip().startswith("dist/")
            ],
            ["dist/hoardarr-release.tar.gz", "dist/offline-repository"],
        )
        offline_install = workflow.split("\n  offline-install:\n", 1)[1]
        self.assertTrue(
            offline_install.startswith(
                "    if: github.event_name == 'workflow_dispatch'\n"
            )
        )
        self.assertEqual(workflow.count("\n  offline-install:\n"), 1)
        self.assertEqual(
            offline_install.count(
                "inputs.offline_validation_mode == 'diagnostic-pass-1'"
            ),
            3,
        )
        self.assertIn("$RUNNER_TEMP/ci-signing-key", workflow)
        self.assertIn("$RUNNER_TEMP/ubuntu-vulnerability-status.json", workflow)
        self.assertIn("-nic none", harness)
        self.assertIn("readonly=on", harness)
        self.assertIn("protected-before.sha256", harness)
        self.assertIn("protected-after.sha256", harness)
        self.assertIn("HOARDARR_OFFLINE_READY", harness)
        self.assertIn("installer-monitor.log", harness)
        self.assertIn("installer-process.tsv", harness)
        self.assertIn("process-identities.txt", harness)
        self.assertIn("qemu-installer-stderr.log", harness)
        self.assertIn("qemu-img-info.json", harness)
        self.assertIn("installer_timeout", harness)
        self.assertIn("timeout --signal=TERM --kill-after=30s 2700s", harness)
        self.assertIn('"acceptance_eligible": False', harness)
        self.assertIn('"bounded_runner_exit_status"', harness)
        self.assertIn("protected-diff.txt", harness)
        self.assertIn("frames/SHA256SUMS", harness)
        self.assertIn("evidence-finalization.txt", harness)
        self.assertIn("diagnostic evidence finalization was incomplete", harness)
        diagnostic_body = harness.split("run_diagnostic_installer() {", 1)[1].split(
            "\n}\n\ninstall_start=", 1
        )[0]
        self.assertLess(
            diagnostic_body.index('wait "$runner_pid"'),
            diagnostic_body.index("finalize_diagnostic_evidence"),
        )
        self.assertIn("timeout --signal=TERM --kill-after=30s 45m", harness)
        success_path = harness.split('if [[ "$diagnostic_mode" == true ]]; then', 2)[2]
        self.assertLess(
            success_path.index("write_diagnostic_metadata installer_reboot_checkpoint"),
            success_path.index("finalize_diagnostic_evidence"),
        )
        self.assertNotIn('cat >"$output/run.json"', success_path.split("else", 1)[0])

    def test_ci_payload_wrapper_preserves_exact_argv_and_status(self) -> None:
        user_data = (ROOT / "tests" / "appliance" / "offline-user-data").read_text(
            encoding="utf-8"
        )
        exact_argv = (
            "/cdrom/hoardarr/install-offline-payload.sh "
            "/target /cdrom/hoardarr/offline-repository"
        )
        self.assertEqual(user_data.count(exact_argv), 1)
        self.assertIn('pipeline_status=("${PIPESTATUS[@]}")', user_data)
        self.assertIn('payload_status="${pipeline_status[0]}"', user_data)
        self.assertIn(
            '[[ "${pipeline_status[1]}" -eq 0 ]] || capture_ok=false', user_data
        )
        self.assertIn('exit "$payload_status"', user_data)
        payload_tail = user_data.split('pipeline_status=("${PIPESTATUS[@]}")', 1)[1]
        self.assertNotIn("set -e", payload_tail.split('exit "$payload_status"', 1)[0])
        self.assertIn("/target/var/log/hoardarr-offline-payload.log", user_data)
        self.assertIn("[[ -c /dev/ttyS0 && -w /dev/ttyS0 ]]", user_data)
        self.assertIn("stty -F /dev/ttyS0 -opost || exit 126", user_data)
        self.assertIn(
            "stty -F /dev/ttyS0 -a | grep -qw -- -opost || exit 127", user_data
        )
        self.assertNotIn("|| true", payload_tail.split('exit "$payload_status"', 1)[0])
        for required_operation in (
            "emit_both HOARDARR_OFFLINE_PAYLOAD_END || capture_ok=false",
            'emit_both "HOARDARR_OFFLINE_PAYLOAD_EXIT=$payload_status" || capture_ok=false',
            'sync "$target_log" || capture_ok=false',
            'target_size="$(wc -c <"$target_log")" || capture_ok=false',
            'target_sha256="$(sha256sum "$target_log" | cut -d" " -f1)" || capture_ok=false',
        ):
            self.assertIn(required_operation, user_data)
        complete_guard = user_data.rsplit(
            'printf "%s\\n" HOARDARR_OFFLINE_PAYLOAD_CAPTURE_COMPLETE', 1
        )[0].rsplit('if [[ "$capture_ok" == true ]]', 1)
        self.assertEqual(len(complete_guard), 2)
        for marker in (
            "HOARDARR_OFFLINE_PAYLOAD_BEGIN",
            "HOARDARR_OFFLINE_PAYLOAD_END",
            "HOARDARR_OFFLINE_PAYLOAD_EXIT=",
            "HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE=",
            "HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SHA256=",
            "HOARDARR_OFFLINE_PAYLOAD_CAPTURE_COMPLETE",
        ):
            self.assertIn(marker, user_data)

    def test_payload_capture_parser_is_fail_closed(self) -> None:
        parser = ROOT / "tests" / "appliance" / "parse-offline-payload-capture.py"

        def run_capture(
            serial: bytes,
        ) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            root = pathlib.Path(temporary.name)
            serial_path = root / "serial.log"
            serial_path.write_bytes(serial)
            result = subprocess.run(
                [
                    sys.executable,
                    str(parser),
                    str(serial_path),
                    str(root / "console.log"),
                    str(root / "target.log"),
                    str(root / "capture.json"),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            return result, root

        def complete(status: int, payload: bytes = b"decisive failure\n") -> bytes:
            target = (
                b"HOARDARR_OFFLINE_PAYLOAD_BEGIN\n"
                + payload
                + b"HOARDARR_OFFLINE_PAYLOAD_END\n"
                + f"HOARDARR_OFFLINE_PAYLOAD_EXIT={status}\n".encode()
            )
            return (
                b"prefix\n"
                + target
                + f"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE={len(target)}\n".encode()
                + b"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SHA256="
                + hashlib.sha256(target).hexdigest().encode()
                + b"\nHOARDARR_OFFLINE_PAYLOAD_CAPTURE_COMPLETE\n"
            )

        nonzero, nonzero_root = run_capture(complete(17))
        self.assertEqual(nonzero.returncode, 10)
        self.assertEqual(
            json.loads((nonzero_root / "capture.json").read_text())["payload_status"],
            17,
        )
        self.assertIn(b"decisive failure", (nonzero_root / "target.log").read_bytes())

        zero, _ = run_capture(complete(0))
        self.assertEqual(zero.returncode, 0)
        crlf_stream = complete(17).replace(b"\n", b"\r\n")
        crlf, crlf_root = run_capture(crlf_stream)
        self.assertEqual(crlf.returncode, 10)
        crlf_metadata = json.loads((crlf_root / "capture.json").read_text())
        self.assertEqual(crlf_metadata["serial_transform"], "onlcr_crlf")
        expected_target = (
            complete(17)
            .split(b"prefix\n", 1)[1]
            .split(b"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE=", 1)[0]
        )
        self.assertEqual((crlf_root / "target.log").read_bytes(), expected_target)
        self.assertEqual(
            crlf_metadata["target_log_sha256"],
            hashlib.sha256(expected_target).hexdigest(),
        )
        absent, _ = run_capture(b"")
        self.assertEqual(absent.returncode, 20)
        partial, _ = run_capture(b"HOARDARR_OFFLINE_PAYLOAD_BEGIN\n")
        self.assertEqual(partial.returncode, 20)
        malformed_bytes = complete(1).replace(
            b"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE=",
            b"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE=999",
        )
        malformed, malformed_root = run_capture(malformed_bytes)
        self.assertEqual(malformed.returncode, 21)
        self.assertFalse((malformed_root / "capture.json").exists())
        duplicate, _ = run_capture(
            complete(1).replace(
                b"HOARDARR_OFFLINE_PAYLOAD_BEGIN\n",
                b"HOARDARR_OFFLINE_PAYLOAD_BEGIN\nHOARDARR_OFFLINE_PAYLOAD_BEGIN\n",
            )
        )
        self.assertEqual(duplicate.returncode, 21)
        duplicate_complete, _ = run_capture(
            complete(1) + b"HOARDARR_OFFLINE_PAYLOAD_CAPTURE_COMPLETE\n"
        )
        self.assertEqual(duplicate_complete.returncode, 21)
        malformed_then_complete, malformed_then_root = run_capture(
            malformed_bytes + complete(1)
        )
        self.assertEqual(malformed_then_complete.returncode, 21)
        self.assertFalse((malformed_then_root / "capture.json").exists())
        arbitrary_cr, arbitrary_cr_root = run_capture(
            complete(1).replace(b"decisive failure", b"decisive\rfailure")
        )
        self.assertEqual(arbitrary_cr.returncode, 21)
        self.assertFalse((arbitrary_cr_root / "capture.json").exists())

    def test_diagnostic_early_stop_requires_valid_nonzero_capture(self) -> None:
        harness = (ROOT / "tests" / "appliance" / "run-offline-iso-pass.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("if (( payload_parser_status == 10 )); then", harness)
        self.assertIn("payload_failure_observed=true\n            sleep 5", harness)
        self.assertIn("for _ in {1..3}; do", harness)
        self.assertIn('if [[ "$payload_failure_observed" == true ]]', harness)
        self.assertIn("offline_payload_failure_observed", harness)
        self.assertIn("offline_payload_capture_invalid", harness)
        self.assertIn(
            'if [[ "$payload_failure_observed" == true && "$payload_capture_invalid" != true ]]',
            harness,
        )
        self.assertNotIn(
            "payload_parser_status == 0 )); then\n            payload_failure_observed",
            harness,
        )
        parser_guard = harness.split('if [[ "$diagnostic_mode" == true ]]; then', 1)[1]
        self.assertIn('payload_capture_parser="$script_root/', parser_guard)
        self.assertNotIn(
            "parse-offline-payload-capture.py", harness.split(parser_guard, 1)[0]
        )

    def test_two_compatibility_families_emit_deterministic_verifiable_evidence(
        self,
    ) -> None:
        required = (
            "dists/noble/InRelease",
            "dists/noble/Release",
            "dists/noble/Release.gpg",
            "dists/noble/main/binary-amd64/Packages.gz",
            "evidence/SBOM.cdx.json",
            "evidence/provenance.json",
            "evidence/root-package-versions.txt",
            "evidence/vulnerability-status.json",
            "hoardarr-offline-archive-keyring.gpg",
        )
        version = "candidate-version-from-apt"
        families = (
            {
                "id": "systemd-noble",
                "members": ("udev", "systemd-dev"),
                "version_policy": "single-candidate-version",
                "exact_dependencies": {},
            },
            {
                "id": "linux-meta-noble",
                "members": (
                    "linux-generic",
                    "linux-image-generic",
                    "linux-headers-generic",
                ),
                "version_policy": "single-candidate-version",
                "exact_dependencies": {
                    "linux-generic": (
                        "linux-image-generic",
                        "linux-headers-generic",
                    )
                },
            },
        )
        declarations = [
            {
                "id": "systemd-noble",
                "members": ["udev", "systemd-dev"],
                "version_policy": "single-candidate-version",
            },
            {
                "id": "linux-meta-noble",
                "members": [
                    "linux-generic",
                    "linux-image-generic",
                    "linux-headers-generic",
                ],
                "version_policy": "single-candidate-version",
                "exact_dependencies": {
                    "linux-generic": [
                        "linux-image-generic",
                        "linux-headers-generic",
                    ]
                },
            },
        ]
        plan = offline_repo.PackagePlan(
            roots=("linux-image-generic",),
            compatibility_families=families,
            matrix={"compatibility_families": declarations},
            policy={},
        )
        dependency = (
            f"linux-image-generic (= {version}), linux-headers-generic (= {version})"
        )
        records = [
            {"name": "udev", "version": version, "architecture": "amd64"},
            {"name": "systemd-dev", "version": version, "architecture": "all"},
            {
                "name": "linux-generic",
                "version": version,
                "architecture": "amd64",
                "declared_dependencies": {"depends": dependency},
            },
            {
                "name": "linux-image-generic",
                "version": version,
                "architecture": "amd64",
            },
            {
                "name": "linux-headers-generic",
                "version": version,
                "architecture": "amd64",
            },
        ]
        family_versions = {
            family["id"]: {member: version for member in family["members"]}
            for family in families
        }
        evidence = offline_repo._resolved_family_evidence(
            plan, family_versions, records
        )
        self.assertEqual(
            evidence,
            offline_repo._resolved_family_evidence(plan, family_versions, records),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for relative in required:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative, encoding="utf-8")
            packages = "".join(
                f"Package: {record['name']}\nVersion: {version}\n"
                f"Architecture: {record['architecture']}\n\n"
                for record in records
            )
            (root / "dists/noble/main/binary-amd64/Packages").write_text(
                packages, encoding="utf-8"
            )
            (root / "evidence/package-manifest.json").write_text(
                json.dumps({"schema_version": 1, "packages": records}),
                encoding="utf-8",
            )
            (root / "evidence/compatibility-matrix.json").write_text(
                json.dumps(plan.matrix), encoding="utf-8"
            )
            (root / "evidence/compatibility-families.json").write_text(
                json.dumps(evidence), encoding="utf-8"
            )
            offline_repo._write_tree_manifest(root)
            with mock.patch.object(offline_repo.shutil, "which", return_value=None):
                offline_repo.verify_repository(root)
                records[2]["declared_dependencies"]["depends"] = (
                    f"linux-image-generic (= {version}) | linux-image-virtual, "
                    f"linux-headers-generic (= {version})"
                )
                (root / "evidence/package-manifest.json").write_text(
                    json.dumps({"schema_version": 1, "packages": records}),
                    encoding="utf-8",
                )
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "depend exactly"
                ):
                    offline_repo.verify_repository(root)

    def test_repository_tree_verification_rejects_tampering(self) -> None:
        required = (
            "dists/noble/InRelease",
            "dists/noble/Release",
            "dists/noble/Release.gpg",
            "dists/noble/main/binary-amd64/Packages",
            "dists/noble/main/binary-amd64/Packages.gz",
            "evidence/SBOM.cdx.json",
            "evidence/compatibility-matrix.json",
            "evidence/compatibility-families.json",
            "evidence/package-manifest.json",
            "evidence/provenance.json",
            "evidence/root-package-versions.txt",
            "evidence/vulnerability-status.json",
            "hoardarr-offline-archive-keyring.gpg",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for relative in required:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative, encoding="utf-8")
            (root / "evidence" / "package-manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "packages": [
                            {
                                "name": "udev",
                                "version": "8.17",
                                "architecture": "amd64",
                            },
                            {
                                "name": "systemd-dev",
                                "version": "8.17",
                                "architecture": "all",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            nonalphabetical_plan = offline_repo.PackagePlan(
                roots=(),
                compatibility_families=(
                    {
                        "id": "systemd-noble",
                        "members": ("udev", "systemd-dev"),
                        "version_policy": "single-candidate-version",
                    },
                ),
                matrix={},
                policy={},
            )
            generated_family_evidence = offline_repo._resolved_family_evidence(
                nonalphabetical_plan,
                {"systemd-noble": {"udev": "8.17", "systemd-dev": "8.17"}},
                [
                    {"name": "udev", "version": "8.17", "architecture": "amd64"},
                    {
                        "name": "systemd-dev",
                        "version": "8.17",
                        "architecture": "all",
                    },
                ],
            )
            (root / "evidence" / "compatibility-families.json").write_text(
                json.dumps(generated_family_evidence),
                encoding="utf-8",
            )
            (root / "evidence" / "compatibility-matrix.json").write_text(
                json.dumps(
                    {
                        "compatibility_families": [
                            {
                                "id": "systemd-noble",
                                "members": ["udev", "systemd-dev"],
                                "version_policy": "single-candidate-version",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "dists/noble/main/binary-amd64/Packages").write_text(
                "Package: udev\nVersion: 8.17\nArchitecture: amd64\n\n"
                "Package: systemd-dev\nVersion: 8.17\nArchitecture: all\n\n",
                encoding="utf-8",
            )
            offline_repo._write_tree_manifest(root)
            with mock.patch.object(offline_repo.shutil, "which", return_value=None):
                offline_repo.verify_repository(root)
                family_path = root / "evidence" / "compatibility-families.json"
                family_document = json.loads(family_path.read_text(encoding="utf-8"))
                family_document["schema_version"] = 2
                family_path.write_text(json.dumps(family_document), encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "evidence schema"
                ):
                    offline_repo.verify_repository(root)
                family_document["schema_version"] = 1
                family_path.write_text(json.dumps(family_document), encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                offline_repo.verify_repository(root)
                family_document["families"][0]["members"][0] = "udev=8.17"
                family_path.write_text(json.dumps(family_document), encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError,
                    "incomplete or incoherent",
                ):
                    offline_repo.verify_repository(root)
                family_document["families"][0]["members"][0] = {
                    "name": "udev",
                    "version": "8.17",
                }
                family_path.write_text(json.dumps(family_document), encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                offline_repo.verify_repository(root)
                package_manifest_path = root / "evidence" / "package-manifest.json"
                package_document = json.loads(
                    package_manifest_path.read_text(encoding="utf-8")
                )
                package_document["packages"] = package_document["packages"][1:]
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "incomplete or incoherent"
                ):
                    offline_repo.verify_repository(root)
                package_document["packages"].insert(
                    0,
                    {"name": "udev", "version": "8.17", "architecture": "amd64"},
                )
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                packages_path = root / "dists/noble/main/binary-amd64/Packages"
                packages_document = packages_path.read_text(encoding="utf-8")
                package_document["packages"].append(
                    {
                        "name": "systemd-dev",
                        "version": "8.17",
                        "architecture": "amd64",
                    }
                )
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "incomplete or incoherent"
                ):
                    offline_repo.verify_repository(root)
                package_document["packages"].pop()
                package_document["packages"][1]["architecture"] = "i386"
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                packages_path.write_text(
                    packages_document.replace(
                        "Architecture: all", "Architecture: i386"
                    ),
                    encoding="utf-8",
                )
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "incomplete or incoherent"
                ):
                    offline_repo.verify_repository(root)
                package_document["packages"][1]["architecture"] = "all"
                package_document["packages"].append(
                    dict(package_document["packages"][1])
                )
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                packages_path.write_text(packages_document, encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "invalid binary identities"
                ):
                    offline_repo.verify_repository(root)
                package_document["packages"].pop()
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                packages_path.write_text(
                    packages_document.split("\n\n", 1)[1], encoding="utf-8"
                )
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "incomplete or incoherent"
                ):
                    offline_repo.verify_repository(root)
                packages_path.write_text(packages_document, encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                offline_repo.verify_repository(root)
                (root / "evidence" / "package-manifest.json").write_text(
                    "tampered", encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "digest mismatch"
                ):
                    offline_repo.verify_repository(root)

    def test_deferred_release_install_does_not_enable_lldpd(self) -> None:
        installer = (ROOT / "scripts" / "install-release-bundle.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("systemctl disable lldpd.service", installer)
        deferred = installer.split('if [[ "${DEFER_SERVICE_START}" == "true" ]]', 1)[1]
        self.assertNotIn("systemctl enable lldpd.service", deferred.split("else", 1)[0])

    def test_release_bundle_emits_dependency_sbom_license_and_provenance_evidence(
        self,
    ) -> None:
        builder = (ROOT / "scripts" / "build-release-bundle.py").read_text(
            encoding="utf-8"
        )
        installer = (ROOT / "scripts" / "install-release-bundle.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('staging / "evidence" / "SBOM.cdx.json"', builder)
        self.assertIn('staging / "evidence" / "python-licenses.json"', builder)
        self.assertIn('staging / "evidence" / "npm-licenses.json"', builder)
        self.assertIn('staging / "evidence" / "provenance.json"', builder)
        self.assertIn('"evidence/SBOM.cdx.json"', installer)
        self.assertIn('"evidence/vulnerability-status.json"', installer)


if __name__ == "__main__":
    unittest.main()
