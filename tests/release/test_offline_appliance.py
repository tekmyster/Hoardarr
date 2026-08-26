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
                "declare -A preserved_unit_masks=()",
                shell_function("prepare_temporary_unit_mask"),
                shell_function("cleanup_temporary_masks"),
                shell_function("disable_unmasked_units"),
                'root="$1"',
                'if command -v cygpath >/dev/null 2>&1; then root="$(cygpath -u -- "$root")"; fi',
                'mkdir -p -- "$root"',
                "",
                "# Newly created exact iscsi.service masks are temporary.",
                'absent="$root/absent/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$absent")"',
                "temporary_masks=()",
                "preserved_unit_masks=()",
                'prepare_temporary_unit_mask "$absent" iscsi.service',
                '[[ -L "$absent" && "$(readlink -- "$absent")" == /dev/null ]]',
                '[[ "${#temporary_masks[@]}" -eq 1 && "${temporary_masks[0]}" == "$absent" ]]',
                "cleanup_temporary_masks",
                '[[ ! -e "$absent" && ! -L "$absent" ]]',
                'later="$root/later-failure/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$later")"',
                "later_status=0",
                "(",
                "  temporary_masks=()",
                "  preserved_unit_masks=()",
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
                "temporary_masks=()",
                "preserved_unit_masks=()",
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
                "  temporary_masks=()",
                "  preserved_unit_masks=()",
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
                "temporary_masks=()",
                "preserved_unit_masks=()",
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
                "temporary_masks=()",
                "preserved_unit_masks=()",
                'prepare_temporary_unit_mask "$lifecycle_safe" iscsi.service',
                'prepare_temporary_unit_mask "$lifecycle_new" iscsid.service',
                "cleanup_temporary_masks",
                "denied_units=(iscsi.service iscsid.service)",
                'target="$root/target"',
                "chroot() { printf '%s\\n' \"$*\" >>\"$disable_log\"; }",
                "disable_unmasked_units",
                '[[ -L "$lifecycle_safe" && "$(readlink -- "$lifecycle_safe")" == /dev/null ]]',
                '[[ "$(stat -c %i -- "$lifecycle_safe")" == "$lifecycle_inode" ]]',
                '[[ ! -e "$lifecycle_new" && ! -L "$lifecycle_new" ]]',
                '[[ "$(cat "$disable_log")" == "$target systemctl disable iscsid.service" ]]',
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [bash, "-c", script, "mask-regression", temporary],
                capture_output=True,
                check=False,
                env={**os.environ, "MSYS": "winsymlinks:sys"},
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
        self.assertIn("--no-download --no-install-recommends", installer)
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
        self.assertIn("if: github.event_name == 'workflow_dispatch'", workflow)
        self.assertIn("name: hoardarr-offline-install-inputs\n          path: dist", workflow)
        self.assertIn('$RUNNER_TEMP/ci-signing-key', workflow)
        self.assertIn('$RUNNER_TEMP/ubuntu-vulnerability-status.json', workflow)
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
        self.assertIn('[[ "${pipeline_status[1]}" -eq 0 ]] || capture_ok=false', user_data)
        self.assertIn('exit "$payload_status"', user_data)
        payload_tail = user_data.split('pipeline_status=("${PIPESTATUS[@]}")', 1)[1]
        self.assertNotIn("set -e", payload_tail.split('exit "$payload_status"', 1)[0])
        self.assertIn("/target/var/log/hoardarr-offline-payload.log", user_data)
        self.assertIn("[[ -c /dev/ttyS0 && -w /dev/ttyS0 ]]", user_data)
        self.assertIn("stty -F /dev/ttyS0 -opost || exit 126", user_data)
        self.assertIn("stty -F /dev/ttyS0 -a | grep -qw -- -opost || exit 127", user_data)
        self.assertNotIn("|| true", payload_tail.split('exit "$payload_status"', 1)[0])
        for required_operation in (
            'emit_both HOARDARR_OFFLINE_PAYLOAD_END || capture_ok=false',
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

        def run_capture(serial: bytes) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
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
            json.loads((nonzero_root / "capture.json").read_text())["payload_status"], 17
        )
        self.assertIn(b"decisive failure", (nonzero_root / "target.log").read_bytes())

        zero, _ = run_capture(complete(0))
        self.assertEqual(zero.returncode, 0)
        crlf_stream = complete(17).replace(b"\n", b"\r\n")
        crlf, crlf_root = run_capture(crlf_stream)
        self.assertEqual(crlf.returncode, 10)
        crlf_metadata = json.loads((crlf_root / "capture.json").read_text())
        self.assertEqual(crlf_metadata["serial_transform"], "onlcr_crlf")
        expected_target = complete(17).split(b"prefix\n", 1)[1].split(
            b"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE=", 1
        )[0]
        self.assertEqual((crlf_root / "target.log").read_bytes(), expected_target)
        self.assertEqual(
            crlf_metadata["target_log_sha256"], hashlib.sha256(expected_target).hexdigest()
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
            "payload_parser_status == 0 )); then\n            payload_failure_observed", harness
        )
        parser_guard = harness.split('if [[ "$diagnostic_mode" == true ]]; then', 1)[1]
        self.assertIn('payload_capture_parser="$script_root/', parser_guard)
        self.assertNotIn("parse-offline-payload-capture.py", harness.split(parser_guard, 1)[0])

    def test_repository_tree_verification_rejects_tampering(self) -> None:
        required = (
            "dists/noble/InRelease",
            "dists/noble/Release",
            "dists/noble/Release.gpg",
            "dists/noble/main/binary-amd64/Packages",
            "dists/noble/main/binary-amd64/Packages.gz",
            "evidence/SBOM.cdx.json",
            "evidence/compatibility-matrix.json",
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
            offline_repo._write_tree_manifest(root)
            with mock.patch.object(offline_repo.shutil, "which", return_value=None):
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
