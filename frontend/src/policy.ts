import type { Drive, StorageRole, WizardMode } from "./types";

export interface LayoutChoice {
  id: StorageRole;
  label: string;
  description: string;
  recommended?: boolean;
  warning?: string;
  requiresAdvanced?: boolean;
}

export interface ExistingDataSummary {
  headline: string;
  detail?: string;
  uncertain: boolean;
}

const BASE_CHOICES: LayoutChoice[] = [
  {
    id: "individual",
    label: "Use as one drive",
    description: "Keep this drive independent and expose its folders directly.",
    recommended: true,
  },
  {
    id: "mergerfs",
    label: "Add to combined storage",
    description: "Present this drive with other independent drives under one combined path.",
  },
  {
    id: "download-cache",
    label: "Use for downloads and temporary files",
    description: "Keep active torrent and Usenet work away from the media disks.",
    warning: "A disconnected USB cache interrupts active downloads and imports.",
  },
  {
    id: "block",
    label: "Provide block storage",
    description: "Expose capacity to another host as a managed block device.",
    warning: "USB disconnects can abruptly interrupt a remote filesystem or virtual machine.",
  },
];

const ADVANCED_USB_CHOICES: LayoutChoice[] = [
  {
    id: "zfs",
    label: "Add to a ZFS vdev",
    description: "Override the guided USB safety policy and use this drive in ZFS.",
    warning: "USB bridges can hide identity and health data or reset under load. A disconnect can fault the vdev.",
    requiresAdvanced: true,
  },
  {
    id: "raid",
    label: "Add to a Linux RAID set",
    description: "Override the guided USB safety policy and use this drive in a Linux RAID array.",
    warning: "USB resets or identity changes can remove a member unexpectedly and complicate recovery.",
    requiresAdvanced: true,
  },
  {
    id: "snapraid",
    label: "Add to SnapRAID",
    description: "Override the guided USB safety policy and add this drive to a SnapRAID set.",
    warning: "USB identity and availability are less predictable than direct-attached storage.",
    requiresAdvanced: true,
  },
  {
    id: "mixed",
    label: "Combine multiple protected pools",
    description: "Build separate ZFS or Linux RAID pools and present them through one mergerFS path.",
    warning: "Every component pool has its own protection and failure boundary. USB disconnects can remove an entire component.",
    requiresAdvanced: true,
  },
];

const GUIDED_ZFS_CHOICE: LayoutChoice = {
  id: "zfs",
  label: "Protected storage",
  description: "Combine the selected drives and choose how many can fail without losing the storage.",
};

export function layoutChoicesForDrive(drive: Drive | undefined, mode: WizardMode, preserveData = false, driveCount = 1): LayoutChoice[] {
  const base = preserveData
    ? [
        {
          id: "import" as const,
          label: "Import this drive",
          description: "Keep the existing filesystem and make its files available without formatting.",
          recommended: true,
        },
        ...BASE_CHOICES.map((choice) => ({ ...choice, recommended: false })),
      ]
    : BASE_CHOICES;
  if (mode === "guided") {
    const usb = Boolean(drive) && (drive!.connection.bus.toLowerCase() === "usb" || drive!.connection.transport.toLowerCase().includes("usb"));
    return !usb && driveCount >= 2 ? [...base, GUIDED_ZFS_CHOICE] : base;
  }
  if (!drive) return [...base, ...ADVANCED_USB_CHOICES];
  const usb = drive.connection.bus.toLowerCase() === "usb" || drive.connection.transport.toLowerCase().includes("usb");
  if (usb) return [...base, ...ADVANCED_USB_CHOICES];
  return [...base, ...ADVANCED_USB_CHOICES.map((choice) => ({ ...choice, warning: undefined }))];
}

export function isUsbRaidOverride(drive: Drive | undefined, role: StorageRole): boolean {
  if (!drive) return false;
  const usb = drive.connection.bus.toLowerCase() === "usb" || drive.connection.transport.toLowerCase().includes("usb");
  return usb && (role === "zfs" || role === "raid" || role === "snapraid" || role === "mixed");
}

export function exactConsentAccepted(value: string): boolean {
  return value === "I AGREE";
}

export function selectPortableSystem(values: string[], system: "windows" | "macos" | "linux"): string[] {
  if (system === "linux") return ["linux"];
  const portable = values.filter((value) => value !== "linux");
  return portable.includes(system) ? portable.filter((value) => value !== system) : [...portable, system];
}

export function detectedFilesystems(drives: Drive[]): string[] {
  const detected = new Set<string>();
  for (const drive of drives) {
    for (const partition of drive.partitions) {
      if (partition.filesystem) detected.add(partition.filesystem.toLowerCase());
    }
    for (const signature of drive.signatures) {
      const normalized = signature.toLowerCase();
      if (["ntfs", "ntfs3", "ext4", "xfs", "btrfs", "exfat"].includes(normalized)) detected.add(normalized);
    }
  }
  return [...detected].sort();
}

export function isImportedNtfs(preserveData: boolean, drives: Drive[]): boolean {
  if (!preserveData) return false;
  return detectedFilesystems(drives).some((filesystem) => filesystem === "ntfs" || filesystem === "ntfs3");
}

export function toggleNetworkInterfaceSelection(values: string[], interfaceId: string, singleSelection: boolean): string[] {
  if (singleSelection) return values.includes(interfaceId) ? [] : [interfaceId];
  return values.includes(interfaceId) ? values.filter((value) => value !== interfaceId) : [...values, interfaceId];
}

