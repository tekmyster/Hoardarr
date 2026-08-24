# Persistent Hoardarr A/B development lab

Hoardarr-A and Hoardarr-B are persistent VMware development appliances. They complement disposable
CI: CI proves reproducibility, while the lab exposes stable completed increments for owner review.
Both nodes run Ubuntu 24.04, Python 3.12, systemd and the same release-candidate artifact.

The lab provisioner accepts vCenter credentials only from an external key/value environment file.
It creates two 2-vCPU, 2-GiB VMs with thin 24-GiB OS VMDKs. After installation, the optional data
disk phase attaches four thin virtual disks to each node (12, 12, 12 and 8 GiB). It cannot map an
RDM or host block device. The four protected physical Cisco SSDs are outside this topology and must
remain unmodified unless the owner separately designates their exact identities disposable.
The provisioner enables VMware `disk.EnableUUID` while each VM is powered off so Linux and Hoardarr
receive stable VMDK WWNs. A lab disk without that stable identity is not eligible for managed
storage merely because it has a `/dev/sdX` name.

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
vCenter credentials. The lab-only appliance uses key-only SSH and creates no repository-embedded
web password. Web accounts are paired separately on each installed node; any local audit credential
must remain outside the repository. DHCP addresses are observations, not node identity—use the
VMware inventory to resolve the current address after a restart.

For the persistent virtual lab only, `.github/workflows/lab-appliance.yml` can render a separate
unattended ISO from `lab-user-data.template`. The workflow accepts a bounded OpenSSH public key,
locks password authentication, and retains no private key. Before booting this lab-only image,
the operator must verify each powered-off VM has exactly one 24-GiB virtual OS disk and no RDM or
host block device. Production appliance identity and storage selection remain interactive.
