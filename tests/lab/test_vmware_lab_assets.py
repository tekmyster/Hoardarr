from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class VmwareLabAssetTests(unittest.TestCase):
    def test_lab_provisioner_is_virtual_only_and_bounded(self) -> None:
        script = (ROOT / "scripts" / "lab" / "provision-vmware-lab.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("Hoardarr-A", script)
        self.assertIn("Hoardarr-B", script)
        self.assertIn("StorageFormat Thin", script)
        self.assertIn("protected physical SSDs excluded", script)
        self.assertIn("At least 4 GiB", script)
        self.assertIn("At least 100 GiB", script)
        self.assertNotIn("-DeviceName", script)
        self.assertNotIn("RawPhysical", script)
        self.assertNotIn("Cisco", script)
        self.assertNotIn("10.81.60.100", script)
        self.assertNotIn("10.81.200.250", script)

    def test_lab_provisioner_requires_external_credentials(self) -> None:
        script = (ROOT / "scripts" / "lab" / "provision-vmware-lab.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("[Parameter(Mandatory)]", script)
        self.assertIn("$CredentialEnvFile", script)
        self.assertNotIn("ConvertTo-SecureString", script)


if __name__ == "__main__":
    unittest.main()
