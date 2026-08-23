#!/usr/bin/env bash
set -euo pipefail
trap 'rc=$?; printf "FAILED at line %s: %s (rc=%s)\n" "$LINENO" "$BASH_COMMAND" "$rc" >&2' ERR
[[ "$(id -u)" -eq 0 ]] || { echo "requires root in a disposable runner" >&2; exit 1; }
[[ -f /.hoardarr-disposable-runner ]] || {
  echo "refusing drain test: disposable runner marker is missing" >&2
  exit 1
}
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work="$(mktemp -d -t hoardarr-drain.XXXXXXXX)"
managed="/mnt/hoardarr/drain-ci-$$"
loops=()
cleanup() {
  set +e
  for mountpoint in "$managed/source" "$managed/destination"; do
    mountpoint -q "$mountpoint" && umount "$mountpoint"
  done
  for loop in "${loops[@]}"; do losetup -d "$loop" 2>/dev/null || true; done
  [[ "$managed" == /mnt/hoardarr/drain-ci-[0-9]* ]] && rm -rf -- "$managed"
  rm -rf -- "$work"
}
trap cleanup EXIT
protected="$({ findmnt -rn -o SOURCE / /boot /boot/efi 2>/dev/null || true; swapon --noheadings --raw --output NAME 2>/dev/null || true; } | sort -u)"
for name in source destination; do
  image="$work/$name.img"
  truncate -s 512M "$image"
  loop="$(losetup --find --show "$image")"
  loops+=("$loop")
  [[ "$(realpath "$(losetup --noheadings --output BACK-FILE "$loop" | xargs)")" == "$(realpath "$image")" ]]
  ! grep -Fxq "$loop" <<<"$protected"
  [[ -z "$(findmnt -rn -S "$loop" -o TARGET)" ]]
  mkfs.ext4 -F -E lazy_itable_init=1,lazy_journal_init=1,nodiscard "$loop"
  mkdir -p "$managed/$name"
  mount -o noatime "$loop" "$managed/$name"
done
mkdir -p "$repo/dist/validation"
python="${HOARDARR_TEST_PYTHON:-$repo/backend/.venv/bin/python}"
"$python" "$repo/tests/integration/storage_group_drain_lifecycle.py" \
  --source "$managed/source" \
  --destination "$managed/destination" \
  --source-device "${loops[0]}" \
  --destination-device "${loops[1]}" \
  --state "$work/state" \
  --evidence "$repo/dist/validation/storage-group-drain-lifecycle.json"
jq -e '.classification == "VERIFIED IN ISOLATION"' "$repo/dist/validation/storage-group-drain-lifecycle.json"
jq -e '.restart_recovered == true and .source_lifecycle == "retired"' "$repo/dist/validation/storage-group-drain-lifecycle.json"
jq -e '.hashes_before == .hashes_after' "$repo/dist/validation/storage-group-drain-lifecycle.json"
jq -e '.source_mount_read_only == true and .bandwidth_limit_mib_per_second == 16' "$repo/dist/validation/storage-group-drain-lifecycle.json"
jq -e '.io_priority == "background" and .verification_algorithm == "blake3"' "$repo/dist/validation/storage-group-drain-lifecycle.json"
jq -e '.elapsed_seconds >= 0 and .average_mib_per_second >= 0' "$repo/dist/validation/storage-group-drain-lifecycle.json"
