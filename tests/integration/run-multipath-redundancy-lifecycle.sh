#!/usr/bin/env bash
set -euo pipefail
trap 'rc=$?; printf "FAILED at line %s: %s (rc=%s)\n" "$LINENO" "$BASH_COMMAND" "$rc" >&2; multipath -ll 2>&1 || true; ls -l /dev/mapper 2>&1 || true' ERR

[[ "$(id -u)" -eq 0 ]] || { echo "requires root in a disposable runner" >&2; exit 1; }
[[ -f /.hoardarr-disposable-runner ]] || {
  echo "refusing multipath test: disposable runner marker is missing" >&2
  exit 1
}

work="$(mktemp -d -t hoardarr-multipath.XXXXXXXX)"
target_iqn="iqn.2026-08.local.hoardarr:ci-${GITHUB_RUN_ID:-$$}"
backstore="hoardarr_ci_${GITHUB_RUN_ID:-$$}"
backing="$work/lun.img"
mountpoint="$work/media"
device_mountpoint="$work/device"
database="$work/hoardarr.sqlite3"
transactions="$work/transactions"
initiator="iqn.2026-08.local.hoardarr:initiator-${GITHUB_RUN_ID:-$$}"
portals=(127.0.0.2 127.0.0.3 127.0.0.4)

cleanup() {
  set +e
  jobs -pr | xargs -r kill 2>/dev/null || true
  sync
  mountpoint -q "$mountpoint" && umount -- "$mountpoint"
  mountpoint -q "$device_mountpoint" && umount -- "$device_mountpoint"
  for portal in "${portals[@]}"; do
    iscsiadm -m node -T "$target_iqn" -p "$portal:3260" --logout >/dev/null 2>&1 || true
    iscsiadm -m node -T "$target_iqn" -p "$portal:3260" -o delete >/dev/null 2>&1 || true
  done
  multipath -f "${wwid:-missing}" >/dev/null 2>&1 || true
  targetcli "/iscsi/$target_iqn" delete >/dev/null 2>&1 || true
  targetcli "/backstores/fileio/$backstore" delete >/dev/null 2>&1 || true
  rm -f /etc/multipath/conf.d/hoardarr-ci.conf
  rm -rf -- "$work"
}
trap cleanup EXIT

wait_path() {
  local portal="$1" link attempt
  link="/dev/disk/by-path/ip-${portal}:3260-iscsi-${target_iqn}-lun-0"
  for attempt in $(seq 1 60); do
    [[ -e "$link" ]] && { readlink -f -- "$link"; return 0; }
    sleep 1
  done
  echo "iSCSI path did not appear for $portal" >&2
  return 1
}

login_portal() {
  local portal="$1"
  iscsiadm -m discovery -t sendtargets -p "$portal:3260" >/dev/null
  iscsiadm -m node -T "$target_iqn" -p "$portal:3260" --login >/dev/null
  wait_path "$portal"
}

logout_portal() {
  local portal="$1"
  iscsiadm -m node -T "$target_iqn" -p "$portal:3260" --logout >/dev/null
  udevadm settle
}

run_helper() {
  local action="$1"; shift
  "$HOARDARR_TEST_PYTHON" tests/integration/multipath_lifecycle.py "$action" \
    --database "$database" --transaction-root "$transactions" --wwid "$wwid" \
    --mountpoint "$mountpoint" --device-mountpoint "$device_mountpoint" \
    --filesystem-uuid "$filesystem_uuid" "$@"
}

modprobe target_core_file
modprobe iscsi_target_mod
mkdir -p "$mountpoint" "$device_mountpoint" /etc/multipath/conf.d
systemctl stop iscsid.service iscsid.socket >/dev/null 2>&1 || true
printf 'InitiatorName=%s\n' "$initiator" >/etc/iscsi/initiatorname.iscsi
targetcli /backstores/fileio create name="$backstore" file_or_dev="$backing" size=3G write_back=false >/dev/null
targetcli /iscsi create "$target_iqn" >/dev/null
targetcli "/iscsi/$target_iqn/tpg1/luns" create "/backstores/fileio/$backstore" >/dev/null
targetcli "/iscsi/$target_iqn/tpg1/acls" create wwn="$initiator" >/dev/null
targetcli "/iscsi/$target_iqn/tpg1/portals" delete 0.0.0.0 3260 >/dev/null 2>&1 || true
for portal in "${portals[@]}"; do
  targetcli "/iscsi/$target_iqn/tpg1/portals" create "$portal" 3260 >/dev/null
done
systemctl start iscsid

# Day 1: create and use the filesystem through one path only.
path_a="$(login_portal "${portals[0]}")"
wwid="$(/lib/udev/scsi_id --whitelisted --device="$path_a")"
[[ -n "$wwid" ]]
mkfs.ext4 -F -E lazy_itable_init=1,lazy_journal_init=1,nodiscard "$path_a" >/dev/null
filesystem_uuid="$(blkid -s UUID -o value "$path_a")"
mount "$path_a" "$device_mountpoint"
mount --bind "$device_mountpoint" "$mountpoint"
printf 'Plex and ARR keep using %s\n' "$mountpoint" >"$work/share-path.txt"
mkdir -p "$mountpoint/Movies" "$mountpoint/TV"
dd if=/dev/urandom of="$mountpoint/Movies/existing.bin" bs=1M count=64 status=none
sha256sum "$mountpoint/Movies/existing.bin" >"$work/existing.sha256"
register_json="$(run_helper register --path "${portals[0]}=$path_a")"
storage_id="$(jq -r .storage_entity_id <<<"$register_json")"

