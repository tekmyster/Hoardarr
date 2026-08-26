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

for command_name in qemu-img qemu-system-x86_64 sha256sum timeout; do
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

install_start="$(date --iso-8601=seconds)"
if ! timeout --signal=TERM --kill-after=30s 45m qemu-system-x86_64 \
    "${common[@]}" -boot d -cdrom "$iso" -no-reboot \
    -serial "file:$output/installer-serial.log"; then
    echo "offline installer did not reach its bounded reboot checkpoint" >&2
    exit 1
fi
install_end="$(date --iso-8601=seconds)"

first_boot_start="$(date --iso-8601=seconds)"
if ! timeout --signal=TERM --kill-after=30s 15m qemu-system-x86_64 \
    "${common[@]}" -boot c -no-reboot -serial "file:$output/first-boot-serial.log"; then
    echo "offline first boot did not shut down within its bound" >&2
    exit 1
fi
first_boot_end="$(date --iso-8601=seconds)"
grep -Fq HOARDARR_OFFLINE_READY "$output/first-boot-serial.log" || {
    echo "offline first boot did not emit the readiness sentinel" >&2
    exit 1
}
grep -Fq HOARDARR_OFFLINE_EVIDENCE_BEGIN "$output/first-boot-serial.log" || {
    echo "offline first boot did not emit package/service evidence" >&2
    exit 1
}
qemu-img check "$os_disk" >"$output/qemu-img-check.txt"
sha256sum "$protected_one" "$protected_two" >"$output/protected-after.sha256"
diff -u "$output/protected-before.sha256" "$output/protected-after.sha256"

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
