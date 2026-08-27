#!/usr/bin/env bash
set -euo pipefail
trap 'rc=$?; printf "managed-zvol lifecycle failed at line %s (rc=%s)\n" "$LINENO" "$rc" >&2' ERR

[[ "$(id -u)" -eq 0 ]] || { echo "requires root in a disposable runner" >&2; exit 1; }
[[ "${GITHUB_ACTIONS:-}" == "true" ]] || {
  echo "refusing managed-zvol test outside GitHub Actions" >&2
  exit 1
}
[[ -f /.hoardarr-disposable-runner ]] || {
  echo "refusing managed-zvol test: disposable runner marker is missing" >&2
  exit 1
}

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python="${HOARDARR_TEST_PYTHON:-$repo/backend/.venv/bin/python}"
[[ -x "$python" ]]
run_id="${GITHUB_RUN_ID:-$$}"
[[ "$run_id" =~ ^[0-9]+$ ]]
work="$(mktemp -d -t hoardarr-managed-zvol.XXXXXXXX)"
touch "$work/.hoardarr-owned"
pool="hda4_${run_id}_$$"
zvol="managed_lio"
zvol_size_bytes=$((256 * 1024 * 1024))
service_id="66666666-6666-4666-8666-666666666666"
volume_id="77777777-7777-4777-8777-777777777777"
chap_fixture="A4$(printf '%s' "$service_id" | sha256sum | cut -c1-30)"
target_iqn="iqn.2026-08.local.hoardarr:a4-${run_id}-$$"
initiator_iqn="iqn.2026-08.local.hoardarr:a4-initiator-${run_id}-$$"
portal="127.0.0.5"
chap_user="hoardarr_a4"
backstore="hoardarr-zvol-$(printf '%s' "$service_id" | sha256sum | cut -c1-24)"
state_file="$work/connectivity/services.json"
mountpoint="$work/mount"
initiator_backup="$work/initiatorname.backup"
initiator_had_original=false
loops=()
images=()
logged_in=false
mounted=false
pool_created=false
cleanup_invocations=0
cleanup_targetcli_mutations=0
cleanup_pool_destroys=0
cleanup_loop_detaches=0
cleanup_complete=false

safe_work_root() {
  [[ -n "$work" && "$work" == /tmp/hoardarr-managed-zvol.* ]]
  [[ -d "$work" && -f "$work/.hoardarr-owned" ]]
  [[ "$(stat -c %u "$work")" -eq 0 ]]
}

assert_owned_loop() {
  local candidate="$1" expected="$2" backing
  [[ "$candidate" =~ ^/dev/loop[0-9]+$ ]]
  [[ -f "$expected" && "$expected" == "$work"/disk[1-6].img ]]
  backing="$(losetup --noheadings --output BACK-FILE "$candidate" | xargs realpath)"
  [[ "$backing" == "$(realpath "$expected")" ]]
}

target_exists() {
  targetcli /iscsi ls 2>/dev/null | grep -Fq -- "$target_iqn"
}

backstore_exists() {
  targetcli /backstores/block ls 2>/dev/null | grep -Fq -- "$backstore"
}

cleanup_resources() {
  set +e
  cleanup_invocations=$((cleanup_invocations + 1))
  if [[ "$mounted" == true ]] && mountpoint -q "$mountpoint"; then
    umount -- "$mountpoint"
  fi
  mounted=false
  if [[ "$logged_in" == true ]]; then
    iscsiadm -m node -T "$target_iqn" -p "$portal:3260" --logout >/dev/null 2>&1 || true
  fi
  logged_in=false
  iscsiadm -m node -T "$target_iqn" -p "$portal:3260" -o delete >/dev/null 2>&1 || true
  if target_exists; then
    targetcli "/iscsi/$target_iqn" delete >/dev/null 2>&1 || true
    cleanup_targetcli_mutations=$((cleanup_targetcli_mutations + 1))
  fi
  if backstore_exists; then
    targetcli "/backstores/block/$backstore" delete >/dev/null 2>&1 || true
    cleanup_targetcli_mutations=$((cleanup_targetcli_mutations + 1))
  fi
  targetcli saveconfig >/dev/null 2>&1 || true
  if [[ "$pool_created" == true ]] && zpool list -H -o name "$pool" >/dev/null 2>&1; then
    zpool destroy -f "$pool" >/dev/null 2>&1 || true
    cleanup_pool_destroys=$((cleanup_pool_destroys + 1))
  fi
  pool_created=false
  for index in "${!loops[@]}"; do
    loop="${loops[$index]}"
    image="${images[$index]}"
    if losetup "$loop" >/dev/null 2>&1 && assert_owned_loop "$loop" "$image"; then
      losetup -d -- "$loop" >/dev/null 2>&1 || true
      cleanup_loop_detaches=$((cleanup_loop_detaches + 1))
    fi
  done
  if [[ "$initiator_had_original" == true && -f "$initiator_backup" ]]; then
    install -m 600 "$initiator_backup" /etc/iscsi/initiatorname.iscsi
  else
    rm -f -- /etc/iscsi/initiatorname.iscsi
  fi
  if safe_work_root; then
    rm -f -- "$work/.hoardarr-owned"
    rmdir -- "$work" 2>/dev/null || rm -rf -- "$work"
  fi
  rm -f -- /.hoardarr-disposable-runner
}
trap cleanup_resources EXIT