cat >/etc/multipath/conf.d/hoardarr-ci.conf <<EOF
defaults {
    find_multipaths no
    user_friendly_names no
}
blacklist_exceptions {
    wwid "$wwid"
}
multipaths {
    multipath {
        wwid "$wwid"
        alias "$wwid"
    }
}
EOF
systemctl restart multipathd

# Later: add Controller B and let Hoardarr move only the access layer.
path_b="$(login_portal "${portals[1]}")"
udevadm settle
multipath -r >/dev/null
add_json="$(run_helper add --path "${portals[0]}=$path_a" --path "${portals[1]}=$path_b")"
[[ "$(jq -r .storage_entity_id <<<"$add_json")" == "$storage_id" ]]
[[ "$(jq -r .filesystem_uuid <<<"$add_json")" == "$filesystem_uuid" ]]
[[ "$(jq -r .mountpoint <<<"$add_json")" == "$mountpoint" ]]
[[ "$(findmnt -rn -T "$mountpoint" -o SOURCE)" == "/dev/mapper/$wwid" ]]
sha256sum -c "$work/existing.sha256" >/dev/null

# Replace B with C. The mapper and application mount stay online.
path_c="$(login_portal "${portals[2]}")"
udevadm settle
multipath -r >/dev/null
path_a="$(wait_path "${portals[0]}")"
path_b="$(wait_path "${portals[1]}")"
path_c="$(wait_path "${portals[2]}")"
replace_json="$(run_helper replace --remove-controller "${portals[1]}" \
  --path "${portals[0]}=$path_a" --path "${portals[1]}=$path_b" \
  --path "${portals[2]}=$path_c")"
[[ "$(jq -r .storage_entity_id <<<"$replace_json")" == "$storage_id" ]]
[[ "$(findmnt -rn -T "$mountpoint" -o SOURCE)" == "/dev/mapper/$wwid" ]]
logout_portal "${portals[1]}"

# Continuous application IO survives loss and recovery of each serving path.
fio --name=failover --filename="$mountpoint/failover.bin" --size=384M \
  --rw=randrw --bs=128k --time_based=1 --runtime=35 --direct=1 \
  --verify=crc32c --verify_fatal=1 --group_reporting >/dev/null &
fio_pid=$!
sleep 7
logout_portal "${portals[0]}"
sleep 7
path_a="$(login_portal "${portals[0]}")"
sleep 7
logout_portal "${portals[2]}"
wait "$fio_pid"
sync
sha256sum -c "$work/existing.sha256" >/dev/null
[[ "$(blkid -s UUID -o value "/dev/mapper/$wwid")" == "$filesystem_uuid" ]]

# multipathd restart must not change the mounted object or data.
systemctl restart multipathd
sleep 2
[[ "$(findmnt -rn -T "$mountpoint" -o SOURCE)" == "/dev/mapper/$wwid" ]]
sha256sum -c "$work/existing.sha256" >/dev/null

# Restore C, then deliberately remove redundancy without touching filesystem data.
path_c="$(login_portal "${portals[2]}")"
udevadm settle
multipath -r >/dev/null
path_a="$(wait_path "${portals[0]}")"
path_c="$(wait_path "${portals[2]}")"
remove_json="$(run_helper remove --remove-controller "${portals[2]}" \
  --path "${portals[0]}=$path_a" --path "${portals[2]}=$path_c")"
[[ "$(jq -r .storage_entity_id <<<"$remove_json")" == "$storage_id" ]]
[[ "$(jq -r .filesystem_uuid <<<"$remove_json")" == "$filesystem_uuid" ]]
[[ "$(jq -r .mountpoint <<<"$remove_json")" == "$mountpoint" ]]
sha256sum -c "$work/existing.sha256" >/dev/null

mkdir -p dist/validation
jq -n \
  --arg storage_id "$storage_id" \
  --arg filesystem_uuid "$filesystem_uuid" \
  --arg mountpoint "$mountpoint" \
  --arg wwid "$wwid" \
  --argjson register "$register_json" \
  --argjson add "$add_json" \
  --argjson replace "$replace_json" \
  --argjson remove "$remove_json" \
  '{status:"verified_in_isolation", storage_entity_id:$storage_id,
    filesystem_uuid_before:$filesystem_uuid, filesystem_uuid_after:$filesystem_uuid,
    mountpoint_before:$mountpoint, mountpoint_after:$mountpoint, wwid:$wwid,
    register:$register, add:$add, replace:$replace, remove:$remove,
    data_hash_preserved:true, failover_io_completed:true,
    multipathd_restart_preserved_mount:true, formatting_commands_after_day_one:0}' \
  >dist/validation/multipath-redundancy-lifecycle.json

echo "Hoardarr single-path to multipath lifecycle verified in isolation"
