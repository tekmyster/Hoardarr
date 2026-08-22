from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "bootstrap.py"
SPEC = importlib.util.spec_from_file_location("hoardarr_bootstrap_under_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
bootstrap = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bootstrap
SPEC.loader.exec_module(bootstrap)


def completed(stdout: str = "", *, stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(["test"], returncode, stdout, stderr)


@contextlib.contextmanager
def temporary_policy_paths(root: Path):
    state_root = root / "state"
    with mock.patch.multiple(
        bootstrap,
        STATE_ROOT=state_root,
        POLICY_PATH=root / "usr" / "sbin" / "policy-rc.d",
        POLICY_STATE=state_root / "policy-rc.d-state.json",
        POLICY_BACKUP=state_root / "policy-rc.d-original",
    ):
        (root / "usr" / "sbin").mkdir(parents=True)
        with mock.patch.object(bootstrap.os, "chown", create=True), mock.patch.object(
            bootstrap.os, "lchown", create=True
        ):
            yield


class PackageReconciliationTests(unittest.TestCase):
    @staticmethod
    def comparator(left: str, operator: str, right: str) -> bool:
        values = {"1": 1, "2": 2, "3": 3}
        if operator == "lt":
            return values[left] < values[right]
        if operator == "gt":
            return values[left] > values[right]
        return values[left] == values[right]

    def test_missing_outdated_current_and_ahead_are_distinct(self) -> None:
        result = bootstrap.reconcile_package_versions(
            ["missing", "old", "current", "ahead"],
            {"old": "1", "current": "2", "ahead": "3"},
            {"missing": "2", "old": "2", "current": "2", "ahead": "2"},
            comparator=self.comparator,
        )
        self.assertEqual(result["missing"], ["missing"])
        self.assertEqual([item["package"] for item in result["outdated"]], ["old"])
        self.assertEqual(result["current"], ["current"])
        self.assertEqual([item["package"] for item in result["ahead_of_candidate"]], ["ahead"])
        self.assertEqual(result["transaction_packages"], ["missing", "old"])

    def test_only_held_packages_needing_a_transaction_block(self) -> None:
        reconciliation = {"transaction_packages": ["old"]}
        self.assertEqual(
            bootstrap.blocking_held_packages(reconciliation, ["current", "old"]),
            ["old"],
        )
        self.assertEqual(
            bootstrap.blocking_held_packages(reconciliation, ["current"]),
            [],
        )

    def test_simulation_rejects_removal_and_downgrade(self) -> None:
        with mock.patch.object(
            bootstrap, "run_command", return_value=completed("Remv existing [1]\n")
        ):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "remove packages"):
                bootstrap.simulate_packages(["wanted"])
        with mock.patch.object(
            bootstrap,
            "run_command",
            return_value=completed("The following packages will be DOWNGRADED:\n wanted\n"),
        ):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "downgrade"):
                bootstrap.simulate_packages(["wanted"])

    def test_simulation_records_the_inst_union(self) -> None:
        output = "Inst wanted (2 Ubuntu:24.04 [amd64])\nInst dependency (1 Ubuntu:24.04 [amd64])\n"
        with mock.patch.object(bootstrap, "run_command", return_value=completed(output)):
            result = bootstrap.simulate_packages(["wanted"])
        self.assertEqual(result["install_packages"], ["dependency", "wanted"])

    def test_actual_delta_allows_native_arch_suffix_but_not_unknown_package(self) -> None:
        delta = {
            "added": [
                {"package": "libpciaccess0:amd64", "version": "1"},
                {"package": "surprise:amd64", "version": "1"},
            ],
            "changed": [],
        }
        with mock.patch.object(bootstrap, "normalized_arch", return_value="amd64"):
            self.assertEqual(
                bootstrap.unexpected_dpkg_names(delta, ["libpciaccess0"]),
                ["surprise:amd64"],
            )


