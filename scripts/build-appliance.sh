#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "usage: $0 BASE_ISO BASE_ISO_SHA256 RELEASE_BUNDLE OUTPUT_ISO" >&2
    exit 2
}

[[ $# -eq 4 ]] || usage
base_iso="$(realpath -- "$1")"
expected_sha="$2"
bundle="$(realpath -- "$3")"
output="$(realpath -m -- "$4")"
[[ "$expected_sha" =~ ^[0-9a-f]{64}$ ]] || usage
[[ -f "$base_iso" && ! -L "$base_iso" ]] || { echo "base ISO must be a regular file" >&2; exit 1; }
[[ -f "$bundle" && ! -L "$bundle" ]] || { echo "release bundle must be a regular file" >&2; exit 1; }
[[ "$(sha256sum -- "$base_iso" | awk '{print $1}')" == "$expected_sha" ]] || {
    echo "base ISO digest mismatch" >&2
    exit 1
}
command -v xorriso >/dev/null || { echo "xorriso is required" >&2; exit 1; }

work="$(mktemp -d -t hoardarr-appliance.XXXXXXXX)"
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT
install -m 0644 packaging/appliance/user-data "$work/user-data"
install -m 0644 packaging/appliance/meta-data "$work/meta-data"
install -m 0644 "$bundle" "$work/hoardarr-release.tar.gz"
grub_maps=()

# The seed is inert unless the installer kernel is told where to find it.
# Patch both normal and loopback boot menus while preserving the signed ISO's
# original BIOS/UEFI boot images.
for config in boot/grub/grub.cfg boot/grub/loopback.cfg; do
    destination="$work/$(basename -- "$config")"
    xorriso -osirrox on -indev "$base_iso" -extract "/$config" "$destination" >/dev/null 2>&1 || continue
    # Keep the interactive installer on the virtual/physical display.  When a
    # serial console is also listed, Subiquity selects it as its controlling
    # terminal and VMware users receive a black console even if tty0 is listed
    # last.  Headless CI validates the VGA framebuffer directly instead.
    sed -i -E 's/[[:space:]]+autoinstall ds=nocloud\\;s=\/cdrom\/nocloud\///g; s/[[:space:]]+console=ttyS0,115200n8([[:space:]]+console=tty0)?//g' "$destination"
    sed -i -E '/^[[:space:]]*linux[[:space:]]/ s/[[:space:]]---/ autoinstall ds=nocloud\\;s=\/cdrom\/nocloud\/ console=tty0 ---/' "$destination"
    grep -q 'autoinstall ds=nocloud' "$destination" || {
        echo "could not enable NoCloud autoinstall in /$config" >&2
        exit 1
    }
    grub_maps+=( -map "$destination" "/$config" )
done
[[ ${#grub_maps[@]} -gt 0 ]] || { echo "Ubuntu GRUB configuration was not found" >&2; exit 1; }

# Replay the signed Ubuntu ISO's original BIOS/UEFI boot layout. Only the
# NoCloud seed and verified Hoardarr release bundle are injected.
xorriso \
    -indev "$base_iso" \
    -outdev "$output" \
    -map "$work/user-data" /nocloud/user-data \
    -map "$work/meta-data" /nocloud/meta-data \
    -map "$work/hoardarr-release.tar.gz" /hoardarr/hoardarr-release.tar.gz \
    "${grub_maps[@]}" \
    -boot_image any replay \
    -commit

sha256sum -- "$output" >"${output}.sha256"