helper() {
  local action="$1"
  HOARDARR_A4_CHAP_FIXTURE="$chap_fixture" \
    "$python" "$repo/tests/integration/managed_zvol_lio_lifecycle.py" lifecycle \
    --action "$action" --state-file "$state_file" --service-id "$service_id" \
    --volume-id "$volume_id" --pool "$pool" --zvol "$zvol" \
    --size-bytes "$zvol_size_bytes" --target-iqn "$target_iqn" --portal "$portal" \
    --initiator-iqn "$initiator_iqn" --chap-user "$chap_user"
}

safe_work_root
"$python" "$repo/tests/integration/managed_zvol_lio_lifecycle.py" guard \
  --effective-uid "$(id -u)" --github-actions "$GITHUB_ACTIONS" \
  --marker-exists true --work-root "$work" >/dev/null

modprobe loop
modprobe zfs
modprobe target_core_mod
modprobe iscsi_target_mod
mkdir -p "$mountpoint" "$(dirname "$state_file")"

loop_pairs=()
for number in 1 2 3 4 5 6; do
  image="$work/disk${number}.img"
  truncate -s 768M "$image"
  loop="$(losetup --find --show "$image")"
  images+=("$image")
  loops+=("$loop")
  assert_owned_loop "$loop" "$image"
  [[ "$(blockdev --getsize64 "$loop")" -eq "$(stat -c %s "$image")" ]]
  [[ -z "$(findmnt -rn -S "$loop" -o TARGET)" ]]
  [[ "$(lsblk -dnro TYPE "$loop")" == "loop" ]]
  [[ -z "$(find "/sys/class/block/$(basename "$loop")/holders" -mindepth 1 -maxdepth 1 -print -quit)" ]]
  [[ -z "$(wipefs -n "$loop" 2>/dev/null)" ]]
  ! zpool status -P 2>/dev/null | grep -Fq -- "$loop"
  loop_pairs+=(--loop-pair "$loop=$(realpath "$image")")
done
"$python" "$repo/tests/integration/managed_zvol_lio_lifecycle.py" guard \
  --effective-uid "$(id -u)" --github-actions "$GITHUB_ACTIONS" \
  --marker-exists true --work-root "$work" "${loop_pairs[@]}" >/dev/null

zpool create -f -o ashift=12 -O mountpoint=none -O compression=off "$pool" raidz2 "${loops[@]}"
pool_created=true
[[ "$(zpool get -Hp -o value health "$pool")" == "ONLINE" ]]
[[ "$(zpool get -Hp -o value ashift "$pool")" == "12" ]]
[[ "$(zpool status -P "$pool" | grep -Ec '^[[:space:]]+raidz2-[0-9]+[[:space:]]')" -eq 1 ]]
raidz2_member_count=0
for loop in "${loops[@]}"; do
  [[ "$(zpool status -P "$pool" | grep -Fc -- "$loop")" -eq 1 ]]
  raidz2_member_count=$((raidz2_member_count + 1))
