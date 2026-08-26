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
install -D -m 0755 /dev/stdin "$policy" <<'EOF'
#!/bin/sh
# Hoardarr package-install guard: deny maintainer-script service starts.
exit 101
EOF

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
for unit in "${denied_units[@]}"; do
    [[ "$unit" =~ ^[A-Za-z0-9@_.:-]+\.(service|socket|timer|target)$ ]] || {
        echo "unsafe unit in offline service policy" >&2
        exit 1
    }
    destination="$mask_root/$unit"
    if [[ -e "$destination" || -L "$destination" ]]; then
        echo "offline install refuses to replace a pre-existing unit override: $unit" >&2
        exit 1
    fi
    ln -s /dev/null "$destination"
    temporary_masks+=("$destination")
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

cleanup_guard() {
    local mask
    for mask in "${temporary_masks[@]}"; do
        [[ -L "$mask" && "$(readlink -- "$mask")" == /dev/null ]] && rm -f -- "$mask"
    done
    if [[ "$policy_state" == regular ]]; then
        cp -a -- "$policy_backup" "$policy"
    else
        rm -f -- "$policy"
    fi
}
trap cleanup_guard EXIT

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

cleanup_guard
trap - EXIT
for unit in "${denied_units[@]}"; do
    chroot "$target" systemctl disable "$unit" >/dev/null 2>&1 || true
done

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
    states.append({"unit":unit,"enabled_state":state})
path=pathlib.Path(sys.argv[3]); path.write_text(json.dumps({"schema_version":1,"units":states},indent=2,sort_keys=True)+"\n",encoding="utf-8")
PY

sha256sum "$state_root/package-readback.json" "$state_root/service-policy-readback.json" \
    >"$state_root/SHA256SUMS"
echo "Hoardarr offline package payload installed and verified."
