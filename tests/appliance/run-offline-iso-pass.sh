#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 3 ]] || {
    echo "usage: $0 APPLIANCE_ISO OUTPUT_DIRECTORY PASS_NAME" >&2
    exit 2
}
iso="$(realpath -- "$1")"
output="$(realpath -m -- "$2")"
pass_name="$3"
[[ "$pass_name" =~ ^pass-[12]$ ]] || { echo "pass name must be pass-1 or pass-2" >&2; exit 2; }
[[ -f "$iso" && ! -L "$iso" ]] || { echo "appliance ISO must be a regular file" >&2; exit 1; }
[[ ! -e "$output" ]] || { echo "output directory already exists" >&2; exit 1; }
mkdir -p "$output"

for command_name in qemu-img qemu-system-x86_64 sha256sum timeout python3 ps; do
    command -v "$command_name" >/dev/null || { echo "missing command: $command_name" >&2; exit 1; }
done
os_disk="$output/os.qcow2"
protected_one="$output/protected-one.raw"
protected_two="$output/protected-two.raw"
qemu-img create -q -f qcow2 "$os_disk" 32G
truncate -s 64M "$protected_one" "$protected_two"
printf 'HOARDARR-PROTECTED-ONE' | dd of="$protected_one" conv=notrunc status=none
printf 'HOARDARR-PROTECTED-TWO' | dd of="$protected_two" conv=notrunc status=none
printf 'END-ONE' | dd of="$protected_one" bs=1 seek=$((64*1024*1024-7)) conv=notrunc status=none
printf 'END-TWO' | dd of="$protected_two" bs=1 seek=$((64*1024*1024-7)) conv=notrunc status=none
sha256sum "$protected_one" "$protected_two" >"$output/protected-before.sha256"

accelerator=tcg
[[ -r /dev/kvm && -w /dev/kvm ]] && accelerator=kvm
common=(
    -machine "accel=$accelerator" -m 4096 -smp 4 -nic none -display none
    -drive "if=none,id=osdisk,file=$os_disk,format=qcow2,cache=unsafe"
    -device "virtio-blk-pci,drive=osdisk,serial=HOARDARR-OS-DISK"
    -drive "if=none,id=protected1,file=$protected_one,format=raw,readonly=on"
    -device "virtio-blk-pci,drive=protected1,serial=HOARDARR-PROTECTED-ONE"
    -drive "if=none,id=protected2,file=$protected_two,format=raw,readonly=on"
    -device "virtio-blk-pci,drive=protected2,serial=HOARDARR-PROTECTED-TWO"
)

diagnostic_mode="${HOARDARR_OFFLINE_DIAGNOSTIC_MODE:-false}"
[[ "$diagnostic_mode" == true || "$diagnostic_mode" == false ]] || {
    echo "HOARDARR_OFFLINE_DIAGNOSTIC_MODE must be true or false" >&2
    exit 2
}
payload_capture_parser=""
if [[ "$diagnostic_mode" == true ]]; then
    script_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    payload_capture_parser="$script_root/parse-offline-payload-capture.py"
    [[ -f "$payload_capture_parser" && ! -L "$payload_capture_parser" ]] || {
        echo "offline payload capture parser is missing" >&2
        exit 1
    }
fi