export function actionDestructiveLabel(action: Record<string, unknown>, overallRiskDestructive: boolean): string {
  if (action.destructive === true) return "Yes";
  if (action.destructive === false) return "No";
  const kind = typeof action.type === "string" ? action.type.toLowerCase() : "";
  if (/wipe|erase|format|partition|filesystem\.create|secure_?delete|destroy/.test(kind)) {
    return "Yes (inferred from action)";
  }
  if (overallRiskDestructive) return "Not declared — treat as destructive";
  return "Not declared";
}

export interface SectorGeometryAssessment {
  writeCompatible: boolean;
  kind: "usable" | "unknown" | "nonstandard" | "incompatible";
  message: string;
}

function isPowerOfTwo(value: number): boolean {
  return Number.isSafeInteger(value) && value > 0 && Number.isInteger(Math.log2(value));
}

export function sectorGeometryAssessment(drive: Drive): SectorGeometryAssessment {
  const { logical, physical } = drive.sector;
  if (logical === null || physical === null) {
    return {
      writeCompatible: false,
      kind: "unknown",
      message: "Both logical and physical sector sizes must be reported before a geometry-dependent write can be planned.",
    };
  }
  if (logical !== 512 && logical !== 4096) {
    return {
      writeCompatible: false,
      kind: "nonstandard",
      message: "Nonstandard sector format; dedicated low-level reformat required and not implemented.",
    };
  }
  if (physical < logical || !isPowerOfTwo(physical) || physical % logical !== 0) {
    return {
      writeCompatible: false,
      kind: "incompatible",
      message: "Physical sector size must be a power of two, at least as large as the logical size, and an exact multiple of it.",
    };
  }
  return {
    writeCompatible: true,
    kind: "usable",
    message: "Sector geometry is compatible with write planning.",
  };
}

export function hasKnownSectorGeometry(drive: Drive): boolean {
  return sectorGeometryAssessment(drive).writeCompatible;
}

export function storageChoiceNeedsSectorGeometry(input: {
  preserveData: boolean;
  topology: StorageRole;
  encryption: string;
}): boolean {
  if (!input.preserveData) return true;
  if (input.encryption !== "none") return true;
  return input.topology === "download-cache"
    || input.topology === "zfs"
    || input.topology === "raid"
    || input.topology === "snapraid"
    || input.topology === "mixed";
}

export function existingDataSummary(drive: Drive): ExistingDataSummary {
  const partitionNames = drive.partitions.map((partition) => partition.path ?? partition.kernelName).filter(Boolean);
  const detected = [
    ...(drive.signatures.length ? [`signatures: ${drive.signatures.join(", ")}`] : []),
    ...(partitionNames.length ? [`partitions: ${partitionNames.join(", ")}`] : []),
  ];
  if (detected.length) {
    return {
      headline: `${drive.partitions.length} partition${drive.partitions.length === 1 ? "" : "s"}; ${drive.signatures.length} recognized signature${drive.signatures.length === 1 ? "" : "s"}`,
      detail: `${detected.join("; ")}.${drive.signatureScan.reason ? ` ${drive.signatureScan.reason}` : ""}`,
      uncertain: drive.signatureScan.status !== "complete",
    };
  }
  if (drive.signatureScan.status === "complete") {
    return {
      headline: "No partitions or signatures found by the complete scan",
      detail: drive.signatureScan.reason ?? undefined,
      uncertain: false,
    };
  }
  if (drive.signatureScan.status === "partial") {
    return {
      headline: "No recognized signatures; scan is incomplete",
      detail: drive.signatureScan.reason ?? "Only partial signature evidence is available.",
      uncertain: true,
    };
  }
  return {
    headline: "Existing data unknown; signature scan unavailable",
    detail: drive.signatureScan.reason ?? "No reliable signature scan result is available.",
    uncertain: true,
  };
}

export function humanCapacity(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "Not reported";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let size = bytes;
  let index = 0;
  while (size >= 1000 && index < units.length - 1) {
    size /= 1000;
    index += 1;
  }
  return `${size.toLocaleString(undefined, { maximumFractionDigits: index > 2 ? 2 : 1 })} ${units[index]}`;
}

export function filesystemRecommendation(portability: string[], purpose: string): {
  filesystem: string;
  partitionTable: "gpt";
  alignmentBytes: number;
  allocationUnitBytes: number;
  noatime: boolean;
  trimMode: "conditional";
  settings: string[];
  reason: string;
} {
  if (portability.includes("windows")) {
    return {
      filesystem: "NTFS",
      partitionTable: "gpt",
      alignmentBytes: 1_048_576,
      allocationUnitBytes: 4096,
      noatime: true,
      trimMode: "conditional",
      settings: [
        "GPT partition table",
        "1 MiB partition alignment",
        "4 KiB allocation unit",
        "Linux ntfs3 driver",
        "Windows-safe file names",
        "noatime mount option",
        "TRIM only when the USB bridge reports it safely",
      ],
      reason: `Selected because the ${purpose || "storage"} drive may be connected directly to Windows.`,
    };
  }
  if (portability.includes("macos")) {
    return {
      filesystem: "exFAT",
      partitionTable: "gpt",
      alignmentBytes: 1_048_576,
      allocationUnitBytes: 131_072,
      noatime: true,
      trimMode: "conditional",
      settings: [
        "GPT partition table",
        "1 MiB partition alignment",
        "128 KiB allocation unit",
        "Linux exfat driver",
        "noatime mount option",
        "TRIM when supported",
      ],
      reason: "Selected because the drive must be connected directly to macOS without Windows portability.",
    };
  }
  return {
    filesystem: "ext4",
    partitionTable: "gpt",
    alignmentBytes: 1_048_576,
    allocationUnitBytes: 4096,
    noatime: true,
    trimMode: "conditional",
    settings: ["GPT partition table", "1 MiB partition alignment", "4 KiB allocation unit", "noatime mount option", "TRIM when supported"],
    reason: "Selected for a Hoardarr-managed Linux data drive.",
  };
}
