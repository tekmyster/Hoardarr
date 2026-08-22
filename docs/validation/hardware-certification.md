# Hardware support validation

No matching physical storage hardware has been exercised during this repository
validation. Fixture coverage and architectural support must not be read as a
hardware certification.

| Family | Architecture | Detection/provider | Fixture tests | Isolated integration | Matching hardware/firmware | Utility / limitation |
|---|---|---|---|---|---|---|
| SATA/AHCI | Yes | Generic Linux detector | Yes | Pending | Not tested | `lsblk`, udev, SMART |
| SAS / generic HBA | Yes | Generic SCSI/SAS topology plus Linux SAS PHY counters | Yes | CI pending | Not tested | invalid DWORD/disparity counters and link metadata come from SAS transport sysfs |
| NVMe/VMD/U.2/U.3/M.2 | Yes | NVMe and PCI facts | Yes | Pending | Not tested | `nvme-cli`, sysfs |
| USB/UAS bridges | Yes | Generic detector with honest SMART limitations | Yes | Pending | Not tested | Bridge may hide identity/SMART |
| Fibre Channel | Yes | Generic FC topology fixtures | Yes | Pending | Not tested | `multipath-tools`, sysfs |
| FCoE | Yes | Capability-gated connectivity provider | Yes | Pending | Not tested | Requires CNA, DCB/PFC, switch fabric |
| iSCSI/multipath | Yes | Connectivity, health normalization, active path-group identity and durable failover transitions | Yes | CI pending | Not tested | `targetcli-fb`, `open-iscsi`, multipath; polling observations are not counted as failovers |
| LSI/Avago/Broadcom | Yes | Package/provider registry | Yes | Pending | Not tested | Vendor utility acquisition/licensing varies |
| Dell PERC/PowerEdge/VxRail-derived | Yes | Registry and fixtures | Yes | Pending | Not tested | `perccli`/`storcli` where licensed/available |
| HPE Smart Array | Yes | Registry/fixtures | Yes | Pending | Not tested | `ssacli` availability varies |
| Adaptec/Microchip | Yes | Registry/fixtures | Yes | Pending | Not tested | Vendor utility required for full telemetry |
| Areca | Yes | Registry, capability detection and bounded health parser | Yes | Pending | Not tested | Model-specific CLI required; no slot is invented |
| Intel VROC | Yes | PCI/VMD, generic NVMe and existing metadata capability detection | Yes | Pending | Not tested | Licensed RAID mutation remains platform-dependent |
| NetApp/Dell EMC/generic SES shelves | Yes | Bounded `sg_ses` JSON health, temperature, fan, PSU, voltage, LED, expander and path normalization | Yes | CI pending | Not tested | Exact slot maps remain model-specific and are reported only when exposed |
| Supermicro/45Drives/Oracle/Sun | Yes | DMI/controller profiles and fixtures | Yes | Pending | Not tested | Exact chassis layouts incomplete |
| Lenovo enclosures | Yes | Platform registry plus generic SES | Yes | Pending | Not tested | Exact model layout requires captured physical evidence |

Four Windows-visible Cisco SSD-240G V01 USB devices (serials `STP26501RJH`,
`STP26500SG9`, `STP26510Q4N`, and `STP26501RAW`) were inspected read-only. They
were not used because Linux root/boot/device ancestry and explicit disposable
status were not established. The repository-controlled four-loop mergerFS test
is separate software evidence and remains pending execution in Ubuntu CI.