write_diagnostic_metadata() {
    local classification="$1"
    local qemu_exit="$2"
    local install_end="$3"
    local elapsed_seconds="$4"
    python3 - "$output/run.json" "$pass_name" "$diagnostic_mode" "$accelerator" \
        "$classification" "$qemu_exit" "$install_start" "$install_end" "$elapsed_seconds" \
        "${first_boot_start:-}" "${first_boot_end:-}" "${first_boot_classification:-not_started}" <<'PY'
import json
import pathlib
import sys

(
    destination,
    pass_name,
    diagnostic_mode,
    accelerator,
    classification,
    bounded_runner_exit,
    install_started,
    install_finished,
    elapsed_seconds,
    first_boot_started,
    first_boot_finished,
    first_boot_classification,
) = sys.argv[1:]
payload = {
    "schema_version": 2,
    "pass": pass_name,
    "validation_mode": "diagnostic-pass-1" if diagnostic_mode == "true" else "two-pass",
    "acceptance_eligible": False if diagnostic_mode == "true" else True,
    "network_device": "absent (-nic none)",
    "accelerator": accelerator,
    "kvm_available": accelerator == "kvm",
    "os_disk_serial": "HOARDARR-OS-DISK",
    "protected_disk_serials": ["HOARDARR-PROTECTED-ONE", "HOARDARR-PROTECTED-TWO"],
    "install_bound_seconds": 2700,
    "first_boot_bound_seconds": 900,
    "install_classification": classification,
    "bounded_runner_exit_status": int(bounded_runner_exit),
    "qemu_exit_status": None if classification == "installer_timeout" else int(bounded_runner_exit),
    "install_started": install_started,
    "install_finished": install_finished,
    "install_elapsed_seconds": int(elapsed_seconds),
    "first_boot_classification": first_boot_classification,
    "first_boot_started": first_boot_started or None,
    "first_boot_finished": first_boot_finished or None,
}
capture_path = pathlib.Path(destination).with_name("offline-payload-capture.json")
if capture_path.is_file():
    payload["offline_payload_capture"] = json.loads(capture_path.read_text(encoding="utf-8"))
pathlib.Path(destination).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
}

finalize_diagnostic_evidence() {
    local finalization_failed=0
    sha256sum "$protected_one" "$protected_two" >"$output/protected-after.sha256" || finalization_failed=1
    if ! diff -u "$output/protected-before.sha256" "$output/protected-after.sha256" \
        >"$output/protected-diff.txt"; then
        finalization_failed=1
    fi
    qemu-img info --output=json "$os_disk" >"$output/qemu-img-info.json" \
        2>"$output/qemu-img-info.stderr" || finalization_failed=1
    if qemu-img check "$os_disk" >"$output/qemu-img-check.txt" \
        2>"$output/qemu-img-check.stderr"; then
        printf '0\n' >"$output/qemu-img-check.exit"
    else
        printf '%s\n' "$?" >"$output/qemu-img-check.exit"
        finalization_failed=1
    fi
    if compgen -G "$output/frames/*.ppm" >/dev/null; then
        find "$output/frames" -maxdepth 1 -type f -name '*.ppm' -printf '%f\0' |
            sort -z | while IFS= read -r -d '' name; do
                sha256sum "$output/frames/$name"
            done >"$output/frames/SHA256SUMS"
    else
        : >"$output/frames/SHA256SUMS"
        finalization_failed=1
    fi
    if (( finalization_failed == 0 )); then
        printf 'complete\n' >"$output/evidence-finalization.txt"
    else
        printf 'incomplete\n' >"$output/evidence-finalization.txt"
    fi
    if ! find "$output" -type f ! -path "$output/SHA256SUMS" \
        ! -path "$output/SHA256SUMS.tmp" -printf '%P\0' |
        sort -z | while IFS= read -r -d '' name; do sha256sum "$output/$name"; done \
        >"$output/SHA256SUMS.tmp"; then
        printf 'incomplete\n' >"$output/evidence-finalization.txt"
        rm -f -- "$output/SHA256SUMS.tmp"
        return 1
    fi
    mv -- "$output/SHA256SUMS.tmp" "$output/SHA256SUMS"
    return "$finalization_failed"
}

