#!/usr/bin/env bash
set -euo pipefail

if [[ "${HOARDARR_RUNTIME_TEST_CHILD:-}" == 1 ]]; then
    [[ $# -eq 3 ]] || exit 2
else
    [[ $# -eq 1 ]] || { echo "usage: $0 PRODUCTION_FUNCTIONS" >&2; exit 2; }
fi
functions_file="$(realpath -- "$1")"
[[ -f "$functions_file" && ! -L "$functions_file" ]] || exit 1
for command_name in unshare mount umount mountpoint dpkg dpkg-deb ldd; do
    command -v "$command_name" >/dev/null || { echo "missing $command_name" >&2; exit 1; }
done

if [[ "${HOARDARR_RUNTIME_TEST_CHILD:-}" != 1 ]]; then
    work="$(mktemp -d)"
    trap 'rm -rf -- "$work"' EXIT
    parent_namespace="$(readlink -- /proc/self/ns/mnt)"
    HOARDARR_OFFLINE_PRIVATE_MOUNT_NAMESPACE=1 \
    HOARDARR_OFFLINE_PARENT_MOUNT_NAMESPACE='mnt:[1]' \
        unshare --mount --propagation private -- \
        env HOARDARR_RUNTIME_TEST_CHILD=1 \
        bash "$0" "$functions_file" "$work" "$parent_namespace"
    while IFS= read -r path; do
        [[ -z "$(python3 - "$path" <<'PY'
import re, sys
expected=sys.argv[1]
decode=lambda value: re.sub(r"\\([0-7]{3})",lambda match:chr(int(match.group(1),8)),value)
with open('/proc/self/mountinfo',encoding='utf-8') as stream:
    print('\n'.join(line for line in stream if decode(line.split(' ')[4]) == expected))
PY
)" ]] || { echo "runtime mount propagated outside child namespace: $path" >&2; exit 1; }
    done <"$work/all-target-paths"
    echo "private_namespace_containment=true"
    exit 0
fi

[[ $# -eq 3 ]]
work="$2"
parent_namespace="$3"
current_namespace="$(readlink -- /proc/self/ns/mnt)"
[[ "$current_namespace" != "$parent_namespace" ]]
# Preseeded legacy variables do not act as an isolation credential.
[[ "${HOARDARR_OFFLINE_PRIVATE_MOUNT_NAMESPACE:-}" == 1 ]]
[[ "${HOARDARR_OFFLINE_PARENT_MOUNT_NAMESPACE:-}" == 'mnt:[1]' ]]
source "$functions_file"
real_mount="$(command -v mount)"
real_umount="$(command -v umount)"

new_target() {
    local name="$1"
    target="$work/$name"
    mkdir -p -- "$target"/{proc,sys,dev/pts,run,var/lib/hoardarr-install}
    runtime_mount_record="$target/var/lib/hoardarr-install/runtime-mounts.tsv"
    runtime_cleanup_record="$target/var/lib/hoardarr-install/runtime-mount-cleanup.tsv"
    created_runtime_mounts=()
    runtime_mount_ids=()
    runtime_mount_records=()
    printf '%s\n' \
        "$target/proc" "$target/sys" "$target/dev" "$target/dev/pts" "$target/run" \
        >>"$work/all-target-paths"
}

assert_runtime_absent() {
    local relative
    for relative in proc sys dev/pts dev run; do
        [[ -z "$(mountinfo_exact_record "$target/$relative")" ]]
    done
}

# Normal production preparation exposes all five paths, cleans in exact
# reverse order, retains five evidence rows, and is idempotent afterwards.
new_target success
umount_log="$work/success-umount.log"
umount() { printf '%s\n' "${*: -1}" >>"$umount_log"; "$real_umount" "$@"; }
prepare_runtime_mounts
for relative in proc sys dev dev/pts run; do
    [[ -n "$(mountinfo_exact_record "$target/$relative")" ]]
done
cleanup_runtime_mounts
assert_runtime_absent
expected_reverse="$work/expected-reverse"
printf '%s\n' "$target/run" "$target/dev/pts" "$target/dev" "$target/sys" "$target/proc" >"$expected_reverse"
cmp -s "$expected_reverse" "$umount_log"
[[ "$(wc -l <"$runtime_cleanup_record")" -eq 6 ]]
tail -n +2 "$runtime_cleanup_record" | cut -f2 >"$work/receipt-order"
cmp -s "$expected_reverse" "$work/receipt-order"
receipt_hash="$(sha256sum "$runtime_cleanup_record" | cut -d' ' -f1)"
temporary_masks_cleanup_complete=true
policy_cleanup_complete=true
service_guard_cleanup_complete=true
cleanup_service_guards() { return 0; }
cleanup_guard 0
[[ "$(sha256sum "$runtime_cleanup_record" | cut -d' ' -f1)" == "$receipt_hash" ]]
[[ "$(wc -l <"$runtime_cleanup_record")" -eq 6 ]]
unset -f umount

# A locally built package postinst reproduces the unmounted failure, then
# succeeds inside the corrected target while reading every required runtime
# interface and a kernel-hook-equivalent /proc,/sys,/dev view.
copy_shell() {
    local root="$1" binary=/bin/dash dependency
    mkdir -p -- "$root/bin"
    cp --parents -- "$binary" "$root"
    ln -s -- dash "$root/bin/sh"
    while IFS= read -r dependency; do
        [[ -f "$dependency" ]] && cp --parents -- "$dependency" "$root"
    done < <(ldd "$binary" | grep -oE '/[^[:space:]]+' | sort -u)
    mkdir -p -- "$root/var/lib/dpkg/updates" "$root/var/lib/dpkg/info" "$root/var/lib/hoardarr-runtime-probe"
    : >"$root/var/lib/dpkg/status"
}
package_root="$work/package"
mkdir -p -- "$package_root/DEBIAN"
cat >"$package_root/DEBIAN/control" <<'EOF'
Package: hoardarr-runtime-probe
Version: 1.0
Architecture: all
Maintainer: Hoardarr CI <ci@invalid.hoardarr.local>
Description: disposable target runtime mount probe
EOF
cat >"$package_root/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -eu
printf '%s\n' entered >/var/lib/hoardarr-runtime-probe/entered
test -r /proc/self/mountinfo
test -r /proc/mounts
test -r /proc/cmdline
test -d /sys/class
test -c /dev/null
test -e /dev/pts/ptmx
test -d /run
IFS= read -r _ </proc/self/mountinfo
IFS= read -r _ </proc/mounts
printf '%s\n' passed >/var/lib/hoardarr-runtime-probe/result
EOF
chmod 0755 "$package_root/DEBIAN/postinst"
dpkg-deb --build --root-owner-group "$package_root" "$work/runtime-probe.deb" >/dev/null

new_target old-runtime
copy_shell "$target"
old_status=0
dpkg --root="$target" --install "$work/runtime-probe.deb" >"$work/old-dpkg.log" 2>&1 || old_status=$?
[[ "$old_status" -ne 0 && \
    "$(cat "$target/var/lib/hoardarr-runtime-probe/entered")" == entered && \
    ! -e "$target/var/lib/hoardarr-runtime-probe/result" ]]
assert_runtime_absent

new_target corrected-runtime
copy_shell "$target"
prepare_runtime_mounts
dpkg --root="$target" --install "$work/runtime-probe.deb" >"$work/corrected-dpkg.log" 2>&1
[[ "$(cat "$target/var/lib/hoardarr-runtime-probe/entered")" == entered ]]
[[ "$(cat "$target/var/lib/hoardarr-runtime-probe/result")" == passed ]]
cleanup_runtime_mounts
assert_runtime_absent

# Failures before each bind leave no created mount; failures after each
# partial mount clean every recorded predecessor in reverse order.
for fail_at in 1 2 3 4 5; do
    new_target "bind-failure-$fail_at"
    mount_calls=0
    mount() {
        if [[ "$1" == --bind ]]; then
            mount_calls=$((mount_calls + 1))
            (( mount_calls == fail_at )) && return 71
        fi
        "$real_mount" "$@"
    }
    status=0
    prepare_runtime_mounts >/dev/null 2>&1 || status=$?
    [[ "$status" -ne 0 ]]
    cleanup_runtime_mounts
    assert_runtime_absent
    unset -f mount
done

# Every post-bind seam either has a recorded ID for ordinary cleanup or uses
# the immediate unrecorded rollback. All injected failures leave no mount.
for mode in initial-record id-extract make-private post-private-record identity-validation; do
    new_target "post-bind-$mode"
    original_mountinfo="$(declare -f mountinfo_exact_record | sed '1s/mountinfo_exact_record/original_mountinfo_exact_record/')"
    original_record_field="$(declare -f runtime_record_field | sed '1s/runtime_record_field/original_runtime_record_field/')"
    eval "$original_mountinfo"
    eval "$original_record_field"
    made_private=false
    field_zero_calls=0
    mount() {
        if [[ "$1" == --make-private ]]; then
            [[ "$mode" == make-private ]] && return 72
            made_private=true
        fi
        "$real_mount" "$@"
    }
    mountinfo_exact_record() {
        local record
        record="$(original_mountinfo_exact_record "$1")" || return
        if [[ "$1" == "$target/proc" && -n "$record" ]]; then
            if [[ "$mode" == initial-record && "$made_private" == false ]]; then return 73; fi
            if [[ "$mode" == post-private-record && "$made_private" == true ]]; then return 74; fi
            if [[ "$mode" == identity-validation && "$made_private" == true ]]; then
                printf '%s\n' "${record//proc$'\x1f'proc/proc$'\x1f'wrong-source}"
                return 0
            fi
        fi
        printf '%s\n' "$record"
    }
    runtime_record_field() {
        if [[ "$mode" == id-extract && "$2" == 0 ]]; then
            field_zero_calls=$((field_zero_calls + 1))
            (( field_zero_calls == 1 )) && return 75
        fi
        original_runtime_record_field "$@"
    }
    status=0
    prepare_runtime_mounts >/dev/null 2>&1 || status=$?
    [[ "$status" -ne 0 ]]
    cleanup_runtime_mounts >/dev/null 2>&1 || true
    assert_runtime_absent
    unset -f mount mountinfo_exact_record runtime_record_field
    eval "$(declare -f original_mountinfo_exact_record | sed '1s/original_mountinfo_exact_record/mountinfo_exact_record/')"
    eval "$(declare -f original_runtime_record_field | sed '1s/original_runtime_record_field/runtime_record_field/')"
    unset -f original_mountinfo_exact_record original_runtime_record_field
done

# Unsafe targets fail before mutation. An unexpected pre-existing mount keeps
# its exact mount ID and is never recursively or lazily removed.
new_target symlink-reject
rmdir "$target/proc"
ln -s -- /proc "$target/proc"
status=0; prepare_runtime_mounts >/dev/null 2>&1 || status=$?
[[ "$status" -ne 0 ]]; assert_runtime_absent

new_target nondirectory-reject
rmdir "$target/sys"; : >"$target/sys"
status=0; prepare_runtime_mounts >/dev/null 2>&1 || status=$?
[[ "$status" -ne 0 ]]; assert_runtime_absent

new_target preexisting-reject
"$real_mount" -t tmpfs -o nosuid,nodev,noexec tmpfs "$target/proc"
preexisting="$(mountinfo_exact_record "$target/proc")"
preexisting_id="$(runtime_record_field "$preexisting" 0)"
status=0; prepare_runtime_mounts >/dev/null 2>&1 || status=$?
[[ "$status" -ne 0 && "$(runtime_record_field "$(mountinfo_exact_record "$target/proc")" 0)" == "$preexisting_id" ]]
"$real_umount" "$target/proc"
assert_runtime_absent

# Chrooted-work failure and TERM both retain their original status while the
# single EXIT lifecycle removes every mount.
new_target chroot-work-failure
status=0
(
    trap 'exit_cleanup $?' EXIT
    trap 'signal_exit 143' TERM
    prepare_runtime_mounts
    false
) || status=$?
[[ "$status" -eq 1 ]]; assert_runtime_absent

new_target signal-cleanup
status=0
(
    trap 'exit_cleanup $?' EXIT
    trap 'signal_exit 143' TERM
    prepare_runtime_mounts
    kill -TERM "$BASHPID"
) || status=$?
[[ "$status" -eq 143 ]]; assert_runtime_absent

# Cleanup failure is visible for success, preserves an existing failure, and
# a subsequent real cleanup removes only the still-recorded mount.
new_target cleanup-failure
prepare_runtime_mounts
failed_once=false
umount() {
    if [[ "$failed_once" == false ]]; then failed_once=true; return 76; fi
    "$real_umount" "$@"
}
cleanup_status=0; cleanup_guard 0 >/dev/null 2>&1 || cleanup_status=$?
[[ "$cleanup_status" -ne 0 ]]
unset -f umount
cleanup_runtime_mounts
assert_runtime_absent

new_target cleanup-original-status
prepare_runtime_mounts
umount() { return 77; }
cleanup_status=0; cleanup_guard 79 >/dev/null 2>&1 || cleanup_status=$?
[[ "$cleanup_status" -eq 79 ]]
unset -f umount
cleanup_runtime_mounts
assert_runtime_absent

echo "runtime_paths=proc,sys,dev,dev/pts,run"
echo "postinst_runtime_probe=passed"
echo "partial_and_signal_cleanup=passed"
