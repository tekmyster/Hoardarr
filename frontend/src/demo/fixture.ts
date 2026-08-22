import type {
  Drive,
  HardwareSnapshot,
  NetworkInterface,
  OnboardingDefaults,
  PlanDocument,
  SetupStatus,
  WizardDocument,
  WizardMode,
} from "../types";
import { sha256Hex } from "../sha256";

export const demoSetupStatus: SetupStatus = {
  configured: false,
  claim_available: true,
};

export const demoOnboarding: OnboardingDefaults = {
  version: 1,
  steps: ["administrator", "server", "network_discovery", "network_redundancy", "network_addressing", "time", "network_test", "review", "storage_discovery"],
  defaults: {
    experience: "guided",
    server: { hostname: "hoardarr", timezone: "UTC", dst_mode: "automatic" },
    network: {
      mode: "single",
      interface_ids: [],
      addressing: "dhcp",
      addresses: [],
      gateway: null,
      dns_servers: [],
      vlan_id: null,
      mtu: 1500,
      bridge: { enabled: false, stp: true, prefer_rstp: true },
    },
    ntp: { servers: ["pool.ntp.org"] },
    discovery: {
      lldp: { enabled: true, mode: "rx_tx" },
      cdp: { receive: true, smart_transmit: true },
    },
  },
  apply_available: false,
};

export const demoInterfaces: NetworkInterface[] = [
  {
    id: "pci-0000:18:00.0",
    name: "enp24s0f0",
    mac: "3c:fd:fe:20:71:10",
    speed_mbps: 40000,
    link: "up",
    driver: "i40e",
    model: "Intel Ethernet Controller XL710",
    warnings: ["Intel X710 firmware may own LLDP for DCB/FCoE; ownership must be checked before host LLDP transmission."],
  },
  {
    id: "pci-0000:18:00.1",
    name: "enp24s0f1",
    mac: "3c:fd:fe:20:71:11",
    speed_mbps: 40000,
    link: "down",
    driver: "i40e",
    model: "Intel Ethernet Controller XL710",
    warnings: ["Intel X710 firmware may own LLDP for DCB/FCoE; ownership must be checked before host LLDP transmission."],
  },
];

export const demoDrive: Drive = {
  id: "usb-STP26501RAW",
  path: "/dev/sdb",
  vendor: "CISCO",
  model: "SSD-240G V01",
  serial: "STP26501RAW",
  wwn: null,
  capacityBytes: 240_057_409_536,
  stableIdentity: true,
  readOnly: false,
  selectable: true,
  selectionBlockers: [],
  connection: {
    bus: "USB",
    transport: "USB/UAS → Hyper-V SCSI",
    bridge: "USB Attached SCSI bridge",
  },
  sector: { logical: 512, physical: 4096 },
  signatures: [],
  partitions: [],
  signatureScan: {
    status: "partial",
    source: "udev",
    reason: "udev reports recognized active signatures only; a privileged read-only on-media scan has not run.",
  },
  location: "USB / Hyper-V SCSI target 1",
  removable: true,
  healthStatus: "unknown",
  metrics: [
    {
      name: "power_on_hours",
      label: "Power-on hours",
      value: null,
      available: false,
      provenance: {
        source: "OS translated counter",
        capturedAt: "2026-08-17T13:20:00-04:00",
        transport: "USB/UAS → Hyper-V SCSI",
        confidence: "unreliable",
        detail: "The translated value of 8 hours conflicts with the observed attachment interval of at least 16h 37m. Raw SMART is unavailable through this path.",
      },
    },
    {
      name: "temperature",
      label: "Temperature",
      value: null,
      unit: "°C",
      available: false,
      provenance: {
        source: "SMART",
        capturedAt: "2026-08-17T13:20:00-04:00",
        transport: "USB/UAS → Hyper-V SCSI",
        confidence: "low",
        detail: "The USB bridge did not expose this SMART attribute.",
      },
    },
  ],
  observations: [
    {
      name: "translated_power_on_hours",
      label: "Translated host counter",
      value: 8,
      unit: "hours",
      qualifiesAsLifetime: false,
      reason: "Translated host counter was not corroborated by raw SMART data.",
      provenance: {
        source: "windows-storage-reliability-counter",
        capturedAt: "2026-08-17T13:20:00-04:00",
        transport: "USB/UAS → Hyper-V SCSI",
        confidence: "low",
      },
    },
    {
      name: "attachment_duration",
      label: "Device attachment duration",
      value: 59820,
      unit: "seconds",
      qualifiesAsLifetime: false,
      reason: "Attachment duration is not drive lifetime power-on hours.",
      provenance: {
        source: "os-device-attachment-duration",
        capturedAt: "2026-08-17T13:20:00-04:00",
        transport: "USB/UAS → Hyper-V SCSI",
        confidence: "high",
      },
    },
  ],
  tests: [
    {
      id: "identity",
      label: "Identity and structure",
      status: "not-run",
      summary: "Planned for intake; no test runner has executed this check.",
    },
    {
      id: "surface-read",
      label: "Full surface read",
      status: "not-run",
      summary: "Planned for intake; no full-surface read has run.",
    },
    {
      id: "smart-self-test",
      label: "SMART self-test",
      status: "not-run",
      summary: "Not selected and not run. Support will be checked before execution.",
    },
  ],
};