monitor_snapshot() {
    local monitor_socket="$1"
    local frame="$2"
    local captured_at="$3"
    python3 - "$monitor_socket" "$frame" "$captured_at" <<'PY'
import socket
import sys
import time

monitor_socket, frame, captured_at = sys.argv[1:]
commands = ("info status", "info cpus", f'screendump "{frame}"')
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as monitor:
    monitor.settimeout(1)
    monitor.connect(monitor_socket)
    chunks = []
    try:
        chunks.append(monitor.recv(65536))
    except TimeoutError:
        pass
    for command in commands:
        monitor.sendall(command.encode("utf-8") + b"\n")
        time.sleep(0.2)
        try:
            chunks.append(monitor.recv(65536))
        except TimeoutError:
            chunks.append(b"<monitor response timed out>\n")
print(f"[{captured_at}]", flush=True)
print(b"".join(chunks).decode("utf-8", errors="replace"), flush=True)
PY
}

run_diagnostic_installer() {
    local monitor_socket="$output/installer-monitor.sock"
    local monitor_log="$output/installer-monitor.log"
    local process_log="$output/installer-process.tsv"
    local qemu_stderr="$output/qemu-installer-stderr.log"
    local frame_number=0
    local next_frame=0
    local timed_out=false
    local payload_failure_observed=false
    local payload_capture_invalid=false
    local qemu_exit=0
    local now elapsed captured_at frame payload_parser_status
    mkdir -p "$output/frames"
    : >"$output/installer-serial.log"
    : >"$monitor_log"
    printf 'timestamp\telapsed_seconds\tpid\tstate\tcpu_time\tpercent_cpu\trss_kib\tvsz_kib\n' >"$process_log"

    timeout --signal=TERM --kill-after=30s 2700s \
        qemu-system-x86_64 "${common[@]}" -boot d -cdrom "$iso" -no-reboot \
        -serial "file:$output/installer-serial.log" \
        -monitor "unix:$monitor_socket,server=on,wait=off" 2>"$qemu_stderr" &
    local runner_pid=$!
    local qemu_pid=""
    local started_epoch
    started_epoch="$(date +%s)"
    for _ in {1..50}; do
        if [[ -r "/proc/$runner_pid/task/$runner_pid/children" ]]; then
            read -r qemu_pid _ <"/proc/$runner_pid/task/$runner_pid/children" || true
        fi
        [[ -n "$qemu_pid" ]] && break
        kill -0 "$runner_pid" 2>/dev/null || break
        sleep 0.1
    done
    [[ -n "$qemu_pid" ]] || qemu_pid="$runner_pid"
    {
        printf 'bounded_runner_pid=%s\n' "$runner_pid"
        printf 'observed_qemu_pid=%s\n' "$qemu_pid"
        printf 'qemu_child_discovered=%s\n' "$([[ "$qemu_pid" != "$runner_pid" ]] && echo true || echo false)"
    } >"$output/process-identities.txt"

    while kill -0 "$runner_pid" 2>/dev/null; do
        now="$(date +%s)"
        elapsed=$(( now - started_epoch ))
        captured_at="$(date --iso-8601=seconds)"
        ps -p "$qemu_pid" -o pid=,stat=,time=,%cpu=,rss=,vsz= |
            awk -v timestamp="$captured_at" -v elapsed="$elapsed" \
                '{print timestamp "\t" elapsed "\t" $1 "\t" $2 "\t" $3 "\t" $4 "\t" $5 "\t" $6}' \
                >>"$process_log" || true
        if (( elapsed >= next_frame )); then
            printf -v frame '%s/frames/installer-%04d.ppm' "$output" "$frame_number"
            if monitor_snapshot "$monitor_socket" "$frame" "$captured_at" >>"$monitor_log" 2>&1; then
                frame_number=$(( frame_number + 1 ))
                next_frame=$(( elapsed + 60 ))
            else
                printf '[%s] monitor snapshot unavailable\n' "$captured_at" >>"$monitor_log"
                next_frame=$(( elapsed + 10 ))
            fi
        fi
        set +e
        python3 "$payload_capture_parser" \
            "$output/installer-serial.log" \
            "$output/offline-payload-console.log" \
            "$output/offline-payload-target-log.reconstructed.log" \
            "$output/offline-payload-capture.json" \
            2>"$output/offline-payload-capture-parser.stderr"
        payload_parser_status=$?
        set -e
        if (( payload_parser_status == 10 )); then
            payload_failure_observed=true
            sleep 5
            captured_at="$(date --iso-8601=seconds)"
            elapsed=$(( $(date +%s) - started_epoch ))
            printf -v frame '%s/frames/installer-%04d.ppm' "$output" "$frame_number"
            if monitor_snapshot "$monitor_socket" "$frame" "$captured_at" \
                >>"$monitor_log" 2>&1; then
                frame_number=$(( frame_number + 1 ))
            else
                payload_capture_invalid=true
            fi
            ps -p "$qemu_pid" -o pid=,stat=,time=,%cpu=,rss=,vsz= |
                awk -v timestamp="$captured_at" -v elapsed="$elapsed" \
                    '{print timestamp "\t" elapsed "\t" $1 "\t" $2 "\t" $3 "\t" $4 "\t" $5 "\t" $6}' \
                    >>"$process_log" || payload_capture_invalid=true
            break
        elif (( payload_parser_status == 21 )); then
            payload_capture_invalid=true
        fi
        sleep 5
    done

    if [[ "$payload_failure_observed" == true ]]; then
        kill -TERM "$qemu_pid" 2>/dev/null || true
        for _ in {1..3}; do
            kill -0 "$qemu_pid" 2>/dev/null || break
            sleep 1
        done
        kill -KILL "$qemu_pid" 2>/dev/null || true
    fi
    set +e
    wait "$runner_pid"
    qemu_exit=$?
    set -e
    install_end="$(date --iso-8601=seconds)"
    elapsed=$(( $(date +%s) - started_epoch ))
    if (( qemu_exit == 124 || qemu_exit == 137 )); then
        timed_out=true
    fi
    rm -f -- "$monitor_socket"

    if [[ "$payload_failure_observed" != true ]]; then
        set +e
        python3 "$payload_capture_parser" \
            "$output/installer-serial.log" \
            "$output/offline-payload-console.log" \
            "$output/offline-payload-target-log.reconstructed.log" \
            "$output/offline-payload-capture.json" \
            2>"$output/offline-payload-capture-parser.stderr"
        payload_parser_status=$?
        set -e
        if (( payload_parser_status == 10 )); then
            payload_failure_observed=true
        elif (( payload_parser_status == 21 )); then
            payload_capture_invalid=true
        fi
    fi

    if [[ "$payload_failure_observed" == true && "$payload_capture_invalid" != true ]]; then
        write_diagnostic_metadata offline_payload_failure_observed "$qemu_exit" "$install_end" "$elapsed"
        if ! finalize_diagnostic_evidence; then
            echo "diagnostic evidence finalization was incomplete" >&2
            return 2
        fi
        echo "offline payload failure was captured exactly" >&2
        return 1
    fi
    if [[ "$payload_capture_invalid" == true ]]; then
        write_diagnostic_metadata offline_payload_capture_invalid "$qemu_exit" "$install_end" "$elapsed"
        if ! finalize_diagnostic_evidence; then
            echo "diagnostic evidence finalization was incomplete" >&2
        fi
        echo "offline payload capture was malformed or inconsistent" >&2
        return 2
    fi

    if [[ "$timed_out" == true ]]; then
        write_diagnostic_metadata installer_timeout "$qemu_exit" "$install_end" "$elapsed"
        if ! finalize_diagnostic_evidence; then
            echo "diagnostic evidence finalization was incomplete" >&2
            return 2
        fi
        echo "offline installer did not reach its bounded reboot checkpoint" >&2
        return 1
    fi
    if (( qemu_exit != 0 )); then
        write_diagnostic_metadata installer_unexpected_exit "$qemu_exit" "$install_end" "$elapsed"
        if ! finalize_diagnostic_evidence; then
            echo "diagnostic evidence finalization was incomplete" >&2
            return 2
        fi
        echo "offline installer exited unexpectedly" >&2
        return 1
    fi
    diagnostic_qemu_exit="$qemu_exit"
    diagnostic_install_elapsed="$elapsed"
    return 0
}

