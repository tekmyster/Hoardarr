# WO-OWNER-001 result — production storage/virtualization inventory

**Disposition:** read-only inventory completed for every storage endpoint that had both an exact canonical-vault credential entry and an existing pinned identity. The first SAS-SSD/NVMe ZFS-iSCSI tier is **blocked**: none of the accessible views identifies the intended 18 x 960-GB SSD set or 4–8 NVMe devices, the 48-HDD physical set remains controller-hidden/unlocated, no approved Proxmox access source is represented, and three independent data-bearing LINSTOR hosts are not evidenced. This handoff does not select RAIDZ geometry, authorize storage, or change a host.

All identifiers in this Git handoff are deterministic engagement-local pseudonyms. Hostnames, IPs, MACs, serials, WWNs, UUIDs, raw bay/path data, and the pseudonym map exist only in the ACL-restricted private evidence root.

## Access and collection boundary

| Pseudonym | Approved source | Pin/credential result | This collection |
|---|---|---|---|
| `SRC-PHYS-01` | canonical `All Servers / unraid / Other / unraid - Other - pass` password-entry class | exactly one entry; existing `known_hosts` key matched; authenticated | two successful SSH sessions: bounded inventory and a holders/ZFS supplement |
| `SRC-RAID-02` | canonical `All Servers / <SRC-RAID-02> / Other / <SRC-RAID-02 password entry>` class; raw exact path retained privately because it embeds an address | exactly one entry; existing `known_hosts` key matched; authenticated | three successful SSH sessions; the first authenticated session returned only “command line too long” for one combined Windows inventory command, then two bounded sessions collected inventory and protocol state. No authentication failure or retry loop occurred |
| `VM-SRC-01`, `VM-SRC-02` | accepted vCenter inventory in `WO-LAB-001` and supervisor state | accepted TLS-thumbprint/PowerCLI evidence reused | no vCenter collection repeated |
| `HV-OOB-01` | existing CIMC hardware-monitoring note and canonical CIMC entry | local accepted evidence reused; expired device certificate is documented | no CIMC login/API call made |
| Proxmox/PVE | canonical vault metadata search | no named Proxmox/PVE endpoint or credential entry found | inaccessible evidence gap; this is **not** proof that no Proxmox host exists |

No vault write, credential write, token action, TOFU, subnet scan, authentication retry, service action, SMART self-test, standby-media wake, pool/import/assemble/activate/mount operation, controller action, iSCSI login/logout, or package install occurred. Secret values and secret hashes were never emitted or persisted.

Exact read-only command classes and results:

- `SRC-PHYS-01`: platform/version, CPU/memory/DMI; `mdcmd status`, `/proc/mdstat`, `lsblk`, `findmnt`, `df`, `blkid`, `mdadm --detail --scan`, `pvs/vgs/lvs`, `btrfs filesystem show`, `zpool status/list`, `zfs list/get`; `/dev/disk` links; `lspci`, `lsscsi`, SCSI-host/enclosure sysfs; `ip` address/route, bonding, bridge and listening sockets; `iscsiadm`, `multipath`, FC-host sysfs; SMB-share status and Docker process status; `smartctl -n standby -a -j` for the sole rotational USB boot device and `smartctl -a -j` for non-rotational SAS devices; kernel holders/slaves. Inventory commands completed. The controller/protocol aggregate commands returned nonzero only because enclosure/FC paths and safe controller/iSCSI/multipath utilities were absent.
- `SRC-RAID-02`: CIM/WMI platform, disks, partitions and volumes; Storage Management physical disks, reliability counters, pools, virtual disks, subsystem and enclosure; SCSI controllers and signed drivers; SMB shares/sessions/open files; adapters/IP/default routes; iSCSI initiator ports, sessions, connections and targets; MPIO/iSCSI services; bounded search for vendor controller CLIs only in known install roots. Status reads completed. `mpclaim.exe` was absent; no StorCLI/PercCLI/MegaCLI/safe equivalent was found. No LSA control/API operation was attempted.

## Machine-evidenced inventory

### `SRC-PHYS-01` — actual Unraid source

