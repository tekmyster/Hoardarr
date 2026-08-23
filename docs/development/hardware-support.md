# Hardware support and enclosure mapping

Hoardarr uses the Linux kernel and published storage protocols as its hardware
compatibility layer. It does not carry private forks of controller drivers and
does not infer that a controller, disk, or shelf is safe merely because a
command returned successfully.

This document defines the initial hardware-discovery contract. It covers
inventory, health collection, physical location, and indicator control. Pool
creation, wiping, firmware updates, and controller reconfiguration are separate
lifecycle operations with stronger approvals.

## Support layers

Hardware support is intentionally layered:

1. **Kernel drivers** expose controllers and devices through standard Linux
   block, SCSI, SAS, ATA, NVMe, PCI, USB, and enclosure interfaces.
2. **Standards providers** use sysfs, udev, SCSI Enclosure Services (SES),
   Serial Management Protocol (SMP), SCSI VPD, SMART, NVMe logs, and Device
   Mapper Multipath. These are the default providers.
3. **Vendor providers** fill gaps where RAID firmware virtualizes drives, hides
   SES processors, or exposes controller-only health. They are adapters around
   a precisely versioned vendor interface, not replacements for canonical
   device identity.
4. **Hardware profiles** describe tested model and firmware quirks, physical
   bay drawings, and known-safe controls. A profile may improve presentation;
   it may not weaken identity checks.

The provider registry is in
[`packaging/hardware/providers.json`](../../packaging/hardware/providers.json).
The generic command contract is in
[`packaging/hardware/command-capabilities.json`](../../packaging/hardware/command-capabilities.json).

## Identity is not location

A physical device keeps one canonical identity while its paths and location may
change. Hoardarr therefore stores these separately:

- **Device identity:** NAA/WWID, SCSI VPD designator, ATA serial plus WWN, or
  NVMe NGUID/EUI-64 plus namespace identity.
- **Enclosure identity:** SES logical enclosure identifier, supplemented by a
  trustworthy enclosure serial or VPD designator.
- **Location:** enclosure identity, subenclosure, and reported device-slot
  number.
- **Path:** current block node, SCSI H:C:T:L, HBA PCI address and port, expander
  SAS address and phy, and target-port SAS address.

`/dev/sdX`, `/dev/sgN`, controller device IDs, and discovery order are never
identities. A shelf's user-settable display number is useful metadata, but is
not the enclosure key.

Every location assertion carries its source, last-confirmed time, and one
normalized confidence value:

- `high` (shown as **Confirmed**): a direct Linux enclosure-device association,
  independent SES/SMP sources agree, or an operator confirmed an identify-light
  check.
- `medium` (shown as **Inferred**): one authoritative standards/vendor source or
  a validated topology/HCTL relationship reports the association.
- `low` (shown as **Inferred**): a hardware profile or bounded heuristic supplies
  a candidate association that still needs confirmation.
- `unknown` (shown as **Not reported**): no trustworthy association exists.

The stored source remains visible so `medium` and `low` never become facts by
presentation alone. Empty enclosure slots keep their own slot-source evidence;
they do not imply a disk association.

Destructive workflows accept only a policy-approved confidence level and
re-resolve device identity immediately before execution.

## SAS PHY evidence and slow links

For the exact SAS PHY present in a disk's Linux transport path, Hoardarr reads
the kernel SAS transport attributes for minimum, negotiated, and maximum link
rates plus invalid DWORD, running-disparity, loss-of-sync, and PHY-reset
counters. Missing files remain `Not reported`; they are never converted to
zero. The same stable SAS-address/PHY identity is used by persistent telemetry
so historical counters and the live topology refer to the same physical link.

When the negotiated rate is below the reported path capability, the UI calls
out the rate difference without declaring a failure. A 3 Gb/s device on a
12 Gb/s-capable path can be operating exactly as designed. Diagnosis must also
consider the device-side link, expander link, and HBA uplink rather than assume
the drive is at fault.

