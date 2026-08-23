#!/usr/bin/env bash
set -euo pipefail
trap 'rc=$?; printf "FAILED at line %s: %s (rc=%s)\n" "$LINENO" "$BASH_COMMAND" "$rc" >&2' ERR
[[ "$(id -u)" -eq 0 ]] || { echo "requires root in a disposable runner" >&2; exit 1; }
[[ -f /.hoardarr-disposable-runner ]] || {
  echo "refusing loop tests: disposable runner marker is missing" >&2
  exit 1
}
work="$(mktemp -d -t hoardarr-loop.XXXXXXXX)"
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python="${HOARDARR_TEST_PYTHON:-$repo/backend/.venv/bin/python}"
managed="/mnt/hoardarr/ci-$$"
loops=()
md_device=""
zpool_name=""
cleanup() {
  set +e
  [[ -z "$zpool_name" ]] || zpool destroy -f "$zpool_name" 2>/dev/null || true
  [[ -z "$md_device" ]] || mdadm --stop "$md_device" 2>/dev/null || true
  for loop in "${loops[@]}"; do
    findmnt -rn -S "$loop" -o TARGET | while IFS= read -r target; do umount -- "$target" 2>/dev/null || true; done
    losetup -d -- "$loop" 2>/dev/null || true
  done
  [[ "$managed" == /mnt/hoardarr/ci-[0-9]* ]] && rm -rf -- "$managed"
  rm -rf -- "$work"
}
trap cleanup EXIT

make_loop() {
  local name="$1" size="$2" image loop backing
  image="$work/$name.img"
  truncate -s "$size" "$image"
  loop="$(losetup --find --show "$image")"
  loops+=("$loop")
  backing="$(losetup --noheadings --output BACK-FILE "$loop" | xargs realpath)"
  [[ "$backing" == "$(realpath "$image")" ]]
  [[ "$(blockdev --getsize64 "$loop")" -eq "$(stat -c %s "$image")" ]]
  created_loop="$loop"
}

assert_test_loop() {
  local candidate="$1" backing source parent
  [[ " ${loops[*]} " == *" $candidate "* ]]
  backing="$(losetup --noheadings --output BACK-FILE "$candidate" | xargs realpath)"
  [[ "$backing" == "$work/"*.img ]]
  while IFS= read -r source; do
    [[ -z "$source" ]] && continue
    parent="$(readlink -f -- "$source" 2>/dev/null || printf '%s' "$source")"
    [[ "$candidate" != "$parent" ]]
  done < <({ findmnt -rn -o SOURCE / /boot /boot/efi 2>/dev/null || true; swapon --noheadings --raw --output NAME 2>/dev/null || true; } | sort -u)
  [[ -z "$(findmnt -rn -S "$candidate" -o TARGET)" ]]
}

make_loop ext4 2G
loop="$created_loop"
assert_test_loop "$loop"
mkfs.ext4 -F -E lazy_itable_init=1,lazy_journal_init=1,nodiscard "$loop"
mkdir "$work/mnt"
mount "$loop" "$work/mnt"
mkdir "$work/mnt/Movies" "$work/mnt/TV"
setfacl -m u::rwx,g::r-x,o::--- "$work/mnt"
getfacl "$work/mnt" >/dev/null
umount "$work/mnt"

[[ "${HOARDARR_EXTENDED_STORAGE_TESTS:-0}" == "1" ]] || exit 0

md_members=()
for number in 1 2 3 4; do
  make_loop "md$number" 512M
  member="$created_loop"
  assert_test_loop "$member"
  md_members+=("$member")
done
md_device="/dev/md/hoardarr-ci-$$"
mdadm --create "$md_device" --run --assume-clean --level=6 --raid-devices=4 --metadata=1.2 "${md_members[@]}"
mkfs.xfs -f -K "$md_device"
mkdir "$work/md"
mount "$md_device" "$work/md"
touch "$work/md/md-verified"
umount "$work/md"
mdadm --detail "$md_device" | grep -q 'Raid Level : raid6'
mdadm --stop "$md_device"
md_device=""

zfs_members=()
for number in 1 2 3 4; do
  make_loop "zfs$number" 512M
  member="$created_loop"
  assert_test_loop "$member"
  zfs_members+=("$member")
done
zpool_name="hoardarr_ci_$$"
zpool create -f -o ashift=12 -O mountpoint=none -O compression=lz4 "$zpool_name" mirror "${zfs_members[@]:0:2}"
zfs create -o mountpoint="$work/zfs" -o recordsize=1M "$zpool_name/media"
printf 'zfs expansion data integrity\n' >"$work/zfs/zfs-verified"
zfs_hash_before="$(sha256sum "$work/zfs/zfs-verified" | awk '{print $1}')"
zfs_guid_before="$(zpool get -Hp -o value guid "$zpool_name")"
zfs_mount_before="$(zfs get -Hp -o value mountpoint "$zpool_name/media")"
zfs snapshot "$zpool_name/media@initial"
zpool scrub "$zpool_name"
zpool status "$zpool_name" | grep -q 'state: ONLINE'
"$python" - "$zpool_name" >"$work/zfs-before.json" <<'PY'
import json
import sys
from hoardarr.storage.executor import _live_zfs_pool_state