- Unraid 7.1.4. Its MD source-of-truth reports historical `sbNumDisks=24`, `mdState=ERROR:TOO_MANY_MISSING_DISKS`, `mdNumDisks=0`, and `mdNumMissing=24`. There is no assembled MD array and no MD member currently usable as source data.
- The actual online data-bearing source is one five-leaf RAIDZ1 ZFS pool: all five leaves ONLINE with zero read/write/checksum errors and no known data errors. Raw pool size is **7,988,639,170,560 bytes (7.988639 TB / 7.265625 TiB)**; initial allocated/free were **2,233,590,145,024 bytes (2.233590 TB / 2.031438 TiB)** and **5,755,049,025,536 bytes (5.755049 TB / 5.234187 TiB)** across 13 filesystems/datasets.
- Seven non-boot, non-rotational SAS SSDs are visible, not eight: five at **1,600,321,314,816 bytes each** (**8,001,606,574,080 bytes total; 8.001607 TB / 7.277419 TiB**) and two at **800,166,076,416 bytes each** (**1,600,332,152,832 bytes total; 1.600332 TB / 1.455494 TiB**). Total visible SAS SSD capacity is **9,601,938,726,912 bytes (9.601939 TB / 8.732912 TiB)**. Five 1.6-TB-class devices are the ZFS leaves; the two 800-GB-class devices form a second, currently unmounted Btrfs filesystem with **1,475,617,460,224 bytes** reported used. A separate one-device Btrfs loop filesystem reports **16,647,122,944 bytes** used. No LVM PV/VG/LV or assembled Linux MD array exists.
- All seven SAS SSDs passed SMART. Temperatures were 32–37 C, power-on hours 3,069–49,686, percentage-used endurance indicators 1–3%, grown-defect counts zero, and read/write/verify uncorrected errors zero. No self-test was started. The USB boot device did not provide SMART data and was queried with the no-wake option.
- Two Broadcom/LSI HBAs are PCI-visible (SAS2308 and SAS3008, both `mpt3sas`). One SCSI host exposes all seven SAS block devices; no second path is exposed. No enclosure object, expander/bay map, or safe vendor controller utility is present. Across 29 block objects, sysfs exposed zero kernel holder and zero slave relationships; ZFS/Btrfs membership is instead evidenced by filesystem labels and ZFS topology.
- `iscsiadm` and `multipath` are absent; there are no FC-host objects. The network view has an active-backup bond with two slaves, but switch, uplink and power-domain independence are unknown. Eight containers were running. This is an active source, not spare destination media.

The accepted partial preflight recorded six 1.6-TB-class plus two 800-GB-class SAS SSDs. The fresh inventory records five plus two. The absent sixth 1.6-TB-class device is an unresolved current-state discrepancy; it is not treated as duplicate, standby, or available media.

### `SRC-RAID-02` — hardware-RAID source/logical view

- One physical Dell R320 running Windows Server 2019 exposes three logical disks: a **199,446,589,440-byte (0.199447 TB / 0.181396 TiB)** PERC H310 boot logical disk and two LSI MR9286CV-8e logical disks of **40,002,234,531,840** and **40,002,250,982,400 bytes**. Windows reports the logical disks and volumes Healthy/OK.
- The two data volumes are NTFS. At observation, one was **40,002,232,762,368 bytes**, with **19,933,047,406,592 bytes free** and **20,069,185,355,776 bytes used (20.069185 TB / 18.252818 TiB)**; the other was **40,002,115,272,704 bytes**, with **18,402,030,059,520 bytes free** and **21,600,085,213,184 bytes used (21.600085 TB / 19.645163 TiB)**. Seven SMB shares, two SMB sessions and nine open files were live. These are active sources.
- Windows exposes the PERC H310, AVAGO MegaRAID SAS adapter and Microsoft Storage Spaces controller, plus one Healthy/OK 24-slot enclosure object. It does **not** expose a join from either 40-TB logical disk to constituent members, bays, expanders, external enclosure, path redundancy, media type, member health or endurance. The 24-slot object does not prove that 24 or 48 HDDs back the arrays.
- Three logical-layer reliability-counter rows exist, but do not provide physical-member health/endurance and cannot qualify media. No safe installed controller CLI is available. The exact physical topology is therefore hidden and ineligible as ZFS/LINSTOR backing evidence.
- There are zero iSCSI initiator ports, sessions, connections and targets. The Microsoft iSCSI service is stopped/manual. No MPIO service/tool is present and no FC initiator port is exposed. Only one of four Ethernet adapters was Up; NIC teaming, switch redundancy and power-domain independence are unproven.

### Accepted VMware sources

`VM-SRC-01` and `VM-SRC-02` were not recollected. Accepted vCenter evidence says both are powered on, have zero snapshots/RDM, and each has one 24-GiB OS VMDK plus 12/12/12/8/6/6/6-GiB data VMDKs on the same VMware datastore. They are virtual source workloads sharing a datastore fault domain, not independent physical storage hosts. Accepted `HV-OOB-01` evidence dated 2026-07-09 reports one healthy virtual drive and dual operable PSUs, but supplies no physical-drive count/capacity/bay mapping; it cannot qualify destination media or prove datastore independence.

## Expected-media reconciliation

