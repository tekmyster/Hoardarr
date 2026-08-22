"""Validate bootstrap package and hardware-provider manifests.

The tests use only the Python standard library so they can run before the
build-host dependency set has been installed. They are also discoverable by
pytest when it is available.
"""

from __future__ import annotations

import json
import importlib.util
import re
import types
import unittest
from collections.abc import Iterable
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PACKAGES_DIR = ROOT / "packaging" / "packages"
HARDWARE_DIR = ROOT / "packaging" / "hardware"
VERSIONS_FILE = PACKAGES_DIR / "versions.env"
PROVIDERS_FILE = HARDWARE_DIR / "providers.json"
COMMAND_CAPABILITIES_FILE = HARDWARE_DIR / "command-capabilities.json"
BOOTSTRAP_FILE = ROOT / "scripts" / "bootstrap.py"

PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]*(?::[a-z0-9][a-z0-9-]*)?$")
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
INTEGRITY_RE = re.compile(r"^sha(?:256|384|512)-[A-Za-z0-9+/]+={0,2}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:[._+-][a-z0-9]+)*$")
CAPABILITY_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
COMMAND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")

EXPECTED_MANIFESTS = {
    "advanced-fcoe.txt",
    "advanced-ha.txt",
    "appliance-core.txt",
    "build-host.txt",
    "storage-services.txt",
    "tiered-storage.txt",
    "vendor-optional.txt",
}

REQUIRED_STORAGE_LIFECYCLE_PACKAGES = {
    "btrfs-progs",
    "cryptsetup",
    "e2fsprogs",
    "f3",
    "fio",
    "hdparm",
    "lvm2",
    "mdadm",
    "mergerfs",
    "multipath-tools",
    "nvme-cli",
    "samba",
    "sdparm",
    "sg3-utils",
    "smartmontools",
    "usbutils",
    "util-linux",
    "xfsprogs",
    "zfsutils-linux",
}

REQUIRED_ONBOARDING_NETWORK_PACKAGES = {
    "iproute2",
    "lldpd",
    "netplan.io",
    "systemd-timesyncd",
}

REQUIRED_ONBOARDING_NETWORK_COMMANDS = {"ip", "lldpcli", "netplan", "timedatectl"}

REQUIRED_STORAGE_LIFECYCLE_COMMANDS = {
    "badblocks",
    "blkid",
    "blockdev",
    "btrfs",
    "cryptsetup",
    "f3probe",
    "f3read",
    "f3write",
    "fio",
    "findmnt",
    "hdparm",
    "lsblk",
    "lsscsi",
    "lsusb",
    "mdadm",
    "multipath",
    "nvme",
    "pdbedit",
    "sdparm",
    "sg_inq",
    "sg_logs",
    "sg_readcap",
    "sg_ses",
    "smartctl",
    "smartd",
    "smbpasswd",
    "wipefs",
    "xfs_repair",
    "zdb",
    "zfs",
    "zpool",
}

WIZARD_COMMANDS_BY_UBUNTU_PACKAGE = {
    "e2fsprogs": {"badblocks"},
    "util-linux": {"blkid", "findmnt", "lsblk", "wipefs"},
    "cryptsetup": {"cryptsetup"},
    "f3": {"f3probe", "f3read", "f3write"},
    "fio": {"fio"},
    "hdparm": {"hdparm"},
    "usbutils": {"lsusb"},
    "sdparm": {"sdparm"},
    "sg3-utils": {"sg_logs", "sg_ses"},
    "smartmontools": {"smartctl"},
    "zfsutils-linux": {"zdb", "zfs", "zpool"},
}

EXPECTED_VERSION_KEYS = {
    "COREPACK_INTEGRITY",
    "COREPACK_VERSION",
    "NODE_SHA256_AMD64",
    "NODE_SHA256_ARM64",
    "NODE_VERSION",
    "PNPM_INTEGRITY",
    "PNPM_VERSION",
    "UV_SHA256_AMD64",
    "UV_SHA256_ARM64",
    "UV_VERSION",
}

EXPECTED_PACKAGE_COMMANDS = {
    "dmidecode": {"dmidecode"},
    "freeipmi-tools": {"ipmi-fru", "ipmi-sensors"},
    "hdparm": {"hdparm"},
    "ipmitool": {"ipmitool"},
    "ledmon": {"ledctl", "ledmon"},
    "lsscsi": {"lsscsi"},
    "mdadm": {"mdadm"},
    "multipath-tools": {"multipath", "multipathd"},
    "nvme-cli": {"nvme"},
    "pciutils": {"lspci"},
    "sdparm": {"sdparm"},
    "sg3-utils": {"sg_inq", "sg_logs", "sg_map", "sg_ses", "sg_vpd"},
    "smartmontools": {"smartctl", "smartd"},
    "smp-utils": {"smp_discover", "smp_discover_list"},
    "usbutils": {"lsusb"},
}

