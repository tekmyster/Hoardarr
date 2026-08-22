#!/usr/bin/env bash
set -euo pipefail
[[ "$(id -u)" -eq 0 ]] || { echo "requires root in a disposable runner" >&2; exit 1; }
[[ -f /.hoardarr-disposable-runner ]] || {
  echo "refusing workload: disposable runner marker is missing" >&2
  exit 1
}
[[ "${GITHUB_ACTIONS:-false}" == "true" || "${HOARDARR_DISPOSABLE_VM:-0}" == "1" ]] || {
  echo "refusing workload outside GitHub Actions or an explicitly disposable VM" >&2
  exit 1
}

repo="$(cd "$(dirname "$0")/../.." && pwd)"
python_runtime="${HOARDARR_TEST_PYTHON:-$repo/backend/.venv/bin/python}"
[[ -x "$python_runtime" ]] || { echo "locked backend Python is unavailable" >&2; exit 1; }
work="$(mktemp -d -t hoardarr-mergerfs-telemetry.XXXXXXXX)"
loops=()
member_mounts=()
pool="$work/pool"
cleanup() {
  mountpoint -q "$pool" && umount -- "$pool" || true
  for mountpoint in "${member_mounts[@]}"; do
    mountpoint -q "$mountpoint" && umount -- "$mountpoint" || true
  done
  for loop in "${loops[@]}"; do losetup -d -- "$loop" 2>/dev/null || true; done
  rm -rf -- "$work"
}
trap cleanup EXIT

protected="$( { findmnt -rn -o SOURCE / /boot /boot/efi 2>/dev/null || true; swapon --noheadings --raw --output NAME 2>/dev/null || true; } | sort -u )"
for number in 1 2 3 4; do
  image="$work/member-$number.img"
  truncate -s 2G "$image"
  loop="$(losetup --find --show "$image")"
  loops+=("$loop")
  [[ "$(realpath "$(losetup --noheadings --output BACK-FILE "$loop" | xargs)")" == "$(realpath "$image")" ]]
  ! grep -Fxq "$loop" <<<"$protected"
  [[ -z "$(findmnt -rn -S "$loop" -o TARGET)" ]]
  [[ "$(blockdev --getsize64 "$loop")" -eq "$(stat -c %s "$image")" ]]
  mkfs.ext4 -F -E lazy_itable_init=1,lazy_journal_init=1,nodiscard "$loop"
  mountpoint="$work/member-$number"
  mkdir "$mountpoint"
  mount -o noatime "$loop" "$mountpoint"
  member_mounts+=("$mountpoint")
done

mkdir "$pool"
branches="$(IFS=:; echo "${member_mounts[*]}")"
mergerfs -o category.create=mfs,moveonenospc=true,cache.files=partial,dropcacheonclose=true \
  "$branches" "$pool"
mountpoint -q "$pool"

python3 - "$work/config.json" "$pool" "${loops[@]}" -- "${member_mounts[@]}" <<'PY'
import json, os, sys
output, pool, *values = sys.argv[1:]
separator = values.index("--")
loops, branches = values[:separator], values[separator + 1:]
disks = []
for index, (device, branch) in enumerate(zip(loops, branches, strict=True), 1):
    filesystem = os.statvfs(branch)
    size = filesystem.f_blocks * filesystem.f_frsize
    disks.append({
        "id": f"test-image:mergerfs-member-{index}",
        "kernel_name": os.path.basename(device), "kernel_path": device,
        "vendor": "Linux", "model": "Disposable loop-backed SSD image",
        "capacity_bytes": size, "rotational": False, "system_disk": False,
        "identity": {"serial": f"TEST-MERGERFS-{index}", "wwn": None},
        "sector_sizes": {"logical_bytes": 512, "physical_bytes": 512},
        "partitions": [], "signatures": ["ext4"], "mount_state": branch,
        "health": {"status": "Not reported"},
    })