The unprivileged bootstrap detector implements the read-only first layer of
this contract. Its `disks` records keep `id` and `identity` separate from the
volatile `kernel_path`, and include capacity, logical/physical sector sizes,
connection, partitions, and signatures recognized by udev. Signature status is
explicitly `partial` because the detector does not open media to search for
stale metadata. Lifetime power-on hours likewise remain `Not reported` unless a
later typed health collector obtains raw SMART/NVMe evidence. Every health
metric carries its source, capture time, transport, and confidence; translated
OS counters and attachment duration are retained only as distinct observations
and never promoted to lifetime values.

## Standards-first SAS shelf discovery

The discovery service follows this sequence:

1. Enumerate enclosure processors and disks through udev, sysfs, `lsscsi`, and
   `/dev/bsg` or `/dev/sg` pass-through nodes.
2. Read each enclosure's SES pages:

   - `0x01` Configuration for enclosure and subenclosure descriptors;
   - `0x02` Enclosure Status/Control for slots, cooling, power supplies,
     sensors, faults, and indicator state;
   - `0x05` Threshold for supported sensor thresholds;
   - `0x07` Element Descriptor for vendor labels;
   - `0x0a` Additional Element Status (AES) for device-slot numbers, SAS
     addresses, and phys.

3. Read disk identity and target-port designators from SCSI VPD and the kernel
   SAS transport.
4. Prefer the Linux enclosure-class device link when it is present, then
   correlate AES SAS addresses. Use `smp_discover --dsn` to cross-check the
   expander phy, attached SAS address, and device-slot number.
5. Collapse redundant enclosure-service endpoints by logical enclosure ID while
   retaining the health and route of every endpoint.
6. Publish the slot only with its provenance. A model profile may translate
   `slot 7` into a rack drawing, but the stored location remains the exact
   enclosure ID and slot.

Linux's `ses` and `enclosure` drivers provide a useful fast path, including
slot/device links and locate/fault attributes. The raw SES result remains the
source of truth because the kernel enclosure `slot` attribute falls back to an
element number when a real device-slot number is absent.

The initial `sg_ses` adapter uses JSON where the installed version supports it
and retains a raw diagnostic-page capture for troubleshooting. Human-readable
output is not a stable API. Captured `sg_ses --page=all -HHHH` fixtures allow
decoder regression tests without requiring the shelf in CI.

References:

- [Linux SCSI and SAS transport documentation](https://docs.kernel.org/driver-api/scsi.html)
- [Linux SES driver](https://github.com/torvalds/linux/blob/master/drivers/scsi/ses.c)
- [Linux enclosure class](https://github.com/torvalds/linux/blob/master/drivers/misc/enclosure.c)
- [sg_ses documentation](https://sg.danny.cz/sg/sg_ses.html)
- [smp_utils documentation](https://sg.danny.cz/sg/smp_utils.html)

## Multipath rules

Dual-ported SAS devices and externally presented LUNs may produce several SCSI
paths for one device. Hoardarr groups paths by an authoritative VPD `0x83` WWID
and retains each path as a separate observation.

Slot correlation is performed against the underlying SCSI paths, never against
`/dev/mapper/mpath*` alone. Each path can have a different target-port SAS
address or enclosure-service route. A logical device receives a physical
location only after the path observations agree or a provider explains the
difference.

Two rules follow from this:

- a missing path is a path-health event, not a removed-drive event; and
- two block nodes with the same authoritative WWID are not two inventory
  drives.

Discovery uses status-only `multipath` and `multipathd` queries. A separate
Advanced, immutable lifecycle operation may create or change a verified map
after matching authoritative logical identity, capacity, sector geometry, and
filesystem UUID. That operation preserves the Hoardarr storage ID and public
mount path; see [Storage controller and path redundancy](storage-redundancy.md).

## NetApp and other reused shelves

Direct-attached NetApp DS2246/DS4246-style shelves are treated as SAS/SES
enclosures. Their dual IOM paths are management endpoints for one logical
shelf. A tested profile supplies the documented bay layout and model-specific
quirks; the core association still comes from SES/AES, SMP, and disk identity.

The normalized topology keeps the PCI HBA and Linux SAS host as distinct
objects: PCI BDF → SAS host → port → PHY → expander → enclosure → bay → disk.
This prevents a `hostN` renumber from replacing the stable controller identity.
The sanitized mixed-HBA regression fixture contains SAS2308 and SAS3008
controllers, DS424IOM6 and DS224IOM6 shelves, SAS and SATA devices, 3/6/12 Gb/s
observations, PHY error counters, confirmed mappings, and an explicitly unknown
bay. It contains no real host name or complete physical serial number and is
software evidence only, not certification of those models.

The two-digit physical shelf ID is secondary because it is configurable and
can conflict. The SES logical ID or shelf UID is canonical. IOM A and IOM B are
kept as independent paths so a partial cabling, IOM, or expander failure remains
visible.

When storage remains behind ONTAP, a SAN array, or another NAS controller, the
host usually sees a virtual LUN rather than the internal drives. Hoardarr cannot
derive an internal bay from that LUN. A vendor API add-on must report the array,
shelf, and disk relationship, and the UI must label that location as reported
by the external platform.

## Oracle and Sun servers

Oracle and Sun server identity comes from DMI and selects the ILOM platform
provider. Hoardarr uses read-only Redfish when that ILOM generation exposes it
and IPMI/freeIPMI as the fallback for older systems. ILOM supplies chassis,
sensor, fan, power, event, and FRU evidence; it does not decide how disks are
managed.

The installed PCI controllers remain authoritative. An Oracle chassis with an
LSI/Broadcom HBA receives the matching HBA/SES provider, while native NVMe
devices use the Linux NVMe provider. Platform and controller results are
reported together without relabeling controller slots as Oracle bays. Exact
server/backplane profiles and an identify-light confirmation are required
before an ILOM/controller location becomes a confirmed front-panel bay.

## Supermicro and 45Drives chassis

Supermicro and 45Drives identify a platform family, not one storage topology.
`dmidecode` and `/sys/class/dmi/id` select the chassis, board, and candidate
physical-layout profile. They do **not** select StorCLI, SES, or another storage
tool. The installed controller's PCI and subsystem IDs, bound kernel driver,
firmware personality, and exposed protocol select the provider and commands.
Two servers with the same chassis model can therefore require different
storage providers.

For a Supermicro chassis with a SAS expander and enclosure processor, Hoardarr
uses the normal SES/AES and SMP mapping: enclosure logical ID, device-slot
number, expander phy, disk identifier, and locate-light readback. A direct-wired
or SGPIO backplane may expose no SES enclosure at all. In that case mapping is
HBA port/connector/phy to physical bay through an exact chassis/backplane wiring
profile, with `ledctl` or a controller provider used only when that combination
supports indicators.

Supermicro BMCs may provide Redfish and IPMI inventory, FRU, fan, power,
temperature, and chassis state; supported Redfish versions can also expose
PCIe or storage-backplane resources. These are corroborating platform sources,
not automatic proof of disk identity or bay. Hoardarr uses read-only Redfish or
IPMI collection by default and accepts a BMC drive location only when its
serial/WWID can be joined to the operating-system device. Availability and URI
coverage remain model-, license-, and BMC-firmware-specific. See Supermicro's
[Redfish overview](https://www.supermicro.com/en/solutions/management-software/redfish)
and [BMC/IPMI documentation](https://www.supermicro.com/support/manuals/?mlg=0).

Traditional Storinator systems use a direct one-to-one connection from each
drive lane to high-port-count HBAs, including direct-wired backplanes; such a
system may have neither a SAS expander nor a SES slot table. Hoardarr therefore
maps each disk through the actual HBA connector and path, then applies the exact
45Drives model/revision wiring profile. If a different 45Drives configuration
does expose an expander and SES, the standards provider takes precedence. The
architecture must be detected, not inferred from the 45Drives name. 45Drives
documents both its [direct-wired architecture](https://www.45drives.com/blog/Hardware/tech-tip-direct-wired-architecture-of-the-storinator/)
and [direct-wired backplane design](https://www.45drives.com/community/articles/Storinator-Re-Design---Direct-Wired-Backplanes/).

A physical-layout profile is keyed by exact chassis/backplane revision and
controller topology. It records the visible bay labels, HBA connector/phy or
SES-slot mapping, orientation, and supported indicator method. During profile
onboarding the wizard flashes one or more identify lights where possible and
asks the operator to confirm the labeled bays. A profile that cannot be
confirmed remains `profiled` or `uncertain`; it never silently becomes a
`confirmed` location.

Other Dell, HPE, Lenovo, EMC, and generic JBODs follow the same
standards path. Their profiles can specify:

- exact INQUIRY vendor/product/revision matches;
- safe `sg_ses --eiioe` handling for non-conforming AES indexes;
- front/rear and zero/one-based physical labels;
- duplicate enclosure-service endpoint behavior; and
- controls that passed physical identify-light and readback tests.

Profiles are compatibility evidence, not a claim that every firmware revision
of a product family behaves identically.

## Package-to-command map

The authoritative machine-readable map is
`command-capabilities.json`. The important appliance packages are:

| Package | Commands used | Purpose | Discovery policy |
| --- | --- | --- | --- |
| `pciutils` | `lspci` | PCI controller identity and bound driver | Read-only |
| `lsscsi` | `lsscsi` | SCSI endpoint and pass-through inventory | Read-only |
| `sg3-utils` | `sg_inq`, `sg_vpd`, `sg_map`, `sg_logs`, `sg_ses` | SCSI identity, health, SES inventory, and locate | Reads plus allowlisted locate only |
| `smp-utils` | `smp_discover`, `smp_discover_list` | SAS expander, phy, address, and slot correlation | Read-only |
| `multipath-tools` | `multipath`, `multipathd` | WWID maps and per-path health | Status-only |
| `ledmon` | `ledctl`, `ledmon` | SES/SMP, SGPIO, VMD, and NPEM indicators where supported | Indicator-only |
| `smartmontools` | `smartctl`, `smartd` | Drive identity, health, trends, and self-tests | Allowlisted reads/tests |
| `nvme-cli` | `nvme` | NVMe identity, logs, topology, and self-tests | Allowlisted reads/tests |
| `hdparm` | `hdparm` | ATA identity and capabilities | Allowlisted reads |
| `sdparm` | `sdparm` | SCSI identity and capabilities | Allowlisted reads |
| `mdadm` | `mdadm` | MD/VROC inventory and health | Status-only during discovery |
| `dmidecode` | `dmidecode` | Chassis and platform inventory | Read-only |

### SMART self-tests

Hoardarr probes `smartctl -j -c` with a bounded read-only command. The UI reports
short and extended self-tests as `Supported`, `Unsupported`, or `Not reported`;
`Not reported` is never converted to failure. When smartmontools supplies its
recommended polling duration, the review and Activity views show that
drive-reported estimate. Applied self-tests use argv-only `smartctl -t short` or
`smartctl -t long`, poll the device's own state, and persist start, progress,
expected finish, and final pass/skip evidence in the storage operation journal.
USB/RAID/controller paths that hide the self-test log are recorded as an honest
skip with guidance to use a capable direct path.
| `freeipmi-tools`, `ipmitool` | `ipmi-fru`, `ipmi-sensors`, `ipmitool` | Platform FRU and sensor corroboration | Read-only |
| `usbutils` | `lsusb` | USB bridge and physical-path inventory | Read-only |

`udevadm`, `lsblk`, and sysfs are base-system interfaces and are also used by
discovery. Hoardarr consumes structured output when available, pins adapter
behavior to tested tool versions, and keeps captured fixtures for every parser.

## Execution and API safety

The web application and public API request typed operations such as “read SES
health” or “identify this verified slot.” They never submit shell text, an
arbitrary argument vector, a raw SCSI CDB, or an NVMe admin command.

The privileged system service enforces these rules:

- command and argument allowlists are deny-by-default;
- all processes have deadlines, output limits, fixed locale, and a minimal
  environment;
- the selected provider, binary digest/version, command class, target identity,
  and result are audited;
- locate/fault indicators require a fresh identity-to-slot resolution, have a
  bounded duration, are read back where possible, and are cleared by a durable
  cleanup job;
- discovery adapters cannot format, sanitize, secure-erase, download firmware,
  power off slots, alter RAID personality, create arrays, or change multipath
  configuration;
- health tests that can affect workload or media are jobs with capability
  checks, throttling, and explicit scope; and
- every destructive lifecycle plan revalidates WWID/serial, current holders,
  mounts, pool membership, and the boot device immediately before execution.

Even a read-oriented utility such as `nvme`, `hdparm`, `sdparm`, `smartctl`,
`sg_ses`, `mdadm`, or a vendor CLI can expose destructive subcommands. Binary
allowlisting alone is insufficient; each allowed invocation has a typed
argument builder.

## Vendor utility staging and licensing

StorCLI/StorCLI2, PERCCLI/PERCCLI2, SSACLI, ARCCONF, Areca CLI, RACADM,
iLOrest, and legacy SAS flash/IR tools are not part of the generic Ubuntu
package installation. Public availability does not imply redistribution
permission.

The vendor-tool registry is the only authority from which the bootstrap may
install a proprietary binary. It separates artifacts that have a stable,
official public download from tools that still require a manual vendor flow.
The current milestone resolves and reports this plan but blocks all runtime
apply before mutation because storage-package autoactivation quarantine is not
yet complete. Once that fail-closed gate is satisfied, installation follows
this process:

1. Detect the controller with standard PCI/sysfs data and select a compatible
   provider without executing a vendor binary.
2. Display the required utility, tested versions, source, license owner, and
   why it is needed.
3. For an `official-public-fetch` entry, require explicit acceptance of that
   tool's vendor license, fetch its exact HTTPS URL, and verify the catalogued
   SHA-256. Public download access is not treated as permission to redistribute
   the payload in a Hoardarr image or USB installer.
4. Accept only a Debian package or one exact Debian-package member from a
   pinned tar/ZIP archive. Validate package name, version, architecture, and
   the simulated APT transaction before installation. Reject removals,
   downgrades, and unplanned dependency changes.
5. Let the signed/pinned vendor Debian package install to its declared system
   paths. Record its dpkg identity and artifact digest in a Hoardarr receipt;
   never manufacture a global wrapper or place an unverified binary in
   `PATH`.
6. Leave `official-public-manual`, login-gated, click-through-only, unsupported-
   OS, and unstable-download entries uninstalled and report the exact operator
   action. An explicit request to include vendor tools cannot silently count a
   skipped tool as installed.
7. Execute installed utilities only through typed, read-only provider commands
   during discovery and monitoring. Keep captured output fixtures per utility
   version because vendor JSON and text schemas change.

Firmware flashing utilities are maintenance tools, not discovery dependencies.
They remain unavailable to the public API unless a future, separately reviewed
firmware lifecycle is implemented.

## Current limitations

- A RAID controller can hide physical disks, SES processors, SMART passthrough,
  or expander topology. Software cannot reconstruct information that firmware
  does not expose; a vendor provider or HBA/JBOD personality is required.
- SES pages, device-slot numbers, and element descriptors are optional and are
  sometimes empty, duplicated, or incorrect. Unverified component indexes are
  shown as uncertain rather than renamed into convincing bay numbers.
- Some SES-2 implementations disagree about AES element indexing. Quirk modes
  are selected only by a tested model/firmware profile.
- Indicator patterns and even the meaning of fault versus locate vary across
  backplanes. Unsupported controls remain hidden.
- Fibre Channel and iSCSI generally identify an array LUN, not the physical
  drives behind it. Vendor APIs are needed for internal shelf mapping.
- USB bridges frequently suppress serials, SMART passthrough, sanitize support,
  and physical bay data. Duplicate or missing identifiers block unattended
  destructive operations.
- NVMe-MI enclosure support is not yet as consistently deployed as SAS SES.
  PCIe slot mapping may require NPEM, VMD, platform firmware, or a vendor
  provider.
- Vendor command output is versioned evidence, not a stable schema. An unknown
  version falls back to standards discovery or reports unsupported rather than
  guessing.
- The package and provider registries describe intended capability. A support
  claim is earned only after the exact controller, shelf, firmware, cabling,
  path-loss, hotplug, and indicator behavior pass the hardware test matrix.