class RuntimeSafetyTests(unittest.TestCase):
    def test_differences_are_symmetric_for_active_and_enabled(self) -> None:
        expected = {"x.service": {"active": "active", "enabled": "enabled"}}
        observed = {"x.service": {"active": "inactive", "enabled": "disabled"}}
        differences = bootstrap.runtime_state_differences(expected, observed)
        self.assertEqual(differences[0]["aspects"], ["active", "enabled"])

    def test_alias_linked_and_generated_are_not_autostart(self) -> None:
        for state in ("alias", "linked", "linked-runtime", "generated"):
            with self.subTest(state=state):
                expected = {"x.service": {"active": "not-found", "enabled": "not-found"}}
                observed = {"x.service": {"active": "inactive", "enabled": state}}
                self.assertEqual(
                    bootstrap.runtime_state_differences(expected, observed),
                    [],
                )

    def test_sysv_rc_enablement_is_separate_and_symmetric(self) -> None:
        expected = {
            "openipmi.service": {
                "active": "inactive",
                "enabled": "generated",
                "sysv_enabled": "disabled",
            }
        }
        observed = {
            "openipmi.service": {
                "active": "inactive",
                "enabled": "generated",
                "sysv_enabled": "enabled",
            }
        }
        self.assertEqual(
            bootstrap.runtime_state_differences(expected, observed)[0]["aspects"],
            ["sysv-enabled"],
        )

    def test_restore_disables_new_sysv_enablement_without_starting_anything(self) -> None:
        before = {
            "openipmi.service": {
                "active": "inactive",
                "enabled": "generated",
                "sysv_enabled": "disabled",
            }
        }
        after = {
            "openipmi.service": {
                "active": "inactive",
                "enabled": "generated",
                "sysv_enabled": "enabled",
            }
        }
        with mock.patch.object(bootstrap, "unit_state", return_value=after), mock.patch.object(
            bootstrap, "run_command", return_value=completed()
        ) as command:
            changes = bootstrap.restore_runtime_unit_safety(before)
        self.assertEqual(changes[0]["action"], "disabled unexpected enablement")
        argv = [call.args[0] for call in command.call_args_list]
        self.assertIn(["systemctl", "disable", "openipmi.service"], argv)
        self.assertFalse(any("start" in item for call in argv for item in call))

    def test_preexisting_drift_aborts_without_explicit_refresh(self) -> None:
        baseline = {"units": {"x.service": {"active": "active", "enabled": "enabled"}}}
        current = {"x.service": {"active": "inactive", "enabled": "disabled"}}
        with self.assertRaisesRegex(bootstrap.BootstrapError, "differs from its saved baseline"):
            bootstrap.require_runtime_baseline_match(
                baseline, current, refresh_requested=False
            )
        self.assertTrue(
            bootstrap.require_runtime_baseline_match(
                baseline, current, refresh_requested=True
            )
        )

    def test_package_unit_discovery_includes_native_and_sysv_units(self) -> None:
        listing = "/etc/init.d/openipmi\n/lib/systemd/system/native.service\n"
        with mock.patch.object(bootstrap, "run_command", return_value=completed(listing)):
            self.assertEqual(
                bootstrap.package_systemd_units(["openipmi"]),
                ["native.service", "openipmi.service"],
            )


class PolicyRecoveryTests(unittest.TestCase):
    def _exercise_crash_recovery(self, original: str) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with temporary_policy_paths(root):
                policy = bootstrap.POLICY_PATH
                target = root / "admin-policy"
                if original == "regular":
                    policy.write_bytes(b"#!/bin/sh\nexit 0\n")
                    os.chmod(policy, 0o640)
                elif original == "symlink":
                    target.write_bytes(b"#!/bin/sh\nexit 0\n")
                    policy.symlink_to(target)
                context = bootstrap.inhibit_service_starts()
                context.__enter__()
                self.assertEqual(policy.read_bytes(), bootstrap.POLICY_GUARD)
                self.assertTrue(bootstrap._restore_policy_from_state())
                if original == "absent":
                    self.assertFalse(policy.exists())
                elif original == "regular":
                    self.assertEqual(policy.read_bytes(), b"#!/bin/sh\nexit 0\n")
                    if os.name == "posix":
                        self.assertEqual(stat.S_IMODE(os.stat(policy).st_mode), 0o640)
                else:
                    self.assertTrue(policy.is_symlink())
                    self.assertEqual(policy.resolve(), target.resolve())
                self.assertFalse(bootstrap.POLICY_STATE.exists())
                context.__exit__(None, None, None)

    def test_absent_regular_and_symlink_crash_recovery(self) -> None:
        for original in ("absent", "regular", "symlink"):
            with self.subTest(original=original):
                self._exercise_crash_recovery(original)

    def test_recovery_refuses_to_clobber_an_admin_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with temporary_policy_paths(root):
                policy = bootstrap.POLICY_PATH
                policy.write_bytes(b"original")
                context = bootstrap.inhibit_service_starts()
                context.__enter__()
                bootstrap.atomic_write(policy, b"administrator changed this", 0o755)
                with self.assertRaisesRegex(bootstrap.BootstrapError, "changed after Hoardarr"):
                    bootstrap._restore_policy_from_state()
                self.assertEqual(policy.read_bytes(), b"administrator changed this")
                self.assertTrue(bootstrap.POLICY_STATE.exists())
                self.assertTrue(bootstrap.POLICY_BACKUP.exists())
                bootstrap.atomic_write(policy, bootstrap.POLICY_GUARD, 0o755)
                bootstrap._restore_policy_from_state()
                context.__exit__(None, None, None)