| Expectation | Observed and uniquely identified | Duplicate | Missing/unlocated | Ambiguous/inaccessible | Standby | Unexpected |
|---|---|---|---|---|---|---|
| 48 HDDs | 0 physical HDDs in approved accessible views | 0 provable | not safely classifiable as physically missing | all 48 remain unproven; some or all could be hidden behind `SRC-RAID-02`, but the two 40-TB logical arrays and one 24-slot enclosure do not prove count or membership | unknown; no HDD was addressed or woken | 0 directly evidenced |
| 18 x 960-GB SAS-connected SSDs | 0 matching 960-GB devices; nominal expectation is **17,280,000,000,000 bytes (17.280000 TB / 15.716068 TiB)** | 0 among the seven visible stable identities | all 18 are unlocated relative to the approved endpoints | controller-hidden or on an unidentified host remains possible | none of the visible devices was standby rotational media | seven other-capacity SAS SSDs on the active Unraid source; fresh count is one 1.6-TB-class device lower than accepted partial preflight |
| 4–8 NVMe | 0 in the approved accessible block views | 0 provable | 4–8 unlocated | another host/controller may hide them; approved endpoint coverage is incomplete | unknown | 0 directly evidenced |

The LSI logical arrays' `Unspecified` media type is deliberately not counted as HDD, SSD or NVMe. Raw stable IDs showed no duplicate among the seven visible Unraid SAS devices; physical-member deduplication behind hardware RAID is impossible with the available view.

## Source/destination independence and fault domains

| Source → proposed destination | Host independence | Controller/enclosure independence | Power independence | Network-path independence | Result |
|---|---|---|---|---|---|
| `SRC-PHYS-01` → intended SSD/NVMe tier | destination host unknown | destination controller/enclosure unknown; source's seven SAS devices share one exposed SCSI path | unknown | source has two-link active-backup; destination/switch domains unknown | **not proven** |
| `SRC-RAID-02` → intended SSD/NVMe tier | destination host unknown | source physical members/bays/paths hidden; destination unknown | unknown | source has one Up NIC; destination/switch domains unknown | **not proven** |
| `VM-SRC-01`/`VM-SRC-02` → Proxmox target | target unknown; the two VMs share one VMware datastore | source datastore backing and target storage backing unknown | source OOB shows dual PSUs only; target unknown | unknown | **not proven** |
| `SRC-PHYS-01` ↔ `SRC-RAID-02` | distinct physical hosts/endpoints evidenced | enclosure/backplane relationship unknown | unknown | VLAN/endpoints differ in private evidence, but switch/uplink independence unknown | host-level separation only; not sufficient for a safe source/destination claim |

There are **zero proven production data-bearing LINSTOR physical hosts** in the approved evidence. The two accepted VMware VMs are workloads, not independent physical data-bearing LINSTOR hosts, and there is no evidenced third host. A VM or diskless witness would not satisfy the requirement. Architecture plans requiring three data-bearing hosts are not implementation evidence.

## Eligibility and disposition gates

| Asset/view | Eligible now | Disposition |
|---|---|---|
| `SRC-PHYS-01` online ZFS pool and five leaves | source read only; **not destination media** | preserve online and unchanged until separately backed up, quiesced and migrated under an authorized work order |
| `SRC-PHYS-01` two-device Btrfs filesystem and its two SAS SSDs | potential source data only; **not free/eligible media** | ownership and recovery purpose unresolved; do not mount, clear or reuse |
| `SRC-PHYS-01` Unraid MD configuration | **not eligible** | zero current members/24 missing; retain as historical source metadata pending owner disposition |
| `SRC-RAID-02` logical arrays/volumes | source read only; **not ZFS/LINSTOR backing** | active SMB data; retain unchanged until physical topology, backup and migration gates close |
| `SRC-RAID-02` hidden members/24-slot enclosure view | **not eligible** | member identity, media, bay, path, health and independence are unproven |
| intended 18 SSD / 4–8 NVMe / 48 HDD sets | **not eligible** | unlocated or inaccessible; no device may be assigned, qualified or designed into a pool from this evidence |
| `VM-SRC-01` / `VM-SRC-02` | source workloads only | accepted VMware source remains unchanged; shared datastore prevents treating the VMs as independent storage hosts |
| Proxmox and three-host LINSTOR target | **not eligible/not proven** | require approved endpoints/pins and three real independent data-bearing hosts before any storage design or provisioning |

## Before/after no-mutation evidence