done
[[ "$raidz2_member_count" -eq 6 ]]
pool_guid="$(zpool get -Hp -o value guid "$pool")"
zfs create -V "$zvol_size_bytes" -o volblocksize=16K "$pool/$zvol"
zvol_device="/dev/zvol/$pool/$zvol"
for _attempt in $(seq 1 30); do [[ -b "$zvol_device" ]] && break; udevadm settle; sleep 1; done
[[ -b "$zvol_device" ]]
[[ "$(blockdev --getsize64 "$zvol_device")" -eq "$zvol_size_bytes" ]]
zvol_used_before_apply="$(zfs get -Hp -o value used "$pool/$zvol")"
zvol_size_before_apply="$(zfs get -Hp -o value volsize "$pool/$zvol")"

targetcli saveconfig >/dev/null
initial_json="$(helper apply)"
[[ "$(jq -r .state <<<"$initial_json")" == "active" ]]
[[ "$(jq -r .counters.targetcli <<<"$initial_json")" -eq 1 ]]
[[ "$(jq -r .counters.state_writes <<<"$initial_json")" -eq 1 ]]
[[ "$(jq -r .counters.readbacks <<<"$initial_json")" -eq 2 ]]
[[ "$(jq -r '.readback.block_plugin and .readback.lun_zero and .readback.portal_exact and .readback.acl_exact and .readback.chap_configured and .readback.chap_user_matches and .readback.chap_secret_matches and .readback.device_matches_binding' <<<"$initial_json")" == "true" ]]
[[ "$(zfs get -Hp -o value used "$pool/$zvol")" == "$zvol_used_before_apply" ]]
[[ "$(zfs get -Hp -o value volsize "$pool/$zvol")" == "$zvol_size_before_apply" ]]
independent_json="$(helper readback)"
initial_digest="$(jq -r .readback.evidence_sha256 <<<"$initial_json")"
[[ "$(jq -r .readback.evidence_sha256 <<<"$independent_json")" == "$initial_digest" ]]

if [[ -f /etc/iscsi/initiatorname.iscsi ]]; then
  cp --preserve=mode /etc/iscsi/initiatorname.iscsi "$initiator_backup"
  initiator_had_original=true
fi
install -m 600 /dev/null /etc/iscsi/initiatorname.iscsi
printf 'InitiatorName=%s\n' "$initiator_iqn" >/etc/iscsi/initiatorname.iscsi
systemctl restart iscsid.service
iscsiadm -m discovery -t sendtargets -p "$portal:3260" >/dev/null
iscsiadm -m node -T "$target_iqn" -p "$portal:3260" --op update -n node.session.auth.authmethod -v CHAP
iscsiadm -m node -T "$target_iqn" -p "$portal:3260" --op update -n node.session.auth.username -v "$chap_user"
iscsiadm -m node -T "$target_iqn" -p "$portal:3260" --op update -n node.session.auth.password -v "$chap_fixture"
iscsiadm -m node -T "$target_iqn" -p "$portal:3260" --login >/dev/null
logged_in=true
by_path="/dev/disk/by-path/ip-${portal}:3260-iscsi-${target_iqn}-lun-0"
for _attempt in $(seq 1 30); do [[ -e "$by_path" ]] && break; udevadm settle; sleep 1; done
[[ -L "$by_path" ]]
lun_device="$(readlink -f -- "$by_path")"
[[ -b "$lun_device" && "$lun_device" != "$zvol_device" ]]
mkfs.ext4 -F -E lazy_itable_init=1,lazy_journal_init=1,nodiscard "$by_path" >/dev/null
mount "$by_path" "$mountpoint"
mounted=true
dd if=/dev/zero of="$mountpoint/a4-payload.bin" bs=1M count=8 status=none
printf 'Hoardarr managed zvol standalone lifecycle\n' >"$mountpoint/a4-marker.txt"
sync
data_hash_before="$(sha256sum "$mountpoint/a4-payload.bin" | awk '{print $1}')"
umount -- "$mountpoint"
mounted=false
iscsiadm -m node -T "$target_iqn" -p "$portal:3260" --logout >/dev/null
logged_in=false
udevadm settle
raw_hash_before="$(sha256sum "$zvol_device" | awk '{print $1}')"