class DownloadAndArchiveTests(unittest.TestCase):
    class Response(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def geturl(self) -> str:
            return "https://vendor.example/tool.deb"

    def test_verified_download_is_cached_and_bad_hash_is_not_published(self) -> None:
        payload = b"verified artifact"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "tool.deb"
            with mock.patch.object(
                bootstrap.urllib.request,
                "urlopen",
                return_value=self.Response(payload),
            ):
                self.assertTrue(
                    bootstrap.download_pinned(
                        "https://vendor.example/tool.deb", destination, sha256=digest
                    )
                )
            with mock.patch.object(bootstrap.urllib.request, "urlopen") as request:
                self.assertFalse(
                    bootstrap.download_pinned(
                        "https://vendor.example/tool.deb", destination, sha256=digest
                    )
                )
                request.assert_not_called()
            destination.unlink()
            with mock.patch.object(
                bootstrap.urllib.request,
                "urlopen",
                return_value=self.Response(payload),
            ):
                with self.assertRaisesRegex(bootstrap.BootstrapError, "Checksum mismatch"):
                    bootstrap.download_pinned(
                        "https://vendor.example/tool.deb", destination, sha256="0" * 64
                    )
            self.assertFalse(destination.exists())

    def test_vendor_archives_extract_only_the_exact_safe_member(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tar_path = root / "vendor.tar"
            with tarfile.open(tar_path, "w") as archive:
                for name, value in (("nested/tool.deb", b"deb"), ("../escape", b"bad")):
                    info = tarfile.TarInfo(name)
                    info.size = len(value)
                    archive.addfile(info, io.BytesIO(value))
            with bootstrap._vendor_deb(tar_path, "tar-deb", "nested/tool.deb") as deb:
                self.assertEqual(deb.read_bytes(), b"deb")
            zip_path = root / "vendor.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("nested/tool.deb", b"zip-deb")
                archive.writestr("../escape", b"bad")
            with bootstrap._vendor_deb(zip_path, "zip-deb", "nested/tool.deb") as deb:
                self.assertEqual(deb.read_bytes(), b"zip-deb")
            with self.assertRaisesRegex(bootstrap.BootstrapError, "Unsafe deb_member"):
                with bootstrap._vendor_deb(zip_path, "zip-deb", "../tool.deb"):
                    pass


class VendorAndToolchainTests(unittest.TestCase):
    def test_conflicting_storcli_variants_fail_before_install(self) -> None:
        plan = [
            {
                "id": "broadcom-storcli",
                "available": True,
                "deb_package": "storcli",
                "deb_version": "2",
                "conflict_group": "storcli-installation",
            },
            {
                "id": "hpe-storcli",
                "available": True,
                "deb_package": "storcli",
                "deb_version": "1",
                "conflict_group": "storcli-installation",
            },
        ]
        with self.assertRaisesRegex(bootstrap.BootstrapError, "broadcom-storcli"):
            bootstrap.validate_vendor_plan_conflicts(plan)

    def test_vendor_deb_simulation_rejects_downgrade(self) -> None:
        with mock.patch.object(bootstrap, "_installed_deb_version", return_value="2"), mock.patch.object(
            bootstrap, "run_command", return_value=completed(returncode=0)
        ):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "downgrade"):
                bootstrap.simulate_vendor_deb(
                    Path("tool.deb"),
                    {"package": "vendor-tool", "version": "1", "architecture": "all"},
                )

    def test_vendor_receipt_is_exact_and_idempotent(self) -> None:
        tool = {
            "id": "vendor-tool",
            "artifact": {"sha256": "a" * 64, "url": "https://vendor.example/tool.deb"},
        }
        receipt = {
            "id": "vendor-tool",
            "artifact_sha256": "a" * 64,
            "url": "https://vendor.example/tool.deb",
            "package": "vendor-tool",
            "deb_version": "1",
        }
        with mock.patch.object(bootstrap, "_installed_deb_version", return_value="1"):
            self.assertTrue(bootstrap._vendor_receipt_matches(tool, receipt))
        with mock.patch.object(bootstrap, "_installed_deb_version", return_value="2"):
            self.assertFalse(bootstrap._vendor_receipt_matches(tool, receipt))

    def test_managed_symlink_replacement_is_atomic_and_refuses_unmanaged_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool_root = root / "toolchains"
            bin_root = tool_root / "bin"
            bin_root.mkdir(parents=True)
            source = tool_root / "new" / "tool"
            source.parent.mkdir()
            source.write_bytes(b"new")
            old = tool_root / "old" / "tool"
            old.parent.mkdir()
            old.write_bytes(b"old")
            destination = bin_root / "tool"
            destination.symlink_to(old)
            changes: list[str] = []
            with mock.patch.multiple(
                bootstrap, TOOLCHAIN_ROOT=tool_root, TOOLCHAIN_BIN=bin_root
            ):
                bootstrap._managed_symlink(source, destination, changes)
                self.assertEqual(destination.resolve(), source.resolve())
                external = root / "external"
                external.write_bytes(b"external")
                destination.unlink()
                destination.symlink_to(external)
                with self.assertRaisesRegex(bootstrap.BootstrapError, "unmanaged symlink"):
                    bootstrap._managed_symlink(source, destination, changes)


class CliAndManifestSafetyTests(unittest.TestCase):
    def test_hardware_fixture_is_rejected_before_apply_preflight(self) -> None:
        args = bootstrap.build_parser().parse_args(
            ["apply", "--yes", "--hardware-fixture", "fixture.json"]
        )
        report = {"warnings": []}
        with mock.patch.object(bootstrap, "preflight") as preflight:
            with self.assertRaisesRegex(bootstrap.BootstrapError, "forbidden during apply"):
                bootstrap.execute(args, report)
            preflight.assert_not_called()

    def test_runtime_apply_is_blocked_without_an_override(self) -> None:
        args = bootstrap.build_parser().parse_args(
            [
                "apply",
                "--yes",
                "--profile",
                "appliance-core",
                "--confirm-runtime-host",
            ]
        )
        report = {"warnings": []}
        with mock.patch.object(bootstrap, "preflight") as preflight:
            with self.assertRaisesRegex(bootstrap.BootstrapError, "intentionally unavailable"):
                bootstrap.execute(args, report)
            preflight.assert_not_called()

    def test_report_write_failure_changes_final_status_before_json(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(bootstrap, "execute"), mock.patch.object(
            bootstrap, "write_report", side_effect=OSError("disk full")
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = bootstrap.main(["check", "--json"])
        self.assertEqual(code, 1)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["report_write_error"], "disk full")

    def test_boot_image_packages_are_architecture_conditioned(self) -> None:
        self.assertIn("grub-efi-amd64-bin", bootstrap.BUILD_BOOT_PACKAGES["amd64"])
        self.assertNotIn("grub-efi-amd64-bin", bootstrap.BUILD_BOOT_PACKAGES["arm64"])
        self.assertIn("grub-efi-arm64-bin", bootstrap.BUILD_BOOT_PACKAGES["arm64"])

    def test_runtime_manifests_avoid_kernel_rewrites_and_fence_meta_package(self) -> None:
        appliance = bootstrap.load_manifest(
            ROOT / "packaging" / "packages" / "appliance-core.txt"
        )
        high_availability = bootstrap.load_manifest(
            ROOT / "packaging" / "packages" / "advanced-ha.txt"
        )
        self.assertNotIn("linux-generic", appliance)
        self.assertNotIn("linux-firmware", appliance)
        self.assertNotIn("fence-agents", high_availability)
        self.assertIn("fence-agents-base", high_availability)


if __name__ == "__main__":
    unittest.main()
