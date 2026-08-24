# Persistent Hoardarr A/B development lab

Hoardarr-A and Hoardarr-B are persistent VMware development appliances. They complement disposable
CI: CI proves reproducibility, while the lab exposes stable completed increments for owner review.
Both nodes run Ubuntu 24.04, Python 3.12, systemd and the same release-candidate artifact.

The lab provisioner accepts vCenter credentials only from an external key/value environment file.
It creates two 2-vCPU, 2-GiB VMs with thin 24-GiB OS VMDKs. After installation, the optional data
disk phase attaches four thin virtual disks to each node (12, 12, 12 and 8 GiB). It cannot map an
RDM or host block device. The four protected physical Cisco SSDs are outside this topology and must
remain unmodified unless the owner separately designates their exact identities disposable.

```powershell
./scripts/lab/provision-vmware-lab.ps1 `
  -CredentialEnvFile C:/secure/vcenter.env `
  -ApplianceIso C:/artifacts/hoardarr.iso
```

Data VMDKs are intentionally attached only after the operating system is installed, so the Ubuntu
installer cannot select a lab data disk as the appliance root. Add them while both VMs are powered
off:

```powershell
./scripts/lab/provision-vmware-lab.ps1 `
  -CredentialEnvFile C:/secure/vcenter.env `
  -AttachDataDisks
```

The script emits a JSON inventory suitable for attaching to validation evidence. It never prints
vCenter credentials. Lab setup credentials are chosen during the interactive Ubuntu installer and
must not be embedded in the repository or appliance artifact.
