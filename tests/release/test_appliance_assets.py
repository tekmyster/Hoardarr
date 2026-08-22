from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class ApplianceAssetsTests(unittest.TestCase):
    def test_autoinstall_and_build_pipeline_are_present_and_fail_closed(self) -> None:
        script = (ROOT / "scripts/build-appliance.sh").read_text(encoding="utf-8")
        user_data = (ROOT / "packaging/appliance/user-data").read_text(encoding="utf-8")
        self.assertIn("sha256sum", script)
        self.assertIn("-boot_image any replay", script)
        self.assertIn("autoinstall ds=nocloud", script)
        self.assertIn("grub_maps", script)
        self.assertIn('"$release/scripts/install.sh" "$release"', user_data)
        self.assertIn("interactive-sections", user_data)
        self.assertNotIn("size: largest", user_data)
        self.assertNotIn("curl |", script)

    def test_appliance_workflow_builds_the_real_release_cli_and_archive_layout(self) -> None:
        workflow = (ROOT / ".github/workflows/appliance.yml").read_text(encoding="utf-8")
        self.assertIn("build-release-bundle.py build --output-dir dist/releases", workflow)
        self.assertIn("dist/hoardarr-release.tar.gz", workflow)
        self.assertIn("--sort=name", workflow)
        self.assertIn("qemu-system-x86_64", workflow)
        self.assertIn("qemu-serial.log", workflow)
        self.assertNotIn("build-release-bundle.py --python", workflow)
        self.assertNotIn("${{ inputs.base_iso_url }}'", workflow)

    def test_ci_has_linux_installer_accessibility_and_isolated_storage_profiles(self) -> None:
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        storage = (ROOT / ".github/workflows/storage-integration.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("ubuntu-24.04", ci)
        self.assertIn("test:a11y", ci)
        self.assertIn("requirements/runtime.lock", ci)
        self.assertIn("requirements/hoardarr.lock", ci)
        self.assertNotIn("locked-project.txt", ci)
        self.assertIn("hoardarr-storage-test", storage)
        self.assertIn("extended-storage-stacks:", storage)
        self.assertIn("runs-on: ubuntu-24.04", storage)
        self.assertIn("HOARDARR_EXTENDED_STORAGE_TESTS=1", storage)
        self.assertIn("sudo modprobe zfs", storage)
        loop_test = (ROOT / "tests/integration/run-loop-device-tests.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("/.hoardarr-disposable-runner", loop_test)
        self.assertIn('image="$work/$name.img"', loop_test)
        self.assertIn("run-mergerfs-telemetry-workload.sh", storage)
        workload = (ROOT / "tests/integration/run-mergerfs-telemetry-workload.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("browser_disconnected_start", workload)
        self.assertIn("mergerfs-persistent-telemetry.json", workload)
        self.assertIn("test-image:mergerfs-member-", workload)


if __name__ == "__main__":
    unittest.main()