export const demoSnapshot: HardwareSnapshot = {
  id: "019-demo-snapshot",
  captured_at: "2026-08-17T13:20:00-04:00",
  sha256: "63ad1e1103e59596fb367d98f0dc5acc2a41848fe6238e5e9442d88433783101",
  hardware: { drives: [demoDrive] },
};

export function demoWizard(mode: WizardMode): WizardDocument {
  return {
    id: "019-demo-wizard",
    revision: 0,
    mode,
    status: "draft",
    current_step: "discovery",
    hardware_snapshot_id: demoSnapshot.id,
    answers: {},
    plan_id: null,
    created_at: "2026-08-17T13:20:00-04:00",
    updated_at: "2026-08-17T13:20:00-04:00",
  };
}

function objectValue(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stringValues(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function demoFormat(portableSystems: string[]): Record<string, unknown> {
  if (portableSystems.includes("windows")) return { filesystem: "ntfs", partition_table: "gpt", alignment_bytes: 1_048_576, allocation_unit_bytes: 4096, linux_driver: "ntfs3", mount_options: ["windows_names", "noatime"], trim: { mode: "conditional" } };
  if (portableSystems.includes("macos")) return { filesystem: "exfat", partition_table: "gpt", alignment_bytes: 1_048_576, allocation_unit_bytes: 131_072, linux_driver: "exfat", mount_options: ["noatime"], trim: { mode: "conditional" } };
  return { filesystem: "ext4", partition_table: "gpt", alignment_bytes: 1_048_576, allocation_unit_bytes: 4096, linux_driver: "ext4", mount_options: ["noatime"], trim: { mode: "conditional" } };
}

function demoAdvancedFormat(defaults: Record<string, unknown>, rawOptions: unknown): Record<string, unknown> {
  const options = objectValue(rawOptions);
  if (!Object.keys(options).length) return defaults;
  const filesystem = typeof options.filesystem === "string" ? options.filesystem : String(defaults.filesystem);
  const noatime = options.noatime !== false;
  return {
    ...defaults,
    filesystem,
    partition_table: typeof options.partition_table === "string" ? options.partition_table : defaults.partition_table,
    alignment_bytes: typeof options.alignment_bytes === "number" ? options.alignment_bytes : defaults.alignment_bytes,
    allocation_unit_bytes: typeof options.allocation_unit_bytes === "number" ? options.allocation_unit_bytes : defaults.allocation_unit_bytes,
    linux_driver: filesystem === "ntfs" ? "ntfs3" : filesystem,
    mount_options: [...(filesystem === "ntfs" ? ["windows_names"] : []), ...(noatime ? ["noatime"] : [])],
    trim: { mode: typeof options.trim_mode === "string" ? options.trim_mode : "conditional" },
    reason: "Advanced disk format settings were selected",
  };
}

function documentSha256(document: Record<string, unknown>): string {
  const bytes = new TextEncoder().encode(JSON.stringify(document));
  return sha256Hex(bytes);
}

export async function demoPlan(wizard: WizardDocument): Promise<PlanDocument> {
  const storage = objectValue(wizard.answers.storage);
  const layout = objectValue(wizard.answers.layout);
  const selectedIds = stringValues(storage.selected_device_ids);
  const deviceIds = selectedIds.length ? selectedIds : [demoDrive.id];
  const topology = typeof storage.topology === "string" ? storage.topology : "individual";
  const preserveData = storage.preserve_data === true;
  const portableSystems = stringValues(storage.portable_systems);
  const format = demoAdvancedFormat(
    demoFormat(portableSystems.length ? portableSystems : ["windows"]),
    storage.format_options,
  );
  const intakeTests = objectValue(storage.intake_tests);
  const testKinds = [
    ["identity", "drive.identity.verify", false],
    ["full_surface_read", "drive.surface.read", false],
    ["smart_short", "drive.smart.short", false],
    ["smart_extended", "drive.smart.extended", false],
    ["destructive_write_read", "drive.write_read.destructive", true],
  ] as const;
  const actions: Array<Record<string, unknown>> = [];
  deviceIds.forEach((deviceId) => {
    testKinds.forEach(([answer, type, destructive]) => {
      const defaultEnabled = answer === "identity" || answer === "full_surface_read";
      if (intakeTests[answer] === true || (intakeTests[answer] === undefined && defaultEnabled)) {
        actions.push({ action_id: `test:${answer}:${deviceId}`, type, device_id: deviceId, destructive });
      }
    });
    if (!preserveData) {
      actions.push({ action_id: `partition:${deviceId}`, type: "disk.partition_table.create", device_id: deviceId, destructive: true });
      actions.push({ action_id: `filesystem:${deviceId}`, type: "filesystem.create", device_id: deviceId, destructive: true });
    }
  });
  const layoutDestructive = ["cache", "zfs", "raid", "snapraid"].includes(topology);
  actions.push({ action_id: "storage-layout", type: "storage.layout.ensure", device_ids: deviceIds, topology, destructive: layoutDestructive });

  const builtInLibraries = stringValues(storage.libraries);
  const customLibraries = Array.isArray(storage.custom_libraries) ? storage.custom_libraries.map(objectValue) : [];
  const mediaPath = typeof layout.media_path === "string" ? layout.media_path : "/data/media";
  const downloadsPath = typeof layout.downloads_path === "string" ? layout.downloads_path : "/data/downloads";
  const folders = [
    ...builtInLibraries.map((name) => `${mediaPath}/${name}`),
    ...customLibraries.flatMap((library) => typeof library.name === "string" ? [`${mediaPath}/${library.name}`] : []),
  ];
  const downloads = objectValue(storage.downloads);
  const torrents = downloads.torrents !== false;
  const usenet = downloads.usenet !== false;
  if (torrents) folders.push(`${downloadsPath}/torrents/incomplete`, `${downloadsPath}/torrents/complete`);
  if (usenet) folders.push(`${downloadsPath}/usenet/incomplete`, `${downloadsPath}/usenet/complete`);

  const destructive = actions.some((action) => action.destructive === true);
  const riskMessages = [
    ...(!preserveData ? ["The listed drives will be repartitioned and formatted. Existing data will be lost."] : []),
    ...(intakeTests.destructive_write_read === true ? ["The destructive write/read test will overwrite data on the listed drives."] : []),
    ...(layoutDestructive ? [`Creating the ${topology} layout can overwrite storage metadata or data on the listed drives.`] : []),
  ];
  const document: PlanDocument["document"] = {
    apply_available: false,
    storage: {
      topology,
      format,
      portable_systems: portableSystems,
      preserve_data: preserveData,
      libraries: [
        ...builtInLibraries.map((name) => ({ name, path: `${mediaPath}/${name}` })),
        ...customLibraries.map((library) => ({ ...library, path: `${mediaPath}/${String(library.name ?? "Unnamed")}` })),
      ],
      downloads: { torrents: { enabled: torrents }, usenet: { enabled: usenet }, hardlinks: "same_filesystem_only" },
      risk: {
        destructive,
        approval_required: destructive,
        heading: destructive ? "ARE YOU SURE?" : null,
        message: riskMessages.join(" ") || "No destructive disk action is planned.",
        required_phrase: destructive ? "I AGREE" : null,
      },
      actions,
      folders,
      warnings: [],
    },
    blockers: [{ code: "privileged_executor_not_implemented", message: "The preview is safe to review; storage apply remains blocked on this build." }],
    summary: { selected_drives: deviceIds.length, filesystems_to_create: preserveData ? 0 : deviceIds.length, shares_to_create: 1 },
  };
  return {
    id: "019-demo-plan",
    revision: wizard.revision,
    sha256: documentSha256(document),
    document,
  };
}
