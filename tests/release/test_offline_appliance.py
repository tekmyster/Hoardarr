from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build-offline-apt-repository.py"
SPEC = importlib.util.spec_from_file_location("hoardarr_offline_repo", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
offline_repo = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = offline_repo
SPEC.loader.exec_module(offline_repo)


class OfflineApplianceTests(unittest.TestCase):
    @staticmethod
    def _actual_install_fragment(payload: str) -> str:
        match = re.search(
            r'^chroot "\$target" apt-get "\$\{apt_options\[@\]\}" \\\n'
            r'    --yes [^\n]+ install "\$\{exact_roots\[@\]\}"$',
            payload,
            flags=re.MULTILINE,
        )
        if match is None:
            raise AssertionError("missing unique production actual-install command")
        if payload.count(match.group(0)) != 1:
            raise AssertionError("production actual-install command is ambiguous")
        return match.group(0)

    @classmethod
    def _assert_actual_install_contract(cls, payload: str) -> None:
        fragment = cls._actual_install_fragment(payload)
        expected_fragment = (
            'chroot "$target" apt-get "${apt_options[@]}" \\\n'
            '    --yes --no-install-recommends install "${exact_roots[@]}"'
        )
        if fragment != expected_fragment:
            raise AssertionError("production actual-install argv changed")
        required = (
            "sha256sum --check --strict SHA256SUMS",
            'cp -a -- "$source_repo" "$retained_repo"',
            "deb [signed-by=/usr/share/keyrings/hoardarr-offline-archive-keyring.gpg] file:/opt/hoardarr/offline-repository noble main",
            "*.hoardarr-online-disabled",
            '-o "Dir::Etc::sourcelist=/etc/apt/sources.list.d/hoardarr-offline.list"',
            '-o "Dir::Etc::sourceparts=-"',
            '-o "Acquire::Retries=0"',
            '-o "Acquire::http::Proxy=false"',
            '-o "Acquire::https::Proxy=false"',
            "policy-rc.d",
            "AUTO -all",
            'global_filter = [ "r|.*|" ]',
            'devnode ".*"',
            'mapfile -t exact_roots <"$retained_repo/evidence/root-package-versions.txt"',
            'simulation="$(chroot "$target" apt-get "${apt_options[@]}" --simulate --no-install-recommends install "${exact_roots[@]}")"',
            'chroot "$target" apt-get "${apt_options[@]}" --simulate check',
            "package-readback.json",
            "service-policy-readback.json",
        )
        for value in required:
            if value not in payload:
                raise AssertionError(f"missing offline install safeguard: {value}")
        lowered = payload.lower()
        for forbidden in (
            "trusted=yes",
            "allow-unauthenticated",
            "allowinsecurerepositories=true",
        ):
            if forbidden in lowered:
                raise AssertionError(f"signature safeguard weakened: {forbidden}")
        if "http://" in payload or "https://" in payload:
            raise AssertionError("network source introduced into offline payload")
        if "--no-download" in fragment:
            raise AssertionError(
                "actual install cannot acquire from the file repository"
            )

    def test_offline_service_masks_are_classified_and_cleaned_executably(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")

        def shell_function(name: str) -> str:
            match = re.search(
                rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n",
                payload,
                flags=re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"missing production function {name}")
            assert match is not None
            return match.group(0)

        if sys.platform == "win32":
            candidates = (
                pathlib.Path(r"C:\Program Files\Git\bin\bash.exe"),
                pathlib.Path(r"C:\msys64\usr\bin\bash.exe"),
            )
            bash = next((str(path) for path in candidates if path.is_file()), None)
        else:
            bash = shutil.which("bash")
        self.assertIsNotNone(bash, "Bash is required for executable mask regression")
        assert bash is not None

        script = "\n".join(
            (
                "set -euo pipefail",
                "temporary_masks=()",
                "declare -A temporary_mask_inodes=()",
                "declare -A preserved_unit_masks=()",
                "declare -A preserved_unit_mask_inodes=()",
                "declare -A preserved_package_aliases=()",
                "declare -A preserved_package_alias_inodes=()",
                "declare -A preserved_package_alias_targets=()",
                "declare -A preserved_package_alias_canonical_units=()",
                "declare -A policy_guarded_canonical_units=()",
                shell_function("entry_is_root_owned"),
                shell_function("exact_iscsi_alias_parents_are_safe"),
                shell_function("unit_declares_exact_alias"),
                shell_function("is_exact_package_backed_iscsi_alias"),
                shell_function("record_package_backed_iscsi_alias"),
                shell_function("validate_preserved_unit_objects"),
                shell_function("prepare_temporary_unit_mask"),
                shell_function("cleanup_temporary_masks"),
                shell_function("disable_unmasked_units"),
                "reset_tracking() {",
                "  temporary_masks=()",
                "  temporary_mask_inodes=()",
                "  preserved_unit_masks=()",
                "  preserved_unit_mask_inodes=()",
                "  preserved_package_aliases=()",
                "  preserved_package_alias_inodes=()",
                "  preserved_package_alias_targets=()",
                "  preserved_package_alias_canonical_units=()",
                "  policy_guarded_canonical_units=()",
                "}",
                'root="$1"',
                'if command -v cygpath >/dev/null 2>&1; then root="$(cygpath -u -- "$root")"; fi',
                'mkdir -p -- "$root"',
                'target="$root/no-package-root"',
                'mask_root="$target/etc/systemd/system"',
                "",
                "# Newly created exact iscsi.service masks are temporary.",
                'absent="$root/absent/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$absent")"',
                "reset_tracking",
                'prepare_temporary_unit_mask "$absent" iscsi.service',
                '[[ -L "$absent" && "$(readlink -- "$absent")" == /dev/null ]]',
                '[[ "${#temporary_masks[@]}" -eq 1 && "${temporary_masks[0]}" == "$absent" ]]',
                "cleanup_temporary_masks",
                '[[ ! -e "$absent" && ! -L "$absent" ]]',
                'later="$root/later-failure/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$later")"',
                "later_status=0",
                "(",
                "  reset_tracking",
                "  trap cleanup_temporary_masks EXIT",
                '  prepare_temporary_unit_mask "$later" iscsi.service',
                "  exit 79",
                ") || later_status=$?",
                '[[ "$later_status" -eq 79 && ! -e "$later" && ! -L "$later" ]]',
                "",
                "# A pre-existing exact absolute mask is never tracked or recreated.",
                'safe="$root/safe/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$safe")"',
                'ln -s -- /dev/null "$safe"',
                'safe_inode="$(stat -c %i -- "$safe")"',
                "reset_tracking",
                'prepare_temporary_unit_mask "$safe" iscsi.service',
                '[[ "${#temporary_masks[@]}" -eq 0 ]]',
                '[[ "${preserved_unit_masks[iscsi.service]}" == "$safe" ]]',
                "cleanup_temporary_masks",
                '[[ -L "$safe" && "$(readlink -- "$safe")" == /dev/null ]]',
                '[[ "$(stat -c %i -- "$safe")" == "$safe_inode" ]]',
                'safe_failure="$root/safe-failure/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$safe_failure")"',
                'ln -s -- /dev/null "$safe_failure"',
                'safe_failure_inode="$(stat -c %i -- "$safe_failure")"',
                "safe_status=0",
                "(",
                "  reset_tracking",
                "  trap cleanup_temporary_masks EXIT",
                '  prepare_temporary_unit_mask "$safe_failure" iscsi.service',
                "  exit 81",
                ") || safe_status=$?",
                '[[ "$safe_status" -eq 81 ]]',
                '[[ -L "$safe_failure" && "$(readlink -- "$safe_failure")" == /dev/null ]]',
                '[[ "$(stat -c %i -- "$safe_failure")" == "$safe_failure_inode" ]]',
                "",
                "# Every other pre-existing object is rejected without modification.",
                'regular="$root/reject-regular/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$regular")"',
                'printf preserved >"$regular"',
                'regular_sha="$(sha256sum "$regular")"',
                'if prepare_temporary_unit_mask "$regular" iscsi.service; then exit 91; fi',
                '[[ "$(sha256sum "$regular")" == "$regular_sha" ]]',
                'directory="$root/reject-directory/iscsi.service"',
                'mkdir -p -- "$directory"',
                'printf marker >"$directory/preserved"',
                'if prepare_temporary_unit_mask "$directory" iscsi.service; then exit 92; fi',
                '[[ "$(cat "$directory/preserved")" == marker ]]',
                'target="$root/ordinary-target"',
                'printf target >"$target"',
                'ordinary="$root/reject-symlink/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$ordinary")"',
                'ln -s -- "$target" "$ordinary"',
                'ordinary_inode="$(stat -c %i -- "$ordinary")"',
                'if prepare_temporary_unit_mask "$ordinary" iscsi.service; then exit 93; fi',
                '[[ "$(readlink -- "$ordinary")" == "$target" ]]',
                '[[ "$(stat -c %i -- "$ordinary")" == "$ordinary_inode" ]]',
                'dangling="$root/reject-dangling/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$dangling")"',
                'ln -s -- /does/not/exist "$dangling"',
                'if prepare_temporary_unit_mask "$dangling" iscsi.service; then exit 94; fi',
                '[[ "$(readlink -- "$dangling")" == /does/not/exist ]]',
                'relative="$root/reject-relative/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$relative")"',
                'ln -s -- ../../dev/null "$relative"',
                'if prepare_temporary_unit_mask "$relative" iscsi.service; then exit 95; fi',
                '[[ "$(readlink -- "$relative")" == ../../dev/null ]]',
                "",
                "# Mixed ownership cleanup preserves existing and removes only new.",
                'mixed_safe="$root/mixed/iscsi.service"',
                'mixed_new="$root/mixed/iscsid.service"',
                'mkdir -p -- "$(dirname -- "$mixed_safe")"',
                'ln -s -- /dev/null "$mixed_safe"',
                'mixed_inode="$(stat -c %i -- "$mixed_safe")"',
                "reset_tracking",
                'prepare_temporary_unit_mask "$mixed_safe" iscsi.service',
                'prepare_temporary_unit_mask "$mixed_new" iscsid.service',
                '[[ "${#temporary_masks[@]}" -eq 1 && "${temporary_masks[0]}" == "$mixed_new" ]]',
                "cleanup_temporary_masks",
                '[[ -L "$mixed_safe" && "$(readlink -- "$mixed_safe")" == /dev/null ]]',
                '[[ "$(stat -c %i -- "$mixed_safe")" == "$mixed_inode" ]]',
                '[[ ! -e "$mixed_new" && ! -L "$mixed_new" ]]',
                "",
                "# Final disable skips the accepted mask and mutates only the new unit.",
                'lifecycle_safe="$root/lifecycle/iscsi.service"',
                'lifecycle_new="$root/lifecycle/iscsid.service"',
                'mkdir -p -- "$(dirname -- "$lifecycle_safe")"',
                'ln -s -- /dev/null "$lifecycle_safe"',
                'lifecycle_inode="$(stat -c %i -- "$lifecycle_safe")"',
                'disable_log="$root/disable.log"',
                "reset_tracking",
                'prepare_temporary_unit_mask "$lifecycle_safe" iscsi.service',
                'prepare_temporary_unit_mask "$lifecycle_new" iscsid.service',
                "cleanup_temporary_masks",
                "denied_units=(iscsi.service iscsid.service)",
                'target="$root/target"',
                'chroot() { printf \'%s\\n\' "$*" >>"$disable_log"; }',
                "disable_unmasked_units",
                '[[ -L "$lifecycle_safe" && "$(readlink -- "$lifecycle_safe")" == /dev/null ]]',
                '[[ "$(stat -c %i -- "$lifecycle_safe")" == "$lifecycle_inode" ]]',
                '[[ ! -e "$lifecycle_new" && ! -L "$lifecycle_new" ]]',
                '[[ "$(cat "$disable_log")" == "$target systemctl disable iscsid.service" ]]',
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            script_path = pathlib.Path(temporary) / "mask-regression.sh"
            script_path.write_text(script, encoding="utf-8", newline="\n")
            result = subprocess.run(
                [bash, str(script_path), temporary],
                capture_output=True,
                check=False,
                env={**os.environ, "MSYS": "winsymlinks:sys"},
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("command not found", result.stderr)

    def test_package_backed_iscsi_alias_lifecycle_is_fail_closed(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")

        def shell_function(name: str) -> str:
            match = re.search(
                rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n",
                payload,
                flags=re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"missing production function {name}")
            assert match is not None
            return match.group(0)

        if sys.platform == "win32":
            candidates = (
                pathlib.Path(r"C:\Program Files\Git\bin\bash.exe"),
                pathlib.Path(r"C:\msys64\usr\bin\bash.exe"),
            )
            bash = next((str(path) for path in candidates if path.is_file()), None)
        else:
            bash = shutil.which("bash")
        self.assertIsNotNone(bash, "Bash is required for executable alias regression")
        assert bash is not None

        script = "\n".join(
            (
                "set -euo pipefail",
                "temporary_masks=()",
                "declare -A temporary_mask_inodes=()",
                "declare -A preserved_unit_masks=()",
                "declare -A preserved_unit_mask_inodes=()",
                "declare -A preserved_package_aliases=()",
                "declare -A preserved_package_alias_inodes=()",
                "declare -A preserved_package_alias_targets=()",
                "declare -A preserved_package_alias_canonical_units=()",
                "declare -A policy_guarded_canonical_units=()",
                shell_function("install_service_start_guard"),
                shell_function("entry_is_root_owned"),
                shell_function("exact_iscsi_alias_parents_are_safe"),
                shell_function("unit_declares_exact_alias"),
                shell_function("is_exact_package_backed_iscsi_alias"),
                shell_function("record_package_backed_iscsi_alias"),
                shell_function("validate_preserved_unit_objects"),
                shell_function("prepare_temporary_unit_mask"),
                shell_function("cleanup_temporary_masks"),
                shell_function("cleanup_guard"),
                shell_function("disable_unmasked_units"),
                r"""
root="$1"
if command -v cygpath >/dev/null 2>&1; then root="$(cygpath -u -- "$root")"; fi
mkdir -p -- "$root"
real_stat="$(command -v stat)"
non_root_path=
fail_inode_path=
package_metadata_mode=ok
stat() {
    local format="${2:-}"
    local path="${*: -1}"
    if [[ "${1:-}" == -c && "$format" == %u:%g ]]; then
        if [[ -n "$non_root_path" && "$path" == "$non_root_path" ]]; then
            printf '%s\n' 1000:1000
        else
            printf '%s\n' 0:0
        fi
        return 0
    fi
    if [[ "${1:-}" == -c && "$format" == %i && -n "$fail_inode_path" && \
        "$path" == "$fail_inode_path" ]]; then
        return 1
    fi
    "$real_stat" "$@"
}
dpkg-query() {
    if [[ " $* " == *" -W "* ]]; then
        case "$package_metadata_mode" in
            ok|wrong-owner) printf 'installed\topen-iscsi\n' ;;
            wrong-package) printf 'installed\tother-package\n' ;;
            malformed) printf 'not-a-valid-status\n' ;;
            missing) return 1 ;;
        esac
        return 0
    fi
    if [[ " $* " == *" -S "* ]]; then
        case "$package_metadata_mode" in
            ok|wrong-package) printf 'open-iscsi: /usr/lib/systemd/system/open-iscsi.service\n' ;;
            wrong-owner) printf 'other-package: /usr/lib/systemd/system/open-iscsi.service\n' ;;
            malformed) printf 'ambiguous\nopen-iscsi: /usr/lib/systemd/system/open-iscsi.service\n' ;;
            missing) return 1 ;;
        esac
        return 0
    fi
    return 1
}
reset_tracking() {
    temporary_masks=()
    temporary_mask_inodes=()
    preserved_unit_masks=()
    preserved_unit_mask_inodes=()
    preserved_package_aliases=()
    preserved_package_alias_inodes=()
    preserved_package_alias_targets=()
    preserved_package_alias_canonical_units=()
    policy_guarded_canonical_units=()
}
refresh_md5() {
    local canonical="$target/usr/lib/systemd/system/open-iscsi.service"
    local digest
    digest="$(md5sum -- "$canonical" | awk '{print $1}')"
    printf '%s  %s\n' "$digest" usr/lib/systemd/system/open-iscsi.service \
        >"$target/var/lib/dpkg/info/open-iscsi.md5sums"
}
make_fixture() {
    target="$root/$1"
    mask_root="$target/etc/systemd/system"
    mkdir -p -- \
        "$mask_root" \
        "$target/usr/lib/systemd/system" \
        "$target/var/lib/dpkg/info" \
        "$target/usr/sbin" \
        "$target/opt/hoardarr-install"
    printf '%s\n' \
        '[Unit]' \
        'Description=Open-iSCSI' \
        '[Install]' \
        'WantedBy=sysinit.target' \
        'Alias=iscsi.service' \
        >"$target/usr/lib/systemd/system/open-iscsi.service"
    printf '%s\n' 'Package: open-iscsi' 'Status: install ok installed' \
        >"$target/var/lib/dpkg/status"
    printf '%s\n' /usr/lib/systemd/system/open-iscsi.service \
        >"$target/var/lib/dpkg/info/open-iscsi.list"
    refresh_md5
    ln -s -- /usr/lib/systemd/system/open-iscsi.service "$mask_root/iscsi.service"
    policy="$target/usr/sbin/policy-rc.d"
    policy_backup="$target/opt/hoardarr-install/policy-rc.d.original"
    policy_state=absent
    package_metadata_mode=ok
    non_root_path=
    fail_inode_path=
    reset_tracking
}
expect_alias_rejected_unchanged() {
    local unit="${1:-iscsi.service}"
    local alias="$mask_root/iscsi.service"
    local inode target_before status=0
    inode="$(stat -c %i -- "$alias")"
    target_before="$(readlink -- "$alias")"
    prepare_temporary_unit_mask "$alias" "$unit" >/dev/null 2>&1 || status=$?
    [[ "$status" -ne 0 ]]
    [[ -L "$alias" && "$(readlink -- "$alias")" == "$target_before" ]]
    [[ "$(stat -c %i -- "$alias")" == "$inode" ]]
}

# Exact retained tuple: no replacement, exact inode survives the entire
# pre-finalization lifecycle, and policy-rc.d denies the retained postinst start.
make_fixture exact
alias="$mask_root/iscsi.service"
canonical_override="$mask_root/open-iscsi.service"
wants="$mask_root/sysinit.target.wants/open-iscsi.service"
alias_inode="$(stat -c %i -- "$alias")"
install_service_start_guard
prepare_temporary_unit_mask "$alias" iscsi.service
prepare_temporary_unit_mask "$canonical_override" open-iscsi.service
[[ "${preserved_package_aliases[iscsi.service]}" == "$alias" ]]
[[ "${policy_guarded_canonical_units[open-iscsi.service]}" == "$canonical_override" ]]
[[ ! -e "$canonical_override" && ! -L "$canonical_override" ]]

# Retained open-iscsi.postinst semantics: unmask, enable, then invoke start.
rm -f -- "$canonical_override"
mkdir -p -- "$(dirname -- "$wants")"
ln -s -- /usr/lib/systemd/system/open-iscsi.service "$wants"
postinst_start_status=0
invoke_start() {
    local status=0
    "$policy" open-iscsi.service start || status=$?
    if (( status == 0 )); then
        mkdir -p -- "$target/run"
        : >"$target/run/open-iscsi.started"
    fi
    return "$status"
}
invoke_start || postinst_start_status=$?
[[ "$postinst_start_status" -eq 101 ]]
[[ ! -e "$target/run/open-iscsi.started" ]]
[[ -L "$alias" && "$(readlink -- "$alias")" == /usr/lib/systemd/system/open-iscsi.service ]]
[[ "$(stat -c %i -- "$alias")" == "$alias_inode" ]]
cleanup_temporary_masks
[[ "$(stat -c %i -- "$alias")" == "$alias_inode" ]]

# A later payload failure preserves the original status and alias identity.
failure_status=0
cleanup_guard 79 || failure_status=$?
[[ "$failure_status" -eq 79 ]]
[[ -L "$alias" && "$(stat -c %i -- "$alias")" == "$alias_inode" ]]

# A clean success finalization acts only on the canonical unit, removes the
# vendor alias and wants link, and requires a disabled canonical readback.
make_fixture final
alias="$mask_root/iscsi.service"
canonical_override="$mask_root/open-iscsi.service"
wants="$mask_root/sysinit.target.wants/open-iscsi.service"
mkdir -p -- "$(dirname -- "$wants")"
ln -s -- /usr/lib/systemd/system/open-iscsi.service "$wants"
install_service_start_guard
prepare_temporary_unit_mask "$alias" iscsi.service
prepare_temporary_unit_mask "$canonical_override" open-iscsi.service
cleanup_temporary_masks
disable_log="$target/disable.log"
chroot() {
    [[ "$1" == "$target" && "$2" == systemctl ]]
    if [[ "$3" == disable && "$4" == open-iscsi.service ]]; then
        printf '%s\n' disable-open-iscsi >>"$disable_log"
        rm -f -- "$alias" "$wants"
        return 0
    fi
    if [[ "$3" == is-enabled && "$4" == open-iscsi.service ]]; then
        printf '%s\n' disabled
        return 1
    fi
    printf 'unexpected chroot argv: %s\n' "$*" >&2
    return 97
}
denied_units=(iscsi.service open-iscsi.service)
disable_unmasked_units
[[ ! -e "$alias" && ! -L "$alias" ]]
[[ ! -e "$wants" && ! -L "$wants" ]]
[[ "$(cat "$disable_log")" == disable-open-iscsi ]]

# Canonical disable failure is not ignored and does not remove either link.
make_fixture disable-failure
alias="$mask_root/iscsi.service"
wants="$mask_root/sysinit.target.wants/open-iscsi.service"
mkdir -p -- "$(dirname -- "$wants")"
ln -s -- /usr/lib/systemd/system/open-iscsi.service "$wants"
prepare_temporary_unit_mask "$alias" iscsi.service
chroot() {
    [[ "$3" == disable && "$4" == open-iscsi.service ]]
    return 1
}
denied_units=(iscsi.service open-iscsi.service)
disable_failure_status=0
disable_unmasked_units >/dev/null 2>&1 || disable_failure_status=$?
[[ "$disable_failure_status" -ne 0 ]]
[[ -L "$alias" && -L "$wants" ]]

# A nominal disable that leaves either generated link is rejected.
make_fixture disable-incomplete
alias="$mask_root/iscsi.service"
wants="$mask_root/sysinit.target.wants/open-iscsi.service"
mkdir -p -- "$(dirname -- "$wants")"
ln -s -- /usr/lib/systemd/system/open-iscsi.service "$wants"
prepare_temporary_unit_mask "$alias" iscsi.service
chroot() {
    if [[ "$3" == disable ]]; then return 0; fi
    printf '%s\n' disabled
    return 1
}
denied_units=(iscsi.service open-iscsi.service)
disable_incomplete_status=0
disable_unmasked_units >/dev/null 2>&1 || disable_incomplete_status=$?
[[ "$disable_incomplete_status" -ne 0 ]]
[[ -L "$alias" && -L "$wants" ]]

# Exact tuple negative cases all reject without changing the alias object.
make_fixture wrong-unit
expect_alias_rejected_unchanged other.service

make_fixture relative-target
rm -f -- "$mask_root/iscsi.service"
ln -s -- ../../usr/lib/systemd/system/open-iscsi.service "$mask_root/iscsi.service"
expect_alias_rejected_unchanged

make_fixture alternate-target
rm -f -- "$mask_root/iscsi.service"
ln -s -- /usr/lib/systemd/system/alternate.service "$mask_root/iscsi.service"
expect_alias_rejected_unchanged

make_fixture missing-canonical
rm -f -- "$target/usr/lib/systemd/system/open-iscsi.service"
expect_alias_rejected_unchanged

make_fixture canonical-symlink
rm -f -- "$target/usr/lib/systemd/system/open-iscsi.service"
ln -s -- /dev/null "$target/usr/lib/systemd/system/open-iscsi.service"
expect_alias_rejected_unchanged

make_fixture canonical-directory
rm -f -- "$target/usr/lib/systemd/system/open-iscsi.service"
mkdir -- "$target/usr/lib/systemd/system/open-iscsi.service"
expect_alias_rejected_unchanged

make_fixture non-root-alias
non_root_path="$mask_root/iscsi.service"
expect_alias_rejected_unchanged

make_fixture non-root-canonical
non_root_path="$target/usr/lib/systemd/system/open-iscsi.service"
expect_alias_rejected_unchanged

make_fixture wrong-owner
package_metadata_mode=wrong-owner
expect_alias_rejected_unchanged

make_fixture wrong-package
package_metadata_mode=wrong-package
expect_alias_rejected_unchanged

make_fixture missing-package
package_metadata_mode=missing
expect_alias_rejected_unchanged

make_fixture malformed-package
package_metadata_mode=malformed
expect_alias_rejected_unchanged

make_fixture missing-alias-metadata
sed -i '/^Alias=/d' "$target/usr/lib/systemd/system/open-iscsi.service"
refresh_md5
expect_alias_rejected_unchanged

make_fixture wrong-alias-metadata
sed -i 's/^Alias=.*/Alias=other.service/' "$target/usr/lib/systemd/system/open-iscsi.service"
refresh_md5
expect_alias_rejected_unchanged

make_fixture extra-alias-metadata
sed -i 's/^Alias=.*/Alias=iscsi.service other.service/' \
    "$target/usr/lib/systemd/system/open-iscsi.service"
refresh_md5
expect_alias_rejected_unchanged

make_fixture status-symlink
mv -- "$target/var/lib/dpkg/status" "$target/status-retained"
ln -s -- "$target/status-retained" "$target/var/lib/dpkg/status"
expect_alias_rejected_unchanged

make_fixture package-list-symlink
mv -- "$target/var/lib/dpkg/info/open-iscsi.list" "$target/list-retained"
ln -s -- "$target/list-retained" "$target/var/lib/dpkg/info/open-iscsi.list"
expect_alias_rejected_unchanged

make_fixture missing-md5
rm -f -- "$target/var/lib/dpkg/info/open-iscsi.md5sums"
expect_alias_rejected_unchanged

make_fixture malformed-md5
printf '%s\n' malformed >"$target/var/lib/dpkg/info/open-iscsi.md5sums"
expect_alias_rejected_unchanged

make_fixture parent-symlink
external="$root/parent-symlink-systemd"
mv -- "$target/etc/systemd" "$external"
ln -s -- "$external" "$target/etc/systemd"
expect_alias_rejected_unchanged

# Recorded alias drift is detected before finalization and never overwritten.
make_fixture alias-drift
alias="$mask_root/iscsi.service"
prepare_temporary_unit_mask "$alias" iscsi.service
original_inode="$(stat -c %i -- "$alias")"
mv -- "$alias" "$target/original-alias-retained"
ln -s -- /usr/lib/systemd/system/drifted.service "$alias"
drift_inode="$(stat -c %i -- "$alias")"
drift_status=0
cleanup_temporary_masks >/dev/null 2>&1 || drift_status=$?
[[ "$drift_status" -ne 0 ]]
[[ "$(readlink -- "$alias")" == /usr/lib/systemd/system/drifted.service ]]
[[ "$(stat -c %i -- "$alias")" == "$drift_inode" ]]
[[ "$(stat -c %i -- "$target/original-alias-retained")" == "$original_inode" ]]

# Drift between alias classification and canonical policy-guard registration
# is rejected before the canonical path is accepted.
make_fixture canonical-registration-drift
alias="$mask_root/iscsi.service"
canonical_override="$mask_root/open-iscsi.service"
prepare_temporary_unit_mask "$alias" iscsi.service
mv -- "$alias" "$target/original-alias-retained"
ln -s -- /usr/lib/systemd/system/drifted.service "$alias"
canonical_registration_status=0
prepare_temporary_unit_mask "$canonical_override" open-iscsi.service \
    >/dev/null 2>&1 || canonical_registration_status=$?
[[ "$canonical_registration_status" -ne 0 ]]
[[ ! -e "$canonical_override" && ! -L "$canonical_override" ]]
[[ "$(readlink -- "$alias")" == /usr/lib/systemd/system/drifted.service ]]

# Temporary-mask identity drift fails closed while cleanup continues for peers.
make_fixture mask-drift
first="$mask_root/first.service"
second="$mask_root/second.service"
prepare_temporary_unit_mask "$first" first.service
prepare_temporary_unit_mask "$second" second.service
mv -- "$first" "$target/original-first-mask"
ln -s -- /dev/null "$first"
replacement_inode="$(stat -c %i -- "$first")"
cleanup_status=0
cleanup_temporary_masks >/dev/null 2>&1 || cleanup_status=$?
[[ "$cleanup_status" -ne 0 ]]
[[ -L "$first" && "$(stat -c %i -- "$first")" == "$replacement_inode" ]]
[[ ! -e "$second" && ! -L "$second" ]]

# Identity acquisition fails before the path is published; the exact mask is
# removed and cleanup never indexes an unset associative-array entry.
make_fixture stat-failure
stat_failure="$mask_root/stat-failure.service"
fail_inode_path="$stat_failure"
stat_failure_status=0
prepare_temporary_unit_mask "$stat_failure" stat-failure.service >/dev/null 2>&1 || \
    stat_failure_status=$?
[[ "$stat_failure_status" -ne 0 ]]
[[ ! -e "$stat_failure" && ! -L "$stat_failure" ]]
[[ "${#temporary_masks[@]}" -eq 0 ]]
cleanup_temporary_masks

safe_stat_failure="$mask_root/safe-stat-failure.service"
ln -s -- /dev/null "$safe_stat_failure"
safe_stat_failure_inode="$(stat -c %i -- "$safe_stat_failure")"
fail_inode_path="$safe_stat_failure"
safe_stat_failure_status=0
prepare_temporary_unit_mask "$safe_stat_failure" safe-stat-failure.service \
    >/dev/null 2>&1 || safe_stat_failure_status=$?
fail_inode_path=
[[ "$safe_stat_failure_status" -ne 0 ]]
[[ -L "$safe_stat_failure" && "$(readlink -- "$safe_stat_failure")" == /dev/null ]]
[[ "$(stat -c %i -- "$safe_stat_failure")" == "$safe_stat_failure_inode" ]]
[[ "${#preserved_unit_masks[@]}" -eq 0 ]]

# Cleanup failure is aggregated; an existing payload failure remains exact,
# while an otherwise successful invocation becomes failure.
make_fixture cleanup-original-status
reset_tracking
rm -f -- "$policy"
mkdir -- "$policy"
original_status=0
cleanup_guard 73 >/dev/null 2>&1 || original_status=$?
[[ "$original_status" -eq 73 ]]

make_fixture cleanup-success-status
reset_tracking
rm -f -- "$policy"
mkdir -- "$policy"
success_status=0
cleanup_guard 0 >/dev/null 2>&1 || success_status=$?
[[ "$success_status" -ne 0 ]]
""",
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            script_path = pathlib.Path(temporary) / "alias-regression.sh"
            script_path.write_text(script, encoding="utf-8", newline="\n")
            result = subprocess.run(
                [bash, str(script_path), temporary],
                capture_output=True,
                check=False,
                env={**os.environ, "MSYS": "winsymlinks:sys"},
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("command not found", result.stderr)

    def test_debian_control_metadata_is_parsed_by_field_name(self) -> None:
        control = """Package: snapraid
Version: 12.3-1
Architecture: amd64
Source: snapraid
Depends: libc6 (>= 2.34), libgcc-s1 (>= 3.0)
Homepage: https://www.snapraid.it/
Description: Backup program for disk arrays
"""
        completed = mock.Mock(stdout=control)
        with mock.patch.object(offline_repo, "_run", return_value=completed) as run:
            fields = offline_repo._deb_fields(pathlib.Path("snapraid.deb"))

        run.assert_called_once_with(["dpkg-deb", "-f", "snapraid.deb"])
        self.assertEqual(fields["Package"], "snapraid")
        self.assertEqual(fields["Version"], "12.3-1")
        self.assertEqual(fields["Architecture"], "amd64")
        self.assertEqual(fields["Source"], "snapraid")
        self.assertEqual(fields["Depends"], "libc6 (>= 2.34), libgcc-s1 (>= 3.0)")

    def test_reconciled_plan_covers_profiles_providers_and_all_dispositions(
        self,
    ) -> None:
        plan = offline_repo.build_plan()
        candidates = plan.matrix["candidates"]
        self.assertEqual(len(plan.roots), 109)
        self.assertEqual(len(candidates), 129)
        self.assertEqual(
            {item["disposition"] for item in candidates},
            {
                "included-and-installed",
                "included-but-feature-disabled",
                "sidecar-manual-offline-import",
                "not-supported",
            },
        )
        selected = {item["package"] for item in candidates if item["package"]}
        self.assertEqual(selected, set(plan.roots))
        self.assertIn("snapraid", selected)
        self.assertIn("b3sum", selected)
        self.assertIn("xxhash", selected)
        self.assertIn("lm-sensors", selected)
        self.assertIn("sysstat", selected)
        self.assertIn("pcp", selected)
        self.assertNotIn("dstat", selected)

    def test_compatibility_families_are_explicit_without_changing_product_roots(
        self,
    ) -> None:
        plan = offline_repo.build_plan()
        systemd_members = {
            "systemd",
            "systemd-sysv",
            "systemd-timesyncd",
            "systemd-resolved",
            "udev",
            "libudev1",
            "libsystemd0",
            "libsystemd-shared",
            "libpam-systemd",
            "libnss-systemd",
            "systemd-dev",
        }
        linux_members = {
            "linux-generic",
            "linux-image-generic",
            "linux-headers-generic",
        }
        self.assertEqual(len(plan.roots), 109)
        self.assertEqual(len(plan.compatibility_families), 2)
        families = {family["id"]: family for family in plan.compatibility_families}
        self.assertEqual(set(families), {"systemd-noble", "linux-meta-noble"})
        self.assertEqual(set(families["systemd-noble"]["members"]), systemd_members)
        self.assertEqual(families["systemd-noble"]["exact_dependencies"], {})
        linux_family = families["linux-meta-noble"]
        self.assertEqual(set(linux_family["members"]), linux_members)
        self.assertEqual(
            linux_family["exact_dependencies"],
            {
                "linux-generic": (
                    "linux-image-generic",
                    "linux-headers-generic",
                )
            },
        )
        self.assertTrue(
            all(
                family["version_policy"] == "single-candidate-version"
                for family in families.values()
            )
        )
        self.assertEqual(
            set(plan.roots),
            {item["package"] for item in plan.matrix["candidates"] if item["package"]},
        )
        self.assertEqual(set(plan.roots) & linux_members, {"linux-image-generic"})
        self.assertTrue(systemd_members - set(plan.roots))

    def test_compatibility_family_schema_rejects_unsafe_and_duplicate_values(
        self,
    ) -> None:
        valid = [
            {
                "id": "systemd-noble",
                "members": ["systemd", "systemd-sysv"],
                "version_policy": "single-candidate-version",
            }
        ]
        self.assertEqual(
            offline_repo._compatibility_families(valid)[0]["id"], "systemd-noble"
        )
        invalid_values = (
            [{**valid[0], "members": ["systemd", "../unsafe"]}],
            [valid[0], {**valid[0], "members": ["udev"]}],
            [valid[0], {**valid[0], "id": "udev-noble"}],
            [{**valid[0], "version_policy": "runner-installed"}],
            [{**valid[0], "extra": True}],
            [{**valid[0], "members": []}],
            [
                {
                    **valid[0],
                    "exact_dependencies": {"not-a-member": ["systemd"]},
                }
            ],
            [
                {
                    **valid[0],
                    "exact_dependencies": {"systemd": ["systemd"]},
                }
            ],
            [
                {
                    **valid[0],
                    "exact_dependencies": {"systemd": ["missing-member"]},
                }
            ],
        )
        for value in invalid_values:
            with (
                self.subTest(value=value),
                self.assertRaises(offline_repo.OfflineRepositoryError),
            ):
                offline_repo._compatibility_families(value)

    def test_exact_dependency_parser_is_whitespace_and_alternative_safe(self) -> None:
        version = "6.8.0-138.138"
        self.assertEqual(
            offline_repo._exact_dependency_versions(
                " linux-image-generic   (= 6.8.0-138.138) , "
                "linux-headers-generic (= 6.8.0-138.138), "
                "unrelated:any (>= 1) "
            ),
            {
                "linux-image-generic": version,
                "linux-headers-generic": version,
            },
        )
        self.assertEqual(
            offline_repo._exact_dependency_versions(
                "linux-image-generic (= 6.8.0-138.138) | linux-image-virtual "
                "(= 6.8.0-138.138), linux-headers-generic (>= 6.8.0-138.138)"
            ),
            {},
        )
        with self.assertRaisesRegex(
            offline_repo.OfflineRepositoryError, "unsupported clause"
        ):
            offline_repo._exact_dependency_versions("linux-image-generic (6.8.0)")

    def test_linux_meta_dependency_validation_requires_exact_sibling_versions(
        self,
    ) -> None:
        version = "6.8.0-138.138"
        family = {
            "id": "linux-meta-noble",
            "members": (
                "linux-generic",
                "linux-image-generic",
                "linux-headers-generic",
            ),
            "version_policy": "single-candidate-version",
            "exact_dependencies": {
                "linux-generic": (
                    "linux-image-generic",
                    "linux-headers-generic",
                )
            },
        }
        valid = [
            {
                "name": "linux-generic",
                "version": version,
                "depends": (
                    f"linux-image-generic (= {version}), "
                    f"linux-headers-generic (= {version})"
                ),
            }
        ]
        offline_repo._validate_family_dependencies((family,), valid)
        invalid_depends = (
            f"linux-image-generic (= {version})",
            (
                f"linux-image-generic (= {version}), "
                "linux-headers-generic (= 6.8.0-137.137)"
            ),
            (
                f"linux-image-generic (= {version}), "
                f"linux-headers-generic (>= {version})"
            ),
            (
                f"linux-image-generic (= {version}), "
                f"linux-headers-generic (= {version}) | linux-headers-virtual"
            ),
        )
        for depends in invalid_depends:
            with (
                self.subTest(depends=depends),
                self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "depend exactly"
                ),
            ):
                offline_repo._validate_family_dependencies(
                    (family,), [{**valid[0], "depends": depends}]
                )

    def test_download_closure_pins_roots_and_complete_family_at_one_version(
        self,
    ) -> None:
        plan = offline_repo.PackagePlan(
            roots=("root-package",),
            compatibility_families=(
                {
                    "id": "systemd-noble",
                    "members": ("systemd", "systemd-sysv"),
                    "version_policy": "single-candidate-version",
                },
            ),
            matrix={},
            policy={},
        )
        candidates = {
            "root-package": "1.0",
            "systemd": "255.4-1ubuntu8.17",
            "systemd-sysv": "255.4-1ubuntu8.17",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)

            def run(command: list[str], **_: object) -> mock.Mock:
                archives_arg = next(
                    item for item in command if item.startswith("Dir::Cache::archives=")
                )
                archives = pathlib.Path(archives_arg.split("=", 1)[1])
                for package in candidates:
                    (archives / f"{package}.deb").write_bytes(package.encode())
                return mock.Mock(stdout="", stderr="", returncode=0)

            def fields(path: pathlib.Path) -> dict[str, str]:
                package = path.stem
                return {
                    "Package": package,
                    "Version": candidates[package],
                    "Architecture": "amd64",
                }

            with (
                mock.patch.object(
                    offline_repo,
                    "_candidate",
                    side_effect=lambda name: candidates[name],
                ),
                mock.patch.object(offline_repo, "_run", side_effect=run) as apt_run,
                mock.patch.object(offline_repo, "_deb_fields", side_effect=fields),
            ):
                roots, families, debs = offline_repo._download_closure(plan, root)

        argv = apt_run.call_args.args[0]
        self.assertEqual(roots, {"root-package": "1.0"})
        self.assertEqual(
            families["systemd-noble"],
            {
                "systemd": "255.4-1ubuntu8.17",
                "systemd-sysv": "255.4-1ubuntu8.17",
            },
        )
        self.assertEqual(len(debs), 3)
        self.assertEqual(
            argv[-4:],
            [
                "install",
                "root-package=1.0",
                "systemd=255.4-1ubuntu8.17",
                "systemd-sysv=255.4-1ubuntu8.17",
            ],
        )

    def test_download_closure_rejects_family_version_mismatch_and_omission(
        self,
    ) -> None:
        plan = offline_repo.PackagePlan(
            roots=("root-package",),
            compatibility_families=(
                {
                    "id": "systemd-noble",
                    "members": ("systemd", "systemd-sysv"),
                    "version_policy": "single-candidate-version",
                },
            ),
            matrix={},
            policy={},
        )
        with (
            mock.patch.object(
                offline_repo,
                "_candidate",
                side_effect=lambda name: {
                    "root-package": "1.0",
                    "systemd": "8.17",
                    "systemd-sysv": "8.12",
                }[name],
            ),
            tempfile.TemporaryDirectory() as temporary,
            self.assertRaisesRegex(
                offline_repo.OfflineRepositoryError, "candidate versions differ"
            ),
        ):
            offline_repo._download_closure(plan, pathlib.Path(temporary))

        candidates = {
            "root-package": "1.0",
            "systemd": "8.17",
            "systemd-sysv": "8.17",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)

            def run(command: list[str], **_: object) -> mock.Mock:
                archives = pathlib.Path(
                    next(
                        item
                        for item in command
                        if item.startswith("Dir::Cache::archives=")
                    ).split("=", 1)[1]
                )
                for package in ("root-package", "systemd"):
                    (archives / f"{package}.deb").write_bytes(package.encode())
                return mock.Mock(stdout="", stderr="", returncode=0)

            with (
                mock.patch.object(
                    offline_repo,
                    "_candidate",
                    side_effect=lambda name: candidates[name],
                ),
                mock.patch.object(offline_repo, "_run", side_effect=run),
                mock.patch.object(
                    offline_repo,
                    "_deb_fields",
                    side_effect=lambda path: {
                        "Package": path.stem,
                        "Version": candidates[path.stem],
                        "Architecture": "amd64",
                    },
                ),
                self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError,
                    "omitted required exact inputs: systemd-sysv",
                ),
            ):
                offline_repo._download_closure(plan, root)

    def test_download_closure_rejects_duplicate_binary_identity(self) -> None:
        plan = offline_repo.PackagePlan(
            roots=("systemd",),
            compatibility_families=(),
            matrix={},
            policy={},
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)

            def run(command: list[str], **_: object) -> mock.Mock:
                archives = pathlib.Path(
                    next(
                        item
                        for item in command
                        if item.startswith("Dir::Cache::archives=")
                    ).split("=", 1)[1]
                )
                (archives / "one.deb").write_bytes(b"one")
                (archives / "two.deb").write_bytes(b"two")
                return mock.Mock(stdout="", stderr="", returncode=0)

            with (
                mock.patch.object(offline_repo, "_candidate", return_value="8.17"),
                mock.patch.object(offline_repo, "_run", side_effect=run),
                mock.patch.object(
                    offline_repo,
                    "_deb_fields",
                    return_value={
                        "Package": "systemd",
                        "Version": "8.17",
                        "Architecture": "amd64",
                    },
                ),
                self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "duplicate binary identity"
                ),
            ):
                offline_repo._download_closure(plan, root)

    def test_owner_workbook_aliases_and_superseded_container_rows_are_explicit(
        self,
    ) -> None:
        matrix = offline_repo.build_plan().matrix
        self.assertEqual(
            matrix["owner_workbook"]["sha256"],
            "438991f1a7def5de709beea6337780baf50e2fd5e50f3a9229ef858d8186ed4c",
        )
        self.assertEqual(matrix["command_aliases"]["dd"], "coreutils")
        self.assertEqual(matrix["command_aliases"]["shred"], "coreutils")
        self.assertEqual(matrix["command_aliases"]["wipefs"], "util-linux")
        self.assertEqual(matrix["command_aliases"]["dstat"], "pcp")
        intake = json.loads(
            (ROOT / "packaging" / "offline" / "owner-workbook-intake.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual([item["row"] for item in intake["rows"]], list(range(4, 51)))
        dispositions = {item["candidate"]: item for item in matrix["candidates"]}
        for candidate in (
            "docker-ce",
            "docker-ce-cli",
            "containerd.io",
            "docker-compose-plugin",
        ):
            self.assertEqual(dispositions[candidate]["disposition"], "not-supported")
            self.assertTrue(dispositions[candidate]["reason"])
        self.assertEqual(dispositions["nwipe"]["disposition"], "not-supported")

    def test_every_vendor_tool_is_manual_sidecar_not_silently_downloaded(self) -> None:
        matrix = offline_repo.build_plan().matrix
        sidecars = {
            item["candidate"]
            for item in matrix["candidates"]
            if item["disposition"] == "sidecar-manual-offline-import"
        }
        catalog = json.loads(
            (ROOT / "packaging" / "hardware" / "vendor-tools.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(sidecars, {item["id"] for item in catalog["tools"]})

    def test_autoinstall_is_explicitly_offline_and_installs_payload_before_release(
        self,
    ) -> None:
        user_data = (ROOT / "packaging" / "appliance" / "user-data").read_text(
            encoding="utf-8"
        )
        self.assertIn("fallback: offline-install", user_data)
        self.assertIn("geoip: false", user_data)
        self.assertNotRegex(user_data, r"(?m)^\s+packages:\s*$")
        payload = user_data.index("install-offline-payload.sh /target")
        release = user_data.index("hoardarr-release.tar.gz")
        self.assertLess(payload, release)
        self.assertNotIn("http://", user_data)
        self.assertNotIn("https://", user_data)

    def test_offline_installer_has_independent_service_and_storage_guards(self) -> None:
        installer = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("policy-rc.d", installer)
        self.assertIn("AUTO -all", installer)
        self.assertIn('global_filter = [ "r|.*|" ]', installer)
        self.assertIn('devnode ".*"', installer)
        self.assertIn("--simulate --no-install-recommends", installer)
        self._assert_actual_install_contract(installer)
        self.assertIn("package-readback.json", installer)
        self.assertIn("service-policy-readback.json", installer)
        self.assertIn("sha256sum --check --strict SHA256SUMS", installer)
        self.assertNotIn("curl ", installer)
        self.assertNotIn("wget ", installer)
        verifier = (
            ROOT / "packaging" / "appliance" / "verify-offline-appliance.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("HOARDARR_OFFLINE_EVIDENCE_BEGIN", verifier)
        self.assertIn("list-unit-files", verifier)
        self.assertIn("127.0.0.1:7877/health/ready", verifier)

    def test_actual_install_argv_executes_exact_production_fragment(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        fragment = self._actual_install_fragment(payload)
        if sys.platform == "win32":
            candidates = (
                pathlib.Path(r"C:\Program Files\Git\bin\bash.exe"),
                pathlib.Path(r"C:\msys64\usr\bin\bash.exe"),
            )
            bash = next((str(path) for path in candidates if path.is_file()), None)
        else:
            bash = shutil.which("bash")
        self.assertIsNotNone(
            bash, "Bash is required for actual-install argv regression"
        )
        assert bash is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            fragment_path = root / "production-fragment.sh"
            log_path = root / "argv.bin"
            fragment_path.write_text(fragment + "\n", encoding="utf-8")
            script = "\n".join(
                (
                    "set -euo pipefail",
                    'target="/target"',
                    "apt_options=(",
                    '  -o "Dir::Etc::sourcelist=/etc/apt/sources.list.d/hoardarr-offline.list"',
                    '  -o "Dir::Etc::sourceparts=-"',
                    '  -o "Acquire::Languages=none"',
                    '  -o "Acquire::Retries=0"',
                    '  -o "Acquire::http::Proxy=false"',
                    '  -o "Acquire::https::Proxy=false"',
                    ")",
                    'exact_roots=("alpha=1" "beta=2")',
                    'log="$1"',
                    'chroot() { printf \'%s\\0\' "$@" >"$log"; }',
                    f'source "{fragment_path.as_posix()}"',
                )
            )
            result = subprocess.run(
                [bash, "-c", script, "bash", log_path.as_posix()],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            argv = log_path.read_bytes().split(b"\0")[:-1]
            self.assertEqual(
                [value.decode() for value in argv],
                [
                    "/target",
                    "apt-get",
                    "-o",
                    "Dir::Etc::sourcelist=/etc/apt/sources.list.d/hoardarr-offline.list",
                    "-o",
                    "Dir::Etc::sourceparts=-",
                    "-o",
                    "Acquire::Languages=none",
                    "-o",
                    "Acquire::Retries=0",
                    "-o",
                    "Acquire::http::Proxy=false",
                    "-o",
                    "Acquire::https::Proxy=false",
                    "--yes",
                    "--no-install-recommends",
                    "install",
                    "alpha=1",
                    "beta=2",
                ],
            )

    def test_actual_install_contract_rejects_safeguard_regressions(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        self._assert_actual_install_contract(payload)
        mutations = {
            "network source": payload.replace(
                "file:/opt/hoardarr/offline-repository",
                "https://example.invalid/repository",
            ),
            "missing signed-by": payload.replace("signed-by=", "keyring="),
            "signature weakening": payload.replace(
                "noble main", "noble main trusted=yes", 1
            ),
            "missing retry guard": payload.replace(
                "Acquire::Retries=0", "Acquire::Retries=1"
            ),
            "missing HTTP proxy guard": payload.replace(
                "Acquire::http::Proxy=false", "Acquire::http::Proxy=direct"
            ),
            "missing HTTPS proxy guard": payload.replace(
                "Acquire::https::Proxy=false", "Acquire::https::Proxy=direct"
            ),
            "root loss": payload.replace(
                '"${exact_roots[@]}"', '"${exact_roots[0]}"', 1
            ),
            "service guard loss": payload.replace("policy-rc.d", "policy-start.d"),
            "md storage guard loss": payload.replace("AUTO -all", "AUTO +all"),
            "LVM storage guard loss": payload.replace(
                'global_filter = [ "r|.*|" ]', 'global_filter = [ "a|.*|" ]'
            ),
            "multipath storage guard loss": payload.replace(
                'devnode ".*"', 'devnode "^$"'
            ),
            "no-download reintroduced": payload.replace(
                "--yes --no-install-recommends install",
                "--yes --no-download --no-install-recommends install",
            ),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name), self.assertRaises(AssertionError):
                self._assert_actual_install_contract(mutation)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux APT")
    def test_signed_local_file_repository_actual_install(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        fragment = self._actual_install_fragment(payload)
        required = ("apt-get", "dpkg-deb", "dpkg-query", "gpg", "gzip", "sudo")
        missing = [command for command in required if shutil.which(command) is None]
        self.assertEqual(missing, [], f"missing Linux integration tools: {missing}")
        sudo = subprocess.run(
            ["sudo", "-n", "true"], text=True, capture_output=True, check=False
        )
        self.assertEqual(sudo.returncode, 0, sudo.stderr)
        with tempfile.TemporaryDirectory() as temporary:
            fragment_path = pathlib.Path(temporary) / "production-fragment.sh"
            fragment_path.write_text(fragment + "\n", encoding="utf-8")
            result = subprocess.run(
                [
                    "sudo",
                    "-n",
                    "bash",
                    str(
                        ROOT / "tests" / "appliance" / "test-local-file-apt-install.sh"
                    ),
                    str(fragment_path),
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=180,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"(?m)^old_no_download_status=[1-9][0-9]*$")
        self.assertIn("archive_cache_was_empty=true", result.stdout)
        self.assertIn("actual_install_file_acquisition=true", result.stdout)
        self.assertIn("network_sources=0", result.stdout)
        self.assertIn("package_readback=installed\t1.0\tall", result.stdout)

    def test_appliance_builder_embeds_verified_repository_and_emits_complete_tree_manifest(
        self,
    ) -> None:
        builder = (ROOT / "scripts" / "build-appliance.sh").read_text(encoding="utf-8")
        self.assertIn("build-offline-apt-repository.py verify", builder)
        self.assertIn("/hoardarr/offline-repository", builder)
        self.assertIn("hoardarr/install-offline-payload.sh", builder)
        self.assertIn("offline repository contains a symbolic link", builder)
        self.assertIn('>"${output}.tree-sha256"', builder)

    def test_two_clean_no_nic_install_passes_are_manual_release_gates(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "appliance.yml").read_text(
            encoding="utf-8"
        )
        harness = (ROOT / "tests" / "appliance" / "run-offline-iso-pass.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("offline_validation_mode:", workflow)
        self.assertIn("default: two-pass", workflow)
        self.assertIn("- diagnostic-pass-1", workflow)
        self.assertIn(
            "inputs.offline_validation_mode == 'diagnostic-pass-1'"
            ' && \'["pass-1"]\' || \'["pass-1","pass-2"]\'',
            workflow,
        )
        self.assertIn("HOARDARR_OFFLINE_DIAGNOSTIC_MODE", workflow)
        self.assertIn(
            "'hoardarr-offline-diagnostic-pass-1'"
            " || format('hoardarr-offline-{0}', matrix.pass)",
            workflow,
        )
        retention = workflow.split(
            "- name: Retain offline install inputs for no-network validation", 1
        )[1].split("\n\n  offline-install:", 1)[0]
        self.assertNotIn("if:", retention)
        self.assertIn("name: hoardarr-offline-install-inputs", retention)
        self.assertIn("compression-level: 0", retention)
        self.assertIn("retention-days: 3", retention)
        self.assertEqual(
            [
                line.strip()
                for line in retention.splitlines()
                if line.strip().startswith("dist/")
            ],
            ["dist/hoardarr-release.tar.gz", "dist/offline-repository"],
        )
        offline_install = workflow.split("\n  offline-install:\n", 1)[1]
        self.assertTrue(
            offline_install.startswith(
                "    if: github.event_name == 'workflow_dispatch'\n"
            )
        )
        self.assertEqual(workflow.count("\n  offline-install:\n"), 1)
        self.assertEqual(
            offline_install.count(
                "inputs.offline_validation_mode == 'diagnostic-pass-1'"
            ),
            3,
        )
        self.assertIn("$RUNNER_TEMP/ci-signing-key", workflow)
        self.assertIn("$RUNNER_TEMP/ubuntu-vulnerability-status.json", workflow)
        self.assertIn("-nic none", harness)
        self.assertIn("readonly=on", harness)
        self.assertIn("protected-before.sha256", harness)
        self.assertIn("protected-after.sha256", harness)
        self.assertIn("HOARDARR_OFFLINE_READY", harness)
        self.assertIn("installer-monitor.log", harness)
        self.assertIn("installer-process.tsv", harness)
        self.assertIn("process-identities.txt", harness)
        self.assertIn("qemu-installer-stderr.log", harness)
        self.assertIn("qemu-img-info.json", harness)
        self.assertIn("installer_timeout", harness)
        self.assertIn("timeout --signal=TERM --kill-after=30s 2700s", harness)
        self.assertIn('"acceptance_eligible": False', harness)
        self.assertIn('"bounded_runner_exit_status"', harness)
        self.assertIn("protected-diff.txt", harness)
        self.assertIn("frames/SHA256SUMS", harness)
        self.assertIn("evidence-finalization.txt", harness)
        self.assertIn("diagnostic evidence finalization was incomplete", harness)
        diagnostic_body = harness.split("run_diagnostic_installer() {", 1)[1].split(
            "\n}\n\ninstall_start=", 1
        )[0]
        self.assertLess(
            diagnostic_body.index('wait "$runner_pid"'),
            diagnostic_body.index("finalize_diagnostic_evidence"),
        )
        self.assertIn("timeout --signal=TERM --kill-after=30s 45m", harness)
        success_path = harness.split('if [[ "$diagnostic_mode" == true ]]; then', 2)[2]
        self.assertLess(
            success_path.index("write_diagnostic_metadata installer_reboot_checkpoint"),
            success_path.index("finalize_diagnostic_evidence"),
        )
        self.assertNotIn('cat >"$output/run.json"', success_path.split("else", 1)[0])

    def test_ci_payload_wrapper_preserves_exact_argv_and_status(self) -> None:
        user_data = (ROOT / "tests" / "appliance" / "offline-user-data").read_text(
            encoding="utf-8"
        )
        exact_argv = (
            "/cdrom/hoardarr/install-offline-payload.sh "
            "/target /cdrom/hoardarr/offline-repository"
        )
        self.assertEqual(user_data.count(exact_argv), 1)
        self.assertIn('pipeline_status=("${PIPESTATUS[@]}")', user_data)
        self.assertIn('payload_status="${pipeline_status[0]}"', user_data)
        self.assertIn(
            '[[ "${pipeline_status[1]}" -eq 0 ]] || capture_ok=false', user_data
        )
        self.assertIn('exit "$payload_status"', user_data)
        payload_tail = user_data.split('pipeline_status=("${PIPESTATUS[@]}")', 1)[1]
        self.assertNotIn("set -e", payload_tail.split('exit "$payload_status"', 1)[0])
        self.assertIn("/target/var/log/hoardarr-offline-payload.log", user_data)
        self.assertIn("[[ -c /dev/ttyS0 && -w /dev/ttyS0 ]]", user_data)
        self.assertIn("stty -F /dev/ttyS0 -opost || exit 126", user_data)
        self.assertIn(
            "stty -F /dev/ttyS0 -a | grep -qw -- -opost || exit 127", user_data
        )
        self.assertNotIn("|| true", payload_tail.split('exit "$payload_status"', 1)[0])
        for required_operation in (
            "emit_both HOARDARR_OFFLINE_PAYLOAD_END || capture_ok=false",
            'emit_both "HOARDARR_OFFLINE_PAYLOAD_EXIT=$payload_status" || capture_ok=false',
            'sync "$target_log" || capture_ok=false',
            'target_size="$(wc -c <"$target_log")" || capture_ok=false',
            'target_sha256="$(sha256sum "$target_log" | cut -d" " -f1)" || capture_ok=false',
        ):
            self.assertIn(required_operation, user_data)
        complete_guard = user_data.rsplit(
            'printf "%s\\n" HOARDARR_OFFLINE_PAYLOAD_CAPTURE_COMPLETE', 1
        )[0].rsplit('if [[ "$capture_ok" == true ]]', 1)
        self.assertEqual(len(complete_guard), 2)
        for marker in (
            "HOARDARR_OFFLINE_PAYLOAD_BEGIN",
            "HOARDARR_OFFLINE_PAYLOAD_END",
            "HOARDARR_OFFLINE_PAYLOAD_EXIT=",
            "HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE=",
            "HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SHA256=",
            "HOARDARR_OFFLINE_PAYLOAD_CAPTURE_COMPLETE",
        ):
            self.assertIn(marker, user_data)

    def test_payload_capture_parser_is_fail_closed(self) -> None:
        parser = ROOT / "tests" / "appliance" / "parse-offline-payload-capture.py"

        def run_capture(
            serial: bytes,
        ) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            root = pathlib.Path(temporary.name)
            serial_path = root / "serial.log"
            serial_path.write_bytes(serial)
            result = subprocess.run(
                [
                    sys.executable,
                    str(parser),
                    str(serial_path),
                    str(root / "console.log"),
                    str(root / "target.log"),
                    str(root / "capture.json"),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            return result, root

        def complete(status: int, payload: bytes = b"decisive failure\n") -> bytes:
            target = (
                b"HOARDARR_OFFLINE_PAYLOAD_BEGIN\n"
                + payload
                + b"HOARDARR_OFFLINE_PAYLOAD_END\n"
                + f"HOARDARR_OFFLINE_PAYLOAD_EXIT={status}\n".encode()
            )
            return (
                b"prefix\n"
                + target
                + f"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE={len(target)}\n".encode()
                + b"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SHA256="
                + hashlib.sha256(target).hexdigest().encode()
                + b"\nHOARDARR_OFFLINE_PAYLOAD_CAPTURE_COMPLETE\n"
            )

        nonzero, nonzero_root = run_capture(complete(17))
        self.assertEqual(nonzero.returncode, 10)
        self.assertEqual(
            json.loads((nonzero_root / "capture.json").read_text())["payload_status"],
            17,
        )
        self.assertIn(b"decisive failure", (nonzero_root / "target.log").read_bytes())

        zero, _ = run_capture(complete(0))
        self.assertEqual(zero.returncode, 0)
        crlf_stream = complete(17).replace(b"\n", b"\r\n")
        crlf, crlf_root = run_capture(crlf_stream)
        self.assertEqual(crlf.returncode, 10)
        crlf_metadata = json.loads((crlf_root / "capture.json").read_text())
        self.assertEqual(crlf_metadata["serial_transform"], "onlcr_crlf")
        expected_target = (
            complete(17)
            .split(b"prefix\n", 1)[1]
            .split(b"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE=", 1)[0]
        )
        self.assertEqual((crlf_root / "target.log").read_bytes(), expected_target)
        self.assertEqual(
            crlf_metadata["target_log_sha256"],
            hashlib.sha256(expected_target).hexdigest(),
        )
        absent, _ = run_capture(b"")
        self.assertEqual(absent.returncode, 20)
        partial, _ = run_capture(b"HOARDARR_OFFLINE_PAYLOAD_BEGIN\n")
        self.assertEqual(partial.returncode, 20)
        malformed_bytes = complete(1).replace(
            b"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE=",
            b"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE=999",
        )
        malformed, malformed_root = run_capture(malformed_bytes)
        self.assertEqual(malformed.returncode, 21)
        self.assertFalse((malformed_root / "capture.json").exists())
        duplicate, _ = run_capture(
            complete(1).replace(
                b"HOARDARR_OFFLINE_PAYLOAD_BEGIN\n",
                b"HOARDARR_OFFLINE_PAYLOAD_BEGIN\nHOARDARR_OFFLINE_PAYLOAD_BEGIN\n",
            )
        )
        self.assertEqual(duplicate.returncode, 21)
        duplicate_complete, _ = run_capture(
            complete(1) + b"HOARDARR_OFFLINE_PAYLOAD_CAPTURE_COMPLETE\n"
        )
        self.assertEqual(duplicate_complete.returncode, 21)
        malformed_then_complete, malformed_then_root = run_capture(
            malformed_bytes + complete(1)
        )
        self.assertEqual(malformed_then_complete.returncode, 21)
        self.assertFalse((malformed_then_root / "capture.json").exists())
        arbitrary_cr, arbitrary_cr_root = run_capture(
            complete(1).replace(b"decisive failure", b"decisive\rfailure")
        )
        self.assertEqual(arbitrary_cr.returncode, 21)
        self.assertFalse((arbitrary_cr_root / "capture.json").exists())

    def test_diagnostic_early_stop_requires_valid_nonzero_capture(self) -> None:
        harness = (ROOT / "tests" / "appliance" / "run-offline-iso-pass.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("if (( payload_parser_status == 10 )); then", harness)
        self.assertIn("payload_failure_observed=true\n            sleep 5", harness)
        self.assertIn("for _ in {1..3}; do", harness)
        self.assertIn('if [[ "$payload_failure_observed" == true ]]', harness)
        self.assertIn("offline_payload_failure_observed", harness)
        self.assertIn("offline_payload_capture_invalid", harness)
        self.assertIn(
            'if [[ "$payload_failure_observed" == true && "$payload_capture_invalid" != true ]]',
            harness,
        )
        self.assertNotIn(
            "payload_parser_status == 0 )); then\n            payload_failure_observed",
            harness,
        )
        parser_guard = harness.split('if [[ "$diagnostic_mode" == true ]]; then', 1)[1]
        self.assertIn('payload_capture_parser="$script_root/', parser_guard)
        self.assertNotIn(
            "parse-offline-payload-capture.py", harness.split(parser_guard, 1)[0]
        )

    def test_two_compatibility_families_emit_deterministic_verifiable_evidence(
        self,
    ) -> None:
        required = (
            "dists/noble/InRelease",
            "dists/noble/Release",
            "dists/noble/Release.gpg",
            "dists/noble/main/binary-amd64/Packages.gz",
            "evidence/SBOM.cdx.json",
            "evidence/provenance.json",
            "evidence/root-package-versions.txt",
            "evidence/vulnerability-status.json",
            "hoardarr-offline-archive-keyring.gpg",
        )
        version = "candidate-version-from-apt"
        families = (
            {
                "id": "systemd-noble",
                "members": ("udev", "systemd-dev"),
                "version_policy": "single-candidate-version",
                "exact_dependencies": {},
            },
            {
                "id": "linux-meta-noble",
                "members": (
                    "linux-generic",
                    "linux-image-generic",
                    "linux-headers-generic",
                ),
                "version_policy": "single-candidate-version",
                "exact_dependencies": {
                    "linux-generic": (
                        "linux-image-generic",
                        "linux-headers-generic",
                    )
                },
            },
        )
        declarations = [
            {
                "id": "systemd-noble",
                "members": ["udev", "systemd-dev"],
                "version_policy": "single-candidate-version",
            },
            {
                "id": "linux-meta-noble",
                "members": [
                    "linux-generic",
                    "linux-image-generic",
                    "linux-headers-generic",
                ],
                "version_policy": "single-candidate-version",
                "exact_dependencies": {
                    "linux-generic": [
                        "linux-image-generic",
                        "linux-headers-generic",
                    ]
                },
            },
        ]
        plan = offline_repo.PackagePlan(
            roots=("linux-image-generic",),
            compatibility_families=families,
            matrix={"compatibility_families": declarations},
            policy={},
        )
        dependency = (
            f"linux-image-generic (= {version}), linux-headers-generic (= {version})"
        )
        records = [
            {"name": "udev", "version": version, "architecture": "amd64"},
            {"name": "systemd-dev", "version": version, "architecture": "all"},
            {
                "name": "linux-generic",
                "version": version,
                "architecture": "amd64",
                "declared_dependencies": {"depends": dependency},
            },
            {
                "name": "linux-image-generic",
                "version": version,
                "architecture": "amd64",
            },
            {
                "name": "linux-headers-generic",
                "version": version,
                "architecture": "amd64",
            },
        ]
        family_versions = {
            family["id"]: {member: version for member in family["members"]}
            for family in families
        }
        evidence = offline_repo._resolved_family_evidence(
            plan, family_versions, records
        )
        self.assertEqual(
            evidence,
            offline_repo._resolved_family_evidence(plan, family_versions, records),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for relative in required:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative, encoding="utf-8")
            packages = "".join(
                f"Package: {record['name']}\nVersion: {version}\n"
                f"Architecture: {record['architecture']}\n\n"
                for record in records
            )
            (root / "dists/noble/main/binary-amd64/Packages").write_text(
                packages, encoding="utf-8"
            )
            (root / "evidence/package-manifest.json").write_text(
                json.dumps({"schema_version": 1, "packages": records}),
                encoding="utf-8",
            )
            (root / "evidence/compatibility-matrix.json").write_text(
                json.dumps(plan.matrix), encoding="utf-8"
            )
            (root / "evidence/compatibility-families.json").write_text(
                json.dumps(evidence), encoding="utf-8"
            )
            offline_repo._write_tree_manifest(root)
            with mock.patch.object(offline_repo.shutil, "which", return_value=None):
                offline_repo.verify_repository(root)
                records[2]["declared_dependencies"]["depends"] = (
                    f"linux-image-generic (= {version}) | linux-image-virtual, "
                    f"linux-headers-generic (= {version})"
                )
                (root / "evidence/package-manifest.json").write_text(
                    json.dumps({"schema_version": 1, "packages": records}),
                    encoding="utf-8",
                )
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "depend exactly"
                ):
                    offline_repo.verify_repository(root)

    def test_repository_tree_verification_rejects_tampering(self) -> None:
        required = (
            "dists/noble/InRelease",
            "dists/noble/Release",
            "dists/noble/Release.gpg",
            "dists/noble/main/binary-amd64/Packages",
            "dists/noble/main/binary-amd64/Packages.gz",
            "evidence/SBOM.cdx.json",
            "evidence/compatibility-matrix.json",
            "evidence/compatibility-families.json",
            "evidence/package-manifest.json",
            "evidence/provenance.json",
            "evidence/root-package-versions.txt",
            "evidence/vulnerability-status.json",
            "hoardarr-offline-archive-keyring.gpg",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for relative in required:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative, encoding="utf-8")
            (root / "evidence" / "package-manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "packages": [
                            {
                                "name": "udev",
                                "version": "8.17",
                                "architecture": "amd64",
                            },
                            {
                                "name": "systemd-dev",
                                "version": "8.17",
                                "architecture": "all",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            nonalphabetical_plan = offline_repo.PackagePlan(
                roots=(),
                compatibility_families=(
                    {
                        "id": "systemd-noble",
                        "members": ("udev", "systemd-dev"),
                        "version_policy": "single-candidate-version",
                    },
                ),
                matrix={},
                policy={},
            )
            generated_family_evidence = offline_repo._resolved_family_evidence(
                nonalphabetical_plan,
                {"systemd-noble": {"udev": "8.17", "systemd-dev": "8.17"}},
                [
                    {"name": "udev", "version": "8.17", "architecture": "amd64"},
                    {
                        "name": "systemd-dev",
                        "version": "8.17",
                        "architecture": "all",
                    },
                ],
            )
            (root / "evidence" / "compatibility-families.json").write_text(
                json.dumps(generated_family_evidence),
                encoding="utf-8",
            )
            (root / "evidence" / "compatibility-matrix.json").write_text(
                json.dumps(
                    {
                        "compatibility_families": [
                            {
                                "id": "systemd-noble",
                                "members": ["udev", "systemd-dev"],
                                "version_policy": "single-candidate-version",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "dists/noble/main/binary-amd64/Packages").write_text(
                "Package: udev\nVersion: 8.17\nArchitecture: amd64\n\n"
                "Package: systemd-dev\nVersion: 8.17\nArchitecture: all\n\n",
                encoding="utf-8",
            )
            offline_repo._write_tree_manifest(root)
            with mock.patch.object(offline_repo.shutil, "which", return_value=None):
                offline_repo.verify_repository(root)
                family_path = root / "evidence" / "compatibility-families.json"
                family_document = json.loads(family_path.read_text(encoding="utf-8"))
                family_document["schema_version"] = 2
                family_path.write_text(json.dumps(family_document), encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "evidence schema"
                ):
                    offline_repo.verify_repository(root)
                family_document["schema_version"] = 1
                family_path.write_text(json.dumps(family_document), encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                offline_repo.verify_repository(root)
                family_document["families"][0]["members"][0] = "udev=8.17"
                family_path.write_text(json.dumps(family_document), encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError,
                    "incomplete or incoherent",
                ):
                    offline_repo.verify_repository(root)
                family_document["families"][0]["members"][0] = {
                    "name": "udev",
                    "version": "8.17",
                }
                family_path.write_text(json.dumps(family_document), encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                offline_repo.verify_repository(root)
                package_manifest_path = root / "evidence" / "package-manifest.json"
                package_document = json.loads(
                    package_manifest_path.read_text(encoding="utf-8")
                )
                package_document["packages"] = package_document["packages"][1:]
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "incomplete or incoherent"
                ):
                    offline_repo.verify_repository(root)
                package_document["packages"].insert(
                    0,
                    {"name": "udev", "version": "8.17", "architecture": "amd64"},
                )
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                packages_path = root / "dists/noble/main/binary-amd64/Packages"
                packages_document = packages_path.read_text(encoding="utf-8")
                package_document["packages"].append(
                    {
                        "name": "systemd-dev",
                        "version": "8.17",
                        "architecture": "amd64",
                    }
                )
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "incomplete or incoherent"
                ):
                    offline_repo.verify_repository(root)
                package_document["packages"].pop()
                package_document["packages"][1]["architecture"] = "i386"
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                packages_path.write_text(
                    packages_document.replace(
                        "Architecture: all", "Architecture: i386"
                    ),
                    encoding="utf-8",
                )
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "incomplete or incoherent"
                ):
                    offline_repo.verify_repository(root)
                package_document["packages"][1]["architecture"] = "all"
                package_document["packages"].append(
                    dict(package_document["packages"][1])
                )
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                packages_path.write_text(packages_document, encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "invalid binary identities"
                ):
                    offline_repo.verify_repository(root)
                package_document["packages"].pop()
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                packages_path.write_text(
                    packages_document.split("\n\n", 1)[1], encoding="utf-8"
                )
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "incomplete or incoherent"
                ):
                    offline_repo.verify_repository(root)
                packages_path.write_text(packages_document, encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                offline_repo.verify_repository(root)
                (root / "evidence" / "package-manifest.json").write_text(
                    "tampered", encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "digest mismatch"
                ):
                    offline_repo.verify_repository(root)

    def test_deferred_release_install_does_not_enable_lldpd(self) -> None:
        installer = (ROOT / "scripts" / "install-release-bundle.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("systemctl disable lldpd.service", installer)
        deferred = installer.split('if [[ "${DEFER_SERVICE_START}" == "true" ]]', 1)[1]
        self.assertNotIn("systemctl enable lldpd.service", deferred.split("else", 1)[0])

    def test_release_bundle_emits_dependency_sbom_license_and_provenance_evidence(
        self,
    ) -> None:
        builder = (ROOT / "scripts" / "build-release-bundle.py").read_text(
            encoding="utf-8"
        )
        installer = (ROOT / "scripts" / "install-release-bundle.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('staging / "evidence" / "SBOM.cdx.json"', builder)
        self.assertIn('staging / "evidence" / "python-licenses.json"', builder)
        self.assertIn('staging / "evidence" / "npm-licenses.json"', builder)
        self.assertIn('staging / "evidence" / "provenance.json"', builder)
        self.assertIn('"evidence/SBOM.cdx.json"', installer)
        self.assertIn('"evidence/vulnerability-status.json"', installer)


if __name__ == "__main__":
    unittest.main()
