#!/usr/bin/env bash
set -euo pipefail

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
chmod 700 "$work"
receipt_dir="$repo/dist/validation"
receipt="$receipt_dir/managed-zvol-lio-lifecycle.json"
pool="hda5_${run_id}_$$"
zvol="managed_lio"
zvol_size_bytes=$((256 * 1024 * 1024))
service_id="66666666-6666-4666-8666-666666666666"
volume_id="77777777-7777-4777-8777-777777777777"
chap_fixture="A5$(printf '%s' "$service_id" | sha256sum | cut -c1-30)"
target_iqn="iqn.2026-08.local.hoardarr:a5-${run_id}-$$"
initiator_iqn="iqn.2026-08.local.hoardarr:a5-initiator-${run_id}-$$"
portal="127.0.0.5"
chap_user="hoardarr_a5"
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
pool_created_once=false
zvol_created=false
node_created=false
classification="HARNESS_ERROR"
failure_code="UNCLASSIFIED_HARNESS_STOP"
failure_line=0
original_lifecycle_status=1
login_attempt_count=0
login_status=-1
initial_apply_passed=false
prelogin_readback_passed=false
tpg_authentication_json='{"schema_version":1,"observed":false,"enabled":null}'
bounded_io_passed=false
idempotent_passed=false
reconciled_passed=false
restart_passed=false
remove_passed=false
backing_retained=false
persistence_control_plane=false
payload_verification_attempted=false
payload_verification_matched=false
parity_json='{"schema_version":1,"exact":false,"mismatch":"NOT_RUN","record_count":0,"auth_method_chap":false,"username_match":false,"password_match":false,"record_count_exact":false,"record_safe":false,"username_length":0,"password_length":0,"target_identity_sha256":"","initiator_identity_sha256":"","parity_sha256":""}'
diagnostic_json='{"schema_version":3,"status":-1,"streams":[],"ordered_classifications":[],"diagnosed_class":null,"protocol_status":{"observed":false,"status_class":null,"status_detail":null,"meaning":"NONE","source_label":null}}'
initial_json='{}'
independent_json='{}'
idempotent_json='{}'
reconciled_json='{}'
restart_json='{}'
remove_json='{}'
data_hash_before=""
data_hash_after=""
raw_integrity_stages=()
raw_integrity_baseline_equal=()
raw_integrity_previous_equal=()
raw_integrity_previous_hash=""
raw_integrity_first_mismatch_stage="NONE"
raw_integrity_final_comparison_attempted=false
raw_integrity_timeline_json='{"schema_version":1,"checkpoints":[],"first_mismatch_stage":"NONE","final_comparison_attempted":false}'
pool_guid=""
cleanup_started=false
cleanup_first_failure="NONE"
phase_names=()
phase_attempted=()
phase_statuses=()
phase_exits=()
phase_timeouts=()
phase_postconditions=()
loop_release_json="[]"
loop_holder_limit=8

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

loop_mapping_state() {
  local candidate="$1" expected="$2" probe stderr_probe rc backing stdout_size stderr_size
  probe="$(mktemp "$work/.loop-mapping.XXXXXX")"
  stderr_probe="$(mktemp "$work/.loop-mapping-stderr.XXXXXX")"
  chmod 600 "$probe"
  chmod 600 "$stderr_probe"
  set +e
  (ulimit -f 8; timeout --signal=TERM --kill-after=1s 2s losetup --noheadings --output BACK-FILE "$candidate" >"$probe" 2>"$stderr_probe")
  rc=$?
  set -e
  stdout_size="$(stat -c %s "$probe")"; stderr_size="$(stat -c %s "$stderr_probe")"
  if [[ "$rc" -eq 1 && "$stdout_size" -eq 0 && "$stderr_size" -le 16384 ]] && grep -Eqi '^losetup:.*no such device' "$stderr_probe"; then
    rm -f -- "$probe" "$stderr_probe"; printf '%s' "ABSENT"; return
  fi
  if [[ "$rc" -ne 0 || "$stdout_size" -eq 0 || "$stdout_size" -gt 16384 || "$stderr_size" -gt 16384 ]]; then
    rm -f -- "$probe" "$stderr_probe"; printf '%s' "UNSAFE"; return
  fi
  backing="$(xargs realpath <"$probe" 2>/dev/null || true)"
  rm -f -- "$probe" "$stderr_probe"
  if [[ "$backing" == "$(realpath "$expected")" ]]; then printf '%s' "ORIGINAL_OWNED"; else printf '%s' "DIFFERENT_BACKING"; fi
}