print(json.dumps(_live_zfs_pool_state(sys.argv[1]), sort_keys=True))
PY
zfs_config_before="$(jq -r .config_sha256 "$work/zfs-before.json")"
zfs_vdev_count_before="$(jq -r .vdev_count "$work/zfs-before.json")"
"$python" "$repo/tests/integration/zfs_expand_vdev.py" \
  --pool "$zpool_name" \
  --vdev-type mirror \
  --expected-guid "$zfs_guid_before" \
  --expected-config-sha256 "$zfs_config_before" \
  --expected-vdev-count "$zfs_vdev_count_before" \
  --evidence "$repo/dist/validation/zfs-expansion.json" \
  "${zfs_members[@]:2:2}"
[[ "$(zfs get -Hp -o value mountpoint "$zpool_name/media")" == "$zfs_mount_before" ]]
[[ "$(zpool get -Hp -o value guid "$zpool_name")" == "$zfs_guid_before" ]]
[[ "$(sha256sum "$work/zfs/zfs-verified" | awk '{print $1}')" == "$zfs_hash_before" ]]
[[ "$(zpool status -P "$zpool_name" | grep -Ec 'mirror-[0-9]+')" -eq 2 ]]
zpool destroy -f "$zpool_name"
zpool_name=""

make_loop snap-data 512M
snap_data="$created_loop"
make_loop snap-parity 512M
snap_parity="$created_loop"
for member in "$snap_data" "$snap_parity"; do assert_test_loop "$member"; mkfs.ext4 -F -E nodiscard "$member"; done
mkdir -p "$managed/data-1" "$managed/parity-1"
mount "$snap_data" "$managed/data-1"
mount "$snap_parity" "$managed/parity-1"
printf 'test payload\n' >"$managed/data-1/file.txt"
snap_config_root="$work/snapraid"
snap_config="$snap_config_root/media.conf"
mkdir -p "$snap_config_root"
cat >"$snap_config" <<EOF
parity $managed/parity-1/snapraid.parity
content $managed/data-1/snapraid.content
content $managed/parity-1/snapraid.content
data d1 $managed/data-1
EOF
snapraid -c "$snap_config" sync
snapraid -c "$snap_config" status
snapraid -c "$snap_config" diff
snapraid -c "$snap_config" check

make_loop snap-data-2 512M
snap_data_2="$created_loop"
assert_test_loop "$snap_data_2"
mkfs.ext4 -F -E nodiscard "$snap_data_2"
mkdir "$managed/data-2"
mount "$snap_data_2" "$managed/data-2"
printf 'second data member\n' >"$managed/data-2/file.txt"
config_sha="$(sha256sum "$snap_config" | awk '{print $1}')"
"$python" "$repo/tests/integration/snapraid_expand_config.py" \
  --config "$snap_config" \
  --role data \
  --mountpoint "$managed/data-2" \
  --expected-sha256 "$config_sha"
snapraid -c "$snap_config" status
snapraid -c "$snap_config" sync
snapraid -c "$snap_config" check
grep -Eq "^data h[0-9a-f]{12} $managed/data-2$" "$snap_config"

make_loop snap-parity-2 512M
snap_parity_2="$created_loop"
assert_test_loop "$snap_parity_2"
mkfs.ext4 -F -E nodiscard "$snap_parity_2"
mkdir "$managed/parity-2"
mount "$snap_parity_2" "$managed/parity-2"
config_sha="$(sha256sum "$snap_config" | awk '{print $1}')"
"$python" "$repo/tests/integration/snapraid_expand_config.py" \
  --config "$snap_config" \
  --role parity \
  --mountpoint "$managed/parity-2" \
  --expected-sha256 "$config_sha"
snapraid -c "$snap_config" status
snapraid -c "$snap_config" --force-full sync
snapraid -c "$snap_config" check
grep -Fq "2-parity $managed/parity-2/snapraid.parity" "$snap_config"
mkdir -p "$repo/dist/validation"
final_config_sha="$(sha256sum "$snap_config" | awk '{print $1}')"
cat >"$repo/dist/validation/snapraid-expansion.json" <<EOF
{
  "classification": "VERIFIED IN ISOLATION",
  "source": "disposable Linux loop devices",
  "data_member_added": true,
  "second_parity_added": true,
  "snapraid_sync_completed": true,
  "snapraid_check_completed": true,
  "configuration_sha256": "$final_config_sha",
  "member_count": 2,
  "parity_level_count": 2
}
EOF

# Lose d1 after a synchronized snapshot, then exercise the production executor
# against a newly created, blank replacement loop. The original member is never
# reformatted and the reconstructed file is independently hashed before cleanup.
snapraid_recovery_hash="$(sha256sum "$managed/data-1/file.txt" | awk '{print $1}')"
umount "$managed/data-1"
make_loop snap-replacement 512M
snap_replacement="$created_loop"
assert_test_loop "$snap_replacement"
"$python" "$repo/tests/integration/snapraid_replace_data.py" \
  --config "$snap_config" \
  --pool media \
  --data-name d1 \
  --replacement-loop "$snap_replacement" \
  --work-root "$work" \
  --expected-file file.txt \
  --expected-sha256 "$snapraid_recovery_hash" \
  --evidence "$repo/dist/validation/snapraid-replacement.json"

stat --format='%n size=%s blocks=%b' \
  "$managed/data-2/snapraid.content" \
  "$managed/parity-1/snapraid.parity" \
  "$managed/parity-2/snapraid.parity"
findmnt -rn -T "$managed/data-1" >/dev/null 2>&1 && umount "$managed/data-1" || true
umount "$managed/data-2"
umount "$managed/parity-1"
umount "$managed/parity-2"
