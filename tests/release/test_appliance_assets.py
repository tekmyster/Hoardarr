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
        self.assertIn("console=tty0 ---", script)
        self.assertNotIn("console=ttyS0,115200n8 console=tty0 ---", script)
        self.assertIn("grub_maps", script)
        self.assertIn(
            '"$release/scripts/install.sh" apply --yes --preserve-existing-login-account --defer-service-start',
            user_data,
        )
        self.assertIn("mkdir -p /etc/motd.d", user_data)
        self.assertIn("interactive-sections", user_data)
        self.assertRegex(user_data, r"interactive-sections:\s*\n\s*- identity\s*\n\s*- storage")
        self.assertNotIn('password: "!"', user_data)
        self.assertNotIn("username: hoardarr-setup", user_data)
        self.assertNotIn("size: largest", user_data)
        self.assertNotIn("curl |", script)

    def test_appliance_workflow_builds_the_real_release_cli_and_archive_layout(self) -> None:
        workflow = (ROOT / ".github/workflows/appliance.yml").read_text(encoding="utf-8")
        self.assertIn("build-release-bundle.py build --output-dir dist/releases", workflow)
        self.assertIn("dist/hoardarr-release.tar.gz", workflow)
        self.assertIn("--sort=name", workflow)
        self.assertIn("qemu-system-x86_64", workflow)
        self.assertIn("qemu-serial.log", workflow)
        self.assertIn("QEMU installer console stayed blank after bounded wake retries", workflow)
        self.assertIn("monitor_command(b\"sendkey ret\")", workflow)
        self.assertIn("for attempt in range(40)", workflow)
        self.assertIn("if: always()", workflow)
        self.assertNotIn("build-release-bundle.py --python", workflow)
        self.assertNotIn("${{ inputs.base_iso_url }}'", workflow)

    def test_appliance_rewrites_media_checksums_for_every_injected_file(self) -> None:
        builder = (ROOT / "scripts" / "build-appliance.sh").read_text(encoding="utf-8")
        self.assertIn("xorriso -osirrox on -indev \"$base_iso\" -extract /md5sum.txt", builder)
        self.assertIn('find "$work" -type d -exec chmod u+rwx {} +', builder)
        self.assertIn('update_checksum "$work/user-data" nocloud/user-data', builder)
        self.assertIn('update_checksum "$work/meta-data" nocloud/meta-data', builder)
        self.assertIn(
            'update_checksum "$work/hoardarr-release.tar.gz" hoardarr/hoardarr-release.tar.gz',
            builder,
        )
        self.assertIn('"${checksum_map[@]}"', builder)

    def test_lab_appliance_is_separate_key_only_automation(self) -> None:
        builder = (ROOT / "scripts" / "build-appliance.sh").read_text(encoding="utf-8")
        template = (ROOT / "packaging" / "appliance" / "lab-user-data.template").read_text(
            encoding="utf-8"
        )
        workflow = (ROOT / ".github" / "workflows" / "lab-appliance.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("[USER_DATA]", builder)
        self.assertIn('user_data="$(realpath -- "${5:-packaging/appliance/user-data}")"', builder)
        self.assertRegex(template, r'password: "\$6\$[^"\s]+"')
        self.assertNotIn('password: "!"', template)
        self.assertIn("lock_passwd: false", template)
        self.assertIn("allow-pw: false", template)
        self.assertEqual(template.count("__SSH_PUBLIC_KEY__"), 2)
        self.assertIn("--defer-service-start", template)
        self.assertIn("mkdir -p /etc/motd.d", template)
        self.assertIn("workflow_dispatch", workflow)
        self.assertIn("rc/0.3.11-validation", workflow)
        self.assertIn("LAB_SSH_PUBLIC_KEY", workflow)
        self.assertIn("placeholder count changed", workflow)
        self.assertNotIn("PRIVATE KEY", template + workflow)

    def test_vmware_lab_reinstall_is_virtual_only_and_checksum_addressed(self) -> None:
        provisioner = (ROOT / "scripts" / "lab" / "provision-vmware-lab.ps1").read_text(
            encoding="utf-8"
        )
        wrapper = (
            ROOT / "scripts" / "lab" / "invoke-vmware-lab-provision.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("DiskType -ne 'Flat'", provisioner)
        self.assertIn("CapacityGB - 24.0", provisioner)
        self.assertIn("does not have the exact disposable one-disk lab OS topology", provisioner)
        self.assertIn("Remove-HardDisk -HardDisk $disks[0] -DeletePermanently", provisioner)
        self.assertIn("Get-FileHash -Algorithm SHA256", provisioner)
        self.assertIn("$isoDigest.Substring(0, 12)", provisioner)
        self.assertNotIn("RawDevice", provisioner)
        self.assertNotIn("ScsiLun", provisioner)
        self.assertIn("returned an unexpected console screenshot path", provisioner)
        self.assertIn("^hoardarr-vcenter-[0-9a-f-]{36}$", wrapper)
        self.assertIn("-WhatIf:$false", wrapper)

    def test_vmware_lab_installed_os_boot_is_bounded_to_exact_virtual_disk(self) -> None:
        provisioner = (ROOT / "scripts" / "lab" / "provision-vmware-lab.ps1").read_text(
            encoding="utf-8"
        )
        wrapper = (
            ROOT / "scripts" / "lab" / "invoke-vmware-lab-provision.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("[switch]$BootInstalledOs", provisioner)
        self.assertIn("Assert-ExactLabOsTopology -Vm $vm -Datastore $datastore", provisioner)
        self.assertIn("Set-CDDrive -CD $cd -NoMedia -StartConnected:$false", provisioner)
        self.assertIn("BootInstalledOs cannot be combined", provisioner)
        self.assertIn("BootInstalledOs = $BootInstalledOs", wrapper)

    def test_vmware_lab_can_recover_one_exact_node_without_overwriting_iso(self) -> None:
        provisioner = (ROOT / "scripts" / "lab" / "provision-vmware-lab.ps1").read_text(
            encoding="utf-8"
        )
        wrapper = (
            ROOT / "scripts" / "lab" / "invoke-vmware-lab-provision.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("[string]$TargetVmName", provisioner)
        self.assertIn("if ($TargetVmName) { @($TargetVmName) }", provisioner)
        self.assertIn("-not $ReuseUploadedIso", provisioner)
        self.assertIn("$provisionArguments.TargetVmName = $TargetVmName", wrapper)

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
        self.assertIn("parity-1/snapraid.content", loop_test)
        self.assertIn("run-mergerfs-telemetry-workload.sh", storage)
        workload = (ROOT / "tests/integration/run-mergerfs-telemetry-workload.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("browser_disconnected_start", workload)
        self.assertIn("mergerfs-persistent-telemetry.json", workload)
        self.assertIn("test-image:mergerfs-member-", workload)


if __name__ == "__main__":
    unittest.main()
