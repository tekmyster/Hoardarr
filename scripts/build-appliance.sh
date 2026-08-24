#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "usage: $0 BASE_ISO BASE_ISO_SHA256 RELEASE_BUNDLE OUTPUT_ISO [USER_DATA]" >&2
    exit 2
}

[[ $# -ge 4 && $# -le 5 ]] || usage
base_iso="$(realpath -- "$1")"
expected_sha="$2"
bundle="$(realpath -- "$3")"
output="$(realpath -m -- "$4")"
user_data="$(realpath -- "${5:-packaging/appliance/user-data}")"
[[ "$expected_sha" =~ ^[0-9a-f]{64}$ ]] || usage
[[ -f "$base_iso" && ! -L "$base_iso" ]] || { echo "base ISO must be a regular file" >&2; exit 1; }
[[ -f "$bundle" && ! -L "$bundle" ]] || { echo "release bundle must be a regular file" >&2; exit 1; }
[[ -f "$user_data" && ! -L "$user_data" ]] || { echo "user-data must be a regular file" >&2; exit 1; }
[[ "$(sha256sum -- "$base_iso" | awk '{print $1}')" == "$expected_sha" ]] || {
    echo "base ISO digest mismatch" >&2
    exit 1
}
command -v xorriso >/dev/null || { echo "xorriso is required" >&2; exit 1; }

work="$(mktemp -d -t hoardarr-appliance.XXXXXXXX)"
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT
install -m 0644 "$user_data" "$work/user-data"
install -m 0644 packaging/appliance/meta-data "$work/meta-data"
install -m 0644 "$bundle" "$work/hoardarr-release.tar.gz"
grub_maps=()
checksum_map=()

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

# Ubuntu verifies the installer medium against md5sum.txt. Replacing a GRUB
# file without replacing its recorded digest makes an otherwise valid custom
# appliance report a corrupt medium during installation. Include the injected
# seed and release payload as well, so every Hoardarr-owned ISO file is covered.
checksum_file="$work/md5sum.txt"
if xorriso -osirrox on -indev "$base_iso" -extract /md5sum.txt "$checksum_file" >/dev/null 2>&1; then
    update_checksum() {
        local source="$1" relative="$2" digest updated
        digest="$(md5sum -- "$source" | awk '{print $1}')"
        updated="$work/md5sum.updated"
        awk -v digest="$digest" -v target="./$relative" '
            $2 == target || $2 == substr(target, 3) { print digest "  " $2; found=1; next }
            { print }
            END { if (!found) print digest "  " target }
        ' "$checksum_file" >"$updated"
        mv -- "$updated" "$checksum_file"
    }
    for config in boot/grub/grub.cfg boot/grub/loopback.cfg; do
        candidate="$work/$(basename -- "$config")"
        [[ -f "$candidate" ]] && update_checksum "$candidate" "$config"
    done
    update_checksum "$work/user-data" nocloud/user-data
    update_checksum "$work/meta-data" nocloud/meta-data
    update_checksum "$work/hoardarr-release.tar.gz" hoardarr/hoardarr-release.tar.gz
    checksum_map=( -map "$checksum_file" /md5sum.txt )
else
    echo "Ubuntu md5sum.txt was not found" >&2
    exit 1
fi

# Replay the signed Ubuntu ISO's original BIOS/UEFI boot layout. Only the
# NoCloud seed and verified Hoardarr release bundle are injected.
xorriso \
    -indev "$base_iso" \
    -outdev "$output" \
    -map "$work/user-data" /nocloud/user-data \
    -map "$work/meta-data" /nocloud/meta-data \
    -map "$work/hoardarr-release.tar.gz" /hoardarr/hoardarr-release.tar.gz \
    "${grub_maps[@]}" \
    "${checksum_map[@]}" \
    -boot_image any replay \
    -commit

sha256sum -- "$output" >"${output}.sha256"
