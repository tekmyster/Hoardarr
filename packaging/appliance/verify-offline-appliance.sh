#!/usr/bin/env bash
set -euo pipefail

emit_serial=false
require_api=false
while (($#)); do
    case "$1" in
        --emit-serial) emit_serial=true ;;
        --require-api) require_api=true ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

repo=/opt/hoardarr/offline-repository
state=/var/lib/hoardarr-install
[[ -d "$repo" && ! -L "$repo" ]] || { echo "retained offline repository is unavailable" >&2; exit 1; }
(cd "$repo" && sha256sum --check --strict SHA256SUMS >/dev/null)
(cd "$state" && sha256sum --check --strict SHA256SUMS >/dev/null)
[[ "$(awk 'NF && $1 !~ /^#/ {print $1; exit}' /etc/apt/sources.list.d/hoardarr-offline.list)" == deb ]] || {
    echo "retained offline APT source is unavailable" >&2
    exit 1
}
if find /etc/apt -maxdepth 2 -type f \( -name '*.list' -o -name '*.sources' \) \
    ! -path /etc/apt/sources.list.d/hoardarr-offline.list -print -quit | grep -q .; then
    echo "an unapproved online APT source remains enabled" >&2
    exit 1
fi

audit="$(dpkg --audit)"
[[ -z "$audit" ]] || { echo "dpkg audit is not clean" >&2; exit 1; }
apt-get \
    -o Dir::Etc::sourcelist=/etc/apt/sources.list.d/hoardarr-offline.list \
    -o Dir::Etc::sourceparts=- \
    -o Acquire::Retries=0 \
    --simulate check >/dev/null

required_commands=(
    b3sum blkid btrfs cryptsetup exportfs fio fcoeadm iostat iscsiadm jq lsof
    dstat lsscsi mergerfs multipath ncdu nvme pv rclone rsync sg_ses smartctl snapraid
    targetcli xxhsum zpool
)
missing_commands=()
for command_name in "${required_commands[@]}"; do
    command -v "$command_name" >/dev/null 2>&1 || missing_commands+=("$command_name")
done
(( ${#missing_commands[@]} == 0 )) || {
    echo "required offline commands are unavailable: ${missing_commands[*]}" >&2
    exit 1
}

python3 - "$repo/evidence/compatibility-matrix.json" "$state/offline-first-boot-readback.json" <<'PY'
import json, pathlib, subprocess, sys
matrix=json.load(open(sys.argv[1], encoding="utf-8"))
states=[]
for unit in matrix["denied_units"]:
    enabled=subprocess.run(["systemctl","is-enabled",unit],text=True,capture_output=True,check=False)
    active=subprocess.run(["systemctl","is-active",unit],text=True,capture_output=True,check=False)
    enabled_state=(enabled.stdout or enabled.stderr).strip().splitlines()[0] if (enabled.stdout or enabled.stderr).strip() else "not-found"
    active_state=(active.stdout or active.stderr).strip().splitlines()[0] if (active.stdout or active.stderr).strip() else "not-found"
    if enabled_state not in {"disabled","masked","static","indirect","not-found","generated","transient","alias"}:
        raise SystemExit(f"optional unit is enabled: {unit}={enabled_state}")
    if active_state == "active":
        raise SystemExit(f"optional unit is active: {unit}")
    states.append({"unit":unit,"enabled":enabled_state,"active":active_state})
enabled=subprocess.run(
    ["systemctl","list-unit-files","--state=enabled","--no-legend","--no-pager"],
    text=True,capture_output=True,check=True,
).stdout.splitlines()
active=subprocess.run(
    ["systemctl","list-units","--state=active","--no-legend","--no-pager"],
    text=True,capture_output=True,check=True,
).stdout.splitlines()
path=pathlib.Path(sys.argv[2])
path.write_text(json.dumps({
    "schema_version":1,
    "optional_units":states,
    "enabled_units":[line.split()[0] for line in enabled if line.split()],
    "active_units":[line.split()[0] for line in active if line.split()],
},indent=2,sort_keys=True)+"\n",encoding="utf-8")
PY

if "$require_api"; then
    for _ in $(seq 1 120); do
        curl --fail --silent --show-error http://127.0.0.1:7877/health/ready >/dev/null && break
        sleep 2
    done
    curl --fail --silent --show-error http://127.0.0.1:7877/health/ready >/dev/null
fi

sha256sum "$state/offline-first-boot-readback.json" >"$state/offline-first-boot-readback.sha256"
if "$emit_serial"; then
    {
        echo HOARDARR_OFFLINE_EVIDENCE_BEGIN
        cat "$state/package-readback.json"
        cat "$state/service-policy-readback.json"
        cat "$state/offline-first-boot-readback.json"
        echo HOARDARR_OFFLINE_EVIDENCE_END
    } >/dev/ttyS0
fi
echo "Hoardarr offline appliance payload verified."
