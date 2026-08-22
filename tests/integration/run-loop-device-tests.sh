#!/usr/bin/env bash
set -euo pipefail
[[ "$(id -u)" -eq 0 ]] || { echo "requires root in a disposable runner" >&2; exit 1; }
[[ -f /.hoardarr-disposable-runner ]] || {
  echo "refusing loop tests: disposable runner marker is missing" >&2
  exit 1
}
work="$(mktemp -d -t hoardarr-loop.XXXXXXXX)"
loops=()
md_device=""
zpool_name=""
cleanup() {
  [[ -z "$zpool_name" ]] || zpool destroy -f "$zpool_name" 2>/dev/null || true
  [[ -z "$md_device" ]] || mdadm --stop "$md_device" 2>/dev/null || true
  for loop in "${loops[@]}"; do
    findmnt -rn -S "$loop" -o TARGET | while IFS= read -r target; do umount -- "$target" 2>/dev/null || true; done
    losetup -d -- "$loop" 2>/dev/null || true
  done
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
mkfs.ext4 -F -E lazy_itable_init=1,lazy_journal_init=1 "$loop"
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
zpool create -f -o ashift=12 -O mountpoint=none -O compression=lz4 "$zpool_name" raidz2 "${zfs_members[@]}"
zfs create -o mountpoint="$work/zfs" -o recordsize=1M "$zpool_name/media"
touch "$work/zfs/zfs-verified"
zfs snapshot "$zpool_name/media@initial"
zpool scrub "$zpool_name"
zpool status "$zpool_name" | grep -q 'state: ONLINE'
zpool destroy -f "$zpool_name"
zpool_name=""

make_loop snap-data 512M
snap_data="$created_loop"
make_loop snap-parity 512M
snap_parity="$created_loop"
for member in "$snap_data" "$snap_parity"; do assert_test_loop "$member"; mkfs.ext4 -F "$member"; done
mkdir "$work/snap-data" "$work/snap-parity"
mount "$snap_data" "$work/snap-data"
mount "$snap_parity" "$work/snap-parity"
printf 'test payload\n' >"$work/snap-data/file.txt"
cat >"$work/snapraid.conf" <<EOF
parity $work/snap-parity/snapraid.parity
content $work/snap-data/snapraid.content
data d1 $work/snap-data
EOF
snapraid -c "$work/snapraid.conf" sync
snapraid -c "$work/snapraid.conf" status | grep -Eq 'No error|100%'
umount "$work/snap-data" "$work/snap-parity"