| Host | Gate | Result |
|---|---|---|
| `SRC-PHYS-01` | MD state and `lsblk` topology | byte-identical before/after |
| `SRC-PHYS-01` | mounts, ZFS topology/health, container identity/state | semantically identical; live workload changed counters only. ZFS allocation rose from 2,233,590,145,024 to 2,234,276,880,384 bytes while topology stayed ONLINE/error-free |
| `SRC-PHYS-01` | supplemental MD/ZFS/`lsblk` state around holders/properties reads | byte-identical before/after |
| `SRC-RAID-02` | disk/volume/pool/virtual-disk/service/share topology | semantically identical after excluding live free-space and user-count counters |
| `SRC-RAID-02` | iSCSI/service state around protocol reads | byte-identical before/after |

Every remote command was inventory/status only. The private collection ledger records five successful authenticated sessions, zero authentication failures, zero authentication retries and zero mutating remote commands.

## Exact blockers

- **Missing information:** physical location/owner of the 18 x 960-GB SSDs and 4–8 NVMe; physical identity and ownership of the intended 48 HDDs; destination Proxmox hosts; source/destination enclosure, power and switch domains; whether the currently absent sixth 1.6-TB-class Unraid SSD was intentionally removed; complete current data ownership.
- **Credential/pin coverage:** no canonical Proxmox/PVE entry and no approved named/pinned endpoint for the intended SSD/NVMe host. Absence from the vault is an access/evidence gap only.
- **Tooling:** `SRC-RAID-02` has no safe available StorCLI/PercCLI/MegaCLI-equivalent, so hardware-RAID members remain hidden. `SRC-PHYS-01` exposes no enclosure/bay mapping or safe controller utility. No utility was installed or improvised.
- **Actual service state/misconfiguration:** Unraid MD is unusable with zero current disks and 24 missing; its ZFS source is independently ONLINE. `SRC-RAID-02` logical arrays are Healthy/OK and active; no logical-array misconfiguration is evidenced. No current iSCSI/FC/multipath configuration exists on either accessible source.
- **Safety/rollback gate:** both physical sources are actively data-bearing and serving workloads. No pool, format, mount, controller, migration or service mutation can be authorized until exact media ownership, destination independence, controller-level topology, backups and per-mutation rollback evidence exist.

**Single next evidence action:** the owner/supervisor must identify the exact canonical-vault entry and pre-pinned management identity for the physical host/controller that contains the intended 18 x 960-GB SAS SSDs and 4–8 NVMe devices. A separately authorized read-only collection of that one endpoint can then prove media, bays/paths, health/endurance and whether it is independent of these active sources. Do not provision, design RAIDZ, or infer the endpoint meanwhile.

## Evidence and integrity

Private root (outside Git): `C:\Users\dmessana\Desktop\all servers\Hoardarr-owner-evidence\WO-OWNER-001\WO-OWNER-001-20260826T190410Z`

- ACL inheritance disabled; exactly three FullControl rules: owner/Administrators, SYSTEM and the collecting account. Raw host/media identities, command output and pseudonym mapping exist only there.
- `manifest.sha256`: 1,184 bytes, 13 file entries, SHA-256 `677F4CD7066E3C4D38D97E5637D03732E66D88C8C990805038746D2394F1BDE0`; all 13 entries independently reopened and verified with zero failures.
- Disposition: retain the private root ACL-restricted for Supervisor QA; do not add it to Git or distribute it.

Key secret-free local sources:

| Evidence | Bytes | SHA-256 |
|---|---:|---|
| `docs/planning/work-orders/WO-OWNER-001.md` | 6,606 | `B959E51CD0DD57635019665CB751E7D9A2B3C555EA53A72F75D89A853233C68C` |
| `docs/planning/unified-product-roadmap.md` (OWNER-01 row) | 156,986 | `BD13A06037F9D12960F2419FDF8FE663F38245D1ACB71E96FBA7F5B4DAC700F2` |
| `docs/planning/supervisor-state.md` (OWNER-01 partial preflight and accepted vCenter state) | 37,626 | `450F3058D3083FBBDA2B47C97B4D7225EC7E5C19D66597C8070EF4D118241690` |
| `docs/planning/handoffs/WO-LAB-001-result.md` (accepted VMware sources) | 102,018 | `C07015796D69CDBA633D27E55EC63DC0F163AAD9D3B9AA93E76E838F52F551E7` |
| `docs/planning/handoffs/WO-LAB-002-result.md` (vault reconciliation) | 10,221 | `8F412D3083A5290860A2BB2562F9F934EEE0009BF8BC95D79681944DF893B30F` |
| `<HV-OOB-01 local note>/CIMC-Hardware-Monitoring-20260709.md` (existing OOB source only; raw path is in private `local-source-checksums.json`) | 3,221 | `C76438C110C90E27D61CED52E7F62F94408BF8B7292CF9959D01D1A8439B64D5` |

The canonical KeePass database was opened read-only and is intentionally not hashed because secret hashes are excluded. No secret-bearing local source is copied into the private root or Git.