collect_loop_holders() {
  local candidate="$1" holder_dir holder name probe rc
  loop_holder_count=0
  loop_holder_hashes_json="[]"
  loop_holder_probe_state="NOT_APPLICABLE"
  holder_dir="/sys/block/${candidate##*/}/holders"
  [[ -d "$holder_dir" ]] || { loop_holder_probe_state="PROBE_ERROR"; return 1; }
  probe="$(mktemp "$work/.loop-holders.XXXXXX")"
  chmod 600 "$probe"
  set +e
  (ulimit -f 8; timeout --signal=TERM --kill-after=1s 2s find "$holder_dir" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | sort | head -n $((loop_holder_limit + 1)) >"$probe")
  rc=$?
  set -e
  [[ "$rc" -eq 0 && "$(stat -c %s "$probe")" -le 16384 ]] || { loop_holder_probe_state="PROBE_ERROR"; rm -f -- "$probe"; return 1; }
  mapfile -t loop_holder_names <"$probe"
  rm -f -- "$probe"
  [[ "${#loop_holder_names[@]}" -le "$loop_holder_limit" ]] || { loop_holder_probe_state="OVER_LIMIT"; return 1; }
  for holder in "${loop_holder_names[@]}"; do
    [[ "$holder" =~ ^[A-Za-z0-9_.:-]+$ ]] || { loop_holder_probe_state="INVALID_NAME"; return 1; }
  done
  loop_holder_count="${#loop_holder_names[@]}"
  if [[ "$loop_holder_count" -gt 0 ]]; then
    loop_holder_hashes_json="["
    for name in "${loop_holder_names[@]}"; do
      [[ "$loop_holder_hashes_json" == "[" ]] || loop_holder_hashes_json+=","
      loop_holder_hashes_json+="\"$(printf '%s' "$name" | sha256sum | cut -d' ' -f1)\""
    done
    loop_holder_hashes_json+="]"
  fi
  loop_holder_probe_state="COMPLETE"
}

classify_loop_stderr() {
  local stream="$1" text
  loop_stderr_size="$(stat -c %s "$stream")"
  loop_stderr_sha256="$(sha256sum "$stream" | cut -d' ' -f1)"
  loop_stderr_classification="UNCLASSIFIED_BOUNDED"
  [[ "$loop_stderr_size" -le 16384 ]] || return
  text="$(cat "$stream")"
  if [[ -z "$text" ]]; then loop_stderr_classification="EMPTY"
  elif grep -Eqi 'device or resource busy|device busy' <<<"$text"; then loop_stderr_classification="DEVICE_BUSY"
  elif grep -Eqi 'no such device' <<<"$text"; then loop_stderr_classification="NO_SUCH_DEVICE"
  elif grep -Eqi 'invalid argument|invalid option|unrecognized option' <<<"$text"; then loop_stderr_classification="INVALID_ARGUMENT_OR_OPTION"
  elif grep -Eqi 'permission denied|operation not permitted' <<<"$text"; then loop_stderr_classification="PERMISSION_DENIED"
  fi
}

append_loop_release() {
  local index="$1" precheck="$2" post_state="$3" released="$4" release_probe="$5" separator object
  separator=","; [[ "$loop_release_json" == "[]" ]] && separator=""
  printf -v object '{"index":%d,"precheck":"%s","holder_count":%d,"holder_identity_sha256":%s,"holder_probe_state":"%s","detach_exit_status":%d,"detach_timed_out":%s,"stderr_classification":"%s","stderr_size_bytes":%d,"stderr_sha256":"%s","post_detach_state":"%s","owned_image_released":%s,"release_probe_state":"%s"}' \
    "$index" "$precheck" "$loop_holder_count" "$loop_holder_hashes_json" "$loop_holder_probe_state" "$rc" "$loop_timed_out" "$loop_stderr_classification" "$loop_stderr_size" "$loop_stderr_sha256" "$post_state" "$released" "$release_probe"
  loop_release_json="${loop_release_json%]}${separator}${object}]"
}

target_absent() {
  local output rc
  set +e
  output="$(timeout --signal=TERM --kill-after=1s 3s targetcli /iscsi ls 2>/dev/null | head -c 16385)"
  rc=$?
  set -e
  [[ "$rc" -eq 0 ]] || return 2
  ! grep -Fq -- "$target_iqn" <<<"$output"
}

backstore_absent() {
  local output rc
  set +e
  output="$(timeout --signal=TERM --kill-after=1s 3s targetcli /backstores/block ls 2>/dev/null | head -c 16385)"
  rc=$?
  set -e
  [[ "$rc" -eq 0 ]] || return 2
  ! grep -Fq -- "$backstore" <<<"$output"
}

record_phase() {
  phase_names+=("$1")
  phase_attempted+=("$2")
  phase_statuses+=("$3")
  phase_exits+=("$4")
  phase_timeouts+=("$5")
  phase_postconditions+=("$6")
  if [[ "$3" != "success" && "$3" != "skipped" && "$cleanup_first_failure" == "NONE" ]]; then
    cleanup_first_failure="$1:$3"
  fi
  if [[ "$6" != "true" && "$cleanup_first_failure" == "NONE" ]]; then
    cleanup_first_failure="$1:postcondition"
  fi
}

run_bounded() {
  local seconds="$1"; shift
  timeout --signal=TERM --kill-after=2s "${seconds}s" "$@" >/dev/null 2>&1
}

phase_result() {
  local name="$1" attempted="$2" seconds="$3" postcondition="$4" status="skipped" rc=0
  shift 4
  if [[ "$attempted" == "true" ]]; then
    set +e
    run_bounded "$seconds" "$@"
    rc=$?
    set -e
    if [[ "$rc" -eq 0 ]]; then status="success"; elif [[ "$rc" -eq 124 || "$rc" -eq 137 ]]; then status="timeout"; else status="failed"; fi
    [[ "$rc" -eq 0 ]] || postcondition=false
  fi
  record_phase "$name" "$attempted" "$status" "$rc" "$seconds" "$postcondition"
}