inventory = {"pools": {"items": [{
    "id": "mergerfs:workload", "name": "workload", "type": "mergerFS",
    "status": "mounted", "branches": branches, "mountpoint": pool,
    "device_names": [os.path.basename(item) for item in loops],
    "total_bytes": sum(item["capacity_bytes"] for item in disks),
    "used_bytes": 0, "free_bytes": sum(item["capacity_bytes"] for item in disks),
}]}, "controllers": {"items": []}, "topology": {"nodes": []}}
json.dump({"environment": "Ubuntu 24.04 disposable GitHub runner", "hardware": {"disks": disks}, "inventory": inventory, "branches": branches, "mountpoint": pool, "policy": "category.create=mfs"}, open(output, "w"), indent=2)
PY

phase_file="$work/phases.json"
python3 - "$phase_file" <<'PY'
import json, sys
json.dump({}, open(sys.argv[1], "w"))
PY
phase() {
  python3 - "$phase_file" "$1" <<'PY'
import datetime, json, sys
path, name = sys.argv[1:]
data = json.load(open(path))
data[name] = datetime.datetime.now(datetime.timezone.utc).isoformat()
json.dump(data, open(path, "w"), indent=2)
PY
}

database="$work/telemetry.db"
independent="$work/independent"
mkdir "$independent"
cat /proc/diskstats >"$independent/diskstats-before.txt"
df -B1 "$pool" >"$independent/df-before.txt"
du -sb "$pool" >"$independent/du-before.txt"
iostat -dx 1 35 >"$independent/iostat.txt" &
iostat_pid=$!
vmstat 1 35 >"$independent/vmstat.txt" &
vmstat_pid=$!
phase idle_baseline_start
"$python_runtime" "$repo/tests/integration/mergerfs_persistent_telemetry.py" collect \
  --database "$database" --config "$work/config.json" --seconds 38 &
collector_pid=$!
sleep 4
phase browser_disconnected_start
phase small_file_start
python3 - "$pool" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1]) / "small"
root.mkdir()
payload = bytes(range(256)) * 16
for number in range(1200):
    (root / f"small-{number:05d}.bin").write_bytes(payload)
PY
phase sequential_write_start
write_pids=()
for number in 1 2 3 4; do
  dd if=/dev/zero of="$pool/large-$number.bin" bs=1M count=128 conv=fsync status=none &
  write_pids+=("$!")
done
wait "${write_pids[@]}"
phase mixed_concurrent_write_start
write_pids=()
for number in 1 2 3 4; do
  dd if=/dev/zero of="$pool/mixed-$number.bin" bs=256K count=128 conv=fsync status=none &
  write_pids+=("$!")
done
wait "${write_pids[@]}"
phase sequential_read_start
find "$pool" -type f -print0 | xargs -0 -n 1 cat >/dev/null
phase random_read_start
fio --name=hoardarr-random-read --filename="$pool/large-1.bin" --rw=randread --bs=4k \
  --size=64M --time_based=1 --runtime=4 --direct=1 --output="$work/fio-random.json" --output-format=json
phase mixed_read_write_start
fio --name=hoardarr-mixed --filename="$pool/fio-mixed.bin" --rw=randrw --rwmixread=60 \
  --bs=64k --size=128M --time_based=1 --runtime=4 --direct=1 \
  --output="$work/fio-mixed.json" --output-format=json
phase workload_stopped
sleep 5
phase browser_reconnected_at
wait "$collector_pid"
wait "$iostat_pid" "$vmstat_pid"
cat /proc/diskstats >"$independent/diskstats-after.txt"
df -B1 "$pool" >"$independent/df-after.txt"
du -sb "$pool" >"$independent/du-after.txt"

# A new collector process proves service-process restart persistence.
phase service_restart_started
"$python_runtime" "$repo/tests/integration/mergerfs_persistent_telemetry.py" collect \
  --database "$database" --config "$work/config.json" --seconds 4
phase service_restart_completed

mkdir -p "$repo/dist/validation"
"$python_runtime" "$repo/tests/integration/mergerfs_persistent_telemetry.py" report \
  --database "$database" --config "$work/config.json" --phases "$phase_file" \
  --independent "$independent" \
  --output "$repo/dist/validation/mergerfs-persistent-telemetry.json"

# Delete only the uniquely named test dataset, then let the trap unmount/detach.
find "$pool" -mindepth 1 -maxdepth 1 \
  \( -name 'small' -o -name 'large-*.bin' -o -name 'mixed-*.bin' -o -name 'fio-mixed.bin' \) \
  -exec rm -rf -- {} +
