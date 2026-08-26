#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "usage: $0 TARGET_ROOT ISO_REPOSITORY" >&2
    exit 2
}

[[ $# -eq 2 ]] || usage
target="$(realpath -- "$1")"
source_repo="$(realpath -- "$2")"
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
    for mask in "${temporary_masks[@]}"; do
        if [[ -z "${temporary_mask_inodes[$mask]-}" || ! -L "$mask" || \
            "$(readlink -- "$mask")" != /dev/null || \
            "$(stat -c %i -- "$mask")" != "${temporary_mask_inodes[$mask]-}" ]]; then
            echo "temporary unit mask changed during offline install: $mask" >&2
            status=1
            continue
        fi
        rm -f -- "$mask" || status=1
    done
    validate_preserved_unit_objects || status=1
    return "$status"
}
cleanup_guard() {
    local original_status="${1:-0}"
    local cleanup_status=0
    cleanup_temporary_masks || cleanup_status=1
    if [[ "$policy_state" == regular ]]; then
        cp -a -- "$policy_backup" "$policy" || cleanup_status=1
    else
        rm -f -- "$policy" || cleanup_status=1
    fi
    if (( cleanup_status != 0 )); then
        echo "offline install cleanup integrity check failed" >&2
    fi
    if (( original_status != 0 )); then
        return "$original_status"
    fi
    return "$cleanup_status"
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
trap 'original_status=$?; trap - EXIT; cleanup_guard "$original_status"; exit $?' EXIT
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
chroot "$target" apt-get "${apt_options[@]}" update
simulation="$(chroot "$target" apt-get "${apt_options[@]}" --simulate --no-install-recommends install "${exact_roots[@]}")"
if grep -Eq '^(Remv|Purg) |DOWNGRADED' <<<"$simulation"; then
    echo "offline package transaction would remove or downgrade a package" >&2
    exit 1
fi
chroot "$target" apt-get "${apt_options[@]}" \
    --yes --no-download --no-install-recommends install "${exact_roots[@]}"

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

trap - EXIT
cleanup_guard 0
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
echo "Hoardarr offline package payload installed and verified."