cleanup_controller() {
  local attempted post loop image number session_output precheck post_state released release_probe loop_stderr loop_timed_out loop_stderr_classification release_stdout release_stderr release_rc release_stdout_size release_stderr_size
  cleanup_started=true

  attempted=false
  [[ "$mounted" == true ]] && attempted=true
  if [[ "$attempted" == true ]]; then
    set +e; run_bounded 8 umount -- "$mountpoint"; rc=$?; set -e
    if timeout 3s mountpoint -q "$mountpoint"; then post=false; else post=true; fi
    status="failed"; [[ "$rc" -eq 0 ]] && status="success"; [[ "$rc" -eq 124 || "$rc" -eq 137 ]] && status="timeout"
    record_phase "unmount" true "$status" "$rc" 8 "$post"
  else
    record_phase "unmount" false "skipped" 0 8 true
  fi
  mounted=false

  attempted=false
  [[ "$logged_in" == true ]] && attempted=true
  set +e
  if [[ "$attempted" == true ]]; then
    run_bounded 8 iscsiadm -m node -T "$target_iqn" -p "$portal:3260" --logout; rc=$?
  else
    rc=0
  fi
  set -e
  set +e
  session_output="$(timeout --signal=TERM --kill-after=1s 3s iscsiadm -m session 2>/dev/null)"
  session_rc=$?
  set -e
  post=false
  if [[ "$session_rc" -eq 21 ]] || { [[ "$session_rc" -eq 0 ]] && ! grep -Fq -- "$target_iqn" <<<"$session_output"; }; then post=true; fi
  status="skipped"; [[ "$attempted" == true && "$rc" -eq 0 ]] && status="success"; [[ "$attempted" == true && "$rc" -ne 0 ]] && status="failed"; [[ "$rc" -eq 124 || "$rc" -eq 137 ]] && status="timeout"
  record_phase "logout" "$attempted" "$status" "$rc" 8 "$post"
  logged_in=false

  attempted="$node_created"
  set +e
  if [[ "$attempted" == true ]]; then run_bounded 8 iscsiadm -m node -T "$target_iqn" -p "$portal:3260" -o delete; rc=$?; else rc=0; fi
  set -e
  if [[ ! -e "/etc/iscsi/nodes/$target_iqn" ]]; then post=true; else post=false; fi
  status="skipped"; [[ "$attempted" == true && "$rc" -eq 0 ]] && status="success"; [[ "$attempted" == true && "$rc" -ne 0 ]] && status="failed"; [[ "$rc" -eq 124 || "$rc" -eq 137 ]] && status="timeout"
  record_phase "node_delete" "$attempted" "$status" "$rc" 8 "$post"

  if target_absent; then attempted=false; else attempted=true; fi
  set +e
  if [[ "$attempted" == true ]]; then run_bounded 10 targetcli /iscsi delete "$target_iqn"; rc=$?; else rc=0; fi
  set -e
  if target_absent; then post=true; else post=false; fi
  status="skipped"; [[ "$attempted" == true && "$rc" -eq 0 ]] && status="success"; [[ "$attempted" == true && "$rc" -ne 0 ]] && status="failed"; [[ "$rc" -eq 124 || "$rc" -eq 137 ]] && status="timeout"
  record_phase "target_delete" "$attempted" "$status" "$rc" 10 "$post"

  if backstore_absent; then attempted=false; else attempted=true; fi
  set +e
  if [[ "$attempted" == true ]]; then run_bounded 10 targetcli /backstores/block delete "$backstore"; rc=$?; else rc=0; fi
  set -e
  if backstore_absent; then post=true; else post=false; fi
  status="skipped"; [[ "$attempted" == true && "$rc" -eq 0 ]] && status="success"; [[ "$attempted" == true && "$rc" -ne 0 ]] && status="failed"; [[ "$rc" -eq 124 || "$rc" -eq 137 ]] && status="timeout"
  record_phase "backstore_delete" "$attempted" "$status" "$rc" 10 "$post"

  phase_result "saveconfig" true 10 true targetcli saveconfig

  attempted="$pool_created"
  set +e
  if [[ "$attempted" == true ]]; then run_bounded 25 zpool destroy -f "$pool"; rc=$?; else rc=0; fi
  set -e
  set +e; timeout --signal=TERM --kill-after=1s 3s zpool list -H -o name "$pool" >/dev/null 2>&1; probe_rc=$?; set -e
  post=false; [[ "$probe_rc" -eq 1 ]] && post=true
  status="skipped"; [[ "$attempted" == true && "$rc" -eq 0 ]] && status="success"; [[ "$attempted" == true && "$rc" -ne 0 ]] && status="failed"; [[ "$rc" -eq 124 || "$rc" -eq 137 ]] && status="timeout"
  record_phase "pool_destroy" "$attempted" "$status" "$rc" 25 "$post"
  pool_created=false

  for number in 1 2 3 4 5 6; do
    if [[ ${#loops[@]} -ge "$number" ]]; then
      loop="${loops[$((number - 1))]}"; image="${images[$((number - 1))]}"
      attempted=false
      precheck="$(loop_mapping_state "$loop" "$image")"
      loop_holder_count=0; loop_holder_hashes_json="[]"; loop_holder_probe_state="NOT_APPLICABLE"
      if [[ "$precheck" == "ORIGINAL_OWNED" ]] && ! collect_loop_holders "$loop"; then
        precheck="UNSAFE"
      fi
      if [[ "$precheck" == "ORIGINAL_OWNED" ]] && [[ "$(loop_mapping_state "$loop" "$image")" != "ORIGINAL_OWNED" ]]; then
        precheck="IDENTITY_CHANGED"
      fi
      [[ "$precheck" == "ORIGINAL_OWNED" ]] && attempted=true
      loop_stderr="$work/loop-detach-$number.stderr"
      install -m 600 /dev/null "$loop_stderr"
      set +e
      if [[ "$attempted" == true ]]; then (ulimit -f 8; timeout --signal=TERM --kill-after=2s 5s losetup -d "$loop" >/dev/null 2>"$loop_stderr"); rc=$?; else rc=0; fi
      set -e
      loop_timed_out=false; [[ "$rc" -eq 124 || "$rc" -eq 137 ]] && loop_timed_out=true
      classify_loop_stderr "$loop_stderr"
      post_state="$(loop_mapping_state "$loop" "$image")"
      release_stdout="$(mktemp "$work/.loop-release.XXXXXX")"; release_stderr="$(mktemp "$work/.loop-release-stderr.XXXXXX")"
      chmod 600 "$release_stdout" "$release_stderr"
      set +e
      (ulimit -f 8; timeout --signal=TERM --kill-after=1s 2s losetup -j "$image" >"$release_stdout" 2>"$release_stderr")
      release_rc=$?
      set -e
      release_stdout_size="$(stat -c %s "$release_stdout")"; release_stderr_size="$(stat -c %s "$release_stderr")"
      released=false; release_probe="PROBE_ERROR"
      if [[ "$release_rc" -eq 0 && "$release_stdout_size" -eq 0 && "$release_stderr_size" -eq 0 ]]; then released=true; release_probe="RELEASED"; fi
      if [[ "$release_rc" -eq 0 && "$release_stdout_size" -gt 0 && "$release_stdout_size" -le 16384 && "$release_stderr_size" -le 16384 ]]; then release_probe="STILL_MAPPED"; fi
      rm -f -- "$release_stdout" "$release_stderr"
      post=false
      if [[ "$post_state" == "ABSENT" ]] || { [[ "$post_state" == "DIFFERENT_BACKING" ]] && [[ "$released" == true ]] && [[ "$release_probe" == "RELEASED" ]] && [[ "$loop_holder_count" -eq 0 ]] && [[ "$loop_holder_probe_state" == "COMPLETE" ]]; }; then post=true; fi
      append_loop_release "$number" "$precheck" "$post_state" "$released" "$release_probe"
    else
      attempted=false; rc=0; post=true; precheck="ABSENT"; post_state="ABSENT"; released=true
      loop_holder_count=0; loop_holder_hashes_json="[]"; loop_holder_probe_state="NOT_APPLICABLE"; loop_timed_out=false
      loop_stderr="$work/loop-detach-$number.stderr"; install -m 600 /dev/null "$loop_stderr"
      classify_loop_stderr "$loop_stderr"
      append_loop_release "$number" "$precheck" "$post_state" "$released" "RELEASED"
    fi
    status="skipped"; [[ "$attempted" == true && "$rc" -eq 0 ]] && status="success"; [[ "$attempted" == true && "$rc" -ne 0 ]] && status="failed"; [[ "$rc" -eq 124 || "$rc" -eq 137 ]] && status="timeout"
    record_phase "loop_detach_$number" "$attempted" "$status" "$rc" 5 "$post"
  done

  set +e
  if [[ "$initiator_had_original" == true && -f "$initiator_backup" ]]; then
    run_bounded 8 install -m 600 "$initiator_backup" /etc/iscsi/initiatorname.iscsi; rc=$?
  else
    run_bounded 8 rm -f -- /etc/iscsi/initiatorname.iscsi; rc=$?
  fi
  set -e
  status="failed"; [[ "$rc" -eq 0 ]] && status="success"; [[ "$rc" -eq 124 || "$rc" -eq 137 ]] && status="timeout"
  post=false
  if [[ "$rc" -eq 0 && "$initiator_had_original" == true ]] && timeout --signal=TERM --kill-after=1s 2s cmp -s -- "$initiator_backup" /etc/iscsi/initiatorname.iscsi; then post=true; fi
  if [[ "$rc" -eq 0 && "$initiator_had_original" == false && ! -e /etc/iscsi/initiatorname.iscsi ]]; then post=true; fi
  record_phase "initiator_restore" true "$status" "$rc" 8 "$post"

  attempted=false
  if safe_work_root; then attempted=true; fi
  set +e
  if [[ "$attempted" == true ]]; then run_bounded 10 rm -rf -- "$work"; rc=$?; else rc=0; fi
  set -e
  [[ ! -e "$work" ]] && post=true || post=false
  status="skipped"; [[ "$attempted" == true && "$rc" -eq 0 ]] && status="success"; [[ "$attempted" == true && "$rc" -ne 0 ]] && status="failed"; [[ "$rc" -eq 124 || "$rc" -eq 137 ]] && status="timeout"
  record_phase "work_root_remove" "$attempted" "$status" "$rc" 10 "$post"

  set +e; run_bounded 3 rm -f -- /.hoardarr-disposable-runner; rc=$?; set -e
  [[ ! -e /.hoardarr-disposable-runner ]] && post=true || post=false
  status="failed"; [[ "$rc" -eq 0 ]] && status="success"; [[ "$rc" -eq 124 || "$rc" -eq 137 ]] && status="timeout"
  record_phase "runner_marker_remove" true "$status" "$rc" 3 "$post"
}

build_cleanup_json() {
  local index separator="" object
  cleanup_phases_json="["
  for index in "${!phase_names[@]}"; do
    printf -v object '{"name":"%s","order":%d,"attempted":%s,"status":"%s","exit_status":%d,"timeout_seconds":%d,"postcondition":%s}' \
      "${phase_names[$index]}" "$((index + 1))" "${phase_attempted[$index]}" \
      "${phase_statuses[$index]}" "${phase_exits[$index]}" "${phase_timeouts[$index]}" \
      "${phase_postconditions[$index]}"
    cleanup_phases_json+="$separator$object"; separator=","
  done
  cleanup_phases_json+="]"
  if [[ "$cleanup_started" != true ]]; then
    cleanup_classification="cleanup_not_started"
  elif [[ "$cleanup_first_failure" == "NONE" ]]; then
    cleanup_classification="cleanup_complete"
  else
    cleanup_classification="cleanup_incomplete_bounded"
  fi
}

record_raw_integrity_checkpoint() {
  local stage="$1" current baseline_equal=false previous_equal=false
  current="$(sha256sum "$zvol_device" | awk '{print $1}')"
  [[ "$current" == "$raw_hash_before" ]] && baseline_equal=true
  [[ "$current" == "$raw_integrity_previous_hash" ]] && previous_equal=true
  raw_integrity_stages+=("$stage")
  raw_integrity_baseline_equal+=("$baseline_equal")
  raw_integrity_previous_equal+=("$previous_equal")
  if [[ "$baseline_equal" != true && "$raw_integrity_first_mismatch_stage" == "NONE" ]]; then
    raw_integrity_first_mismatch_stage="$stage"
  fi
  raw_integrity_previous_hash="$current"
}

build_raw_integrity_timeline_json() {
  local index separator="" checkpoint
  raw_integrity_timeline_json='['
  for index in "${!raw_integrity_stages[@]}"; do
    printf -v checkpoint '{"stage":"%s","baseline_equal":%s,"previous_equal":%s}' \
      "${raw_integrity_stages[$index]}" "${raw_integrity_baseline_equal[$index]}" \
      "${raw_integrity_previous_equal[$index]}"
    raw_integrity_timeline_json+="$separator$checkpoint"; separator=","
  done
  raw_integrity_timeline_json+=']'
  printf -v raw_integrity_timeline_json '{"schema_version":1,"checkpoints":%s,"first_mismatch_stage":"%s","final_comparison_attempted":%s}' \
    "$raw_integrity_timeline_json" "$raw_integrity_first_mismatch_stage" \
    "$raw_integrity_final_comparison_attempted"
}

write_receipt() {
  local draft_tmp receipt_rc
  build_cleanup_json
  build_raw_integrity_timeline_json
  mkdir -p "$receipt_dir"
  draft_tmp="$(mktemp "$receipt_dir/.managed-zvol-a5-draft.XXXXXX")"
  chmod 600 "$draft_tmp"
  jq -n \
    --arg classification "$classification" --arg run_id "$run_id" \
    --arg failure_code "$failure_code" --argjson failure_status "$original_lifecycle_status" \
    --argjson failure_line "$failure_line" --arg pool_guid_sha256 "$(printf '%s' "$pool_guid" | sha256sum | cut -d' ' -f1)" \
    --argjson loop_count "${#loops[@]}" --argjson raidz2_vdev_count "$([[ "$pool_created_once" == true ]] && echo 1 || echo 0)" \
    --argjson raidz2_member_count "$([[ "$pool_created_once" == true ]] && echo 6 || echo 0)" \
    --argjson zvol_count "$([[ "$zvol_created" == true ]] && echo 1 || echo 0)" \
    --argjson initial_apply "$initial_apply_passed" --argjson prelogin_readback "$prelogin_readback_passed" \
    --argjson tpg_authentication "$tpg_authentication_json" \
    --argjson parity "$parity_json" --argjson login_attempt_count "$login_attempt_count" \
    --argjson login_status "$login_status" --argjson diagnostic "$diagnostic_json" \
    --argjson bounded_io "$bounded_io_passed" --argjson idempotent "$idempotent_passed" \
    --argjson reconciled "$reconciled_passed" --argjson restart "$restart_passed" \
    --argjson remove "$remove_passed" --argjson backing_retained "$backing_retained" \
    --argjson persistence_control_plane "$persistence_control_plane" \
    --argjson payload_attempted "$payload_verification_attempted" \
    --argjson payload_matched "$payload_verification_matched" \
    --argjson raw_integrity_timeline "$raw_integrity_timeline_json" \
    --arg cleanup_classification "$cleanup_classification" --arg cleanup_first_failure "$cleanup_first_failure" \
    --argjson cleanup_phases "$cleanup_phases_json" --argjson loop_release "$loop_release_json" \
    '{schema_version:2,classification:$classification,workflow:"storage-integration",
      job:"managed-zvol-lio-lifecycle",run_id:$run_id,
      failure:{code:$failure_code,status:$failure_status,line:$failure_line},
      topology:{loop_count:$loop_count,raidz2_vdev_count:$raidz2_vdev_count,
        raidz2_member_count:$raidz2_member_count,zvol_count:$zvol_count,
        pool_guid_sha256:$pool_guid_sha256,raw_paths_emitted:false},
      prelogin:{production_apply_passed:$initial_apply,production_readback_passed:$prelogin_readback,
        tpg_authentication:$tpg_authentication},
      parity:$parity,
      login:{attempt_count:$login_attempt_count,status:$login_status,
        succeeded:($login_status==0),diagnostic:$diagnostic},
      downstream:{bounded_io:$bounded_io,idempotent_apply:$idempotent,
        state_only_recovery:$reconciled,target_persistence_restart:$restart,
        persistence_control_plane:$persistence_control_plane,
        remove_absence:$remove,backing_retained:$backing_retained},
      payload_verification:{attempted:$payload_attempted,matched:$payload_matched},
      raw_integrity_timeline:$raw_integrity_timeline,
      cleanup:{classification:$cleanup_classification,first_failure:$cleanup_first_failure,
        total_budget_seconds:191,phases:$cleanup_phases,loop_release:$loop_release},
      prohibited_actions:{physical_media:0,host_or_vm:0,network_storage:0,multipath:0,
        controller_or_ha:0,credential_output:0,raw_saveconfig_output:0,login_retries:0}}' >"$draft_tmp"
  set +e
  "$python" "$repo/tests/integration/managed_zvol_lio_lifecycle.py" receipt \
    --draft "$draft_tmp" --output "$receipt"
  receipt_rc=$?
  rm -f -- "$draft_tmp"
  set -e
  return "$receipt_rc"
}

finalize() {
  local original_status="$1" receipt_status
  trap - EXIT ERR
  set +e
  original_lifecycle_status="$original_status"
  cleanup_controller
  if [[ "$original_status" -eq 0 && "$cleanup_first_failure" != "NONE" ]]; then
    classification="HARNESS_ERROR"
    failure_code="CLEANUP_INCOMPLETE"
    original_lifecycle_status=44
  elif [[ "$classification" == "LOGIN_SUCCEEDED_PAYLOAD_VERIFIED_RAW_TRANSITION" && "$cleanup_first_failure" != "NONE" ]]; then
    classification="HARNESS_ERROR"
    failure_code="CLEANUP_INCOMPLETE"
    original_lifecycle_status=45
  fi
  set +e
  write_receipt
  receipt_status=$?
  if [[ "$receipt_status" -ne 0 ]]; then
    printf 'managed-zvol receipt validation failed (rc=%s)\n' "$receipt_status" >&2
    exit 97
  fi
  exit "$original_lifecycle_status"
}

on_error() {
  local status="$1" line="$2"
  failure_line="$line"
  if [[ "$failure_code" == "UNCLASSIFIED_HARNESS_STOP" ]]; then failure_code="LIFECYCLE_COMMAND_FAILED"; fi
  printf 'managed-zvol lifecycle stopped at line %s (rc=%s)\n' "$line" "$status" >&2
}
trap 'on_error "$?" "$LINENO"' ERR
trap 'finalize "$?"' EXIT

helper() {
  local action="$1"
  HOARDARR_A4_CHAP_FIXTURE="$chap_fixture" \
    "$python" "$repo/tests/integration/managed_zvol_lio_lifecycle.py" lifecycle \
    --action "$action" --state-file "$state_file" --service-id "$service_id" \
    --volume-id "$volume_id" --pool "$pool" --zvol "$zvol" \
    --size-bytes "$zvol_size_bytes" --target-iqn "$target_iqn" --portal "$portal" \
    --initiator-iqn "$initiator_iqn" --chap-user "$chap_user"
}

read_tpg_authentication() {
  local candidate status
  candidate=""
  set +e
  candidate="$("$python" "$repo/tests/integration/managed_zvol_lio_lifecycle.py" \
    tpg-authentication --target-iqn "$target_iqn")"
  status=$?
  set -e
  if [[ "$status" -ne 0 ]] || \
    ! jq -e 'type == "object" and keys == ["enabled", "observed", "schema_version"] and .schema_version == 1 and .observed == true and (.enabled | type == "boolean")' \
      <<<"$candidate" >/dev/null; then
    tpg_authentication_json='{"schema_version":1,"observed":false,"enabled":null}'
    return 1
  fi
  tpg_authentication_json="$candidate"
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
  images+=("$image"); loops+=("$loop")
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
pool_created_once=true
[[ "$(zpool get -Hp -o value health "$pool")" == "ONLINE" ]]
[[ "$(zpool get -Hp -o value ashift "$pool")" == "12" ]]
[[ "$(zpool status -P "$pool" | grep -Ec '^[[:space:]]+raidz2-[0-9]+[[:space:]]')" -eq 1 ]]
for loop in "${loops[@]}"; do [[ "$(zpool status -P "$pool" | grep -Fc -- "$loop")" -eq 1 ]]; done
pool_guid="$(zpool get -Hp -o value guid "$pool")"
zfs create -V "$zvol_size_bytes" -o volblocksize=16K "$pool/$zvol"
zvol_device="/dev/zvol/$pool/$zvol"
for _attempt in $(seq 1 30); do [[ -b "$zvol_device" ]] && break; udevadm settle; sleep 1; done
[[ -b "$zvol_device" ]]
zvol_created=true
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
initial_apply_passed=true
independent_json="$(helper readback)"
initial_digest="$(jq -r .readback.evidence_sha256 <<<"$initial_json")"
[[ "$(jq -r .readback.evidence_sha256 <<<"$independent_json")" == "$initial_digest" ]]

prelogin_target_json="$(helper readback)"
[[ "$(jq -r '.readback.state == "active" and .readback.portal_exact and .readback.acl_exact and .readback.chap_configured and .readback.chap_user_matches and .readback.chap_secret_matches and .readback.device_matches_binding' <<<"$prelogin_target_json")" == "true" ]]
[[ "$(jq -r .readback.evidence_sha256 <<<"$prelogin_target_json")" == "$initial_digest" ]]
prelogin_readback_passed=true
if ! read_tpg_authentication; then
  classification="HARNESS_ERROR"; failure_code="TPG_AUTHENTICATION_READBACK_FAILED"; exit 44
fi

if [[ -f /etc/iscsi/initiatorname.iscsi ]]; then
  cp --preserve=mode /etc/iscsi/initiatorname.iscsi "$initiator_backup"
  initiator_had_original=true
fi
install -m 600 /dev/null /etc/iscsi/initiatorname.iscsi
printf 'InitiatorName=%s\n' "$initiator_iqn" >/etc/iscsi/initiatorname.iscsi
systemctl restart iscsid.service
iscsiadm -m discovery -t sendtargets -p "$portal:3260" >/dev/null
node_created=true
iscsiadm -m node -T "$target_iqn" -p "$portal:3260" --op update -n node.session.auth.authmethod -v CHAP
iscsiadm -m node -T "$target_iqn" -p "$portal:3260" --op update -n node.session.auth.username -v "$chap_user"
iscsiadm -m node -T "$target_iqn" -p "$portal:3260" --op update -n node.session.auth.password -v "$chap_fixture"

parity_json="$(HOARDARR_A4_CHAP_FIXTURE="$chap_fixture" "$python" \
  "$repo/tests/integration/managed_zvol_lio_lifecycle.py" parity \
  --node-root /etc/iscsi/nodes --target-iqn "$target_iqn" --portal "$portal" \
  --initiator-iqn "$initiator_iqn" --chap-user "$chap_user")"
if [[ "$(jq -r .exact <<<"$parity_json")" != "true" ]]; then
  classification="PARITY_MISMATCH_IDENTIFIED"
  failure_code="$(jq -r .mismatch <<<"$parity_json")"
  exit 41
fi

login_stdout="$work/login.stdout"
login_stderr="$work/login.stderr"
login_journal="$work/login.journal"
login_kernel="$work/login.kernel"
install -m 600 /dev/null "$login_stdout"
install -m 600 /dev/null "$login_stderr"
install -m 600 /dev/null "$login_journal"
install -m 600 /dev/null "$login_kernel"
attempt_started="$(date --iso-8601=seconds)"
login_attempt_count=1
trap - ERR
set +e
(ulimit -f 16; timeout --signal=TERM --kill-after=2s 20s \
  iscsiadm -d 1 -m node -T "$target_iqn" -p "$portal:3260" --login \
  >"$login_stdout" 2>"$login_stderr")
login_status=$?
(ulimit -f 16; timeout --signal=TERM --kill-after=2s 10s journalctl \
  -u iscsid.service -u rtslib-fb-targetctl.service --since "$attempt_started" \
  --no-pager -n 80 >"$login_journal" 2>/dev/null)
diagnostic_capture_status=$?
(ulimit -f 16; timeout --signal=TERM --kill-after=2s 10s journalctl -k \
  --since "$attempt_started" --no-pager -o short-iso -n 80 >"$login_kernel" 2>/dev/null)
kernel_capture_status=$?
set -e
if [[ "$login_status" -eq 153 || "$diagnostic_capture_status" -eq 153 || "$kernel_capture_status" -eq 153 ]]; then
  classification="HARNESS_ERROR"; failure_code="DIAGNOSTIC_OVERFLOW"; exit 42
fi
if { [[ "$diagnostic_capture_status" -ne 0 && "$diagnostic_capture_status" -ne 1 ]]; } || { [[ "$kernel_capture_status" -ne 0 && "$kernel_capture_status" -ne 1 ]]; }; then
  classification="HARNESS_ERROR"; failure_code="DIAGNOSTIC_CAPTURE_FAILED"; exit 42
fi
set +e
diagnostic_json="$(HOARDARR_A4_CHAP_FIXTURE="$chap_fixture" "$python" \
  "$repo/tests/integration/managed_zvol_lio_lifecycle.py" diagnostic \
  --stdout "$login_stdout" --stderr "$login_stderr" --journal "$login_journal" \
  --kernel "$login_kernel" \
  --status "$login_status")"
diagnostic_status=$?
set -e
trap 'on_error "$?" "$LINENO"' ERR
if [[ "$diagnostic_status" -ne 0 ]]; then
  classification="HARNESS_ERROR"; failure_code="DIAGNOSTIC_VALIDATION_FAILED"; exit 43
fi
if [[ "$login_status" -ne 0 ]]; then
  if [[ "$(jq -r .diagnosed_class <<<"$diagnostic_json")" == "null" ]]; then
    classification="LOGIN_FAILURE_UNRESOLVED"
  else
    classification="LOGIN_FAILURE_DIAGNOSED"
  fi
  failure_code="LOGIN_ATTEMPT_FAILED"
  exit "$login_status"
fi
logged_in=true

by_path="/dev/disk/by-path/ip-${portal}:3260-iscsi-${target_iqn}-lun-0"
for _attempt in $(seq 1 30); do [[ -e "$by_path" ]] && break; udevadm settle; sleep 1; done
[[ -L "$by_path" ]]
lun_device="$(readlink -f -- "$by_path")"
[[ -b "$lun_device" && "$lun_device" != "$zvol_device" ]]
mkfs.ext4 -F -E lazy_itable_init=1,lazy_journal_init=1,nodiscard "$by_path" >/dev/null
mount "$by_path" "$mountpoint"; mounted=true
dd if=/dev/zero of="$mountpoint/a5-payload.bin" bs=1M count=8 status=none
sync
data_hash_before="$(sha256sum "$mountpoint/a5-payload.bin" | awk '{print $1}')"
umount -- "$mountpoint"; mounted=false
iscsiadm -m node -T "$target_iqn" -p "$portal:3260" --logout >/dev/null; logged_in=false
udevadm settle
raw_hash_before="$(sha256sum "$zvol_device" | awk '{print $1}')"
raw_integrity_previous_hash="$raw_hash_before"
record_raw_integrity_checkpoint "after_logout"
bounded_io_passed=true

state_hash_before="$(sha256sum "$state_file" | awk '{print $1}')"
state_mtime_before="$(stat -c %Y "$state_file")"
idempotent_json="$(helper apply)"
[[ "$(jq -r .already_active <<<"$idempotent_json")" == "true" ]]
[[ "$(jq -r '.counters.targetcli == 0 and .counters.state_writes == 0' <<<"$idempotent_json")" == "true" ]]
[[ "$(jq -r .readback.evidence_sha256 <<<"$idempotent_json")" == "$initial_digest" ]]
[[ "$(sha256sum "$state_file" | awk '{print $1}')" == "$state_hash_before" ]]
[[ "$(stat -c %Y "$state_file")" == "$state_mtime_before" ]]
[[ "$(sha256sum "$zvol_device" | awk '{print $1}')" == "$raw_hash_before" ]]
record_raw_integrity_checkpoint "after_idempotent_apply"
idempotent_passed=true

rm -f -- "$state_file"
reconciled_json="$(helper apply)"
[[ "$(jq -r '.reconciled_existing and .counters.targetcli == 0 and .counters.state_writes == 1' <<<"$reconciled_json")" == "true" ]]
[[ "$(jq -r .readback.evidence_sha256 <<<"$reconciled_json")" == "$initial_digest" ]]
[[ "$(sha256sum "$zvol_device" | awk '{print $1}')" == "$raw_hash_before" ]]
record_raw_integrity_checkpoint "after_state_only_reconciliation"
reconciled_passed=true

targetcli saveconfig >/dev/null
record_raw_integrity_checkpoint "after_saveconfig"
systemctl restart rtslib-fb-targetctl.service
record_raw_integrity_checkpoint "after_target_persistence_restart"
restart_json=""
for _attempt in $(seq 1 30); do if restart_json="$(helper readback 2>/dev/null)"; then break; fi; sleep 1; done
[[ "$(jq -r .readback.evidence_sha256 <<<"$restart_json")" == "$initial_digest" ]]
record_raw_integrity_checkpoint "after_persistence_readback"
post_restart_json="$(helper apply)"
[[ "$(jq -r '.already_active and .counters.targetcli == 0 and .counters.state_writes == 0' <<<"$post_restart_json")" == "true" ]]
record_raw_integrity_checkpoint "after_post_restart_idempotent_apply"
if [[ "${#raw_integrity_stages[@]}" -ne 7 ]]; then
  classification="HARNESS_ERROR"; failure_code="RAW_TIMELINE_INCOMPLETE"; exit 45
fi
persistence_control_plane=true
raw_integrity_final_comparison_attempted=true
if ! raw_hash_final="$(sha256sum "$zvol_device" | awk '{print $1}')"; then
  classification="HARNESS_ERROR"; failure_code="RAW_FINAL_COMPARISON_FAILED"; exit 45
fi
if [[ "$raw_hash_final" == "$raw_hash_before" ]]; then
  for raw_checkpoint_equal in "${raw_integrity_baseline_equal[@]}"; do
    if [[ "$raw_checkpoint_equal" != true ]]; then
      classification="HARNESS_ERROR"; failure_code="RAW_TIMELINE_TRANSIENT_MISMATCH"; exit 45
    fi
  done
  restart_passed=true
else
  restart_passed=false
fi

remove_json="$(helper remove)"
[[ "$(jq -r '.state == "removed" and (.backing_data_deleted | not) and .readback.target_absent and .readback.backstore_absent' <<<"$remove_json")" == "true" ]]
[[ "$(jq -r '.counters.targetcli == 1 and .counters.state_writes == 1' <<<"$remove_json")" == "true" ]]
[[ -b "$zvol_device" ]]
mount -o ro,noload "$zvol_device" "$mountpoint"; mounted=true
payload_verification_attempted=true
if ! data_hash_after="$(sha256sum "$mountpoint/a5-payload.bin" | awk '{print $1}')"; then
  classification="HARNESS_ERROR"; failure_code="PAYLOAD_VERIFICATION_READ_FAILED"; exit 45
fi
if [[ "$data_hash_after" == "$data_hash_before" ]]; then
  payload_verification_matched=true
else
  classification="HARNESS_ERROR"; failure_code="PAYLOAD_VERIFICATION_MISMATCH"; exit 45
fi
umount -- "$mountpoint"; mounted=false
reject_json="$(helper reject-delete)"
[[ "$(jq -r '.rejected_before_mutation and (.counters.targetcli + .counters.state_reads + .counters.state_writes + .counters.readbacks == 0)' <<<"$reject_json")" == "true" ]]
remove_passed=true
backing_retained=true
if [[ "$restart_passed" == true ]]; then
  classification="LOGIN_SUCCEEDED_LIFECYCLE_RESULT"
  failure_code="NONE"
  exit 0
fi
classification="LOGIN_SUCCEEDED_PAYLOAD_VERIFIED_RAW_TRANSITION"
failure_code="RAW_RESTART_TRANSITION_OBSERVED"
exit 44
