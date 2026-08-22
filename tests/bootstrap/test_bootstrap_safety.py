"""Behavioral regression tests for privileged bootstrap safety boundaries."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import pathlib
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
BOOTSTRAP_FILE = ROOT / "scripts" / "bootstrap.py"


def load_bootstrap():
    spec = importlib.util.spec_from_file_location("hoardarr_bootstrap_safety", BOOTSTRAP_FILE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {BOOTSTRAP_FILE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BootstrapSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bootstrap = load_bootstrap()

    def test_runtime_apply_fails_before_preflight_or_mutation(self) -> None:
        args = self.bootstrap.build_parser().parse_args(
            [
                "apply",
                "--profile",
                "appliance-core",
                "--confirm-runtime-host",
                "--yes",
            ]
        )
        report = {"warnings": []}
        with mock.patch.object(self.bootstrap, "preflight") as preflight:
            with self.assertRaisesRegex(
                self.bootstrap.BootstrapError, "Runtime profile apply is intentionally unavailable"
            ):
                self.bootstrap.execute(args, report)
        preflight.assert_not_called()

    def test_mixed_all_profile_cannot_bypass_runtime_apply_block(self) -> None:
        args = self.bootstrap.build_parser().parse_args(
            ["apply", "--profile", "all", "--confirm-runtime-host", "--yes"]
        )
        with mock.patch.object(self.bootstrap, "preflight") as preflight:
            with self.assertRaises(self.bootstrap.BootstrapError):
                self.bootstrap.execute(args, {"warnings": []})
        preflight.assert_not_called()

    def test_apt_simulation_rejects_removals(self) -> None:
        completed = subprocess.CompletedProcess(
            ["apt-get"],
            0,
            stdout="Inst wanted (1.0)\nRemv existing [1.0]\n",
            stderr="",
        )
        with mock.patch.object(self.bootstrap, "run_command", return_value=completed):
            with self.assertRaisesRegex(self.bootstrap.BootstrapError, "would remove packages"):
                self.bootstrap.simulate_packages(["wanted"])

    def test_apt_simulation_rejects_downgrades(self) -> None:
        completed = subprocess.CompletedProcess(
            ["apt-get"],
            0,
            stdout="Inst wanted [2.0] (1.0)\nThe following packages will be DOWNGRADED\n",
            stderr="",
        )
        with mock.patch.object(self.bootstrap, "run_command", return_value=completed):
            with self.assertRaisesRegex(self.bootstrap.BootstrapError, "downgrade"):
                self.bootstrap.simulate_packages(["wanted"])

    def test_reconciliation_selects_only_missing_and_outdated_packages(self) -> None:
        order = {"1": 1, "2": 2, "3": 3}

        def compare(left: str, operator: str, right: str) -> bool:
            operations = {
                "lt": order[left] < order[right],
                "eq": order[left] == order[right],
                "gt": order[left] > order[right],
            }
            return operations[operator]

        result = self.bootstrap.reconcile_package_versions(
            ["ahead", "current", "missing", "old"],
            {"ahead": "3", "current": "2", "old": "1"},
            {"ahead": "2", "current": "2", "missing": "1", "old": "2"},
            comparator=compare,
        )
        self.assertEqual(result["transaction_packages"], ["missing", "old"])
        self.assertEqual(
            self.bootstrap.blocking_held_packages(result, ["current", "old"]), ["old"]
        )

    def test_package_unit_discovery_includes_sysv_generated_service(self) -> None:
        completed = subprocess.CompletedProcess(
            ["dpkg-query"],
            0,
            stdout=(
                "/etc/init.d/openipmi\n"
                "/lib/systemd/system/native.service\n"
                "/usr/lib/systemd/system/alias.service\n"
                "/etc/init.d/README\n"
            ),
            stderr="",
        )
        with mock.patch.object(self.bootstrap, "run_command", return_value=completed):
            self.assertEqual(
                self.bootstrap.package_systemd_units(["example"]),
                ["alias.service", "native.service", "openipmi.service"],
            )

    def test_only_real_enablement_states_count_as_autostart(self) -> None:
        before = {"example.service": {"active": "inactive", "enabled": "not-found"}}
        for state in ("alias", "disabled", "generated", "indirect", "linked", "static"):
            with self.subTest(state=state):
                after = {"example.service": {"active": "inactive", "enabled": state}}
                self.assertEqual(self.bootstrap.runtime_safety_violations(before, after), [])
        enabled = {"example.service": {"active": "inactive", "enabled": "enabled"}}
        self.assertEqual(
            self.bootstrap.runtime_safety_violations(before, enabled),
            [{"unit": "example.service", "state": "newly enabled"}],
        )

    def test_native_multiarch_suffix_is_not_an_unexpected_apt_delta(self) -> None:
        delta = {
            "added": [{"package": "libexample:amd64", "version": "1"}],
            "changed": [{"package": "plain", "before": "1", "after": "2"}],
            "removed": [],
        }
        self.assertEqual(
            self.bootstrap.unexpected_dpkg_names(delta, ["libexample", "plain"]), []
        )
        self.assertEqual(
            self.bootstrap.unexpected_dpkg_names(delta, ["plain"]), ["libexample:amd64"]
        )

    def test_changed_policy_guard_is_never_overwritten_during_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            policy = root / "policy-rc.d"
            state = root / "state.json"
            backup = root / "original"
            changed = b"#!/bin/sh\n# administrator replacement\nexit 0\n"
            patches = (
                mock.patch.object(self.bootstrap, "STATE_ROOT", root),
                mock.patch.object(self.bootstrap, "POLICY_PATH", policy),
                mock.patch.object(self.bootstrap, "POLICY_STATE", state),
                mock.patch.object(self.bootstrap, "POLICY_BACKUP", backup),
            )
            with contextlib.ExitStack() as stack:
                for patcher in patches:
                    stack.enter_context(patcher)
                with self.assertRaisesRegex(self.bootstrap.BootstrapError, "changed after"):
                    with self.bootstrap.inhibit_service_starts():
                        policy.write_bytes(changed)
                self.assertEqual(policy.read_bytes(), changed)
                self.assertTrue(state.is_file(), "recovery evidence must be retained")

    def test_normal_policy_guard_restores_an_absent_original(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            policy = root / "policy-rc.d"
            state = root / "state.json"
            backup = root / "original"
            with (
                mock.patch.object(self.bootstrap, "STATE_ROOT", root),
                mock.patch.object(self.bootstrap, "POLICY_PATH", policy),
                mock.patch.object(self.bootstrap, "POLICY_STATE", state),
                mock.patch.object(self.bootstrap, "POLICY_BACKUP", backup),
            ):
                with self.bootstrap.inhibit_service_starts():
                    self.assertEqual(policy.read_bytes(), self.bootstrap.POLICY_GUARD)
                self.assertFalse(policy.exists())
                self.assertFalse(state.exists())

    def test_safe_tar_extraction_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive = root / "hostile.tar"
            destination = root / "extract"
            destination.mkdir()
            content = b"escape"
            with tarfile.open(archive, "w") as stream:
                member = tarfile.TarInfo("../escaped")
                member.size = len(content)
                stream.addfile(member, io.BytesIO(content))
            with self.assertRaises((self.bootstrap.BootstrapError, tarfile.FilterError)):
                self.bootstrap.safe_extract(archive, destination)
            self.assertFalse((root / "escaped").exists())

    def test_conflicting_exact_vendor_packages_fail_before_install(self) -> None:
        plan = [
            {
                "id": "one",
                "available": True,
                "deb_package": "storcli",
                "deb_version": "1",
                "conflict_group": "storcli-provider",
            },
            {
                "id": "two",
                "available": True,
                "deb_package": "storcli",
                "deb_version": "2",
                "conflict_group": "storcli-provider",
            },
        ]
        with self.assertRaisesRegex(self.bootstrap.BootstrapError, "(?i)conflict"):
            self.bootstrap.validate_vendor_plan_conflicts(plan)

    def test_report_write_failure_changes_success_to_failure(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        with (
            mock.patch.object(self.bootstrap, "execute", return_value=None),
            mock.patch.object(self.bootstrap, "write_report", side_effect=OSError("disk full")),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            result = self.bootstrap.main(["check"])
        self.assertEqual(result, 1)
        self.assertIn("bootstrap check: failed", output.getvalue())
        self.assertIn("could not write report", errors.getvalue())

    def test_privileged_subprocess_environment_drops_injection_variables(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "PATH": "/tmp/hostile",
                "HOME": "/tmp/hostile-home",
                "LD_PRELOAD": "/tmp/hostile.so",
                "PYTHONPATH": "/tmp/hostile-python",
                "HTTPS_PROXY": "http://proxy.example:8080",
            },
            clear=True,
        ):
            environment = self.bootstrap.base_environment()
        self.assertEqual(environment["PATH"], self.bootstrap.SYSTEM_PATH)
        self.assertNotEqual(environment.get("HOME"), "/tmp/hostile-home")
        self.assertNotIn("LD_PRELOAD", environment)
        self.assertNotIn("PYTHONPATH", environment)
        self.assertEqual(environment["HTTPS_PROXY"], "http://proxy.example:8080")

    @unittest.skipUnless(os.name == "posix", "POSIX mode preservation")
    def test_toolchain_setup_does_not_broaden_existing_parent_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = pathlib.Path(temporary) / "hoardarr"
            parent.mkdir(mode=0o700)
            toolchain = parent / "toolchains"
            with mock.patch.object(self.bootstrap, "TOOLCHAIN_ROOT", toolchain), mock.patch.object(
                self.bootstrap, "TOOLCHAIN_BIN", toolchain / "bin"
            ):
                self.bootstrap.ensure_toolchain_roots()
            self.assertEqual(os.stat(parent).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(toolchain).st_mode & 0o777, 0o755)


if __name__ == "__main__":
    unittest.main()