install_start="$(date --iso-8601=seconds)"
if [[ "$diagnostic_mode" == true ]]; then
    if ! run_diagnostic_installer; then
        exit 1
    fi
else
    if ! timeout --signal=TERM --kill-after=30s 45m qemu-system-x86_64 \
        "${common[@]}" -boot d -cdrom "$iso" -no-reboot \
        -serial "file:$output/installer-serial.log"; then
        echo "offline installer did not reach its bounded reboot checkpoint" >&2
        exit 1
    fi
    install_end="$(date --iso-8601=seconds)"
fi

first_boot_start="$(date --iso-8601=seconds)"
first_boot_classification=completed
if ! timeout --signal=TERM --kill-after=30s 15m qemu-system-x86_64 \
    "${common[@]}" -boot c -no-reboot -serial "file:$output/first-boot-serial.log"; then
    first_boot_classification=timeout_or_unexpected_exit
    if [[ "$diagnostic_mode" != true ]]; then
        echo "offline first boot did not shut down within its bound" >&2
        exit 1
    fi
fi
first_boot_end="$(date --iso-8601=seconds)"
if [[ "$first_boot_classification" == completed ]] && \
    ! grep -Fq HOARDARR_OFFLINE_READY "$output/first-boot-serial.log"; then
    first_boot_classification=readiness_sentinel_missing
    if [[ "$diagnostic_mode" != true ]]; then
        echo "offline first boot did not emit the readiness sentinel" >&2
        exit 1
    fi
