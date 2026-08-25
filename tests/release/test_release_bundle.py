from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = ROOT / "scripts" / "build-release-bundle.py"
INSTALLER_PATH = ROOT / "scripts" / "install-release-bundle.sh"


def load_builder():
    spec = importlib.util.spec_from_file_location("hoardarr_release_builder", BUILDER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load release builder")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = load_builder()


class VersionConsistencyTests(unittest.TestCase):
    def test_release_package_and_ui_versions_match(self):
        project_version = str(
            tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))[
                "project"
            ]["version"]
        )
        frontend_version = json.loads(
            (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
        )["version"]
        frontend_lock = json.loads(
            (ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8")
        )
        backend_lock = tomllib.loads(
            (ROOT / "backend" / "uv.lock").read_text(encoding="utf-8")
        )
        locked_backend = next(
            package for package in backend_lock["package"] if package["name"] == "hoardarr"
        )
        namespace: dict[str, object] = {}
        exec(
            (ROOT / "backend" / "src" / "hoardarr" / "__init__.py").read_text(
                encoding="utf-8"
            ),
            namespace,
        )

        self.assertRegex(project_version, r"^0\.\d+\.\d+$")
        self.assertEqual(namespace["__version__"], project_version)
        self.assertEqual(frontend_version, project_version)
        self.assertEqual(frontend_lock["version"], project_version)
        self.assertEqual(frontend_lock["packages"][""]["version"], project_version)
        self.assertEqual(locked_backend["version"], project_version)
        self.assertIn(
            "packageMetadata.version",
            (ROOT / "frontend" / "src" / "components" / "AppShell.tsx").read_text(
                encoding="utf-8"
            ),
        )

    def test_release_installer_enforces_mandatory_mergerfs(self):
        installer = INSTALLER_PATH.read_text(encoding="utf-8")

        self.assertIn('grep -Fxq "mergerfs"', installer)
        self.assertIn("ensure_mergerfs", installer)
        self.assertIn("install --yes --no-install-recommends mergerfs", installer)
        self.assertIn("mandatory command failed its version check: mergerfs", installer)

    def test_release_installer_enforces_mergerfs_attribute_runtime(self):
        installer = INSTALLER_PATH.read_text(encoding="utf-8")

        self.assertIn('grep -Fxq "attr"', installer)
        self.assertIn("ensure_filesystem_attribute_tools", installer)
        self.assertIn("install --yes --no-install-recommends attr", installer)
        self.assertIn("getfattr setfattr", installer)

    def test_release_installer_enforces_media_account_runtime(self):
        installer = INSTALLER_PATH.read_text(encoding="utf-8")

        self.assertIn('grep -Fxq "samba"', installer)
        self.assertIn("ensure_account_tools", installer)
        self.assertIn("install --yes --no-install-recommends samba", installer)
        self.assertIn("hoardarr-account-executor.service", installer)

    def test_release_installer_enforces_neighbor_discovery_runtime(self):
        installer = INSTALLER_PATH.read_text(encoding="utf-8")
        drop_in = (
            ROOT / "packaging" / "systemd" / "hoardarr-lldpd.conf"
        ).read_text(encoding="utf-8")

        self.assertIn('grep -Fxq "lldpd"', installer)
        self.assertIn("ensure_neighbor_discovery", installer)
        self.assertIn("install --yes --no-install-recommends lldpd", installer)
        self.assertIn("systemctl enable --now lldpd.service", installer)
        self.assertIn('Environment="DAEMON_ARGS=-c"', drop_in)

    def test_release_installer_reconciles_managed_mounts_before_runtime_start(self):
        installer = INSTALLER_PATH.read_text(encoding="utf-8")

        prepare = '"${QUARANTINE_CLI_LINK}" prepare --yes'
        reconcile = '"${QUARANTINE_CLI_LINK}" reconcile-managed --yes --activate'
        self.assertIn(prepare, installer)
        self.assertIn(reconcile, installer)
        self.assertLess(installer.index(prepare), installer.index(reconcile))
        self.assertLess(
            installer.index(reconcile),
            installer.rindex("systemctl start hoardarr-account-executor.service"),
        )
        self.assertIn(
            "drive quarantine could not be prepared; the previous runtime was restored",
            installer,
        )
        self.assertIn(
            "managed storage could not be reconciled; the previous runtime was restored",
            installer,
        )

    def test_media_account_executor_allows_samba_interface_discovery(self):
        unit = (
            ROOT / "packaging" / "systemd" / "hoardarr-account-executor.service"
        ).read_text(encoding="utf-8")

        self.assertIn("RestrictAddressFamilies=AF_UNIX AF_NETLINK", unit)
        self.assertNotIn("AF_INET", unit)

    def test_privileged_executors_preserve_their_shared_runtime_directory(self):
        for name in ("account-executor", "storage-executor", "storage-status"):
            with self.subTest(service=name):
                unit = (
                    ROOT / "packaging" / "systemd" / f"hoardarr-{name}.service"
                ).read_text(encoding="utf-8")
                self.assertIn("RuntimeDirectory=hoardarr", unit)
                self.assertIn("RuntimeDirectoryPreserve=yes", unit)

    def test_central_fleet_service_is_not_packaged_as_an_appliance_unit(self):
        appliance_unit = (
            ROOT / "packaging" / "systemd" / "hoardarr-fleet-ingestion.service"
        )
        central_unit = (
            ROOT
            / "packaging"
            / "hoardarr-com"
            / "systemd"
            / "hoardarr-fleet-ingestion.service"
        )

        self.assertFalse(appliance_unit.exists())
        self.assertTrue(central_unit.is_file())
        text = central_unit.read_text(encoding="utf-8")
        self.assertIn("User=hoardarr-fleet", text)
        self.assertIn(
            "ExecStartPre=/usr/lib/hoardarr-fleet/venv/bin/hoardarr-fleet-ingestion migrate",
            text,
        )
        self.assertIn(
            "ExecStart=/usr/lib/hoardarr-fleet/venv/bin/hoardarr-fleet-ingestion serve",
            text,
        )
        installer = (
            ROOT / "packaging" / "hoardarr-com" / "install-fleet-ingestion.sh"
        ).read_text(encoding="utf-8")
        example = (
            ROOT / "packaging" / "hoardarr-com" / "fleet-ingestion.env.example"
        ).read_text(encoding="utf-8")
        self.assertIn("postgresql+psycopg://", example)
        self.assertIn("${wheel}[fleet-central]", installer)
        self.assertIn("replace every change-me placeholder", installer)
        self.assertIn("systemctl is-active", installer)
        self.assertIn('release_id="$(sha256sum', installer)
        self.assertIn("/usr/lib/hoardarr-fleet/releases", installer)
        self.assertIn(".ready", installer)
        self.assertIn(".venv.rollback", installer)


class ManifestTests(unittest.TestCase):
    def test_manifest_is_deterministic_and_detects_tampering_and_extra_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "nested").mkdir()
            (root / "z.txt").write_text("z\n", encoding="utf-8")
            (root / "nested" / "a.txt").write_text("a\n", encoding="utf-8")

            first = builder.write_manifest(root).read_bytes()
            builder.verify_manifest(root)
            second = builder.write_manifest(root).read_bytes()
            self.assertEqual(first, second)
            self.assertEqual(
                [line.split(b"  ", 1)[1] for line in first.splitlines()],
                [b"nested/a.txt", b"z.txt"],
            )

            (root / "z.txt").write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(builder.BuildError, "SHA-256 mismatch"):
                builder.verify_manifest(root)

            (root / "z.txt").write_text("z\n", encoding="utf-8")
            (root / "extra.txt").write_text("extra\n", encoding="utf-8")
            with self.assertRaisesRegex(builder.BuildError, "file set mismatch"):
                builder.verify_manifest(root)

    def test_manifest_path_validation_rejects_escaping_and_noncanonical_paths(self):
        rejected = ("/absolute", "../escape", "a/../b", "a\\b", "./a", "a//b", "")
        for value in rejected:
            with self.subTest(value=value), self.assertRaises(builder.BuildError):
                builder.safe_relative_path(value)
        self.assertEqual(str(builder.safe_relative_path("a/b-c_1.json")), "a/b-c_1.json")


class RequirementsTests(unittest.TestCase):
    def test_exact_hashed_requirements_are_accepted(self):
        digest = "a" * 64
        text = f"example==1.2.3 \\\n+    --hash=sha256:{digest}\n"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.lock"
            path.write_text(text, encoding="utf-8")
            builder.validate_hashed_requirements(path)

    def test_unhashed_or_remote_requirements_are_rejected(self):
        values = (
            "example==1.2.3\n",
            f"example>=1 --hash=sha256:{'a' * 64}\n",
            f"https://example.invalid/archive.whl --hash=sha256:{'a' * 64}\n",
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.lock"
            for value in values:
                with self.subTest(value=value):
                    path.write_text(value, encoding="utf-8")
                    with self.assertRaises(builder.BuildError):
                        builder.validate_hashed_requirements(path)


class BuildPlanTests(unittest.TestCase):
    def test_plan_is_read_only_and_versioned_from_source_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "not-created"
            plan = builder.create_plan(ROOT, output)
            self.assertRegex(plan.version, r"^[0-9]+\.[0-9]+\.[0-9]+")
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(plan.release_id, f"{plan.version}-{commit[:12]}")
            self.assertIn(plan.release_id, plan.bundle_name)
            self.assertIn("requirements/runtime.lock", plan.copied_paths)
            self.assertIn("scripts/bootstrap.py", plan.copied_paths)
            self.assertIn("scripts/export-nas-source-evidence.py", plan.copied_paths)
            self.assertIn("packages/", plan.copied_paths)
            self.assertIn("frontend/", plan.copied_paths)
            self.assertFalse(output.exists())

    def test_installed_detector_and_hardware_manifests_share_release_root(self):
        detector = (ROOT / "scripts" / "detect-hardware.py").read_text(encoding="utf-8")
        installer = INSTALLER_PATH.read_text(encoding="utf-8")

        self.assertIn('HARDWARE_ROOT = REPO_ROOT / "packaging" / "hardware"', detector)
        self.assertIn('BUNDLE_HARDWARE_ROOT = REPO_ROOT / "hardware"', detector)
        self.assertIn('"${stage}/packaging/hardware/${relative}"', installer)
        self.assertIn('"${stage}/packaging/packages/${relative}"', installer)
        self.assertIn('"${stage}/scripts/bootstrap.py"', installer)
        self.assertIn('atomic_symlink "current/packaging" "${LIB_ROOT}/packaging"', installer)
        self.assertIn('install_runtime_wrapper "${CLI_LINK}" cli', installer)
        self.assertIn("python -m hoardarr.runtime", installer)
        self.assertIn('"${stage}/frontend/${relative}"', installer)

    def test_detector_runs_from_unpacked_release_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary)
            (release / "scripts").mkdir()
            shutil.copy2(ROOT / "scripts" / "detect-hardware.py", release / "scripts")
            shutil.copytree(ROOT / "packaging" / "hardware", release / "hardware")

            result = subprocess.run(
                [
                    sys.executable,
                    str(release / "scripts" / "detect-hardware.py"),
                    "--format",
                    "json",
                    "--fixture",
                    str(ROOT / "tests" / "fixtures" / "hardware" / "hyperv-usb-cisco-ssd.json"),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["source"]["kind"], "fixture")
            self.assertTrue(any(disk["kernel_name"] == "sdb" for disk in payload["disks"]))

    def test_installer_serializes_apply_and_rejects_root_service_identity(self):
        installer = INSTALLER_PATH.read_text(encoding="utf-8")

        self.assertIn('readonly RUNTIME_ROOT="/run/hoardarr"', installer)
        self.assertIn('install -d -o root -g root -m 0755 "${RUNTIME_ROOT}"', installer)
        self.assertIn("stat -c '%u:%g:%a'", installer)
        self.assertIn('( ! -f "${INSTALL_LOCK_PATH}" || -L "${INSTALL_LOCK_PATH}" )', installer)
        self.assertIn('flock --exclusive --nonblock "${INSTALL_LOCK_FD}"', installer)
        self.assertLess(
            installer.index(
                "acquire_install_lock\n    ensure_mergerfs\n    ensure_filesystem_attribute_tools\n    ensure_account_tools\n    ensure_neighbor_discovery\n    ensure_service_account"
            ),
            installer.index('stage_release "${expected_manifest}"'),
        )
        self.assertIn('((uid > 0)) || die "existing hoardarr account must not use UID 0"', installer)
        self.assertIn('((gid > 0)) || die "existing hoardarr account must not use GID 0"', installer)
        self.assertIn('--preserve-existing-login-account', installer)
        self.assertIn('--defer-service-start', installer)
        self.assertIn(
            '--defer-service-start is only allowed for a first appliance installation',
            installer,
        )
        self.assertIn(
            'Database migration and runtime readiness will be enforced by systemd on boot.',
            installer,
        )
        self.assertIn(
            'Validating an offline appliance target; runtime PID 1 checks are deferred to first boot.',
            installer,
        )
        self.assertIn(
            '[[ "${PRESERVE_EXISTING_LOGIN_ACCOUNT}" == "true" ]]', installer
        )
        self.assertIn(
            "Preserving existing hoardarr administrator login for this legacy development host.",
            installer,
        )
        self.assertIn('usermod --lock --shell /usr/sbin/nologin hoardarr', installer)

    def test_plan_cli_does_not_create_output_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "planned-output"
            result = subprocess.run(
                [sys.executable, str(BUILDER_PATH), "plan", "--output-dir", str(output)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            value = json.loads(result.stdout)
            self.assertRegex(value["version"], r"^[0-9]+\.[0-9]+\.[0-9]+")
            self.assertFalse(output.exists())


def is_supported_installer_test_host() -> bool:
    if shutil.which("bash") is None or sys.platform != "linux" or platform.machine() != "x86_64":
        return False
    try:
        values = {}
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip().strip('"')
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
        pid_one = subprocess.run(
            ["ps", "-p", "1", "-o", "comm="], check=True, capture_output=True, text=True
        ).stdout.strip()
        return (
            values.get("ID") == "ubuntu"
            and values.get("VERSION_ID") == "24.04"
            and (python_version == "3.12" and pid_one == "systemd")
        )
    except (OSError, subprocess.CalledProcessError):
        return False


@unittest.skipUnless(
    is_supported_installer_test_host(), "requires Ubuntu 24.04/systemd/Python 3.12"
)
class InstallerPlanTests(unittest.TestCase):
    def test_installer_plan_verifies_bundle_without_host_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            for directory in (
                "scripts",
                "requirements",
                "config",
                "systemd",
                "docs",
                "wheels",
                "hardware",
                "packages",
                "frontend",
            ):
                (bundle / directory).mkdir(parents=True, exist_ok=True)
            shutil.copy2(INSTALLER_PATH, bundle / "scripts" / "install.sh")
            (bundle / "requirements" / "runtime.lock").write_text(
                f"example==1 --hash=sha256:{'a' * 64}\n", encoding="utf-8"
            )
            (bundle / "requirements" / "hoardarr.lock").write_text(
                f"hoardarr==0.1.0 --hash=sha256:{'b' * 64}\n", encoding="utf-8"
            )
            (bundle / "config" / "hoardarr.env").write_text(
                "HOARDARR_BIND_HOST=127.0.0.1\n", encoding="utf-8"
            )
            (bundle / "scripts" / "detect-hardware.py").write_text("pass\n", encoding="utf-8")
            (bundle / "scripts" / "bootstrap.py").write_text("pass\n", encoding="utf-8")
            for package_manifest in (
                "appliance-core.txt",
                "storage-services.txt",
                "tiered-storage.txt",
                "versions.env",
            ):
                (bundle / "packages" / package_manifest).write_text(
                    "attr\nlldpd\nmergerfs\nsamba\n"
                    if package_manifest == "appliance-core.txt"
                    else "placeholder\n",
                    encoding="utf-8",
                )
            for unit in (
                "api",
                "worker",
                "migrate",
                "account-executor",
                "storage-executor",
                "storage-status",
            ):
                (bundle / "systemd" / f"hoardarr-{unit}.service").write_text(
                    "[Unit]\n", encoding="utf-8"
                )
            (bundle / "systemd" / "hoardarr-lldpd.conf").write_text(
                "[Service]\n", encoding="utf-8"
            )
            (bundle / "docs" / "backend.md").write_text("# Backend\n", encoding="utf-8")
            (bundle / "frontend" / "index.html").write_text(
                "<!doctype html><title>Hoardarr</title>\n", encoding="utf-8"
            )
            (bundle / "wheels" / "fake-1-py3-none-any.whl").write_text("wheel\n", encoding="utf-8")
            (bundle / "hardware" / "providers.json").write_text("{}\n", encoding="utf-8")
            release = {
                "schema": 1,
                "name": "hoardarr",
                "version": "0.1.0",
                "release_id": "0.1.0-test",
                "target": {
                    "os_id": "ubuntu",
                    "os_version": "24.04",
                    "architecture": "amd64",
                    "machine": "x86_64",
                    "python": "3.12",
                },
            }
            (bundle / "RELEASE.json").write_text(json.dumps(release), encoding="utf-8")
            builder.write_manifest(bundle)

            result = subprocess.run(
                ["bash", str(bundle / "scripts" / "install.sh"), "plan"],
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "LANG": "C"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Plan (no changes made)", result.stdout)
            self.assertIn("0.1.0-test", result.stdout)


if __name__ == "__main__":
    unittest.main()
