#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

[[ "${EUID}" -eq 0 ]] || { echo "prepare-node must run as root" >&2; exit 2; }
[[ -f /run/hoardarr-two-node-disposable ]] || {
    echo "disposable VM marker is missing" >&2
    exit 2
}

readonly NODE_NAME="${1:?node name required}"
readonly SHARED_WWID="${2:?shared WWID required}"
readonly LOCAL_ONE_SERIAL="HOARDARR_${NODE_NAME}_SSD1"
readonly LOCAL_TWO_SERIAL="HOARDARR_${NODE_NAME}_SSD2"
readonly MAX_TEST_DEVICE_BYTES=$((2 * 1024 * 1024 * 1024))

device_for_serial() {
    local serial="$1"
    lsblk -dn -o PATH,SERIAL | awk -v expected="${serial}" '$2 == expected {print $1}'
}

validate_test_device() {
    local device="$1" expected_serial="$2" size root_source root_parent parent
    [[ "${device}" =~ ^/dev/[a-zA-Z0-9._/-]+$ ]] || {
        echo "invalid test device path: ${device}" >&2
        exit 2
    }
    [[ "$(lsblk -dn -o SERIAL -- "${device}" | xargs)" == "${expected_serial}" ]] || {
        echo "device serial does not match the test topology: ${device}" >&2
        exit 2
    }
    size="$(blockdev --getsize64 "${device}")"
    ((size > 64 * 1024 * 1024 && size <= MAX_TEST_DEVICE_BYTES)) || {
        echo "test device capacity is outside the disposable profile: ${device}" >&2
        exit 2
    }
    root_source="$(findmnt -n -o SOURCE /)"
    root_source="$(readlink -f "${root_source}")"
    root_parent="$(lsblk -dn -o PKNAME -- "${root_source}" 2>/dev/null || true)"
    parent="$(lsblk -dn -o PKNAME -- "${device}" 2>/dev/null || true)"
    [[ "$(readlink -f "${device}")" != "${root_source}" ]] || exit 2
    [[ -z "${root_parent}" || "${parent}" != "${root_parent}" ]] || exit 2
    if lsblk -nro MOUNTPOINTS -- "${device}" | grep -q '[^[:space:]]'; then
        echo "test device is already mounted: ${device}" >&2
        exit 2
    fi
}

local_one="$(device_for_serial "${LOCAL_ONE_SERIAL}")"
local_two="$(device_for_serial "${LOCAL_TWO_SERIAL}")"
[[ -n "${local_one}" && -n "${local_two}" && "${local_one}" != "${local_two}" ]] || {
    echo "the two local virtual SSDs were not discovered" >&2
    exit 2
}
validate_test_device "${local_one}" "${LOCAL_ONE_SERIAL}"
validate_test_device "${local_two}" "${LOCAL_TWO_SERIAL}"

local_devices=("${local_one}" "${local_two}")
for index in 1 2; do
    device="${local_devices[index - 1]}"
    wipefs --all -- "${device}"
    mkfs.ext4 -F -E lazy_itable_init=1,lazy_journal_init=1,nodiscard \
        -L "hoardarr-${NODE_NAME,,}-${index}" "${device}"
    install -d -m 0750 "/srv/hoardarr/${NODE_NAME,,}/member-${index}"
    mount -o noatime "${device}" "/srv/hoardarr/${NODE_NAME,,}/member-${index}"
done
install -d -m 0750 "/srv/hoardarr/${NODE_NAME,,}/local"
mergerfs -o category.create=mfs,category.search=ff,minfreespace=32M,use_ino,cache.files=off \
    "/srv/hoardarr/${NODE_NAME,,}/member-1:/srv/hoardarr/${NODE_NAME,,}/member-2" \
    "/srv/hoardarr/${NODE_NAME,,}/local"

mapfile -t shared_paths < <(lsblk -dn -o PATH,SERIAL | awk '$2 == "HOARDARR_SHARED" {print $1}')
[[ "${#shared_paths[@]}" -eq 2 ]] || {
    echo "exactly two shared-LUN paths are required" >&2
    exit 2
}
for shared_path in "${shared_paths[@]}"; do
    validate_test_device "${shared_path}" "HOARDARR_SHARED"
    observed_wwid="$(/lib/udev/scsi_id --whitelisted --device="${shared_path}")"
    [[ "${observed_wwid}" == "${SHARED_WWID}" ]] || {
        echo "shared path WWID mismatch: ${shared_path}" >&2
        exit 2
    }
done

cat >/etc/multipath.conf <<EOF
defaults {
    find_multipaths no
    user_friendly_names no
}
blacklist_exceptions {
    wwid "${SHARED_WWID}"
}
multipaths {
    multipath {
        wwid "${SHARED_WWID}"
        alias "hoardarr-shared"
        path_grouping_policy multibus
        path_selector "service-time 0"
        failback followover
        no_path_retry fail
    }
}
EOF
systemctl restart multipathd.service
multipath -v2
udevadm settle --timeout=60
[[ -b /dev/mapper/hoardarr-shared ]] || {
    multipath -ll >&2 || true
    echo "shared multipath mapper was not created" >&2
    exit 2
}

if [[ "${NODE_NAME}" == "A" ]]; then
    if ! blkid -s TYPE -o value /dev/mapper/hoardarr-shared | grep -qx ext4; then
        wipefs --all -- /dev/mapper/hoardarr-shared
        mkfs.ext4 -F -E lazy_itable_init=1,lazy_journal_init=1,nodiscard \
            -L hoardarr-two-node-shared /dev/mapper/hoardarr-shared
    fi
    install -d -m 0750 /srv/hoardarr/shared
    mount -o noatime /dev/mapper/hoardarr-shared /srv/hoardarr/shared
fi

local_one_uuid="$(blkid -s UUID -o value "${local_one}")"
local_two_uuid="$(blkid -s UUID -o value "${local_two}")"
shared_uuid="$(blkid -s UUID -o value /dev/mapper/hoardarr-shared)"
python=/usr/lib/hoardarr/venv/bin/python
helper=/usr/local/libexec/hoardarr-two-node-evidence
"${python}" "${helper}" register-local --node "Node ${NODE_NAME}" \
    --name "Node ${NODE_NAME} SSD 1" --wwid "virtual-${NODE_NAME,,}-ssd1" \
    --path "${local_one}" --mountpoint "/srv/hoardarr/${NODE_NAME,,}/member-1" \
    --filesystem-uuid "${local_one_uuid}"
"${python}" "${helper}" register-local --node "Node ${NODE_NAME}" \
    --name "Node ${NODE_NAME} SSD 2" --wwid "virtual-${NODE_NAME,,}-ssd2" \
    --path "${local_two}" --mountpoint "/srv/hoardarr/${NODE_NAME,,}/member-2" \
    --filesystem-uuid "${local_two_uuid}"
"${python}" "${helper}" register-shared --node "Node ${NODE_NAME}" --wwid "${SHARED_WWID}" \
    --path "${shared_paths[0]}" --path "${shared_paths[1]}" \
    --mountpoint /srv/hoardarr/shared --mapper /dev/mapper/hoardarr-shared \
    --filesystem-uuid "${shared_uuid}"

cat <<EOF
NODE=${NODE_NAME}
LOCAL_ONE=${local_one}
LOCAL_TWO=${local_two}
SHARED_PATH_ONE=${shared_paths[0]}
SHARED_PATH_TWO=${shared_paths[1]}
SHARED_WWID=${SHARED_WWID}
SHARED_UUID=${shared_uuid}
EOF
