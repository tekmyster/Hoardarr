#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "usage: $0 TARGET_ROOT ISO_REPOSITORY" >&2
    exit 2
}

internal_namespace_mode=false
parent_namespace_fd=
if [[ $# -eq 4 && "$1" == --hoardarr-private-mount-namespace ]]; then
    internal_namespace_mode=true
    parent_namespace_fd="$2"
    shift 2
fi
[[ $# -eq 2 ]] || usage
target="$(realpath -- "$1")"
source_repo="$(realpath -- "$2")"
script_path="$(realpath -- "$0")"
[[ -f "$script_path" && ! -L "$script_path" ]] || {
    echo "offline payload must be a real regular file" >&2
    exit 1
}
for namespace_command in unshare mount umount readlink python3; do
    command -v "$namespace_command" >/dev/null || {
        echo "offline payload requires $namespace_command" >&2
        exit 1
    }
done
unset HOARDARR_OFFLINE_PRIVATE_MOUNT_NAMESPACE HOARDARR_OFFLINE_PARENT_MOUNT_NAMESPACE
if [[ "$internal_namespace_mode" != true ]]; then
    exec {parent_namespace_fd}< /proc/self/ns/mnt || {
        echo "offline payload cannot retain its parent mount namespace" >&2
        exit 1
    }
    exec unshare --mount --propagation private -- \
        "$script_path" --hoardarr-private-mount-namespace \
        "$parent_namespace_fd" "$target" "$source_repo"
fi
[[ "$parent_namespace_fd" =~ ^[1-9][0-9]*$ && \
    -e "/proc/self/fd/$parent_namespace_fd" ]] || {
    echo "offline payload parent mount namespace descriptor is invalid" >&2
    exit 1
}
parent_mount_namespace="$(readlink -- "/proc/self/fd/$parent_namespace_fd")" || {
    echo "offline payload cannot identify its parent mount namespace" >&2
    exit 1
}
current_mount_namespace="$(readlink -- /proc/self/ns/mnt)" || {
    echo "offline payload cannot identify its private mount namespace" >&2
    exit 1
}
[[ "$parent_mount_namespace" =~ ^mnt:\[[0-9]+\]$ && \
    "$current_mount_namespace" =~ ^mnt:\[[0-9]+\]$ && \
    "$current_mount_namespace" != "$parent_mount_namespace" ]] || {
    echo "offline payload mount namespace isolation is not proven" >&2
    exit 1
}
python3 - <<'PY'
import re


def decode(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


roots = []
with open("/proc/self/mountinfo", encoding="utf-8") as stream:
    for line in stream:
        fields = line.rstrip("\n").split(" ")
        separator = fields.index("-")
        if decode(fields[4]) == "/":
            roots.append(fields[6:separator])
if len(roots) != 1 or any(
    value.startswith(("shared:", "master:", "propagate_from:"))
    for value in roots[0]
):
    raise SystemExit("offline payload root mount propagation is not private")
PY
exec {parent_namespace_fd}<&-
[[ "$target" == /target && -d "$target" && ! -L "$target" ]] || {
    echo "offline payload target must be the real /target directory" >&2
    exit 1
}
[[ -d "$source_repo" && ! -L "$source_repo" ]] || {
    echo "ISO-local repository must be a real directory" >&2
    exit 1
}
for required in \
    SHA256SUMS \
    dists/noble/InRelease \
    evidence/compatibility-matrix.json \
    evidence/package-manifest.json \
    evidence/root-package-versions.txt \
    hoardarr-offline-archive-keyring.gpg
do
    [[ -f "$source_repo/$required" && ! -L "$source_repo/$required" ]] || {
        echo "ISO-local repository is missing $required" >&2
        exit 1
    }
done
(cd "$source_repo" && sha256sum --check --strict SHA256SUMS)
find "$source_repo" -type l -print -quit | grep -q . && {
    echo "ISO-local repository contains a symbolic link" >&2
    exit 1
}

install_root="$target/opt/hoardarr-install"
retained_repo="$target/opt/hoardarr/offline-repository"
state_root="$target/var/lib/hoardarr-install"
runtime_mount_record="$state_root/runtime-mounts.tsv"
runtime_cleanup_record="$state_root/runtime-mount-cleanup.tsv"
install -d -m 0755 "$install_root" "$target/opt/hoardarr" "$state_root"
[[ ! -e "$retained_repo" ]] || {
    echo "retained offline repository destination already exists" >&2
    exit 1
}
cp -a -- "$source_repo" "$retained_repo"
(cd "$retained_repo" && sha256sum --check --strict SHA256SUMS)

keyring="$target/usr/share/keyrings/hoardarr-offline-archive-keyring.gpg"
install -D -m 0644 \
    "$retained_repo/hoardarr-offline-archive-keyring.gpg" "$keyring"
source_list="$target/etc/apt/sources.list.d/hoardarr-offline.list"
install -d -m 0755 "$target/etc/apt/sources.list.d"
printf '%s\n' \
    'deb [signed-by=/usr/share/keyrings/hoardarr-offline-archive-keyring.gpg] file:/opt/hoardarr/offline-repository noble main' \
    >"$source_list"
chmod 0644 "$source_list"

# A production appliance update is an explicit, signed Hoardarr operation. Keep
# inherited Internet sources recoverable but disabled so repair remains offline.
while IFS= read -r -d '' online_source; do
    [[ "$online_source" == "$source_list" ]] && continue
    case "$online_source" in
        *.hoardarr-online-disabled) continue ;;
    esac
    mv -- "$online_source" "${online_source}.hoardarr-online-disabled"
done < <(find "$target/etc/apt" -maxdepth 2 -type f \( -name '*.list' -o -name '*.sources' \) -print0)
install -D -m 0644 /dev/stdin "$target/etc/apt/apt.conf.d/90hoardarr-offline" <<'EOF'
APT::Periodic::Enable "0";
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Unattended-Upgrade "0";
Acquire::Retries "0";
EOF

# Runtime mounts are created only inside the private mount namespace above.
# /proc/self/mountinfo is the kernel interface used for both preflight and
# identity-aware cleanup; human-formatted mount output is never parsed.
runtime_mount_paths=(proc sys dev dev/pts run)
runtime_mount_sources=(/proc /sys /dev /dev/pts /run)
created_runtime_mounts=()
declare -A runtime_mount_ids=()
declare -A runtime_mount_records=()
mountinfo_exact_record() {
    local path="$1"
    python3 - "$path" <<'PY'
import re
import sys

expected = sys.argv[1]


def decode(value: str) -> str:
    return re.sub(
        r"\\([0-7]{3})",
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


matches = []
with open("/proc/self/mountinfo", encoding="utf-8") as stream:
    for raw_line in stream:
        fields = raw_line.rstrip("\n").split(" ")
        separator = fields.index("-")
        mountpoint = decode(fields[4])
        if mountpoint != expected:
            continue
        matches.append(
            (
                fields[0],
                fields[1],
                fields[2],
                decode(fields[3]),
                mountpoint,
                fields[5],
                ",".join(fields[6:separator]),
                fields[separator + 1],
                decode(fields[separator + 2]),
                fields[separator + 3],
            )
        )
if len(matches) > 1:
    raise SystemExit("multiple exact mountinfo records")
if matches:
    print("\x1f".join(matches[0]))
PY
}
runtime_path_is_safe() {
    local destination="$1"
    local expected="$2"
    local resolved
    [[ "$destination" == "$expected" && -d "$destination" && ! -L "$destination" ]] || {
        echo "offline runtime mount path is not a real directory: $expected" >&2
        return 1
    }
    resolved="$(realpath -e -- "$destination")" || return 1
    [[ "$resolved" == "$expected" && "$resolved" == "$target"/* ]] || {
        echo "offline runtime mount path escapes the target: $expected" >&2
        return 1
    }
}
runtime_record_field() {
    local record="$1"
    local field="$2"
    local values=()
    IFS=$'\x1f' read -r -a values <<<"$record"
    [[ "${#values[@]}" -eq 10 && "$field" -ge 0 && "$field" -lt 10 ]] || return 1
    printf '%s\n' "${values[$field]}"
}
prepare_runtime_mounts_failure() {
    local failure_status="${1:-1}"
    [[ "$failure_status" =~ ^[1-9][0-9]*$ && "$failure_status" -le 255 ]] || \
        failure_status=1
    if ! cleanup_runtime_mounts; then
        echo "offline runtime mount preparation cleanup failed" >&2
    fi
    return "$failure_status"
}
runtime_mount_matches_source() {
    local target_record="$1"
    local source_record="$2"
    local expected_destination="$3"
    local mount_id source_root source_type source_name
    local target_root target_path target_type target_source
    mount_id="$(runtime_record_field "$target_record" 0)" || return 2
    source_root="$(runtime_record_field "$source_record" 3)" || return 2
    source_type="$(runtime_record_field "$source_record" 7)" || return 2
    source_name="$(runtime_record_field "$source_record" 8)" || return 2
    target_root="$(runtime_record_field "$target_record" 3)" || return 2
    target_path="$(runtime_record_field "$target_record" 4)" || return 2
    target_type="$(runtime_record_field "$target_record" 7)" || return 2
    target_source="$(runtime_record_field "$target_record" 8)" || return 2
    [[ "$mount_id" =~ ^[1-9][0-9]*$ && \
        "$target_path" == "$expected_destination" && \
        "$target_root" == "$source_root" && \
        "$target_type" == "$source_type" && \
        "$target_source" == "$source_name" ]]
}
runtime_mount_path_is_absent() {
    local destination="$1"
    python3 - "$destination" <<'PY'
import re
import sys

expected = sys.argv[1]


def decode(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


with open("/proc/self/mountinfo", encoding="utf-8") as stream:
    matches = [
        line
        for line in stream
        if decode(line.rstrip("\n").split(" ")[4]) == expected
    ]
raise SystemExit(0 if not matches else 1)
PY
}
rollback_just_attempted_runtime_mount() {
    local destination="$1"
    local unmount_status=0
    runtime_path_is_safe "$destination" "$destination" || return 1
    if umount -- "$destination"; then
        unmount_status=0
    else
        unmount_status=$?
    fi
    if runtime_mount_path_is_absent "$destination"; then
        return 0
    fi
    echo "offline just-attempted runtime mount rollback failed (umount status $unmount_status): $destination" >&2
    return 1
}
prepare_runtime_mounts() {
    local index relative source destination source_record target_record existing_record
    local target_optional target_path mount_id current_id bind_status propagation_status match_status
    local source_records=()
    (( ${#runtime_mount_paths[@]} == ${#runtime_mount_sources[@]} )) || {
        echo "offline runtime mount plan is inconsistent" >&2
        return 1
    }
    (( ${#created_runtime_mounts[@]} == 0 )) || {
        echo "offline runtime mounts are already tracked" >&2
        return 1
    }
    if ! printf 'mount_id\tparent_id\tmajor_minor\troot\ttarget\tmount_options\toptional_fields\tfilesystem\tsource\tsuper_options\n' \
        >"$runtime_mount_record"; then
        echo "offline runtime mount receipt cannot be initialized" >&2
        return 1
    fi
    if ! printf 'mount_id\ttarget\tresult\n' >"$runtime_cleanup_record"; then
        echo "offline runtime cleanup receipt cannot be initialized" >&2
        return 1
    fi
    if ! sync -f "$runtime_mount_record" || ! sync -f "$runtime_cleanup_record"; then
        echo "offline runtime mount receipt initialization is not durable" >&2
        return 1
    fi
    for index in "${!runtime_mount_paths[@]}"; do
        relative="${runtime_mount_paths[$index]}"
        source="${runtime_mount_sources[$index]}"
        destination="$target/$relative"
        if ! runtime_path_is_safe "$destination" "$target/$relative"; then
            prepare_runtime_mounts_failure 1
            return $?
        fi
        [[ -d "$source" && ! -L "$source" ]] || {
            echo "offline runtime source is unavailable: $source" >&2
            return 1
        }
        if ! existing_record="$(mountinfo_exact_record "$destination")"; then
            echo "offline runtime path state cannot be read: $destination" >&2
            return 1
        fi
        [[ -z "$existing_record" ]] || {
            echo "offline runtime path is already mounted: $destination" >&2
            return 1
        }
        source_record="$(mountinfo_exact_record "$source")" || return 1
        [[ -n "$source_record" ]] || {
            echo "offline runtime source is not an exact mountpoint: $source" >&2
            return 1
        }
        source_records[$index]="$source_record"
    done
    for index in "${!runtime_mount_paths[@]}"; do
        relative="${runtime_mount_paths[$index]}"
        source="${runtime_mount_sources[$index]}"
        destination="$target/$relative"
        if ! runtime_path_is_safe "$destination" "$target/$relative"; then
            prepare_runtime_mounts_failure 1
            return $?
        fi
        if ! existing_record="$(mountinfo_exact_record "$destination")"; then
            echo "offline runtime path state cannot be re-read: $destination" >&2
            prepare_runtime_mounts_failure 1
            return $?
        fi
        [[ -z "$existing_record" ]] || {
            echo "offline runtime path changed before mount: $destination" >&2
            prepare_runtime_mounts_failure 1
            return $?
        }
        bind_status=0
        if mount --bind -- "$source" "$destination"; then
            bind_status=0
        else
            bind_status=$?
        fi
        if ! target_record="$(mountinfo_exact_record "$destination")"; then
            echo "offline runtime mount state read failed after bind; rechecking once: $destination" >&2
            if ! target_record="$(mountinfo_exact_record "$destination")"; then
                echo "offline runtime mount state cannot be classified after bind: $destination" >&2
                if ! rollback_just_attempted_runtime_mount "$destination"; then
                    echo "offline runtime mount remains after ambiguous bind state: $destination" >&2
                fi
                prepare_runtime_mounts_failure "${bind_status:-1}"
                return $?
            fi
        fi
        if [[ -z "$target_record" ]]; then
            if (( bind_status == 0 )); then
                echo "offline runtime mount is missing after successful bind: $destination" >&2
                bind_status=1
            else
                echo "offline runtime bind failed without creating a mount: $destination" >&2
            fi
            prepare_runtime_mounts_failure "$bind_status"
            return $?
        fi
        if ! mount_id="$(runtime_record_field "$target_record" 0)"; then
            echo "offline runtime bind mount ID parse failed; rechecking once: $destination" >&2
            mount_id="$(runtime_record_field "$target_record" 0)" || {
                echo "offline runtime bind mount ID is ambiguous: $destination" >&2
                if ! rollback_just_attempted_runtime_mount "$destination"; then
                    echo "offline runtime mount remains after ambiguous ID state: $destination" >&2
                fi
                prepare_runtime_mounts_failure 1
                return $?
            }
        fi
        if ! target_path="$(runtime_record_field "$target_record" 4)"; then
            echo "offline runtime bind path parse failed; rechecking once: $destination" >&2
            target_path="$(runtime_record_field "$target_record" 4)" || {
                echo "offline runtime bind path is ambiguous: $destination" >&2
                if ! rollback_just_attempted_runtime_mount "$destination"; then
                    echo "offline runtime mount remains after ambiguous path state: $destination" >&2
                fi
                prepare_runtime_mounts_failure 1
                return $?
            }
        fi
        [[ "$mount_id" =~ ^[1-9][0-9]*$ && "$target_path" == "$destination" ]] || {
            echo "offline runtime bind mount ID or path is unsafe: $destination" >&2
            if ! rollback_just_attempted_runtime_mount "$destination"; then
                echo "offline runtime mount remains after unsafe ID/path state: $destination" >&2
            fi
            prepare_runtime_mounts_failure 1
            return $?
        }
        created_runtime_mounts+=("$destination")
        runtime_mount_ids["$destination"]="$mount_id"
        runtime_mount_records["$destination"]="$target_record"
        match_status=0
        if runtime_mount_matches_source "$target_record" "${source_records[$index]}" "$destination"; then
            match_status=0
        else
            match_status=$?
        fi
        if (( match_status == 2 )); then
            echo "offline runtime bind identity parse failed; rechecking once: $destination" >&2
            if runtime_mount_matches_source "$target_record" "${source_records[$index]}" "$destination"; then
                match_status=0
            else
                match_status=$?
            fi
        fi
        if (( match_status != 0 )); then
            echo "offline runtime bind produced an unexpected mount identity: $destination" >&2
            prepare_runtime_mounts_failure 1
            return $?
        fi
        if (( bind_status != 0 )); then
            echo "offline runtime bind reported failure after creating a mount: $destination" >&2
            prepare_runtime_mounts_failure "$bind_status"
            return $?
        fi
        propagation_status=0
        if mount --make-private -- "$destination"; then
            propagation_status=0
        else
            propagation_status=$?
        fi
        if ! target_record="$(mountinfo_exact_record "$destination")"; then
            echo "offline runtime mount state read failed after propagation change; rechecking once: $destination" >&2
            if ! target_record="$(mountinfo_exact_record "$destination")"; then
                echo "offline runtime mount state cannot be classified after propagation change: $destination" >&2
                prepare_runtime_mounts_failure "${propagation_status:-1}"
                return $?
            fi
        fi
        current_id="$(runtime_record_field "$target_record" 0)" || current_id=
        match_status=0
        if runtime_mount_matches_source "$target_record" "${source_records[$index]}" "$destination"; then
            match_status=0
        else
            match_status=$?
        fi
        if (( match_status == 2 )); then
            echo "offline runtime propagation identity parse failed; rechecking once: $destination" >&2
            if runtime_mount_matches_source "$target_record" "${source_records[$index]}" "$destination"; then
                match_status=0
            else
                match_status=$?
            fi
        fi
        if [[ -z "$target_record" || "$current_id" != "$mount_id" ]] || \
            (( match_status != 0 )); then
            echo "offline runtime mount identity changed during propagation isolation: $destination" >&2
            prepare_runtime_mounts_failure 1
            return $?
        fi
        target_optional="$(runtime_record_field "$target_record" 6)" || {
            prepare_runtime_mounts_failure 1
            return $?
        }
        if [[ "$target_optional" == *shared:* || "$target_optional" == *master:* || \
            "$target_optional" == *propagate_from:* ]]; then
            echo "offline runtime mount propagation remains unsafe: $destination" >&2
            prepare_runtime_mounts_failure 1
            return $?
        fi
        if (( propagation_status != 0 )); then
            echo "offline runtime propagation change reported failure: $destination" >&2
            prepare_runtime_mounts_failure "$propagation_status"
            return $?
        fi
        [[ "$target_optional" != *shared:* && "$target_optional" != *master:* && \
            "$target_optional" != *propagate_from:* ]] || {
            echo "offline runtime mount identity or propagation is unsafe: $destination" >&2
            prepare_runtime_mounts_failure 1
            return $?
        }
        runtime_mount_records["$destination"]="$target_record"
        if ! printf '%s\n' "${target_record//$'\x1f'/$'\t'}" >>"$runtime_mount_record"; then
            echo "offline runtime mount receipt write failed: $destination" >&2
            prepare_runtime_mounts_failure 1
            return $?
        fi
    done
    if (( ${#created_runtime_mounts[@]} != ${#runtime_mount_paths[@]} )); then
        prepare_runtime_mounts_failure 1
        return $?
    fi
    if ! sync -f "$runtime_mount_record"; then
        echo "offline runtime mount receipt is not durable" >&2
        prepare_runtime_mounts_failure 1
        return $?
    fi
}
cleanup_runtime_mounts() {
    local index destination expected_id current_record current_id
    local status=0
    local remaining=()
    (( ${#created_runtime_mounts[@]} > 0 )) || return 0
    [[ -f "$runtime_cleanup_record" && ! -L "$runtime_cleanup_record" ]] || {
        echo "offline runtime cleanup receipt is unavailable" >&2
        return 1
    }
    for (( index=${#created_runtime_mounts[@]}-1; index>=0; index-- )); do
        destination="${created_runtime_mounts[$index]}"
        expected_id="${runtime_mount_ids[$destination]-}"
        current_record="$(mountinfo_exact_record "$destination")" || {
            status=1
            remaining=("$destination" "${remaining[@]}")
            continue
        }
        if [[ -z "$current_record" ]]; then
            echo "tracked offline runtime mount disappeared: $destination" >&2
            status=1
            unset 'runtime_mount_ids[$destination]' 'runtime_mount_records[$destination]'
            continue
        fi
        current_id="$(runtime_record_field "$current_record" 0)" || current_id=
        if [[ -z "$expected_id" || "$current_id" != "$expected_id" ]] || \
            ! runtime_path_is_safe "$destination" "$destination"; then
            echo "offline runtime mount identity changed before cleanup: $destination" >&2
            status=1
            remaining=("$destination" "${remaining[@]}")
            continue
        fi
        if ! umount -- "$destination"; then
            echo "offline runtime mount cleanup failed: $destination" >&2
            status=1
            remaining=("$destination" "${remaining[@]}")
            continue
        fi
        if ! current_record="$(mountinfo_exact_record "$destination")"; then
            echo "offline runtime mount state cannot be read after cleanup: $destination" >&2
            status=1
            remaining=("$destination" "${remaining[@]}")
            continue
        fi
        if [[ -n "$current_record" ]]; then
            echo "offline runtime mount remains after cleanup: $destination" >&2
            status=1
            remaining=("$destination" "${remaining[@]}")
            continue
        fi
        printf '%s\t%s\tunmounted\n' "$expected_id" "$destination" \
            >>"$runtime_cleanup_record" || status=1
        unset 'runtime_mount_ids[$destination]' 'runtime_mount_records[$destination]'
    done
    created_runtime_mounts=("${remaining[@]}")
    sync -f "$runtime_cleanup_record" 2>/dev/null || status=1
    return "$status"
}

policy="$target/usr/sbin/policy-rc.d"
policy_backup="$install_root/policy-rc.d.original"
policy_state=absent
if [[ -e "$policy" || -L "$policy" ]]; then
    [[ -f "$policy" && ! -L "$policy" ]] || {
        echo "existing policy-rc.d is not a regular file" >&2
        exit 1
    }
    cp -a -- "$policy" "$policy_backup"
    policy_state=regular
fi
install_service_start_guard() {
    install -D -m 0755 /dev/stdin "$policy" <<'EOF'
#!/bin/sh
# Hoardarr package-install guard: deny maintainer-script service starts.
exit 101
EOF
}
install_service_start_guard

mapfile -t denied_units < <(
    python3 - "$retained_repo/evidence/compatibility-matrix.json" <<'PY'
import json, sys
value=json.load(open(sys.argv[1], encoding="utf-8"))
for unit in value["denied_units"]:
    print(unit)
PY
)
(( ${#denied_units[@]} > 0 )) || {
    echo "offline service deny list is empty" >&2
    exit 1
}
mask_root="$target/etc/systemd/system"
install -d -m 0755 "$mask_root"
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
entry_is_root_owned() {
    [[ "$(stat -c %u:%g -- "$1")" == 0:0 ]]
}
exact_iscsi_alias_parents_are_safe() {
    local directory
    for directory in \
        "$target/etc" \
        "$target/etc/systemd" \
        "$mask_root" \
        "$target/usr" \
        "$target/usr/lib" \
        "$target/usr/lib/systemd" \
        "$target/usr/lib/systemd/system" \
        "$target/var" \
        "$target/var/lib" \
        "$target/var/lib/dpkg" \
        "$target/var/lib/dpkg/info"
    do
        [[ -d "$directory" && ! -L "$directory" ]] || return 1
        entry_is_root_owned "$directory" || return 1
    done
}
unit_declares_exact_alias() {
    local canonical="$1"
    awk '
        /^\[/ { in_install = ($0 == "[Install]"); next }
        in_install && /^Alias=/ {
            value = substr($0, 7)
            count = split(value, aliases, /[[:space:]]+/)
            for (alias_index = 1; alias_index <= count; alias_index++) {
                if (aliases[alias_index] != "") total++
                if (aliases[alias_index] == "iscsi.service") found++
            }
        }
        END { exit(found == 1 && total == 1 ? 0 : 1) }
    ' "$canonical"
}
is_exact_package_backed_iscsi_alias() {
    local destination="$1"
    local unit="$2"
    local link_target
    local canonical
    local package_status
    local package_owner
    local package_status_metadata
    local package_list_metadata
    local md5_metadata
    local expected_md5
    local actual_md5
    [[ "$unit" == iscsi.service ]] || return 1
    [[ "$destination" == "$mask_root/iscsi.service" ]] || return 1
    exact_iscsi_alias_parents_are_safe || return 1
    [[ -L "$destination" ]] || return 1
    entry_is_root_owned "$destination" || return 1
    link_target="$(readlink -- "$destination")" || return 1
    [[ "$link_target" == /usr/lib/systemd/system/open-iscsi.service ]] || return 1
    canonical="$target$link_target"
    [[ -f "$canonical" && ! -L "$canonical" ]] || return 1
    entry_is_root_owned "$canonical" || return 1
    package_status_metadata="$target/var/lib/dpkg/status"
    package_list_metadata="$target/var/lib/dpkg/info/open-iscsi.list"
    [[ -f "$package_status_metadata" && ! -L "$package_status_metadata" ]] || return 1
    [[ -f "$package_list_metadata" && ! -L "$package_list_metadata" ]] || return 1
    entry_is_root_owned "$package_status_metadata" || return 1
    entry_is_root_owned "$package_list_metadata" || return 1
    package_status="$(LC_ALL=C dpkg-query \
        --admindir="$target/var/lib/dpkg" \
        -W '-f=${db:Status-Status}\t${binary:Package}\n' open-iscsi 2>/dev/null)" || return 1
    [[ "$package_status" == $'installed\topen-iscsi' ]] || return 1
    package_owner="$(LC_ALL=C dpkg-query \
        --admindir="$target/var/lib/dpkg" \
        -S /usr/lib/systemd/system/open-iscsi.service 2>/dev/null)" || return 1
    [[ "$package_owner" == 'open-iscsi: /usr/lib/systemd/system/open-iscsi.service' ]] || return 1
    md5_metadata="$target/var/lib/dpkg/info/open-iscsi.md5sums"
    [[ -f "$md5_metadata" && ! -L "$md5_metadata" ]] || return 1
    entry_is_root_owned "$md5_metadata" || return 1
    expected_md5="$(awk '
        $2 == "usr/lib/systemd/system/open-iscsi.service" { print $1; found++ }
        END { if (found != 1) exit 1 }
    ' "$md5_metadata")" || return 1
    [[ "$expected_md5" =~ ^[0-9a-f]{32}$ ]] || return 1
    actual_md5="$(md5sum -- "$canonical" | awk '{print $1}')" || return 1
    [[ "$actual_md5" == "$expected_md5" ]] || return 1
    unit_declares_exact_alias "$canonical" || return 1
}
record_package_backed_iscsi_alias() {
    local destination="$1"
    local unit="$2"
    local inode
    is_exact_package_backed_iscsi_alias "$destination" "$unit" || return 1
    inode="$(stat -c %i -- "$destination")" || return 1
    is_exact_package_backed_iscsi_alias "$destination" "$unit" || return 1
    [[ "$(stat -c %i -- "$destination")" == "$inode" ]] || return 1
    preserved_package_aliases["$unit"]="$destination"
    preserved_package_alias_inodes["$unit"]="$inode"
    preserved_package_alias_targets["$unit"]=/usr/lib/systemd/system/open-iscsi.service
    preserved_package_alias_canonical_units["$unit"]=open-iscsi.service
}
validate_preserved_unit_objects() {
    local unit
    local destination
    local expected_inode
    local expected_target
    local status=0
    for unit in "${!preserved_unit_masks[@]}"; do
        destination="${preserved_unit_masks[$unit]}"
        expected_inode="${preserved_unit_mask_inodes[$unit]-}"
        if [[ -z "$expected_inode" || ! -L "$destination" || \
            "$(readlink -- "$destination")" != /dev/null || \
            "$(stat -c %i -- "$destination")" != "$expected_inode" ]]; then
            echo "pre-existing unit mask changed during offline install: $unit" >&2
            status=1
        fi
    done
    for unit in "${!preserved_package_aliases[@]}"; do
        destination="${preserved_package_aliases[$unit]}"
        expected_inode="${preserved_package_alias_inodes[$unit]}"
        expected_target="${preserved_package_alias_targets[$unit]}"
        if [[ ! -L "$destination" || "$(readlink -- "$destination")" != "$expected_target" || \
            "$(stat -c %i -- "$destination")" != "$expected_inode" ]] || \
            ! is_exact_package_backed_iscsi_alias "$destination" "$unit"; then
            echo "package-backed unit alias changed during offline install: $unit" >&2
            status=1
        fi
    done
    return "$status"
}
prepare_temporary_unit_mask() {
    local destination="$1"
    local unit="$2"
    local link_target
    local new_inode
    if [[ "$unit" == open-iscsi.service && \
        "$destination" == "$mask_root/open-iscsi.service" && \
        -n "${preserved_package_aliases[iscsi.service]+present}" && \
        ! -e "$destination" && ! -L "$destination" ]]; then
        validate_preserved_unit_objects || return 1
        policy_guarded_canonical_units["$unit"]="$destination"
        return 0
    fi
    if [[ -L "$destination" ]]; then
        link_target="$(readlink -- "$destination")" || {
            echo "offline install cannot inspect a pre-existing unit override: $unit" >&2
            return 1
        }
        if [[ "$link_target" == /dev/null ]]; then
            new_inode="$(stat -c %i -- "$destination")" || {
                echo "offline install cannot record pre-existing unit mask identity: $unit" >&2
                return 1
            }
            [[ -L "$destination" && "$(readlink -- "$destination")" == /dev/null && \
                "$(stat -c %i -- "$destination")" == "$new_inode" ]] || {
                echo "pre-existing unit mask changed while recording identity: $unit" >&2
                return 1
            }
            preserved_unit_mask_inodes["$unit"]="$new_inode"
            preserved_unit_masks["$unit"]="$destination"
            return 0
        fi
        if record_package_backed_iscsi_alias "$destination" "$unit"; then
            return 0
        fi
        echo "offline install refuses to replace a pre-existing unit override: $unit" >&2
        return 1
    fi
    if [[ -e "$destination" ]]; then
        echo "offline install refuses to replace a pre-existing unit override: $unit" >&2
        return 1
    fi
    ln -s -- /dev/null "$destination"
    new_inode="$(stat -c %i -- "$destination")" || {
        if [[ -L "$destination" && "$(readlink -- "$destination")" == /dev/null ]]; then
            rm -f -- "$destination" || {
                echo "offline install cannot remove an untracked temporary unit mask: $unit" >&2
                return 1
            }
        fi
        echo "offline install cannot record temporary unit mask identity: $unit" >&2
        return 1
    }
    temporary_mask_inodes["$destination"]="$new_inode"
    temporary_masks+=("$destination")
}
cleanup_temporary_masks() {
    local mask
    local status=0
    local remaining=()
    for mask in "${temporary_masks[@]}"; do
        if [[ -z "${temporary_mask_inodes[$mask]-}" || ! -L "$mask" || \
            "$(readlink -- "$mask")" != /dev/null || \
            "$(stat -c %i -- "$mask")" != "${temporary_mask_inodes[$mask]-}" ]]; then
            echo "temporary unit mask changed during offline install: $mask" >&2
            status=1
            remaining+=("$mask")
            continue
        fi
        if rm -f -- "$mask"; then
            unset 'temporary_mask_inodes[$mask]'
        else
            status=1
            remaining+=("$mask")
        fi
    done
    temporary_masks=("${remaining[@]}")
    validate_preserved_unit_objects || status=1
    return "$status"
}
cleanup_service_guards() {
    local cleanup_status=0
    if [[ "$temporary_masks_cleanup_complete" != true ]]; then
        if cleanup_temporary_masks; then
            temporary_masks_cleanup_complete=true
        else
            cleanup_status=1
        fi
    fi
    if [[ "$policy_cleanup_complete" != true ]]; then
        if [[ "$policy_state" == regular ]]; then
            cp -a -- "$policy_backup" "$policy" || cleanup_status=1
        else
            rm -f -- "$policy" || cleanup_status=1
        fi
        (( cleanup_status != 0 )) || policy_cleanup_complete=true
    fi
    if [[ "$temporary_masks_cleanup_complete" == true && \
        "$policy_cleanup_complete" == true ]]; then
        validate_preserved_unit_objects || cleanup_status=1
        (( cleanup_status != 0 )) || service_guard_cleanup_complete=true
    fi
    return "$cleanup_status"
}
cleanup_guard() {
    local original_status="${1:-0}"
    local cleanup_status=0
    cleanup_runtime_mounts || cleanup_status=1
    cleanup_service_guards || cleanup_status=1
    if (( cleanup_status != 0 )); then
        echo "offline install cleanup integrity check failed" >&2
    fi
    if (( original_status != 0 )); then
        return "$original_status"
    fi
    return "$cleanup_status"
}
exit_cleanup() {
    local original_status="$1"
    trap - EXIT HUP INT TERM
    set +e
    cleanup_guard "$original_status"
    exit $?
}
signal_exit() {
    local signal_status="$1"
    trap - HUP INT TERM
    exit "$signal_status"
}
disable_unmasked_units() {
    local unit
    local preserved_mask
    local alias_path
    local canonical_unit
    local canonical_state
    local canonical_state_status
    local canonical_handled=false
    for unit in "${denied_units[@]}"; do
        if [[ -n "${preserved_unit_masks[$unit]+present}" ]]; then
            preserved_mask="${preserved_unit_masks[$unit]}"
            if [[ ! -L "$preserved_mask" || "$(readlink -- "$preserved_mask")" != /dev/null || \
                "$(stat -c %i -- "$preserved_mask")" != \
                "${preserved_unit_mask_inodes[$unit]-}" ]]; then
                echo "pre-existing unit mask changed during offline install: $unit" >&2
                return 1
            fi
            continue
        fi
        if [[ -n "${preserved_package_aliases[$unit]+present}" ]]; then
            alias_path="${preserved_package_aliases[$unit]}"
            canonical_unit="${preserved_package_alias_canonical_units[$unit]}"
            validate_preserved_unit_objects || return 1
            chroot "$target" systemctl disable "$canonical_unit" >/dev/null 2>&1 || {
                echo "offline install could not disable canonical unit: $canonical_unit" >&2
                return 1
            }
            if [[ -e "$alias_path" || -L "$alias_path" || \
                -e "$mask_root/sysinit.target.wants/$canonical_unit" || \
                -L "$mask_root/sysinit.target.wants/$canonical_unit" ]]; then
                echo "offline install canonical unit enablement remains: $canonical_unit" >&2
                return 1
            fi
            canonical_state_status=0
            canonical_state="$(chroot "$target" systemctl is-enabled "$canonical_unit" 2>&1)" || \
                canonical_state_status=$?
            canonical_state="$(printf '%s\n' "$canonical_state" | head -n 1)"
            [[ "$canonical_state" == disabled && "$canonical_state_status" -eq 1 ]] || {
                echo "offline install canonical unit is not disabled: $canonical_unit" >&2
                return 1
            }
            canonical_handled=true
            continue
        fi
        if [[ "$canonical_handled" == true && "$unit" == open-iscsi.service ]]; then
            continue
        fi
        chroot "$target" systemctl disable "$unit" >/dev/null 2>&1 || true
    done
}
trap 'exit_cleanup $?' EXIT
trap 'signal_exit 129' HUP
trap 'signal_exit 130' INT
trap 'signal_exit 143' TERM
for unit in "${denied_units[@]}"; do
    [[ "$unit" =~ ^[A-Za-z0-9@_.:-]+\.(service|socket|timer|target)$ ]] || {
        echo "unsafe unit in offline service policy" >&2
        exit 1
    }
    destination="$mask_root/$unit"
    prepare_temporary_unit_mask "$destination" "$unit"
done

# Autoassembly/import is denied independently of service-start policy. These
# files contain no device identity and authorize no storage operation.
install -D -m 0644 /dev/stdin "$target/etc/mdadm/mdadm.conf" <<'EOF'
# Hoardarr deny-by-default appliance policy. Explicit jobs invoke mdadm directly.
AUTO -all
EOF
install -D -m 0644 /dev/stdin "$target/etc/multipath.conf" <<'EOF'
# Hoardarr deny-by-default appliance policy. The redundancy workflow replaces
# this blacklist only after stable logical-storage identity is proven.
defaults {
    find_multipaths strict
}
blacklist {
    devnode ".*"
}
EOF
lvm_guard="$install_root/lvm-guard"
install -d -m 0700 "$lvm_guard"
install -m 0600 /dev/stdin "$lvm_guard/lvm.conf" <<'EOF'
devices {
    filter = [ "r|.*|" ]
    global_filter = [ "r|.*|" ]
}
activation {
    event_activation = 0
    auto_activation_volume_list = [ ]
}
EOF

mapfile -t exact_roots <"$retained_repo/evidence/root-package-versions.txt"
(( ${#exact_roots[@]} > 0 )) || {
    echo "offline root package list is empty" >&2
    exit 1
}
apt_options=(
    -o "Dir::Etc::sourcelist=/etc/apt/sources.list.d/hoardarr-offline.list"
    -o "Dir::Etc::sourceparts=-"
    -o "Acquire::Languages=none"
    -o "Acquire::Retries=0"
    -o "Acquire::http::Proxy=false"
    -o "Acquire::https::Proxy=false"
)
export DEBIAN_FRONTEND=noninteractive
export LVM_SYSTEM_DIR=/opt/hoardarr-install/lvm-guard
export NEEDRESTART_MODE=l
export UCF_FORCE_CONFFOLD=1
prepare_runtime_mounts
chroot "$target" apt-get "${apt_options[@]}" update
simulation="$(chroot "$target" apt-get "${apt_options[@]}" --simulate --no-install-recommends install "${exact_roots[@]}")"
if grep -Eq '^(Remv|Purg) |DOWNGRADED' <<<"$simulation"; then
    echo "offline package transaction would remove or downgrade a package" >&2
    exit 1
fi
chroot "$target" apt-get "${apt_options[@]}" \
    --yes --no-install-recommends install "${exact_roots[@]}"

python3 - "$target" "$retained_repo/evidence/package-manifest.json" "$state_root/package-readback.json" <<'PY'
import json, pathlib, subprocess, sys
target=pathlib.Path(sys.argv[1])
manifest=json.load(open(sys.argv[2], encoding="utf-8"))["packages"]
expected={(p["name"], p["architecture"]):p["version"] for p in manifest}
command=["chroot",str(target),"dpkg-query","-W","-f=${binary:Package}\\t${Version}\\t${Architecture}\\t${db:Status-Abbrev}\\n"]
result=subprocess.run(command,check=True,text=True,capture_output=True)
actual={}
for line in result.stdout.splitlines():
    name,version,arch,status=line.split("\t")
    actual[(name.split(":",1)[0],arch)]=(version,status)
missing=[]
mismatched=[]
for key,version in sorted(expected.items()):
    installed=actual.get(key)
    if installed is None:
        missing.append({"name":key[0],"architecture":key[1]})
    elif installed != (version,"ii "):
        mismatched.append({"name":key[0],"architecture":key[1],"expected":version,"actual":installed})
if missing or mismatched:
    raise SystemExit(f"offline package readback mismatch: missing={missing!r} mismatched={mismatched!r}")
receipt={"schema_version":1,"expected_count":len(expected),"missing":missing,"mismatched":mismatched}
path=pathlib.Path(sys.argv[3]); path.parent.mkdir(parents=True,exist_ok=True)
path.write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n",encoding="utf-8")
PY

audit="$(chroot "$target" dpkg --audit)"
[[ -z "$audit" ]] || {
    echo "dpkg audit is not clean after offline package installation" >&2
    printf '%s\n' "$audit" >&2
    exit 1
}
chroot "$target" apt-get "${apt_options[@]}" --simulate check

cleanup_service_guards
disable_unmasked_units

python3 - "$target" "$retained_repo/evidence/compatibility-matrix.json" "$state_root/service-policy-readback.json" <<'PY'
import json, pathlib, subprocess, sys
target=pathlib.Path(sys.argv[1])
matrix=json.load(open(sys.argv[2], encoding="utf-8"))
states=[]
for unit in matrix["denied_units"]:
    result=subprocess.run(["systemctl",f"--root={target}","is-enabled",unit],text=True,capture_output=True)
    state=(result.stdout or result.stderr).strip().splitlines()[0] if (result.stdout or result.stderr).strip() else "not-found"
    if state not in {"disabled","masked","static","indirect","not-found","generated","transient","alias"}:
        raise SystemExit(f"optional unit remains enabled: {unit}={state}")
    if unit in {"iscsi.service", "open-iscsi.service"} and state == "alias":
        raise SystemExit(f"canonical iSCSI unit alias remains after final disable: {unit}")
    states.append({"unit":unit,"enabled_state":state})
path=pathlib.Path(sys.argv[3]); path.write_text(json.dumps({"schema_version":1,"units":states},indent=2,sort_keys=True)+"\n",encoding="utf-8")
PY

sha256sum "$state_root/package-readback.json" "$state_root/service-policy-readback.json" \
    >"$state_root/SHA256SUMS"
cleanup_runtime_mounts
cleanup_guard 0
trap - EXIT HUP INT TERM
echo "Hoardarr offline package payload installed and verified."
