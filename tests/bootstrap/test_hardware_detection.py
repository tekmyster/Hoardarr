"""Tests for deterministic, read-only storage hardware discovery."""

from __future__ import annotations

import ast
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DETECTOR = ROOT / "scripts" / "detect-hardware.py"
FIXTURES = ROOT / "tests" / "fixtures" / "hardware"
PROVIDERS_FILE = ROOT / "packaging" / "hardware" / "providers.json"
VENDOR_TOOLS_FILE = ROOT / "packaging" / "hardware" / "vendor-tools.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def invoke_detector(
    fixture: pathlib.Path | None = None, sysfs_root: pathlib.Path | None = None
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(DETECTOR), "--format", "json"]
    if fixture is not None:
        command.extend(("--fixture", str(fixture)))
    if sysfs_root is not None:
        command.extend(("--sysfs-root", str(sysfs_root)))
    return subprocess.run(command, capture_output=True, check=False, text=True, timeout=30)


def detect_fixture(name: str) -> tuple[dict[str, object], str]:
    result = invoke_detector(FIXTURES / name)
    if result.returncode != 0:
        raise AssertionError(f"detector failed for {name}: {result.stderr}")
    return json.loads(result.stdout), result.stdout


def providers_by_address(payload: dict[str, object]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    records = [
        *payload["controllers"],  # type: ignore[index]
        *payload.get("transport_hosts", []),  # type: ignore[arg-type]
    ]
    for record in records:
        provider = record["provider"]
        result[record["address"]] = None if provider is None else provider["id"]
    return result


def write_sysfs_value(root: pathlib.Path, relative: str, value: str) -> None:
    path = root / pathlib.PurePosixPath(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")


class HardwareFixtureTests(unittest.TestCase):
    def test_dell_oem_generation_resolution_precedes_generic_drivers(self) -> None:
        payload, _ = detect_fixture("dell-generations.json")
        self.assertEqual(
            providers_by_address(payload),
            {
                "0000:02:00.0": "dell-perc-megaraid",
                "0000:41:00.0": "dell-perc-mpi3",
                "0000:83:00.0": "dell-fusion-hba",
            },
        )
        for controller in payload["controllers"]:
            self.assertEqual(controller["provider"]["match_tier"], "oem-driver")
        self.assertIn("dell-platform-management", self._platform_ids(payload))
        self.assertIn("generic-bmc-management", self._platform_ids(payload))

    def test_hpe_oem_and_microchip_resolution(self) -> None:
        payload, _ = detect_fixture("hpe-microchip.json")
        self.assertEqual(
            providers_by_address(payload),
            {
                "0000:03:00.0": "hpe-mr-mpi3",
                "0000:04:00.0": "hpe-smart-array-legacy",
                "0000:05:00.0": "hpe-smart-array-smartpqi",
                "0000:06:00.0": "microchip-smartpqi",
                "0000:07:00.0": "adaptec-aacraid",
            },
        )
        self.assertIn("hpe-platform-management", self._platform_ids(payload))
        self.assertIn("ilorest", payload["recommendations"]["packages"])
        self.assertIn("redfishtool", payload["recommendations"]["packages"])
        self.assertNotIn("hpe-ilorest", payload["recommendations"]["vendor_tools"])
        self.assertIn("hpe-ssacli", payload["recommendations"]["vendor_tools"])
        self.assertIn("hpe-storcli2", payload["recommendations"]["vendor_tools"])

    def test_broadcom_lsi_generations(self) -> None:
        payload, _ = detect_fixture("broadcom-generations.json")
        self.assertEqual(
            providers_by_address(payload),
            {
                "0000:01:00.0": "broadcom-fusion-hba",
                "0000:42:00.0": "broadcom-megaraid",
                "0000:65:00.0": "broadcom-mpi3",
            },
        )

    def test_areca_marvell_ahci_and_generic_raid(self) -> None:
        payload, _ = detect_fixture("common-controllers.json")
        self.assertEqual(
            providers_by_address(payload),
            {
                "0000:02:00.0": "areca-raid",
                "0000:03:00.0": "marvell-mvumi",
                "0000:04:00.0": "marvell-sas-hba",
                "0000:05:00.0": "marvell-ahci",
                "0000:06:00.0": "ahci-sata",
                "0000:08:00.0": "generic-raid-controller",
            },
        )

    def test_vmd_and_native_nvme(self) -> None:
        payload, _ = detect_fixture("nvme-vmd.json")
        self.assertEqual(
            providers_by_address(payload),
            {
                "0000:00:0e.0": "intel-vmd",
                "0000:5d:00.0": "native-nvme",
            },
        )
        self.assertIn("nvme-cli", payload["recommendations"]["packages"])

    def test_fc_fcoe_and_host_attributes(self) -> None:
        payload, _ = detect_fixture("fibre-channel.json")
        self.assertEqual(
            providers_by_address(payload),
            {
                "0000:82:00.0": "emulex-fibre-channel",
                "0000:84:00.0": "generic-fibre-channel",
                "host12": "qlogic-fibre-channel",
                "host7": "fcoe-host",
            },
        )
        host = next(item for item in payload["transport_hosts"] if item["address"] == "host12")
        self.assertEqual(host["attributes"]["port_state"], "Online")
        self.assertIn("fcoe-utils", payload["recommendations"]["packages"])
        self.assertIn("multipath-tools", payload["recommendations"]["packages"])

    def test_hyperv_virtual_storage_warning(self) -> None:
        payload, _ = detect_fixture("hyperv.json")
        self.assertEqual(
            providers_by_address(payload),
            {"f8b3781a-1e82-4818-a1c3-63d806ec15bb": "hyperv-storvsc"},
        )
        self.assertIn("microsoft-virtual-machine", self._platform_ids(payload))
        self.assertTrue(any("Hyper-V host" in item for item in payload["warnings"]))

    def test_usb_uas_hyperv_disk_keeps_identity_and_unavailable_lifetime_metric(self) -> None:
        payload, _ = detect_fixture("hyperv-usb-cisco-ssd.json")
        self.assertEqual(len(payload["disks"]), 1)
        disk = payload["disks"][0]
        self.assertEqual(disk["id"], "serial:cisco:ssd-240g:stp26501raw")
        self.assertTrue(disk["stable_identity"])
        self.assertTrue(disk["volatile_locator"])
        self.assertEqual(disk["identity"]["serial"], "STP26501RAW")
        self.assertEqual(
            (disk["vendor"], disk["model"], disk["firmware_revision"]), ("CISCO", "SSD-240G", "V01")
        )
        self.assertEqual(disk["capacity_bytes"], 240057409536)
        self.assertEqual(disk["sector_sizes"], {"logical_bytes": 512, "physical_bytes": 4096})
        self.assertEqual(
            disk["connection"],
            {
                "capable_speed_gbps": None,
                "controller_address": "f8b3781a-1e82-4818-a1c3-63d806ec15bb",
                "enclosure_id": None,
                "enclosure_model": None,
                "enclosure_status": None,
                "enclosure_vendor": None,
                "expander_id": None,
                "hba_port": None,
                "negotiated_speed_gbps": None,
                "path_components": [],
                "path_id": None,
                "presentation": "hyperv-scsi",
                "protocol": "uas",
                "slot": None,
                "transport": "usb",
                "transport_host": None,
            },
        )

        power_on_hours = disk["health"]["power_on_hours"]
        self.assertEqual(power_on_hours["status"], "unavailable")
        self.assertIsNone(power_on_hours["value"])
        self.assertEqual(power_on_hours["confidence"], "unavailable")
        self.assertEqual(power_on_hours["transport"], "usb/uas -> hyperv-scsi")
        self.assertTrue(power_on_hours["source"])
        self.assertTrue(power_on_hours["captured_at"])
        # The OS-reported 8 hours and 16h37m attachment duration remain
        # traceable observations, never a fabricated lifetime SMART value.
        self.assertEqual(
            [(item["source"], item["value"]) for item in power_on_hours["observations"]],
            [
                ("windows-storage-reliability-counter", 8),
                ("os-device-attachment-duration", 59820),
            ],
        )
        self.assertTrue(
            all(not item["qualifies_as_lifetime"] for item in power_on_hours["observations"])
        )

    def test_conflicting_raw_and_translated_health_values_never_select_a_winner(self) -> None:
        document = json.loads((FIXTURES / "hyperv-usb-cisco-ssd.json").read_text(encoding="utf-8"))
        metric = document["disks"][0]["health"]["power_on_hours"]
        metric.update(
            {
                "confidence": "conflicting",
                "reason": "Raw SMART and translated OS counters disagree.",
                "status": "conflicting",
                "value": None,
            }
        )
        metric["observations"] = [
            {
                "captured_at": "2026-08-16T20:07:00Z",
                "confidence": "high",
                "qualifies_as_lifetime": True,
                "reason": "Raw SMART attribute.",
                "source": "smartctl-raw-device-data",
                "transport": "usb/uas -> hyperv-scsi",
                "unit": "hours",
                "value": 9,
            },
            {
                "captured_at": "2026-08-16T20:07:00Z",
                "confidence": "low",
                "qualifies_as_lifetime": True,
                "reason": "Translated host counter.",
                "source": "windows-storage-reliability-counter",
                "transport": "usb/uas -> hyperv-scsi",
                "unit": "hours",
                "value": 8,
            },
        ]
        with tempfile.TemporaryDirectory() as temporary:
            fixture = pathlib.Path(temporary) / "conflicting-health.json"
            fixture.write_text(json.dumps(document), encoding="utf-8")
            result = invoke_detector(fixture)
        self.assertEqual(result.returncode, 0, result.stderr)
        output_metric = json.loads(result.stdout)["disks"][0]["health"]["power_on_hours"]
        self.assertEqual(output_metric["status"], "conflicting")
        self.assertEqual(output_metric["confidence"], "conflicting")
        self.assertIsNone(output_metric["value"])
        self.assertEqual(
            [item["source"] for item in output_metric["observations"]],
            ["smartctl-raw-device-data", "windows-storage-reliability-counter"],
        )

    def test_supermicro_and_45drives_keep_lsi_provider_authoritative(self) -> None:
        cases = (
            (
                "supermicro-lsi.json",
                "broadcom-fusion-hba",
                "supermicro-platform-management",
                "supermicro-sum",
            ),
            (
                "45drives-lsi.json",
                "broadcom-megaraid",
                "fortyfive-drives-platform-management",
                "fortyfive-drives-tools",
            ),
        )
        for fixture, controller_provider, platform_provider, platform_tool in cases:
            with self.subTest(fixture=fixture):
                payload, _ = detect_fixture(fixture)
                only_provider = next(iter(providers_by_address(payload).values()))
                self.assertEqual(only_provider, controller_provider)
                self.assertIn(platform_provider, self._platform_ids(payload))
                self.assertIn("generic-bmc-management", self._platform_ids(payload))
                self.assertIn(platform_tool, payload["recommendations"]["vendor_tools"])
                self.assertIn("redfishtool", payload["recommendations"]["packages"])
                self.assertTrue(
                    any(
                        "PCI controller provider remains authoritative" in item
                        for item in payload["warnings"]
                    )
                )
                if fixture == "45drives-lsi.json":
                    self.assertTrue(any("wipedev" in item for item in payload["warnings"]))

    def test_oracle_platform_keeps_lsi_and_nvme_providers_authoritative(self) -> None:
        payload, _ = detect_fixture("oracle-lsi-nvme.json")
        self.assertEqual(
            providers_by_address(payload),
            {
                "0000:18:00.0": "broadcom-fusion-hba",
                "0000:5e:00.0": "native-nvme",
            },
        )
        self.assertIn("oracle-sun-platform-management", self._platform_ids(payload))
        self.assertIn("generic-bmc-management", self._platform_ids(payload))
        self.assertIn("redfishtool", payload["recommendations"]["packages"])
        self.assertIn("nvme-cli", payload["recommendations"]["packages"])
        self.assertIn("broadcom-storcli", payload["recommendations"]["vendor_tools"])
        self.assertTrue(any("Oracle ILOM" in item for item in payload["warnings"]))
        self.assertTrue(
            any(
                "PCI controller provider remains authoritative" in item
                for item in payload["warnings"]
            )
        )

    def test_output_is_byte_deterministic_sorted_and_path_private(self) -> None:
        payload_a, text_a = detect_fixture("dell-generations.json")
        _payload_b, text_b = detect_fixture("dell-generations.json")
        self.assertEqual(text_a, text_b)
        addresses = [item["address"] for item in payload_a["controllers"]]
        self.assertEqual(addresses, sorted(addresses))
        transport_addresses = [item["address"] for item in payload_a["transport_hosts"]]
        self.assertEqual(transport_addresses, sorted(transport_addresses))
        recommendations = payload_a["recommendations"]
        self.assertEqual(recommendations["packages"], sorted(set(recommendations["packages"])))
        self.assertEqual(
            recommendations["vendor_tools"], sorted(set(recommendations["vendor_tools"]))
        )
        self.assertEqual(payload_a["warnings"], sorted(set(payload_a["warnings"])))
        self.assertEqual(payload_a["source"], {"kind": "fixture", "name": "dell-generations.json"})
        self.assertNotIn(str(FIXTURES.resolve()), text_a)

    @staticmethod
    def _platform_ids(payload: dict[str, object]) -> set[str]:
        return {item["id"] for item in payload["platform"]["recommendations"]}  # type: ignore[index]


class LiveSysfsTests(unittest.TestCase):
    def test_system_disk_resolution_follows_device_mapper_slaves(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            disk = "class/block/sda"
            partition = f"{disk}/sda2"
            write_sysfs_value(root, f"{disk}/dev", "8:0")
            write_sysfs_value(root, f"{disk}/size", "2097152")
            write_sysfs_value(root, f"{disk}/ro", "0")
            write_sysfs_value(root, f"{disk}/removable", "0")
            write_sysfs_value(root, f"{disk}/queue/rotational", "0")
            write_sysfs_value(root, f"{disk}/queue/logical_block_size", "512")
            write_sysfs_value(root, f"{disk}/queue/physical_block_size", "4096")
            write_sysfs_value(root, f"{disk}/device/type", "0")
            write_sysfs_value(root, f"{disk}/device/vendor", "TEST")
            write_sysfs_value(root, f"{disk}/device/model", "SYSTEM")
            write_sysfs_value(root, f"{disk}/device/serial", "SERIAL-SYSTEM")
            write_sysfs_value(root, f"{partition}/dev", "8:2")
            write_sysfs_value(root, f"{partition}/partition", "2")
            write_sysfs_value(root, f"{partition}/start", "2048")
            write_sysfs_value(root, f"{partition}/size", "1048576")
            write_sysfs_value(root, "class/block/dm-0/dev", "253:0")
            (root / "class" / "block" / "dm-0" / "slaves" / "sda2").mkdir(parents=True)
            write_sysfs_value(
                root,
                "proc/self/mountinfo",
                "36 25 253:0 / / rw - ext4 /dev/mapper/ubuntu-root rw",
            )
            write_sysfs_value(root, "proc/swaps", "Filename Type Size Used Priority\n")

            result = invoke_detector(sysfs_root=root)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)

        self.assertEqual(len(payload["disks"]), 1)
        self.assertTrue(payload["disks"][0]["system_disk"])

    def test_system_disks_include_boot_efi_and_swap_backing_devices(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for index, name in enumerate(("sda", "sdb", "sdc")):
                disk = f"class/block/{name}"
                partition = f"{disk}/{name}1"
                write_sysfs_value(root, f"{disk}/dev", f"8:{index * 16}")
                write_sysfs_value(root, f"{disk}/size", "2097152")
                write_sysfs_value(root, f"{disk}/ro", "0")
                write_sysfs_value(root, f"{disk}/removable", "0")
                write_sysfs_value(root, f"{disk}/queue/rotational", "0")
                write_sysfs_value(root, f"{disk}/queue/logical_block_size", "512")
                write_sysfs_value(root, f"{disk}/queue/physical_block_size", "4096")
                write_sysfs_value(root, f"{disk}/device/type", "0")
                write_sysfs_value(root, f"{disk}/device/vendor", "TEST")
                write_sysfs_value(root, f"{disk}/device/model", name.upper())
                write_sysfs_value(root, f"{disk}/device/serial", f"SERIAL-{name}")
                write_sysfs_value(root, f"{partition}/dev", f"8:{index * 16 + 1}")
                write_sysfs_value(root, f"{partition}/partition", "1")
                write_sysfs_value(root, f"{partition}/start", "2048")
                write_sysfs_value(root, f"{partition}/size", "1048576")
            write_sysfs_value(
                root,
                "proc/self/mountinfo",
                "\n".join(
                    (
                        "36 25 8:1 / / rw - ext4 /dev/sda1 rw",
                        "37 36 8:17 / /boot/efi rw - vfat /dev/sdb1 rw",
                    )
                ),
            )
            write_sysfs_value(
                root,
                "proc/swaps",
                "Filename Type Size Used Priority\n/dev/sdc1 partition 1024 0 -2\n",
            )

            result = invoke_detector(sysfs_root=root)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)

        self.assertEqual(
            {disk["kernel_name"] for disk in payload["disks"] if disk["system_disk"]},
            {"sda", "sdb", "sdc"},
        )

    def test_live_disk_discovery_reads_sysfs_and_udev_without_opening_device(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            disk = "class/block/sda"
            write_sysfs_value(root, f"{disk}/dev", "8:0")
            write_sysfs_value(root, f"{disk}/size", "468862128")
            write_sysfs_value(root, f"{disk}/ro", "0")
            write_sysfs_value(root, f"{disk}/removable", "0")
            write_sysfs_value(root, f"{disk}/queue/rotational", "0")
            write_sysfs_value(root, f"{disk}/queue/logical_block_size", "512")
            write_sysfs_value(root, f"{disk}/queue/physical_block_size", "4096")
            write_sysfs_value(root, f"{disk}/device/type", "0")
            write_sysfs_value(root, f"{disk}/device/vendor", "CISCO")
            write_sysfs_value(root, f"{disk}/device/model", "SSD-240G")
            write_sysfs_value(root, f"{disk}/queue/discard_granularity", "4096")
            write_sysfs_value(root, f"{disk}/queue/discard_max_bytes", "2147483648")
            write_sysfs_value(root, f"{disk}/queue/discard_zeroes_data", "0")
            write_sysfs_value(root, f"{disk}/device/rev", "V01")
            write_sysfs_value(root, f"{disk}/device/serial", "STP26501RAW")
            write_sysfs_value(root, f"{disk}/driver", "uas")
            partition = f"{disk}/sda1"
            write_sysfs_value(root, f"{partition}/dev", "8:1")
            write_sysfs_value(root, f"{partition}/partition", "1")
            write_sysfs_value(root, f"{partition}/start", "2048")
            write_sysfs_value(root, f"{partition}/size", "468858000")
            write_sysfs_value(
                root,
                "proc/self/mountinfo",
                "36 25 8:1 / / rw,relatime - ext4 /dev/sda1 rw",
            )
            write_sysfs_value(
                root,
                "run/udev/data/b8:0",
                "E:ID_BUS=usb\nE:ID_PART_TABLE_TYPE=gpt\nE:ID_PART_TABLE_UUID=disk-guid",
            )
            write_sysfs_value(
                root,
                "run/udev/data/b8:1",
                "E:ID_FS_TYPE=ntfs\nE:ID_FS_USAGE=filesystem\nE:ID_FS_UUID=volume-guid\nE:ID_FS_LABEL=Media",
            )

            result = invoke_detector(sysfs_root=root)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)

        self.assertEqual(len(payload["disks"]), 1)
        discovered = payload["disks"][0]
        self.assertEqual(discovered["id"], "serial:cisco:ssd-240g:stp26501raw")
        self.assertEqual(discovered["kernel_path"], "/dev/sda")
        self.assertTrue(discovered["system_disk"])
        self.assertEqual(discovered["capacity_bytes"], 240057409536)
        self.assertEqual(discovered["connection"]["transport"], "usb")
        self.assertEqual(discovered["connection"]["protocol"], "uas")
        self.assertEqual(
            discovered["discard"],
            {
                "granularity_bytes": 4096,
                "max_bytes": 2147483648,
                "zeroes_data": False,
            },
        )
        self.assertEqual(discovered["signature_scan"]["status"], "partial")
        self.assertEqual(discovered["signatures"][0]["type"], "gpt")
        self.assertEqual(len(discovered["partitions"]), 1)
        first_partition = discovered["partitions"][0]
        self.assertEqual(first_partition["kernel_path"], "/dev/sda1")
        self.assertEqual(first_partition["mountpoints"], ["/"])
        self.assertEqual(first_partition["start_bytes"], 1048576)
        self.assertEqual(first_partition["filesystem"]["type"], "ntfs")
        power_on_hours = discovered["health"]["power_on_hours"]
        self.assertEqual(power_on_hours["status"], "unavailable")
        self.assertIsNone(power_on_hours["value"])
        self.assertIn("attachment duration", power_on_hours["reason"])

    def test_hyperv_live_sysfs_filters_unrelated_vmbus_devices(self) -> None:
        fixture = json.loads((FIXTURES / "hyperv-live-sysfs.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for relative in fixture["directories"]:
                (root / pathlib.PurePosixPath(relative)).mkdir(parents=True, exist_ok=True)
            for relative, value in fixture["files"].items():
                write_sysfs_value(root, relative, value)
            result = invoke_detector(sysfs_root=root)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)

        expected = fixture["expected"]
        self.assertEqual(len(payload["controllers"]), 1)
        self.assertEqual(payload["controllers"][0]["address"], expected["controller_address"])
        self.assertEqual(payload["controllers"][0]["provider"]["id"], expected["provider"])
        self.assertEqual(len(payload["transport_hosts"]), 1)
        self.assertEqual(payload["transport_hosts"][0]["address"], expected["transport_host"])
        self.assertEqual(payload["transport_hosts"][0]["kernel_driver"], "storvsc_host")
        self.assertEqual(payload["transport_hosts"][0]["provider"]["id"], expected["provider"])
        self.assertNotIn("hv_netvsc", result.stdout)
        self.assertNotIn("hv_balloon", result.stdout)
        provider = next(item for item in payload["providers"] if item["id"] == expected["provider"])
        self.assertEqual(provider["controller_addresses"], [expected["controller_address"]])
        self.assertEqual(provider["transport_host_addresses"], [expected["transport_host"]])

    def test_read_only_sysfs_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            # Windows fixture directories cannot contain the ':' used in a
            # Linux BDF.  The detector treats the sysfs entry name as opaque.
            pci = "bus/pci/devices/0000-03-00.0"
            write_sysfs_value(root, f"{pci}/vendor", "0x1000")
            write_sysfs_value(root, f"{pci}/device", "0x0097")
            write_sysfs_value(root, f"{pci}/subsystem_vendor", "0x1000")
            write_sysfs_value(root, f"{pci}/subsystem_device", "0x30e0")
            write_sysfs_value(root, f"{pci}/class", "0x010700")
            write_sysfs_value(root, f"{pci}/driver", "mpt3sas")
            network = "bus/pci/devices/0000-04-00.0"
            write_sysfs_value(root, f"{network}/vendor", "0x8086")
            write_sysfs_value(root, f"{network}/device", "0x10fb")
            write_sysfs_value(root, f"{network}/class", "0x020000")
            write_sysfs_value(root, f"{network}/driver", "ixgbe")
            write_sysfs_value(root, "class/dmi/id/sys_vendor", "Super Micro Computer, Inc.")
            write_sysfs_value(root, "class/dmi/id/product_name", "Super Server")
            (root / "class" / "ipmi" / "ipmi0").mkdir(parents=True)
            write_sysfs_value(root, "class/scsi_host/host4/proc_name", "qla2xxx")
            write_sysfs_value(root, "class/fc_host/host4/port_name", "0x10000090fa987654")
            write_sysfs_value(root, "class/fc_host/host4/port_state", "Online")

            result = invoke_detector(sysfs_root=root)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)

        self.assertEqual(payload["source"], {"kind": "sysfs"})
        self.assertEqual(
            providers_by_address(payload),
            {"0000-03-00.0": "broadcom-fusion-hba", "host4": "qlogic-fibre-channel"},
        )
        platform_ids = {item["id"] for item in payload["platform"]["recommendations"]}
        self.assertIn("supermicro-platform-management", platform_ids)
        self.assertTrue(payload["platform"]["bmc_detected"])
        fc_host = next(item for item in payload["transport_hosts"] if item["address"] == "host4")
        self.assertEqual(fc_host["attributes"]["port_state"], "Online")

    def test_malformed_fixture_fails_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = pathlib.Path(temporary) / "bad.json"
            fixture.write_text('{"controllers":[{"driver":"ahci"}]}', encoding="utf-8")
            result = invoke_detector(fixture)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("address must be a non-empty string", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_detector_has_no_command_execution_imports(self) -> None:
        tree = ast.parse(DETECTOR.read_text(encoding="utf-8"), filename=str(DETECTOR))
        imported: set[str] = set()
        called_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called_names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    called_names.add(node.func.attr)
        self.assertTrue({"subprocess", "ctypes", "socket", "shlex"}.isdisjoint(imported))
        self.assertTrue(
            {"system", "popen", "Popen", "run", "check_call", "check_output"}.isdisjoint(
                called_names
            )
        )


class RegistryAndCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.providers_document = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
        cls.vendor_document = json.loads(VENDOR_TOOLS_FILE.read_text(encoding="utf-8"))
        cls.providers = cls.providers_document["providers"]
        cls.platforms = cls.providers_document["platform_recommendations"]
        cls.tools = {item["id"]: item for item in cls.vendor_document["tools"]}

    def test_all_vendor_references_are_catalogued(self) -> None:
        all_providers = [*self.providers, *self.platforms]
        known_provider_ids = {item["id"] for item in all_providers}
        references = {
            identifier for provider in all_providers for identifier in provider["vendor_tools"]
        }
        self.assertEqual(references - self.tools.keys(), set())
        self.assertEqual(
            {provider_id for tool in self.tools.values() for provider_id in tool["providers"]}
            - known_provider_ids,
            set(),
        )

    def test_catalog_schema_and_fetch_safety(self) -> None:
        required = {
            "architectures",
            "archive_type",
            "deb_member",
            "distro_versions",
            "id",
            "install_method",
            "landing_url",
            "license_url",
            "providers",
            "requires_license_acceptance",
            "sha256",
            "url",
            "version",
        }
        self.assertEqual(self.vendor_document["schema_version"], 1)
        self.assertEqual(len(self.tools), len(self.vendor_document["tools"]))
        for identifier, tool in self.tools.items():
            with self.subTest(tool=identifier):
                self.assertFalse(required - tool.keys())
                self.assertIn(
                    tool["install_method"],
                    {"official-public-fetch", "official-public-manual"},
                )
                self.assertEqual(tool["providers"], sorted(set(tool["providers"])))
                if tool["install_method"] == "official-public-fetch":
                    self.assertTrue(tool["url"].startswith("https://"))
                    self.assertRegex(tool["sha256"], SHA256_RE)
                    self.assertIn(tool["archive_type"], {"deb", "tar-deb", "zip-deb"})
                    if tool["archive_type"] != "deb":
                        self.assertTrue(tool["deb_member"].endswith(".deb"))
                else:
                    self.assertIsNone(tool["url"])
                    self.assertIsNone(tool["sha256"])
                    self.assertIsNone(tool["archive_type"])
                    self.assertIsNone(tool["deb_member"])

    def test_verified_public_artifact_pins(self) -> None:
        expected = {
            "dell-perccli": "db518857870e62ac3690ffdafcb16f55d8f69537c21eaa717c1daff4232e0a75",
            "dell-perccli2": "4ec1d0da7b40c4fd9f46f7cfe9f9be3c7161609567e045285283edfb5bf7193f",
            "broadcom-storcli": "aa864c1055eb1488368593f72c0d064177fc53bd5857e4ac126a2b5a1784b754",
            "hpe-ssacli": "984742d22089aa0563d4aaa97e0c1d7527d14d1beb39f791ba355bff6d22420f",
            "hpe-storcli": "00a2a8c4aabfc0cb5d474bbb22803bc2f85620770fc4479c7aa495eb3d27f81f",
        }
        for identifier, digest in expected.items():
            with self.subTest(tool=identifier):
                self.assertEqual(self.tools[identifier]["install_method"], "official-public-fetch")
                self.assertEqual(self.tools[identifier]["sha256"], digest)
        self.assertEqual(
            self.tools["broadcom-storcli"]["deb_member"],
            "STORCLI_SAS3.5_P37/univ_viva_cli_rel/Unified_storcli_all_os/Ubuntu/"
            "storcli_007.3603.0000.0000_all.deb",
        )
        self.assertNotIn("24.04", self.tools["dell-perccli"]["distro_versions"])
        self.assertNotIn("24.04", self.tools["dell-perccli2"]["distro_versions"])
        self.assertEqual(self.tools["hpe-ssacli"]["distro_versions"], ["24.04"])
        self.assertEqual(self.tools["hpe-storcli"]["distro_versions"], ["24.04"])
        self.assertEqual(
            self.tools["dell-perccli"]["http_headers"]["Referer"],
            self.tools["dell-perccli"]["landing_url"],
        )

    def test_verified_validation_commands_are_read_only(self) -> None:
        expected = {
            "broadcom-storcli": (
                ["/opt/MegaRAID/storcli/storcli64", "-v"],
                "007.3603.0000.0000",
            ),
            "hpe-storcli": (
                ["/opt/MegaRAID/storcli/storcli64", "-v"],
                "007.3210.0000.0000",
            ),
            "dell-perccli": (
                ["/opt/MegaRAID/perccli/perccli64", "-v"],
                "007.2313.0000.0000",
            ),
            "dell-perccli2": (
                ["/opt/MegaRAID/perccli2/perccli2", "show"],
                "008.0004.0000.0022",
            ),
        }
        for identifier, (command, version) in expected.items():
            with self.subTest(tool=identifier):
                self.assertEqual(
                    self.tools[identifier]["validation"],
                    {"command": command, "version_contains": version},
                )
                self.assertIn(command[1], {"-v", "show"})
                self.assertTrue(command[0].startswith("/opt/MegaRAID/"))
        self.assertNotIn("validation", self.tools["hpe-ssacli"])

    def test_open_source_packages_are_separate_and_sorted(self) -> None:
        packages = self.vendor_document["bundled_open_source_packages"]
        self.assertEqual(packages, sorted(set(packages)))
        for package in (
            "ilorest",
            "ipmitool",
            "nvme-cli",
            "redfishtool",
            "sg3-utils",
            "smartmontools",
        ):
            self.assertIn(package, packages)
        self.assertNotIn("hpe-ilorest", self.tools)

    def test_top_level_recommended_tool_metadata_is_machine_readable(self) -> None:
        payload, _ = detect_fixture("dell-generations.json")
        ids = [item["id"] for item in payload["vendor_tools"]]
        self.assertEqual(ids, sorted(payload["recommendations"]["vendor_tools"]))
        perccli = next(item for item in payload["vendor_tools"] if item["id"] == "dell-perccli")
        self.assertEqual(perccli["recommended_by"], ["dell-fusion-hba", "dell-perc-megaraid"])
        self.assertEqual(perccli["install_method"], "official-public-fetch")


if __name__ == "__main__":
    unittest.main()