ALLOWED_COMMAND_OUTPUTS = {"json", "json-or-text", "key-value", "text"}
ALLOWED_COMMAND_POLICIES = {
    "allowlisted",
    "indicator-only",
    "read-only",
    "status-only",
}
ALLOWED_COMMAND_PRIVILEGES = {"root", "user"}
ALLOWED_PROVIDER_KINDS = {"storage", "transport"}


class DuplicateJsonKeyError(ValueError):
    """Raised when a JSON object repeats a key."""


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle, object_pairs_hook=_reject_duplicate_json_keys)
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return value


def load_bootstrap_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("hoardarr_bootstrap", BOOTSTRAP_FILE)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {BOOTSTRAP_FILE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def package_entries(path: Path) -> list[str]:
    entries: list[str] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not PACKAGE_RE.fullmatch(line):
            raise AssertionError(
                f"{path}:{line_number}: invalid Debian package name {line!r}"
            )
        entries.append(line)
    return entries


def resolve_package_profiles(paths: Iterable[Path]) -> list[str]:
    """Return a stable, deduplicated union of independently usable profiles."""

    resolved: list[str] = []
    seen: set[str] = set()
    for path in paths:
        for package in package_entries(path):
            if package not in seen:
                seen.add(package)
                resolved.append(package)
    return resolved


def parse_versions(path: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise AssertionError(f"{path}:{line_number}: expected NAME=value")
        key, value = line.split("=", 1)
        if key != key.strip() or value != value.strip():
            raise AssertionError(
                f"{path}:{line_number}: whitespace around NAME=value is not allowed"
            )
        if not ENV_KEY_RE.fullmatch(key):
            raise AssertionError(f"{path}:{line_number}: invalid variable name {key!r}")
        if key in versions:
            raise AssertionError(f"{path}:{line_number}: duplicate variable {key}")
        if not value:
            raise AssertionError(f"{path}:{line_number}: {key} has an empty value")
        if any(character.isspace() for character in value):
            raise AssertionError(f"{path}:{line_number}: {key} contains whitespace")
        versions[key] = value
    return versions


def assert_unique_strings(
    testcase: unittest.TestCase,
    values: Any,
    *,
    label: str,
    pattern: re.Pattern[str] | None = None,
    normalized_lowercase: bool = False,
) -> list[str]:
    testcase.assertIsInstance(values, list, f"{label} must be an array")
    checked: list[str] = []
    for index, value in enumerate(values):
        testcase.assertIsInstance(value, str, f"{label}[{index}] must be a string")
        testcase.assertEqual(value, value.strip(), f"{label}[{index}] has whitespace")
        testcase.assertTrue(value, f"{label}[{index}] must not be empty")
        if normalized_lowercase:
            testcase.assertEqual(
                value,
                value.lower(),
                f"{label}[{index}] must be normalized lowercase",
            )
        if pattern is not None:
            testcase.assertRegex(value, pattern, f"{label}[{index}] has invalid syntax")
        checked.append(value)
    testcase.assertEqual(
        len(checked), len(set(checked)), f"{label} contains duplicate entries"
    )
    return checked


class PackageManifestTests(unittest.TestCase):
    def test_all_package_manifests_are_valid_and_unique(self) -> None:
        manifests = sorted(PACKAGES_DIR.glob("*.txt"))
        self.assertTrue(manifests, "no package manifests found")
        self.assertTrue(
            EXPECTED_MANIFESTS.issubset({path.name for path in manifests}),
            "one or more required package profiles are missing",
        )

        for manifest in manifests:
            with self.subTest(manifest=manifest.name):
                entries = package_entries(manifest)
                self.assertEqual(
                    len(entries),
                    len(set(entries)),
                    f"{manifest} contains duplicate package entries",
                )

    def test_appliance_core_contains_storage_lifecycle_dependencies(self) -> None:
        packages = set(package_entries(PACKAGES_DIR / "appliance-core.txt"))
        missing_packages = REQUIRED_STORAGE_LIFECYCLE_PACKAGES - packages
        self.assertFalse(
            missing_packages,
            "appliance-core is missing required storage lifecycle packages: "
            f"{sorted(missing_packages)}",
        )

        bootstrap = load_bootstrap_module()
        commands = set(bootstrap.PROFILE_COMMANDS["appliance-core"])
        missing_commands = REQUIRED_STORAGE_LIFECYCLE_COMMANDS - commands
        self.assertFalse(
            missing_commands,
            "appliance-core command validation is missing required tools: "
            f"{sorted(missing_commands)}",
        )

    def test_wizard_commands_match_ubuntu_package_ownership(self) -> None:
        packages = set(package_entries(PACKAGES_DIR / "appliance-core.txt"))
        bootstrap = load_bootstrap_module()
        commands = set(bootstrap.PROFILE_COMMANDS["appliance-core"])
        command_checks = {
            item["command"]: item
            for item in bootstrap.validate_commands(["appliance-core"])
        }

        for package, expected_commands in WIZARD_COMMANDS_BY_UBUNTU_PACKAGE.items():
            with self.subTest(package=package):
                self.assertIn(package, packages)
                self.assertFalse(
                    expected_commands - commands,
                    f"{package} commands are missing from appliance validation: "
                    f"{sorted(expected_commands - commands)}",
                )
                for command in expected_commands:
                    self.assertEqual(
                        bootstrap.COMMAND_PACKAGE_HINTS.get(command),
                        package,
                        f"{command} has the wrong Ubuntu package hint",
                    )
                    self.assertEqual(
                        command_checks[command]["package_hint"],
                        package,
                        f"{command} validation does not report its package hint",
                    )

    def test_appliance_core_contains_first_run_network_dependencies(self) -> None:
        packages = set(package_entries(PACKAGES_DIR / "appliance-core.txt"))
        self.assertFalse(
            REQUIRED_ONBOARDING_NETWORK_PACKAGES - packages,
            "appliance-core is missing first-run network packages",
        )
        bootstrap = load_bootstrap_module()
        commands = set(bootstrap.PROFILE_COMMANDS["appliance-core"])
        self.assertFalse(
            REQUIRED_ONBOARDING_NETWORK_COMMANDS - commands,
            "appliance-core is missing first-run network command validation",
        )

    def test_aggregate_profile_resolution_is_deduplicated(self) -> None:
        self.assertTrue(BOOTSTRAP_FILE.is_file(), f"missing {BOOTSTRAP_FILE}")
        bootstrap = load_bootstrap_module()
        profiles = bootstrap.expand_profiles(["all"])
        by_profile, resolved = bootstrap.collect_profile_packages(profiles)
        expected = {
            package
            for packages in by_profile.values()
            for package in packages
        }

        self.assertEqual(set(resolved), expected)
        self.assertEqual(len(resolved), len(set(resolved)))
        self.assertEqual(resolved, sorted(resolved))

    def test_versions_env_is_pinned_and_well_formed(self) -> None:
        self.assertTrue(VERSIONS_FILE.is_file(), f"missing {VERSIONS_FILE}")
        versions = parse_versions(VERSIONS_FILE)
        self.assertTrue(
            EXPECTED_VERSION_KEYS.issubset(versions),
            f"missing version keys: {sorted(EXPECTED_VERSION_KEYS - versions.keys())}",
        )

        for key, value in versions.items():
            with self.subTest(key=key):
                if key.endswith("_VERSION"):
                    self.assertRegex(value, VERSION_RE)
                elif "_SHA256_" in key:
                    self.assertRegex(value, SHA256_RE)
                elif key.endswith("_INTEGRITY"):
                    self.assertRegex(value, INTEGRITY_RE)


class HardwareManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        manifests = sorted(PACKAGES_DIR.glob("*.txt"))
        cls.packages = set(resolve_package_profiles(manifests))

    def test_provider_registry(self) -> None:
        self.assertTrue(PROVIDERS_FILE.is_file(), f"missing {PROVIDERS_FILE}")
        document = load_json(PROVIDERS_FILE)
        self.assertEqual(document.get("schema_version"), 1)

        providers = document.get("providers")
        self.assertIsInstance(providers, list)
        self.assertTrue(providers, "providers registry must not be empty")

        provider_ids: list[str] = []
        for index, provider in enumerate(providers):
            label = f"providers[{index}]"
            self.assertIsInstance(provider, dict, f"{label} must be an object")
            required = {
                "capabilities",
                "id",
                "kind",
                "match",
                "name",
                "packages",
                "support_level",
                "vendor_tools",
                "warnings",
            }
            self.assertFalse(required - provider.keys(), f"{label} is missing fields")

            provider_id = provider["id"]
            self.assertIsInstance(provider_id, str)
            self.assertRegex(provider_id, SLUG_RE)
            provider_ids.append(provider_id)

            self.assertIsInstance(provider["name"], str)
            self.assertEqual(provider["name"], provider["name"].strip())
            self.assertTrue(provider["name"])
            self.assertIn(provider["kind"], ALLOWED_PROVIDER_KINDS)
            self.assertIsInstance(provider["support_level"], str)
            self.assertRegex(provider["support_level"], SLUG_RE)

            match = provider["match"]
            self.assertIsInstance(match, dict, f"{label}.match must be an object")
            for field, values in match.items():
                self.assertIsInstance(field, str)
                assert_unique_strings(
                    self,
                    values,
                    label=f"{label}.match.{field}",
                    normalized_lowercase=True,
                )

            assert_unique_strings(
                self,
                provider["capabilities"],
                label=f"{label}.capabilities",
                pattern=CAPABILITY_RE,
                normalized_lowercase=True,
            )
            packages = assert_unique_strings(
                self,
                provider["packages"],
                label=f"{label}.packages",
                pattern=PACKAGE_RE,
                normalized_lowercase=True,
            )
            self.assertFalse(
                set(packages) - self.packages,
                f"{label} references packages absent from package manifests: "
                f"{sorted(set(packages) - self.packages)}",
            )
            assert_unique_strings(
                self,
                provider["vendor_tools"],
                label=f"{label}.vendor_tools",
                pattern=SLUG_RE,
                normalized_lowercase=True,
            )
            assert_unique_strings(self, provider["warnings"], label=f"{label}.warnings")

        self.assertEqual(
            len(provider_ids),
            len(set(provider_ids)),
            "provider registry contains duplicate IDs",
        )

    def test_command_capability_registry(self) -> None:
        self.assertTrue(
            COMMAND_CAPABILITIES_FILE.is_file(),
            f"missing {COMMAND_CAPABILITIES_FILE}",
        )
        document = load_json(COMMAND_CAPABILITIES_FILE)
        self.assertEqual(document.get("schema_version"), 1)

        execution_policy = document.get("execution_policy")
        self.assertIsInstance(execution_policy, dict)
        self.assertEqual(execution_policy.get("argument_policy"), "deny-unlisted")
        self.assertIs(execution_policy.get("public_api_accepts_argv"), False)
        self.assertIs(execution_policy.get("raw_passthrough_allowed"), False)
        self.assertIs(
            execution_policy.get("destructive_operations_require_lifecycle_plan"),
            True,
        )
        indicator_controls = execution_policy.get("indicator_controls")
        self.assertIsInstance(indicator_controls, dict)
        self.assertIs(indicator_controls.get("requires_identity_revalidation"), True)
        self.assertIs(indicator_controls.get("requires_audit_record"), True)
        self.assertGreater(indicator_controls.get("maximum_duration_seconds", 0), 0)

        package_groups = document.get("package_commands")
        self.assertIsInstance(package_groups, list)
        self.assertTrue(package_groups, "command registry must not be empty")

        package_names: list[str] = []
        command_names: list[str] = []
        actual_package_commands: dict[str, set[str]] = {}

        for package_index, package_group in enumerate(package_groups):
            label = f"package_commands[{package_index}]"
            self.assertIsInstance(package_group, dict, f"{label} must be an object")
            self.assertEqual(set(package_group), {"commands", "package"})

            package = package_group["package"]
            self.assertIsInstance(package, str)
            self.assertRegex(package, PACKAGE_RE)
            self.assertIn(package, self.packages)
            package_names.append(package)

            commands = package_group["commands"]
            self.assertIsInstance(commands, list)
            self.assertTrue(commands, f"{label}.commands must not be empty")
            actual_package_commands[package] = set()

            for command_index, command in enumerate(commands):
                command_label = f"{label}.commands[{command_index}]"
                self.assertIsInstance(command, dict, f"{command_label} must be an object")
                required = {"capabilities", "name", "output", "policy", "privilege"}
                self.assertEqual(set(command), required)

                name = command["name"]
                self.assertIsInstance(name, str)
                self.assertRegex(name, COMMAND_RE)
                command_names.append(name)
                actual_package_commands[package].add(name)

                assert_unique_strings(
                    self,
                    command["capabilities"],
                    label=f"{command_label}.capabilities",
                    pattern=CAPABILITY_RE,
                    normalized_lowercase=True,
                )
                self.assertIn(command["output"], ALLOWED_COMMAND_OUTPUTS)
                self.assertIn(command["policy"], ALLOWED_COMMAND_POLICIES)
                self.assertIn(command["privilege"], ALLOWED_COMMAND_PRIVILEGES)

        self.assertEqual(
            len(package_names),
            len(set(package_names)),
            "command registry contains duplicate package entries",
        )
        self.assertEqual(
            len(command_names),
            len(set(command_names)),
            "command registry contains duplicate command entries",
        )

        for package, expected_commands in EXPECTED_PACKAGE_COMMANDS.items():
            with self.subTest(package=package):
                self.assertIn(package, actual_package_commands)
                self.assertTrue(
                    expected_commands.issubset(actual_package_commands[package]),
                    f"{package} is missing expected commands: "
                    f"{sorted(expected_commands - actual_package_commands[package])}",
                )


if __name__ == "__main__":
    unittest.main()