state_hash_before="$(sha256sum "$state_file" | awk '{print $1}')"
state_mtime_before="$(stat -c %Y "$state_file")"
idempotent_json="$(helper apply)"
[[ "$(jq -r .already_active <<<"$idempotent_json")" == "true" ]]
[[ "$(jq -r .counters.targetcli <<<"$idempotent_json")" -eq 0 ]]
[[ "$(jq -r .counters.state_writes <<<"$idempotent_json")" -eq 0 ]]
[[ "$(jq -r .readback.evidence_sha256 <<<"$idempotent_json")" == "$initial_digest" ]]
[[ "$(sha256sum "$state_file" | awk '{print $1}')" == "$state_hash_before" ]]
[[ "$(stat -c %Y "$state_file")" == "$state_mtime_before" ]]
[[ "$(sha256sum "$zvol_device" | awk '{print $1}')" == "$raw_hash_before" ]]

rm -f -- "$state_file"
reconciled_json="$(helper apply)"
[[ "$(jq -r .reconciled_existing <<<"$reconciled_json")" == "true" ]]
[[ "$(jq -r .counters.targetcli <<<"$reconciled_json")" -eq 0 ]]
[[ "$(jq -r .counters.state_writes <<<"$reconciled_json")" -eq 1 ]]
[[ "$(jq -r .readback.evidence_sha256 <<<"$reconciled_json")" == "$initial_digest" ]]
[[ "$(sha256sum "$zvol_device" | awk '{print $1}')" == "$raw_hash_before" ]]

targetcli saveconfig >/dev/null
systemctl restart rtslib-fb-targetctl.service
restart_readback_attempts=0
restart_json=""
for _attempt in $(seq 1 30); do
  restart_readback_attempts=$((restart_readback_attempts + 1))
  if restart_json="$(helper readback 2>/dev/null)"; then break; fi
  sleep 1
done
[[ -n "$restart_json" ]]
[[ "$(jq -r .readback.evidence_sha256 <<<"$restart_json")" == "$initial_digest" ]]
post_restart_json="$(helper apply)"
[[ "$(jq -r .already_active <<<"$post_restart_json")" == "true" ]]
[[ "$(jq -r .counters.targetcli <<<"$post_restart_json")" -eq 0 ]]
[[ "$(jq -r .counters.state_writes <<<"$post_restart_json")" -eq 0 ]]
[[ "$(jq -r .readback.evidence_sha256 <<<"$post_restart_json")" == "$initial_digest" ]]
[[ "$(sha256sum "$zvol_device" | awk '{print $1}')" == "$raw_hash_before" ]]

remove_json="$(helper remove)"
[[ "$(jq -r '.state == "removed" and (.backing_data_deleted | not) and .readback.target_absent and .readback.backstore_absent' <<<"$remove_json")" == "true" ]]
[[ "$(jq -r .counters.targetcli <<<"$remove_json")" -eq 1 ]]
[[ "$(jq -r .counters.state_writes <<<"$remove_json")" -eq 1 ]]
if iscsiadm -m node -T "$target_iqn" -p "$portal:3260" --login >/dev/null 2>&1; then
  logged_in=true
  echo "removed target accepted a login" >&2
  exit 1
fi
[[ -b "$zvol_device" ]]
mount -o ro,noload "$zvol_device" "$mountpoint"
mounted=true
data_hash_after="$(sha256sum "$mountpoint/a4-payload.bin" | awk '{print $1}')"
[[ "$data_hash_after" == "$data_hash_before" ]]
umount -- "$mountpoint"
mounted=false
reject_json="$(helper reject-delete)"
[[ "$(jq -r .rejected_before_mutation <<<"$reject_json")" == "true" ]]
[[ "$(jq -r '.counters.targetcli + .counters.state_reads + .counters.state_writes + .counters.readbacks' <<<"$reject_json")" -eq 0 ]]
[[ "$(zfs get -Hp -o value volsize "$pool/$zvol")" == "$zvol_size_bytes" ]]

cleanup_resources
trap - EXIT
[[ "$cleanup_targetcli_mutations" -eq 0 ]]
[[ "$cleanup_pool_destroys" -eq 1 ]]
[[ "$cleanup_loop_detaches" -eq 6 ]]
! target_exists
! backstore_exists
! zpool list -H -o name "$pool" >/dev/null 2>&1
for loop in "${loops[@]}"; do ! losetup "$loop" >/dev/null 2>&1; done
[[ ! -e "$work" && ! -e /.hoardarr-disposable-runner ]]
cleanup_complete=true