fi
if [[ "$first_boot_classification" == completed ]] && \
    ! grep -Fq HOARDARR_OFFLINE_EVIDENCE_BEGIN "$output/first-boot-serial.log"; then
    first_boot_classification=evidence_sentinel_missing
    if [[ "$diagnostic_mode" != true ]]; then
        echo "offline first boot did not emit package/service evidence" >&2
        exit 1
    fi
fi

if [[ "$diagnostic_mode" == true ]]; then
    write_diagnostic_metadata installer_reboot_checkpoint "$diagnostic_qemu_exit" \
        "$install_end" "$diagnostic_install_elapsed"
    if ! finalize_diagnostic_evidence; then
        echo "diagnostic evidence finalization was incomplete" >&2
        exit 1
    fi
    if [[ "$first_boot_classification" != completed ]]; then
        echo "offline first boot diagnostic did not complete successfully" >&2
        exit 1
    fi
else
    qemu-img check "$os_disk" >"$output/qemu-img-check.txt"
    sha256sum "$protected_one" "$protected_two" >"$output/protected-after.sha256"
    diff -u "$output/protected-before.sha256" "$output/protected-after.sha256" >"$output/protected-diff.txt"

    cat >"$output/run.json" <<EOF
{
  "schema_version": 1,
  "pass": "$pass_name",
  "network_device": "absent (-nic none)",
  "accelerator": "$accelerator",
  "os_disk_serial": "HOARDARR-OS-DISK",
  "protected_disk_serials": ["HOARDARR-PROTECTED-ONE", "HOARDARR-PROTECTED-TWO"],
  "install_started": "$install_start",
  "install_finished": "$install_end",
  "first_boot_started": "$first_boot_start",
  "first_boot_finished": "$first_boot_end"
}
EOF
    find "$output" -maxdepth 1 -type f ! -name SHA256SUMS -printf '%f\0' |
        sort -z | while IFS= read -r -d '' name; do sha256sum "$output/$name"; done >"$output/SHA256SUMS"
fi