mkdir -p "$repo/dist/validation"
jq -n \
  --arg workflow "storage-integration" --arg run_id "$run_id" \
  --arg pool_guid_sha256 "$(printf '%s' "$pool_guid" | sha256sum | cut -d' ' -f1)" \
  --arg pool_identity_sha256 "$(printf '%s' "$pool" | sha256sum | cut -d' ' -f1)" \
  --arg zvol_identity_sha256 "$(printf '%s' "$pool/$zvol" | sha256sum | cut -d' ' -f1)" \
  --arg target_identity_sha256 "$(printf '%s' "$target_iqn" | sha256sum | cut -d' ' -f1)" \
  --arg initiator_identity_sha256 "$(printf '%s' "$initiator_iqn" | sha256sum | cut -d' ' -f1)" \
  --arg initial_digest "$(jq -r .readback.evidence_sha256 <<<"$initial_json")" \
  --arg independent_digest "$(jq -r .readback.evidence_sha256 <<<"$independent_json")" \
  --arg idempotent_digest "$(jq -r .readback.evidence_sha256 <<<"$idempotent_json")" \
  --arg reconciled_digest "$(jq -r .readback.evidence_sha256 <<<"$reconciled_json")" \
  --arg restart_digest "$(jq -r .readback.evidence_sha256 <<<"$restart_json")" \
  --arg data_hash_before "$data_hash_before" --arg data_hash_after "$data_hash_after" \
  --argjson restart_readback_attempts "$restart_readback_attempts" \
  --argjson cleanup_invocations "$cleanup_invocations" \
  --argjson cleanup_pool_destroys "$cleanup_pool_destroys" \
  --argjson cleanup_loop_detaches "$cleanup_loop_detaches" \
  '{schema_version:1,classification:"VERIFIED IN ISOLATION",workflow:$workflow,
    job:"managed-zvol-lio-lifecycle",run_id:$run_id,
    topology:{loop_count:6,raidz2_vdev_count:1,raidz2_member_count:6,zvol_count:1,
      raw_loop_paths_emitted:false,pool_guid_sha256:$pool_guid_sha256,
      pool_identity_sha256:$pool_identity_sha256,zvol_identity_sha256:$zvol_identity_sha256,
      pool_health_online:true,ashift_12:true,zvol_size_equal:true},
    identities:{target_sha256:$target_identity_sha256,initiator_sha256:$initiator_identity_sha256},
    executor:{production_used:true,initial_apply_active:true,block_backstore:true,lun_zero:true,
      portal_exact:true,acl_exact:true,chap_equality_booleans:true,idempotent_apply:true,
      state_only_recovery:true,restart_restored:true,remove_absent:true,
      destructive_delete_rejected_before_mutation:true,
      evidence_digests:{initial:$initial_digest,independent:$independent_digest,
        idempotent:$idempotent_digest,reconciled:$reconciled_digest,restart:$restart_digest}},
    initiator:{discovery:true,login:true,by_path_identity:true,bounded_io:true,logout:true,
      post_remove_login_rejected:true,data_sha256_before:$data_hash_before,
      data_sha256_after:$data_hash_after,data_hash_equal:($data_hash_before==$data_hash_after)},
    retention:{target_absent:true,backstore_absent:true,pool_retained_until_cleanup:true,
      zvol_retained_until_cleanup:true,filesystem_retained:true,backing_retained:true},
    counters:{production_apply_calls:4,production_remove_calls:2,production_targetcli_calls:2,
      production_state_writes:3,target_persistence_restarts:1,
      restart_readback_attempts:$restart_readback_attempts,cleanup_invocations:$cleanup_invocations,
      cleanup_targetcli_mutations:0,cleanup_pool_destroys:$cleanup_pool_destroys,
      cleanup_loop_detaches:$cleanup_loop_detaches},
    prohibited_actions:{physical_media:0,host_or_vm:0,network_storage:0,multipath:0,
      controller_or_ha:0,credential_reads:0,raw_saveconfig_emitted:0},cleanup_complete:true}' \
  >"$repo/dist/validation/managed-zvol-lio-lifecycle.json"

echo "Hoardarr managed ZFS zvol/LIO lifecycle verified in isolation"
